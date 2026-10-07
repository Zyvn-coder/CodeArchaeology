"""Tests for the memory store: the one part of the database that is not a cache.

The rules here are Unit 2's (`docs/v0.5-storage-design.md`) and the definition is
Unit 1's (`docs/v0.5-design.md`). What this file holds is the storage layer's
half of them: the shape, the acts, the repository identity, the version stamp,
and the property the whole phase exists for — **a memory survives everything the
evidence does not**.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from codearchaeology import memory
from codearchaeology.analysis import AnalysisError, analyze, open_analysis
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.explanation import EVIDENCE_KINDS
from codearchaeology.memory import (
    ACTIVE,
    CITATION_KINDS,
    INVALIDATED,
    MEMORY_SCHEMA_VERSION,
    MEMORY_TABLES,
    STATEMENT_CHARACTERS,
    SUBJECT_KINDS,
    SUPERSEDED,
    Citation,
    MemoryNotFound,
    MemoryStoreError,
    Subject,
    adopt,
    admit,
    find_memory,
    foreign_memories,
    invalidate,
    prepare_memory,
    read_memories,
    supersede,
)
from codearchaeology.storage import (
    SCHEMA_VERSION,
    TABLES,
    clear_history,
    connect,
    get_meta,
    prepare_database,
    set_meta,
    write_commits,
)

EARLY = datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)

# The column lists, pinned the way the AST tables' are: a column added later
# fails the build instead of quietly becoming part of the model.
MEMORY_COLUMNS = (
    "memory_id",
    "repository_path",
    "statement",
    "author_name",
    "author_email",
    "admitted_at",
    "admitted_epoch",
    "subject_kind",
    "subject_path",
    "subject_qualname",
    "subject_commit_sha",
    "since_commit_sha",
    "since_date",
    "state",
    "ended_at",
    "ended_epoch",
    "end_reason",
    "superseded_by",
)
CITATION_COLUMNS = ("memory_id", "position", "kind", "ref")


@pytest.fixture
def repository(tmp_path: Path) -> str:
    """A checkout this store's memories are about; nothing reads the directory."""
    return str(tmp_path / "checkout")


@pytest.fixture
def elsewhere(tmp_path: Path) -> str:
    """A second checkout, for the isolation rules."""
    return str(tmp_path / "elsewhere")


@pytest.fixture
def database(tmp_path: Path):
    """An analyzed database, with the memory tables ready to be written to."""
    connection = connect(tmp_path / "memory.db")
    prepare_database(connection)
    prepare_memory(connection)
    try:
        yield connection
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]


def _admit(connection, repository, statement="We keep auth in one module.", **fields):
    """Admit a memory with the fields most tests do not care about filled in."""
    fields.setdefault("subject", Subject("path", path="auth.py"))
    fields.setdefault("admitted_at", EARLY)
    return admit(connection, repository_path=repository, statement=statement, **fields)


def _row(
    connection,
    memory_id,
    repository,
    *,
    statement="Something a person said.",
    subject_kind="repository",
    subject_path=None,
    subject_qualname=None,
    subject_commit_sha=None,
    state="active",
    admitted_at=EARLY,
    ended_at=None,
    end_reason=None,
    superseded_by=None,
) -> str:
    """Write a row by hand, for the constraints the acts never reach."""
    connection.execute(
        "INSERT INTO memories (memory_id, repository_path, statement, author_name,"
        " author_email, admitted_at, admitted_epoch, subject_kind, subject_path,"
        " subject_qualname, subject_commit_sha, since_commit_sha, since_date, state,"
        " ended_at, ended_epoch, end_reason, superseded_by)"
        " VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)",
        (
            memory_id,
            repository,
            statement,
            admitted_at.isoformat(),
            int(admitted_at.timestamp()),
            subject_kind,
            subject_path,
            subject_qualname,
            subject_commit_sha,
            state,
            None if ended_at is None else ended_at.isoformat(),
            None if ended_at is None else int(ended_at.timestamp()),
            end_reason,
            superseded_by,
        ),
    )
    connection.commit()
    return memory_id


# The shape.


