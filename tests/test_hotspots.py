"""Tests for ranking files by how often they change."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.history import read_commits
from codearchaeology.hotspots import Hotspot, build_inventory, build_json, rank_hotspots
from codearchaeology.lifecycle import Lifecycle, build_lifecycles
from codearchaeology.statistics import summarize
from sample_repo import add_commit, build_lifecycle_repo, build_sample_repo

runner = CliRunner()

FIXTURE_FILES = 5

# The sample fixture's six files, in the order the inventory prints them: by
# path. ``legacy.py`` is deleted, which is why the inventory has one more row
# than the ranking, whose five living files are a subset of these.
_FIXTURE_PATHS = (
    "README.md",
    "assets/logo.png",
    "core/app.py",
    "core/cache.py",
    "legacy.py",
    "工具/文本.py",
)


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


def _run_files(sample_repo: Path, database: Path, *extra: str):
    return runner.invoke(
        app, ["files", str(sample_repo), "--db", str(database), *extra]
    )


def test_files_command_prints_a_table(sample_repo: Path, analyzed: Path) -> None:
    result = _run_files(sample_repo, analyzed)

    assert result.exit_code == 0
    assert "FILE" in result.stdout
    assert "STATE" in result.stdout
    assert "COMMITS" in result.stdout
    assert "+LINES" in result.stdout
    assert "-LINES" in result.stdout
    assert "core/app.py" in result.stdout
    assert "19" in result.stdout


def test_files_command_lists_the_deleted_file_with_its_state(
    sample_repo: Path, analyzed: Path
) -> None:
    """The inventory's whole point: the file is gone and still listed."""
    result = _run_files(sample_repo, analyzed)

    assert result.exit_code == 0
    assert "legacy.py" in result.stdout
    assert "deleted" in result.stdout
    assert "alive" in result.stdout


def test_files_command_says_what_a_deleted_row_means(
    sample_repo: Path, analyzed: Path
) -> None:
    """A list of dead files invites one misreading, so it is answered."""
    result = _run_files(sample_repo, analyzed)

    assert "not what the working tree contains" in result.stdout


def test_files_command_orders_by_path(sample_repo: Path, analyzed: Path) -> None:
    """The inventory is a listing, so the order is the path's, not the count's."""
    result = _run_files(sample_repo, analyzed, "--all")

    order = [
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.strip() and line.split()[0] in _FIXTURE_PATHS
    ]
    assert order == sorted(_FIXTURE_PATHS)


def test_files_command_limits_and_says_how_many_are_hidden(
    sample_repo: Path, analyzed: Path
) -> None:
    result = _run_files(sample_repo, analyzed, "--limit", "2")

    assert result.exit_code == 0
    assert "工具/文本.py" not in result.stdout
    assert f"{FIXTURE_FILES + 1 - 2} more files. Use --all to see them." in result.stdout


def test_files_command_all_shows_every_file(sample_repo: Path, analyzed: Path) -> None:
    result = _run_files(sample_repo, analyzed, "--limit", "2", "--all")

    assert result.exit_code == 0
    assert "工具/文本.py" in result.stdout
    assert "more files" not in result.stdout


def test_files_command_warns_when_the_analysis_is_behind(tmp_path: Path) -> None:
    repository = build_sample_repo(tmp_path / "behind")
    database = tmp_path / "behind.db"
    analyze(repository, database)
    add_commit(repository, "A commit made after the analysis")

    result = _run_files(repository, database)

    assert result.exit_code == 0
    assert "Note: this analysis stops at" in result.stderr


def test_the_inventory_holds_every_file_the_history_contained(lives) -> None:
    """Nothing is filtered: the deleted file is as much a fact as the living."""
    entries = build_inventory(lives)

    assert len(entries) == len(lives)
    assert {entry.state for entry in entries} == {"alive", "deleted"}
    assert "heavy.py" in [entry.current_path for entry in entries]


def test_the_inventory_is_ordered_by_path_then_birth(lives) -> None:
    entries = build_inventory(lives)

    keys = [(entry.current_path, entry.born) for entry in entries]
    assert keys == sorted(keys)


