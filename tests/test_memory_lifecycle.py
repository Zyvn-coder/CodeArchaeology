"""The memory state machine: what may follow what, and what never changes.

Two acts end a memory — ``supersede``, which replaces it with a new statement,
and ``invalidate``, which ends it with a reason — and both leave the row where it
is. The rule the whole layer is built on is the one this file holds:

> **A memory can evolve; its history is never overwritten.**

The states are Unit 1's (`docs/v0.5-design.md` §8) and the rules are Unit 2's
(`docs/v0.5-storage-design.md` §5): ``active`` is the only state an act can be
performed on, the other two are terminal, and the link a supersede writes is one
edge — the reverse is derived, so two rows cannot disagree about it.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.memory import (
    ACTIVE,
    INVALIDATED,
    STATES,
    SUPERSEDED,
    Citation,
    MemoryStoreError,
    Subject,
    admit,
    find_memory,
    invalidate,
    read_memories,
    supersede,
)
from codearchaeology.storage import connect

runner = CliRunner()

EARLY = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)
MIDDLE = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
LATE = datetime(2026, 10, 6, 11, 0, tzinfo=timezone.utc)


@pytest.fixture
def repository(tmp_path: Path) -> str:
    return str(tmp_path / "checkout")


@pytest.fixture
def database(sample_repo: Path, tmp_path: Path) -> Path:
    path = tmp_path / "history.db"
    analyze(sample_repo, path)
    return path


@pytest.fixture
def connection(database: Path):
    connection = connect(database)
    try:
        yield connection
    finally:
        connection.close()


def _admit(connection, repository, statement="The first statement.", **fields):
    fields.setdefault("subject", Subject("path", path="core/app.py"))
    fields.setdefault("admitted_at", EARLY)
    return admit(connection, repository_path=repository, statement=statement, **fields)


def _replace(connection, repository, memory, statement="The second statement.", when=MIDDLE):
    return supersede(
        connection,
        memory.memory_id,
        repository_path=repository,
        statement=statement,
        subject=Subject("path", path="core/app.py"),
        admitted_at=when,
    )


def _end(connection, repository, memory, reason="it stopped applying"):
    return invalidate(
        connection, memory.memory_id, repository_path=repository, reason=reason
    )


def _links(connection, repository) -> dict[str, tuple[str | None, str | None]]:
    return {
        memory.memory_id: (memory.superseded_by, memory.supersedes)
        for memory in read_memories(connection, repository, include_ended=True)
    }


def _walk(connection, start: str) -> list[str]:
    """Follow the successor links from a memory, stopping if one repeats."""
    seen: list[str] = []
    current: str | None = start
    while current is not None:
        if current in seen:
            return seen + ["(a cycle)"]
        seen.append(current)
        current = find_memory(connection, current).superseded_by
    return seen


# --- the states --------------------------------------------------------------


def test_the_states_are_the_three_the_schema_has() -> None:
    assert STATES == (ACTIVE, SUPERSEDED, INVALIDATED)


def test_a_new_memory_is_active(connection, repository) -> None:
    assert _admit(connection, repository).state == ACTIVE


def test_supersede_closes_the_old_row_and_opens_a_new_one(
    connection, repository
) -> None:
    old = _admit(connection, repository)
    new = _replace(connection, repository, old)

    closed = find_memory(connection, old.memory_id)
    assert closed.state == SUPERSEDED
    assert closed.ended_at == MIDDLE
    assert closed.superseded_by == new.memory_id
    assert closed.supersedes is None

    assert new.state == ACTIVE
    assert new.ended_at is None
    assert new.superseded_by is None
    assert new.supersedes == old.memory_id


def test_invalidate_closes_the_row_with_its_reason(connection, repository) -> None:
    written = _admit(connection, repository)
    ended = _end(connection, repository, written, reason="the module was split again")

    assert ended.state == INVALIDATED
    assert ended.ended_at is not None
    assert ended.end_reason == "the module was split again"
    assert ended.superseded_by is None


def test_only_the_active_memory_is_the_current_one(connection, repository) -> None:
    old = _admit(connection, repository)
    new = _replace(connection, repository, old)

    assert [found.memory_id for found in read_memories(connection, repository)] == [
        new.memory_id
    ]
    assert len(read_memories(connection, repository, include_ended=True)) == 2


# --- what may follow what ----------------------------------------------------


def _ended(connection, repository, how: str):
    """A memory, and the same memory as it stands once an act has ended it."""
    written = _admit(connection, repository)
    if how == "supersede":
        _replace(connection, repository, written)
    else:
        _end(connection, repository, written)
    return written, find_memory(connection, written.memory_id)


@pytest.mark.parametrize("second", ["supersede", "invalidate"])
@pytest.mark.parametrize("first", ["supersede", "invalidate"])
def test_a_memory_that_has_ended_takes_no_further_act(
    connection, repository, first, second
) -> None:
    """Every pair of acts on one memory: the first ends it, the second is refused.

    A terminal state is terminal, and the refusal says what to do instead —
    record a new memory — rather than leaving a reader to guess whether a second
    act did something.
    """
    written, ended = _ended(connection, repository, first)

    with pytest.raises(MemoryStoreError, match="stays ended"):
        if second == "supersede":
            _replace(connection, repository, written, when=LATE)
        else:
            _end(connection, repository, written, reason="again")

    # And nothing moved: the row is what the first act left.
    assert find_memory(connection, written.memory_id) == ended


def test_the_refusal_names_the_state_it_is_in(connection, repository) -> None:
    written = _admit(connection, repository)
    _end(connection, repository, written)

    with pytest.raises(MemoryStoreError) as raised:
        _replace(connection, repository, written)

    assert "is invalidated" in str(raised.value)
    assert "record a new one instead" in str(raised.value)


def test_a_memory_that_comes_back_is_a_new_memory(connection, repository) -> None:
    """States are terminal, so a rule that returns is written again.

    The record then reads "in force, then not, then in force again" — which is
    what happened — instead of un-ending a row and losing the middle.
    """
    written = _admit(connection, repository, statement="We use SQLite.")
    _end(connection, repository, written, reason="we moved to PostgreSQL")

    again = _admit(
        connection,
        repository,
        statement="We use SQLite.",
        admitted_at=LATE,
    )

    assert again.memory_id != written.memory_id
    assert find_memory(connection, written.memory_id).state == INVALIDATED
    assert [found.memory_id for found in read_memories(connection, repository)] == [
        again.memory_id
    ]


# --- the history is never overwritten ----------------------------------------


def test_the_old_row_is_never_edited(connection, repository) -> None:
    """The goal of the phase, as one assertion.

    Everything a person wrote is byte-identical afterwards: the statement, the
    subject, the citations, the author, the times, the ``since``. The act writes
    a state, an end time and a link, and nothing else.
    """
    old = _admit(
        connection,
        repository,
        statement="Auth lives in app.py.",
        author_name="Ada Lovelace",
        author_email="ada@example.com",
        since_commit_sha="1edec27",
        citations=[Citation("file", "core/app.py"), Citation("absence", "parse_failed")],
    )
    new = _replace(connection, repository, old, statement="Auth lives in auth.py.")

    found = find_memory(connection, old.memory_id)

    assert (
        found.statement,
        found.subject,
        found.citations,
        found.author_name,
        found.author_email,
        found.admitted_at,
        found.since_commit_sha,
        found.since_date,
    ) == (
        old.statement,
        old.subject,
        old.citations,
        old.author_name,
        old.author_email,
        old.admitted_at,
        old.since_commit_sha,
        old.since_date,
    )
    assert (found.state, found.ended_at, found.superseded_by) == (
        SUPERSEDED,
        MIDDLE,
        new.memory_id,
    )


def test_the_two_rows_share_the_moment_of_the_act(connection, repository) -> None:
    """The successor's admission and the old one's end are the same instant."""
    old = _admit(connection, repository)
    new = _replace(connection, repository, old)

    assert find_memory(connection, old.memory_id).ended_at == new.admitted_at == MIDDLE


def test_a_supersede_writes_both_rows_or_neither(
    connection, repository, monkeypatch
) -> None:
    """One transaction, so a failure cannot leave a successor nobody points at."""
    from codearchaeology import memory as module

    old = _admit(connection, repository)
    original = module._write

    def half(connection, **fields):
        original(connection, **fields)
        raise MemoryStoreError("interrupted")

    monkeypatch.setattr(module, "_write", half)
    with pytest.raises(MemoryStoreError):
        _replace(connection, repository, old)

    assert find_memory(connection, old.memory_id) == old
    assert len(read_memories(connection, repository, include_ended=True)) == 1


# --- chains, and the cycle that cannot be built ------------------------------


def test_a_chain_records_every_step(connection, repository) -> None:
    first = _admit(connection, repository, statement="First.")
    second = _replace(connection, repository, first, statement="Second.", when=MIDDLE)
    third = _replace(connection, repository, second, statement="Third.", when=LATE)

    assert _links(connection, repository) == {
        first.memory_id: (second.memory_id, None),
        second.memory_id: (third.memory_id, first.memory_id),
        third.memory_id: (None, second.memory_id),
    }
    assert [found.memory_id for found in read_memories(connection, repository)] == [
        third.memory_id
    ]
    assert _walk(connection, first.memory_id) == [
        first.memory_id,
        second.memory_id,
        third.memory_id,
    ]


def test_a_chain_cannot_be_closed_into_a_cycle(connection, repository) -> None:
    """Every act that could close one is refused, so no cycle is reachable.

    The successor is always a memory the act has just admitted, and only an
    active memory can be acted on — so a link can only ever point forward. What
    a *hand* writes into the table is another matter, and the schema does not
    pretend to see it: a CHECK cannot look at another row, which is why the rule
    lives in the act and is stated here rather than implied.
    """
    first = _admit(connection, repository, statement="First.")
    second = _replace(connection, repository, first, statement="Second.")
    third = _replace(connection, repository, second, statement="Third.")

    for ended in (first, second):
        with pytest.raises(MemoryStoreError, match="stays ended"):
            _replace(connection, repository, ended)

    # The walk ends at the active memory and never returns to where it started.
    walked = _walk(connection, first.memory_id)
    assert walked[-1] == third.memory_id
    assert len(set(walked)) == len(walked)


def test_an_ended_memory_can_be_read_from_either_end(connection, repository) -> None:
    first = _admit(connection, repository, statement="First.")
    second = _replace(connection, repository, first, statement="Second.")

    assert find_memory(connection, first.memory_id).superseded_by == second.memory_id
    assert find_memory(connection, second.memory_id).supersedes == first.memory_id


# --- the commands ------------------------------------------------------------


def _run(repository: Path, database: Path, *arguments: str):
    return runner.invoke(
        app, ["memory", *arguments, str(repository), "--db", str(database)]
    )


def _create(repository: Path, database: Path, *arguments: str):
    result = _run(repository, database, "create", *arguments)
    assert result.exit_code == 0, result.stderr
    return result


def _stored(database: Path, repository: Path):
    connection = connect(database)
    try:
        return read_memories(connection, repository, include_ended=True)
    finally:
        connection.close()


def test_supersede_replaces_a_memory_and_names_both(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "The first statement.", "--about-repository")
    old = _stored(database, sample_repo)[0]

    result = _run(
        sample_repo,
        database,
        "supersede",
        old.memory_id[:8],
        "The second statement.",
        "--about-repository",
    )

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Memory      ")
    assert lines[1] == f"Supersedes  {old.memory_id}"
    assert lines[2] == "Subject     the project"
    assert [found.state for found in _stored(database, sample_repo)] == [
        ACTIVE,
        SUPERSEDED,
    ]


def test_supersede_refuses_a_memory_that_has_ended(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "The first statement.", "--about-repository")
    old = _stored(database, sample_repo)[0]
    _run(sample_repo, database, "invalidate", old.memory_id, "--reason", "wrong")

    result = _run(
        sample_repo,
        database,
        "supersede",
        old.memory_id,
        "The second statement.",
        "--about-repository",
    )

    assert result.exit_code == 1
    assert "stays ended" in result.stderr
    assert result.stdout == ""


def test_a_supersede_whose_evidence_does_not_resolve_writes_nothing(
    sample_repo: Path, database: Path
) -> None:
    """The act is checked before it writes, so a refusal leaves no successor."""
    _create(sample_repo, database, "The first statement.", "--about-repository")
    old = _stored(database, sample_repo)[0]

    result = _run(
        sample_repo,
        database,
        "supersede",
        old.memory_id,
        "The second statement.",
        "--about-repository",
        "--cite-commit",
        "abcdef12",
    )

    assert result.exit_code == 1
    assert "is not in the stored history" in result.stderr
    assert _stored(database, sample_repo) == (old,)


def test_supersede_needs_a_subject(sample_repo: Path, database: Path) -> None:
    _create(sample_repo, database, "The first statement.", "--about-repository")
    old = _stored(database, sample_repo)[0]

    result = _run(
        sample_repo, database, "supersede", old.memory_id, "The second statement."
    )

    assert result.exit_code == 1
    assert "a memory is about one thing" in result.stderr


def test_invalidate_ends_a_memory_and_prints_the_reason(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "A statement.", "--about-repository")
    written = _stored(database, sample_repo)[0]

    result = _run(
        sample_repo,
        database,
        "invalidate",
        written.memory_id[:8],
        "--reason",
        "the module was split again",
    )

    assert result.exit_code == 0, result.stderr
    assert f"Memory      {written.memory_id}" in result.stdout
    assert "State       invalidated" in result.stdout
    assert "Reason      the module was split again" in result.stdout
    assert _stored(database, sample_repo)[0].state == INVALIDATED


def test_invalidate_refuses_an_empty_reason(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "A statement.", "--about-repository")
    written = _stored(database, sample_repo)[0]

    result = _run(
        sample_repo, database, "invalidate", written.memory_id, "--reason", "   "
    )

    assert result.exit_code == 1
    assert "with a reason" in result.stderr
    assert _stored(database, sample_repo)[0].state == ACTIVE


def test_invalidate_without_a_reason_is_a_usage_error(
    sample_repo: Path, database: Path
) -> None:
    """A missing required option is Click's refusal, as it is everywhere else."""
    _create(sample_repo, database, "A statement.", "--about-repository")
    written = _stored(database, sample_repo)[0]

    result = _run(sample_repo, database, "invalidate", written.memory_id)

    assert result.exit_code == 2
    assert "--reason" in result.stderr


