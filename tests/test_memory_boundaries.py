"""The boundary: memory may not reach the evidence, and a model may not reach memory.

The risk this file exists for is not that a memory cannot be written. It is that
memory leaks the *other* way — into a bundle, into a citation, into a prompt, or
into the store through a model's answer — because every one of those would be
invisible. A bundle with a memory row in it looks exactly like a bundle, and an
answer that cites a memory as a fact reads exactly like an answer that cites a
commit.

Six directions, and what holds each one:

| The direction | What holds it |
|---|---|
| `analyze` → memory | nothing on the evidence side reads it: every evidence command runs with the memory store broken |
| `ast` → memory | the same, and the memory rows are still there afterwards |
| `context` → memory | the bundle is byte-identical with memories and without them |
| `selection` → memory | the view the model is sent is byte-identical too |
| `provider` → memory | one module in the package can call a writing act, and it is the command line |
| `validator` → memory as evidence | the semantic pass refuses it, for free, because memory is not in the bundle |

And one direction that **cannot** be held, stated here rather than papered over: a
model that paraphrases a memory into an observation, with a real citation beside
it, passes every pass there is. The sentence is the model's, the citation is one
the bundle really holds, and no program can tell where the sentence came from.
What covers that is the labelled section and the reader, which is the residual
risk `docs/v0.5-design.md` §11.2 names.
"""

import ast
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.context import build_context, build_object
from codearchaeology.context import build_json as build_context_json
from codearchaeology.explanation import INTERPRETATION_NOTE
from codearchaeology.memory import (
    MEMORY_VERSION_KEY,
    Citation,
    Subject,
    admit,
    invalidate,
    read_memories,
)
from codearchaeology.memory_view import NOTE
from codearchaeology.selection import build_view
from codearchaeology.storage import connect, set_meta
from codearchaeology.validation import ExplanationError, validate
from sample_repo import build_sample_repo
from test_provider import _completion, _endpoint

runner = CliRunner()

SOURCE = Path(__file__).resolve().parent.parent / "src" / "codearchaeology"

FIXED = "Fix login bug"
INITIAL = "Initial commit"

# The two statements the fixture keeps, chosen to be findable: a bundle that
# leaked one would be caught by looking for its words, not only by its shape.
MEMORY_STATEMENT = (
    "The login helper stays in one module: the split state machine caused an"
    " incident."
)
PROJECT_STATEMENT = "We do not add runtime dependencies casually."
ENDED_STATEMENT = "The health check belonged in core/app.py."

