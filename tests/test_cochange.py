"""Tests for the co-change statistic.

Every expected number in this file is either counted by hand from a fixture or
read off a fixture whose shape makes it arithmetic. The hand-computable one is
``build_cochange_repo``: three commits, three files, and the three pairs the
statistic can produce.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from codearchaeology.cochange import analyze_cochange
from codearchaeology.history import Commit, FileChange, read_commits
from codearchaeology.lifecycle import build_lifecycles
from sample_repo import (
    build_cochange_repo,
    build_large_repo,
    build_lifecycle_repo,
    build_single_commit_repo,
)


def _commit(number: int, *changes: FileChange) -> Commit:
    """One hand-made commit, for histories no repository has to produce."""
    moment = datetime(2024, 9, number, tzinfo=timezone.utc)
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


def _only(reports, path: str):
    """The one report for *path*, or a failure naming what came back instead."""
    matches = [report for report in reports if report.path == path]
    assert len(matches) == 1, f"expected one life for {path}, got {len(matches)}"
    return matches[0]


def _as_pairs(report) -> list[tuple[str, int, float]]:
    return [
        (row.path, row.shared_commits, row.score) for row in report.co_changes
    ]


@pytest.fixture(scope="module")
def cochange_commits(tmp_path_factory: pytest.TempPathFactory) -> list[Commit]:
    """The hand-computable repository's commits."""
    repository = build_cochange_repo(tmp_path_factory.mktemp("cochange-repo"))
    return read_commits(repository)


@pytest.fixture(scope="module")
def sample_commits(tmp_path_factory: pytest.TempPathFactory) -> list[Commit]:
    """The sample repository's commits: a rename, a deletion and a merge."""
    from sample_repo import build_sample_repo

    repository = build_sample_repo(tmp_path_factory.mktemp("cochange-sample"))
    return read_commits(repository)


@pytest.fixture(scope="module")
def lifecycle_commits(tmp_path_factory: pytest.TempPathFactory) -> list[Commit]:
    """The lifecycle repository's commits: two renames, a deletion, a reuse."""
    repository = build_lifecycle_repo(tmp_path_factory.mktemp("cochange-life"))
    return read_commits(repository)


# --- the hand-computable fixture -------------------------------------------


def test_a_pair_that_repeats_is_counted_twice(cochange_commits) -> None:
    """A and B changed together in two of A's three commits."""
    report = _only(analyze_cochange(cochange_commits, "a.py"), "a.py")

    assert report.analyzed_commits == 3
    assert _as_pairs(report) == [("b.py", 2, 2 / 3)]


def test_a_pair_that_happens_once_is_hidden_by_default(cochange_commits) -> None:
    """C shares one commit with A, which is below the default minimum."""
    report = _only(analyze_cochange(cochange_commits, "a.py"), "a.py")

    assert report.hidden_pairs == 1
    assert "c.py" not in [row.path for row in report.co_changes]


def test_the_other_denominator_is_the_other_file(cochange_commits) -> None:
    """B's score is over B's two commits, not over A's three."""
    report = _only(analyze_cochange(cochange_commits, "b.py"), "b.py")

    assert report.analyzed_commits == 2
    assert _as_pairs(report) == [("a.py", 2, 1.0)]


def test_a_file_alone_in_its_commits_has_no_pair(cochange_commits) -> None:
    """C is the only file in one of its commits and shares the other with A."""
    report = _only(analyze_cochange(cochange_commits, "c.py"), "c.py")

    assert report.analyzed_commits == 1
    assert report.co_changes == ()
    assert report.hidden_pairs == 1


def test_minimum_one_shows_every_pair(cochange_commits) -> None:
    """The threshold is a display filter, so lowering it changes only the list."""
    report = _only(
        analyze_cochange(cochange_commits, "a.py", min_shared=1), "a.py"
    )

    assert report.analyzed_commits == 3
    assert report.hidden_pairs == 0
    assert _as_pairs(report) == [("b.py", 2, 2 / 3), ("c.py", 1, 1 / 3)]