def test_a_fresh_database_has_the_two_tables(database: sqlite3.Connection) -> None:
    assert _columns(database, "memories") == list(MEMORY_COLUMNS)
    assert _columns(database, "memory_citations") == list(CITATION_COLUMNS)


def test_the_memory_tables_are_not_evidence_tables(database: sqlite3.Connection) -> None:
    """The cache exception, as one assertion.

    ``TABLES`` is what a schema rebuild drops, so a memory survives a rebuild
    because it was never on the list — and this is the test that fails the day
    somebody adds it there "for tidiness".
    """
    assert set(MEMORY_TABLES).isdisjoint(TABLES)


def test_the_memory_stamp_did_not_move_the_evidence_stamp(
    database: sqlite3.Connection,
) -> None:
    assert get_meta(database, memory.MEMORY_VERSION_KEY) == MEMORY_SCHEMA_VERSION
    assert MEMORY_SCHEMA_VERSION == "1"
    assert get_meta(database, "schema_version") == SCHEMA_VERSION == "4"


def test_the_subject_kinds_are_the_four_the_schema_has(
    database: sqlite3.Connection, repository: str
) -> None:
    assert SUBJECT_KINDS == ("repository", "path", "definition", "commit")

    with pytest.raises(sqlite3.IntegrityError):
        _row(database, "a" * 36, repository, subject_kind="file")


def test_the_citation_kinds_are_the_evidence_kinds(database: sqlite3.Connection) -> None:
    """One vocabulary for the whole tool, held by a test rather than by a copy."""
    assert CITATION_KINDS == EVIDENCE_KINDS


