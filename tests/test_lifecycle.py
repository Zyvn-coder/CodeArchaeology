"""Tests for rebuilding each file's life from the stored history."""

from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.history import Commit, FileChange, read_commits
from codearchaeology.lifecycle import Lifecycle, build_lifecycles, load_lifecycles
from sample_repo import (
    build_edit_history_repo,
    build_lifecycle_repo,
    build_rename_boundary_repo,
    build_same_second_repo,
)

FIXTURE_COMMITS = 8
FIXTURE_LIVES = 7


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture repository: eight commits, described in ``sample_repo``."""
    return build_lifecycle_repo(tmp_path_factory.mktemp("lifecycle-repo"))


@pytest.fixture(scope="module")
def lives(repository: Path) -> tuple[Lifecycle, ...]:
    return build_lifecycles(read_commits(repository))


@pytest.fixture(scope="module")
def boundary_lives(tmp_path_factory: pytest.TempPathFactory) -> tuple[Lifecycle, ...]:
    """Lives for the three files that sit either side of the rename threshold."""
    boundary = build_rename_boundary_repo(tmp_path_factory.mktemp("boundary-repo"))
    return build_lifecycles(read_commits(boundary))


def _lives_of(lives: tuple[Lifecycle, ...], path: str) -> list[Lifecycle]:
    """Every life that ever carried *path*, oldest first.

    A path can belong to more than one life over a repository's history, which
    is the whole point of the delete-and-recreate rule.
    """
    return [life for life in lives if path in life.path_history]


def _synthetic(number: int, *changes: FileChange) -> Commit:
    """One hand-made commit, for histories no fixture repository can produce."""
    moment = datetime(2024, 6, number, tzinfo=timezone.utc)
    return Commit(
        sha=f"{number:040d}",
        parents=(),
        author_name="Synthetic Author",
        author_email="synthetic@example.com",
        authored_at=moment,
        committed_at=moment,
        message=f"Commit {number}",
        changes=tuple(changes),
    )


def test_the_fixture_produces_the_expected_number_of_lives(lives) -> None:
    assert len(lives) == FIXTURE_LIVES


def test_lives_come_back_oldest_first(lives) -> None:
    births = [life.created.committed_at for life in lives]

    assert births == sorted(births)


def test_a_file_that_is_only_edited(lives) -> None:
    plain = _lives_of(lives, "plain.py")[0]

    assert plain.created.change_type == "A"
    assert plain.path_history == ("plain.py",)
    assert [event.change_type for event in plain.events] == ["A", "M"]
    assert plain.renames == 0
    assert plain.is_alive


def test_a_rename_does_not_split_the_life(lives) -> None:
    """One rename is one event: no death and no birth may be invented."""
    moved = _lives_of(lives, "src/app.py")
    further = _lives_of(lives, "src/core/app.py")

    assert len(moved) == 1
    assert len(further) == 1
    assert moved[0] is further[0]
    assert moved[0].renames == 2


def test_path_history_lists_every_name_in_order(lives) -> None:
    first_app = _lives_of(lives, "src/core/app.py")[0]

    assert first_app.path_history == ("app.py", "src/app.py", "src/core/app.py")


def test_a_deleted_file_is_closed(lives) -> None:
    first_app = _lives_of(lives, "src/core/app.py")[0]

    assert not first_app.is_alive
    assert first_app.deleted is not None
    assert first_app.deleted.change_type == "D"
    assert first_app.current_path == "src/core/app.py"


def test_a_name_reused_after_a_deletion_is_a_second_life(lives) -> None:
    apps = _lives_of(lives, "app.py")

    assert len(apps) == 2
    first, second = apps
    assert not first.is_alive
    assert second.is_alive
    assert second.created.change_type == "A"
    assert second.path_history == ("app.py",)


def test_a_name_reused_after_a_rename_is_a_second_life(lives) -> None:
    """The first file moved away, so it is still alive under its new name.

    Only the deletion rule closes a life. A rename carries it somewhere else,
    which is why the second ``kept.py`` cannot join the first one.
    """
    kepts = _lives_of(lives, "kept.py")

    assert len(kepts) == 2
    first, second = kepts
    assert first.is_alive
    assert first.path_history == ("kept.py", "reused.py")
    assert second.is_alive
    assert second.path_history == ("kept.py",)


def test_a_rename_that_changed_too_much_breaks_the_chain(lives) -> None:
    """Git's default 50% similarity does not recognise this rename, and the
    tool reports what git reported: a death and a separate birth.

    This test exists to pin that behaviour down. Anyone who lowers the rename
    threshold will fail here first, which is the point: it is a decision, not an
    accident.
    """
    heavy = _lives_of(lives, "heavy.py")[0]
    renamed = _lives_of(lives, "renamed.py")[0]

    assert not heavy.is_alive
    assert heavy.path_history == ("heavy.py",)
    assert renamed.created.change_type == "A"
    assert renamed.path_history == ("renamed.py",)


def test_the_first_commit_births_every_file_it_contains(lives) -> None:
    earliest = min(life.created.committed_at for life in lives)
    born_then = [life for life in lives if life.created.committed_at == earliest]

    assert sorted(life.created.path for life in born_then) == [
        "app.py",
        "heavy.py",
        "kept.py",
        "plain.py",
    ]


def test_the_threshold_splits_three_files_differently(boundary_lives) -> None:
    """The cliff itself, pinned down.

    Three files are renamed in one commit, each with a different number of lines
    rewritten. Forty rewritten lines still clears git's default 50% similarity,
    so that file stays whole and carries both names. Fifty and eighty do not, so
    those two end at their old name and a separate file is born at the new one.
    """
    kept = _lives_of(boundary_lives, "under.py")

    assert len(kept) == 1
    assert kept[0].path_history == ("under.py", "under_after.py")
    assert kept[0].is_alive

    for name in ("over", "mostly"):
        died = _lives_of(boundary_lives, f"{name}.py")[0]
        born = _lives_of(boundary_lives, f"{name}_after.py")[0]

        assert not died.is_alive
        assert died.path_history == (f"{name}.py",)
        assert born.created.change_type == "A"
        assert born.path_history == (f"{name}_after.py",)


def test_a_rename_that_survived_carries_the_score_git_gave_it(
    boundary_lives,
) -> None:
    """The score travels with the event, so a near miss stays visible."""
    under = _lives_of(boundary_lives, "under.py")[0]

    assert [event.similarity for event in under.events] == [None, 56]


def test_the_order_of_the_input_does_not_matter(repository: Path, lives) -> None:
    assert build_lifecycles(reversed(read_commits(repository))) == lives


def test_commits_sharing_a_timestamp_keep_their_chain(tmp_path: Path) -> None:
    """A rebase, a scripted import or a converted history puts commits in one
    second, and the walk used to fall back on the order it was handed — which
    from the store is newest first, so the rename came before the creation.

    Ordering by the parent links fixes it; the clock only breaks ties between
    commits that are not related to each other.
    """
    repository = build_same_second_repo(tmp_path / "same-second")
    commits = read_commits(repository)

    assert len({commit.committed_at for commit in commits}) == 1

    built = build_lifecycles(commits)

    assert len(built) == 1
    assert built[0].path_history == ("app.py", "src/app.py")
    assert [event.change_type for event in built[0].events] == ["A", "R", "M"]
    assert built == build_lifecycles(reversed(commits))


def test_a_cycle_does_not_lose_commits() -> None:
    """Git cannot produce a cycle, but this function takes whatever it is given,
    and dropping a commit silently would be worse than placing it imperfectly."""
    first = _synthetic(1, FileChange("a.py", "A", 1, 0))
    second = _synthetic(2, FileChange("b.py", "A", 1, 0))
    looping = [
        replace(first, parents=(second.sha,)),
        replace(second, parents=(first.sha,)),
    ]

    built = build_lifecycles(looping)

    assert sum(len(life.events) for life in built) == 2


def test_lives_are_rebuilt_from_the_stored_history(
    repository: Path, lives, tmp_path: Path
) -> None:
    database = tmp_path / "lifecycle.db"
    analyze(repository, database)

    assert load_lifecycles(repository.resolve(), database) == lives


def test_an_edit_without_a_live_owner_joins_the_last_life_that_held_the_path() -> None:
    """Two branches interleaved by time: a rename lands, then an edit of the old
    name. The edit belongs to the same file, so it must not invent a new one.
    """
    history = [
        _synthetic(1, FileChange("app.py", "A", 1, 0)),
        _synthetic(2, FileChange("src/app.py", "R", 0, 0, old_path="app.py")),
        _synthetic(3, FileChange("app.py", "M", 1, 1)),
    ]

    built = build_lifecycles(history)

    assert len(built) == 1
    assert built[0].path_history == ("app.py", "src/app.py", "app.py")


def test_repeated_edits_stay_one_life(tmp_path: Path) -> None:
    """Three modifications in a row. Nothing about editing the same file again
    starts a new life, however many times it happens."""
    repository = build_edit_history_repo(tmp_path / "edits")
    lives = build_lifecycles(read_commits(repository))

    assert len(lives) == 3

    edited = _lives_of(lives, "edited.py")[0]

    assert [event.change_type for event in edited.events] == ["A", "M", "M", "M"]
    assert edited.path_history == ("edited.py",)
    assert edited.renames == 0
    assert edited.is_alive


def test_a_file_of_no_bytes_is_born_and_stays_one_life(tmp_path: Path) -> None:
    """An empty file is an ordinary file with nothing in it.

    Git reports zero added and zero deleted lines rather than the dashes it uses
    for a binary file, so nothing here may treat the zeroes as "unknown".
    """
    repository = build_edit_history_repo(tmp_path / "empty")
    lives = build_lifecycles(read_commits(repository))

    untouched = _lives_of(lives, "untouched.py")[0]

    assert [event.change_type for event in untouched.events] == ["A"]
    assert (untouched.created.added_lines, untouched.created.deleted_lines) == (0, 0)
    assert not untouched.created.is_binary
    assert untouched.is_alive


def test_an_empty_file_that_is_filled_in_keeps_its_life(tmp_path: Path) -> None:
    """Writing the first line into an empty file is a modification, not a birth."""
    repository = build_edit_history_repo(tmp_path / "filled")
    lives = build_lifecycles(read_commits(repository))

    empty = _lives_of(lives, "empty.py")[0]

    assert [event.change_type for event in empty.events] == ["A", "M"]
    assert empty.created.added_lines == 0
    assert empty.events[-1].added_lines == 3