def test_a_two_file_commit_pairs_only_those_two(cochange_commits) -> None:
    """Commit 1 holds A and B alone, and commit 2 the same two.

    Read from B's side, where the denominator is B's two commits. The A+B+C
    shape is the sample fixture's first commit, asserted through
    ``legacy.py`` below.
    """
    report = _only(
        analyze_cochange(cochange_commits, "b.py", min_shared=1), "b.py"
    )

    assert _as_pairs(report) == [("a.py", 2, 1.0)]


def test_a_commit_with_three_files_makes_every_pair(sample_commits) -> None:
    """The sample fixture's first commit holds README.md, app.py and legacy.py.

    Read from legacy.py, which is in exactly two commits, so its score is 0.5
    against each of the three files that commit touched.
    """
    report = _only(
        analyze_cochange(sample_commits, "legacy.py", min_shared=1), "legacy.py"
    )

    assert report.analyzed_commits == 2
    assert _as_pairs(report) == [
        ("README.md", 1, 0.5),
        ("assets/logo.png", 1, 0.5),
        ("core/app.py", 1, 0.5),
        ("工具/文本.py", 1, 0.5),
    ]


def test_a_file_never_pairs_with_itself(cochange_commits) -> None:
    for name in ("a.py", "b.py", "c.py"):
        for report in analyze_cochange(cochange_commits, name, min_shared=1):
            assert name not in [row.path for row in report.co_changes]


def test_the_pair_set_is_stable_across_calls(cochange_commits) -> None:
    first = analyze_cochange(cochange_commits, "a.py", min_shared=1)
    second = analyze_cochange(cochange_commits, "a.py", min_shared=1)

    assert first == second


def test_the_pair_set_does_not_depend_on_the_input_order(cochange_commits) -> None:
    """The walk follows the parent links, so a shuffled input answers the same."""
    shuffled = list(reversed(cochange_commits))
    ordered = analyze_cochange(cochange_commits, "a.py", min_shared=1)
    jumbled = analyze_cochange(shuffled, "a.py", min_shared=1)

    assert jumbled == ordered


def test_a_path_the_history_never_carried_has_no_answer(cochange_commits) -> None:
    assert analyze_cochange(cochange_commits, "missing.py") == ()


def test_a_commit_reports_a_file_once_even_with_two_rows() -> None:
    """No git repository produces this, which is why it is hand-made.

    A file that appears in two rows of one commit must contribute one
    occurrence: the commit is reduced to a set of identities before anything is
    counted. The alternative — counting rows — would let a file be its own
    co-change partner.
    """
    commits = [
        _commit(1, FileChange("a.py", "A", 1, 0), FileChange("b.py", "A", 1, 0)),
        _commit(
            2,
            FileChange("a.py", "M", 1, 1),
            FileChange("a.py", "M", 2, 2),
            FileChange("b.py", "M", 1, 1),
        ),
    ]

    report = _only(analyze_cochange(commits, "a.py", min_shared=1), "a.py")

    assert report.analyzed_commits == 2
    assert _as_pairs(report) == [("b.py", 2, 1.0)]


# --- identity: rename, delete, reuse ---------------------------------------


def test_a_rename_does_not_end_the_file(lifecycle_commits) -> None:
    """Querying the final name answers for the life that began under the first."""
    report = _only(analyze_cochange(lifecycle_commits, "src/core/app.py"), "src/core/app.py")

    assert report.path_history == ("app.py", "src/app.py", "src/core/app.py")
    assert report.analyzed_commits == 4


def test_a_rename_and_an_edit_in_one_commit_are_one_pair(lifecycle_commits) -> None:
    """Commit 3 moves the app module and edits plain.py, so they co-change.

    Read from plain.py, whose two commits are its birth (with three others) and
    that rename, which makes the pair exact.
    """
    report = _only(analyze_cochange(lifecycle_commits, "plain.py"), "plain.py")

    assert report.analyzed_commits == 2
    assert _as_pairs(report) == [("src/core/app.py", 2, 1.0)]


