"""Tests for ``selection``'s second half: what the model is shown of a range.

The commit view's four promises carry over unchanged — the evidence is not
reduced, a range that fits is sent whole, a selection is stated and never
silent, and the answer will be checked against the view — and the set adds one
rule of its own, the one this file exists to hold:

* **The commit list is the spine, and the spine is not re-ordered.** Which
  commits stay is a fact about the rows; the order they appear in stays the
  graph's, because the prompt promises the model oldest first and a view that
  scrambled the story would break its own rule. Files may be ranked by size; a
  narrative may not.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.selection import (
    COMMITS,
    DEFINITIONS,
    FILE_CHANGES,
    MESSAGE_CHARACTERS,
    RANGES_PER_FILE,
    build_span_view,
)
from codearchaeology.span import build_span, build_span_json
from sample_repo import (
    _commit,
    _configure,
    _write_file,
    build_sample_repo,
    git_output,
)


@pytest.fixture(scope="module")
def wide_range(tmp_path_factory: pytest.TempPathFactory):
    """A range of thirty-one commits whose sizes are all different.

    File ``f{i}`` is created with ``i + 2`` lines — a docstring, a two-line
    function, and filler — so the commit that creates it moved a number of
    lines no other commit moved, while every function spans the same two lines.
    That is what makes the two rules tellable apart: the commit and file rules
    rank by size, and the definition rule, all sizes equal, falls to the name.
    The last commit edits ``base.py`` in eight separate places, so one file's
    net diff holds more spans than the per-file cap keeps.
    """
    repository = tmp_path_factory.mktemp("span-selection") / "repo"
    repository.mkdir(parents=True)
    git_output(repository, "init", "--initial-branch", "main")
    _configure(repository)

    _write_file(
        repository,
        "base.py",
        "".join(f"line {number}\n" for number in range(10, 40, 2)),
    )
    _commit(repository, "2024-09-01T09:00:00+00:00", "create base.py")
    for number in range(1, 31):
        _write_file(
            repository,
            f"f{number:02d}.py",
            f'"""File {number}."""\ndef value():\n    return 0\n'
            + "".join(f"# filler {line}\n" for line in range(number - 1)),
        )
        _commit(
            repository,
            f"2024-09-{number + 1:02d}T09:00:00+00:00",
            f"create f{number:02d}",
        )
    _write_file(
        repository,
        "base.py",
        "".join(
            f"line {number} changed\n" if (number - 10) % 4 == 0 else f"line {number}\n"
            for number in range(10, 40, 2)
        ),
    )
    _commit(repository, "2024-10-02T09:00:00+00:00", "edit base.py everywhere")

    database = repository.parent / "wide-range.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    return repository, database, root, head


@pytest.fixture(scope="module")
def small_range(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture, whose whole range fits in a model's window."""
    repository = build_sample_repo(tmp_path_factory.mktemp("span-fits"))
    database = repository.parent / "fits.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    return repository, database, root, head


def _view(wide_range):
    repository, database, root, head = wide_range
    return build_span_view(build_span(repository, database, root, head))


def test_a_range_that_fits_is_shown_whole(small_range):
    repository, database, root, head = small_range
    span = build_span(repository, database, root, head)

    view = build_span_view(span)

    assert view.selection is None
    assert view.json == build_span_json(span)


def test_the_evidence_is_not_reduced_by_being_selected(wide_range):
    repository, database, root, head = wide_range
    span = build_span(repository, database, root, head)
    before = build_span_json(span)

    view = build_span_view(span)

    assert build_span_json(span) == before
    assert len(view.json) < len(before)


