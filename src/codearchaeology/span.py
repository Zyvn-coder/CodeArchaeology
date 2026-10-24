"""Build the explanation context for a revision range: the set bundle.

A commit is the floor this builds on; a range is the unit a reviewer actually
holds — a branch, a series pushed together, the commits between two points. The
question is the same one ``context`` answers for a commit, asked of a set: what
is the shape of this change, what did it touch, what does the history say about
those files, and what could not be read.

Four rules shape the bundle, and each is the design freeze's:

* **The set is git's, and its order is the graph's.** ``X..Y`` is resolved to
  two full shas — a ref is a moving name, and a bundle built on a moment must
  name the moment — and the commits come back from git's topological order,
  oldest first, parents before children. No ordering by importance exists here,
  and "these commits form one change" stays a candidate the model may offer,
  never a fact the bundle asserts.
* **The diff is the unit's own.** One diff per bundle: the two endpoints, the
  same bounded per-file text ``context`` reads for a commit. Per-commit diffs
  are never carried — they are recomputable, they would multiply the bundle's
  largest section by the commit count, and the net diff is the honest object
  for the unit. A merge inside the range contributes its facts and no diff,
  because the endpoints' diff exists regardless of what the range contains.
* **A path is one row, however many commits touched it.** The row carries every
  occurrence in the bundle's order, the net status git reports for the
  endpoints, and — where the net diff holds no text for a path the set touched
  — one of three named absences that must never be merged: it lived and died
  inside the range, it ended where it began, or its endpoint blobs are binary.
* **Nothing is filled in.** A commit the analysis holds no row for is marked
  ``not_analyzed`` rather than skipped or invented, and every kind of
  not-there travels in ``absences``, one kind per reason.

The deterministic promise is the same one the commit bundle makes: the same
resolved pair and the same database produce the same bytes. Re-running the same
ref names after the refs moved is a different set, honestly so.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codearchaeology.analysis import open_analysis
from codearchaeology.ast_pass import PYTHON_SUFFIX
from codearchaeology.cochange import DEFAULT_MIN_SHARED
from codearchaeology.context import (
    BINARY_NO_TEXT,
    CO_CHANGE_FILES,
    CO_CHANGE_PARTNERS,
    HISTORY_COMMITS,
    MERGE_NO_DIFF,
    NOT_IN_THIS_COMMIT,
    NO_VERSION_STORED,
    PARSE_FAILED,
    Absence,
    Bounds,
    DefinitionContext,
    DiffText,
    EarlierCommit,
    HistoryContext,
    LineRange,
    cochange_contexts,
    definition_events,
    diff_ranges_between,
    diff_texts_between,
    lifecycle_contexts,
)
from codearchaeology.definition_history import (
    NOT_IN_TREE,
    NOT_STORED,
    BlindSpot,
    build_histories,
)
from codearchaeology.history import GitError, read_commits_between
from codearchaeology.lifecycle import Lifecycle, build_lifecycles
from codearchaeology.statistics import summarize
from codearchaeology.storage import (
    read_definitions_for_paths,
    read_stored_commits,
    read_versions_for_paths,
)

# The three absences a range adds to the vocabulary, kept apart because each is
# a different sentence about why something is missing.
NOT_ANALYZED = "not_analyzed"
GONE_WITHIN_RANGE = "gone_within_range"
NET_UNCHANGED = "net_unchanged"

# The net change of the endpoints, as git's ``--raw`` records it: status, both
# blob shas, and the two paths of a rename. ``-z`` keeps the paths verbatim,
# and the flags mirror the patch reader's so the two invocations cannot
# disagree about what a rename is.
RAW_ARGUMENTS = (
    "--no-pager",
    "-c",
    "core.quotePath=false",
    "diff",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--find-renames=50%",
    "--raw",
    "-z",
)


class InvalidRangeError(ValueError):
    """Raised when a spec is not a ``<base>..<tip>`` revision range."""


class EmptyRangeError(LookupError):
    """Raised when a range resolves to no commits at all."""


@dataclass(frozen=True, slots=True)
class SpanCommit:
    """One commit of the range, with whether the analysis holds it.

    The metadata is git's — the range is chosen by git, and a commit the
    analysis never read still has an author and a message. ``analyzed`` is what
    the database says, and a commit it says nothing about is marked here and
    named in the absences rather than quietly dropped.
    """

    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: str
    committed_at: str
    message: str
    is_merge: bool
    analyzed: bool


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One commit's touch of one path, inside the range's order."""

    commit_sha: str
    change_type: str
    old_path: str | None
    added_lines: int | None
    deleted_lines: int | None
    similarity: int | None