def test_a_deleted_file_still_answers(sample_commits) -> None:
    """A file that is gone is a legitimate historical question.

    Every partner shares exactly one of its two commits — its birth and its
    death — so the default minimum hides all four, and the hidden count is what
    says they were found.
    """
    report = _only(analyze_cochange(sample_commits, "legacy.py"), "legacy.py")

    assert report.analyzed_commits == 2
    assert report.co_changes == ()
    assert report.hidden_pairs == 4


def test_a_name_reused_after_a_delete_answers_twice(lifecycle_commits) -> None:
    """app.py was one file, was deleted, and a second file took the name."""
    reports = analyze_cochange(lifecycle_commits, "app.py")

    assert len(reports) == 2
    first, second = reports
    assert first.analyzed_commits == 4
    assert first.path_history == ("app.py", "src/app.py", "src/core/app.py")
    assert second.analyzed_commits == 1
    assert second.path_history == ("app.py",)


def test_a_rename_git_refused_to_recognise_is_two_files(lifecycle_commits) -> None:
    """heavy.py and renamed.py are a death and a birth, not one file.

    Git did not call it a rename — too little content survived — so the two
    lives share their one commit as two files, which is what the tool observed.
    The shared commit is a single one, so the pair is hidden by default.
    """
    heavy = _only(analyze_cochange(lifecycle_commits, "heavy.py"), "heavy.py")

    assert heavy.analyzed_commits == 2
    assert heavy.co_changes == ()

    shown = _only(
        analyze_cochange(lifecycle_commits, "heavy.py", min_shared=1), "heavy.py"
    )
    assert ("renamed.py", 1, 0.5) in _as_pairs(shown)


# --- merge and large commits ------------------------------------------------


def test_a_merge_commit_contributes_nothing(sample_commits) -> None:
    """The merge has no file rows, so it is in no numerator and no denominator.

    The assertion is the absence, not a skipped flag: the merge could only
    contribute if it had occurrences, and it has none.
    """
    merge = next(commit for commit in sample_commits if commit.is_merge)
    report = _only(analyze_cochange(sample_commits, "core/app.py"), "core/app.py")

    assert merge.changes == ()
    assert report.analyzed_commits == 3
    assert report.excluded_large_commits == 0


def test_a_commit_over_the_limit_leaves_the_analysis(tmp_path_factory) -> None:
    """Every commit touches 101 files, so the limit of 100 excludes them all."""
    repository = build_large_repo(
        tmp_path_factory.mktemp("cochange-large"),
        commits=3,
        files=101,
        touched=101,
    )
    report = _only(analyze_cochange(read_commits(repository), "pkg0/mod0.py"), "pkg0/mod0.py")

    assert report.analyzed_commits == 0
    assert report.excluded_large_commits == 3
    assert report.co_changes == ()


def test_a_commit_at_the_limit_stays_in_the_analysis(tmp_path_factory) -> None:
    """One hundred files is not over the limit, so the commits are analyzed.

    With every commit touching the same five files, each pair shares all five
    commits and the default minimum shows them.
    """
    repository = build_large_repo(
        tmp_path_factory.mktemp("cochange-boundary"),
        commits=5,
        files=100,
        touched=100,
    )
    report = _only(analyze_cochange(read_commits(repository), "pkg0/mod0.py"), "pkg0/mod0.py")

    assert report.analyzed_commits == 5
    assert report.excluded_large_commits == 0
    assert len(report.co_changes) == 99
    assert all(row.shared_commits == 5 for row in report.co_changes)


# --- the report itself ------------------------------------------------------


