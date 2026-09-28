"""The whole "read a repository, store it" operation, in one call."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codearchaeology.cache import database_path
from codearchaeology.history import (
    GitError,
    find_repository_root,
    read_commits,
    read_head_sha,
)
from codearchaeology.storage import (
    clear_history,
    connect,
    create_schema,
    get_meta,
    set_meta,
    write_commits,
)


class AnalysisError(RuntimeError):
    """Raised when a repository has no stored analysis that can be used."""


@contextmanager
def open_analysis(repository_root, database):
    """Open the stored analysis of *repository_root* and hand out a connection.

    Raises :class:`AnalysisError` naming what is missing: no database file, one
    that was never filled in, or one holding a different repository.
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
        yield connection
    finally:
        connection.close()


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
        create_schema(connection)
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
