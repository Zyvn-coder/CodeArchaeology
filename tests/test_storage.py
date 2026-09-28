"""Tests for the SQLite storage layer."""

import sqlite3
from pathlib import Path

import pytest

from codearchaeology.history import Commit, read_commits
from codearchaeology.storage import (
    SCHEMA_VERSION,
    connect,
    create_schema,
    get_meta,
    prepare_database,
    read_schema_version,
    read_stored_commits,
    write_commits,
)


@pytest.fixture
def commits(sample_repo: Path) -> list[Commit]:
    """The commits the reader produces for the fixture repository."""
    return read_commits(sample_repo)


@pytest.fixture
def connection(tmp_path: Path, commits: list[Commit]):
    """A database that already holds the fixture repository's commits.

    Depending on *commits* on purpose: a test asking for a database should never
    silently get an empty one.
    """
    connection = connect(tmp_path / "history.db")
    create_schema(connection)
    write_commits(connection, commits)
    yield connection
    connection.close()


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = ("commits", "commit_parents", "commit_files")
    return {
        table: connection.execute(
            f"SELECT COUNT(*) AS n FROM {table}"
        ).fetchone()["n"]
        for table in tables
    }


def test_schema_version_is_recorded(connection: sqlite3.Connection) -> None:
    assert get_meta(connection, "schema_version") == SCHEMA_VERSION


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]


def test_a_file_that_was_just_created_has_no_schema_version(tmp_path: Path) -> None:
    """No tables at all is a different thing from an older version's tables."""
    connection = connect(tmp_path / "fresh.db")
    try:
        assert read_schema_version(connection) is None
    finally:
        connection.close()


def test_prepare_database_leaves_a_current_database_alone(
    connection: sqlite3.Connection,
) -> None:
    before = _table_counts(connection)

    prepare_database(connection)

    assert _table_counts(connection) == before


def test_prepare_database_rebuilds_a_database_from_another_version(
    older_database: Path,
) -> None:
    connection = connect(older_database)
    try:
        assert "similarity" not in _columns(connection, "commit_files")

        prepare_database(connection)

        assert read_schema_version(connection) == SCHEMA_VERSION
        assert "similarity" in _columns(connection, "commit_files")
    finally:
        connection.close()


def test_round_trip_preserves_the_similarity_score(
    connection: sqlite3.Connection,
) -> None:
    """The score is a fact git reported, so it survives storage like the rest."""
    scores = [
        change.similarity
        for commit in read_stored_commits(connection)
        for change in commit.changes
        if change.change_type == "R"
    ]

    assert scores == [100]


def test_missing_meta_key_returns_none(connection: sqlite3.Connection) -> None:
    assert get_meta(connection, "nothing_here") is None


def test_round_trip_preserves_every_commit(
    connection: sqlite3.Connection, commits: list[Commit]
) -> None:
    assert read_stored_commits(connection) == commits


def test_rename_keeps_both_paths(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT old_path, path FROM commit_files WHERE old_path IS NOT NULL"
    ).fetchone()

    assert row["old_path"] == "app.py"
    assert row["path"] == "core/app.py"


def test_binary_file_stores_null_line_counts(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT added_lines, deleted_lines FROM commit_files WHERE path = ?",
        ("assets/logo.png",),
    ).fetchone()

    assert row is not None
    assert row["added_lines"] is None
    assert row["deleted_lines"] is None


def test_parent_order_is_preserved(
    connection: sqlite3.Connection, commits: list[Commit]
) -> None:
    merge = next(commit for commit in commits if commit.is_merge)

    rows = connection.execute(
        "SELECT parent_sha FROM commit_parents WHERE commit_sha = ? ORDER BY position",
        (merge.sha,),
    ).fetchall()

    assert tuple(row["parent_sha"] for row in rows) == merge.parents


def test_merge_commit_is_stored_without_files(
    connection: sqlite3.Connection, commits: list[Commit]
) -> None:
    merge = next(commit for commit in commits if commit.is_merge)

    row = connection.execute(
        "SELECT COUNT(*) AS n FROM commit_files WHERE commit_sha = ?", (merge.sha,)
    ).fetchone()

    assert row["n"] == 0
    assert connection.execute(
        "SELECT COUNT(*) AS n FROM commit_parents WHERE commit_sha = ?", (merge.sha,)
    ).fetchone()["n"] == 2


def test_writing_twice_changes_nothing(
    connection: sqlite3.Connection, commits: list[Commit]
) -> None:
    before = _table_counts(connection)

    write_commits(connection, commits)

    assert _table_counts(connection) == before
    assert read_stored_commits(connection) == commits


def test_foreign_keys_reject_a_file_without_its_commit(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO commit_files (commit_sha, path, change_type)"
            " VALUES (?, ?, ?)",
            ("0" * 40, "orphan.py", "A"),
        )
