"""Build the explanation context for one commit: the evidence, and nothing else.

This is the layer between the stored history and a model. It reads the database,
asks git for the two things the database does not hold — where in each file the
change landed, and the patch text itself — and assembles all of it into a value
an explanation is written from.

**No model takes part in building it.** The same commit and the same database
produce the same bytes, which is what makes this layer testable, and what keeps a
change of model from being a change of evidence: the architecture freeze puts
every provider behind one interface, and this module sits above that line, where
nothing reaches a network.

The context is sectioned the way the evidence contract sections the evidence:
what happened, what the files' lives say, what the structure layer derived, what
the statistics say, and what could not be read. Four rules shape the rest.

* **The facts about this commit are not capped; the context around them is.** A
  file list or a definition list that stopped early would hide the answer to the
  question being asked. Co-change and the earlier commits are context rather than
  the subject, so they get a ceiling — and every ceiling is reported in
  ``bounds``, because a context that quietly left rows out reads exactly like a
  complete one.
* **The diff is the one fact the bundle itself bounds.** A repository can make
  one file's patch arbitrarily large without the commit being large in any other
  way — a lockfile, a vendored copy — and the contract settled this before the
  reader existed: the diff is available and bounded, and the bound is stated on
  every row it cut, so a partial patch never reads as a whole one.
* **The fourth absence the contract names is not repeated here.** "A definition
  is gone" is the ``deleted`` entries in ``definitions``; writing it into
  ``absences`` as well would be one fact in two places, free to disagree with
  itself.
* **A merge has no ranges, and that is not the same as no changes.** Git prints
  no diff for a merge, so the ranges are unknown for a reason the context states
  rather than leaves to be guessed at.
"""

import json
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codearchaeology.analysis import open_analysis
from codearchaeology.ast_pass import PYTHON_SUFFIX
from codearchaeology.cochange import (
    DEFAULT_MIN_SHARED,
    CoChangeReport,
    analyze_cochange,
)
from codearchaeology.commit import load_commit
from codearchaeology.definition_history import (
    BlindSpot,
    DefinitionEvent,
    DefinitionLife,
    FileHistory,
    NOT_IN_TREE,
    NOT_STORED,
    build_histories,
)
from codearchaeology.history import Commit, FileChange, GitError
from codearchaeology.lifecycle import (
    DELETED,
    Lifecycle,
    LifecycleEvent,
    build_lifecycles,
)
from codearchaeology.statistics import summarize
from codearchaeology.storage import (
    read_definitions_for_paths,
    read_stored_commits,
    read_versions_for_paths,
)

# What the context around the commit is allowed to carry. The commit's own files
# and definitions are not capped: they are the subject, not the context.
CO_CHANGE_PARTNERS = 5
CO_CHANGE_FILES = 5
HISTORY_COMMITS = 5

# The ceiling on one file's patch text, the one fact the bundle bounds. It is the
# message's own ceiling — a file's patch may cost a reader no more than a long
# commit message — and the measurement behind it: every shape
# `benchmarks/context_benchmark.py` builds stays whole under it except the two
# single-file extremes (22,297 and 32,727 characters), while the largest patches
# a real repository produces — a lockfile rewrite measured at 110,944 — are cut
# to their first hundred lines with the whole size stated on the row. Counted in
# characters rather than bytes for the same reason the message cap is: the
# ceiling is about how much a reader has to read.
DIFF_CHARACTERS = 4000

# Why a file has no changed line ranges. Four answers, kept apart because a
# reader has to be able to tell "the change landed nowhere" from "nobody looked".
RANGES = "ranges"
DELETED_FILE = "deleted_file"
BINARY = "binary"
NO_RANGES = "no_ranges_recorded"
NO_DIFF = "no_diff_recorded"

# The absences the evidence contract names, minus the one that is a deletion.
# ``binary_no_text`` is v0.6's: the contract's amendment makes the binary case a
# named absence rather than an empty diff, so "no text exists" is never read as
# "nothing happened".
MERGE_NO_DIFF = "merge_no_diff"
PARSE_FAILED = "parse_failed"
NO_VERSION_STORED = "no_version_stored"
NOT_IN_THIS_COMMIT = "not_in_this_commit"
BINARY_NO_TEXT = "binary_no_text"

# `git show` is used rather than `git diff` so that a root commit needs no second
# code path: with no parent it prints the whole file as an addition, which is
# what diffing against an empty tree would give. `--no-ext-diff` and
# `--no-textconv` are there for determinism rather than for looks — a diff driver
# or a text conversion filter configured on the machine would otherwise change
# the answer, and the context has to be the same on every machine.
SHOW_ARGUMENTS = (
    "--no-pager",
    "-c",
    "core.quotePath=false",
    "show",
    "--format=",
    "--unified=0",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
)

