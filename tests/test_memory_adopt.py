"""The sixth act, and the two real-world states it exists for.

A repository that moved, and a database two repositories were pointed at. Both
leave memories that name a path this checkout no longer has, and in both the
tool's answer is the same: the rows are *kept*, the read says whose they are,
and `memory adopt --from` is the explicit act that says "these are the same
project, at a new path".

The recipe a moved repository follows is the thing this file tests end to end —
it has three steps and a middle state, and every one of them is asserted, because
a recipe nobody runs is a recipe that is wrong in the second step.
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.memory import read_memories
from codearchaeology.storage import connect
from sample_repo import build_sample_repo, build_single_commit_repo

runner = CliRunner()

STATEMENT = "Login lives in core/app.py."


@pytest.fixture
def moved(tmp_path: Path):
    """A repository with a memory in it, moved to a second path.

    The move is a real one — the directory is renamed — because that is what a
    person does and it is what breaks the database's name: the cache file is
    keyed on a digest of the absolute path, so the tool looks for a different
    file afterwards.
    """
    root = tmp_path / "work"
    root.mkdir()
    before = build_sample_repo(root / "A").resolve()
    database = tmp_path / "history.db"
    analyze(before, database)

    created = _run(before, database, "create", STATEMENT, "--about-path", "core/app.py")
    assert created.exit_code == 0, created.stderr

    after = root / "moved" / "A"
    after.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(before), str(after))
    return before.resolve(), after.resolve(), database


def _run(repository: Path, database: Path | None, *arguments: str):
    base = ["memory", *arguments, str(repository)]
    if database is not None:
        base += ["--db", str(database)]
    return runner.invoke(app, base)


def _statements(database: Path, repository: Path) -> list[str]:
    connection = connect(database)
    try:
        return [memory.statement for memory in read_memories(connection, repository, include_ended=True)]
    finally:
        connection.close()


# --- the move, step by step --------------------------------------------------


def test_a_moved_repository_looks_for_a_database_that_does_not_exist(
    moved, tmp_path: Path
) -> None:
    """The hazard, pinned: the memories are in the old file and the tool is not
    looking at it.

    Nothing is lost — the file is still there — but a checkout at a new path
    gets a new database name, so the default lookup finds an empty one and says
    the only true thing it can: there is no analysis here yet.
    """
    _, after, _ = moved

    result = runner.invoke(
        app,
        ["memory", "list", str(after), "--db", str(tmp_path / "elsewhere.db")],
    )

    assert result.exit_code == 1
    assert "no analysis" in result.stderr
    assert "archaeology analyze" in result.stderr


def test_the_old_file_still_holds_them_and_says_whose_they_are(moved) -> None:
    """The middle state: the evidence is this repository's, the memories are not.

    This is the step a person is most likely to misread, so the read says it in
    words on stderr — the block itself stays what it is, and the count and the
    path are the whole of what is added.
    """
    before, after, database = moved
    assert runner.invoke(app, ["analyze", str(after), "--db", str(database)]).exit_code == 0

    listed = _run(after, database, "list")
    written = _run(after, database, "create", "Another one.", "--about-repository")

    assert "Memories (0)" in listed.stdout
    assert f"also holds memories made about {before} (1 memory)" in listed.stderr
    assert written.exit_code == 1
    assert "one repository's memories" in written.stderr
    assert f"memory adopt --from {before}" in written.stderr


def test_analyze_says_what_it_is_about_to_leave_behind(moved) -> None:
    """The note at the moment it happens, and the act that finishes the recipe.

    `analyze --db` is the one thing the tool itself does that can leave memories
    naming a path nobody is looking at. It is not destruction — the rows stay —
    but a person should hear it here rather than discover it later.
    """
    before, after, database = moved

    result = runner.invoke(app, ["analyze", str(after), "--db", str(database)])

    assert result.exit_code == 0
    assert f"also holds memories made about {before} (1 memory)" in result.stderr
    assert f"memory adopt --from {before}" in result.stderr
    assert "Note:" not in result.stdout


def test_adopt_moves_them_and_says_what_it_moved(moved) -> None:
    """The act itself, and the whole recipe read back afterwards."""
    before, after, database = moved
    runner.invoke(app, ["analyze", str(after), "--db", str(database)])

    adopted = _run(after, database, "adopt", "--from", str(before))

    assert adopted.exit_code == 0, adopted.stderr
    assert "Adopted     1 memory" in adopted.stdout
    assert f"From        {before}" in adopted.stdout
    assert f"To          {after}" in adopted.stdout

    listed = _run(after, database, "list")
    assert "Memories (1)" in listed.stdout
    assert listed.stderr == ""
    assert _statements(database, after) == [STATEMENT]


def test_adopt_changes_nothing_but_the_repository(moved) -> None:
    """Statements, authors, times, citations and states are kept as written."""
    before, after, database = moved
    runner.invoke(app, ["analyze", str(after), "--db", str(database)])

    connection = connect(database)
    try:
        was = read_memories(connection, before, include_ended=True)[0]
    finally:
        connection.close()

    _run(after, database, "adopt", "--from", str(before))

    connection = connect(database)
    try:
        now = read_memories(connection, after, include_ended=True)[0]
    finally:
        connection.close()

    assert now.memory_id == was.memory_id
    assert now.statement == was.statement
    assert now.author_name == was.author_name
    assert now.author_email == was.author_email
    assert now.admitted_at == was.admitted_at
    assert now.subject == was.subject
    assert now.state == was.state
    assert now.citations == was.citations
    assert now.repository_path == str(after)
    assert was.repository_path == str(before)


def test_adopt_records_that_it_happened(moved) -> None:
    """The store says an adoption happened rather than looking as though the rows
    were always this repository's."""
    before, after, database = moved
    runner.invoke(app, ["analyze", str(after), "--db", str(database)])
    _run(after, database, "adopt", "--from", str(before))

    connection = connect(database)
    try:
        recorded = dict(
            connection.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'memory_adopted%'"
            ).fetchall()
        )
    finally:
        connection.close()

    assert recorded["memory_adopted_from"] == str(before)
    assert recorded["memory_adopted_at"]