@dataclass(frozen=True, slots=True)
class SpanChange:
    """One path the range touched, across every commit that touched it.

    ``net_status`` is what git reports for the two endpoints — ``A``, ``M``,
    ``D``, ``R87`` — or ``None`` where the net diff says nothing about the
    path, which is a named absence rather than a silence. ``occurrences`` are
    in the bundle's order; ``added_lines`` and ``deleted_lines`` sum the ones
    git could count, and a binary occurrence contributes nothing to them while
    saying so in its own row.
    """

    path: str
    net_status: str | None
    occurrences: tuple[Occurrence, ...]
    added_lines: int
    deleted_lines: int
    is_binary: bool
    ranges: tuple[LineRange, ...]
    diff: DiffText | None


@dataclass(frozen=True, slots=True)
class SpanDefinition:
    """One definition's whole life inside the range.

    ``events`` are the same objects the commit bundle carries, in the order
    they happened. ``presence_base`` and ``presence_tip`` say whether the
    definition stood at each endpoint — ``present``, ``absent``, or
    ``unreadable`` when the version at that end could not be parsed — so "what
    did the range do to this function" is one row rather than a walk. No
    identity is inferred across a rename: the group is (path, qualname,
    occurrence), and a renamed definition is one life ending and another
    beginning, exactly as the commit bundle reads it.
    """

    path: str
    kind: str
    qualname: str
    occurrence: int
    events: tuple[DefinitionContext, ...]
    presence_base: str
    presence_tip: str


@dataclass(frozen=True, slots=True)
class NetChange:
    """What ``git diff --raw`` says about one path between the endpoints."""

    status: str
    old_blob: str
    new_blob: str
    old_path: str | None
    similarity: int | None


@dataclass(frozen=True, slots=True)
class SpanContext:
    """Everything the model may see about one revision range, and nothing else."""

    base: str
    tip: str
    commits: tuple[SpanCommit, ...]
    changes: tuple[SpanChange, ...]
    lifecycle: tuple
    definitions: tuple[SpanDefinition, ...]
    cochange: tuple
    history: tuple[HistoryContext, ...]
    absences: tuple[Absence, ...]
    bounds: Bounds


def resolve_range(repository_root, spec: str) -> tuple[str, str]:
    """Resolve ``<base>..<tip>`` to two full commit shas.

    A ref is a moving name; the bundle records the shas it resolved to, and a
    later run of the same names after they moved is a different set. A side git
    cannot resolve is git's own error, which names the rev.
    """
    left, separator, right = spec.partition("..")
    left, right = left.strip(), right.strip()
    if not separator or not left or not right or ".." in right:
        raise InvalidRangeError(
            f"{spec!r} is not a revision range; write it as <base>..<tip>"
        )
    return _resolve(repository_root, left), _resolve(repository_root, right)


def build_span(repository_root, database, base: str, tip: str) -> SpanContext:
    """Assemble the set bundle for the commits ``base..tip`` names.

    Nothing here reads the network or a model. The commits, their metadata and
    their per-commit file rows come from git in one call; the versions, the
    definitions, the lives and the statistics come from the database, which is
    the only place they exist — and the commits the database holds nothing for
    are marked rather than faked.
    """
    span_commits = read_commits_between(repository_root, base, tip)
    if not span_commits:
        raise EmptyRangeError(
            f"the range {base}..{tip} holds no commits;"
            f" the tip may not be ahead of the base"
        )
    order = {commit.sha: index for index, commit in enumerate(span_commits)}
    in_range = frozenset(order)

    with open_analysis(repository_root, database) as connection:
        stored_commits = read_stored_commits(connection)
        stored = frozenset(commit.sha for commit in stored_commits)
        lives = build_lifecycles(stored_commits)
        touched = _touched_lives(lives, in_range)
        names = tuple(sorted({name for life in touched for name in life.path_history}))
        versions = read_versions_for_paths(connection, names)
        definitions = read_definitions_for_paths(connection, names)

    histories = build_histories(touched, versions, definitions)
    occurrences = _occurrences(span_commits)
    net = _net_changes(repository_root, base, tip)
    texts = diff_texts_between(repository_root, base, tip)
    spans = diff_ranges_between(repository_root, base, tip)

    return SpanContext(
        base=base,
        tip=tip,
        commits=_span_commits(span_commits, stored),
        changes=_changes(occurrences, net, texts, spans),
        lifecycle=lifecycle_contexts(_truncated(touched, in_range)),
        definitions=_definitions(histories, in_range, versions, order),
        cochange=cochange_contexts(stored_commits, touched),
        history=_history(touched, in_range),
        absences=_absences(span_commits, stored, histories, occurrences, net, texts),
        bounds=_bounds(touched, in_range),
    )


