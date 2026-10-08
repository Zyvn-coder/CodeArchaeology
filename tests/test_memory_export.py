"""Tests for ``memory export``: the file a person can carry.

The freeze is ``docs/v0.5.1-portability-design.md``, and this file holds its §6
acceptance criteria — minus the two that belong to other units and are named
there rather than pretended to be here: the count guard's *reader* is the import
unit's, and the README and version contracts are ``tests/test_readme.py``'s and
``tests/test_cli.py``'s.

Three of the criteria are the ones the format turns on, and each is asserted in
the way that can actually fail:

* **A row carries what the store holds and nothing the store works out.** Held as
  a key-set comparison against ``memory show --json``'s object, field by field,
  rather than by spot-checking four names — the point is that the two shapes
  cannot drift, not that four fields are absent today.
* **The bytes are the same on every platform.** Asserted on ``read_bytes`` and
  never on a decoded string, because a decoded string is exactly where a CRLF
  stops being visible.
* **Export reads no evidence.** Held the way this project holds that class of
  claim — ``tests/test_memory_boundaries.py``'s shape — with a control: the
  evidence is wiped, the export is compared byte for byte, and ``memory list
  --json`` on the same database is compared too, so that the test fails if the
  wipe did nothing rather than passing because nothing was wiped.

That control is the point. A test that empties a table nothing reads would pass
while proving nothing, which is the trap this project already has a name for.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze, stored_head_sha
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.memory import Citation, Memory, Subject, admit, read_memories, supersede
from codearchaeology.memory_checks import Evidence
from codearchaeology.memory_export import (
    FORMAT_VERSION,
    VERSION_KEY,
    MemoryExportError,
    build_document,
    memory_line,
    write_document,
)
from codearchaeology.memory_view import memory_object, shown
from codearchaeology.storage import connect
from sample_repo import build_single_commit_repo

runner = CliRunner()

MOMENT = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)

# A statement with everything a line has to survive: a newline that must be
# escaped, a quote, a backslash, and characters that are not ASCII. It is one
# statement and it has to stay on one line.
AWKWARD = '换了模块，因为 load() 里的 "state machine" 出过事故。\n第二个理由：重试包装器。\\'


@pytest.fixture
def database(sample_repo: Path, tmp_path: Path) -> Path:
    """An analyzed repository whose structure has been read."""
    path = tmp_path / "history.db"
    analyze(sample_repo, path)
    run_ast_pass(sample_repo, path)
    return path


def _run(repository: Path, database: Path, *arguments: str):
    return runner.invoke(
        app, ["memory", *arguments, str(repository), "--db", str(database)]
    )


def _lines(document: str) -> list[dict]:
    """The document's lines, parsed. A trailing newline is not a line."""
    return [json.loads(line) for line in document.splitlines()]


def _rows(document: str) -> list[str]:
    """The document's lines, unparsed — for comparing bytes rather than values."""
    return document.splitlines()


def _head(repository: Path, database: Path) -> str:
    """The commit the stored analysis stops at, for tests that have to cite one."""
    return stored_head_sha(repository, database)


def _write(repository: Path, database: Path, **fields) -> Memory:
    """Admit a memory straight into the store, for tests that are not about acts."""
    with closing(connect(database)) as connection:
        return admit(connection, repository_path=repository, **fields)


def _store_state(database: Path) -> dict:
    """Every row of the memory tables and of ``meta``, as plain values.

    ``meta`` is in here as well as the two memory tables: an export that wrote
    nothing and a ``meta`` row it had no business writing would otherwise look
    the same as one that wrote nothing at all.
    """
    with closing(connect(database)) as connection:
        return {
            name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")]
            for name in ("meta", "memories", "memory_citations")
        }


def _wipe_evidence(database: Path) -> None:
    """Empty the tables a citation is resolved against, foreign keys aside.

    Opened raw rather than through ``storage.connect``, which turns foreign keys
    on: the point is to make the evidence *gone*, and a cascade would refuse
    rather than let the test build the state it needs.
    """
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in ("commits", "commit_files", "file_versions", "definition_versions"):
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
    finally:
        connection.close()


