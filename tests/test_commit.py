"""Tests for the commit detail view and its command."""

import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.commit import (
    CommitNotFound,
    build_file_table,
    line_counters,
    load_commit,
)
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.history import Commit, FileChange, read_commits
from codearchaeology.storage import connect, write_commits
from sample_repo import build_single_commit_repo

runner = CliRunner()

WHEN = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "history.db"


@pytest.fixture
def analyzed(sample_repo: Path, database: Path) -> Path:
    analyze(sample_repo, database)
    return database


def _sha_of(repository: Path, message: str) -> str:
    return next(
        commit.sha for commit in read_commits(repository) if commit.message == message
    )


def _synthetic_commit(sha: str) -> Commit:
    """A commit that exists only in the database, for lookup edge cases."""
    return Commit(
        sha=sha,
        parents=(),
        author_name="Ada Lovelace",
        author_email="ada@example.com",
        authored_at=WHEN,
        committed_at=WHEN,
        message=f"synthetic {sha[:4]}",
        changes=(),
    )


def _render(commit: Commit, width: int = 80) -> str:
    stream = io.StringIO()
    Console(file=stream, width=width).print(build_file_table(commit.changes, width))
    return stream.getvalue()


def test_finds_a_commit_by_full_sha(
    sample_repo: Path, analyzed: Path
) -> None:
    sha = _sha_of(sample_repo, "Fix login bug")

    found = load_commit(sample_repo.resolve(), analyzed, sha)

    assert found.sha == sha
    assert found.message == "Fix login bug"


def test_finds_a_commit_by_short_prefix(sample_repo: Path, analyzed: Path) -> None:
    sha = _sha_of(sample_repo, "Add caching")

    found = load_commit(sample_repo.resolve(), analyzed, sha[:8])

    assert found.sha == sha


def test_unknown_prefix_is_reported(sample_repo: Path, analyzed: Path) -> None:
    with pytest.raises(CommitNotFound, match="no stored commit starts with"):
        load_commit(sample_repo.resolve(), analyzed, "f" * 40)


def test_ambiguous_prefix_names_the_candidates(
    sample_repo: Path, analyzed: Path
) -> None:
    # The fixture's own shas happen to agree on no prefix at all, so two
    # synthetic commits are added that deliberately share one.
    connection = connect(analyzed)
    try:
        write_commits(
            connection,
            [
                _synthetic_commit("dead" + "0" * 36),
                _synthetic_commit("dead" + "1" * 36),
            ],
        )
    finally:
        connection.close()

    with pytest.raises(CommitNotFound) as raised:
        load_commit(sample_repo.resolve(), analyzed, "dead")

    message = str(raised.value)
    assert "matches 2 commits" in message
    assert "dead00000000" in message
    assert "dead11111111" in message


@pytest.mark.parametrize("prefix", ["", "hello", "%", "abc def"])
def test_a_prefix_that_is_not_a_sha_is_reported(
    sample_repo: Path, analyzed: Path, prefix: str
) -> None:
    with pytest.raises(CommitNotFound, match="is not a commit sha"):
        load_commit(sample_repo.resolve(), analyzed, prefix)


def test_line_counters_for_a_normal_change() -> None:
    change = FileChange(
        path="a.py", change_type="M", added_lines=12, deleted_lines=3
    )

    assert line_counters(change) == "+12/-3"


def test_line_counters_for_a_binary_change() -> None:
    change = FileChange(
        path="logo.png", change_type="A", added_lines=None, deleted_lines=None
    )

    assert line_counters(change) == "-"


def test_binary_files_render_a_dash_in_the_table(
    sample_repo: Path, analyzed: Path
) -> None:
    sha = _sha_of(sample_repo, "Add logo and unicode module, drop legacy helper")
    commit = load_commit(sample_repo.resolve(), analyzed, sha)

    line = next(
        line
        for line in _render(commit).splitlines()
        if "assets/logo.png" in line
    )

    assert " - " in line
    assert "+" not in line


def test_rename_is_rendered_with_an_arrow(sample_repo: Path, analyzed: Path) -> None:
    sha = _sha_of(sample_repo, "Move app module into the core package")
    commit = load_commit(sample_repo.resolve(), analyzed, sha)

    assert "app.py \u2192 core/app.py" in _render(commit)


def test_command_rejects_a_name_instead_of_a_sha(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "body", message="Only commit")
    database = tmp_path / "body.db"
    analyze(repository, database)

    result = runner.invoke(
        app, ["commit", "HEAD", str(repository), "--db", str(database)]
    )

    assert result.exit_code == 1
    assert "is not a commit sha" in result.stderr


def test_command_prints_metadata_and_message(tmp_path: Path) -> None:
    repository = build_single_commit_repo(
        tmp_path / "body", message="Subject line\n\nA body that goes on."
    )
    database = tmp_path / "body.db"
    analyze(repository, database)
    sha = _sha_of(repository, "Subject line\n\nA body that goes on.")

    result = runner.invoke(
        app, ["commit", sha[:8], str(repository), "--db", str(database)]
    )

    assert result.exit_code == 0
    assert sha in result.stdout
    assert "Ada Lovelace <ada@example.com>" in result.stdout
    assert "Subject line" in result.stdout
    assert "A body that goes on." in result.stdout
    assert "Parents     (none)" in result.stdout


def test_command_shows_both_parents_and_no_files_for_a_merge(
    sample_repo: Path, analyzed: Path
) -> None:
    sha = _sha_of(sample_repo, "Merge branch 'feature/caching'")
    merge = load_commit(sample_repo.resolve(), analyzed, sha)

    result = runner.invoke(
        app, ["commit", sha[:8], str(sample_repo), "--db", str(analyzed)]
    )

    assert result.exit_code == 0
    for parent in merge.parents:
        assert parent[:SHORT_SHA_LENGTH] in result.stdout
    assert "No file changes recorded" in result.stdout
    assert "no diff for a merge commit" in result.stdout


def test_command_reports_an_unknown_sha(sample_repo: Path, analyzed: Path) -> None:
    result = runner.invoke(
        app, ["commit", "f" * 40, str(sample_repo), "--db", str(analyzed)]
    )

    assert result.exit_code == 1
    assert "no stored commit starts with" in result.stderr


def test_command_asks_for_an_analysis_when_there_is_none(
    sample_repo: Path, database: Path
) -> None:
    result = runner.invoke(
        app, ["commit", "abc123", str(sample_repo), "--db", str(database)]
    )

    assert result.exit_code == 1
    assert "archaeology analyze" in result.stderr
