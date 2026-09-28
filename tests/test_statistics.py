"""Tests for the numbers that summarise a file's life."""

import subprocess
from pathlib import Path

import pytest

from codearchaeology.history import read_commits
from codearchaeology.lifecycle import Lifecycle, build_lifecycles
from codearchaeology.statistics import FileStatistics, summarize
from sample_repo import (
    build_edit_history_repo,
    build_lifecycle_repo,
    build_rename_boundary_repo,
    build_sample_repo,
)


@pytest.fixture(scope="module")
def lifecycle_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_lifecycle_repo(tmp_path_factory.mktemp("stats-lifecycle"))


@pytest.fixture(scope="module")
def boundary_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_rename_boundary_repo(tmp_path_factory.mktemp("stats-boundary"))


@pytest.fixture(scope="module")
def edit_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_edit_history_repo(tmp_path_factory.mktemp("stats-edits"))


@pytest.fixture(scope="module")
def lives(lifecycle_repo: Path) -> tuple[Lifecycle, ...]:
    return build_lifecycles(read_commits(lifecycle_repo))


@pytest.fixture(scope="module")
def boundary_lives(boundary_repo: Path) -> tuple[Lifecycle, ...]:
    return build_lifecycles(read_commits(boundary_repo))


@pytest.fixture(scope="module")
def edit_lives(edit_repo: Path) -> tuple[Lifecycle, ...]:
    return build_lifecycles(read_commits(edit_repo))


@pytest.fixture(scope="module")
def sample_lives(tmp_path_factory: pytest.TempPathFactory) -> tuple[Lifecycle, ...]:
    repository = build_sample_repo(tmp_path_factory.mktemp("stats-sample"))
    return build_lifecycles(read_commits(repository))


def _lives_of(lives: tuple[Lifecycle, ...], path: str) -> list[Lifecycle]:
    return [life for life in lives if path in life.path_history]


def _stats(lives: tuple[Lifecycle, ...], path: str, index: int = 0) -> FileStatistics:
    """The statistics of the *index*-th life that ever carried *path*."""
    return summarize(_lives_of(lives, path)[index])


def _lines_at(repository: Path, sha: str, path: str) -> int | None:
    """How many lines the file really had at *sha*, read back out of git.

    ``None`` when the file is not in that commit at all. This is deliberately
    not the same arithmetic the statistics use: it asks git for the content, so
    the two can disagree.
    """
    completed = subprocess.run(
        ["git", "show", f"{sha}:{path}"], cwd=repository, capture_output=True
    )
    if completed.returncode != 0:
        return None
    return len(completed.stdout.decode("utf-8").splitlines())


def test_a_file_that_is_created_and_then_edited(lives) -> None:
    plain = _stats(lives, "plain.py")

    assert plain.commits == 2
    assert plain.modifications == 1
    assert plain.renames == 0
    assert (plain.additions, plain.deletions) == (8, 1)
    assert plain.net_change == 7
    assert not plain.is_deleted
    assert plain.binary_changes == 0


def test_a_file_that_is_created_and_never_touched(lives) -> None:
    """``kept.py`` is renamed but never edited, and a rename is not an edit.

    Its last-modified time is empty rather than equal to its birth, because
    nothing ever modified it.
    """
    kept = _stats(lives, "kept.py")

    assert kept.commits == 2
    assert kept.modifications == 0
    assert kept.renames == 1
    assert kept.last_modified_at is None
    assert kept.last_modified_sha is None
    assert kept.created_at == _lives_of(lives, "kept.py")[0].created.committed_at


def test_a_rename_that_rewrote_lines_counts_as_a_modification(boundary_lives) -> None:
    """A pure rename leaves the content alone; this one rewrote forty lines."""
    under = _stats(boundary_lives, "under.py")

    assert under.renames == 1
    assert under.modifications == 1
    assert (under.additions, under.deletions) == (140, 40)
    assert under.net_change == 100
    assert not under.is_deleted


def test_a_pure_rename_is_not_a_modification(lives) -> None:
    """Two renames in a row, no line ever rewritten, so no modification."""
    first_app = _stats(lives, "src/core/app.py")

    assert first_app.commits == 4
    assert first_app.renames == 2
    assert first_app.modifications == 0
    assert first_app.last_modified_at is None
    assert first_app.is_deleted


def test_a_deleted_file_is_marked_deleted(lives) -> None:
    first_app = _stats(lives, "src/core/app.py")

    assert first_app.is_deleted
    assert (first_app.additions, first_app.deletions) == (1, 1)
    assert first_app.net_change == 0


def test_a_file_split_by_the_threshold_is_counted_as_two(boundary_lives) -> None:
    over = _stats(boundary_lives, "over.py")
    over_after = _stats(boundary_lives, "over_after.py")

    assert over.is_deleted
    assert (over.additions, over.deletions) == (100, 100)
    assert over.net_change == 0

    assert not over_after.is_deleted
    assert (over_after.additions, over_after.deletions) == (100, 0)
    assert over_after.net_change == 100


