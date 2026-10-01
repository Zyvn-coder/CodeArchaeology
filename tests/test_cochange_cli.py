"""Tests for the co-change command and its two output shapes."""

import json
from pathlib import Path
import subprocess

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.cli import app
from codearchaeology.cochange import analyze_cochange, build_json, build_object
from codearchaeology.history import read_commits
from sample_repo import (
    add_commit,
    build_cochange_repo,
    build_lifecycle_repo,
    build_sample_repo,
)

runner = CliRunner()


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The hand-computable fixture: a and b twice, then a and c."""
    return build_cochange_repo(tmp_path_factory.mktemp("cochange-cli"))


@pytest.fixture(scope="module")
def database(repository: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("cochange-cli-db") / "history.db"
    analyze(repository, path)
    return path


@pytest.fixture(scope="module")
def sample_repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The sample fixture: a three-file commit, a rename, a deletion, a merge."""
    return build_sample_repo(tmp_path_factory.mktemp("cochange-cli-sample"))


@pytest.fixture(scope="module")
def sample_database(
    sample_repository: Path, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    path = tmp_path_factory.mktemp("cochange-cli-sample-db") / "history.db"
    analyze(sample_repository, path)
    return path


def _run(repository: Path, database: Path, *arguments: str):
    return runner.invoke(
        app, ["cochange", *arguments, str(repository), "--db", str(database)]
    )


# --- the text block ---------------------------------------------------------


def test_the_block_names_the_file_and_its_sample(repository, database) -> None:
    result = _run(repository, database, "a.py")

    assert result.exit_code == 0
    assert result.stdout.startswith("a.py\n")
    assert "Analyzed: 3 commits of this file" in result.stdout


def test_the_block_shows_the_partner_count_and_score(repository, database) -> None:
    result = _run(repository, database, "a.py")

    assert "b.py" in result.stdout
    assert "0.667" in result.stdout


def test_the_block_says_what_the_score_is_not(repository, database) -> None:
    """The number needs a sentence, the way the hotspot ranking has one."""
    result = _run(repository, database, "a.py")

    assert "not a dependency" in result.stdout


def test_the_block_names_the_hidden_pairs(repository, database) -> None:
    """A filter that says nothing is a filter the reader cannot see."""
    result = _run(repository, database, "a.py")

    assert "1 pairs hidden" in result.stdout


def test_a_lower_threshold_shows_the_pair_and_drops_the_note(
    repository, database
) -> None:
    result = _run(repository, database, "a.py", "--min-shared", "1")

    assert "c.py" in result.stdout
    assert "0.333" in result.stdout
    assert "pairs hidden" not in result.stdout


def test_a_file_with_no_partner_says_so_without_failing(
    repository, database
) -> None:
    """No relationship is a result, not an error."""
    result = _run(repository, database, "c.py")

    assert result.exit_code == 0
    assert "No file changed alongside it." in result.stdout


def test_a_renamed_file_shows_its_earlier_names(
    sample_repository, sample_database
) -> None:
    result = _run(sample_repository, sample_database, "core/app.py")

    assert "History:  app.py -> core/app.py" in result.stdout


def test_the_limit_hides_rows_and_says_how_many(
    sample_repository, sample_database
) -> None:
    result = _run(
        sample_repository, sample_database, "legacy.py", "--min-shared", "1",
        "--limit", "2",
    )

    assert result.stdout.count("0.500") == 2
    assert "2 more files. Use --all to see them." in result.stdout


def test_all_shows_every_row(sample_repository, sample_database) -> None:
    result = _run(
        sample_repository, sample_database, "legacy.py", "--min-shared", "1",
        "--all",
    )

    assert result.stdout.count("0.500") == 4
    assert "more files" not in result.stdout


def test_a_reused_name_is_printed_once_per_file(
    tmp_path_factory: pytest.TempPathFactory
) -> None:
    """app.py was one file, was deleted, and a second file took the name."""
    repository = build_lifecycle_repo(tmp_path_factory.mktemp("cochange-reused"))
    database = tmp_path_factory.mktemp("cochange-reused-db") / "history.db"
    analyze(repository, database)

    result = _run(repository, database, "app.py")

    assert result.exit_code == 0
    assert result.stdout.count("Analyzed:") == 2
    assert "Analyzed: 4 commits of this file" in result.stdout
    assert "Analyzed: 1 commits of this file" in result.stdout


# --- the JSON ---------------------------------------------------------------


def test_json_is_the_only_thing_on_stdout(repository, database) -> None:
    result = _run(repository, database, "a.py", "--json")

    assert result.exit_code == 0
    reported = json.loads(result.stdout)

    assert reported["path"] == "a.py"
    assert reported["repository"] == str(repository.resolve())
    assert reported["head_sha"]


def test_json_carries_the_denominator_and_the_hidden_count(
    repository, database
) -> None:
    """A reader must be able to verify a score by division."""
    result = _run(repository, database, "a.py", "--json")
    entry = json.loads(result.stdout)["files"][0]

    assert entry["analyzed_commits"] == 3
    assert entry["large_commits_excluded"] == 0
    assert entry["hidden_pairs"] == 1

    shown = entry["co_changes"][0]
    assert shown["path"] == "b.py"
    assert shown["shared_commits"] == 2
    assert shown["score"] == 2 / 3


def test_json_keeps_the_frozen_order(repository, database) -> None:
    result = _run(repository, database, "a.py", "--min-shared", "1", "--json")
    entry = json.loads(result.stdout)["files"][0]

    assert [row["path"] for row in entry["co_changes"]] == ["b.py", "c.py"]
    assert [row["shared_commits"] for row in entry["co_changes"]] == [2, 1]


def test_json_and_the_table_show_the_same_rows(repository, database) -> None:
    text = _run(repository, database, "a.py", "--min-shared", "1")
    reported = json.loads(
        _run(repository, database, "a.py", "--min-shared", "1", "--json").stdout
    )
    entry = reported["files"][0]

    for row in entry["co_changes"]:
        assert row["path"] in text.stdout


def test_json_holds_a_list_for_one_file_too(repository, database) -> None:
    """The shape must not depend on which repository was asked."""
    reported = json.loads(_run(repository, database, "a.py", "--json").stdout)

    assert isinstance(reported["files"], list)
    assert len(reported["files"]) == 1


def test_json_lists_both_files_when_a_name_was_reused(
    tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Both lives that carried the queried name, oldest first.

    Each entry's ``path`` is what that life is called *now*, which is the rule
    the single-file view already follows: the first life was renamed away to
    ``src/core/app.py``, and only the second still answers to ``app.py``.
    """
    repository = build_lifecycle_repo(tmp_path_factory.mktemp("cochange-json-reused"))
    database = tmp_path_factory.mktemp("cochange-json-db") / "history.db"
    analyze(repository, database)

    result = _run(repository, database, "app.py", "--json")
    reported = json.loads(result.stdout)

    assert reported["path"] == "app.py"
    assert [entry["path"] for entry in reported["files"]] == [
        "src/core/app.py",
        "app.py",
    ]
    assert [entry["analyzed_commits"] for entry in reported["files"]] == [4, 1]


def test_json_keeps_the_stale_note_off_stdout(
    tmp_path: Path,
) -> None:
    """The warning goes to stderr, so a program reading stdout never sees it."""
    repository = build_cochange_repo(tmp_path / "json-behind")
    database = tmp_path / "json-behind.db"
    analyze(repository, database)
    add_commit(repository, "A commit made after the analysis")

    result = _run(repository, database, "a.py", "--json")

    assert "Note: this analysis stops at" in result.stderr
    assert json.loads(result.stdout)["files"][0]["path"] == "a.py"


def test_json_keeps_the_closing_note_off_stdout(repository, database) -> None:
    """The sentence about what the score is not is prose for a reader."""
    result = _run(repository, database, "a.py", "--json")

    assert "not a dependency" not in result.stdout


# --- errors -----------------------------------------------------------------


def test_a_file_the_history_never_carried_is_an_error(repository, database) -> None:
    result = _run(repository, database, "nowhere/at/all.py")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "nothing in the stored history touched" in result.stderr


def test_the_error_points_at_the_command_that_lists_files(
    repository, database
) -> None:
    result = _run(repository, database, "nowhere.py", "--json")

    assert result.exit_code == 1
    assert "use 'archaeology files' to see what is there" in result.stderr


def test_a_directory_that_is_not_a_repository_is_an_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["cochange", "a.py", str(tmp_path), "--db", str(tmp_path / "no.db")]
    )

    assert result.exit_code == 1
    assert "not a git repository" in result.stderr


def test_a_missing_directory_is_refused_before_anything_else() -> None:
    """The repository argument is checked by the command line, not by the code."""
    result = runner.invoke(app, ["cochange", "a.py", "D:/nowhere/at/all"])

    assert result.exit_code == 2
    assert "does not exist" in result.stderr


def test_a_threshold_below_one_is_refused(repository, database) -> None:
    result = _run(repository, database, "a.py", "--min-shared", "0")

    assert result.exit_code == 2


def test_a_large_commit_limit_below_one_is_refused(repository, database) -> None:
    result = _run(repository, database, "a.py", "--large-commit-limit", "0")

    assert result.exit_code == 2


def test_an_empty_history_answers_with_an_error_not_a_crash(
    tmp_path: Path,
) -> None:
    """A repository with no commits has nothing to say, and says so."""
    repository = tmp_path / "empty"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch", "main", "-q", str(repository)],
        check=True,
    )

    result = runner.invoke(
        app, ["cochange", "a.py", str(repository), "--db", str(tmp_path / "empty.db")]
    )

    assert result.exit_code == 1
    assert "Error:" in result.stderr


# --- the contract between the fixture and the command -----------------------


def test_the_hand_computed_numbers_survive_to_the_command(
    repository, database
) -> None:
    """The long-term statistical contract: one fixture, one set of numbers.

    ``build_cochange_repo``'s docstring works them out by hand — a's three
    commits, b shared 2 of 3, c shared 1 — and this asserts the command still
    prints them. If a change ever moves one of these numbers, either the
    statistic changed or the implementation did, and neither may happen
    silently.
    """
    a = json.loads(
        _run(repository, database, "a.py", "--min-shared", "1", "--json").stdout
    )["files"][0]
    b = json.loads(_run(repository, database, "b.py", "--json").stdout)["files"][0]
    c = json.loads(
        _run(repository, database, "c.py", "--min-shared", "1", "--json").stdout
    )["files"][0]

    assert a["analyzed_commits"] == 3
    assert [(row["path"], row["shared_commits"], row["score"]) for row in a["co_changes"]] == [
        ("b.py", 2, 2 / 3),
        ("c.py", 1, 1 / 3),
    ]

    assert b["analyzed_commits"] == 2
    assert [(row["path"], row["shared_commits"], row["score"]) for row in b["co_changes"]] == [
        ("a.py", 2, 1.0)
    ]

    assert c["analyzed_commits"] == 1
    assert [(row["path"], row["shared_commits"], row["score"]) for row in c["co_changes"]] == [
        ("a.py", 1, 1.0)
    ]


def test_the_design_document_names_the_statistic_the_code_implements() -> None:
    """The five things that have to agree: definition, code, command, tests, doc.

    The document is where the definition lives, so the check is that it is still
    the same definition the module and the command are built from: the file
    exists, and the words the code uses for its two knobs are the words it uses.
    """
    document = Path(__file__).parent.parent / "docs" / "v0.4-cochange-design.md"
    text = document.read_text(encoding="utf-8")

    assert "co_change_score(A → B) = shared(A,B) / |analyzed(A)|" in text
    assert "--min-shared" in text
    assert "--large-commit-limit" in text
    assert "archaeology cochange <file>" in text

    from codearchaeology import cochange

    assert cochange.DEFAULT_MIN_SHARED == 2
    assert cochange.DEFAULT_LARGE_COMMIT_LIMIT == 100


# --- the renderers, without a database --------------------------------------


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory):
    """One analyzed file, for the two tests that only exercise the renderers."""
    built = build_cochange_repo(tmp_path_factory.mktemp("cochange-render"))
    return analyze_cochange(read_commits(built), "a.py")[0]


def test_the_object_carries_every_number_the_block_shows(report) -> None:
    """JSON is not a summary: it holds what the terminal was told and more."""
    reported = build_object(report)

    assert reported["path"] == "a.py"
    assert reported["analyzed_commits"] == report.analyzed_commits
    assert reported["large_commits_excluded"] == report.excluded_large_commits
    assert reported["hidden_pairs"] == report.hidden_pairs
    assert len(reported["co_changes"]) == len(report.co_changes)


def test_the_json_object_names_the_queried_path_and_the_repository(report) -> None:
    reported = json.loads(build_json([report], "a.py", "D:/somewhere", "abc123"))

    assert reported["path"] == "a.py"
    assert reported["repository"] == str(Path("D:/somewhere").resolve())
    assert reported["head_sha"] == "abc123"
