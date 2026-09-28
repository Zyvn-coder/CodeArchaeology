"""Tests for the analyze operation and its command."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze, stale_analysis_note
from codearchaeology.cache import CACHE_DIRECTORY_VARIABLE, cache_directory, database_path
from codearchaeology.cli import app
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.history import Commit, GitError, read_commits, read_head_sha
from codearchaeology.storage import (
    SCHEMA_VERSION,
    connect,
    get_meta,
    read_stored_commits,
)
from sample_repo import add_commit, build_single_commit_repo

runner = CliRunner()

FIXTURE_COMMITS = 6
FIXTURE_FILE_CHANGES = 9


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "analysis" / "history.db"


def _stored(database: Path) -> list[Commit]:
    connection = connect(database)
    try:
        return read_stored_commits(connection)
    finally:
        connection.close()


def test_reports_what_it_stored(sample_repo: Path, database: Path) -> None:
    result = analyze(sample_repo, database)

    assert result.repository_root == sample_repo.resolve()
    assert result.database == database
    assert result.head_sha == read_head_sha(sample_repo)
    assert result.commits == FIXTURE_COMMITS
    assert result.file_changes == FIXTURE_FILE_CHANGES
    assert result.earliest_commit < result.latest_commit


def test_stores_everything_it_reports(sample_repo: Path, database: Path) -> None:
    result = analyze(sample_repo, database)

    stored = _stored(database)

    assert len(stored) == result.commits
    assert sum(len(commit.changes) for commit in stored) == result.file_changes
    assert stored == read_commits(sample_repo)


def test_creates_the_directory_it_needs(sample_repo: Path, database: Path) -> None:
    assert not database.parent.exists()

    analyze(sample_repo, database)

    assert database.is_file()


def test_records_the_repository_and_its_head(sample_repo: Path, database: Path) -> None:
    result = analyze(sample_repo, database)

    connection = connect(database)
    try:
        assert get_meta(connection, "repository_root") == str(result.repository_root)
        assert get_meta(connection, "head_sha") == result.head_sha
    finally:
        connection.close()


def test_accepts_a_directory_inside_the_repository(
    sample_repo: Path, database: Path
) -> None:
    result = analyze(sample_repo / "core", database)

    assert result.repository_root == sample_repo.resolve()


def test_rescanning_replaces_the_previous_history(
    sample_repo: Path, tmp_path: Path, database: Path
) -> None:
    analyze(sample_repo, database)
    assert len(_stored(database)) == FIXTURE_COMMITS

    analyze(build_single_commit_repo(tmp_path / "tiny"), database)

    assert len(_stored(database)) == 1


def test_rejects_a_directory_outside_any_repository(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="not a git repository"):
        analyze(tmp_path, tmp_path / "history.db")


def test_an_older_database_is_rebuilt_rather_than_failing(
    sample_repo: Path, older_database: Path
) -> None:
    """The writer has to rebuild too, not only the reader.

    create_schema uses CREATE TABLE IF NOT EXISTS, so it cannot add a column to
    a table that already exists. Without an explicit rebuild the insert would
    fail with a raw sqlite error naming a column that is not there, which tells
    the user nothing they can act on.
    """
    result = analyze(sample_repo, older_database)

    connection = connect(older_database)
    try:
        assert get_meta(connection, "schema_version") == SCHEMA_VERSION
        assert len(read_stored_commits(connection)) == FIXTURE_COMMITS
    finally:
        connection.close()

    assert result.commits == FIXTURE_COMMITS


def test_database_path_is_stable_and_repository_specific(tmp_path: Path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()

    assert database_path(tmp_path / "one") == database_path(tmp_path / "one")
    assert database_path(tmp_path / "one") != database_path(tmp_path / "two")


def test_database_path_lives_in_the_cache_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CACHE_DIRECTORY_VARIABLE, str(tmp_path / "cache"))

    assert cache_directory() == tmp_path / "cache"
    assert database_path(tmp_path).parent == tmp_path / "cache"


def test_no_note_while_the_analysis_is_current(
    sample_repo: Path, database: Path
) -> None:
    result = analyze(sample_repo, database)

    assert stale_analysis_note(sample_repo, result.head_sha) is None


def test_note_names_both_ends_once_the_repository_moves_on(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "moving")
    database = tmp_path / "history.db"
    result = analyze(repository, database)

    added = add_commit(repository, "A commit made after the analysis")

    note = stale_analysis_note(repository, result.head_sha)

    assert note is not None
    assert result.head_sha[:SHORT_SHA_LENGTH] in note
    assert added[:SHORT_SHA_LENGTH] in note
    assert "archaeology analyze" in note


def test_no_note_without_a_stored_head(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "headless")

    assert stale_analysis_note(repository, None) is None
    assert stale_analysis_note(repository, "") is None


def test_no_note_when_the_repository_cannot_be_read(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repository"
    plain.mkdir()

    assert stale_analysis_note(plain, "0" * 40) is None


def test_command_prints_a_summary(sample_repo: Path, database: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(sample_repo), "--db", str(database)]
    )

    assert result.exit_code == 0
    assert f"Commits     {FIXTURE_COMMITS} ({FIXTURE_FILE_CHANGES} file changes)" in (
        result.stdout
    )
    assert str(database) in result.stdout


def test_command_explains_a_missing_repository(tmp_path: Path, database: Path) -> None:
    result = runner.invoke(app, ["analyze", str(tmp_path), "--db", str(database)])

    assert result.exit_code == 1
    assert "not a git repository" in result.stderr


def test_command_stays_quiet_about_a_full_repository(
    sample_repo: Path, database: Path
) -> None:
    result = runner.invoke(app, ["analyze", str(sample_repo), "--db", str(database)])

    assert result.exit_code == 0
    assert "shallow" not in result.stderr


def test_command_warns_about_a_shallow_clone(
    shallow_clone: Path, database: Path
) -> None:
    result = runner.invoke(
        app, ["analyze", str(shallow_clone), "--db", str(database)]
    )

    assert result.exit_code == 0
    assert "shallow clone" in result.stderr


def test_command_rejects_a_path_that_does_not_exist(
    tmp_path: Path, database: Path
) -> None:
    missing = tmp_path / "nowhere"

    result = runner.invoke(app, ["analyze", str(missing), "--db", str(database)])

    assert result.exit_code == 2


def test_command_can_run_twice(sample_repo: Path, database: Path) -> None:
    first = runner.invoke(app, ["analyze", str(sample_repo), "--db", str(database)])
    second = runner.invoke(app, ["analyze", str(sample_repo), "--db", str(database)])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert len(_stored(database)) == FIXTURE_COMMITS