def build_span_object(context: SpanContext) -> dict:
    """The set bundle as the fields a model is shown and a program reads."""
    return {
        "range": {
            "base": context.base,
            "tip": context.tip,
            "commits": len(context.commits),
            "authors": sorted(
                {
                    f"{commit.author_name} <{commit.author_email}>"
                    for commit in context.commits
                }
            ),
            "first_authored_at": context.commits[0].authored_at,
            "last_authored_at": context.commits[-1].authored_at,
            "merge_commits": sum(1 for commit in context.commits if commit.is_merge),
        },
        "commits": [
            {
                "sha": commit.sha,
                "parents": list(commit.parents),
                "author_name": commit.author_name,
                "author_email": commit.author_email,
                "authored_at": commit.authored_at,
                "committed_at": commit.committed_at,
                "message": commit.message,
                "is_merge": commit.is_merge,
                "analyzed": commit.analyzed,
            }
            for commit in context.commits
        ],
        "file_changes": [
            {
                "path": change.path,
                "net_status": change.net_status,
                "occurrences": [
                    {
                        "commit_sha": occurrence.commit_sha,
                        "change_type": occurrence.change_type,
                        "old_path": occurrence.old_path,
                        "added_lines": occurrence.added_lines,
                        "deleted_lines": occurrence.deleted_lines,
                        "similarity": occurrence.similarity,
                    }
                    for occurrence in change.occurrences
                ],
                "added_lines": change.added_lines,
                "deleted_lines": change.deleted_lines,
                "is_binary": change.is_binary,
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
                "path": definition.path,
                "kind": definition.kind,
                "qualname": definition.qualname,
                "occurrence": definition.occurrence,
                "events": [
                    {
                        "commit_sha": event.commit_sha,
                        "change": event.change,
                        "lineno": event.lineno,
                        "end_lineno": event.end_lineno,
                        "decorators": list(event.decorators),
                        "previous_commit_sha": event.previous_commit_sha,
                        "blind_spots": [
                            {
                                "commit_sha": spot.commit_sha,
                                "committed_at": spot.committed_at,
                                "reason": spot.reason,
                            }
                            for spot in event.blind_spots
                        ],
                    }
                    for event in definition.events
                ],
                "presence_base": definition.presence_base,
                "presence_tip": definition.presence_tip,
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


def build_span_json(context: SpanContext) -> str:
    """The set bundle as JSON — the bytes a model is shown, and a reader prints."""
    return json.dumps(build_span_object(context), indent=2, ensure_ascii=False)


def _resolve(repository_root, revision: str) -> str:
    completed = subprocess.run(
        ["git", "--no-pager", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=Path(repository_root),
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        # Git's own words for the failure, with the rev named: ``--verify``'s
        # message alone ("Needed a single revision") does not say which side of
        # the range failed, and a reader has to be able to tell.
        raise GitError(f"{revision}: {detail}")
    return completed.stdout.decode("ascii").strip()


def _run_git(repository_root, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments], cwd=Path(repository_root), capture_output=True
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git: {detail}")
    return completed.stdout


def _touched_lives(lives, in_range: frozenset) -> tuple:
    """The lives with an event inside the range, in the walk's own order."""
    return tuple(
        life
        for life in lives
        if any(event.commit_sha in in_range for event in life.events)
    )


def _truncated(touched, in_range: frozenset) -> tuple:
    """Each life cut to end at its last in-range event.

    A file's summary should describe it as the range leaves it, not as the
    repository's later history renames it into — so the events after the range
    are left out, and the life's current name is the name it carried when the
    range ended.
    """
    truncated = []
    for life in touched:
        last = max(
            index
            for index, event in enumerate(life.events)
            if event.commit_sha in in_range
        )
        truncated.append(Lifecycle(events=life.events[: last + 1]))
    return tuple(truncated)


def _span_commits(span_commits, stored: frozenset) -> tuple[SpanCommit, ...]:
    return tuple(
        SpanCommit(
            sha=commit.sha,
            parents=commit.parents,
            author_name=commit.author_name,
            author_email=commit.author_email,
            authored_at=commit.authored_at.isoformat(),
            committed_at=commit.committed_at.isoformat(),
            message=commit.message,
            is_merge=commit.is_merge,
            analyzed=commit.sha in stored,
        )
        for commit in span_commits
    )


def _occurrences(span_commits) -> dict[str, tuple[Occurrence, ...]]:
    """Every path the set touched, with each commit's touch in the bundle's order."""
    by_path: dict[str, list[Occurrence]] = {}
    for commit in span_commits:
        for change in commit.changes:
            by_path.setdefault(change.path, []).append(
                Occurrence(
                    commit_sha=commit.sha,
                    change_type=change.change_type,
                    old_path=change.old_path,
                    added_lines=change.added_lines,
                    deleted_lines=change.deleted_lines,
                    similarity=change.similarity,
                )
            )
    return {path: tuple(found) for path, found in by_path.items()}


def _changes(occurrences, net, texts, spans) -> tuple[SpanChange, ...]:
    """One row per path, from the occurrences, the net change and the patch."""
    rows = []
    for path in sorted(occurrences):
        found = occurrences[path]
        entry = net.get(path)
        rows.append(
            SpanChange(
                path=path,
                net_status=None if entry is None else entry.status,
                occurrences=found,
                added_lines=sum(
                    touch.added_lines
                    for touch in found
                    if touch.added_lines is not None
                ),
                deleted_lines=sum(
                    touch.deleted_lines
                    for touch in found
                    if touch.deleted_lines is not None
                ),
                is_binary=any(
                    touch.added_lines is None and touch.deleted_lines is None
                    for touch in found
                )
                or (
                    entry is not None
                    and entry.old_blob != entry.new_blob
                    and path not in texts
                ),
                ranges=spans.get(path, ()),
                diff=texts.get(path),
            )
        )
    return tuple(rows)


def _net_changes(repository_root, base: str, tip: str) -> dict[str, NetChange]:
    """The endpoints' net change per path, from ``git diff --raw``.

    The raw listing names every path whose state differs between the endpoints
    — content or mode — with both blob shas, which is what makes the absence
    partition mechanical: a path the listing does not hold at all either ended
    exactly where it began or never left the range alive, and a path it holds
    with one blob twice changed nothing but its mode.
    """
    output = _run_git(repository_root, *RAW_ARGUMENTS, base, tip).decode(
        "utf-8", errors="surrogateescape"
    )
    tokens = [token for token in output.split("\0") if token]

    found: dict[str, NetChange] = {}
    index = 0
    while index < len(tokens):
        fields = tokens[index][1:].split(" ")
        old_blob, new_blob, status = fields[2], fields[3], fields[4]
        index += 1
        change_type = status[0]
        similarity = int(status[1:]) if status[1:] else None

        old_path = None
        if change_type in "RC":
            old_path = tokens[index]
            index += 1
        path = tokens[index]
        index += 1

        found[path] = NetChange(
            status=status,
            old_blob=old_blob,
            new_blob=new_blob,
            old_path=old_path,
            similarity=similarity,
        )
    return found


def _definitions(
    histories, in_range: frozenset, versions, order
) -> tuple[SpanDefinition, ...]:
    """Every definition event inside the range, grouped into one life per name."""
    grouped: dict[tuple[str, str, int], list] = {}
    for event in definition_events(histories, in_range):
        grouped.setdefault((event.path, event.qualname, event.occurrence), []).append(
            event
        )

    found = [
        SpanDefinition(
            path=path,
            kind=events[0].kind,
            qualname=qualname,
            occurrence=occurrence,
            events=tuple(events),
            presence_base="absent" if events[0].change == "created" else "present",
            presence_tip=_presence_at_tip(path, events, versions, order),
        )
        for (path, qualname, occurrence), events in grouped.items()
    ]
    found.sort(
        key=lambda definition: (
            definition.path,
            definition.qualname,
            definition.occurrence,
        )
    )
    return tuple(found)


def _presence_at_tip(path, events, versions, order) -> str:
    """Whether the definition stood at the tip, or the tip version could not say.

    ``unreadable`` is its own answer: the path's last in-range version failed to
    parse, so the definition's last event is from an earlier readable one and
    what the tip holds is not known — which is not the same as absent.
    """
    in_range_versions = [
        version
        for (sha, name), version in versions.items()
        if name == path and sha in order
    ]
    if in_range_versions:
        last = max(in_range_versions, key=lambda version: order[version.commit_sha])
        if isinstance(last, BlindSpot):
            return "unreadable"

    if events[-1].change == "deleted":
        return "absent"
    return "present"


def _history(touched, in_range: frozenset) -> tuple[HistoryContext, ...]:
    """The commits before the range first touched each file.

    The window is the file's life up to the moment the change begins — the
    set-level reading of what the window means for one commit — so what is
    counted is the events before the life's first in-range one, and the window
    holds the most recent few of them.
    """
    entries = []
    for life in _truncated(touched, in_range):
        first = min(
            index
            for index, event in enumerate(life.events)
            if event.commit_sha in in_range
        )
        earlier = life.events[:first]
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


def _absences(
    span_commits, stored, histories, occurrences, net, texts
) -> tuple[Absence, ...]:
    """What could not be read or said, one kind per reason.

    The kinds are kept apart because they look alike: a merge git printed no
    diff for is not a commit the analysis missed, and a path that died inside
    the range is not one that came back to where it began. The three path-level
    kinds are decided from what git itself said — the raw listing and the patch
    — never from a guess about why something is missing.
    """
    in_range = frozenset(commit.sha for commit in span_commits)
    found: list[Absence] = []

    for commit in span_commits:
        if commit.sha not in stored:
            found.append(
                Absence(
                    kind=NOT_ANALYZED,
                    path=None,
                    detail=f"the analysis holds no row for {commit.sha}",
                )
            )
        elif commit.is_merge:
            found.append(
                Absence(
                    kind=MERGE_NO_DIFF,
                    path=None,
                    detail=f"git prints no diff for the merge {commit.sha}",
                )
            )

    for history in histories:
        for version in history.versions:
            if version.commit_sha not in in_range or not isinstance(
                version, BlindSpot
            ):
                continue
            if not version.path.endswith(PYTHON_SUFFIX):
                continue
            found.append(
                Absence(
                    kind=_kind_of(version.reason),
                    path=version.path,
                    detail=version.reason,
                )
            )

    for path in sorted(occurrences):
        entry = net.get(path)
        if entry is None:
            born = occurrences[path][0].change_type in "ARC"
            died = occurrences[path][-1].change_type == "D"
            if born and died:
                found.append(
                    Absence(
                        kind=GONE_WITHIN_RANGE,
                        path=path,
                        detail="created inside the range and deleted before the tip",
                    )
                )
            else:
                found.append(
                    Absence(
                        kind=NET_UNCHANGED,
                        path=path,
                        detail="touched inside the range, and the endpoints hold one content",
                    )
                )
        elif entry.old_blob == entry.new_blob:
            found.append(
                Absence(
                    kind=NET_UNCHANGED,
                    path=path,
                    detail="touched inside the range, and the endpoints hold one content",
                )
            )
        elif path not in texts:
            found.append(
                Absence(
                    kind=BINARY_NO_TEXT,
                    path=path,
                    detail="the endpoint blobs are binary, so git prints no diff text",
                )
            )

    found.sort(key=lambda absence: (absence.path or "", absence.kind))
    return tuple(found)


def _kind_of(reason: str) -> str:
    if reason == NOT_STORED:
        return NO_VERSION_STORED
    if reason == NOT_IN_TREE:
        return NOT_IN_THIS_COMMIT
    return PARSE_FAILED


def _bounds(touched, in_range: frozenset) -> Bounds:
    """What the context capped, counted from the same values the caps applied to."""
    history = _history(touched, in_range)
    ranked = sum(
        1 for life in touched if summarize(life).commits >= DEFAULT_MIN_SHARED
    )
    return Bounds(
        co_change_partners=CO_CHANGE_PARTNERS,
        co_change_files=CO_CHANGE_FILES,
        co_change_files_omitted=max(0, ranked - CO_CHANGE_FILES),
        history_commits=HISTORY_COMMITS,
        history_commits_omitted=sum(
            max(0, entry.total - HISTORY_COMMITS) for entry in history
        ),
    )
