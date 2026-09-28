"""The whole "read a repository, store it" operation, in one call."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codearchaeology.cache import database_path
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.history import (
    GitError,
    find_repository_root,
    read_commits,
    read_head_sha,
)
from codearchaeology.storage import (
    SCHEMA_VERSION,
    clear_history,
    connect,
    get_meta,
    prepare_database,
    set_meta,
    write_commits,
)


class AnalysisError(RuntimeError):
    """Raised when a repository has no stored analysis that can be used."""


@contextmanager
def open_analysis(repository_root, database):
    """Open the stored analysis of *repository_root* and hand out a connection.

    Raises :class:`AnalysisError` naming what is missing: no database file, one
    that was never filled in, one holding a different repository, or one written
    by a schema version this code does not read.
    """
    database = Path(database)
    expected_root = str(Path(repository_root).resolve())

    if not database.is_file():
        raise AnalysisError(_missing_analysis(database, expected_root))

    connection = connect(database)
    try:
        stored_root = get_meta(connection, "repository_root")
        if stored_root is None:
            raise AnalysisError(_missing_analysis(database, expected_root))
        if stored_root != expected_root:
            raise AnalysisError(
                f"{database} holds {stored_root}, not {expected_root};"
                f" run 'archaeology analyze {expected_root}' to replace it"
            )

        # Checked before anything reads a table: a database from another schema
        # version would otherwise fail deeper down with a raw sqlite error about
        # a missing column, which tells the user nothing they can act on.
        stored_version = get_meta(connection, "schema_version")
        if stored_version != SCHEMA_VERSION:
            raise AnalysisError(
                f"{database} was written with schema version"
                f" {stored_version or 'unknown'}, and this version of archaeology"
                f" reads {SCHEMA_VERSION};"
                f" run 'archaeology analyze {expected_root}' to rebuild it"
            )

        yield connection
    finally:
        connection.close()


def stale_analysis_note(repository_root, stored_head_sha: str | None) -> str | None:
    """Return a note when *repository_root* has moved past the stored analysis.

    The stored history is not wrong, only incomplete, so reading it stays
    allowed: callers print the note instead of refusing to work. A repository
    that can no longer be read is not a reason to hide the stored history
    either, which is why the git call is allowed to fail quietly.
    """
    if not stored_head_sha:
        return None

    try:
        current_head = read_head_sha(repository_root)
    except GitError:
        return None

    if current_head == stored_head_sha:
        return None

    return (
        f"Note: this analysis stops at {stored_head_sha[:SHORT_SHA_LENGTH]},"
        f" but HEAD is now {current_head[:SHORT_SHA_LENGTH]};"
        f" run 'archaeology analyze {repository_root}' to refresh it"
    )


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """What an analysis found, for reporting back to the user."""

    repository_root: Path
    database: Path
    head_sha: str
    commits: int
    file_changes: int
    earliest_commit: datetime
    latest_commit: datetime


def analyze(path, database=None) -> AnalysisResult:
    """Read the repository containing *path* and store its history.

    Raises :class:`GitError` when *path* is not inside a Git repository.
    """
    repository_root = find_repository_root(path)
    if repository_root is None:
        raise GitError(f"not a git repository: {Path(path)}")

    commits = read_commits(repository_root)
    head_sha = read_head_sha(repository_root)

    if database is None:
        database = database_path(repository_root)
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)

    connection = connect(database)
    try:
        # Rebuilds the tables when the database was written by another schema
        # version, so that a stale cache is refreshed instead of failing on a
        # column that is not there yet.
        prepare_database(connection)
        # A rescan replaces what is already there. Without this, commits that a
        # rebase or an amend removed from git would stay in the database
        # forever.
        clear_history(connection)
        write_commits(connection, commits)
        set_meta(connection, "repository_root", str(repository_root))
        set_meta(connection, "head_sha", head_sha)
    finally:
        connection.close()

    return AnalysisResult(
        repository_root=repository_root,
        database=database,
        head_sha=head_sha,
        commits=len(commits),
        file_changes=sum(len(commit.changes) for commit in commits),
        earliest_commit=min(commit.committed_at for commit in commits),
        latest_commit=max(commit.committed_at for commit in commits),
    )


def _missing_analysis(database: Path, repository_root: str) -> str:
    return (
        f"no analysis of {repository_root} at {database};"
        f" run 'archaeology analyze {repository_root}' first"
    )
