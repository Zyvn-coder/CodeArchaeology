"""What the store does when something is wrong with it, or with the act.

Six situations a real store meets, and the rule each one follows. Five of them
are about a state only a hand can make — the tool never leaves the store in any
of them — and the answer to all five is the same one the design wrote down:
**report, never repair, and never read as empty something that is not**.

The sixth is the opposite kind of case and is here for balance: writing the same
statement twice is *not* a mistake. Identity is the id and not the content, and
two rows saying the same thing are two admissions, which is what a store of what
people said has to be able to hold.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.memory import (
    MEMORY_SCHEMA_VERSION,
    MEMORY_TABLES,
    MEMORY_VERSION_KEY,
    Citation,
    MemoryStoreError,
    Subject,
    adopt,
    admit,
    find_memory,
    invalidate,
    prepare_memory,
    read_memories,
    supersede,
)
from codearchaeology.storage import connect, set_meta
from sample_repo import build_sample_repo

runner = CliRunner()

EARLY = datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)
STATEMENT = "Login lives in core/app.py."


@pytest.fixture
def database(sample_repo: Path, tmp_path: Path) -> Path:
    """An analyzed repository, with the memory tables ready."""
    path = tmp_path / "history.db"
    analyze(sample_repo, path)
    connection = connect(path)
    try:
        prepare_memory(connection)
    finally:
        connection.close()
    return path


def _write(database: Path, repository, statement=STATEMENT, **fields):
    connection = connect(database)
    try:
        return admit(
            connection,
            repository_path=repository,
            statement=statement,
            subject=fields.pop("subject", Subject("path", path="core/app.py")),
            admitted_at=fields.pop("admitted_at", EARLY),
            **fields,
        )
    finally:
        connection.close()


def _rows(database: Path, table: str) -> list[tuple]:
    connection = connect(database)
    try:
        return [
            tuple(row)
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ]
    finally:
        connection.close()


# --- a shape that is not the one the stamp claims ----------------------------


def test_a_store_whose_tables_are_gone_is_refused_not_read_as_empty(
    database: Path, sample_repo: Path
) -> None:
    """The stamp is a claim about a shape, and the shape has to be there.

    Only a hand can drop the tables while the stamp stays, and the tempting
    answer — "no tables, no memories" — is the one that loses knowledge
    silently: it would tell somebody who wrote a hundred memories that they
    never wrote any. The store says it holds memories, so it is asked to account
    for them, and it cannot.
    """
    _write(database, sample_repo)
    connection = connect(database)
    try:
        for table in MEMORY_TABLES:
            connection.execute(f"DROP TABLE {table}")
        connection.commit()
    finally:
        connection.close()

    connection = connect(database)
    try:
        with pytest.raises(MemoryStoreError, match="the tables are not there"):
            read_memories(connection, sample_repo)
        with pytest.raises(MemoryStoreError, match="Copy the file before changing it"):
            admit(
                connection,
                repository_path=sample_repo,
                statement=STATEMENT,
                subject=Subject("repository"),
            )
    finally:
        connection.close()


def test_a_stamp_from_a_newer_tool_refuses_memory_and_leaves_the_evidence(
    database: Path, sample_repo: Path
) -> None:
    """The other mismatch, and the rule that the evidence does not pay for it."""
    _write(database, sample_repo)
    connection = connect(database)
    try:
        set_meta(connection, MEMORY_VERSION_KEY, "9")
    finally:
        connection.close()

    listed = runner.invoke(
        app, ["memory", "list", str(sample_repo), "--db", str(database)]
    )
    timeline = runner.invoke(
        app, ["timeline", str(sample_repo), "--db", str(database)]
    )

    assert listed.exit_code == 1
    assert "newer tool" in listed.stderr
    assert timeline.exit_code == 0
    assert "2024-03-01" in timeline.stdout


def test_a_database_that_is_not_a_database_is_a_sentence_for_memory_too(
    sample_repo: Path, tmp_path: Path
) -> None:
    """The recovery advice changed for a reason, and memory inherits it."""
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database, just some bytes.\n" * 8)

    result = runner.invoke(
        app, ["memory", "list", str(sample_repo), "--db", str(broken)]
    )

    assert result.exit_code == 1
    assert "could not be read" in result.stderr
    assert "Copy it somewhere safe" in result.stderr
    assert "Traceback" not in result.stderr


# --- a lifecycle a hand wrote ------------------------------------------------


def _hand_written(database: Path, memory_id: str, repository, **fields) -> None:
    values = {
        "statement": "Written by hand.",
        "subject_kind": "repository",
        "state": "active",
        "admitted_at": EARLY.isoformat(),
        "admitted_epoch": int(EARLY.timestamp()),
        "ended_at": None,
        "ended_epoch": None,
        "end_reason": None,
        "superseded_by": None,
    }
    values.update(fields)
    connection = connect(database)
    try:
        connection.execute(
            "INSERT INTO memories (memory_id, repository_path, statement, admitted_at,"
            " admitted_epoch, subject_kind, state, ended_at, ended_epoch, end_reason,"
            " superseded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                str(repository),
                values["statement"],
                values["admitted_at"],
                values["admitted_epoch"],
                values["subject_kind"],
                values["state"],
                values["ended_at"],
                values["ended_epoch"],
                values["end_reason"],
                values["superseded_by"],
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _link(database: Path, memory_id: str, successor: str) -> None:
    """Point one row at another, the way a hand would have to.

    The foreign key is real, so the row named has to exist first — which is why
    a loop takes two statements to make and cannot come out of an act.
    """
    connection = connect(database)
    try:
        connection.execute(
            "UPDATE memories SET state = 'superseded', ended_at = ?, ended_epoch = ?,"
            " superseded_by = ? WHERE memory_id = ?",
            (EARLY.isoformat(), int(EARLY.timestamp()), successor, memory_id),
        )
        connection.commit()
    finally:
        connection.close()


def test_a_cycle_a_hand_wrote_reads_without_hanging(database: Path, sample_repo: Path) -> None:
    """A CHECK cannot look at another row, so a loop is possible — and readable.

    The acts cannot make one (a successor is always a memory just admitted, and
    only an active memory can be acted on), so this is a hand's work. What
    matters is that nothing walks the chain: every read here is one query, so a
    loop is data rather than a hang, and the store reports what it holds instead
    of repairing it.
    """
    first, second = "a" * 36, "b" * 36
    _hand_written(database, first, sample_repo)
    _hand_written(database, second, sample_repo)
    _link(database, first, second)
    _link(database, second, first)

    connection = connect(database)
    try:
        found = read_memories(connection, sample_repo, include_ended=True)
        one = find_memory(connection, first[:8])
    finally:
        connection.close()

    assert {memory.memory_id for memory in found} == {first, second}
    assert one.superseded_by == second
    # Read from the other end: the successor's predecessor is derived, and a
    # loop simply says both are each other's.
    assert {memory.supersedes for memory in found} == {first, second}


def test_a_row_cannot_name_a_successor_that_is_not_there(
    database: Path, sample_repo: Path
) -> None:
    """The foreign key makes the state unreachable, and this is the test that says
    so rather than the read pretending it cannot happen.

    Unit 2 §4 chose no foreign key *into the evidence tables* — a cascade would
    delete memories and a plain key would make `analyze` fail — and the key
    *between* memories is a different thing and is real: a superseded row always
    names a row that exists.
    """
    _hand_written(database, "c" * 36, sample_repo)

    connection = connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE memories SET state = 'superseded', ended_at = ?, ended_epoch = ?,"
                " superseded_by = ? WHERE memory_id = ?",
                (EARLY.isoformat(), int(EARLY.timestamp()), "d" * 36, "c" * 36),
            )
    finally:
        connection.close()


# --- an act that did not finish ----------------------------------------------


def _refuse_updates(database: Path, when: str) -> None:
    """A trigger that fails a write in the middle of an act.

    A real failure from the database rather than a monkeypatched function: the
    statement is refused by SQLite itself, which is what a disk going away looks
    like to the code that issued it. ``when`` is the trigger's own condition, so
    each test says which write it breaks. The test holds the *message* rather
    than the exception's class: which of SQLite's errors a raised trigger
    becomes is the database's business, and what the act owes is that it raises
    and writes nothing.
    """
    connection = connect(database)
    try:
        connection.execute(
            "CREATE TRIGGER refuse BEFORE UPDATE ON memories"
            f" WHEN {when} BEGIN SELECT RAISE(FAIL, 'the disk went away'); END"
        )
        connection.commit()
    finally:
        connection.close()


def test_an_interrupted_adopt_moves_nothing(
    database: Path, sample_repo: Path, tmp_path: Path
) -> None:
    """One transaction per act, and adopt is an act.

    The failure lands after the rows have been updated and before the act is
    recorded, which is the worst moment: without a transaction the rows would
    have moved and the store would not say that anything happened.
    """
    old = str(tmp_path / "old-place")
    _write(database, old, "About the old path.")
    before = _rows(database, "memories")
    _refuse_updates(database, "OLD.repository_path <> NEW.repository_path")

    connection = connect(database)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="the disk went away"):
            adopt(connection, from_path=old, to_path=sample_repo)
    finally:
        connection.close()

    assert _rows(database, "memories") == before
    connection = connect(database)
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) AS held FROM meta WHERE key LIKE 'memory_adopted%'"
            ).fetchone()["held"]
            == 0
        )
    finally:
        connection.close()


def test_an_interrupted_supersede_writes_neither_row(
    database: Path, sample_repo: Path
) -> None:
    """The other two-row act, checked the same way: the old row is untouched.

    The successor is written first and the old row closed second, so failing the
    close is exactly what a half-done act would look like — and the successor
    must not be there afterwards either.
    """
    old = _write(database, sample_repo, "The first statement.")
    before = _rows(database, "memories")
    _refuse_updates(database, "NEW.state = 'superseded'")

    connection = connect(database)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="the disk went away"):
            supersede(
                connection,
                old.memory_id,
                repository_path=sample_repo,
                statement="The second statement.",
                subject=Subject("path", path="core/app.py"),
            )
    finally:
        connection.close()

    assert _rows(database, "memories") == before


# --- writing the same thing twice --------------------------------------------


def test_the_same_statement_twice_is_two_memories(database: Path, sample_repo: Path) -> None:
    """Identity is the id, not the words.

    A content hash was rejected as an identity when the store was designed, and
    this is the case that rejected it: two people, or one person twice, saying
    the same thing are two admissions with their own authors and their own
    times, and collapsing them would lose one of those facts. Nothing refuses
    the second write and nothing merges them.
    """
    first = _write(database, sample_repo, "The same words.")
    second = _write(database, sample_repo, "The same words.")

    assert first.memory_id != second.memory_id

    connection = connect(database)
    try:
        found = read_memories(connection, sample_repo)
    finally:
        connection.close()

    assert len(found) == 2
    assert {memory.statement for memory in found} == {"The same words."}


def test_two_admissions_in_one_second_keep_their_order(
    database: Path, sample_repo: Path
) -> None:
    """What the microseconds are for, and what the duplicate above depends on."""
    moments = [
        datetime(2026, 10, 6, 21, 0, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 6, 21, 0, 0, 2, tzinfo=timezone.utc),
    ]
    for number, moment in enumerate(moments):
        _write(database, sample_repo, f"Statement {number}.", admitted_at=moment)

    connection = connect(database)
    try:
        found = read_memories(connection, sample_repo)
    finally:
        connection.close()

    assert [memory.statement for memory in found] == ["Statement 1.", "Statement 0."]


def test_running_analyze_twice_leaves_the_store_identical(
    database: Path, sample_repo: Path
) -> None:
    """The rescan rewrites the evidence; the knowledge is not part of it."""
    _write(database, sample_repo, "Kept through both runs.")
    before = _rows(database, "memories")

    analyze(sample_repo, database)
    analyze(sample_repo, database)

    assert _rows(database, "memories") == before


def test_an_act_that_ends_a_memory_twice_is_refused_the_second_time(
    database: Path, sample_repo: Path
) -> None:
    """Terminal means terminal, and the refusal is the store's own sentence."""
    written = _write(database, sample_repo)
    connection = connect(database)
    try:
        invalidate(
            connection,
            written.memory_id,
            repository_path=sample_repo,
            reason="it stopped applying",
        )
        with pytest.raises(MemoryStoreError, match="stays ended"):
            invalidate(
                connection,
                written.memory_id,
                repository_path=sample_repo,
                reason="again",
            )
    finally:
        connection.close()


def test_a_citation_of_a_commit_that_never_existed_is_kept_and_unresolved(
    database: Path, sample_repo: Path
) -> None:
    """A hand again: the act refuses this, so only SQL can store it.

    Nothing repairs it and nothing drops it — the row is what somebody wrote,
    and the read says it cannot be resolved. The same answer a rewrite gets.
    """
    written = _write(
        database, sample_repo, citations=[Citation("commit", "deadbeef")]
    )

    connection = connect(database)
    try:
        found = find_memory(connection, written.memory_id)
    finally:
        connection.close()

    assert found.citations == (Citation("commit", "deadbeef"),)
    assert MEMORY_SCHEMA_VERSION == "1"