def test_the_replaced_statement_is_still_there_afterwards(
    sample_repo: Path, database: Path
) -> None:
    """End to end: the command that replaced it did not overwrite it."""
    _create(sample_repo, database, "The first statement.", "--about-repository")
    old = _stored(database, sample_repo)[0]
    _run(
        sample_repo,
        database,
        "supersede",
        old.memory_id,
        "The second statement.",
        "--about-repository",
    )

    shown = _run(sample_repo, database, "show", old.memory_id)

    assert "The first statement." in shown.stdout
    assert "State       superseded" in shown.stdout
    successor = [found for found in _stored(database, sample_repo) if found.state == ACTIVE][0]
    assert f"superseded by {successor.memory_id[:8]}" in shown.stdout
    assert "admitted" in shown.stdout


def test_the_successor_names_what_it_replaced(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "The first statement.", "--about-repository")
    old = _stored(database, sample_repo)[0]
    _run(
        sample_repo,
        database,
        "supersede",
        old.memory_id,
        "The second statement.",
        "--about-repository",
    )
    new = [found for found in _stored(database, sample_repo) if found.state == ACTIVE][0]

    shown = _run(sample_repo, database, "show", new.memory_id)

    assert f"Lifecycle   supersedes {old.memory_id[:8]}" in shown.stdout


