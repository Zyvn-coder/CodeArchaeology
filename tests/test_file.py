"""Tests for the single-file view and its command."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.file import build_block, find_files, normalise
from codearchaeology.history import read_commits
from codearchaeology.lifecycle import Lifecycle, build_lifecycles
from sample_repo import add_commit, build_lifecycle_repo, build_sample_repo

runner = CliRunner()


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_lifecycle_repo(tmp_path_factory.mktemp("file-view"))


@pytest.fixture(scope="module")
def lives(repository: Path) -> tuple[Lifecycle, ...]:
    return build_lifecycles(read_commits(repository))


@pytest.fixture(scope="module")
def sample_lives(tmp_path_factory: pytest.TempPathFactory) -> tuple[Lifecycle, ...]:
    repository = build_sample_repo(tmp_path_factory.mktemp("file-sample"))
    return build_lifecycles(read_commits(repository))


@pytest.fixture(scope="module")
def database(repository: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("file-db") / "history.db"
    analyze(repository, path)
    return path


def _value(block: str, label: str) -> str:
    """The value on one labelled line of the block."""
    for line in block.splitlines():
        if line.startswith(label):
            return line[len(label) :].strip()
    raise AssertionError(f"{label!r} is not in the block:\n{block}")


def _run(repository: Path, database: Path, *arguments: str):
    return runner.invoke(
        app, ["file", *arguments, str(repository), "--db", str(database)]
    )


def test_a_path_is_taken_as_git_writes_it() -> None:
    assert normalise("src\\app.py") == "src/app.py"
    assert normalise("./src/app.py") == "src/app.py"
    assert normalise("src/app.py") == "src/app.py"
    assert normalise(".gitignore") == ".gitignore"


def test_a_file_is_found_under_any_of_its_names(lives) -> None:
    for name in ("app.py", "src/app.py", "src/core/app.py"):
        assert find_files(lives, name)


def test_a_reused_name_belongs_to_more_than_one_file(lives) -> None:
    """``app.py`` was renamed away and a different file later took the name."""
    assert len(find_files(lives, "app.py")) == 2
    assert len(find_files(lives, "src/core/app.py")) == 1


def test_the_block_lists_the_names_the_file_carried(lives) -> None:
    first_app = find_files(lives, "src/core/app.py")[0]

    block = build_block(first_app)

    assert block.startswith("src/core/app.py\n")
    assert "app.py -> src/app.py -> src/core/app.py" in block


def test_a_file_that_was_never_renamed_has_no_history_line(lives) -> None:
    plain = find_files(lives, "plain.py")[0]

    assert "History:" not in build_block(plain)


def test_the_block_reports_the_numbers(lives) -> None:
    plain = find_files(lives, "plain.py")[0]

    block = build_block(plain)

    assert _value(block, "Commits:") == "2"
    assert _value(block, "Modifications:") == "1"
    assert _value(block, "Renames:") == "0"
    assert _value(block, "Additions:") == "8"
    assert _value(block, "Deletions:") == "1"
    assert _value(block, "Net change:") == "+7"


def test_a_file_that_was_never_modified_has_no_last_modified_time(lives) -> None:
    """``kept.py`` was renamed but never edited, so there is nothing to report."""
    kept = find_files(lives, "kept.py")[0]

    assert _value(build_block(kept), "Last modified:") == "-"


def test_a_deleted_file_says_when_it_ended(lives) -> None:
    first_app = find_files(lives, "src/core/app.py")[0]

    block = build_block(first_app)

    assert _value(block, "Deleted:").startswith("2024-05-04")


def test_a_binary_file_says_why_its_line_counts_are_zero(sample_lives) -> None:
    """Git reports no line counts for a binary file, so the zeroes above the
    line would otherwise read as "nothing ever happened here"."""
    logo = find_files(sample_lives, "assets/logo.png")[0]

    block = build_block(logo)

    assert _value(block, "Additions:") == "0"
    assert _value(block, "Binary changes:") == "1"


def test_command_prints_one_file(repository: Path, database: Path) -> None:
    result = _run(repository, database, "src/core/app.py")

    assert result.exit_code == 0
    assert "History:" in result.stdout
    assert "Net change:" in result.stdout


def test_command_shows_every_file_that_carried_a_reused_name(
    repository: Path, database: Path
) -> None:
    result = _run(repository, database, "app.py")

    assert result.exit_code == 0
    assert result.stdout.count("Net change:") == 2


def test_command_explains_a_file_it_has_never_seen(
    repository: Path, database: Path
) -> None:
    result = _run(repository, database, "nowhere/at/all.py")

    assert result.exit_code == 1
    assert "nothing in the stored history touched" in result.stderr


def test_command_warns_when_the_analysis_is_behind(tmp_path: Path) -> None:
    repository = build_lifecycle_repo(tmp_path / "behind")
    database = tmp_path / "behind.db"
    analyze(repository, database)
    add_commit(repository, "A commit made after the analysis")

    result = _run(repository, database, "plain.py")

    assert result.exit_code == 0
    assert "Note: this analysis stops at" in result.stderr
