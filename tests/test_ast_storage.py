"""Tests for the two tables the AST pass writes.

Read back with plain SQL rather than through a reader of ours: a writer and a
reader that are wrong in the same way would agree with each other, and the
question here is what actually reached the database.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.definitions import ASYNC_FUNCTION, CLASS, FUNCTION
from codearchaeology.history import Commit
from codearchaeology.storage import (
    CREATED,
    MODIFIED,
    SCHEMA_VERSION,
    UNCHANGED,
    DefinitionVersion,
    FileVersion,
    clear_history,
    connect,
    get_meta,
    prepare_database,
    write_commits,
    write_definition_versions,
    write_file_versions,
)
from sample_repo import add_commit, build_single_commit_repo, git_output

SHA = "a" * 40

# The columns v0.3 agreed to store, in the order they are declared.
FILE_VERSION_COLUMNS = (
    "commit_sha",
    "path",
    "content_sha",
    "parsed_at_version",
    "parse_error",
    "error_lineno",
    "error_offset",
)
DEFINITION_VERSION_COLUMNS = (
    "commit_sha",
    "path",
    "position",
    "kind",
    "qualname",
    "change_type",
    "fingerprint",
    "lineno",
    "end_lineno",
    "decorators",
)

# What the tables are not allowed to grow: the answers a later unit derives.
DERIVED_IDENTITIES = (
    "function_id",
    "lifetime_id",
    "same_as_previous",
    "renamed_from",
    "moved_from",
)


def _commit(sha: str = SHA, message: str = "a commit") -> Commit:
    return Commit(
        sha=sha,
        parents=(),
        author_name="Ada Lovelace",
        author_email="ada@example.com",
        authored_at=datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc),
        committed_at=datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc),
        message=message,
        changes=(),
    )


def _version(**overrides) -> FileVersion:
    fields = {
        "commit_sha": SHA,
        "path": "src/app.py",
        "content_sha": "b" * 40,
        "parsed_at_version": "3.13.5",
    }
    fields.update(overrides)
    return FileVersion(**fields)


def _definition(position: int = 0, **overrides) -> DefinitionVersion:
    fields = {
        "commit_sha": SHA,
        "path": "src/app.py",
        "position": position,
        "kind": FUNCTION,
        "qualname": "login",
        "change_type": CREATED,
        "fingerprint": "c" * 64,
        "decorators": (),
        "lineno": 3,
        "end_lineno": 5,
    }
    fields.update(overrides)
    return DefinitionVersion(**fields)


def _rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(row["name"] for row in connection.execute(f"PRAGMA table_info({table})"))


def _shas(repository: Path) -> list[str]:
    """Every commit git has, oldest first."""
    return git_output(repository, "rev-list", "--reverse", "HEAD").split()


def _store_ast(connection: sqlite3.Connection, commit_sha: str) -> None:
    """Write one parsed file version with one definition, the way the pass will."""
    write_file_versions(connection, [_version(commit_sha=commit_sha)])
    write_definition_versions(connection, [_definition(commit_sha=commit_sha)])


@pytest.fixture
def database(tmp_path: Path):
    """A database with the schema in place and one commit to hang versions on."""
    connection = connect(tmp_path / "ast.db")
    prepare_database(connection)
    write_commits(connection, [_commit()])
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def version_two_database(tmp_path: Path) -> Path:
    """A database shaped the way schema version 2 left it.

    Version 2 knew nothing about the AST tables, and version 3 added them without
    touching anything else — so this is the current schema with the two tables
    taken away and the stamp turned back. A commit is left in it, to show that a
    rebuild throws the old rows away rather than migrating them.
    """
    database = tmp_path / "version-two.db"
    connection = connect(database)
    try:
        prepare_database(connection)
        write_commits(connection, [_commit()])
        with connection:
            connection.execute("DROP TABLE definition_versions")
            connection.execute("DROP TABLE file_versions")
            connection.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    finally:
        connection.close()
    return database


def test_a_fresh_database_has_both_tables(database: sqlite3.Connection) -> None:
    assert _columns(database, "file_versions") == FILE_VERSION_COLUMNS
    assert _columns(database, "definition_versions") == DEFINITION_VERSION_COLUMNS


def test_the_schema_version_is_four(database: sqlite3.Connection) -> None:
    assert SCHEMA_VERSION == "4"
    assert get_meta(database, "schema_version") == "4"


def test_the_tables_hold_no_derived_identity(database: sqlite3.Connection) -> None:
    """v0.3's promise: facts here, identities derived later."""
    stored = set(_columns(database, "file_versions"))
    stored |= set(_columns(database, "definition_versions"))
    assert stored.isdisjoint(DERIVED_IDENTITIES), sorted(stored & set(DERIVED_IDENTITIES))