def test_a_file_with_no_partner_is_an_empty_report(tmp_path_factory) -> None:
    """One commit, one file: nothing can have changed alongside it."""
    repository = build_single_commit_repo(tmp_path_factory.mktemp("cochange-single"))
    report = _only(analyze_cochange(read_commits(repository), "only.py"), "only.py")

    assert report.analyzed_commits == 1
    assert report.co_changes == ()
    assert report.hidden_pairs == 0


def test_the_report_is_ordered_by_score_then_name(lifecycle_commits) -> None:
    """The app life's partners: plain.py scores 0.5, then a 0.25 tie by name."""
    report = _only(
        analyze_cochange(lifecycle_commits, "src/app.py", min_shared=1), "src/core/app.py"
    )

    assert report.path == "src/core/app.py"
    assert [row.path for row in report.co_changes] == [
        "plain.py",
        "heavy.py",
        "reused.py",
    ]
    assert [row.shared_commits for row in report.co_changes] == [2, 1, 1]


def test_two_lives_with_one_name_are_both_answered() -> None:
    """A deleted name and a re-created one are two files with one current path.

    The last ordering step is the only thing that makes this deterministic, so
    the test asserts both reports come back, oldest life first.
    """
    commits = [
        _commit(1, FileChange("a.py", "A", 1, 0), FileChange("b.py", "A", 1, 0)),
        _commit(2, FileChange("a.py", "D", 0, 1)),
        _commit(3, FileChange("a.py", "A", 1, 0)),
    ]

    reports = analyze_cochange(commits, "a.py", min_shared=1)

    assert [report.path for report in reports] == ["a.py", "a.py"]
    assert [report.analyzed_commits for report in reports] == [2, 1]
    assert reports[0].path_history == ("a.py",)
    assert reports[1].path_history == ("a.py",)


# --- hardening: the boundaries the statistic has to survive -----------------


def test_a_huge_commit_is_excluded_without_costing_anything() -> None:
    """Four thousand files in one commit, and the analysis still answers.

    The huge commit is hand-made rather than built by a repository, because
    producing it in git would take minutes and prove nothing extra: what the
    test is about is that the commit can be *held and walked at all*. The
    per-file algorithm never builds its pairs, so the commit's 7,997,998
    potential pairs cost nothing. A stress fixture, not a performance bound —
    the measured numbers are in ``benchmarks/cochange_benchmark.py``, and a
    wall-clock assertion here would fail on a busy machine.
    """
    huge = _commit(
        2,
        FileChange("pkg/mod0.py", "M", 1, 1),
        *[FileChange(f"pkg/mod{index}.py", "A", 1, 0) for index in range(1, 4001)],
    )
    commits = [
        _commit(1, FileChange("pkg/mod0.py", "A", 1, 0)),
        huge,
    ]
    report = _only(analyze_cochange(commits, "pkg/mod0.py"), "pkg/mod0.py")

    assert report.analyzed_commits == 1
    assert report.excluded_large_commits == 1
    assert report.co_changes == ()
    assert report.hidden_pairs == 0


def test_the_excluded_commit_is_the_large_one_not_another(sample_commits) -> None:
    """The exclusion has to be a rule that runs, not a line in the document.

    ``legacy.py`` is in two commits: the three-file birth and the three-file
    death. Both are under the default limit, so both are in the sample — and
    with a limit of two, exactly the two-file commits leave and the pair with
    the three-file one survives.
    """
    default = _only(analyze_cochange(sample_commits, "legacy.py"), "legacy.py")
    assert default.analyzed_commits == 2
    assert default.excluded_large_commits == 0

    tiny = _only(
        analyze_cochange(sample_commits, "legacy.py", large_commit_limit=2),
        "legacy.py",
    )
    assert tiny.analyzed_commits == 0
    assert tiny.excluded_large_commits == 2
    assert tiny.co_changes == ()


