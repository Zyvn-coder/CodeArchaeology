"""Tests for ``span``: the evidence one revision range carries.

The set bundle is the second unit this project has — a commit was the only unit
for five versions — so most of what is below holds it to the design freeze's
own words: the order is the graph's, the diff is the unit's own, a path is one
row however many commits touched it, and the absences that only a range can
have are named and never merged.

The fixtures are real repositories, and the two shapes no shipped fixture has —
a file that dies inside the range and a file that ends where it began — are
built here, because each is a different sentence and a fixture that could not
tell them apart could not test the rule that keeps them apart.
"""

import json
from pathlib import Path

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.explanation import INSTRUCTIONS
from codearchaeology.history import GitError
from codearchaeology.span import (
    GONE_WITHIN_RANGE,
    NET_UNCHANGED,
    NOT_ANALYZED,
    EmptyRangeError,
    InvalidRangeError,
    build_span,
    build_span_json,
    build_span_object,
    resolve_range,
)
from codearchaeology.storage import connect
from sample_repo import (
    _commit,
    _configure,
    _write_file,
    add_commit,
    build_deep_history_repo,
    build_sample_repo,
    git_output,
)
from test_context import _big_diff_repo, _git_diff

# The commits of the sample fixture this file reads, by message.
INITIAL = "Initial commit"
RENAMED = "Move app module into the core package"
CACHED = "Add caching"
FIXED = "Fix login bug"
MERGED = "Merge branch 'feature/caching'"
FINAL = "Add logo and unicode module, drop legacy helper"