def test_a_parsed_file_version_round_trips(database: sqlite3.Connection) -> None:
    write_file_versions(database, [_version()])
    row = _rows(database, "file_versions")[0]
    assert row["commit_sha"] == SHA
    assert row["path"] == "src/app.py"
    assert row["content_sha"] == "b" * 40
    assert row["parsed_at_version"] == "3.13.5"
    assert row["parse_error"] is None


def test_a_file_version_that_failed_keeps_its_reason(
    database: sqlite3.Connection,
) -> None:
    write_file_versions(
        database,
        [
            _version(
                parse_error="SyntaxError: invalid syntax",
                error_lineno=4,
                error_offset=1,
            )
        ],
    )
    row = _rows(database, "file_versions")[0]
    assert row["parse_error"].startswith("SyntaxError: ")
    assert (row["error_lineno"], row["error_offset"]) == (4, 1)


def test_every_definition_of_a_version_is_written(database: sqlite3.Connection) -> None:
    """A snapshot, not a change log: the ones that did not change are here too."""
    write_file_versions(database, [_version()])
    write_definition_versions(
        database,
        [
            _definition(position=0, kind=CLASS, qualname="Service", lineno=1, end_lineno=9),
            _definition(
                position=1, kind=FUNCTION, qualname="Service.login", lineno=2, end_lineno=4
            ),
            _definition(
                position=2,
                kind=ASYNC_FUNCTION,
                qualname="Service.fetch",
                change_type=UNCHANGED,
                lineno=6,
                end_lineno=8,
            ),
        ],
    )
    rows = _rows(database, "definition_versions")
    assert [row["qualname"] for row in rows] == [
        "Service",
        "Service.login",
        "Service.fetch",
    ]
    assert [row["position"] for row in rows] == [0, 1, 2]


def test_a_definition_round_trips(database: sqlite3.Connection) -> None:
    write_file_versions(database, [_version()])
    write_definition_versions(
        database,
        [
            _definition(
                change_type=MODIFIED,
                decorators=("dataclass", "frozen"),
                lineno=10,
                end_lineno=42,
            )
        ],
    )
    row = _rows(database, "definition_versions")[0]
    assert row["change_type"] == MODIFIED
    assert row["fingerprint"] == "c" * 64
    assert (row["lineno"], row["end_lineno"]) == (10, 42)
    assert row["decorators"] == '["dataclass", "frozen"]'


def test_the_three_change_types_are_the_only_ones(
    database: sqlite3.Connection,
) -> None:
    """``deleted`` is not among them: a definition that is gone gets no row."""
    write_file_versions(database, [_version()])
    for change_type in (CREATED, MODIFIED, UNCHANGED):
        write_definition_versions(database, [_definition(change_type=change_type)])
        assert _rows(database, "definition_versions")[0]["change_type"] == change_type

    with pytest.raises(sqlite3.IntegrityError):
        write_definition_versions(database, [_definition(change_type="deleted")])


def test_the_kinds_are_the_ones_definitions_returns(database: sqlite3.Connection) -> None:
    write_file_versions(database, [_version()])
    write_definition_versions(
        database,
        [
            _definition(position=0, kind=FUNCTION),
            _definition(position=1, kind=ASYNC_FUNCTION),
            _definition(position=2, kind=CLASS),
        ],
    )
    assert _count(database, "definition_versions") == 3

    with pytest.raises(sqlite3.IntegrityError):
        write_definition_versions(database, [_definition(position=9, kind="lambda")])


def test_two_definitions_can_share_a_qualified_name(
    database: sqlite3.Connection,
) -> None:
    """Which is why the position, and not the name, is part of the key."""
    write_file_versions(database, [_version()])
    write_definition_versions(
        database,
        [
            _definition(position=0, qualname="f", lineno=2, end_lineno=3),
            _definition(position=1, qualname="f", lineno=5, end_lineno=6),
        ],
    )
    rows = _rows(database, "definition_versions")
    assert [(row["position"], row["lineno"]) for row in rows] == [(0, 2), (1, 5)]


def test_a_definition_without_its_file_version_is_refused(
    database: sqlite3.Connection,
) -> None:
    """The foreign key is enforced, so a definition cannot float free."""
    with pytest.raises(sqlite3.IntegrityError):
        write_definition_versions(database, [_definition()])


