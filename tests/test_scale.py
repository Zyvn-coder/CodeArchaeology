"""Tests over a history too large to build a commit at a time.

Every other fixture in the suite is small enough to read in one sitting: eight
commits, a handful of files, each one there to pin down a rule. This one is not
about rules at all. It is here for what a small fixture cannot show — a walk
that is accidentally quadratic, a query that stops working once there are more
than a handful of rows, a total that drifts once there are thousands of them,
and a file that quietly stops being counted because two others share its name.

The history is a thousand commits and five thousand file changes, which
``git fast-import`` builds in under a second. Nothing here asserts a wall-clock
time: a test that fails when CI is busy teaches people to re-run it. The size is
the assertion.
"""

import subprocess
from pathlib import Path

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.history import read_commits
from codearchaeology.hotspots import rank_hotspots
from codearchaeology.lifecycle import Lifecycle, build_lifecycles
from codearchaeology.statistics import summarize
from codearchaeology.storage import connect
from sample_repo import (
    LARGE_COMMITS,
    LARGE_FILES,
    LARGE_LINES,
    LARGE_TOUCHED,
    build_large_repo,
)


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_large_repo(tmp_path_factory.mktemp("scale"))


@pytest.fixture(scope="module")
def commits(repository: Path):
    return read_commits(repository)


@pytest.fixture(scope="module")
def lives(commits) -> tuple[Lifecycle, ...]:
    return build_lifecycles(commits)


def _tracked_paths(repository: Path) -> list[str]:
    """Every path in the commit at HEAD, as git itself lists them.

    Read from the tree rather than from the index, so the answer comes from the
    history instead of from a working tree that a test might have moved.
    """
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.split()


def _line_counts(repository: Path, revision: str, paths: list[str]) -> dict[str, int]:
    """How many lines each of *paths* has at *revision*, in one git call.

    ``cat-file --batch`` answers many requests from one process: a line naming
    the object, then its bytes. Sixty separate ``git show`` calls cost two and a
    half seconds on this machine against a tenth of a second here, and that
    difference is most of what this file would otherwise spend.
    """
    request = "".join(f"{revision}:{path}\n" for path in paths).encode()
    completed = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=repository,
        input=request,
        capture_output=True,
        check=True,
    )

    counts: dict[str, int] = {}
    stream = completed.stdout
    for path in paths:
        header, _, rest = stream.partition(b"\n")
        _, kind, size = header.rsplit(b" ", 2)
        if kind != b"blob":
            raise AssertionError(f"{revision}:{path} is not a blob: {header!r}")
        length = int(size)
        counts[path] = len(rest[:length].decode("utf-8").splitlines())
        stream = rest[length + 1 :]  # the newline git puts after the bytes

    return counts


def test_the_fixture_is_as_large_as_it_claims(commits) -> None:
    """A scale test that quietly stopped being large would pass for ever."""
    assert len(commits) == LARGE_COMMITS
    assert sum(len(commit.changes) for commit in commits) == LARGE_COMMITS * LARGE_TOUCHED


def test_every_file_ends_up_as_exactly_one_life(lives, repository: Path) -> None:
    """No file split, lost or invented — checked against git's own file list
    rather than against another query over the same rows."""
    assert len(lives) == LARGE_FILES
    assert sorted(life.current_path for life in lives) == sorted(
        _tracked_paths(repository)
    )


def test_the_events_add_up_to_what_the_store_holds(
    lives, repository: Path, tmp_path: Path
) -> None:
    """The walk is over the same rows the database has, so the two totals have
    to meet. A commit dropped or replayed twice would show up as a gap."""
    database = tmp_path / "scale.db"
    analyze(repository, database)
    connection = connect(database)
    try:
        stored = connection.execute(
            "SELECT COUNT(*) AS n FROM commit_files"
        ).fetchone()["n"]
    finally:
        connection.close()

    assert sum(len(life.events) for life in lives) == stored
    assert stored == LARGE_COMMITS * LARGE_TOUCHED


def test_no_file_was_renamed_or_deleted(lives) -> None:
    """The fixture is built without renames and without deletions, so any that
    appear came from the walk rather than from the history."""
    assert all(life.is_alive for life in lives)
    assert all(life.created.change_type == "A" for life in lives)
    assert sum(life.renames for life in lives) == 0
    assert all(len(life.path_history) == 1 for life in lives)


def test_every_life_reads_in_the_order_it_happened(lives) -> None:
    for life in lives:
        moments = [event.committed_at for event in life.events]

        assert moments == sorted(moments), life.path_history


def test_every_file_nets_the_same_number_of_lines(lives) -> None:
    """Each file is created at its final size and then edited in place, so all
    of them end up the same size however many times each was touched. A total
    that drifts with the number of edits would show up here and nowhere else."""
    assert {summarize(life).net_change for life in lives} == {LARGE_LINES}


def test_the_net_change_is_the_size_the_file_really_has(
    lives, repository: Path
) -> None:
    """The same arithmetic as the small fixtures, but over every file rather
    than the two or three a hand-built history can afford."""
    paths = [life.current_path for life in lives]
    sizes = _line_counts(repository, "HEAD", paths)

    for life in lives:
        statistics = summarize(life)

        assert sizes[life.current_path] == statistics.net_change, life.current_path


def test_the_ranking_covers_every_file_and_is_sorted(lives) -> None:
    ranked = rank_hotspots(lives)
    counts = [row.commits for row in ranked]

    assert len(ranked) == LARGE_FILES
    assert counts == sorted(counts, reverse=True)
    assert sum(counts) == sum(len(life.events) for life in lives)
    assert all(row.commits > 1 for row in ranked)