# The patch text is read with `git diff` rather than derived from the show above,
# because a diff is what git printed and `difflib` is not git's algorithm: the
# same two blobs can hunk differently under it, and the fact layer would be
# holding a new thing in an old name. Every axis a configuration could move is
# pinned: colour, drivers and filters as above; the context width at git's own
# default; rename detection on at the 50% the analysis already uses; and
# `--full-index` plus explicit prefixes so `core.abbrev`, `diff.noprefix` and
# `diff.mnemonicPrefix` cannot change the bytes from one machine to the next.
# The empty tree stands in for the parent of a root commit, which is exactly
# what `git show` prints for one: the whole file as an addition.
DIFF_ARGUMENTS = (
    "--no-pager",
    "-c",
    "core.quotePath=false",
    "diff",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--unified=3",
    "--find-renames=50%",
    "--full-index",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

NEW_FILE = "+++ "
OLD_FILE = "--- "
# A rename whose content did not change gets no ``---``/``+++`` pair at all:
# git prints the two rename headers and stops. Without reading this line such a
# file would look like one nobody had looked at, when git did look and the answer
# is that no line moved.
RENAME_TO = "rename to "
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

ESCAPES = {"n": b"\n", "t": b"\t", "r": b"\r", "\\": b"\\", '"': b'"'}


@dataclass(frozen=True, slots=True)
class LineRange:
    """One hunk's span in the file as it stands after the change, both ends in."""

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class DiffText:
    """One file's patch as git printed it, bounded and saying so on itself.

    ``text`` is the file's section of the diff verbatim — headers, hunks and all
    — or the first ``DIFF_CHARACTERS`` characters of it when the ceiling cut it.
    ``characters`` is how much of the patch is held, ``characters_total`` how
    much of it there was, and ``truncated`` which of the two the reader is
    looking at: the contract forbids presenting a bounded diff as the whole one,
    and the row is where that is said.
    """

    text: str
    characters: int
    characters_total: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class FileContext:
    """One file this commit touched, and where in it the change landed.

    ``ranges`` is the diff summary: the new-side spans of every hunk, which is
    what lets a reader put the change beside the definitions it fell inside.
    ``diff`` is the patch text itself, bounded per file, or ``None`` where none
    exists — a binary file, a merge, a path git printed nothing for — each of
    which ``diff_state`` or the absences name rather than leave as an empty
    string standing in for text.
    """

    path: str
    change_type: str
    old_path: str | None
    added_lines: int | None
    deleted_lines: int | None
    similarity: int | None
    is_binary: bool
    diff_state: str
    ranges: tuple[LineRange, ...]
    diff: DiffText | None


@dataclass(frozen=True, slots=True)
class LifecycleContext:
    """The life of one touched file, from its birth up to this commit."""

    path: str
    path_history: tuple[str, ...]
    commits: int
    modifications: int
    renames: int
    additions: int
    deletions: int
    net_change: int
    created_at: str
    created_sha: str
    last_modified_at: str | None
    last_modified_sha: str | None
    deleted: bool
    deleted_at: str | None
    deleted_sha: str | None


@dataclass(frozen=True, slots=True)
class BlindSpotContext:
    """A version of a file that nobody could read, and what is known about why."""

    commit_sha: str
    committed_at: str
    reason: str


@dataclass(frozen=True, slots=True)
class DefinitionContext:
    """One definition this commit created, modified or ended.

    ``occurrence`` is which definition of that name this is, counted in the order
    the file lists them. It is 0 for nearly every name and it is here for the
    ones where it is not: one file can define the same name twice, and without it
    the two entries would be two records nothing could tell apart.

    ``commit_sha`` is the commit the event happened at. For one commit's bundle
    it is the context's own sha and reads redundant; for a range's it is the
    only place an event says when it happened, and one object serves both.

    ``previous_commit_sha`` is the version this one was compared against — the
    nearest earlier version of the file that could be read. ``blind_spots`` are
    the unreadable versions in between, which is why an event that spans one
    cannot say when inside the span it happened.
    """

    change: str
    commit_sha: str
    path: str
    kind: str
    qualname: str
    occurrence: int
    lineno: int | None
    end_lineno: int | None
    decorators: tuple[str, ...]
    previous_commit_sha: str | None
    blind_spots: tuple[BlindSpotContext, ...]


@dataclass(frozen=True, slots=True)
class Partner:
    """One file that changes alongside another, and the counts behind the score."""

    path: str
    shared_commits: int
    score: float


@dataclass(frozen=True, slots=True)
class CoChangeContext:
    """What usually changes with one of this commit's files.

    A statistic over the file's whole life, not a fact about this commit: the
    counts are checkable and the reading of them is not. ``hidden_pairs`` says
    how many pairs the minimum-shared filter left out, so "none found" and "none
    shown" stay two different answers.
    """

    path: str
    analyzed_commits: int
    excluded_large_commits: int
    hidden_pairs: int
    partners: tuple[Partner, ...]


@dataclass(frozen=True, slots=True)
class EarlierCommit:
    """One earlier commit that touched the same file."""

    sha: str
    committed_at: str
    change_type: str
    added_lines: int | None
    deleted_lines: int | None


@dataclass(frozen=True, slots=True)
class HistoryContext:
    """The commits before this one that touched the same file.

    ``total`` is how many there were and ``commits`` is the window that was
    kept — the most recent ones, in the order they happened. The two differ only
    when the window was full, which is a fact the model is given rather than one
    it has to notice.
    """

    path: str
    total: int
    commits: tuple[EarlierCommit, ...]


@dataclass(frozen=True, slots=True)
class Absence:
    """Something the bundle does not hold, named so the edge is visible.

    ``kind`` is which kind of not-there it is. The kinds are kept apart because
    they look alike and mean different things: a file nobody could read is not a
    file that was not there, a merge git printed no diff for is not a commit that
    changed nothing, and a binary file's missing patch is not a failure at all.
    """

    kind: str
    path: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class Bounds:
    """What the context capped, so nothing was left out silently."""

    co_change_partners: int
    co_change_files: int
    co_change_files_omitted: int
    history_commits: int
    history_commits_omitted: int


@dataclass(frozen=True, slots=True)
class CommitContext:
    """Everything the model may see about one commit, and nothing else."""

    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: str
    committed_at: str
    message: str
    is_merge: bool
    changes: tuple[FileContext, ...]
    lifecycle: tuple[LifecycleContext, ...]
    definitions: tuple[DefinitionContext, ...]
    cochange: tuple[CoChangeContext, ...]
    history: tuple[HistoryContext, ...]
    absences: tuple[Absence, ...]
    bounds: Bounds


def build_context(repository_root, database, sha: str) -> CommitContext:
    """Assemble the context for the stored commit *sha* names.

    A prefix is enough, as it is everywhere else, and an ambiguous one is
    refused rather than guessed at. Nothing here reads the network or a model.
    """
    commit = load_commit(repository_root, database, sha)

    with open_analysis(repository_root, database) as connection:
        commits = read_stored_commits(connection)
        lifecycles = build_lifecycles(commits)
        lives = _lives_of(lifecycles, commit)
        names = tuple(sorted({name for life in lives for name in life.path_history}))
        versions = read_versions_for_paths(connection, names)
        definitions = read_definitions_for_paths(connection, names)

    histories = build_histories(lives, versions, definitions)
    ranges = _diff_ranges(repository_root, commit)
    diffs = _diff_texts(repository_root, commit)
    ranked = _ranked(lives)
    history = _history(lives, commit)

    return CommitContext(
        sha=commit.sha,
        parents=commit.parents,
        author_name=commit.author_name,
        author_email=commit.author_email,
        authored_at=commit.authored_at.isoformat(),
        committed_at=commit.committed_at.isoformat(),
        message=commit.message,
        is_merge=commit.is_merge,
        changes=_changes(commit, ranges, diffs),
        lifecycle=lifecycle_contexts(lives),
        definitions=_definitions(histories, commit),
        cochange=_cochange(commits, ranked),
        history=history,
        absences=_absences(histories, commit),
        bounds=_bounds(ranked, history),
    )


def build_object(context: CommitContext) -> dict:
    """The context as the fields a model is shown and a program reads.

    This object is the whole of the input. Whatever sends it to a model sends
    this and nothing else, and whatever prints it offline prints the same bytes,
    which is what keeps the two paths from drifting apart.
    """
    return {
        "commit": {
            "sha": context.sha,
            "parents": list(context.parents),
            "author_name": context.author_name,
            "author_email": context.author_email,
            "authored_at": context.authored_at,
            "committed_at": context.committed_at,
            "message": context.message,
            "is_merge": context.is_merge,
        },
        "file_changes": [
            {
                "path": change.path,
                "change_type": change.change_type,
                "old_path": change.old_path,
                "added_lines": change.added_lines,
                "deleted_lines": change.deleted_lines,
                "similarity": change.similarity,
                "is_binary": change.is_binary,
                "diff_state": change.diff_state,
                "ranges": [
                    {"start": span.start, "end": span.end} for span in change.ranges
                ],
                "diff": None
                if change.diff is None
                else {
                    "text": change.diff.text,
                    "characters": change.diff.characters,
                    "characters_total": change.diff.characters_total,
                    "truncated": change.diff.truncated,
                },
            }
            for change in context.changes
        ],
        "lifecycle": [
            {
                "path": life.path,
                "path_history": list(life.path_history),
                "commits": life.commits,
                "modifications": life.modifications,
                "renames": life.renames,
                "additions": life.additions,
                "deletions": life.deletions,
                "net_change": life.net_change,
                "created_at": life.created_at,
                "created_sha": life.created_sha,
                "last_modified_at": life.last_modified_at,
                "last_modified_sha": life.last_modified_sha,
                "deleted": life.deleted,
                "deleted_at": life.deleted_at,
                "deleted_sha": life.deleted_sha,
            }
            for life in context.lifecycle
        ],
        "ast_changes": [
            {
                "change": definition.change,
                "commit_sha": definition.commit_sha,
                "path": definition.path,
                "kind": definition.kind,
                "qualname": definition.qualname,
                "occurrence": definition.occurrence,
                "lineno": definition.lineno,
                "end_lineno": definition.end_lineno,
                "decorators": list(definition.decorators),
                "previous_commit_sha": definition.previous_commit_sha,
                "blind_spots": [
                    {
                        "commit_sha": spot.commit_sha,
                        "committed_at": spot.committed_at,
                        "reason": spot.reason,
                    }
                    for spot in definition.blind_spots
                ],
            }
            for definition in context.definitions
        ],
        "cochange": [
            {
                "path": report.path,
                "analyzed_commits": report.analyzed_commits,
                "excluded_large_commits": report.excluded_large_commits,
                "hidden_pairs": report.hidden_pairs,
                "partners": [
                    {
                        "path": partner.path,
                        "shared_commits": partner.shared_commits,
                        "score": partner.score,
                    }
                    for partner in report.partners
                ],
            }
            for report in context.cochange
        ],
        "history": [
            {
                "path": entry.path,
                "total": entry.total,
                "commits": [
                    {
                        "sha": earlier.sha,
                        "committed_at": earlier.committed_at,
                        "change_type": earlier.change_type,
                        "added_lines": earlier.added_lines,
                        "deleted_lines": earlier.deleted_lines,
                    }
                    for earlier in entry.commits
                ],
            }
            for entry in context.history
        ],
        "absences": [
            {"kind": absence.kind, "path": absence.path, "detail": absence.detail}
            for absence in context.absences
        ],
        "bounds": {
            "co_change_partners": context.bounds.co_change_partners,
            "co_change_files": context.bounds.co_change_files,
            "co_change_files_omitted": context.bounds.co_change_files_omitted,
            "history_commits": context.bounds.history_commits,
            "history_commits_omitted": context.bounds.history_commits_omitted,
        },
    }


def build_json(context: CommitContext) -> str:
    """The context as JSON — the bytes a model is shown and the offline path prints.

    ``ensure_ascii`` is off for the same reason it is off everywhere else in this
    tool: a path written in Chinese is a path, not an escape sequence, and the
    model reading it should see what the repository holds.
    """
    return json.dumps(build_object(context), indent=2, ensure_ascii=False)


def _lives_of(lifecycles: Iterable[Lifecycle], commit: Commit) -> tuple[Lifecycle, ...]:
    """Every file life this commit touched, in the order the commit lists them.

    Indexed by commit rather than filtered per path: a life is the only thing
    that knows a file across its renames, so the question "what did this commit
    touch" is answered by the events, not by the paths.
    """
    touched: dict[str, list[Lifecycle]] = {}
    for life in lifecycles:
        for event in life.events:
            if event.commit_sha == commit.sha:
                touched.setdefault(event.path, []).append(life)

    found: list[Lifecycle] = []
    seen: set[int] = set()
    for change in commit.changes:
        for life in touched.get(change.path, ()):
            if id(life) not in seen:
                seen.add(id(life))
                found.append(life)
    return tuple(found)


def _changes(
    commit: Commit,
    ranges: Mapping[str, tuple[LineRange, ...]] | None,
    diffs: Mapping[str, DiffText] | None,
):
    return tuple(_change(change, ranges, diffs) for change in commit.changes)


def _change(
    change: FileChange,
    ranges: Mapping[str, tuple[LineRange, ...]] | None,
    diffs: Mapping[str, DiffText] | None,
):
    if ranges is None:
        # A merge. Git prints no diff for one, so nothing here says where the
        # change landed — and that is not the same as a change that landed
        # nowhere, which is why the state is its own word.
        return FileContext(
            path=change.path,
            change_type=change.change_type,
            old_path=change.old_path,
            added_lines=change.added_lines,
            deleted_lines=change.deleted_lines,
            similarity=change.similarity,
            is_binary=change.is_binary,
            diff_state=NO_DIFF,
            ranges=(),
            diff=None,
        )

    if change.change_type == DELETED:
        state, spans = DELETED_FILE, ()
    elif change.is_binary:
        state, spans = BINARY, ()
    elif change.path in ranges:
        state, spans = RANGES, ranges[change.path]
    else:
        # Git printed nothing for a path the commit says it touched. The state
        # says no ranges were recorded rather than that there are none, because
        # the two are different and only one of them is known here.
        state, spans = NO_RANGES, ()

    # A binary file's absence is named in ``absences`` and never stands in as an
    # empty diff; every other touched path carries its patch when git printed
    # one, deletions included — the removal patch is evidence, not a blank.
    diff = None if change.is_binary else diffs.get(change.path) if diffs else None

    return FileContext(
        path=change.path,
        change_type=change.change_type,
        old_path=change.old_path,
        added_lines=change.added_lines,
        deleted_lines=change.deleted_lines,
        similarity=change.similarity,
        is_binary=change.is_binary,
        diff_state=state,
        ranges=spans,
        diff=diff,
    )


def lifecycle_contexts(
    lives: Iterable[Lifecycle],
) -> tuple[LifecycleContext, ...]:
    """Each touched life summarised, for whichever unit is being built.

    Public because the range bundle summarises the same lives through the same
    walk — one implementation, so a summary cannot mean one thing for a commit
    and another for a range.
    """
    contexts = []
    for life in lives:
        statistics = summarize(life)
        ended = life.deleted
        contexts.append(
            LifecycleContext(
                path=life.current_path,
                path_history=life.path_history,
                commits=statistics.commits,
                modifications=statistics.modifications,
                renames=statistics.renames,
                additions=statistics.additions,
                deletions=statistics.deletions,
                net_change=statistics.net_change,
                created_at=statistics.created_at.isoformat(),
                created_sha=statistics.created_sha,
                last_modified_at=_timestamp(statistics.last_modified_at),
                last_modified_sha=statistics.last_modified_sha,
                deleted=statistics.is_deleted,
                deleted_at=_timestamp(None if ended is None else ended.committed_at),
                deleted_sha=None if ended is None else ended.commit_sha,
            )
        )
    return tuple(contexts)


def _definitions(
    histories: Iterable[FileHistory], commit: Commit
) -> tuple[DefinitionContext, ...]:
    return definition_events(histories, (commit.sha,))


def definition_events(
    histories: Iterable[FileHistory], shas: Iterable[str]
) -> tuple[DefinitionContext, ...]:
    """The definition events at any of the given commits, in the walk's order.

    The one collector both units share: a commit asks for its own sha and a
    range for the whole set, and the events come back in the order the lives
    were walked — which is the order they happened in.

    Deletions are here rather than in the absences: a definition that is gone is
    derived from a row that is missing, and the derivation is the finding.

    The kind and the name are read from the life rather than from the event. An
    event is a moment in a definition's life, and the moment a definition ends
    has no row of its own to read them from — while the life knows both for as
    long as it lasts.
    """
    wanted = frozenset(shas)
    found: list[DefinitionContext] = []
    for history in histories:
        for life in history.lives:
            for event in life.events:
                if event.commit_sha in wanted:
                    found.append(_definition(life, event))
    return tuple(found)


def _definition(life: DefinitionLife, event: DefinitionEvent) -> DefinitionContext:
    return DefinitionContext(
        change=event.change,
        commit_sha=event.commit_sha,
        path=event.path,
        kind=life.kind,
        qualname=life.qualname,
        occurrence=life.occurrence,
        lineno=event.lineno,
        end_lineno=event.end_lineno,
        decorators=event.decorators,
        previous_commit_sha=event.previous_commit_sha,
        blind_spots=tuple(
            BlindSpotContext(
                commit_sha=spot.commit_sha,
                committed_at=spot.committed_at.isoformat(),
                reason=spot.reason,
            )
            for spot in event.blind_spots
        ),
    )


def _ranked(lives: Iterable[Lifecycle]) -> tuple[tuple[Lifecycle, int], ...]:
    """The lives worth asking about co-change for, longest-lived first.

    A file with fewer commits than the minimum shared count has no co-change to
    report — every pair would be filtered out — so asking about it would pay for
    a walk of the whole history to be told nothing. The ones left are ordered by
    how much history they have, ties broken by name, so which files get asked
    about is the same choice on every run.
    """
    counted = [(life, summarize(life).commits) for life in lives]
    long_enough = [pair for pair in counted if pair[1] >= DEFAULT_MIN_SHARED]
    long_enough.sort(key=lambda pair: (-pair[1], pair[0].current_path))
    return tuple(long_enough)


def cochange_contexts(
    commits: Iterable[Commit], lives: Iterable[Lifecycle]
) -> tuple[CoChangeContext, ...]:
    """What usually changes with these files, for whichever unit is being built.

    Public for the same reason :func:`lifecycle_contexts` is: the range bundle
    asks the same question of the same whole lives, and one implementation is
    the only way the answer stays one answer.
    """
    return _cochange(commits, _ranked(lives))


def _cochange(
    commits: Iterable[Commit], ranked: Iterable[tuple[Lifecycle, int]]
) -> tuple[CoChangeContext, ...]:
    """What usually changes with this commit's files, for the few that can say."""
    reports: list[CoChangeContext] = []
    for life, _ in tuple(ranked)[:CO_CHANGE_FILES]:
        for report in analyze_cochange(commits, life.current_path):
            # The name can belong to more than one life, so the report is matched
            # by the life's whole path history rather than by the name it was
            # asked about. Taking the first would answer for a different file
            # whenever a name was reused.
            if report.path_history == life.path_history:
                reports.append(_cochange_report(report))
                break
    return tuple(reports)


def _cochange_report(report: CoChangeReport) -> CoChangeContext:
    return CoChangeContext(
        path=report.path,
        analyzed_commits=report.analyzed_commits,
        excluded_large_commits=report.excluded_large_commits,
        hidden_pairs=report.hidden_pairs,
        partners=tuple(
            Partner(
                path=partner.path,
                shared_commits=partner.shared_commits,
                score=partner.score,
            )
            for partner in report.co_changes[:CO_CHANGE_PARTNERS]
        ),
    )


def _history(lives: Iterable[Lifecycle], commit: Commit) -> tuple[HistoryContext, ...]:
    """The commits before this one that touched each of the same files.

    The window is the most recent few, kept in the order they happened, and
    ``total`` says how many there were — so a reader can tell a file with three
    earlier commits from one whose window filled up and stopped early.
    """
    entries = []
    for life in lives:
        earlier = _before(life, commit)
        entries.append(
            HistoryContext(
                path=life.current_path,
                total=len(earlier),
                commits=tuple(
                    EarlierCommit(
                        sha=event.commit_sha,
                        committed_at=event.committed_at.isoformat(),
                        change_type=event.change_type,
                        added_lines=event.added_lines,
                        deleted_lines=event.deleted_lines,
                    )
                    for event in earlier[-HISTORY_COMMITS:]
                ),
            )
        )
    return tuple(entries)


def _before(life: Lifecycle, commit: Commit) -> list[LifecycleEvent]:
    """The events of *life* that happened before *commit*, in the order they did.

    By position in the life rather than by timestamp: two commits can share a
    second, and the walk that built the life already settled which came first —
    the same reason ``build_lifecycles`` follows the parent links instead of
    sorting by time. A life this commit is not part of has nothing before it.
    """
    for index, event in enumerate(life.events):
        if event.commit_sha == commit.sha:
            return list(life.events[:index])
    return []


def _absences(histories: Iterable[FileHistory], commit: Commit) -> tuple[Absence, ...]:
    """What could not be read, and which kind of not-read it is.

    A merge is recorded first because it is a property of the commit rather than
    of any file in it, and the files follow in path order so the list is the same
    bytes every time it is built.

    A binary path is recorded as ``binary_no_text`` and as nothing else. Its
    counts were never read and its blobs have no text, which is a fact about the
    diff rather than a failure of the analysis — so it is named under its own
    word, never under the words that mean something went wrong.

    A path the AST layer never reads is otherwise left out. That layer reads
    Python and nothing else, so a Markdown file has no stored version by design
    — and reporting it here as "no version was stored for this commit" would be a
    failure that never happened, in the one list a reader is meant to trust about
    what is missing. The file is still in ``changes``; what is absent is only the
    claim that something went wrong.
    """
    found: list[Absence] = []
    if commit.is_merge:
        found.append(
            Absence(
                kind=MERGE_NO_DIFF,
                path=None,
                detail="git prints no diff for a merge commit",
            )
        )

    for change in commit.changes:
        if change.is_binary:
            found.append(
                Absence(
                    kind=BINARY_NO_TEXT,
                    path=change.path,
                    detail="the file is binary, so git prints no diff text",
                )
            )

    for history in histories:
        for version in history.versions:
            if version.commit_sha != commit.sha or not isinstance(version, BlindSpot):
                continue
            if not version.path.endswith(PYTHON_SUFFIX):
                continue
            found.append(
                Absence(
                    kind=_absence_kind(version.reason),
                    path=version.path,
                    detail=version.reason,
                )
            )

    found.sort(key=lambda absence: (absence.path or "", absence.kind))
    return tuple(found)


def _absence_kind(reason: str) -> str:
    if reason == NOT_STORED:
        return NO_VERSION_STORED
    if reason == NOT_IN_TREE:
        return NOT_IN_THIS_COMMIT
    return PARSE_FAILED


def _bounds(
    ranked: Iterable[tuple[Lifecycle, int]], history: Iterable[HistoryContext]
) -> Bounds:
    """What the context capped, counted from the same values the caps were applied to.

    The counts come from ``ranked`` and ``history`` rather than from a second
    walk, so a bound can never describe a different set from the one that was
    actually trimmed.
    """
    return Bounds(
        co_change_partners=CO_CHANGE_PARTNERS,
        co_change_files=CO_CHANGE_FILES,
        co_change_files_omitted=max(0, len(tuple(ranked)) - CO_CHANGE_FILES),
        history_commits=HISTORY_COMMITS,
        history_commits_omitted=sum(
            max(0, entry.total - HISTORY_COMMITS) for entry in history
        ),
    )


def diff_ranges(
    repository_root, commit: Commit
) -> dict[str, tuple[LineRange, ...]] | None:
    """Where each file's change landed, for a caller outside this module.

    The bundle reads this for the commit it explains, and a memory's ``range``
    citation is a span of one commit's diff — so holding one against the
    evidence needs the same reading. The work is :func:`_diff_ranges`'s; this is
    a name for it another module may use, so that nothing has to import a
    private one.
    """
    return _diff_ranges(repository_root, commit)


def _diff_ranges(
    repository_root, commit: Commit
) -> dict[str, tuple[LineRange, ...]] | None:
    """Where each file's change landed, or ``None`` for a merge.

    ``None`` is not "no changes". Git prints no diff for a merge, so the ranges
    are unknown, and the caller says so in the absences rather than letting an
    empty list read as a commit that touched nothing.
    """
    if commit.is_merge:
        return None
    return _ranges_by_path(_run_git(repository_root, *SHOW_ARGUMENTS, commit.sha))


def _ranges_by_path(output: bytes) -> dict[str, tuple[LineRange, ...]]:
    """Every file the diff names, with the new-side spans of its hunks.

    A file git printed no hunks for still gets an entry with no ranges, and a
    pure rename is the case that needs it: git prints its two rename headers and
    no hunk at all, and an entry with no ranges says "the change moved no line"
    where a missing entry would say "nobody looked". The two are different
    answers and only one of them is true.
    """
    ranges: dict[str, list[LineRange]] = {}
    path: str | None = None

    for line in output.decode("utf-8", errors="surrogateescape").splitlines():
        if line.startswith(NEW_FILE):
            path = _path_of(line)
            if path is not None:
                ranges.setdefault(path, [])
            continue
        if line.startswith(RENAME_TO) and path is None:
            # Only when no ``+++`` has named the file yet: a rename that also
            # rewrote lines prints both headers, and the ``+++`` one is the side
            # the ranges are measured on.
            path = _after(line, RENAME_TO)
            ranges.setdefault(path, [])
            continue
        if path is None:
            continue
        match = HUNK.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        if count:
            ranges[path].append(LineRange(start=start, end=start + count - 1))

    return {name: tuple(found) for name, found in ranges.items()}


def _diff_texts(repository_root, commit: Commit) -> dict[str, DiffText] | None:
    """Each file's patch as git printed it, bounded per file, or ``None`` for a merge.

    The diff is the unit's own — this commit against its parent, or against the
    empty tree when there is no parent — read in one invocation and split by
    file. Git computes it and the tool does not: the standard library's differ is
    not git's algorithm, and the fact layer's diff is what git printed. A merge
    keeps the absence it has always had; no parent is chosen for it here.
    """
    if commit.is_merge:
        return None
    base = commit.parents[0] if commit.parents else EMPTY_TREE
    return diff_texts_between(repository_root, base, commit.sha)


def diff_texts_between(repository_root, base: str, tip: str) -> dict[str, DiffText]:
    """Each file's patch for an explicit pair of endpoints, bounded per file.

    The one reader both units share: a commit is its parent and it, and a range
    is its resolved base and tip — the same invocation, the same pinned flags,
    the same per-file ceiling, so the diff of a one-commit range and the diff of
    that commit are the same bytes.
    """
    return _diffs_by_path(_run_git(repository_root, *DIFF_ARGUMENTS, base, tip))


# The span reader's twin, at git's own default context width of zero: the hunk
# headers only, which is all a span needs to say where the net change landed.
SPAN_RANGE_ARGUMENTS = (
    "--no-pager",
    "-c",
    "core.quotePath=false",
    "diff",
    "--unified=0",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--find-renames=50%",
)


def diff_ranges_between(repository_root, base: str, tip: str):
    """Where each file's change landed between two endpoints, new side only."""
    return _ranges_by_path(
        _run_git(repository_root, *SPAN_RANGE_ARGUMENTS, base, tip)
    )


def _diffs_by_path(output: bytes) -> dict[str, DiffText]:
    """Every file section of the diff, bounded and saying on itself what it holds.

    A section is everything from one ``diff --git`` line to the next, verbatim —
    headers, index, hunks, and the two rename lines a pure rename stands as. The
    body of a patch cannot start a section: a hunk line beginning ``@@`` ends the
    header block, and every body line carries its own ``+``, ``-`` or space, so
    the split lands where git put the seams.
    """
    text = output.decode("utf-8", errors="surrogateescape")
    parts = text.split("\ndiff --git ")
    sections = []
    for index, part in enumerate(parts):
        if not part.strip():
            continue
        section = part if index == 0 else "diff --git " + part
        # The newline the split consumed is the one that ended this section's
        # last line — it belongs here, not to the header of the next section.
        if index < len(parts) - 1:
            section += "\n"
        sections.append(section)

    found: dict[str, DiffText] = {}
    for section in sections:
        path = _diff_section_path(section)
        if path is not None:
            found[path] = _bounded_diff(section)
    return found


def _diff_section_path(section: str) -> str | None:
    """The path a diff section belongs to, under the name the bundle's row carries.

    The ``+++`` side is the side the ranges are measured on, so it decides here
    too: a modification, an addition and a rename that also moved lines all carry
    it. A deletion prints ``+++ /dev/null`` and keeps its name on the ``---``
    side; a pure rename prints no ``+++``/``---`` pair at all and is named by its
    ``rename to`` line. A binary file prints none of these, so its section stays
    unclaimed — the absence names it, and no blank stands in as its text.

    Only the header block is read: the scan stops at the first hunk line,
    because a body line whose content begins ``-- `` or ``++ `` would otherwise
    read as a header wearing its own sign.
    """
    lines = section.splitlines()
    for line in lines:
        if HUNK.match(line):
            break
        if line.startswith(NEW_FILE):
            new_side = _path_of(line)
            if new_side is not None:
                return new_side
            break

    for line in lines:
        if HUNK.match(line):
            break
        if line.startswith(RENAME_TO):
            return _after(line, RENAME_TO)

    for line in lines:
        if HUNK.match(line):
            break
        if line.startswith(OLD_FILE):
            return _path_of_old(line)
    return None


def _path_of_old(line: str) -> str | None:
    """The path on a ``---`` line, or ``None`` when that side of the diff is empty.

    The mirror of :func:`_path_of` — the same trailing tab, the same quoting,
    undone the same way — for the side a deleted file keeps its name on.
    """
    value = _after(line, OLD_FILE)
    if value == "/dev/null":
        return None
    return value[2:] if value.startswith("a/") else value


def _bounded_diff(section: str) -> DiffText:
    """The section whole, or cut at the ceiling, saying on itself which it is."""
    whole = len(section)
    if whole <= DIFF_CHARACTERS:
        return DiffText(
            text=section, characters=whole, characters_total=whole, truncated=False
        )
    return DiffText(
        text=section[:DIFF_CHARACTERS],
        characters=DIFF_CHARACTERS,
        characters_total=whole,
        truncated=True,
    )


def _path_of(line: str) -> str | None:
    """The path on a ``+++`` line, or ``None`` when the file is gone.

    Git ends the line with a tab when the path holds a space, so that the path
    and whatever follows it stay tellable apart; it quotes the path instead when
    it holds a character it cannot print. Both forms are undone here, and
    ``core.quotePath=false`` is passed so that a path written in another script
    arrives as itself rather than as octal escapes.
    """
    value = _after(line, NEW_FILE)
    if value == "/dev/null":
        return None
    return value[2:] if value.startswith("b/") else value


def _after(line: str, prefix: str) -> str:
    """What a header line says after *prefix*, unquoted."""
    value = line[len(prefix):].rstrip("\t")
    if len(value) > 1 and value.startswith('"') and value.endswith('"'):
        return _unquote(value[1:-1])
    return value


def _unquote(value: str) -> str:
    """Undo git's C-style quoting of a path.

    The octal escapes stand for bytes, not characters, so they are collected as
    bytes and decoded once at the end: a path in Chinese is three octal escapes
    per character, and decoding them one at a time would produce three
    characters that are not the one that was written.
    """
    raw = bytearray()
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\":
            raw.extend(character.encode("utf-8"))
            index += 1
            continue
        escape = value[index + 1]
        if escape in "01234567":
            raw.append(int(value[index + 1:index + 4], 8))
            index += 4
            continue
        raw.extend(ESCAPES.get(escape, escape.encode("utf-8")))
        index += 2
    return raw.decode("utf-8", errors="surrogateescape")


def _run_git(repository_root, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments], cwd=Path(repository_root), capture_output=True
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git: {detail}")
    return completed.stdout


def _timestamp(when: datetime | None) -> str | None:
    """A moment in the form other programs parse, or ``None`` when there is none."""
    return None if when is None else when.isoformat()
