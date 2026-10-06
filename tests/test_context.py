"""Tests for the explanation context: the evidence one commit carries.

Two promises hold this layer up, and most of what is below is about one of them.

**It is deterministic.** The same commit and the same database produce the same
bytes, which is what makes a change of model a change of nothing else. Two tests
hold it: one builds the same context twice, and one builds it from a database
written by a second ``analyze`` of the same repository.

**Every list says why it is what it is.** A range list that is empty because the
file was deleted, one that is empty because it is a merge and git printed no
diff, and one that is empty because the change touched no line are three
different facts, and the context names which one it is rather than leaving a
reader to guess.

The fixtures are real repositories. Where a rule needs a shape no fixture has —
a path git quotes, a hunk with no new-side lines — the input is written out here
and the expected answer is what git itself prints for it.
"""

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from codearchaeology import context as context_module
from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.context import (
    BINARY,
    CO_CHANGE_FILES,
    CO_CHANGE_PARTNERS,
    DELETED_FILE,
    HISTORY_COMMITS,
    MERGE_NO_DIFF,
    NOT_IN_THIS_COMMIT,
    RANGES,
    LineRange,
    _path_of,
    _ranges_by_path,
    build_context,
    build_json,
    build_object,
)
from codearchaeology.storage import connect
from sample_repo import (
    build_deep_history_repo,
    build_sample_repo,
)