# The three acts that write. Nothing but the command line may call one of them.
WRITING_ACTS = ("admit", "supersede", "invalidate")


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture, analyzed, read, and with memories in it.

    Module-scoped because the expensive part is the analysis, and nothing in
    this file writes to it: the tests that need a broken store or a store with
    no memories take a copy.
    """
    repository = build_sample_repo(tmp_path_factory.mktemp("boundaries"))
    database = repository.parent / "boundaries.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    connection = connect(database)
    try:
        sha = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (FIXED,)
        ).fetchone()["sha"]
        first = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (INITIAL,)
        ).fetchone()["sha"]
        admit(
            connection,
            repository_path=repository,
            statement=MEMORY_STATEMENT,
            subject=Subject("path", path="core/app.py"),
            since_commit_sha=first,
            author_name="Ada Lovelace",
            author_email="ada@example.com",
            citations=[Citation("commit", sha[:8]), Citation("file", "core/app.py")],
        )
        admit(
            connection,
            repository_path=repository,
            statement=PROJECT_STATEMENT,
            subject=Subject("repository"),
            author_name="Ada Lovelace",
            author_email="ada@example.com",
        )
        ended = admit(
            connection,
            repository_path=repository,
            statement=ENDED_STATEMENT,
            subject=Subject("path", path="core/app.py"),
            author_name="Ada Lovelace",
            author_email="ada@example.com",
        )
        invalidate(
            connection,
            ended.memory_id,
            repository_path=repository,
            reason="it moved into its own module",
        )
    finally:
        connection.close()

    return repository, database, sha


@pytest.fixture
def broken(tmp_path: Path, analyzed):
    """A copy of the analyzed database whose memory store cannot be read.

    The stamp is from a newer tool, which is the one state the store itself
    refuses to guess at. It is the honest way to ask "does the evidence side
    depend on memory?" — not a mock and not a monkeypatch, but a database a
    reader would really meet, with the memory commands refusing on one side of it
    and the evidence commands having to work on the other.
    """
    repository, database, sha = analyzed
    path = tmp_path / "broken.db"
    shutil.copy(database, path)
    connection = connect(path)
    try:
        set_meta(connection, MEMORY_VERSION_KEY, "9")
    finally:
        connection.close()
    return repository, path, sha


def _run(repository, database, *arguments: str):
    return runner.invoke(app, [*arguments, str(repository), "--db", str(database)])


def _memory_id_of(database: Path, repository, statement: str) -> str:
    """The id of the fixture's memory that says *statement*.

    Looked up by what it says rather than by its position in a list, so a test
    that relates an answer to a memory names the one it means even if the order
    of admissions ever changes.
    """
    connection = connect(database)
    try:
        for memory in read_memories(connection, repository, include_ended=True):
            if memory.statement == statement:
                return memory.memory_id
    finally:
        connection.close()
    raise AssertionError(f"the fixture keeps no memory saying {statement!r}")


def _memory_rows(database: Path) -> tuple:
    """Every row of both memory tables, as the store holds them."""
    connection = connect(database)
    try:
        memories = [
            tuple(row) for row in connection.execute("SELECT * FROM memories ORDER BY memory_id")
        ]
        citations = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM memory_citations ORDER BY memory_id, position"
            )
        ]
    finally:
        connection.close()
    return (tuple(memories), tuple(citations))


def _without_memories(database: Path, destination: Path) -> Path:
    """A copy of the database with the memory rows deleted, and the stamp kept."""
    shutil.copy(database, destination)
    connection = connect(destination)
    try:
        connection.execute("DELETE FROM memory_citations")
        connection.execute("DELETE FROM memories")
        connection.commit()
    finally:
        connection.close()
    return destination


def _imports(path: Path) -> set[str]:
    """The top-level modules *path* imports, read out of its own source."""
    tree = ast.parse(path.read_bytes())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    return imported


def _imported_names(path: Path) -> set[tuple[str, str]]:
    """Every ``from module import name`` in *path*, read out of its own source."""
    tree = ast.parse(path.read_bytes())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                found.add((node.module or "", alias.name))
    return found


def _paths_of(value, needle: str, path: str = "$") -> list[str]:
    """Every place a string appears in a document, as a path a reader can follow."""
    if isinstance(value, dict):
        return [
            found
            for key, item in value.items()
            for found in _paths_of(item, needle, f"{path}.{key}")
        ]
    if isinstance(value, list):
        return [
            found
            for index, item in enumerate(value)
            for found in _paths_of(item, needle, f"{path}[{index}]")
        ]
    if isinstance(value, str) and needle in value:
        return [path]
    return []


def _answer(sha: str, *, related=None) -> str:
    document = {
        "summary": "The commit changed core/app.py, adding six lines [oc1].",
        "observed_changes": [
            {
                "id": "oc1",
                "statement": "core/app.py was modified in this commit",
                "evidence": ["ev1"],
            }
        ],
        "evidence": [{"id": "ev1", "kind": "file", "ref": "core/app.py"}],
        "possible_reasons": [],
        "uncertainty": [],
    }
    if related is not None:
        document["related_memory"] = related
    return json.dumps(document)


def _explained(repository, database, sha: str, answer: str, *flags: str):
    """Run ``explain`` against a scripted model, and return the result."""
    with _endpoint({"status": 200, "body": _completion(answer)}) as server:
        return runner.invoke(
            app,
            ["explain", sha[:8], str(repository), "--db", str(database), *flags],
            env={
                "CODEARCHAEOLOGY_AI_BASE_URL": server.base_url,
                "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            },
        )


# --- the evidence side runs with memory broken -------------------------------


def _evidence_commands(repository: Path, sha: str) -> dict:
    """Every command that must answer with the memory store unreadable."""
    root = str(repository)
    return {
        "analyze": ["analyze", root],
        "ast": ["ast", root],
        "timeline": ["timeline", root],
        "hotspots": ["hotspots", root],
        "files": ["files", root],
        "file": ["file", "core/app.py", root],
        "commit": ["commit", sha[:8], root],
        "structure": ["structure", "core/app.py", root],
        "structure --history": ["structure", "core/app.py", "--history", root],
        "cochange": ["cochange", "core/app.py", root],
        "explain": ["explain", sha[:8], root],
    }


@pytest.mark.parametrize(
    "name",
    [
        "analyze",
        "ast",
        "timeline",
        "hotspots",
        "files",
        "file",
        "commit",
        "structure",
        "structure --history",
        "cochange",
        "explain",
    ],
)
def test_every_evidence_command_works_with_memory_unreadable(
    broken, name: str
) -> None:
    """The strongest form of "the evidence never reads memory": break it and prove
    nothing notices.

    This is ``test_offline.py``'s device pointed the other way. There the network
    is taken away and every command has to work; here the memory store is made
    unreadable — by a version stamp from a newer tool, which is a state a reader
    really meets — and the whole evidence side has to keep working. A module that
    quietly read the store would fail here rather than in somebody's terminal.
    """
    repository, database, sha = broken
    # The command lines carry the repository themselves, the way the README's
    # blocks do, so only the database is added here.
    arguments = _evidence_commands(repository, sha)[name]

    result = runner.invoke(app, [*arguments, "--db", str(database)])

    assert result.exit_code == 0, result.stderr or result.output
    assert result.stdout.strip(), "a command that answers with nothing is not working"


def test_the_break_is_real_and_the_memory_commands_refuse(broken) -> None:
    """The guard on the guard: a store that was still readable would make the
    test above pass while proving nothing."""
    repository, database, _ = broken

    result = _run(repository, database, "memory", "list")

    assert result.exit_code == 1
    assert "newer tool" in result.stderr
    assert "memory" not in result.stdout.lower()


def test_analyze_leaves_a_broken_store_exactly_as_it_found_it(broken) -> None:
    """Rewriting the evidence may not repair, drop or re-stamp memory."""
    repository, database, _ = broken
    before = _memory_rows(database)

    assert _run(repository, database, "analyze").exit_code == 0
    assert _run(repository, database, "ast").exit_code == 0

    assert _memory_rows(database) == before
    connection = connect(database)
    try:
        assert (
            connection.execute(
                "SELECT value FROM meta WHERE key = ?", (MEMORY_VERSION_KEY,)
            ).fetchone()["value"]
            == "9"
        )
    finally:
        connection.close()


# --- determinism: the bundle does not know memory exists ---------------------


def test_the_bundle_is_byte_identical_with_and_without_memories(
    analyzed, tmp_path: Path
) -> None:
    """Two databases, one repository, one commit: the same bytes.

    Not "the same fields" — the same text. A bundle that carried a count, an
    ordering or a single row influenced by what people wrote would be a bundle
    the evidence no longer explains, and the comparison is a string because a
    structural one would miss a reordered key.
    """
    repository, database, sha = analyzed
    stripped = _without_memories(database, tmp_path / "no-memories.db")

    assert build_context_json(build_context(repository, database, sha)) == (
        build_context_json(build_context(repository, stripped, sha))
    )


def test_the_view_the_model_is_sent_is_byte_identical_too(
    analyzed, tmp_path: Path
) -> None:
    """``selection`` reduces the bundle for the model; memory may not enter it."""
    repository, database, sha = analyzed
    stripped = _without_memories(database, tmp_path / "no-memories.db")

    assert build_view(build_context(repository, database, sha)).json == (
        build_view(build_context(repository, stripped, sha)).json
    )


def test_no_memory_reaches_the_bundle_or_the_view(analyzed) -> None:
    """The words, not only the shape: nothing a person wrote is in there."""
    repository, database, sha = analyzed
    context = build_context(repository, database, sha)
    everything = f"{build_context_json(context)}\n{build_view(context).json}"

    for statement in (MEMORY_STATEMENT, PROJECT_STATEMENT, ENDED_STATEMENT):
        assert statement not in everything
    assert "memory" not in build_object(context)


def _reading_commands(repository: Path, sha: str) -> dict:
    """Every command that only reads, with its arguments.

    ``explain`` is not here: the offline answer *does* carry the memory section,
    by design, and the test below it is the one that holds that difference to a
    single key.
    """
    root = str(repository)
    return {
        "timeline": ["timeline", root],
        "hotspots": ["hotspots", root],
        "files": ["files", root],
        "file": ["file", "core/app.py", root],
        "commit": ["commit", sha[:8], root],
        "structure": ["structure", "core/app.py", root],
        "structure --history": ["structure", "core/app.py", "--history", root],
        "cochange": ["cochange", "core/app.py", root],
    }


@pytest.mark.parametrize(
    "name",
    [
        "timeline",
        "hotspots",
        "files",
        "file",
        "commit",
        "structure",
        "structure --history",
        "cochange",
    ],
)
def test_every_reading_command_is_byte_identical_with_and_without_memories(
    analyzed, tmp_path: Path, name: str
) -> None:
    """Determinism over the whole read side, not only the bundle.

    One repository, one commit, two databases differing in exactly one thing:
    one holds memories and one does not. Every reading command is run against
    both and the output is compared as text, so a count, an ordering or a row
    that moved anywhere on the read side because of what people wrote fails
    here — and the failure names the command.
    """
    repository, database, sha = analyzed
    stripped = _without_memories(database, tmp_path / "no-memories.db")
    arguments = _reading_commands(repository, sha)[name]

    with_memory = runner.invoke(app, [*arguments, "--db", str(database)])
    without = runner.invoke(app, [*arguments, "--db", str(stripped)])

    assert with_memory.exit_code == 0, with_memory.stderr
    assert with_memory.stdout == without.stdout
    assert with_memory.stderr == without.stderr


def test_the_offline_answer_differs_only_by_the_memory_key(
    analyzed, tmp_path: Path
) -> None:
    """The one place memory is added is beside the bundle, and it is one key."""
    repository, database, sha = analyzed
    stripped = _without_memories(database, tmp_path / "no-memories.db")

    with_memory = json.loads(_run(repository, database, "explain", sha[:8]).stdout)
    without = json.loads(_run(repository, stripped, "explain", sha[:8]).stdout)

    assert set(with_memory) - set(without) == {"memory"}
    assert without == build_object(build_context(repository, stripped, sha))
    del with_memory["memory"]
    assert with_memory == without


# --- the model may not write memory ------------------------------------------


def test_a_model_run_leaves_the_store_exactly_as_it_was(analyzed) -> None:
    """The loop is broken at the confirmation edge, and this is the edge.

    The answer below is written the way a model that wanted to be helpful would
    write one: it restates a memory, it offers one, and it relates itself to one
    by id. None of that may reach the store — the only thing a model can do is
    produce text, and text is checked, printed and dropped.
    """
    repository, database, sha = analyzed
    memory_id = _memory_id_of(database, repository, MEMORY_STATEMENT)
    before = _memory_rows(database)

    answer = json.dumps(
        {
            "summary": (
                "The commit changed core/app.py, adding six lines [oc1]. We should"
                " record that the login helper stays in one module [oc1]."
            ),
            "observed_changes": [
                {
                    "id": "oc1",
                    "statement": "core/app.py was modified in this commit",
                    "evidence": ["ev1"],
                }
            ],
            "evidence": [{"id": "ev1", "kind": "file", "ref": "core/app.py"}],
            "possible_reasons": [],
            "uncertainty": [],
            "related_memory": [
                {"memory_id": memory_id, "note": "the answer keeps login in one module"}
            ],
        }
    )

    result = _explained(repository, database, sha, answer)

    assert result.exit_code == 0, result.stderr
    assert _memory_rows(database) == before


def test_only_the_command_line_can_call_a_writing_act() -> None:
    """Who may write, held at the level of who may *call* the store.

    ``admit``, ``supersede`` and ``invalidate`` are the only ways a row is
    created or changed, so "only a person's explicit act writes memory" is a
    statement about which modules reach them. A model's answer travels
    provider → validation → the command line, and this is the test that fails if
    any of those three — or anything else in the package — starts writing.
    """
    modules = sorted(SOURCE.glob("*.py"))
    assert {"provider.py", "validation.py", "explanation.py", "selection.py"} <= {
        path.name for path in modules
    }

    writers = {
        path.name
        for path in modules
        if {
            name
            for module, name in _imported_names(path)
            if module == "codearchaeology.memory" and name in WRITING_ACTS
        }
    }

    assert writers == {"cli.py"}, f"these modules can write memory: {sorted(writers)}"


def test_the_provider_cannot_reach_the_store_at_all() -> None:
    """The module that talks to a model imports nothing from this package.

    It is handed a prompt and returns text. It has no database handle, no store
    and no path to either, so "the model never writes memory" is not a rule the
    provider obeys — it is a sentence about what the provider *is*. A later unit
    that gave it a connection would fail here before it could write anything.
    """
    assert _imports(SOURCE / "provider.py") == {
        "json",
        "os",
        "threading",
        "time",
        "urllib",
        "dataclasses",
        "typing",
    }


def test_the_writing_acts_are_reachable_at_all() -> None:
    """The guard on the guard: a scan that found nothing would pass above."""
    imported = {
        name
        for module, name in _imported_names(SOURCE / "cli.py")
        if module == "codearchaeology.memory"
    }

    assert set(WRITING_ACTS) <= imported


# --- the validator: memory may not impersonate evidence ----------------------


@pytest.mark.parametrize(
    "kind", ["commit", "file", "definition", "range", "cochange", "absence"]
)
def test_a_memory_id_is_refused_as_evidence_of_every_kind(
    analyzed, kind: str
) -> None:
    """One vocabulary, and a memory's id is not a word in it.

    The refusal is free — the pass checks every citation against the bundle and
    memory is not in the bundle — which is why the design chose this shape. What
    this test adds is that it is free for *every* kind, so a later unit that
    widened the bundle would break here rather than quietly admit one.
    """
    repository, database, sha = analyzed
    memory_id = _memory_id_of(database, repository, MEMORY_STATEMENT)
    context = build_context(repository, database, sha)

    document = json.loads(_answer(sha))
    document["evidence"] = [{"id": "ev1", "kind": kind, "ref": memory_id}]
    document["observed_changes"][0]["evidence"] = ["ev1"]

    with pytest.raises(ExplanationError, match="not in the evidence for this commit"):
        validate(json.dumps(document), context)


def test_a_memory_id_the_model_was_not_shown_is_refused(analyzed) -> None:
    """``related_memory`` is checked against the section, not against the store."""
    repository, database, sha = analyzed
    context = build_context(repository, database, sha)
    invented = "3f2a9c1e-8b47-4d6a-9f10-2c5e7a0b41d9"

    with pytest.raises(ExplanationError, match="not in the memory section"):
        validate(
            _answer(sha, related=[{"memory_id": invented, "note": "x"}]),
            context,
            frozenset(),
        )


def test_a_memory_statement_written_as_an_observation_is_not_catchable(
    analyzed,
) -> None:
    """The residual risk, pinned so it is a known gap and not a discovery.

    This answer paraphrases a memory into an observed change and cites a row the
    bundle really holds. Every pass accepts it: the sentence is the model's, the
    citation is real, and nothing in the tool can tell where a sentence came
    from — there is no field for provenance of prose and there cannot be one.
    ``docs/v0.5-design.md`` §11.2 names this as the risk that cannot be
    engineered away; what *is* engineered is that the reader meets the memory
    section separately, printed by the tool from the store, so the claim and the
    statement are two things on the page rather than one.

    A test that asserted a refusal here would be asserting a defence the tool
    does not have, which is the more expensive of the two mistakes.
    """
    repository, database, sha = analyzed
    context = build_context(repository, database, sha)

    answer = json.dumps(
        {
            "summary": "The commit changed core/app.py, keeping login in one module [oc1].",
            "observed_changes": [
                {
                    "id": "oc1",
                    "statement": MEMORY_STATEMENT,
                    "evidence": ["ev1"],
                }
            ],
            "evidence": [{"id": "ev1", "kind": "file", "ref": "core/app.py"}],
            "possible_reasons": [],
            "uncertainty": [],
        }
    )

    explanation = validate(answer, context)

    assert explanation.observed_changes[0].statement == MEMORY_STATEMENT

    # What holds instead: the tool prints the statement itself, from the store,
    # under its own heading. The same words therefore appear twice in one block —
    # once as the model's reading and once as the record — and the reader can see
    # both, which is the whole of the defence there is.
    result = _explained(repository, database, sha, answer)
    assert result.exit_code == 0, result.stderr
    reading, _, section = result.stdout.partition("Memory (")

    assert MEMORY_STATEMENT in reading
    assert MEMORY_STATEMENT in section


# --- the rendering: three kinds, three labels --------------------------------


def test_the_three_kinds_are_each_labelled_in_one_block(analyzed) -> None:
    """Historical evidence, confirmed memory and AI interpretation, in one place.

    A reader must be able to tell which of the three they are reading without
    knowing the tool: the answer opens by saying it is a reading, the evidence is
    under its own headings with the citations behind each claim, and the memory
    section has its own heading and ends by saying what a memory is.
    """
    repository, database, sha = analyzed
    memory_id = _memory_id_of(database, repository, MEMORY_STATEMENT)

    result = _explained(
        repository,
        database,
        sha,
        _answer(sha, related=[{"memory_id": memory_id, "note": "the answer keeps login in one module"}]),
    )

    assert result.exit_code == 0, result.stderr
    block = result.stdout

    # The interpretation, labelled before the first claim.
    assert block.startswith(INTERPRETATION_NOTE)
    # The evidence, under the headings the renderer owns.
    assert "Observed (1)" in block
    assert "Possible (0)" in block
    # The memory, labelled as what it is, and closing with the same sentence the
    # memory commands end with. Three are related to this commit and one of them
    # provably held when it was written.
    assert "Memory (1 in force, 2 related)" in block
    assert NOTE in block
    # The model's sentence is marked as the reading, and the statement above it
    # is the store's own text.
    assert "related by the model: the answer keeps login in one module" in block
    assert block.index(MEMORY_STATEMENT) < block.index("related by the model:")


def test_a_memory_is_never_printed_among_the_observations(analyzed) -> None:
    """The section comes after the answer, and the statement only there."""
    repository, database, sha = analyzed
    memory_id = _memory_id_of(database, repository, MEMORY_STATEMENT)

    result = _explained(
        repository, database, sha, _answer(sha, related=[{"memory_id": memory_id, "note": "x"}])
    )

    answer, _, section = result.stdout.partition("Memory (")
    assert MEMORY_STATEMENT not in answer
    assert PROJECT_STATEMENT not in answer
    assert MEMORY_STATEMENT in section


def test_the_json_keeps_the_three_in_separate_keys(analyzed) -> None:
    """And the memory's id appears in exactly the two places that may carry it."""
    repository, database, sha = analyzed
    memory_id = _memory_id_of(database, repository, MEMORY_STATEMENT)

    result = _explained(
        repository,
        database,
        sha,
        _answer(sha, related=[{"memory_id": memory_id, "note": "the answer keeps login in one module"}]),
        "--json",
    )

    assert result.exit_code == 0, result.stderr
    document = json.loads(result.stdout)
    assert set(document) == {"commit", "state", "explanation", "evidence", "memory"}
    assert "explanation" in document and document["explanation"] is not None

    found = _paths_of(document, memory_id)
    assert found
    assert all(
        path.startswith("$.memory") or path.startswith("$.explanation.related_memory")
        for path in found
    ), found

    # And the bundle under `evidence` carries no word of what people wrote.
    assert MEMORY_STATEMENT not in json.dumps(document["evidence"])
    assert PROJECT_STATEMENT not in json.dumps(document["explanation"])


def test_the_memory_section_is_the_store_and_the_note_is_the_model(analyzed) -> None:
    """The one sentence a model may contribute is marked, and it is a sentence.

    The statement is printed from the store, byte for byte; the model's note is
    printed under it and named as the model's. A model never paraphrases a memory
    into the record, and the two lines are different lines.
    """
    repository, database, sha = analyzed
    memory_id = _memory_id_of(database, repository, MEMORY_STATEMENT)
    note = "this is the model's own sentence, and it is not the statement"

    result = _explained(
        repository, database, sha, _answer(sha, related=[{"memory_id": memory_id, "note": note}])
    )

    assert result.exit_code == 0, result.stderr
    assert f"related by the model: {note}" in result.stdout
    assert MEMORY_STATEMENT in result.stdout

    document = json.loads(
        _explained(
            repository,
            database,
            sha,
            _answer(sha, related=[{"memory_id": memory_id, "note": note}]),
            "--json",
        ).stdout
    )
    section = document["memory"]
    assert section["in_force"][0]["statement"] == MEMORY_STATEMENT
    assert document["explanation"]["related_memory"] == [
        {"memory_id": memory_id, "note": note}
    ]
