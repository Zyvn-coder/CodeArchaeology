"""Shared pytest fixtures."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from sample_repo import build_sample_repo


def pytest_configure(config) -> None:
    """Refuse to run on an interpreter other than the one that was requested.

    The CI matrix sets ``EXPECTED_PYTHON``. Without this check a job could pass
    while quietly testing a different Python than the one it claims to test,
    which is the kind of unverified claim this project tries to avoid.
    """
    expected = os.environ.get("EXPECTED_PYTHON")
    if not expected:
        return

    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running != expected:
        raise pytest.UsageError(
            f"this run asked for Python {expected} but is running on {running}"
        )


@pytest.fixture(scope="session")
def sample_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture repository: six commits, a rename, a deletion and a merge."""
    return build_sample_repo(tmp_path_factory.mktemp("sample-repo"))


@pytest.fixture(scope="session")
def shallow_clone(
    sample_repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """A clone of the fixture repository that is missing its oldest commits.

    Cloned through a ``file://`` URL on purpose: git ignores ``--depth`` for a
    plain local path and quietly makes a full clone instead.
    """
    destination = tmp_path_factory.mktemp("shallow") / "clone"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--depth",
            "2",
            sample_repo.as_uri(),
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    return destination


@pytest.fixture
def older_database(tmp_path: Path) -> Path:
    """A database file shaped the way schema version 1 left it.

    Frozen here rather than derived from the current schema, because it is a
    record of what an earlier version of the tool actually wrote. Its
    ``commit_files`` has no ``similarity`` column, so anything that reads or
    writes that column fails unless the tables are rebuilt first.
    """
    database = tmp_path / "older.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE commits (
                sha             TEXT PRIMARY KEY,
                author_name     TEXT NOT NULL,
                author_email    TEXT NOT NULL,
                authored_at     TEXT NOT NULL,
                committed_at    TEXT NOT NULL,
                committed_epoch INTEGER NOT NULL,
                message         TEXT NOT NULL
            );
            CREATE TABLE commit_parents (
                commit_sha TEXT    NOT NULL,
                position   INTEGER NOT NULL,
                parent_sha TEXT    NOT NULL,
                PRIMARY KEY (commit_sha, position)
            );
            CREATE TABLE commit_files (
                commit_sha    TEXT    NOT NULL,
                path          TEXT    NOT NULL,
                old_path      TEXT,
                change_type   TEXT    NOT NULL,
                added_lines   INTEGER,
                deleted_lines INTEGER,
                PRIMARY KEY (commit_sha, path)
            );
            INSERT INTO meta VALUES ('schema_version', '1');
            """
        )
        connection.commit()
    finally:
        connection.close()
    return database