# The commits of the sample fixture this file reads, by message. The repository
# is six commits: a creation, a rename, a branch, a fix, a merge, and a commit
# that adds a binary, adds a file with a Chinese name and deletes a helper.
INITIAL = "Initial commit"
RENAMED = "Move app module into the core package"
FIXED = "Fix login bug"
MERGED = "Merge branch 'feature/caching'"
FINAL = "Add logo and unicode module, drop legacy helper"

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@pytest.fixture(scope="module")
def context_repo(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture, analyzed and read, with the database beside it."""
    repository = build_sample_repo(tmp_path_factory.mktemp("context-repo"))
    database = repository.parent / "context.db"
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


def _context(repository, database, message: str):
    return build_context(repository, database, _sha(repository, database, message))


def _change(context, path: str):
    """The one entry for *path*, or a failure naming what came back instead."""
    matches = [change for change in context.changes if change.path == path]
    assert len(matches) == 1, f"expected one entry for {path}, got {len(matches)}"
    return matches[0]


def _git_ranges(repository, sha: str, path: str) -> list[tuple[int, int]]:
    """The new-side spans of *path*'s hunks, read straight out of git.

    Parsed here rather than through the module, so the two are able to disagree:
    a test that reused the parser under test would agree with it about being
    wrong.
    """
    completed = subprocess.run(
        [
            "git",
            "--no-pager",
            "show",
            "--format=",
            "--unified=0",
            "--no-color",
            sha,
            "--",
            path,
        ],
        cwd=Path(repository),
        capture_output=True,
        check=True,
    )
    spans = []
    for line in completed.stdout.decode("utf-8").splitlines():
        match = HUNK.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        if count:
            spans.append((start, start + count - 1))
    return spans


def _git(repository, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "--no-pager", *arguments],
        cwd=Path(repository),
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode("utf-8")


def _as_isoformat(value: str) -> str:
    """Git writes UTC as ``Z`` and Python writes it as ``+00:00``.

    The same instant in two spellings, so the test compares instants rather than
    the spellings: the context uses ``isoformat`` because every other JSON this
    tool emits does.
    """
    value = value.strip()
    return value[:-1] + "+00:00" if value.endswith("Z") else value


def _messages(database, shas) -> list[str]:
    """The message of each sha, in the order the shas were given."""
    connection = connect(database)
    try:
        return [
            connection.execute(
                "SELECT message FROM commits WHERE sha = ?", (sha,)
            ).fetchone()["message"]
            for sha in shas
        ]
    finally:
        connection.close()


# The promise the whole unit rests on.


def test_the_same_commit_produces_the_same_bytes(context_repo):
    repository, database = context_repo
    sha = _sha(repository, database, FIXED)
    assert build_json(build_context(repository, database, sha)) == build_json(
        build_context(repository, database, sha)
    )


def test_a_second_database_holding_the_same_history_produces_the_same_bytes(
    context_repo, tmp_path: Path
):
    """The architecture freeze's promise: the evidence layer does not move.

    Two databases written by two separate runs of ``analyze`` and ``ast`` over
    the same repository have to produce one context, or "the same evidence"
    would depend on when it was read rather than on what it holds.
    """
    repository, database = context_repo
    again = tmp_path / "again.db"
    analyze(repository, again)
    run_ast_pass(repository, again)

    sha = _sha(repository, database, FIXED)
    assert build_json(build_context(repository, again, sha)) == build_json(
        build_context(repository, database, sha)
    )


# The diff summary: where the change landed.


def test_the_ranges_are_the_hunks_git_prints(context_repo):
    repository, database = context_repo
    sha = _sha(repository, database, FIXED)

    change = _change(build_context(repository, database, sha), "core/app.py")

    assert change.diff_state == RANGES
    assert change.ranges, "this commit changes lines of this file"
    assert [(span.start, span.end) for span in change.ranges] == _git_ranges(
        repository, sha, "core/app.py"
    )


def test_a_deleted_file_is_not_a_file_whose_change_touched_no_line(context_repo):
    """The commit that drops the helper deletes it; nothing was rewritten."""
    repository, database = context_repo
    change = _change(_context(repository, database, FINAL), "legacy.py")

    assert change.change_type == "D"
    assert change.diff_state == DELETED_FILE
    assert change.ranges == ()


def test_a_binary_file_says_binary_rather_than_reading_no_ranges(context_repo):
    repository, database = context_repo
    change = _change(_context(repository, database, FINAL), "assets/logo.png")

    assert change.is_binary
    assert change.diff_state == BINARY
    assert change.ranges == ()


def test_a_merge_has_no_ranges_and_the_absence_says_which_kind(context_repo):
    """Git prints no diff for a merge, so "no ranges" here is not "no change"."""
    repository, database = context_repo
    context = _context(repository, database, MERGED)

    assert context.is_merge
    assert context.changes == ()
    assert [absence.kind for absence in context.absences] == [MERGE_NO_DIFF]
    assert context.absences[0].path is None


def test_a_root_commit_reports_its_whole_file_as_added(context_repo):
    """A commit with no parent has no diff to take; git shows the file instead."""
    repository, database = context_repo
    sha = _sha(repository, database, INITIAL)
    change = _change(build_context(repository, database, sha), "app.py")

    assert change.diff_state == RANGES
    assert [(span.start, span.end) for span in change.ranges] == _git_ranges(
        repository, sha, "app.py"
    )


def test_a_path_git_cannot_print_plainly_still_reaches_its_ranges(context_repo):
    """The fixture has a file whose name is Chinese, and a hunk inside it."""
    repository, database = context_repo
    sha = _sha(repository, database, FINAL)
    change = _change(build_context(repository, database, sha), "工具/文本.py")

    assert change.diff_state == RANGES
    assert [(span.start, span.end) for span in change.ranges] == _git_ranges(
        repository, sha, "工具/文本.py"
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # The ordinary form.
        ("+++ b/app.py", "app.py"),
        # A path with a space: git ends the line with a tab so that the path and
        # whatever follows it stay tellable apart.
        ("+++ b/my file.py\t", "my file.py"),
        # A path git cannot print plainly, quoted with the octal escapes that
        # stand for bytes. 工 is E5 B7 A5 and 文 is E6 96 87 in UTF-8.
        ('+++ "b/\\345\\267\\245/\\346\\226\\207.py"', "工/文.py"),
        # A file this side of the diff does not have.
        ("+++ /dev/null", None),
        ("+++ b/a/b/c.py", "a/b/c.py"),
    ],
)
def test_the_path_on_a_header_is_read_in_every_form_git_writes(line, expected):
    assert _path_of(line) == expected


def test_a_hunk_with_no_lines_on_the_new_side_adds_no_range():
    """``@@ -1,5 +0,0 @@`` is a deletion, and a deletion covers no new line."""
    patch = (
        b"diff --git a/gone.py b/gone.py\n"
        b"--- a/gone.py\n"
        b"+++ /dev/null\n"
        b"@@ -1,5 +0,0 @@\n"
        b"diff --git a/kept.py b/kept.py\n"
        b"--- a/kept.py\n"
        b"+++ b/kept.py\n"
        b"@@ -1,2 +1,3 @@\n"
        b"@@ -9 +10,0 @@\n"
    )

    ranges = _ranges_by_path(patch)

    assert "gone.py" not in ranges, "the deleted side has no new-side lines"
    assert ranges["kept.py"] == (LineRange(start=1, end=3),)


def test_a_file_with_no_hunks_is_recorded_as_having_none():
    """A pure rename changed no line, which is not the same as never being seen."""
    patch = (
        b"diff --git a/old.py b/new.py\n"
        b"similarity index 100%\n"
        b"rename from old.py\n"
        b"rename to new.py\n"
    )

    assert _ranges_by_path(patch) == {"new.py": ()}


# What the rest of the sections carry.


def test_the_commit_metadata_is_what_git_reported(context_repo):
    """Read out of git rather than out of the fixture's own constants."""
    repository, database = context_repo
    sha = _sha(repository, database, FIXED)
    context = build_context(repository, database, sha)

    author, email, authored, committed = _git(
        repository, "show", "-s", "--format=%an%x00%ae%x00%aI%x00%cI", sha
    ).split("\x00")
    parents = _git(repository, "rev-list", "--parents", "-n", "1", sha).split()[1:]

    assert (context.author_name, context.author_email) == (author, email)
    assert context.authored_at == _as_isoformat(authored)
    assert context.committed_at == _as_isoformat(committed)
    assert list(context.parents) == parents
    assert context.is_merge == (len(parents) > 1)
    assert context.message == FIXED


def test_a_definition_that_ended_carries_its_name_from_the_life(context_repo):
    """The commit that ends a definition has no row of its own to read it from."""
    repository, database = context_repo
    ended = [
        definition
        for definition in _context(repository, database, FINAL).definitions
        if definition.change == "deleted"
    ]

    assert [(definition.qualname, definition.kind) for definition in ended] == [
        ("parse", "function")
    ]


def test_a_definition_the_commit_created_is_in_the_context(context_repo):
    repository, database = context_repo
    created = [
        definition
        for definition in _context(repository, database, FINAL).definitions
        if definition.change == "created"
    ]

    assert [definition.qualname for definition in created] == ["拼接"]


def test_a_file_the_ast_layer_never_reads_is_not_reported_as_unreadable(
    context_repo,
):
    """A PNG has no stored version by design, not because something failed.

    Every other absence in the list is a thing that could not be read, and a
    reader is meant to trust it. A binary file reported there would be a failure
    that never happened, in the one place that is supposed to mean one.
    """
    repository, database = context_repo
    context = _context(repository, database, FINAL)

    assert "assets/logo.png" in [change.path for change in context.changes]
    assert "assets/logo.png" not in [
        absence.path for absence in context.absences
    ]


def test_the_commit_that_ends_a_file_records_that_it_was_not_there(context_repo):
    repository, database = context_repo
    absences = {
        absence.path: absence.kind
        for absence in _context(repository, database, FINAL).absences
    }

    assert absences["legacy.py"] == NOT_IN_THIS_COMMIT


def test_co_change_carries_its_denominator_and_stays_within_its_bound(context_repo):
    repository, database = context_repo
    context = _context(repository, database, FIXED)

    assert context.cochange, "the fixture's file has a life long enough to ask"
    for report in context.cochange:
        assert report.analyzed_commits > 0
        assert len(report.partners) <= CO_CHANGE_PARTNERS
        for partner in report.partners:
            assert 0 < partner.score <= 1
            assert partner.shared_commits <= report.analyzed_commits
    assert context.bounds.co_change_partners == CO_CHANGE_PARTNERS
    assert context.bounds.co_change_files == CO_CHANGE_FILES


def test_the_history_window_reports_the_whole_it_was_drawn_from(context_repo):
    repository, database = context_repo
    for message in (RENAMED, FIXED, FINAL):
        context = _context(repository, database, message)
        for entry in context.history:
            assert len(entry.commits) == min(entry.total, HISTORY_COMMITS)


def test_the_window_fills_and_the_omission_is_counted(tmp_path: Path):
    """A cap nothing fills is a cap nothing checks, so the fixture is longer.

    ``deep.py`` is edited eight times before the commit under test, and the
    window holds five — so the count of what was left out is the difference, and
    it has to be in the context rather than worked out from the size of a list.

    The window is the *most recent* few. Keeping the first five instead would
    pass a test that only counted them, which is why the messages are checked:
    the three the window drops are the three oldest.
    """
    repository = build_deep_history_repo(tmp_path / "deep")
    database = tmp_path / "deep.db"
    analyze(repository, database)

    context = build_context(
        repository, database, _sha(repository, database, "Edit deep.py, pass 8")
    )
    entry = next(item for item in context.history if item.path == "deep.py")

    assert entry.total == 8
    assert len(entry.commits) == HISTORY_COMMITS
    assert context.bounds.history_commits_omitted == 8 - HISTORY_COMMITS
    assert _messages(database, [commit.sha for commit in entry.commits]) == [
        "Edit deep.py, pass 3",
        "Edit deep.py, pass 4",
        "Edit deep.py, pass 5",
        "Edit deep.py, pass 6",
        "Edit deep.py, pass 7",
    ]


# The shape, and the boundary the architecture freeze promised.


def test_the_json_is_the_object_it_says_it_is(context_repo):
    repository, database = context_repo
    context = _context(repository, database, FIXED)

    assert json.loads(build_json(context)) == build_object(context)


def test_the_context_carries_every_section_the_contract_names(context_repo):
    repository, database = context_repo
    assert set(build_object(_context(repository, database, FINAL))) == {
        "commit",
        "file_changes",
        "lifecycle",
        "ast_changes",
        "cochange",
        "history",
        "absences",
        "bounds",
    }


def test_the_module_does_not_reach_for_a_network():
    """The architecture freeze §3, held the way ``definitions.py`` holds its own.

    The offline path has to run on a machine with no network, no key and no
    provider, and the only way to keep that true as the module grows is to fix
    the imports it may have. ``urllib``, ``http``, ``socket``, ``ssl`` and
    ``smtplib`` are all absent from the list below, so reaching for one fails
    here rather than in a user's offline terminal.

    ``subprocess`` is on the list because this module runs git — the one thing
    the database does not hold is where a change landed, and git is where that
    lives.
    """
    tree = ast.parse(Path(context_module.__file__).read_bytes())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])

    assert imported <= {
        "json",
        "re",
        "subprocess",
        "collections",
        "dataclasses",
        "datetime",
        "pathlib",
        "codearchaeology",
    }