def test_the_threshold_changes_the_gross_numbers_but_not_the_net(
    boundary_lives,
) -> None:
    """The same edit, counted two ways, because git decided differently.

    ``under.py`` was renamed with forty lines rewritten and git recognised it:
    one file, ``+140/-40``. ``over.py`` was renamed with fifty rewritten and git
    did not: the old name dies at ``+100/-100`` and a new file is born at
    ``+100/-0``. Both accounts gain a hundred lines in the end, but the additions
    and deletions differ by nearly half. Nothing here is a bug; it is the price
    of reporting what git said instead of guessing.
    """
    under = _stats(boundary_lives, "under.py")
    over = _stats(boundary_lives, "over.py")
    over_after = _stats(boundary_lives, "over_after.py")

    assert under.net_change == 100
    assert over.net_change + over_after.net_change == 100

    assert under.additions + under.deletions == 180
    assert over.additions + over.deletions + over_after.additions == 300


def test_a_binary_file_reports_no_lines_but_is_counted(sample_lives) -> None:
    """Git gives no line counts for a binary file, so a zero here must not pass
    for "nothing happened": the change is counted separately."""
    logo = _stats(sample_lives, "assets/logo.png")

    assert (logo.additions, logo.deletions) == (0, 0)
    assert logo.binary_changes == 1
    assert logo.commits == 1


def test_net_change_matches_the_file_git_actually_has(
    lifecycle_repo: Path,
    lives,
    boundary_repo: Path,
    boundary_lives,
    edit_repo: Path,
    edit_lives,
) -> None:
    """The arithmetic is checked against the content, not against itself.

    Every line the file gained was counted and every line it lost was subtracted,
    so the net has to come out as its size at the last event: the file git holds
    at that commit while it lives, and nothing at all once it is gone.
    """
    groups = (
        (lifecycle_repo, lives),
        (boundary_repo, boundary_lives),
        (edit_repo, edit_lives),
    )
    for repository, group in groups:
        for life in group:
            if any(event.is_binary for event in life.events):
                continue

            statistics = summarize(life)
            last = life.events[-1]
            size = _lines_at(repository, last.commit_sha, last.path)

            if statistics.is_deleted:
                assert size is None, life.path_history
                assert statistics.net_change == 0, life.path_history
            else:
                assert size == statistics.net_change, life.path_history


def test_last_modified_names_the_commit_that_changed_it(lives) -> None:
    plain = _lives_of(lives, "plain.py")[0]
    statistics = summarize(plain)
    changed = [event for event in plain.events if event.change_type == "M"]

    assert statistics.last_modified_sha == changed[-1].commit_sha
    assert statistics.last_modified_at == changed[-1].committed_at
    assert statistics.created_sha == plain.created.commit_sha


def test_a_file_edited_three_times_counts_three_modifications(edit_lives) -> None:
    edited = _stats(edit_lives, "edited.py")

    assert edited.commits == 4
    assert edited.modifications == 3
    assert edited.renames == 0
    assert (edited.additions, edited.deletions) == (11, 2)
    assert edited.net_change == 9
    assert not edited.is_deleted


def test_last_modified_names_the_last_edit_not_the_first(edit_lives) -> None:
    """What the edit fixture exists for.

    Every other fixture modifies a file at most once, so its first modification
    and its last are the same event, and a last-modified time taken from the
    wrong end of the list passes on all of them. This is the one file that can
    tell the two apart.
    """
    edited = _lives_of(edit_lives, "edited.py")[0]
    modifications = [event for event in edited.events if event.change_type == "M"]
    statistics = summarize(edited)

    assert len(modifications) == 3
    assert statistics.last_modified_sha == modifications[-1].commit_sha
    assert statistics.last_modified_sha != modifications[0].commit_sha
    assert statistics.last_modified_at == modifications[-1].committed_at


def test_an_empty_file_is_not_a_binary_file(edit_lives) -> None:
    """The two zeroes are different facts.

    Git reports ``0 0`` for a file with no lines and ``- -`` for one it cannot
    count. Both end up as zero on the row, so the binary count is what keeps
    them apart: an empty file's zeroes mean the content really is empty.
    """
    untouched = _stats(edit_lives, "untouched.py")
    life = _lives_of(edit_lives, "untouched.py")[0]

    assert untouched.commits == 1
    assert (untouched.additions, untouched.deletions) == (0, 0)
    assert untouched.binary_changes == 0
    assert not any(event.is_binary for event in life.events)


def test_a_file_that_was_empty_and_then_filled(edit_lives) -> None:
    empty = _stats(edit_lives, "empty.py")

    assert empty.commits == 2
    assert empty.modifications == 1
    assert (empty.additions, empty.deletions) == (3, 0)
    assert empty.net_change == 3
    assert empty.last_modified_sha is not None
    assert not empty.is_deleted


def test_a_file_with_no_lines_and_no_edits_has_no_last_modified_time(
    edit_lives,
) -> None:
    """Nothing modified it, so there is no such time — the same rule that gives
    a renamed-but-untouched file a dash. Its birth does not stand in for it."""
    untouched = _stats(edit_lives, "untouched.py")

    assert untouched.modifications == 0
    assert untouched.last_modified_at is None
    assert untouched.last_modified_sha is None
    assert untouched.net_change == 0
    assert untouched.created_at == _lives_of(edit_lives, "untouched.py")[0].created.committed_at