def test_writing_a_version_twice_replaces_it(database: sqlite3.Connection) -> None:
    write_file_versions(database, [_version(parse_error="SyntaxError: invalid syntax")])
    write_file_versions(database, [_version()])
    rows = _rows(database, "file_versions")
    assert len(rows) == 1
    assert rows[0]["parse_error"] is None


def test_replacing_a_version_takes_its_definitions_with_it(
    database: sqlite3.Connection,
) -> None:
    """Otherwise a second pass would leave the first pass's rows behind."""
    write_file_versions(database, [_version()])
    write_definition_versions(database, [_definition()])
    write_file_versions(database, [_version(content_sha="d" * 40)])
    assert _count(database, "definition_versions") == 0


def test_writing_the_same_definitions_twice_replaces_them(
    database: sqlite3.Connection,
) -> None:
    write_file_versions(database, [_version()])
    write_definition_versions(database, [_definition(qualname="login")])
    write_definition_versions(database, [_definition(qualname="sign_in")])
    rows = _rows(database, "definition_versions")
    assert [row["qualname"] for row in rows] == ["sign_in"]


def test_clear_history_removes_everything(database: sqlite3.Connection) -> None:
    write_file_versions(database, [_version()])
    write_definition_versions(database, [_definition()])
    clear_history(database)
    assert _count(database, "file_versions") == 0
    assert _count(database, "definition_versions") == 0
    assert _count(database, "commits") == 0


def test_a_definition_that_is_gone_gets_no_row(database: sqlite3.Connection) -> None:
    """Absence is the evidence, so nothing has to be written to say it."""
    write_file_versions(database, [_version(content_sha="e" * 40)])
    write_definition_versions(
        database,
        [
            _definition(position=0, qualname="login"),
            _definition(position=1, qualname="logout", lineno=7, end_lineno=9),
        ],
    )
    write_file_versions(database, [_version(content_sha="f" * 40)])
    write_definition_versions(database, [_definition(position=0, qualname="login")])

    rows = _rows(database, "definition_versions")
    assert [row["qualname"] for row in rows] == ["login"]
    assert "deleted" not in {row["change_type"] for row in rows}


def test_a_version_that_could_not_be_parsed_is_not_an_empty_version(
    database: sqlite3.Connection,
) -> None:
    """The whole point: a syntax error must not read as a deleted function.

    Three versions of one file — one that parses with ``foo``, one that does not
    parse at all, one that parses with ``foo`` again. The middle one has a row of
    its own saying it failed, so "it was read and ``foo`` was not there" stays
    distinguishable from "it could not be read".
    """
    three = (SHA, "b" * 40, "c" * 40)
    write_commits(database, [_commit(sha=sha) for sha in three])
    write_file_versions(
        database,
        [
            _version(commit_sha=sha, content_sha=str(number) * 40)
            for number, sha in enumerate(three, start=1)
        ],
    )

    write_definition_versions(database, [_definition(commit_sha=SHA)])
    write_definition_versions(database, [_definition(commit_sha="c" * 40)])
    write_file_versions(
        database,
        [
            _version(
                commit_sha="b" * 40,
                content_sha="2" * 40,
                parse_error="SyntaxError: invalid syntax",
                error_lineno=1,
                error_offset=7,
            )
        ],
    )

    failed = {
        row["commit_sha"]
        for row in database.execute("SELECT commit_sha FROM file_versions"
                                    " WHERE parse_error IS NOT NULL")
    }
    parsed = {
        row["commit_sha"]
        for row in database.execute("SELECT commit_sha FROM file_versions"
                                    " WHERE parse_error IS NULL")
    }
    assert failed == {"b" * 40}
    assert parsed == {SHA, "c" * 40}
    assert _count(database, "definition_versions") == 2
    assert not database.execute(
        "SELECT 1 FROM definition_versions WHERE commit_sha = ?", ("b" * 40,)
    ).fetchone()


def test_a_database_of_schema_version_two_is_rebuilt(
    version_two_database: Path,
) -> None:
    connection = connect(version_two_database)
    try:
        assert get_meta(connection, "schema_version") == "2"
        assert _count(connection, "commits") == 1

        prepare_database(connection)

        assert get_meta(connection, "schema_version") == SCHEMA_VERSION
        assert _columns(connection, "file_versions") == FILE_VERSION_COLUMNS
        assert _columns(connection, "definition_versions") == DEFINITION_VERSION_COLUMNS
        assert _count(connection, "commits") == 0, "rebuilt, not migrated"
    finally:
        connection.close()