def test_the_commits_kept_are_the_largest_and_the_story_keeps_its_order(wide_range):
    """Thirty-one commits against a cap of twenty-five: the six smallest go —
    the six smallest files' commits, sizes three to eight lines — and the
    twenty-five that stay are still listed oldest first."""
    view = _view(wide_range)

    assert view.selection["commits"] == {"shown": COMMITS, "omitted": 6}
    messages = [commit.message for commit in view.context.commits]
    assert "create f01" not in messages
    assert "create f06" not in messages
    assert "create f07" in messages
    assert "edit base.py everywhere" in messages
    # The bundle's own order, not a size ranking: the messages still climb.
    numbers = [
        int(message.rsplit("f", 1)[-1])
        for message in messages
        if message.startswith("create f")
    ]
    assert numbers == sorted(numbers)


def test_the_files_kept_are_the_largest_and_the_count_is_stated(wide_range):
    view = _view(wide_range)

    assert view.selection["file_changes"] == {
        "shown": FILE_CHANGES,
        "omitted": 31 - FILE_CHANGES,
    }
    kept = [change.path for change in view.context.changes]
    assert "f30.py" in kept
    assert "f01.py" not in kept


def test_a_files_spans_are_cut_and_its_row_says_how_many_there_were(wide_range):
    view = _view(wide_range)

    rows = {row["path"]: row for row in view.object["file_changes"]}
    base = rows["base.py"]

    assert len(base["ranges"]) == RANGES_PER_FILE
    assert base["ranges_total"] > RANGES_PER_FILE
    assert view.selection["ranges"]["per_file"] == RANGES_PER_FILE


def test_the_definitions_are_capped_and_a_tie_goes_by_name(wide_range):
    """Every file holds one function spanning the same two lines, so the order
    is the tie-breaker — a name, not whatever the walk returned."""
    view = _view(wide_range)

    assert view.selection["ast_changes"] == {
        "shown": DEFINITIONS,
        "omitted": 30 - DEFINITIONS,
    }
    kept = [definition.path for definition in view.context.definitions]
    assert kept == sorted(kept)
    assert "f01.py" in kept
    assert "f30.py" not in kept


def test_a_message_over_the_ceiling_is_cut_per_commit(wide_range, tmp_path: Path):
    """One ceiling per message, the commit view's own rule: how many messages
    there are is the commits cap's business, not the message cap's."""
    repository, database, root, head = wide_range
    span = build_span(repository, database, root, head)

    long_message = "x" * (MESSAGE_CHARACTERS + 500)
    commits = tuple(
        replace(commit, message=long_message)
        if commit.message == "edit base.py everywhere"
        else commit
        for commit in span.commits
    )
    view = build_span_view(replace(span, commits=commits))

    shown = next(
        commit
        for commit in view.context.commits
        if commit.message.startswith("xxx") or len(commit.message) == MESSAGE_CHARACTERS
    )
    assert len(shown.message) == MESSAGE_CHARACTERS
    assert view.selection["message"]["characters_omitted"] == 500


def test_the_note_names_the_unit_and_the_counts(wide_range):
    view = _view(wide_range)

    assert view.note is not None
    assert "The range is large" in view.note
    assert f"{COMMITS} of 31 commits" in view.note
    assert view.note is not None and "commit is large" not in view.note


def test_the_selection_counts_the_rows_whose_diff_arrived_cut(
    wide_range, tmp_path: Path
):
    from codearchaeology.context import DIFF_CHARACTERS, DiffText

    repository, database, root, head = wide_range
    span = build_span(repository, database, root, head)

    cut = DiffText(
        text="x" * DIFF_CHARACTERS,
        characters=DIFF_CHARACTERS,
        characters_total=DIFF_CHARACTERS + 900,
        truncated=True,
    )
    changes = tuple(
        replace(change, diff=cut) if change.path == "f30.py" else change
        for change in span.changes
    )
    view = build_span_view(replace(span, changes=changes))

    assert "f30.py" in [change.path for change in view.context.changes]
    assert view.selection["diffs"] == {"cut": 1}


def test_the_same_range_selects_the_same_rows(wide_range):
    assert _view(wide_range).json == _view(wide_range).json