def test_the_schema_refuses_a_definition_subject_without_a_path(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _row(database, "b" * 36, repository, subject_kind="definition",
             subject_qualname="load")


def test_the_schema_refuses_a_path_subject_with_a_qualname(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _row(database, "c" * 36, repository, subject_kind="path",
             subject_path="auth.py", subject_qualname="load")


def test_the_schema_refuses_an_invalidated_row_without_a_reason(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _row(database, "d" * 36, repository, state=INVALIDATED, ended_at=LATER)


def test_the_schema_refuses_a_superseded_row_without_a_successor(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _row(database, "e" * 36, repository, state=SUPERSEDED, ended_at=LATER)


def test_the_schema_refuses_an_active_row_with_an_end(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _row(database, "f" * 36, repository, state=ACTIVE, ended_at=LATER)


def test_a_memory_cannot_supersede_itself(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _row(database, "0" * 36, repository, state=SUPERSEDED, ended_at=LATER,
             superseded_by="0" * 36)


def test_the_schema_refuses_an_empty_statement(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _row(database, "1" * 36, repository, statement="   ")


def test_a_citation_kind_the_schema_does_not_have_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository)
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO memory_citations (memory_id, position, kind, ref)"
            " VALUES (?, 0, 'guess', 'x')",
            (written.memory_id,),
        )


def test_the_same_citation_twice_is_refused_by_the_schema(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository, citations=[Citation("commit", "1edec27")])
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO memory_citations (memory_id, position, kind, ref)"
            " VALUES (?, 1, 'commit', '1edec27')",
            (written.memory_id,),
        )


# Writing and reading.


def test_a_memory_round_trips(database: sqlite3.Connection, repository: str) -> None:
    written = _admit(
        database,
        repository,
        statement="We keep authentication in one module.",
        author_name="Zyvn Coder",
        author_email="zyvn@example.com",
        since_commit_sha="1edec27",
        citations=[Citation("commit", "1edec27"), Citation("file", "auth.py")],
    )

    found = find_memory(database, written.memory_id)

    assert found == written
    assert found.statement == "We keep authentication in one module."
    assert found.author_name == "Zyvn Coder"
    assert found.author_email == "zyvn@example.com"
    assert found.subject == Subject("path", path="auth.py")
    assert found.since_commit_sha == "1edec27"
    assert found.since_date is None
    assert found.state == ACTIVE
    assert found.ended_at is None
    assert found.superseded_by is None
    assert found.supersedes is None
    assert found.admitted_at == EARLY
    assert [citation.kind for citation in found.citations] == ["commit", "file"]


def test_the_statement_is_stripped(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository, statement="  spaced out  ")
    assert written.statement == "spaced out"


def test_a_memory_with_no_citations_has_none(
    database: sqlite3.Connection, repository: str
) -> None:
    """Absence is the statement "no evidence was attached", not an error."""
    assert _admit(database, repository).citations == ()


def test_the_citations_are_stored_in_their_canonical_order(
    database: sqlite3.Connection, repository: str
) -> None:
    """The order flags appear in is not kept by a command line, so it is fixed."""
    written = _admit(
        database,
        repository,
        citations=[
            Citation("absence", "parse_failed"),
            Citation("file", "auth.py"),
            Citation("commit", "1edec27"),
        ],
    )
    assert [citation.kind for citation in written.citations] == [
        "commit",
        "file",
        "absence",
    ]


def test_the_same_citation_twice_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(MemoryStoreError, match="given twice"):
        _admit(database, repository, citations=[Citation("file", "auth.py")] * 2)


def test_a_citation_kind_the_module_does_not_have_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(MemoryStoreError, match="not a citation kind"):
        _admit(database, repository, citations=[Citation("guess", "x")])


def test_an_empty_statement_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(MemoryStoreError, match="this one is empty"):
        _admit(database, repository, statement="   ")


def test_a_statement_at_the_cap_is_kept(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository, statement="x" * STATEMENT_CHARACTERS)
    assert len(written.statement) == STATEMENT_CHARACTERS


def test_a_statement_over_the_cap_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(MemoryStoreError, match=f"at most {STATEMENT_CHARACTERS}"):
        _admit(database, repository, statement="x" * (STATEMENT_CHARACTERS + 1))


def test_an_unknown_author_is_recorded_as_unknown(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository)
    assert written.author_name is None
    assert written.author_email is None


def test_every_subject_kind_round_trips(
    database: sqlite3.Connection, repository: str
) -> None:
    subjects = (
        Subject("repository"),
        Subject("path", path="core/app.py"),
        Subject("definition", path="core/app.py", qualname="load"),
        Subject("commit", commit_sha="1edec27"),
    )
    for subject in subjects:
        written = _admit(database, repository, subject=subject)
        assert find_memory(database, written.memory_id).subject == subject


def test_a_definition_subject_needs_both_parts(
    database: sqlite3.Connection, repository: str
) -> None:
    with pytest.raises(MemoryStoreError, match="does not name a definition"):
        _admit(database, repository, subject=Subject("definition", qualname="load"))


def test_a_since_commit_and_a_since_date_together_are_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository, since_date="2024-03-06")

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "UPDATE memories SET since_commit_sha = '1edec27' WHERE memory_id = ?",
            (written.memory_id,),
        )


def test_a_since_date_that_is_not_a_date_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    """The shape, the spelling and the day itself: three ways to not be a date."""
    for spelling in ("2024-3-6", "20240306", "2024-13-45"):
        with pytest.raises(MemoryStoreError, match="YYYY-MM-DD"):
            _admit(database, repository, since_date=spelling)


def test_two_starts_are_refused_with_a_sentence_not_a_constraint(
    database: sqlite3.Connection, repository: str
) -> None:
    """The CHECK is there too; the sentence is what a caller reads instead."""
    with pytest.raises(MemoryStoreError, match="one thing: a commit or a date"):
        _admit(database, repository, since_commit_sha="1edec27", since_date="2024-03-06")


def test_memories_come_back_newest_first(
    database: sqlite3.Connection, repository: str
) -> None:
    first = _admit(database, repository, statement="The older one.", admitted_at=EARLY)
    second = _admit(database, repository, statement="The newer one.", admitted_at=LATER)

    assert [found.memory_id for found in read_memories(database, repository)] == [
        second.memory_id,
        first.memory_id,
    ]


def test_two_memories_admitted_in_the_same_second_are_ordered_by_id(
    database: sqlite3.Connection, repository: str
) -> None:
    """Deterministic rather than accidental: the tie has a stated rule."""
    written = [
        _admit(database, repository, statement=f"number {index}", admitted_at=EARLY)
        for index in range(3)
    ]
    order = [found.memory_id for found in read_memories(database, repository)]
    assert order == sorted(found.memory_id for found in written)


def test_ended_memories_are_hidden_unless_they_are_asked_for(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository)
    invalidate(database, written.memory_id, repository_path=repository, reason="wrong")

    assert read_memories(database, repository) == ()
    assert len(read_memories(database, repository, include_ended=True)) == 1


def test_a_memory_survives_a_reopened_database(tmp_path: Path, repository: str) -> None:
    database = tmp_path / "reopen.db"
    connection = connect(database)
    prepare_database(connection)
    prepare_memory(connection)
    written = _admit(connection, repository)
    connection.close()

    reopened = connect(database)
    try:
        assert find_memory(reopened, written.memory_id) == written
    finally:
        reopened.close()


def test_an_empty_store_reads_as_no_memories(tmp_path: Path, repository: str) -> None:
    """A repository nobody wrote a memory about has no tables, not an error."""
    connection = connect(tmp_path / "bare.db")
    try:
        assert read_memories(connection, repository) == ()
        assert foreign_memories(connection, repository) == (0, ())
    finally:
        connection.close()


# Addressing a memory.


def test_find_accepts_a_prefix(database: sqlite3.Connection, repository: str) -> None:
    written = _admit(database, repository)
    assert find_memory(database, written.memory_id[:8]) == written
    assert find_memory(database, written.memory_id.upper()[:8]) == written


def test_an_unknown_id_is_refused(database: sqlite3.Connection, repository: str) -> None:
    _admit(database, repository)
    with pytest.raises(MemoryNotFound, match="no memory starts with"):
        find_memory(database, "abcdef12")


def test_an_ambiguous_prefix_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    _row(database, "3f2a9c1e-1111-4111-8111-111111111111", repository)
    _row(database, "3f2a9c1e-2222-4222-8222-222222222222", repository)

    with pytest.raises(MemoryNotFound, match="matches 2 memories"):
        find_memory(database, "3f2a9c1e")


def test_a_prefix_that_is_not_an_id_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    """Including a wildcard: the prefix is checked, never handed to LIKE as is."""
    _admit(database, repository)
    for prefix in ("%", "_", "not-an-id", ""):
        with pytest.raises(MemoryNotFound):
            find_memory(database, prefix)


# The lifecycle.


def test_supersede_closes_the_old_memory_and_links_both_ways(
    database: sqlite3.Connection, repository: str
) -> None:
    old = _admit(database, repository, statement="Auth lives in app.py.", admitted_at=EARLY)
    new = supersede(
        database,
        old.memory_id,
        repository_path=repository,
        statement="Auth lives in auth.py, after the split caused an incident.",
        subject=Subject("path", path="auth.py"),
        admitted_at=LATER,
    )

    closed = find_memory(database, old.memory_id)
    assert closed.state == SUPERSEDED
    assert closed.ended_at == LATER
    assert closed.superseded_by == new.memory_id
    assert new.supersedes == closed.memory_id
    assert new.state == ACTIVE
    assert [found.memory_id for found in read_memories(database, repository)] == [
        new.memory_id
    ]


def test_supersede_refuses_a_memory_that_has_ended(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository)
    invalidate(database, written.memory_id, repository_path=repository, reason="wrong")

    with pytest.raises(MemoryStoreError, match="stays ended"):
        supersede(
            database,
            written.memory_id,
            repository_path=repository,
            statement="Another go.",
            subject=Subject("repository"),
        )


def test_invalidate_ends_the_memory_with_its_reason(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository)
    ended = invalidate(
        database,
        written.memory_id,
        repository_path=repository,
        reason="the module was split again",
        ended_at=LATER,
    )

    assert ended.state == INVALIDATED
    assert ended.ended_at == LATER
    assert ended.end_reason == "the module was split again"
    assert ended.superseded_by is None


def test_invalidate_requires_a_reason(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository)
    with pytest.raises(MemoryStoreError, match="with a reason"):
        invalidate(database, written.memory_id, repository_path=repository, reason="  ")
    assert find_memory(database, written.memory_id).state == ACTIVE


def test_a_second_invalidate_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    written = _admit(database, repository)
    invalidate(database, written.memory_id, repository_path=repository, reason="wrong")
    with pytest.raises(MemoryStoreError, match="stays ended"):
        invalidate(database, written.memory_id, repository_path=repository, reason="again")


def test_an_act_writes_nothing_when_it_is_interrupted(
    database: sqlite3.Connection, repository: str, monkeypatch
) -> None:
    """One transaction per act: there is no half-written memory."""
    original = memory._write

    def half_written(connection, **fields):
        original(connection, **fields)
        raise sqlite3.OperationalError("interrupted")

    monkeypatch.setattr(memory, "_write", half_written)
    with pytest.raises(sqlite3.OperationalError):
        _admit(database, repository)

    assert read_memories(database, repository) == ()


# One database, one repository.


def test_a_write_from_another_repository_is_refused(
    database: sqlite3.Connection, repository: str, elsewhere: str
) -> None:
    _admit(database, repository)

    with pytest.raises(MemoryStoreError) as raised:
        _admit(database, elsewhere)

    assert repository in str(raised.value)
    assert "one repository's memories" in str(raised.value)


def test_reads_show_one_repository_and_count_the_rest(
    database: sqlite3.Connection, repository: str, elsewhere: str
) -> None:
    ours = _admit(database, repository)
    theirs = _row(database, "2" * 36, elsewhere)

    assert [found.memory_id for found in read_memories(database, repository)] == [
        ours.memory_id
    ]
    assert [found.memory_id for found in read_memories(database, elsewhere)] == [theirs]
    assert foreign_memories(database, repository) == (1, (elsewhere,))
    assert foreign_memories(database, elsewhere) == (1, (repository,))


def test_adopt_moves_one_path_and_records_the_act(
    database: sqlite3.Connection, repository: str, elsewhere: str
) -> None:
    _admit(database, repository, statement="First.")
    _admit(database, repository, statement="Second.", admitted_at=LATER)

    moved = adopt(database, from_path=repository, to_path=elsewhere)

    assert moved == 2
    assert read_memories(database, repository, include_ended=True) == ()
    assert len(read_memories(database, elsewhere, include_ended=True)) == 2
    assert foreign_memories(database, elsewhere) == (0, ())
    assert get_meta(database, memory.ADOPTED_FROM_KEY) == repository
    assert get_meta(database, memory.ADOPTED_AT_KEY) is not None


def test_adopt_refuses_a_path_nothing_was_made_about(
    database: sqlite3.Connection, repository: str, elsewhere: str
) -> None:
    _admit(database, repository)
    with pytest.raises(MemoryStoreError, match="nothing in this database"):
        adopt(database, from_path=elsewhere, to_path=repository)


def test_adopt_refuses_the_path_it_is_already(
    database: sqlite3.Connection, repository: str
) -> None:
    _admit(database, repository)
    with pytest.raises(MemoryStoreError, match="nothing to adopt"):
        adopt(database, from_path=repository, to_path=repository)


def test_adopt_refuses_to_merge_two_sets(
    database: sqlite3.Connection, repository: str, elsewhere: str
) -> None:
    """Merging is a question this version answers by refusing, not by guessing."""
    _admit(database, repository)
    _row(database, "4" * 36, elsewhere)

    with pytest.raises(MemoryStoreError, match="does not merge"):
        adopt(database, from_path=repository, to_path=elsewhere)


def test_every_writing_act_obeys_the_one_repository_rule(
    database: sqlite3.Connection, repository: str, elsewhere: str
) -> None:
    """A database holding two repositories' memories takes no more writes.

    The rule is about the database and not about the target, so all three acts
    answer the same way — including the one whose target is not in question.
    """
    written = _admit(database, repository)
    _row(database, "5" * 36, elsewhere)

    with pytest.raises(MemoryStoreError, match="one repository's memories"):
        _admit(database, repository)
    with pytest.raises(MemoryStoreError, match="one repository's memories"):
        supersede(
            database,
            written.memory_id,
            repository_path=repository,
            statement="Not now.",
            subject=Subject("repository"),
        )
    with pytest.raises(MemoryStoreError, match="one repository's memories"):
        invalidate(database, written.memory_id, repository_path=repository, reason="x")


# The cache exception: what a rebuild must not touch.


def test_an_evidence_rebuild_keeps_the_memories_and_their_stamp(
    database: sqlite3.Connection, repository: str
) -> None:
    """The unit's central test: the cache is rebuilt, the knowledge is not."""
    written = _admit(database, repository, citations=[Citation("commit", "1edec27")])
    set_meta(database, "schema_version", "2")

    prepare_database(database)

    assert get_meta(database, "schema_version") == SCHEMA_VERSION
    assert get_meta(database, memory.MEMORY_VERSION_KEY) == MEMORY_SCHEMA_VERSION
    assert find_memory(database, written.memory_id) == written


def test_clear_history_keeps_the_memories(
    database: sqlite3.Connection, repository: str
) -> None:
    """Including the citations: clearing the evidence may not take them with it."""
    written = _admit(
        database,
        repository,
        citations=[Citation("commit", "1edec27"), Citation("file", "auth.py")],
    )
    clear_history(database)
    assert find_memory(database, written.memory_id) == written


def test_no_foreign_key_reaches_the_evidence(
    database: sqlite3.Connection, repository: str
) -> None:
    """A rewrite may delete a commit; it may not delete a memory about it.

    A cascade would take the memory with the commit, and a plain foreign key
    would make ``write_commits`` fail on a repository whose history moved.
    Neither happens, and the citation stays as the author wrote it.
    """
    written = _admit(database, repository, citations=[Citation("commit", "1edec27")])

    write_commits(database, [])

    assert find_memory(database, written.memory_id).citations == (
        Citation("commit", "1edec27"),
    )


def test_a_memory_stamp_from_a_newer_tool_is_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    _admit(database, repository)
    set_meta(database, memory.MEMORY_VERSION_KEY, "9")

    with pytest.raises(MemoryStoreError, match="newer tool"):
        read_memories(database, repository)
    with pytest.raises(MemoryStoreError, match="newer tool"):
        _admit(database, repository)

    # The evidence is a different matter, and stays readable.
    assert get_meta(database, "schema_version") == SCHEMA_VERSION
    assert database.execute("SELECT COUNT(*) AS n FROM commits").fetchone()["n"] == 0


def test_tables_without_a_stamp_are_refused(
    database: sqlite3.Connection, repository: str
) -> None:
    """A shape this tool did not write is refused, not guessed at."""
    _admit(database, repository)
    database.execute("DELETE FROM meta WHERE key = ?", (memory.MEMORY_VERSION_KEY,))
    database.commit()

    with pytest.raises(MemoryStoreError, match="no memory schema version"):
        read_memories(database, repository)


def test_a_write_needs_an_analysis(tmp_path: Path, repository: str) -> None:
    connection = connect(tmp_path / "nothing.db")
    try:
        with pytest.raises(MemoryStoreError, match="holds no analysis"):
            _admit(connection, repository)
    finally:
        connection.close()


# Against the real commands.


def test_analyze_and_ast_keep_the_memories(sample_repo: Path, tmp_path: Path) -> None:
    database = tmp_path / "analysis.db"
    analyze(sample_repo, database)

    connection = connect(database)
    try:
        prepare_memory(connection)
        written = _admit(
            connection,
            sample_repo,
            statement="Why the rename happened.",
            citations=[Citation("file", "core/app.py")],
        )
    finally:
        connection.close()

    run_ast_pass(sample_repo, database)
    analyze(sample_repo, database)

    connection = connect(database)
    try:
        assert find_memory(connection, written.memory_id) == written
    finally:
        connection.close()


def test_a_corrupt_database_is_a_sentence_not_a_traceback(
    sample_repo: Path, tmp_path: Path
) -> None:
    """The advice changed: deleting a database can now destroy knowledge."""
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this file is not a database, it is just some bytes.\n" * 8)

    with pytest.raises(AnalysisError) as read:
        with open_analysis(sample_repo, broken):
            pass

    assert "could not be read" in str(read.value)
    assert "Copy it somewhere safe" in str(read.value)

    with pytest.raises(AnalysisError, match="could not be read"):
        analyze(sample_repo, broken)
