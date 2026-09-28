"""Tests for ranking files by how often they change."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.history import read_commits
from codearchaeology.hotspots import Hotspot, rank_hotspots
from codearchaeology.lifecycle import Lifecycle, build_lifecycles
from codearchaeology.statistics import summarize
from sample_repo import build_lifecycle_repo

runner = CliRunner()

FIXTURE_FILES = 5


@pytest.fixture(scope="module")
def lives(tmp_path_factory: pytest.TempPathFactory) -> tuple[Lifecycle, ...]:
    repository = build_lifecycle_repo(tmp_path_factory.mktemp("hotspot"))
    return build_lifecycles(read_commits(repository))


@pytest.fixture(scope="module")
def ranking(lives) -> tuple[Hotspot, ...]:
    return rank_hotspots(lives)


@pytest.fixture
def analyzed(sample_repo: Path, tmp_path: Path) -> Path:
    """A database holding the six-commit fixture repository."""
    database = tmp_path / "hotspots.db"
    analyze(sample_repo, database)
    return database


def _run(sample_repo: Path, database: Path, *extra: str):
    return runner.invoke(
        app, ["hotspots", str(sample_repo), "--db", str(database), *extra]
    )


def _by_path(rows: tuple[Hotspot, ...]) -> dict[str, Hotspot]:
    return {row.current_path: row for row in rows}


def test_only_living_files_are_listed_by_default(ranking) -> None:
    assert [row.current_path for row in ranking] == [
        "plain.py",
        "reused.py",
        "app.py",
        "kept.py",
        "renamed.py",
    ]


def test_the_busiest_file_can_be_one_that_is_gone(lives, ranking) -> None:
    """The consequence of leaving deleted files out, pinned down.

    ``src/core/app.py`` was touched by four commits — more than anything else in
    the fixture — and it is still absent from the default list, because the file
    is gone and a hotspot is a place. Asking for the deleted ones brings it back
    at the top.
    """
    assert "src/core/app.py" not in _by_path(ranking)

    everything = rank_hotspots(lives, include_deleted=True)

    assert everything[0].current_path == "src/core/app.py"
    assert everything[0].commits == 4


def test_ranking_is_by_commits_and_then_by_path(ranking) -> None:
    counts = [row.commits for row in ranking]

    assert counts == sorted(counts, reverse=True)
    assert counts == [2, 2, 1, 1, 1]
    # plain.py and reused.py tie on two commits, so the path decides.
    assert ranking[0].current_path == "plain.py"


def test_a_renamed_file_appears_once_with_its_whole_history(ranking) -> None:
    """The reason the ranking is built on identity rather than on names.

    ``kept.py`` was renamed to ``reused.py``, and later a different file took the
    name ``kept.py`` back. Counting by name would give the first file two rows,
    one per name, and neither would carry its real total of two commits. The
    second file keeps its own row, because it is not the same file.
    """
    rows = _by_path(ranking)

    assert rows["reused.py"].path_history == ("kept.py", "reused.py")
    assert rows["reused.py"].commits == 2
    assert rows["kept.py"].path_history == ("kept.py",)
    assert rows["kept.py"].commits == 1


def test_counts_agree_with_the_statistics(lives) -> None:
    for life in lives:
        if not life.is_alive:
            continue

        statistics = summarize(life)
        row = _by_path(rank_hotspots(lives))[life.current_path]

        assert row.commits == statistics.commits
        assert row.additions == statistics.additions
        assert row.deletions == statistics.deletions


def test_churn_adds_both_directions(ranking) -> None:
    plain = _by_path(ranking)["plain.py"]

    assert (plain.additions, plain.deletions) == (8, 1)
    assert plain.churn == 9


def test_the_order_does_not_depend_on_the_input_order(lives) -> None:
    assert rank_hotspots(reversed(lives)) == rank_hotspots(lives)


def test_command_prints_the_ranking(sample_repo: Path, analyzed: Path) -> None:
    result = _run(sample_repo, analyzed)

    assert result.exit_code == 0
    assert "Most Active Files" in result.stdout
    assert "1. core/app.py" in result.stdout
    assert "   3 commits" in result.stdout
    assert "   +19 / -0" in result.stdout


def test_command_says_what_the_count_is_not(
    sample_repo: Path, analyzed: Path
) -> None:
    """The one thing the command must not let a reader assume."""
    result = _run(sample_repo, analyzed)

    assert "Frequent change is not importance" in result.stdout


def test_command_writes_a_single_commit_in_the_singular(
    sample_repo: Path, analyzed: Path
) -> None:
    result = _run(sample_repo, analyzed)

    assert "1 commit\n" in result.stdout
    assert "1 commits" not in result.stdout


def test_command_limit_says_how_many_are_hidden(
    sample_repo: Path, analyzed: Path
) -> None:
    result = _run(sample_repo, analyzed, "--limit", "2")

    assert result.exit_code == 0
    assert "工具/文本.py" not in result.stdout
    assert f"{FIXTURE_FILES - 2} more files. Use --all to see them." in result.stdout


def test_command_all_shows_every_file(sample_repo: Path, analyzed: Path) -> None:
    result = _run(sample_repo, analyzed, "--limit", "2", "--all")

    assert result.exit_code == 0
    assert "工具/文本.py" in result.stdout
    assert "more files" not in result.stdout


def test_command_asks_for_an_analysis_when_there_is_none(
    sample_repo: Path, tmp_path: Path
) -> None:
    result = _run(sample_repo, tmp_path / "absent.db")

    assert result.exit_code == 1
    assert "archaeology analyze" in result.stderr
