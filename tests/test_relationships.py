"""Tests for the two-way link between commits and files."""

from pathlib import Path

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.history import Commit, read_commits
from codearchaeology.lifecycle import Lifecycle, build_lifecycles
from codearchaeology.relationships import (
    commits_of,
    commits_touching,
    files_of,
    load_commits_touching,
)
from codearchaeology.storage import connect
from sample_repo import build_lifecycle_repo


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture with renames, a deletion and a reused name in it."""
    return build_lifecycle_repo(tmp_path_factory.mktemp("relationships"))


@pytest.fixture(scope="module")
def commits(repository: Path) -> list[Commit]:
    return read_commits(repository)


@pytest.fixture(scope="module")
def lives(commits) -> tuple[Lifecycle, ...]:
    return build_lifecycles(commits)


@pytest.fixture(scope="module")
def connection(repository: Path, tmp_path_factory: pytest.TempPathFactory):
    database = tmp_path_factory.mktemp("relationships-db") / "history.db"
    analyze(repository, database)
    connection = connect(database)
    yield connection
    connection.close()


def _life(lives: tuple[Lifecycle, ...], path: str) -> Lifecycle:
    matches = [life for life in lives if path in life.path_history]
    assert len(matches) == 1
    return matches[0]


def _sha(commits: list[Commit], message: str) -> str:
    matches = [commit for commit in commits if commit.message == message]
    assert len(matches) == 1
    return matches[0].sha


def test_a_commit_knows_the_files_it_touched(commits: list[Commit]) -> None:
    created = commits[-1]

    assert files_of(created) == ("app.py", "heavy.py", "kept.py", "plain.py")


def test_a_file_knows_its_commits_across_its_renames(lives) -> None:
    """The file is followed, not the name: two renames do not end the list."""
    first_app = _life(lives, "src/core/app.py")

    assert len(commits_of(first_app)) == 4
    assert commits_of(first_app)[0] == first_app.created.commit_sha
    assert commits_of(first_app)[-1] == first_app.deleted.commit_sha


def test_a_rename_counts_for_both_of_its_names(
    connection, commits: list[Commit]
) -> None:
    """The commit that moved the file touched the old name by taking it away and
    the new one by putting it there, so both queries have to find it."""
    moved = _sha(commits, "Move app.py into the src package")

    assert moved in {commit.sha for commit in commits_touching(connection, "app.py")}
    assert moved in {
        commit.sha for commit in commits_touching(connection, "src/app.py")
    }


def test_the_name_view_and_the_identity_view_disagree(
    connection, lives, commits: list[Commit]
) -> None:
    """Where a name was reused, and why both answers have to exist.

    ``app.py`` was renamed away and a different file later took the name back.
    Asking for the name returns the second file's birth, which the first file
    never saw. Asking for the file returns the two renames and the deletion,
    which happened under names the query never looks at.
    """
    first_app = _life(lives, "src/core/app.py")

    by_name = {commit.sha for commit in commits_touching(connection, "app.py")}
    by_identity = set(commits_of(first_app))

    assert by_name - by_identity == {_sha(commits, "Create a second app.py")}
    assert by_identity - by_name == {
        _sha(commits, "Move app.py into src/core and edit plain.py"),
        _sha(commits, "Delete the app module"),
    }


def test_commits_come_back_newest_first(connection) -> None:
    found = commits_touching(connection, "plain.py")

    assert [commit.committed_at for commit in found] == sorted(
        (commit.committed_at for commit in found), reverse=True
    )


def test_a_path_nothing_touched_has_no_commits(connection) -> None:
    assert commits_touching(connection, "nowhere/at/all.py") == ()


def test_every_commit_appears_under_every_file_it_touched(
    connection, commits: list[Commit]
) -> None:
    """The two directions are the same facts, so they have to agree.

    This walks the whole fixture in one direction and checks it against the
    other, rather than testing either one on its own.
    """
    for commit in commits:
        for path in files_of(commit):
            found = {other.sha for other in commits_touching(connection, path)}

            assert commit.sha in found, (commit.sha, path)


def test_the_stored_answer_matches_what_git_reports(
    repository: Path, commits: list[Commit], tmp_path: Path
) -> None:
    """Checked against git's own output rather than against the same query."""
    database = tmp_path / "from-git.db"
    analyze(repository, database)
    path = "src/core/app.py"

    expected = [
        commit.sha
        for commit in commits
        if path in {change.path for change in commit.changes}
        or path in {change.old_path for change in commit.changes}
    ]

    found = load_commits_touching(repository, database, path)

    assert [commit.sha for commit in found] == expected