# --- what adopt refuses ------------------------------------------------------


def test_adopt_refuses_a_path_nothing_was_made_about(
    sample_repo: Path, tmp_path: Path
) -> None:
    """A typo cannot match, which is the whole of the safety there is."""
    database = tmp_path / "history.db"
    analyze(sample_repo, database)

    result = _run(sample_repo, database, "adopt", "--from", str(tmp_path / "nowhere"))

    assert result.exit_code == 1
    assert "nothing in this database was made about" in result.stderr


def test_adopt_refuses_the_path_it_is_already(sample_repo: Path, tmp_path: Path) -> None:
    database = tmp_path / "history.db"
    analyze(sample_repo, database)
    _run(sample_repo, database, "create", STATEMENT, "--about-path", "core/app.py")

    result = _run(sample_repo, database, "adopt", "--from", str(sample_repo))

    assert result.exit_code == 1
    assert "is already this repository's path" in result.stderr


def _write_by_hand(database: Path, repository: Path, statement: str) -> None:
    """Put one row in the table directly.

    The only way to reach this state: the tool refuses a write when the database
    holds another path's memories, so a database holding two repositories' rows
    is one somebody edited by hand. The rule about merging has to be tested
    anyway — a hand is exactly who would try it.
    """
    connection = connect(database)
    try:
        connection.execute(
            "INSERT INTO memories (memory_id, repository_path, statement,"
            " admitted_at, admitted_epoch, subject_kind, state)"
            " VALUES (?, ?, ?, ?, ?, 'repository', 'active')",
            (
                "b" * 36,
                str(Path(repository).resolve()),
                statement,
                "2024-06-01T09:00:00+00:00",
                1717232400,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_adopt_refuses_to_merge_two_repositories_memories(
    sample_repo: Path, tmp_path: Path
) -> None:
    """The destination holding memories of its own is a merge, and this version
    answers a merge by refusing rather than by guessing."""
    other = build_single_commit_repo(tmp_path / "other")
    database = tmp_path / "shared.db"
    analyze(other, database)
    _run(other, database, "create", "About the other one.", "--about-repository")
    _write_by_hand(database, sample_repo, STATEMENT)

    result = _run(other, database, "adopt", "--from", str(sample_repo))

    assert result.exit_code == 1
    assert "does not merge two repositories' memories" in result.stderr


def test_adopt_needs_the_path_in_full(moved) -> None:
    """A prefix is not a path, and the act requires what was actually written."""
    before, after, database = moved
    runner.invoke(app, ["analyze", str(after), "--db", str(database)])

    result = _run(after, database, "adopt", "--from", str(before)[:-1])

    assert result.exit_code == 1
    assert "nothing in this database was made about" in result.stderr


def test_adopt_without_from_is_a_usage_error(moved) -> None:
    _, after, database = moved

    result = _run(after, database, "adopt")

    assert result.exit_code == 2
    assert "--from" in result.stderr


# --- the other state it repairs ----------------------------------------------


def test_a_shared_database_is_repaired_by_adopting_the_foreign_rows(
    sample_repo: Path, tmp_path: Path
) -> None:
    """`--db` pointed at one file by two repositories: the write is refused and
    adopt is the way through, one path at a time."""
    other = build_single_commit_repo(tmp_path / "other")
    database = tmp_path / "shared.db"
    analyze(sample_repo, database)
    _run(sample_repo, database, "create", STATEMENT, "--about-path", "core/app.py")

    analyze(other, database)
    refused = _run(other, database, "create", "About the other one.", "--about-repository")
    assert refused.exit_code == 1

    adopted = _run(other, database, "adopt", "--from", str(sample_repo))
    assert adopted.exit_code == 0, adopted.stderr

    listed = _run(other, database, "list")
    assert "Memories (1)" in listed.stdout
    assert listed.stderr == ""
    assert _statements(database, other) == [STATEMENT]
