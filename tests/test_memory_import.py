"""Tests for ``memory import``: the file a person carried, put back.

The freeze is ``docs/v0.5.2-import-design.md``, and this file holds §11's
must-pass matrix — minus the rows that are other files' work and are named here
rather than pretended to be covered: the existing suites (#28–#30), the
``MEMORY_SIDE`` line (#31, ``tests/test_memory_provenance.py``) and the README
replay (#34, ``tests/test_readme.py``).

**The three properties the whole file turns on, and how each is held.**

* **The file is the unit of success.** Every refusal is a sentence, exit 1 and
  nothing on stdout, and the store's rows are compared before and after —
  ``_store_state``, the device the export tests already use.
* **An import that inserts nothing writes nothing at all**, down to the
  database file's own bytes: the no-op test hashes the file, because the row
  level alone would not catch a re-stamped ``meta`` (measured: an identical
  ``INSERT OR REPLACE`` changes the bytes).
* **The transaction is real, not intended.** A failure is injected *by SQLite*
  through a trigger — a store that already has memory tables, and one that has
  none, where the tables and the stamp must roll back too. The static SQL scan
  at the bottom proves the module's shape and nothing else; these two tests are
  where atomicity is actually held.

The files are built the way a person's file is: a real export for the round
trips, and hand-edited JSON for the refusals — one field changed at a time, so
each sentence is tested against the rule it belongs to.
"""

import ast
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.memory import (
    Citation,
    Subject,
    invalidate,
    read_memories,
    supersede,
)
from codearchaeology.memory_export import build_document
from codearchaeology.memory_import import (
    FORMAT_VERSION,
    VERSION_KEY,
    MemoryImportError,
    plan_import,
    read_document,
    write_import,
)
from codearchaeology.storage import connect
from sample_repo import build_single_commit_repo, git_output
from test_memory_adopt import moved
from test_memory_export import AWKWARD, _head, _store_state, _write, database
from test_memory_hardening import _hand_written

runner = CliRunner()

SOURCE = Path(__file__).resolve().parent.parent / "src" / "codearchaeology"

MOMENT = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)

PROJECT = "We do not add runtime dependencies casually."
OLD_FILE_MEMORY = "The login helper lived in two modules."
FILE_MEMORY = "The login helper stays in one module."
REASON = "the split state machine caused an incident"

# A canonical id a test can name in a hand-built file. Any UUID would do; this
# one is written down so a failure message says which row it was about.
SPARE_ID = "aaaaaaaa-1111-4111-8111-111111111111"


def _run(repository: Path, database: Path, *arguments: str):
    return runner.invoke(
        app, ["memory", *arguments, str(repository), "--db", str(database)]
    )


def _identity(repository: Path) -> str:
    return str(Path(repository).resolve())


def _lines(document: str) -> list[dict]:
    """The document's lines, parsed — so a test can change one field."""
    return [json.loads(line) for line in document.splitlines()]


def _document_of(values) -> str:
    """Values as a document, in the writer's own spelling."""
    return "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values)


def _escaped(values) -> str:
    """A document with every non-ASCII character written as a JSON escape.

    A lone surrogate cannot be written into a UTF-8 file at all, so the only way
    a file carries one is an escape like ``"\\ud800"`` — which ``json`` reads
    back into the surrogate the writer's own output cannot contain.
    """
    return "".join(json.dumps(value, ensure_ascii=True) + "\n" for value in values)