def test_a_merge_commit_is_in_no_pair(sample_commits) -> None:
    """The merge rule is the absence of rows, so the absence is what is asserted.

    If a merge were counted, ``README.md`` and ``legacy.py`` would share a third
    commit and their score would move. The merge's own sha is checked against
    every file's commit set to make the point directly.
    """
    merge = next(commit for commit in sample_commits if commit.is_merge)
    report = _only(
        analyze_cochange(sample_commits, "README.md", min_shared=1), "README.md"
    )

    assert merge.changes == ()
    assert report.analyzed_commits == 1
    assert ("legacy.py", 1, 1.0) in _as_pairs(report)

    lives = build_lifecycles(sample_commits)
    for life in lives:
        assert merge.sha not in {event.commit_sha for event in life.events}


def test_repeating_the_same_history_changes_nothing() -> None:
    """The same commit twice is two observations, not a new relationship.

    A history where a and b change together in two commits is different from one
    where the identical pair repeats four times: the count doubles and the score
    does not move, because both the numerator and the denominator grew.
    """
    twice = [
        _commit(1, FileChange("a.py", "A", 1, 0), FileChange("b.py", "A", 1, 0)),
        _commit(2, FileChange("a.py", "M", 1, 1), FileChange("b.py", "M", 1, 1)),
    ]
    repeated = [
        _commit(1, FileChange("a.py", "A", 1, 0), FileChange("b.py", "A", 1, 0)),
        _commit(2, FileChange("a.py", "M", 1, 1), FileChange("b.py", "M", 1, 1)),
        _commit(3, FileChange("a.py", "M", 1, 1), FileChange("b.py", "M", 1, 1)),
        _commit(4, FileChange("a.py", "M", 1, 1), FileChange("b.py", "M", 1, 1)),
    ]

    short = _only(analyze_cochange(twice, "a.py"), "a.py")
    long = _only(analyze_cochange(repeated, "a.py"), "a.py")

    assert _as_pairs(short) == [("b.py", 2, 1.0)]
    assert _as_pairs(long) == [("b.py", 4, 1.0)]
    assert short.analyzed_commits == 2
    assert long.analyzed_commits == 4


def test_a_thousand_unrelated_files_stay_unrelated(tmp_path_factory) -> None:
    """Each commit touches one file, so no pair can exist at all.

    The answer has to stay empty rather than fill up with pairs that share no
    commit, and each file's denominator is its own single commit.
    """
    repository = build_large_repo(
        tmp_path_factory.mktemp("cochange-unrelated"),
        commits=1000,
        files=1000,
        touched=1,
    )
    commits = read_commits(repository)

    for name in ("pkg0/mod0.py", "pkg50/mod500.py", "pkg99/mod999.py"):
        report = _only(analyze_cochange(commits, name, min_shared=1), name)
        assert report.analyzed_commits == 1
        assert report.co_changes == ()
        assert report.hidden_pairs == 0


def test_a_shared_file_name_never_becomes_a_pair_with_itself(
    lifecycle_commits,
) -> None:
    """Two lives can carry one name, and neither may be paired with the other.

    ``app.py`` belongs to the four-commit life and to the one-commit life. The
    two are different files; a naive implementation that keyed on the name would
    report each as co-changing with the other in the commit that created the
    second.
    """
    reports = analyze_cochange(lifecycle_commits, "app.py", min_shared=1)

    assert len(reports) == 2
    for report in reports:
        assert "app.py" not in [row.path for row in report.co_changes]


def test_the_identity_map_agrees_with_the_lifecycle_model(cochange_commits) -> None:
    """The co-change walk must not be a second identity implementation.

    Every event of every life is looked up the way the module looks it up, and
    the answer has to be the life the event belongs to — including the events
    whose path was reused later.
    """
    lives = build_lifecycles(cochange_commits)
    mapping = {
        (event.commit_sha, event.path): ordinal
        for ordinal, life in enumerate(lives)
        for event in life.events
    }

    for ordinal, life in enumerate(lives):
        for event in life.events:
            assert mapping[(event.commit_sha, event.path)] == ordinal
