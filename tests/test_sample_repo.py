"""Tests for the fixture repository itself.

Every other test in the suite reads this repository, so if these fail the rest
of the results mean nothing.
"""

from pathlib import Path

from sample_repo import build_sample_repo, git_output


def _name_status(repo, *revision):
    output = git_output(repo, "log", "--name-status", "-M", "--format=", *revision)
    return [line.split("\t") for line in output.splitlines() if line.strip()]


def test_history_has_six_commits(sample_repo: Path) -> None:
    assert git_output(sample_repo, "rev-list", "--count", "main").strip() == "6"


def test_both_branches_exist(sample_repo: Path) -> None:
    branches = git_output(sample_repo, "branch", "--format=%(refname:short)").split()

    assert sorted(branches) == ["feature/caching", "main"]


def test_file_move_is_detected_as_a_rename(sample_repo: Path) -> None:
    assert ["R100", "app.py", "core/app.py"] in _name_status(sample_repo, "main")


def test_merge_commit_has_two_parents(sample_repo: Path) -> None:
    merges = git_output(sample_repo, "log", "--merges", "--format=%H", "main").split()
    assert len(merges) == 1

    merge_line = git_output(
        sample_repo, "rev-list", "--parents", "--max-count=1", merges[0]
    ).split()

    assert len(merge_line) == 3, "a merge commit has two parents plus its own hash"


def test_building_twice_gives_an_identical_history(tmp_path: Path) -> None:
    first = build_sample_repo(tmp_path / "first")
    second = build_sample_repo(tmp_path / "second")

    assert git_output(first, "rev-list", "--all") == git_output(
        second, "rev-list", "--all"
    )