def _file(tmp_path: Path, document: str, name: str = "memories.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(document, encoding="utf-8", newline="")
    return path


def _header(repository: str, count: int, **overrides) -> dict:
    value = {
        VERSION_KEY: FORMAT_VERSION,
        "repository": repository,
        "count": count,
        "exported_at": MOMENT.isoformat(),
    }
    value.update(overrides)
    return value


def _row(repository: str, memory_id: str, **overrides) -> dict:
    """One memory line, valid unless a test overrides a field."""
    value = {
        "memory_id": memory_id,
        "repository": repository,
        "statement": "One statement.",
        "author": None,
        "admitted_at": MOMENT.isoformat(),
        "state": "active",
        "subject": {"kind": "repository"},
        "since": None,
        "ended_at": None,
        "end_reason": None,
        "superseded_by": None,
        "citations": [],
    }
    value.update(overrides)
    return value


def _export(repository: Path, database: Path) -> str:
    """A real export of the store, built by the tool's own writer."""
    with closing(connect(database)) as connection:
        found = read_memories(connection, repository, include_ended=True)
    return build_document(found, repository=_identity(repository), exported_at=MOMENT)


def _fresh(sample_repo: Path, tmp_path: Path, name: str = "fresh.db") -> Path:
    """A store for the same repository that has never held a memory.

    The ordinary case A: ``analyze`` creates the evidence and nothing of
    memory's, so the import meets a store with no tables and no stamp.
    """
    path = tmp_path / name
    analyze(sample_repo, path)
    return path


def _tables(database: Path) -> set[str]:
    with closing(connect(database)) as connection:
        return {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


def _state(database: Path) -> dict:
    """``_store_state`` for a store that may have no memory tables yet.

    The fresh store's shape is the thing several tests are about, so a helper
    that raised on it would refuse to describe the state it is asserting.
    """
    names = _tables(database)
    with closing(connect(database)) as connection:
        found = {"meta": [tuple(row) for row in connection.execute("SELECT * FROM meta")]}
    for name in ("memories", "memory_citations"):
        if name in names:
            with closing(connect(database)) as connection:
                found[name] = [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")]
        else:
            found[name] = None
    return found


@pytest.fixture
def store(sample_repo: Path, database: Path) -> Path:
    """The populated store: every state, one lifecycle link, two citations.

    Three memories: a superseded pair — so the file carries a link in the one
    direction the store keeps — and an invalidated one with its reason. Between
    them they carry every column the comparison reads.
    """
    project = _write(
        sample_repo,
        database,
        statement=PROJECT,
        subject=Subject("repository"),
        author_name="Dev",
        author_email="dev@example.com",
        admitted_at=MOMENT,
    )
    first = _write(
        sample_repo,
        database,
        statement=OLD_FILE_MEMORY,
        subject=Subject("path", path="core/app.py"),
        admitted_at=MOMENT,
    )
    head = _head(sample_repo, database)
    with closing(connect(database)) as connection:
        supersede(
            connection,
            first.memory_id,
            repository_path=sample_repo,
            statement=FILE_MEMORY,
            subject=Subject("path", path="core/app.py"),
            author_name="Dev",
            author_email="dev@example.com",
            since_commit_sha=head,
            citations=[Citation("commit", head[:8]), Citation("file", "core/app.py")],
            admitted_at=LATER,
        )
        invalidate(
            connection,
            project.memory_id,
            repository_path=sample_repo,
            reason=REASON,
            ended_at=LATER,
        )
    return database


# --- the ordinary case, the round trip, and idempotence ----------------------


def test_a_file_imports_into_a_fresh_store(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#1 — case A: the store lost and re-created, at the same path."""
    document = _export(sample_repo, store)
    file = _file(tmp_path, document)
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 0, result.stderr
    assert "Imported    3 memories" in result.stdout
    assert "Skipped     0 already here, unchanged" in result.stdout
    assert f"From        {file.resolve()}" in result.stdout
    assert f"Repository  {_identity(sample_repo)}" in result.stdout

    with closing(connect(fresh)) as connection:
        imported = read_memories(connection, sample_repo, include_ended=True)
    with closing(connect(store)) as connection:
        original = read_memories(connection, sample_repo, include_ended=True)
    assert [memory.memory_id for memory in imported] == [
        memory.memory_id for memory in original
    ]
    assert [memory.citations for memory in imported] == [
        memory.citations for memory in original
    ]
    assert [memory.state for memory in imported] == [
        memory.state for memory in original
    ]
    assert [memory.superseded_by for memory in imported] == [
        memory.superseded_by for memory in original
    ]


def test_the_derived_epochs_are_rebuilt_from_the_file_s_strings(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """The two integers the file does not carry, recomputed exactly (§6.1).

    The file carries the ISO strings and not ``admitted_epoch``/``ended_epoch``;
    the import rebuilds them with the expression the acts use, so a row written
    by import is the row the act would have written.
    """
    document = _export(sample_repo, store)
    file = _file(tmp_path, document)
    fresh = _fresh(sample_repo, tmp_path)

    assert _run(sample_repo, fresh, "import", str(file)).exit_code == 0

    with closing(connect(fresh)) as connection:
        rows = connection.execute(
            "SELECT admitted_at, admitted_epoch, ended_at, ended_epoch FROM memories"
        ).fetchall()
    assert rows
    for row in rows:
        assert row["admitted_epoch"] == int(
            datetime.fromisoformat(row["admitted_at"]).timestamp()
        )
        if row["ended_at"] is None:
            assert row["ended_epoch"] is None
        else:
            assert row["ended_epoch"] == int(
                datetime.fromisoformat(row["ended_at"]).timestamp()
            )


def test_the_round_trip_is_byte_identical(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#2 — the file is lossless, and import is the inverse of export."""
    document = _export(sample_repo, store)
    file = _file(tmp_path, document)
    fresh = _fresh(sample_repo, tmp_path)

    assert _run(sample_repo, fresh, "import", str(file)).exit_code == 0
    again = _run(sample_repo, fresh, "export")

    assert again.exit_code == 0, again.stderr
    assert again.stdout.splitlines()[1:] == document.splitlines()[1:]
    assert _lines(again.stdout)[0]["count"] == _lines(document)[0]["count"]


def test_the_same_file_imported_twice_changes_nothing(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#3, #5 — strict idempotence, at the rows and at the file's own bytes.

    The digest is the point: a no-op that re-stamped ``meta`` would leave every
    row equal and the file changed, and only the bytes can tell the difference.
    """
    document = _export(sample_repo, store)
    file = _file(tmp_path, document)
    fresh = _fresh(sample_repo, tmp_path)
    assert _run(sample_repo, fresh, "import", str(file)).exit_code == 0

    before = _store_state(fresh)
    digest = hashlib.sha256(fresh.read_bytes()).hexdigest()
    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 0, result.stderr
    assert "Imported    0 memories" in result.stdout
    assert "Skipped     3 already here, unchanged" in result.stdout
    assert _store_state(fresh) == before
    assert hashlib.sha256(fresh.read_bytes()).hexdigest() == digest


def test_a_no_op_on_a_store_with_no_tables_creates_none(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#3c — an import that inserts nothing does not even build the store."""
    document = build_document((), repository=_identity(sample_repo), exported_at=MOMENT)
    file = _file(tmp_path, document, "empty.jsonl")
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 0, result.stderr
    assert "Imported    0 memories" in result.stdout
    assert "memories" not in _tables(fresh)
    assert "memory_citations" not in _tables(fresh)
    with closing(connect(fresh)) as connection:
        stamped = connection.execute(
            "SELECT value FROM meta WHERE key = ?", ("memory_schema_version",)
        ).fetchone()
    assert stamped is None


def test_a_superset_file_layers_over_a_subset(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#4 — two exports months apart are what a backup habit produces.

    The subset is a *closed* one — a row with no lifecycle link — because a file
    that carried a superseded row without its successor would be refused by the
    closure rule, which is its own test.
    """
    lines = _lines(_export(sample_repo, store))
    alone = next(value for value in lines[1:] if value["superseded_by"] is None)
    one = _file(
        tmp_path, _document_of([_header(_identity(sample_repo), 1), alone]), "one.jsonl"
    )
    full = _file(tmp_path, _document_of(lines), "full.jsonl")
    fresh = _fresh(sample_repo, tmp_path)

    assert _run(sample_repo, fresh, "import", str(one)).exit_code == 0
    result = _run(sample_repo, fresh, "import", str(full))

    assert result.exit_code == 0, result.stderr
    assert "Imported    2 memories" in result.stdout
    assert "Skipped     1 already here, unchanged" in result.stdout
    with closing(connect(fresh)) as connection:
        assert len(read_memories(connection, sample_repo, include_ended=True)) == 3


def test_a_moved_repository_comes_back_behind_from(
    moved, tmp_path: Path
) -> None:
    """#3a — case B: the checkout moved, and a person says the two are one.

    The file is what an export written *before* the move carries — the rows name
    the old path — and the store is the new path's. Without ``--from`` the file
    is refused; with it, the rows are written as the new repository's.
    """
    before, after, database = moved
    with closing(connect(database)) as connection:
        found = read_memories(connection, before, include_ended=True)
    document = build_document(found, repository=str(before), exported_at=MOMENT)
    file = _file(tmp_path, document, "before-the-move.jsonl")

    fresh = tmp_path / "after.db"
    analyze(after, fresh)

    refused = _run(after, fresh, "import", str(file))
    assert refused.exit_code == 1
    assert str(before) in refused.stderr and "--from" in refused.stderr
    assert refused.stdout == ""
    assert "memories" not in _tables(fresh)

    result = _run(after, fresh, "import", str(file), "--from", str(before))

    assert result.exit_code == 0, result.stderr
    assert "Imported    1 memory" in result.stdout
    assert f"Made about  {before}" in result.stdout
    with closing(connect(fresh)) as connection:
        rows = connection.execute(
            "SELECT repository_path, statement FROM memories"
        ).fetchall()
    assert [row["repository_path"] for row in rows] == [str(after)]
    assert [row["statement"] for row in rows] == [memory.statement for memory in found]


def test_from_must_agree_with_the_file(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#3b — two sources must agree, and a redundant flag is not an error."""
    document = _export(sample_repo, store)
    file = _file(tmp_path, document)
    fresh = _fresh(sample_repo, tmp_path)
    before = _state(fresh)

    wrong = _run(
        sample_repo, fresh, "import", str(file), "--from", str(tmp_path / "elsewhere")
    )

    assert wrong.exit_code == 1
    assert "they must be the same path" in wrong.stderr
    assert wrong.stdout == ""
    assert _state(fresh) == before

    right = _run(sample_repo, fresh, "import", str(file), "--from", str(sample_repo))

    assert right.exit_code == 0, right.stderr
    assert f"Made about  {_identity(sample_repo)}" in right.stdout
    assert "Imported    3 memories" in right.stdout


# --- the file's own rules ----------------------------------------------------


def test_a_file_about_another_path_is_refused_and_says_from(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#20 — case B is refused by default, and the sentence is actionable."""
    lines = _lines(_export(sample_repo, store))
    for value in lines:
        if "repository" in value:
            value["repository"] = "/somewhere/else"
    file = _file(tmp_path, _document_of(lines))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "/somewhere/else" in result.stderr
    assert _identity(sample_repo) in result.stderr
    assert "--from" in result.stderr
    assert result.stdout == ""


def test_a_format_version_this_tool_does_not_read_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#9 — forward compatibility is refused by name, not guessed at."""
    lines = _lines(_export(sample_repo, store))
    lines[0][VERSION_KEY] = "2"
    file = _file(tmp_path, _document_of(lines))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "'2'" in result.stderr and "'1'" in result.stderr
    assert result.stdout == ""


def test_a_count_that_disagrees_with_the_lines_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#10 — the truncated-backup guard: a short file is loud, not silent."""
    lines = _lines(_export(sample_repo, store))
    lines[0]["count"] = 12
    file = _file(tmp_path, _document_of(lines))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "says 12 memories and carries 3" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("count", [None, "three", -1, True])
def test_a_count_that_is_not_a_whole_number_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path, count
) -> None:
    """#11 — the header's shape, before anything counts anything."""
    values = _lines(_export(sample_repo, store))
    if count is None:
        del values[0]["count"]
    else:
        values[0]["count"] = count
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "count" in result.stderr
    assert result.stdout == ""


def test_an_empty_file_is_refused_and_a_header_only_file_imports_nothing(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#12 — a valid empty backup is not an error, and an empty file is not one."""
    empty = _file(tmp_path, "", "empty.jsonl")
    fresh = _fresh(sample_repo, tmp_path)

    refused = _run(sample_repo, fresh, "import", str(empty))

    assert refused.exit_code == 1
    assert "is empty" in refused.stderr
    assert refused.stdout == ""

    header_only = _file(
        tmp_path,
        build_document((), repository=_identity(sample_repo), exported_at=MOMENT),
        "header.jsonl",
    )
    result = _run(sample_repo, fresh, "import", str(header_only))

    assert result.exit_code == 0, result.stderr
    assert "Imported    0 memories" in result.stdout


def test_a_line_that_is_neither_header_nor_memory_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#13 — the shape rule, including the blank line the format forbids."""
    lines = _lines(_export(sample_repo, store))
    header = json.dumps(lines[0], ensure_ascii=False)

    blank = _file(tmp_path, header + "\n\n" + "\n".join(
        json.dumps(value, ensure_ascii=False) for value in lines[1:]
    ) + "\n", "blank.jsonl")
    both = _file(tmp_path, header + "\n" + json.dumps(
        {**lines[1], VERSION_KEY: "1"}, ensure_ascii=False
    ) + "\n", "both.jsonl")
    broken = _file(tmp_path, header + "\n" + "{not json\n", "broken.jsonl")
    fresh = _fresh(sample_repo, tmp_path)

    for file, what in ((blank, "line 2"), (both, "line 2"), (broken, "line 2")):
        result = _run(sample_repo, fresh, "import", str(file))
        assert result.exit_code == 1, what
        assert what in result.stderr
        assert result.stdout == ""
    assert "memories" not in _tables(fresh)


def test_an_id_given_twice_in_the_file_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """The file's own duplicate — a corrupt file, and not the store's business.

    Kept apart from an id the store already holds on purpose: the first is the
    file disagreeing with itself, the second is a meeting between two sets.
    """
    values = [
        _header(_identity(sample_repo), 2),
        _row(_identity(sample_repo), SPARE_ID),
        _row(_identity(sample_repo), SPARE_ID, statement="A second statement."),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "is given twice in this file" in result.stderr
    assert result.stdout == ""
    assert "memories" not in _tables(fresh)


def test_a_statement_that_holds_a_newline_survives(
    sample_repo: Path, database: Path, tmp_path: Path
) -> None:
    """#14 — one memory per line, whatever is in the statement."""
    _write(
        sample_repo,
        database,
        statement=AWKWARD,
        subject=Subject("repository"),
        admitted_at=MOMENT,
    )
    document = _export(sample_repo, database)
    file = _file(tmp_path, document)
    fresh = _fresh(sample_repo, tmp_path)

    assert _run(sample_repo, fresh, "import", str(file)).exit_code == 0

    with closing(connect(fresh)) as connection:
        found = read_memories(connection, sample_repo, include_ended=True)
    assert [memory.statement for memory in found] == [AWKWARD]
    assert len(document.splitlines()) == 2


@pytest.mark.parametrize(
    "change, sentence",
    [
        ({"statement": ""}, "the statement is empty"),
        ({"statement": 5}, "statement must be text"),
        ({"state": "paused"}, "is not a state"),
        ({"subject": "the project"}, "a subject is an object with a kind"),
        ({"subject": {"kind": "path"}}, "a path subject needs 'path'"),
        ({"subject": {"kind": "path", "path": None}}, "a subject's path must be text"),
        (
            {"subject": {"kind": "definition", "path": "a.py", "qualname": None}},
            "a subject's name must be text",
        ),
        ({"subject": {"kind": "commit", "commit": None}}, "a subject's commit must be text"),
        ({"subject": {"kind": "repository", "path": "x"}}, "carries an unknown field"),
        ({"author": {"name": "Dev"}}, "author is missing 'email'"),
        ({"author": {"name": None, "email": None}}, "an author with no name and no email"),
        (
            {"citations": [{"kind": "commit"}]},
            "a citation is an object with a kind and a reference",
        ),
        ({"citations": [{"kind": "vibe", "ref": "x"}]}, "is not a citation kind"),
        ({"since": {"commit": "a", "date": "2024-01-01"}}, "not both"),
        ({"since": {"commit": None}}, "a start commit must be text"),
        ({"since": {"date": "20240101"}}, "is not a date"),
        ({"ended_at": MOMENT.isoformat()}, "an active memory has no end"),
        ({"end_reason": REASON}, "an active memory has no end"),
        ({"superseded_by": SPARE_ID}, "an active memory has no end"),
    ],
)
def test_a_missing_or_wrong_field_is_refused_by_name(
    sample_repo: Path, store: Path, tmp_path: Path, change, sentence
) -> None:
    """#15 — a file can never smuggle in a row the acts would have refused."""
    values = [
        _header(_identity(sample_repo), 1),
        _row(_identity(sample_repo), SPARE_ID, **change),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1, sentence
    assert sentence in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "value",
    ["2026-10-11T09:00:00Z", "20261011", "yesterday", "2026-10-11T09:00:00"],
)
def test_a_time_that_does_not_round_trip_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path, value: str
) -> None:
    """#16 — a spelling the writer would not have produced breaks every read."""
    values = [
        _header(_identity(sample_repo), 1),
        _row(_identity(sample_repo), SPARE_ID, admitted_at=value),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "admitted_at" in result.stderr
    assert result.stdout == ""


def test_a_moment_without_a_time_zone_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """A naive spelling is one the writer does not produce.

    Every moment this tool writes carries the machine's offset, and a naive one
    would be read as the *importing* machine's clock — one file, two orders on
    two machines.
    """
    values = [
        _header(_identity(sample_repo), 1),
        _row(_identity(sample_repo), SPARE_ID, admitted_at="2026-10-11T09:00:00"),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)
    before = _state(fresh)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "no time zone offset" in result.stderr
    assert result.stdout == ""
    assert _state(fresh) == before


def test_a_moment_before_1970_is_still_an_epoch(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """The offset is what makes the epoch arithmetic rather than ``mktime``.

    ``int(datetime.fromisoformat(s).timestamp())`` is the expression both layers
    use. On Windows a *naive* moment before 1970 raises ``OSError`` from it
    (measured); with the offset the expression is arithmetic, so the row imports
    on every platform. This is the regression guard for that.
    """
    moment = "1969-12-31T23:59:59+00:00"
    values = [
        _header(_identity(sample_repo), 1),
        _row(_identity(sample_repo), SPARE_ID, admitted_at=moment),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 0, result.stderr
    with closing(connect(fresh)) as connection:
        row = connection.execute(
            "SELECT admitted_at, admitted_epoch FROM memories WHERE memory_id = ?",
            (SPARE_ID,),
        ).fetchone()
    assert row["admitted_at"] == moment
    assert row["admitted_epoch"] == -1


@pytest.mark.parametrize(
    "change, what",
    [
        ({"statement": "\ud800"}, "the statement"),
        ({"repository": "\ud800"}, "repository"),
        ({"author": {"name": "\ud800", "email": None}}, "author's name"),
        ({"subject": {"kind": "path", "path": "\ud800"}}, "a subject's path"),
        ({"since": {"commit": "\ud800"}}, "a start commit"),
        (
            {
                "state": "invalidated",
                "ended_at": MOMENT.isoformat(),
                "end_reason": "\ud800",
            },
            "end_reason",
        ),
        ({"citations": [{"kind": "file", "ref": "\ud800"}]}, "a citation's reference"),
    ],
)
def test_a_lone_surrogate_is_refused_before_the_store_is_touched(
    sample_repo: Path, store: Path, tmp_path: Path, change, what
) -> None:
    """A JSON escape can spell text SQLite refuses at bind time.

    ``json.loads('"\\ud800"')`` is a string Python holds and ``sqlite3`` cannot
    bind — ``UnicodeEncodeError``, measured — so every field that becomes a
    column is refused by name before the store is opened.
    """
    values = [
        _header(_identity(sample_repo), 1),
        {**_row(_identity(sample_repo), SPARE_ID), **change},
    ]
    file = _file(tmp_path, _escaped(values))
    fresh = _fresh(sample_repo, tmp_path)
    before = _state(fresh)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "lone surrogate" in result.stderr, result.stderr
    assert what in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert _state(fresh) == before


def test_a_lone_surrogate_in_the_header_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """The header's repository is refused the same way — it is echoed back."""
    values = [
        {**_header(_identity(sample_repo), 1), "repository": "\ud800"},
        _row(_identity(sample_repo), SPARE_ID),
    ]
    file = _file(tmp_path, _escaped(values), "header.jsonl")
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "line 1" in result.stderr
    assert "lone surrogate" in result.stderr
    assert result.stdout == ""


def test_the_statement_cap_is_on_the_value_that_is_written(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """§5.2's cap keeps a row too long for the view out of the store (§10).

    This path writes the statement verbatim, so the cap is checked on the
    verbatim value: a statement padded to 5001 characters is one character after
    stripping, and a check on the trim would let it in.
    """
    padded = _file(
        tmp_path,
        _document_of(
            [
                _header(_identity(sample_repo), 1),
                _row(_identity(sample_repo), SPARE_ID, statement=" " * 5000 + "x"),
            ]
        ),
        "padded.jsonl",
    )
    fresh = _fresh(sample_repo, tmp_path)
    before = _state(fresh)

    refused = _run(sample_repo, fresh, "import", str(padded))

    assert refused.exit_code == 1
    assert "at most 4000 characters" in refused.stderr
    assert "5001" in refused.stderr
    assert _state(fresh) == before

    exact = _file(
        tmp_path,
        _document_of(
            [
                _header(_identity(sample_repo), 1),
                _row(_identity(sample_repo), SPARE_ID, statement="x" * 4000),
            ]
        ),
        "exact.jsonl",
    )

    accepted = _run(sample_repo, fresh, "import", str(exact))

    assert accepted.exit_code == 0, accepted.stderr
    with closing(connect(fresh)) as connection:
        stored = connection.execute(
            "SELECT length(statement) FROM memories WHERE memory_id = ?",
            (SPARE_ID,),
        ).fetchone()[0]
    assert stored == 4000


def test_two_spellings_of_one_reference_are_one_reference(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """§5.2's duplicate rule is the acts' own, and the acts read the stripped
    reference: ``memory._checked_citations`` refuses a pair that strips equal.
    """
    values = [
        _header(_identity(sample_repo), 1),
        _row(
            _identity(sample_repo),
            SPARE_ID,
            citations=[
                {"kind": "file", "ref": "core/app.py"},
                {"kind": "file", "ref": " core/app.py "},
            ],
        ),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)
    before = _state(fresh)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "given twice" in result.stderr
    assert "'core/app.py'" in result.stderr
    assert _state(fresh) == before


def test_an_unknown_key_is_refused_in_the_header_and_in_a_row(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#17 — a field nobody reads is a claim nobody verified."""
    header = _file(
        tmp_path,
        _document_of([_header(_identity(sample_repo), 1, tool_version="9.9")]),
        "header.jsonl",
    )
    row = _file(
        tmp_path,
        _document_of(
            [
                _header(_identity(sample_repo), 1),
                _row(_identity(sample_repo), SPARE_ID, confidence="high"),
            ]
        ),
        "row.jsonl",
    )
    fresh = _fresh(sample_repo, tmp_path)

    first = _run(sample_repo, fresh, "import", str(header))
    assert first.exit_code == 1
    assert "tool_version" in first.stderr

    second = _run(sample_repo, fresh, "import", str(row))
    assert second.exit_code == 1
    assert "confidence" in second.stderr
    assert second.stdout == ""


def test_a_bom_and_a_file_that_is_not_utf8_are_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#18 — the format's bytes, which is what a file copied wrongly breaks."""
    document = _document_of(
        [_header(_identity(sample_repo), 1), _row(_identity(sample_repo), SPARE_ID)]
    )
    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf" + document.encode("utf-8"))
    # One byte that is not valid UTF-8 anywhere in it. The document is built
    # ASCII-only for this test, so the byte cannot be confused with an encoding
    # the format does allow.
    raw = bytearray(document.encode("utf-8"))
    raw[10] = 0xFF
    latin = tmp_path / "latin.jsonl"
    latin.write_bytes(bytes(raw))
    fresh = _fresh(sample_repo, tmp_path)

    first = _run(sample_repo, fresh, "import", str(bom))
    assert first.exit_code == 1
    assert "byte-order mark" in first.stderr

    second = _run(sample_repo, fresh, "import", str(latin))
    assert second.exit_code == 1
    assert "is not UTF-8" in second.stderr
    assert second.stdout == ""


def test_a_row_that_disagrees_with_the_header_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#19 — the file cannot disagree with itself."""
    values = [
        _header(_identity(sample_repo), 1),
        _row("/somewhere/else", SPARE_ID),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "/somewhere/else" in result.stderr
    assert result.stdout == ""


# --- conflicts, lifecycle links, and what is never edited --------------------


def test_a_row_that_is_already_here_and_differs_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#6 — the tool does not choose between two versions of what a person said."""
    lines = _lines(_export(sample_repo, store))
    lines[1]["statement"] = lines[1]["statement"] + " And one more thing."
    file = _file(tmp_path, _document_of(lines))

    before = _store_state(store)
    result = _run(sample_repo, store, "import", str(file))

    assert result.exit_code == 1
    assert (
        f"memory {lines[1]['memory_id']} is already here and differs in statement"
        in result.stderr
    )
    assert result.stdout == ""
    assert _store_state(store) == before


def test_a_different_citation_list_is_a_difference(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#7 — the comparison is total, and the order is part of it."""
    lines = _lines(_export(sample_repo, store))
    cited = next(value for value in lines[1:] if len(value["citations"]) == 2)
    cited["citations"] = list(reversed(cited["citations"]))
    file = _file(tmp_path, _document_of(lines))
    before = _store_state(store)

    result = _run(sample_repo, store, "import", str(file))

    assert result.exit_code == 1
    assert "differs in citations" in result.stderr
    assert _store_state(store) == before


def test_an_id_held_for_another_repository_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#8 — one store, one repository, and `--from` does not bypass it."""
    other = str((tmp_path / "elsewhere").resolve())
    _hand_written(store, SPARE_ID, other, statement="Theirs.")
    values = [_header(_identity(sample_repo), 1), _row(_identity(sample_repo), SPARE_ID)]
    file = _file(tmp_path, _document_of(values))

    result = _run(sample_repo, store, "import", str(file))

    assert result.exit_code == 1
    assert other in result.stderr
    assert "adopt" in result.stderr
    assert result.stdout == ""


def test_a_dangling_successor_is_refused_by_name_not_by_a_constraint(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#21 — closure, and a sentence instead of a foreign-key error."""
    missing = "cccccccc-2222-4222-8222-222222222222"
    values = [
        _header(_identity(sample_repo), 1),
        _row(
            _identity(sample_repo),
            SPARE_ID,
            state="superseded",
            ended_at=LATER.isoformat(),
            superseded_by=missing,
        ),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert f"names successor {missing}" in result.stderr
    assert "which is in neither the file nor this store" in result.stderr
    assert result.stdout == ""
    assert "memories" not in _tables(fresh)


def test_a_successor_already_in_the_store_closes_the_link(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """§7.3 — the closure is the file's rows *together with the store's*.

    The store holds the superseded pair; a file carrying only the old row names
    a successor that is in this store, which is the case the design names: a
    successor an earlier import wrote. Reading only the file's own ids would
    refuse it and say the successor is nowhere.
    """
    lines = _lines(_export(sample_repo, store))
    old = next(value for value in lines[1:] if value["superseded_by"] is not None)
    successor = old["superseded_by"]
    subset = _file(
        tmp_path,
        _document_of([_header(_identity(sample_repo), 1), old]),
        "old-only.jsonl",
    )
    before = _state(store)

    skipped = _run(sample_repo, store, "import", str(subset))

    assert skipped.exit_code == 0, skipped.stderr
    assert "Imported    0 memories" in skipped.stdout
    assert "Skipped     1 already here, unchanged" in skipped.stdout
    assert _state(store) == before

    # And a row that is *new* may point at a successor the store holds: a file
    # that adds an old row back (a partial backup) is the other half of the case.
    added = _row(
        _identity(sample_repo),
        SPARE_ID,
        statement="An older rule about the same file.",
        state="superseded",
        ended_at=MOMENT.isoformat(),
        superseded_by=successor,
    )
    partial = _file(
        tmp_path,
        _document_of([_header(_identity(sample_repo), 1), added]),
        "partial.jsonl",
    )

    imported = _run(sample_repo, store, "import", str(partial))

    assert imported.exit_code == 0, imported.stderr
    assert "Imported    1 memory" in imported.stdout


def test_a_cycle_is_refused(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#22 — a shape the acts cannot write, so the act refuses to store it."""
    first = "dddddddd-3333-4333-8333-333333333333"
    second = "eeeeeeee-4444-4444-8444-444444444444"
    values = [
        _header(_identity(sample_repo), 2),
        _row(
            _identity(sample_repo),
            first,
            state="superseded",
            ended_at=LATER.isoformat(),
            superseded_by=second,
        ),
        _row(
            _identity(sample_repo),
            second,
            state="superseded",
            ended_at=LATER.isoformat(),
            superseded_by=first,
        ),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "close on" in result.stderr
    assert first in result.stderr and second in result.stderr
    assert result.stdout == ""


def test_a_refused_import_writes_nothing_at_all(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#23 — all-or-nothing: a file refused at the last check still writes nothing.

    The store is fresh, so the import would have had to create the tables first
    — and the assertion is that it did not even do that. The refusal comes from
    the closure check, which runs after the plan was built, so this is the last
    moment a write could have happened and did not.
    """
    missing = "cccccccc-2222-4222-8222-222222222222"
    values = [
        _header(_identity(sample_repo), 2),
        _row(_identity(sample_repo), SPARE_ID),
        _row(
            _identity(sample_repo),
            "dddddddd-3333-4333-8333-333333333333",
            state="superseded",
            ended_at=LATER.isoformat(),
            superseded_by=missing,
        ),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)
    before = _state(fresh)

    result = _run(sample_repo, fresh, "import", str(file))

    assert result.exit_code == 1
    assert "names successor" in result.stderr
    assert _state(fresh) == before
    assert "memories" not in _tables(fresh)


def test_an_import_never_edits_a_row_that_is_already_here(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#27 — "existing memories stay as they are", held row for row.

    The file carries the store's three rows plus one new one, so the import
    writes — and the three that were already here must come out identical.
    """
    lines = _lines(_export(sample_repo, store))
    document = _document_of(
        [
            _header(_identity(sample_repo), len(lines)),
            *lines[1:],
            _row(_identity(sample_repo), SPARE_ID),
        ]
    )
    file = _file(tmp_path, document)
    before = _store_state(store)
    already_here = {value["memory_id"] for value in lines[1:]}

    result = _run(sample_repo, store, "import", str(file))

    assert result.exit_code == 0, result.stderr
    assert "Imported    1 memory" in result.stdout
    assert "Skipped     3 already here, unchanged" in result.stdout
    after = _store_state(store)
    kept = [row for row in after["memories"] if row[0] in already_here]
    assert kept == [row for row in before["memories"] if row[0] in already_here]
    assert [
        row for row in after["memory_citations"] if row[0] != SPARE_ID
    ] == before["memory_citations"]


def test_the_local_git_identity_is_never_written_as_an_author(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """The act never speaks for a person: no author is invented on this path.

    The row carries no author at all, and the fixture's repository has a git
    identity configured — the thing ``memory create`` reads and the thing an
    import must not. What is written is what the file said: nothing.
    """
    values = [
        _header(_identity(sample_repo), 1),
        _row(_identity(sample_repo), SPARE_ID),
    ]
    file = _file(tmp_path, _document_of(values))
    fresh = _fresh(sample_repo, tmp_path)

    assert _run(sample_repo, fresh, "import", str(file)).exit_code == 0

    with closing(connect(fresh)) as connection:
        row = connection.execute(
            "SELECT author_name, author_email FROM memories"
        ).fetchone()
    assert (row["author_name"], row["author_email"]) == (None, None)
    assert git_output(sample_repo, "config", "user.name").strip()


# --- the transaction, and the failure it must survive ------------------------


def _refuse_inserts(database: Path, when: str, table: str = "memory_citations") -> None:
    """A trigger that fails an import in the middle of the file.

    ``_refuse_updates``'s device, on INSERT: the statement is refused by SQLite
    itself, which is what a disk going away looks like to the code that issued
    it. The condition says which write breaks.
    """
    connection = connect(database)
    try:
        connection.execute(
            f"CREATE TRIGGER refuse BEFORE INSERT ON {table}"
            f" WHEN {when} BEGIN SELECT RAISE(FAIL, 'the disk went away'); END"
        )
        connection.commit()
    finally:
        connection.close()


def test_a_failure_mid_file_leaves_nothing_behind(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#24 — the transaction, not the intention, on both store shapes.

    Two halves. In the store that already has memory tables, the trigger breaks
    the *last* row's insert while the middle row is already written, so the
    rollback has real work to do. In the store with no tables at all, a trigger
    cannot live on a table that does not exist yet, so it breaks the ``meta``
    write at the end — after the DDL, the stamp and every row — and what is
    asserted is that the tables and the stamp are gone too, not merely the rows.
    Both failures leave as the sentence §10 promises: SQLite's own error is
    translated where it is raised, so a traceback is a failure of this test too.
    """
    document = _export(sample_repo, store)
    lines = _lines(document)
    file = _file(tmp_path, document)
    alone = next(value for value in lines[1:] if value["superseded_by"] is None)
    inserting = [value for value in lines[1:] if value is not alone]

    populated = _fresh(sample_repo, tmp_path, "populated.db")
    subset = _file(
        tmp_path,
        _document_of([_header(_identity(sample_repo), 1), alone]),
        "subset.jsonl",
    )
    assert _run(sample_repo, populated, "import", str(subset)).exit_code == 0
    before = _store_state(populated)

    # The failure lands on the *last* row the full file inserts, so the row
    # before it is already written when SQLite refuses — which is what gives the
    # rollback real work to do.
    _refuse_inserts(
        populated,
        f"NEW.memory_id = '{inserting[-1]['memory_id']}'",
        table="memories",
    )
    result = _run(sample_repo, populated, "import", str(file))

    assert result.exit_code == 1
    assert "could not be written" in result.stderr
    assert "Traceback" not in result.stderr
    assert _store_state(populated) == before

    empty = _fresh(sample_repo, tmp_path, "empty.db")
    _refuse_inserts(empty, "NEW.key = 'memory_imported_at'", table="meta")

    failed = _run(sample_repo, empty, "import", str(file))

    assert failed.exit_code == 1
    assert "could not be written" in failed.stderr
    assert "Traceback" not in failed.stderr
    assert "memories" not in _tables(empty)
    assert "memory_citations" not in _tables(empty)
    keys = {key for key, _ in _state(empty)["meta"]}
    assert "memory_schema_version" not in keys
    assert "memory_imported_from" not in keys
    assert "memory_imported_at" not in keys
    listed = _run(sample_repo, empty, "list")
    assert listed.exit_code == 0, listed.stderr
    assert "No memories are kept" in listed.stdout


def test_an_open_transaction_is_refused_rather_than_committed(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """The module's own rule, held where it can be reached.

    ``write_import`` opens its own transaction, and setting ``isolation_level``
    to ``None`` commits whatever is open (measured) — so a caller's pending work
    would be committed silently. No command-line path reaches this (the CLI
    plans on a connection with nothing pending), so it is held here, against a
    real connection with a real open transaction.
    """
    document = read_document(_export(sample_repo, store), name="memories.jsonl")
    fresh = _fresh(sample_repo, tmp_path)
    with closing(connect(fresh)) as connection:
        plan = plan_import(connection, document, repository_path=sample_repo)
        assert plan.inserts
        connection.execute("INSERT INTO meta (key, value) VALUES ('a_test_key', 'x')")

        with pytest.raises(MemoryImportError) as caught:
            write_import(
                connection,
                plan,
                repository=_identity(sample_repo),
                source="memories.jsonl",
                imported_at=MOMENT,
            )

        assert "already has one open" in str(caught.value)
        assert connection.in_transaction
        connection.rollback()


def test_dry_run_writes_nothing(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#25 — a dry run is really read-only, on a valid file and a refused one."""
    document = _export(sample_repo, store)
    file = _file(tmp_path, document)
    broken = _file(tmp_path, "not a file this format writes\n", "broken.jsonl")
    fresh = _fresh(sample_repo, tmp_path)

    before = _state(fresh)
    digest = hashlib.sha256(fresh.read_bytes()).hexdigest()
    good = _run(sample_repo, fresh, "import", str(file), "--dry-run")

    assert good.exit_code == 0, good.stderr
    assert "Dry run" in good.stdout
    assert "3 memories would be imported" in good.stdout
    assert _state(fresh) == before
    assert hashlib.sha256(fresh.read_bytes()).hexdigest() == digest

    refused = _run(sample_repo, fresh, "import", str(broken), "--dry-run")

    assert refused.exit_code == 1
    assert _state(fresh) == before
    assert hashlib.sha256(fresh.read_bytes()).hexdigest() == digest


def test_the_evidence_is_untouched(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """#26 — the two layers stay apart, held table by table."""
    document = _export(sample_repo, store)
    file = _file(tmp_path, document)
    fresh = _fresh(sample_repo, tmp_path)
    tables = ("commits", "commit_files", "file_versions", "definition_versions")

    def evidence(path: Path) -> dict:
        with closing(connect(path)) as connection:
            return {
                name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")]
                for name in tables
            }

    before = evidence(fresh)
    assert _run(sample_repo, fresh, "import", str(file)).exit_code == 0

    assert evidence(fresh) == before


# --- the command line --------------------------------------------------------


def test_the_json_is_the_shape_the_freeze_froze(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """The ``--json`` report is the shape §9.2 froze: one object, eight fields."""
    document = _export(sample_repo, store)
    file = _file(tmp_path, document)
    fresh = _fresh(sample_repo, tmp_path)

    result = _run(sample_repo, fresh, "import", str(file), "--json")

    assert result.exit_code == 0, result.stderr
    found = json.loads(result.stdout)
    assert list(found) == [
        "file",
        "file_repository",
        "repository",
        "format_version",
        "count_in_file",
        "imported",
        "skipped",
        "dry_run",
    ]
    assert found["file"] == str(file.resolve())
    assert found["file_repository"] == _identity(sample_repo)
    assert found["repository"] == _identity(sample_repo)
    assert found["format_version"] == FORMAT_VERSION
    assert found["count_in_file"] == 3
    assert found["imported"] == 3
    assert found["skipped"] == 0
    assert found["dry_run"] is False


def test_a_file_that_cannot_be_read_is_a_sentence(
    sample_repo: Path, store: Path, tmp_path: Path
) -> None:
    """The path is the one thing the refusal can name, and it does."""
    fresh = _fresh(sample_repo, tmp_path)
    missing = tmp_path / "not-there.jsonl"

    result = _run(sample_repo, fresh, "import", str(missing))

    assert result.exit_code == 1
    assert str(missing) in result.stderr
    assert "could not be read" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_the_store_of_another_repository_is_refused_before_the_file(
    sample_repo: Path, tmp_path: Path
) -> None:
    """``--db`` names a store, and it must hold this repository's memories."""
    other = build_single_commit_repo(tmp_path / "other")
    other_database = tmp_path / "other.db"
    analyze(other, other_database)
    document = build_document((), repository=_identity(sample_repo), exported_at=MOMENT)
    file = _file(tmp_path, document, "header.jsonl")

    result = _run(sample_repo, other_database, "import", str(file))

    assert result.exit_code == 1
    assert "holds" in result.stderr and "not" in result.stderr
    assert result.stdout == ""


# --- the guards on the module's shape ----------------------------------------


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _imported_from_memory(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "codearchaeology.memory":
            found.update(alias.name for alias in node.names)
    return found


def test_the_import_module_cannot_change_a_row() -> None:
    """#35 — defence 1 of §6.4 is a test, and it is only about the shape.

    What it proves: no ``UPDATE`` or ``DELETE`` against either memory table, and
    none of the acts called or imported. What it does **not** prove is anything
    about a transaction — that is
    ``test_a_failure_mid_file_leaves_nothing_behind``'s, and this scan is never
    cited as a substitute.
    """
    module = SOURCE / "memory_import.py"

    for act in ("admit", "supersede", "invalidate", "adopt"):
        assert act not in _called_names(module), act
        assert act not in _imported_from_memory(module), act

    tree = ast.parse(module.read_text(encoding="utf-8"))
    statements = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for statement in statements:
        flattened = " ".join(statement.lower().split())
        for table in ("memories", "memory_citations"):
            assert f"update {table}" not in flattened, statement
            assert f"delete from {table}" not in flattened, statement


def test_the_import_act_is_reachable_only_through_the_command_line() -> None:
    """#33 — the guard is real: the module that writes rows has one importer."""
    importers = {
        path.name
        for path in SOURCE.glob("*.py")
        if "codearchaeology.memory_import" in path.read_text(encoding="utf-8")
    }

    assert importers == {"cli.py"}, sorted(importers)


def test_the_reader_refuses_before_the_store_is_ever_read() -> None:
    """The reader is a pure function: no database, no command line.

    Held by calling it directly with a document that is wrong, and getting a
    sentence that names the line — the shape §14.1's first step asks for.
    """
    with pytest.raises(MemoryImportError) as caught:
        read_document("not a header\n", name="memories.jsonl")

    assert "memories.jsonl" in str(caught.value)
    assert "line 1" in str(caught.value)
