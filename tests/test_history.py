"""Tests for the Git history reader."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from codearchaeology.history import (
    Commit,
    FileChange,
    GitError,
    is_shallow_clone,
    parse_commits,
    read_commits,
)
from sample_repo import build_rename_boundary_repo


@pytest.fixture
def commits(sample_repo: Path) -> list[Commit]:
    return read_commits(sample_repo)


def _find(commits: list[Commit], message: str) -> Commit:
    matches = [commit for commit in commits if commit.message == message]
    assert len(matches) == 1
    return matches[0]


def _change(commit: Commit, path: str) -> FileChange:
    matches = [change for change in commit.changes if change.path == path]
    assert len(matches) == 1
    return matches[0]


def test_reads_the_whole_history(commits: list[Commit]) -> None:
    assert len(commits) == 6
    assert commits[0].message == "Add logo and unicode module, drop legacy helper"


def test_newest_commit_comes_first(commits: list[Commit]) -> None:
    assert commits == sorted(
        commits, key=lambda commit: commit.committed_at, reverse=True
    )


def test_reads_commit_metadata(commits: list[Commit]) -> None:
    initial = commits[-1]

    assert initial.message == "Initial commit"
    assert initial.parents == ()
    assert initial.author_name == "Ada Lovelace"
    assert initial.author_email == "ada@example.com"
    assert initial.authored_at == datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
    assert initial.committed_at == initial.authored_at
    assert len(initial.sha) == 40


def test_merge_commit_lists_both_parents(commits: list[Commit]) -> None:
    merge = _find(commits, "Merge branch 'feature/caching'")

    assert merge.is_merge
    assert len(merge.parents) == 2
    assert set(merge.parents) <= {commit.sha for commit in commits}


def test_merge_commit_reports_no_file_changes(commits: list[Commit]) -> None:
    merge = _find(commits, "Merge branch 'feature/caching'")

    assert merge.changes == ()


def test_rename_keeps_both_paths(commits: list[Commit]) -> None:
    rename = _find(commits, "Move app module into the core package")

    (change,) = rename.changes
    assert change.change_type == "R"
    assert change.old_path == "app.py"
    assert change.path == "core/app.py"
    assert (change.added_lines, change.deleted_lines) == (0, 0)
    assert not change.is_binary


def test_binary_file_has_no_line_counts(commits: list[Commit]) -> None:
    change = _change(commits[0], "assets/logo.png")

    assert change.change_type == "A"
    assert change.is_binary
    assert change.added_lines is None
    assert change.deleted_lines is None


def test_rename_records_gits_similarity_score(commits: list[Commit]) -> None:
    """A rename that changed nothing scores 100, which is git's upper bound."""
    rename = _find(commits, "Move app module into the core package")

    (change,) = rename.changes
    assert change.similarity == 100


def test_a_rename_that_rewrote_lines_scores_lower(tmp_path: Path) -> None:
    repository = build_rename_boundary_repo(tmp_path / "boundary")

    # Newest first, so the commit that renames is the one on the left.
    renaming, _ = read_commits(repository)
    scored = {
        change.path: change.similarity
        for change in renaming.changes
        if change.change_type == "R"
    }

    assert scored == {"under_after.py": 56}


def test_only_renames_carry_a_similarity_score(commits: list[Commit]) -> None:
    """Git scores renames and copies. Every other change type leaves it empty."""
    scored = {
        change.change_type
        for commit in commits
        for change in commit.changes
        if change.similarity is not None
    }

    assert scored == {"R"}


def test_deletion_is_recorded(commits: list[Commit]) -> None:
    change = _change(commits[0], "legacy.py")

    assert change.change_type == "D"
    assert (change.added_lines, change.deleted_lines) == (0, 5)


def test_non_ascii_path_survives(commits: list[Commit]) -> None:
    change = _change(commits[0], "工具/文本.py")

    assert change.change_type == "A"
    assert change.added_lines == 5


def test_directory_that_is_not_a_repository_raises(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="not a git repository"):
        read_commits(tmp_path)


def test_a_full_repository_is_not_shallow(sample_repo: Path) -> None:
    assert not is_shallow_clone(sample_repo)


def test_a_shallow_clone_is_detected(shallow_clone: Path) -> None:
    assert is_shallow_clone(shallow_clone)


def test_a_shallow_clone_invents_history_it_does_not_have(
    shallow_clone: Path, sample_repo: Path
) -> None:
    """Why the warning exists, in one assertion.

    The oldest commit a shallow clone holds is treated as the root, so the merge
    commit there has no parents at all and reports four files as brand new. In
    the full repository the very same commit is a merge with two parents and no
    file changes, because git prints no diff for a merge. The history is not just
    shorter, its shape is different.
    """
    shallow = read_commits(shallow_clone)[-1]
    full = _find(read_commits(sample_repo), "Merge branch 'feature/caching'")

    assert shallow.sha == full.sha
    assert full.is_merge
    assert full.changes == ()
    assert shallow.parents == ()
    assert len(shallow.changes) == 4


def test_parser_rejects_unknown_output() -> None:
    header = "\x1f".join(
        ["a" * 40, "", "Ada", "ada@example.com", "2024-01-01T00:00:00Z",
         "2024-01-01T00:00:00Z", "message"]
    )

    with pytest.raises(ValueError, match="unexpected git output"):
        parse_commits(f"\x1e{header}\x00nonsense\x00".encode())