def test_an_ended_memory_is_hidden_until_it_is_asked_for(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "A statement.", "--about-repository")
    written = _stored(database, sample_repo)[0]
    _run(sample_repo, database, "invalidate", written.memory_id, "--reason", "wrong")

    hidden = _run(sample_repo, database, "list")
    shown = _run(sample_repo, database, "list", "--include-ended")

    assert "Memories (0)" in hidden.stdout
    assert "A statement." in shown.stdout
    assert "invalidated" in shown.stdout
    assert "wrong" in shown.stdout


def test_a_chain_reads_the_same_way_through_the_commands(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "First.", "--about-repository")
    first = _stored(database, sample_repo)[0]
    _run(sample_repo, database, "supersede", first.memory_id, "Second.", "--about-repository")
    second = [found for found in _stored(database, sample_repo) if found.state == ACTIVE][0]
    _run(sample_repo, database, "supersede", second.memory_id, "Third.", "--about-repository")

    document = json.loads(_run(sample_repo, database, "list", "--json", "--include-ended").stdout)
    links = {
        entry["memory_id"]: (entry["supersedes"], entry["superseded_by"])
        for entry in document["memories"]
    }

    third = [found for found in _stored(database, sample_repo) if found.state == ACTIVE][0]
    assert links[first.memory_id] == (None, second.memory_id)
    assert links[second.memory_id] == (first.memory_id, third.memory_id)
    assert links[third.memory_id] == (second.memory_id, None)