# --- the shape of a row ------------------------------------------------------


def test_a_row_carries_what_the_store_holds_and_nothing_it_works_out(
    sample_repo: Path, database: Path
) -> None:
    """The one sentence the format turns on, held against the canonical object.

    Compared field by field rather than by listing the four exclusions, because
    the property is "the two shapes differ by exactly what is derived" — a fifth
    derived field added later has to fail this, and a renamed one has to fail it
    too.
    """
    head = _head(sample_repo, database)
    memory = _write(
        sample_repo,
        database,
        statement="We keep the login helper in one module.",
        subject=Subject(kind="path", path="core/app.py"),
        author_name="Ada",
        author_email="ada@example.com",
        since_commit_sha=head,
        citations=(Citation(kind="commit", ref=head),),
        admitted_at=MOMENT,
    )

    with closing(connect(database)) as connection:
        canonical = memory_object(
            shown(Evidence(connection, sample_repo, database), memory)
        )
    row = memory_line(memory)

    assert set(row) == set(canonical) - {"supersedes"}
    assert set(row["subject"]) == set(canonical["subject"]) - {"resolution"}
    assert set(row["citations"][0]) == set(canonical["citations"][0]) - {"resolution"}
    assert set(row["since"]) == set(canonical["since"]) - {"commit_date"}
    assert row["author"] == canonical["author"]


def test_the_derived_fields_are_in_the_object_and_not_in_the_file(
    sample_repo: Path, database: Path
) -> None:
    """The exclusions are absences in the file, not answers that were dropped.

    The object carries a resolution for the subject, a resolution for the
    citation and the cited commit's date; the row carries none of the three. If
    the object ever stopped carrying them, the test above would pass on a file
    that had simply lost them.
    """
    head = _head(sample_repo, database)
    memory = _write(
        sample_repo,
        database,
        statement="A statement with a citation.",
        subject=Subject(kind="path", path="core/app.py"),
        since_commit_sha=head,
        citations=(Citation(kind="commit", ref=head),),
        admitted_at=MOMENT,
    )
    with closing(connect(database)) as connection:
        canonical = memory_object(
            shown(Evidence(connection, sample_repo, database), memory)
        )

    assert canonical["subject"]["resolution"] == "resolved"
    assert canonical["citations"][0]["resolution"] == "resolved"
    assert canonical["since"]["commit_date"] is not None

    row = memory_line(memory)
    assert "resolution" not in row["subject"]
    assert "resolution" not in row["citations"][0]
    assert "commit_date" not in row["since"]