def test_the_inventory_agrees_with_the_ranking_where_they_overlap(lives) -> None:
    """The two commands print the same numbers for the same file."""
    ranking = _by_path(rank_hotspots(lives))
    inventory = {entry.current_path: entry for entry in build_inventory(lives)}

    assert ranking.keys() <= inventory.keys()
    for path, row in ranking.items():
        entry = inventory[path]
        assert (entry.commits, entry.additions, entry.deletions) == (
            row.commits,
            row.additions,
            row.deletions,
        )


def test_json_reports_the_ranking_in_order(ranking) -> None:
    reported = json.loads(build_json(ranking, "/tmp/wherever", "abc123"))

    assert [entry["path"] for entry in reported["files"]] == [
        row.current_path for row in ranking
    ]
    assert reported["head_sha"] == "abc123"
    assert reported["files"][0]["commits"] == ranking[0].commits


def test_json_row_carries_the_names_the_count_covers(ranking) -> None:
    """A row is counted by identity, so the path alone does not say which names
    the count is over. Without the chain, two rows cannot be told from one file
    that was renamed."""
    reused = _by_path(ranking)["reused.py"]

    reported = json.loads(build_json([reused], "/tmp/wherever", "abc123"))

    assert reported["files"][0]["path_history"] == ["kept.py", "reused.py"]


def test_the_two_json_shapes_differ(sample_repo: Path, analyzed: Path) -> None:
    """They answer different questions, so they must not share a shape.

    The ranking cannot contain a deleted file and carries no state; the
    inventory must do both. Identical bytes would mean the schema was hiding a
    difference the command line shows.
    """
    from_files = _run_files(sample_repo, analyzed, "--json")
    from_hotspots = _run(sample_repo, analyzed, "--json")

    assert from_files.exit_code == 0
    assert from_hotspots.exit_code == 0
    assert from_files.stdout != from_hotspots.stdout

    inventory = json.loads(from_files.stdout)["files"]
    ranking = json.loads(from_hotspots.stdout)["files"]

    assert "state" in inventory[0]
    assert "state" not in ranking[0]


def test_the_ranking_json_is_unchanged_by_the_split(ranking) -> None:
    """`hotspots --json` keeps the bytes it had before `files` moved.

    Its consumers are the ones that must not have to change, so the ranking's
    object is still exactly these fields.
    """
    reported = json.loads(build_json(ranking, "/tmp/wherever", "abc123"))

    assert set(reported) == {"repository", "head_sha", "files"}
    assert set(reported["files"][0]) == {
        "path",
        "commits",
        "additions",
        "deletions",
        "path_history",
    }


def test_inventory_json_carries_the_state_and_the_names(
    sample_repo: Path, analyzed: Path
) -> None:
    result = _run_files(sample_repo, analyzed, "--json")
    reported = json.loads(result.stdout)

    by_path = {entry["path"]: entry for entry in reported["files"]}

    assert by_path["legacy.py"]["state"] == "deleted"
    assert by_path["legacy.py"]["commits"] == 2
    assert by_path["README.md"]["state"] == "alive"
    assert by_path["core/app.py"]["path_history"] == ["app.py", "core/app.py"]


def test_inventory_json_is_complete_where_the_table_is_sliced(
    sample_repo: Path, analyzed: Path
) -> None:
    """`--limit` is a terminal convenience; a program asked for the inventory."""
    result = _run_files(sample_repo, analyzed, "--limit", "2", "--json")
    reported = json.loads(result.stdout)

    assert len(reported["files"]) == FIXTURE_FILES + 1
    assert [entry["path"] for entry in reported["files"]] == sorted(_FIXTURE_PATHS)


def test_files_json_leaves_out_the_sentence_the_table_prints(
    sample_repo: Path, analyzed: Path
) -> None:
    """The note about deleted rows is prose for a reader, and the state field
    already says the same fact to a program."""
    result = _run_files(sample_repo, analyzed, "--json")

    assert "Frequent change" not in result.stdout
    assert "not what the working tree contains" not in result.stdout
    assert json.loads(result.stdout)


def test_files_json_stays_parseable_when_the_analysis_is_behind(
    tmp_path: Path,
) -> None:
    repository = build_sample_repo(tmp_path / "json-behind")
    database = tmp_path / "json-behind.db"
    analyze(repository, database)
    add_commit(repository, "A commit made after the analysis")

    result = _run_files(repository, database, "--json")

    assert "Note: this analysis stops at" in result.stderr
    assert json.loads(result.stdout)["files"]