def test_a_rescan_of_an_unchanged_history_keeps_the_ast_rows(
    tmp_path: Path,
) -> None:
    """Scenario A. Nothing moved, so nothing is thrown away."""
    repository = build_single_commit_repo(tmp_path / "repo")
    database = tmp_path / "analysis.db"
    analyze(repository, database)

    connection = connect(database)
    try:
        _store_ast(connection, _shas(repository)[0])
        assert _count(connection, "definition_versions") == 1
    finally:
        connection.close()

    analyze(repository, database)

    connection = connect(database)
    try:
        assert _count(connection, "file_versions") == 1
        assert _count(connection, "definition_versions") == 1
        assert _count(connection, "commits") == 1
    finally:
        connection.close()


def test_a_rescan_after_a_new_commit_keeps_the_old_ast_rows(tmp_path: Path) -> None:
    """Scenario B. The new commit has no AST rows, and the old ones are untouched."""
    repository = build_single_commit_repo(tmp_path / "repo")
    database = tmp_path / "analysis.db"
    analyze(repository, database)
    first = _shas(repository)[0]

    connection = connect(database)
    try:
        _store_ast(connection, first)
    finally:
        connection.close()

    add_commit(repository, "a second commit")
    analyze(repository, database)
    added = _shas(repository)[1]

    connection = connect(database)
    try:
        assert _count(connection, "commits") == 2
        assert _count(connection, "file_versions") == 1
        assert _count(connection, "definition_versions") == 1
        assert connection.execute(
            "SELECT 1 FROM file_versions WHERE commit_sha = ?", (first,)
        ).fetchone()
        assert not connection.execute(
            "SELECT 1 FROM file_versions WHERE commit_sha = ?", (added,)
        ).fetchone()
    finally:
        connection.close()


def test_a_rescan_drops_the_ast_rows_of_commits_git_no_longer_has(
    tmp_path: Path,
) -> None:
    """Scenario C. A B C stay, D E go — and only their AST rows go with them."""
    repository = build_single_commit_repo(tmp_path / "repo")
    add_commit(repository, "second")
    add_commit(repository, "third")
    database = tmp_path / "analysis.db"
    analyze(repository, database)
    kept, dropped_one, dropped_two = _shas(repository)

    connection = connect(database)
    try:
        for sha in (kept, dropped_one, dropped_two):
            _store_ast(connection, sha)
        assert _count(connection, "definition_versions") == 3
    finally:
        connection.close()

    git_output(repository, "reset", "--hard", "HEAD~2")
    add_commit(repository, "replacement")
    analyze(repository, database)

    connection = connect(database)
    try:
        assert _count(connection, "commits") == 2
        assert _count(connection, "file_versions") == 1
        assert _count(connection, "definition_versions") == 1
        assert connection.execute(
            "SELECT 1 FROM definition_versions WHERE commit_sha = ?", (kept,)
        ).fetchone()
        for gone in (dropped_one, dropped_two):
            assert not connection.execute(
                "SELECT 1 FROM definition_versions WHERE commit_sha = ?", (gone,)
            ).fetchone()
    finally:
        connection.close()


def test_an_analysis_never_writes_ast_rows(tmp_path: Path) -> None:
    """Scenario D. ``analyze`` is the git layer's, and stays out of the AST's."""
    repository = build_single_commit_repo(tmp_path / "repo")
    database = tmp_path / "analysis.db"
    analyze(repository, database)

    connection = connect(database)
    try:
        assert _count(connection, "commits") == 1
        assert _count(connection, "file_versions") == 0
        assert _count(connection, "definition_versions") == 0
    finally:
        connection.close()


def test_a_commit_git_still_has_is_rewritten_without_losing_its_definitions(
    database: sqlite3.Connection,
) -> None:
    """The reason the commit row is updated rather than replaced."""
    write_file_versions(database, [_version()])
    write_definition_versions(database, [_definition()])
    write_commits(database, [_commit(message="rewritten")])
    assert _count(database, "file_versions") == 1
    assert _count(database, "definition_versions") == 1
    assert _rows(database, "commits")[0]["message"] == "rewritten"


def test_a_commit_that_is_gone_takes_its_ast_rows_with_it(
    database: sqlite3.Connection,
) -> None:
    write_file_versions(database, [_version()])
    write_definition_versions(database, [_definition()])
    write_commits(database, [_commit(sha="9" * 40)])
    assert _count(database, "commits") == 1
    assert _count(database, "file_versions") == 0
    assert _count(database, "definition_versions") == 0