def test_a_superseded_memory_carries_one_direction_only(
    sample_repo: Path, database: Path
) -> None:
    """The reverse link is the store's to derive, and a file is storage.

    Both rows are in the file, so both facts are recoverable — the successor is
    named by ``superseded_by``, and which memory it replaced is what that names.
    A second copy of the same edge is a second copy free to disagree.
    """
    first = _write(
        sample_repo,
        database,
        statement="The retry wrapper exists because the provider answers 200.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )
    with closing(connect(database)) as connection:
        supersede(
            connection,
            first.memory_id,
            repository_path=sample_repo,
            statement="The wrapper exists because the provider answers 200 with an error body.",
            subject=Subject(kind="repository"),
            admitted_at=LATER,
        )

    header, *rows = _lines(_run(sample_repo, database, "export").stdout)

    assert header["count"] == 2
    old = next(row for row in rows if row["memory_id"] == first.memory_id)
    new = next(row for row in rows if row["memory_id"] != first.memory_id)
    assert old["state"] == "superseded"
    assert old["superseded_by"] == new["memory_id"]
    assert "supersedes" not in old and "supersedes" not in new


def test_the_lifecycle_times_survive_the_file(
    sample_repo: Path, database: Path
) -> None:
    """A closed span is two times and a state, and all three are in the file.

    An export that kept the state and lost ``ended_at`` would look complete and
    would not be: a reader could no longer tell when the rule stopped holding.
    """
    first = _write(
        sample_repo,
        database,
        statement="A statement that will be replaced.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )
    with closing(connect(database)) as connection:
        supersede(
            connection,
            first.memory_id,
            repository_path=sample_repo,
            statement="The statement that replaced it.",
            subject=Subject(kind="repository"),
            admitted_at=LATER,
        )

    _, *rows = _lines(_run(sample_repo, database, "export").stdout)
    old = next(row for row in rows if row["memory_id"] == first.memory_id)

    assert old["ended_at"] == LATER.isoformat()
    assert old["admitted_at"] == MOMENT.isoformat()


# --- order and determinism --------------------------------------------------


def test_the_lines_are_ordered_by_id(
    sample_repo: Path, database: Path
) -> None:
    """Ascending by ``memory_id``, which is the only total order available.

    Admission order cannot serve — two memories can share a timestamp, which is
    why the store keeps microseconds and still does not promise a total order on
    the clock — so the file sorts on the one unique value a memory has. An
    export that used the store's own order (newest admission first) fails this
    whenever the ids are not themselves reverse-sorted.
    """
    for index in range(4):
        _write(
            sample_repo,
            database,
            statement=f"Statement number {index}.",
            subject=Subject(kind="repository"),
            admitted_at=MOMENT.replace(microsecond=index),
        )

    _, *rows = _lines(_run(sample_repo, database, "export").stdout)

    ids = [row["memory_id"] for row in rows]
    assert len(ids) == 4
    assert len(set(ids)) == 4
    assert ids == sorted(ids)


def test_two_exports_of_one_unchanged_store_differ_only_in_the_moment(
    sample_repo: Path, database: Path
) -> None:
    """The narrowed contract, on the bytes: the rows are identical.

    ``exported_at`` is the one field that may differ, and the rest of the header
    is compared rather than ignored — a header that changed in any *other* way
    is a bug, and this is the only test that would see it.
    """
    for index in range(3):
        _write(
            sample_repo,
            database,
            statement=f"Statement number {index}.",
            subject=Subject(kind="repository"),
            admitted_at=MOMENT.replace(microsecond=index),
        )

    first = _rows(_run(sample_repo, database, "export").stdout)
    second = _rows(_run(sample_repo, database, "export").stdout)

    assert len(first) == 4, "the store has to be non-empty for this to mean anything"
    assert first[1:] == second[1:]
    without_the_moment = {"exported_at": None}
    assert json.loads(first[0]) | without_the_moment == json.loads(second[0]) | without_the_moment


def test_the_two_ways_of_writing_it_produce_the_same_rows(
    sample_repo: Path, database: Path, tmp_path: Path
) -> None:
    """Standard output and ``--output`` are one document, not two.

    They take different code paths — one writes bytes to a stream, one writes a
    file — and a drift between them would be invisible from the outside until a
    person compared two backups.
    """
    _write(
        sample_repo,
        database,
        statement="One statement, written two ways.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )
    target = tmp_path / "export.jsonl"

    printed = _rows(_run(sample_repo, database, "export").stdout)
    written = _run(sample_repo, database, "export", "--output", str(target))

    assert written.exit_code == 0, written.stderr
    assert printed[1:] == _rows(target.read_text(encoding="utf-8"))[1:]


# --- the bytes --------------------------------------------------------------


def test_the_file_is_utf8_with_lf_endings_and_no_bom(
    sample_repo: Path, database: Path, tmp_path: Path
) -> None:
    """Read as bytes, because a decoded string is where a CRLF hides.

    The endings are part of the format: a file whose bytes depend on where it
    was written is a file whose diffs are noise, and this one is meant to be
    diffed.
    """
    _write(
        sample_repo,
        database,
        statement=AWKWARD,
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )
    target = tmp_path / "export.jsonl"
    assert _run(sample_repo, database, "export", "--output", str(target)).exit_code == 0

    data = target.read_bytes()
    assert b"\r" not in data
    assert data.count(b"\n") == 2
    assert data.endswith(b"\n")
    assert not data.startswith(b"\xef\xbb\xbf")
    assert "换了模块".encode("utf-8") in data


def test_a_statement_that_holds_a_newline_stays_on_one_line(
    sample_repo: Path, database: Path
) -> None:
    """One memory per line is the property the whole format rests on.

    It is guaranteed by JSON — a newline inside a string is escaped — and that is
    exactly why it is worth asserting: it is a property nobody wrote, so nothing
    but this test would notice it going away. The line count is the assertion
    that matters, and the statement coming back whole is the one that keeps the
    count from being satisfied by a truncation.
    """
    _write(
        sample_repo,
        database,
        statement=AWKWARD,
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )

    document = _run(sample_repo, database, "export").stdout
    lines = _lines(document)

    assert len(lines) == 2, document
    assert lines[0]["count"] == 1
    assert lines[1]["statement"] == AWKWARD


def test_a_statement_survives_a_round_trip_through_the_file(
    sample_repo: Path, database: Path
) -> None:
    """What goes in comes back, character for character.

    The escape, the quote, the backslash and the characters that are not ASCII
    are one statement here, and four separate ways for a serialiser to lose
    something.
    """
    _write(
        sample_repo,
        database,
        statement=AWKWARD,
        subject=Subject(kind="path", path="core/app.py"),
        author_name="Ada",
        author_email="ada@example.com",
        admitted_at=MOMENT,
    )

    row = _lines(_run(sample_repo, database, "export").stdout)[1]

    assert row["statement"] == AWKWARD
    assert row["author"] == {"name": "Ada", "email": "ada@example.com"}
    assert row["subject"] == {"kind": "path", "path": "core/app.py"}


# --- what it reads, and what it writes --------------------------------------


def test_export_is_indifferent_to_the_evidence_and_the_list_is_not(
    sample_repo: Path, database: Path
) -> None:
    """Export reads no evidence table; the control is that other commands do.

    Wiping ``commits`` and the structure tables changes what a citation and a
    subject *resolve to* — ``memory list --json`` has to show it — and must
    change nothing about the file. Without the control this test would pass on a
    database whose evidence had never been written in the first place.
    """
    head = _head(sample_repo, database)
    _write(
        sample_repo,
        database,
        statement="A statement about the only commit.",
        subject=Subject(kind="commit", commit_sha=head),
        citations=(Citation(kind="commit", ref=head),),
        admitted_at=MOMENT,
    )

    # `list --json` is one pretty-printed document; `export` is one document per
    # line. Two shapes on purpose — the first is read by a program that wants it
    # indented, the second is read by a diff — and reading either with the other
    # one's parser is the mistake this comment exists to prevent.
    before = _rows(_run(sample_repo, database, "export").stdout)
    listed_before = json.loads(_run(sample_repo, database, "list", "--json").stdout)

    _wipe_evidence(database)

    after = _rows(_run(sample_repo, database, "export").stdout)
    listed_after = json.loads(_run(sample_repo, database, "list", "--json").stdout)

    # The control: the wipe did something, and this is the command that sees it.
    assert listed_before["memories"][0]["citations"][0]["resolution"] == "resolved"
    assert listed_after["memories"][0]["citations"][0]["resolution"] == "unresolved"
    assert listed_before["memories"][0]["subject"]["resolution"] == "resolved"
    assert listed_after["memories"][0]["subject"]["resolution"] == "unresolved"

    # The claim: the file does not care.
    assert before[1:] == after[1:]


def test_export_writes_to_the_store_not_at_all(
    sample_repo: Path, database: Path, tmp_path: Path
) -> None:
    """Reading a store out is not an act on it.

    ``adopt`` records itself in ``meta`` because it changes the rows' identity.
    Export changes nothing, so it records nothing — and ``meta`` is compared
    beside the two tables, because a new row there would otherwise be invisible.
    """
    _write(
        sample_repo,
        database,
        statement="Nothing about this changes.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )
    before = _store_state(database)

    assert _run(sample_repo, database, "export").exit_code == 0
    written = _run(sample_repo, database, "export", "--output", str(tmp_path / "x.jsonl"))
    assert written.exit_code == 0, written.stderr

    assert _store_state(database) == before


def test_export_needs_no_network(
    sample_repo: Path, database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core First, in the shape ``tests/test_offline.py`` already uses.

    The command is deterministic throughout, so this is a cheap assertion — and
    it is here because every other memory path carries it too, and a command that
    quietly grew a dependency would otherwise be the one nobody checked.
    """
    import socket

    def refuse(*arguments, **keywords):
        raise AssertionError("memory export reached for a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    _write(
        sample_repo,
        database,
        statement="Written with no network available.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )

    result = _run(sample_repo, database, "export")

    assert result.exit_code == 0, result.stderr
    assert _lines(result.stdout)[0]["count"] == 1


# --- the command -------------------------------------------------------------


def test_export_prints_the_document_and_nothing_else_on_stdout(
    sample_repo: Path, database: Path
) -> None:
    """A redirected export has to be the document, parseable line by line."""
    _write(
        sample_repo,
        database,
        statement="One statement.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )

    result = _run(sample_repo, database, "export")

    assert result.exit_code == 0, result.stderr
    assert all(line.startswith("{") for line in result.stdout.splitlines())


def test_the_block_says_what_was_written_where_and_about_what(
    sample_repo: Path, database: Path, tmp_path: Path
) -> None:
    """Four rows, and the two a surprised user needs are the last two."""
    _write(
        sample_repo,
        database,
        statement="One statement.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )
    target = tmp_path / "backup.jsonl"

    result = _run(sample_repo, database, "export", "--output", str(target))

    assert result.exit_code == 0, result.stderr
    assert "Wrote       1 memory" in result.stdout
    assert f"To          {target.resolve()}" in result.stdout
    assert f"Repository  {sample_repo.resolve()}" in result.stdout
    assert f"Database    {database}" in result.stdout
    assert target.exists()


def test_an_empty_store_exports_a_header_and_says_so(
    sample_repo: Path, database: Path
) -> None:
    """None found is an answer, and the file still says what it is about.

    An empty export that named nothing would be an anonymous file; "this project
    had no memories when I exported" is a useful thing for a backup to say, and
    it is why the repository is in the header as well as on the rows.
    """
    result = _run(sample_repo, database, "export")

    assert result.exit_code == 0, result.stderr
    lines = _lines(result.stdout)
    assert len(lines) == 1
    assert lines[0]["count"] == 0
    assert lines[0][VERSION_KEY] == FORMAT_VERSION
    assert lines[0]["repository"] == str(sample_repo.resolve())


def test_an_existing_file_is_refused_and_the_sentence_says_both_ways_out(
    sample_repo: Path, database: Path, tmp_path: Path
) -> None:
    """This tool does not replace a file it did not write, and never prompts.

    The refusal is the whole safety: there is no question to answer, so a typo
    has to be an error rather than a deleted file. stderr and exit 1 with nothing
    on stdout, so that a script can tell the two apart.
    """
    target = tmp_path / "already-there.jsonl"
    target.write_text("something a person put here\n", encoding="utf-8")

    result = _run(sample_repo, database, "export", "--output", str(target))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "exists; this command does not replace a file it did not write" in result.stderr
    assert "--force" in result.stderr
    assert target.read_text(encoding="utf-8") == "something a person put here\n"


def test_force_replaces_it(sample_repo: Path, database: Path, tmp_path: Path) -> None:
    """The flag is the explicit act, in the family of ``adopt --from``'s path."""
    target = tmp_path / "already-there.jsonl"
    target.write_text("old\n", encoding="utf-8")
    _write(
        sample_repo,
        database,
        statement="Newer than the file.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )

    result = _run(sample_repo, database, "export", "--output", str(target), "--force")

    assert result.exit_code == 0, result.stderr
    assert "Newer than the file." in target.read_text(encoding="utf-8")


def test_a_path_that_cannot_be_written_is_a_sentence_not_a_traceback(
    sample_repo: Path, database: Path, tmp_path: Path
) -> None:
    """A directory that does not exist is refused, and not created.

    Every other path the tool writes to is one it chose itself; this one is a
    path a person typed, and making directories out of a typo is guessing rather
    than helping.
    """
    target = tmp_path / "nowhere" / "export.jsonl"

    result = _run(sample_repo, database, "export", "--output", str(target))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "could not be written" in result.stderr
    assert not target.parent.exists()


def test_another_repositorys_memories_are_reported_and_not_in_the_file(
    sample_repo: Path, tmp_path: Path
) -> None:
    """A file is about one repository, and the others are never included in silence.

    The same note ``memory list`` gives, on stderr so that the document stays the
    document — and the count in the header has to be this repository's, which is
    the part that would be wrong if the read did not filter.
    """
    other = build_single_commit_repo(tmp_path / "other")
    database = tmp_path / "shared.db"
    analyze(sample_repo, database)
    _write(
        sample_repo,
        database,
        statement="About the sample repository.",
        subject=Subject(kind="repository"),
        admitted_at=MOMENT,
    )
    analyze(other, database)

    result = _run(other, database, "export")

    assert result.exit_code == 0, result.stderr
    assert "also holds memories made about" in result.stderr
    assert "(1 memory)" in result.stderr
    assert str(sample_repo.resolve()) in result.stderr
    lines = _lines(result.stdout)
    assert len(lines) == 1
    assert lines[0]["count"] == 0
    assert lines[0]["repository"] == str(other.resolve())


# --- the module's own rules --------------------------------------------------


def test_writing_refuses_an_existing_file_without_the_flag(tmp_path: Path) -> None:
    """The rule has one home, and this reaches it without a command line."""
    target = tmp_path / "there.jsonl"
    target.write_text("kept\n", encoding="utf-8")

    with pytest.raises(MemoryExportError) as refused:
        write_document("replacement\n", target, replace=False)

    assert "does not replace a file it did not write" in str(refused.value)
    assert target.read_text(encoding="utf-8") == "kept\n"

    write_document("replacement\n", target, replace=True)
    assert target.read_text(encoding="utf-8") == "replacement\n"


def test_the_header_counts_the_memories_and_names_the_format(
    sample_repo: Path, database: Path
) -> None:
    """``count`` is the integrity check the reader will make, written by the writer.

    The reader is the import unit's; what can be held now is that the writer
    never produces a file that would fail it.
    """
    for index in range(3):
        _write(
            sample_repo,
            database,
            statement=f"Statement number {index}.",
            subject=Subject(kind="repository"),
            admitted_at=MOMENT.replace(microsecond=index),
        )

    lines = _lines(_run(sample_repo, database, "export").stdout)

    assert lines[0]["count"] == len(lines) - 1 == 3
    assert lines[0][VERSION_KEY] == FORMAT_VERSION


def test_the_document_is_a_function_of_its_arguments(
    sample_repo: Path, database: Path
) -> None:
    """Same store, same moment, same bytes — with no clock read inside.

    ``build_document`` takes the moment rather than reading it, which is what
    makes the determinism claim checkable instead of asserted: this test pins the
    bytes exactly, and the empty store is the case that pins the header alone.
    """
    with closing(connect(database)) as connection:
        memories = read_memories(connection, sample_repo, include_ended=True)

    repository = str(sample_repo.resolve())
    first = build_document(memories, repository=repository, exported_at=MOMENT)
    second = build_document(memories, repository=repository, exported_at=MOMENT)

    assert first == second
    assert first == (
        json.dumps(
            {
                VERSION_KEY: FORMAT_VERSION,
                "repository": repository,
                "count": 0,
                "exported_at": "2026-10-08T09:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