@pytest.fixture(scope="module")
def span_repo(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture, analyzed and read, with the database beside it."""
    repository = build_sample_repo(tmp_path_factory.mktemp("span-repo"))
    database = repository.parent / "span.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    return repository, database


def _sha(repository, database, message: str) -> str:
    """The sha of the fixture commit with this exact message."""
    connection = connect(database)
    try:
        row = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (message,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None, f"the fixture has no commit {message!r}"
    return row["sha"]


def _span(repository, database, base: str, tip: str):
    return build_span(repository, database, base, tip)


def _whole_range(repository, database):
    """The span from the second commit to the last — the fixture's whole story
    minus its root, so every path in it has a life that reaches past the base."""
    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    return build_span(repository, database, root, head)


def _change(span, path: str):
    matches = [change for change in span.changes if change.path == path]
    assert len(matches) == 1, f"expected one entry for {path}, got {len(matches)}"
    return matches[0]


# The selector.


def test_a_range_resolves_to_full_shas(span_repo):
    repository, database = span_repo
    head = git_output(repository, "rev-parse", "HEAD").strip()

    base, tip = resolve_range(repository, f"HEAD~1..HEAD")

    assert (base, tip) == (git_output(repository, "rev-parse", "HEAD~1").strip(), head)


def test_a_spec_that_is_not_a_range_is_refused(span_repo):
    repository, database = span_repo
    for bad in ("HEAD", "", "..HEAD", "HEAD..", "a..b..c"):
        with pytest.raises(InvalidRangeError):
            resolve_range(repository, bad)


def test_a_side_git_cannot_resolve_is_gits_own_error(span_repo):
    repository, database = span_repo
    with pytest.raises(GitError) as error:
        resolve_range(repository, "not-a-revision..HEAD")
    assert "not-a-revision" in str(error.value)


def test_a_range_that_holds_no_commits_is_named(span_repo):
    repository, database = span_repo
    base, tip = resolve_range(repository, "HEAD..HEAD")

    with pytest.raises(EmptyRangeError):
        build_span(repository, database, base, tip)


# The order and the header.


def test_the_order_is_oldest_first_and_parents_before_children(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)
    shas = [commit.sha for commit in span.commits]

    assert shas[0] == _sha(repository, database, RENAMED)
    assert shas[-1] == _sha(repository, database, FINAL)
    order = {sha: index for index, sha in enumerate(shas)}
    assert order[_sha(repository, database, FIXED)] < order[
        _sha(repository, database, MERGED)
    ]
    assert order[_sha(repository, database, CACHED)] < order[
        _sha(repository, database, MERGED)
    ]


def test_the_header_says_what_the_unit_is(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)

    assert span.commits[0].analyzed
    assert [commit.message for commit in span.commits if commit.is_merge] == [MERGED]
    header = build_span_object(span)["range"]
    assert header["commits"] == 5
    assert header["authors"] == ["Ada Lovelace <ada@example.com>"]
    assert header["merge_commits"] == 1
    assert header["base"] == span.base
    assert header["tip"] == span.tip


# The paths.


def test_a_path_touched_twice_is_one_row_with_two_occurrences(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)
    change = _change(span, "core/app.py")

    assert change.net_status is not None and change.net_status.startswith("R")
    assert [
        (occurrence.change_type, occurrence.commit_sha)
        for occurrence in change.occurrences
    ] == [
        ("R", _sha(repository, database, RENAMED)),
        ("M", _sha(repository, database, FIXED)),
    ]


def test_the_net_status_is_what_git_reported(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)

    assert _change(span, "assets/logo.png").net_status == "A"
    assert _change(span, "core/cache.py").net_status == "A"
    assert _change(span, "legacy.py").net_status == "D"


def test_the_diff_is_the_endpoints_own(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)

    # A path the endpoints hold under one name, compared byte for byte with
    # git's own per-path invocation.
    added = _change(span, "工具/文本.py")
    assert added.diff is not None
    assert added.diff.text == _git_diff(repository, span.base, span.tip, "工具/文本.py")
    assert not added.diff.truncated

    # A renamed path cannot be checked that way — git's rename detection needs
    # both names inside the diff, and a per-path invocation shows the file as
    # born instead of moved — so the rename is asserted where it lives.
    renamed = _change(span, "core/app.py")
    assert renamed.diff.text.startswith("diff --git a/app.py b/core/app.py\n")
    assert "rename from app.py" in renamed.diff.text
    assert "rename to core/app.py" in renamed.diff.text
    assert not renamed.diff.truncated


def test_the_definitions_aggregate_and_say_where_they_stood(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)

    login = next(
        definition
        for definition in span.definitions
        if definition.path == "core/app.py" and definition.qualname == "login"
    )
    assert [event.change for event in login.events] == ["modified"]
    assert (login.presence_base, login.presence_tip) == ("present", "present")

    parse = next(
        definition
        for definition in span.definitions
        if definition.path == "legacy.py" and definition.qualname == "parse"
    )
    assert [event.change for event in parse.events] == ["deleted"]
    assert (parse.presence_base, parse.presence_tip) == ("present", "absent")


def test_the_window_is_the_life_before_the_range_first_touched_it(tmp_path: Path):
    """``deep.py`` is created and edited eight times; a range over the last three
    edits has six earlier events to draw from, and the window holds five."""
    repository = build_deep_history_repo(tmp_path / "deep")
    database = tmp_path / "deep.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    base = _sha(repository, database, "Edit deep.py, pass 5")
    tip = _sha(repository, database, "Edit deep.py, pass 8")
    span = build_span(repository, database, base, tip)

    entry = next(item for item in span.history if item.path == "deep.py")
    assert entry.total == 6  # the creation and passes 1 to 5
    assert len(entry.commits) == 5
    assert span.bounds.history_commits_omitted == 1


# The absences.


def test_a_merge_inside_the_range_is_named_and_carries_no_diff(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)
    merge = _sha(repository, database, MERGED)

    assert any(
        absence.kind == "merge_no_diff" and merge in absence.detail
        for absence in span.absences
    )
    assert [absence.kind for absence in span.absences].count("merge_no_diff") == 1


def test_a_commit_the_analysis_holds_no_row_for_is_marked_not_analyzed(
    span_repo, tmp_path: Path
):
    """A range over an analysis that stops short of the tip: the commit is in
    the set, its metadata is git's, and the absence names its sha."""
    repository, database = span_repo
    extra = add_commit(repository, "after the analysis stopped")
    base = git_output(repository, "rev-parse", "HEAD~2").strip()

    span = build_span(repository, database, base, extra)

    assert [commit.analyzed for commit in span.commits] == [True, False]
    assert [
        (absence.kind, absence.detail)
        for absence in span.absences
        if absence.kind == NOT_ANALYZED
    ] == [(NOT_ANALYZED, f"the analysis holds no row for {extra}")]


def _range_fixture(tmp_path: Path, commits):
    """A repository built from (path, content) snapshots, one commit per entry."""
    repository = tmp_path / "shaped"
    repository.mkdir()
    git_output(repository, "init", "--initial-branch", "main")
    _configure(repository)
    for number, (path, content) in enumerate(commits, start=1):
        if content is None:
            (repository / path).unlink()
        else:
            _write_file(repository, path, content)
        _commit(repository, f"2024-08-{number:02d}T09:00:00+00:00", f"change {number}")
    return repository


def test_a_path_that_lived_and_died_inside_the_range(tmp_path: Path):
    repository = _range_fixture(
        tmp_path,
        [
            ("kept.py", "kept = 1\n"),
            ("short.py", "short = 1\n"),
            ("short.py", None),
            ("kept.py", "kept = 2\n"),
        ],
    )
    database = tmp_path / "shaped.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    span = build_span(repository, database, root, head)

    change = _change(span, "short.py")
    assert change.net_status is None
    assert change.diff is None
    assert [(occurrence.change_type) for occurrence in change.occurrences] == [
        "A",
        "D",
    ]
    # The deletion's own version absence is here too — the AST layer's words for
    # a file it no longer holds — and the two are different sentences.
    assert [(absence.kind, absence.path) for absence in span.absences] == [
        (GONE_WITHIN_RANGE, "short.py"),
        ("not_in_this_commit", "short.py"),
    ]


def test_a_path_that_ends_where_it_began(tmp_path: Path):
    """Edited twice inside the range and back to the original bytes: the net
    diff holds nothing for it, and that is its own sentence — not the sentence
    of a file that never left."""
    original = "value = 1\n"
    repository = _range_fixture(
        tmp_path,
        [
            ("back.py", original),
            ("back.py", "value = 2\n"),
            ("back.py", original),
        ],
    )
    database = tmp_path / "shaped.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    span = build_span(repository, database, root, head)

    change = _change(span, "back.py")
    assert change.net_status is None
    assert change.diff is None
    assert len(change.occurrences) == 2
    assert [
        (absence.kind, absence.path) for absence in span.absences
    ] == [(NET_UNCHANGED, "back.py")]


def test_a_binary_pair_is_named_rather_than_shown_empty(tmp_path: Path):
    repository = _range_fixture(tmp_path, [("text.py", "a = 1\n")])
    _write_file(repository, "image.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    _commit(repository, "2024-08-02T09:00:00+00:00", "add a binary")
    database = tmp_path / "shaped.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    span = build_span(repository, database, root, head)

    change = _change(span, "image.png")
    assert change.is_binary
    assert change.diff is None
    assert ("binary_no_text", "image.png") in [
        (absence.kind, absence.path) for absence in span.absences
    ]


def test_a_big_diff_is_bounded_in_the_set_too(tmp_path: Path):
    from codearchaeology.context import DIFF_CHARACTERS

    repository = _big_diff_repo(tmp_path)
    database = tmp_path / "big-span.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    span = build_span(repository, database, root, head)
    change = _change(span, "wide.py")

    assert change.diff.truncated
    assert change.diff.characters == DIFF_CHARACTERS
    assert change.diff.characters_total > DIFF_CHARACTERS


# The shape, and the promises.


def test_the_bundle_carries_every_section(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)

    assert set(build_span_object(span)) == {
        "range",
        "commits",
        "file_changes",
        "lifecycle",
        "ast_changes",
        "cochange",
        "history",
        "absences",
        "bounds",
    }
    assert json.loads(build_span_json(span)) == build_span_object(span)


def test_the_same_range_produces_the_same_bytes(span_repo):
    repository, database = span_repo
    span = _whole_range(repository, database)

    assert build_span_json(span) == build_span_json(
        _whole_range(repository, database)
    )


def test_the_prompt_knows_about_ranges_and_bounded_diffs():
    """The two rules the set bundle's unit owes the prompt, and the form a
    changeset citation takes — present in the wording the model is shown."""
    assert "range of commits" in INSTRUCTIONS
    assert "net diff of the range" in INSTRUCTIONS
    assert "truncated" in INSTRUCTIONS
    assert "changeset" in INSTRUCTIONS
