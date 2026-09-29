"""Derive each definition's life from the snapshots the AST pass stored.

The pass stores facts: one row per file version, one row per definition that
version contained, and, on each of those rows, a cached comparison against the
nearest earlier version of the same file that could be read. This module turns
those snapshots into histories — what was created, what changed, what went away
— and, just as much a part of the answer, records where the snapshots stop and
nothing can be said.

Four rules, all of them the user's:

* **A definition is identified by its kind and its qualified name inside one
  file's life, and by nothing else.** No semantic identity, no rename inference,
  no move inference: a function that was renamed is one definition ending and
  another beginning, and a definition that moves to another file is one ending
  here and another beginning there. A *file* that was renamed carries its
  definitions along, because the file is one life and the identity is scoped to
  it.
* **``created``, ``modified`` and ``deleted`` are derived by comparing
  snapshots**, against the nearest earlier version of the same file that could be
  read. ``unchanged`` is a fact the snapshots hold, not an event: a definition
  that never changed has exactly one event, its creation. That the life continued
  through the versions in between is what the life itself says.
* **A version that could not be read is a gap, and the gap is recorded rather
  than guessed over.** "Nothing could be read" is not "nothing was there", so a
  definition is never deleted on the strength of a version nobody read: a
  deletion needs a version that *was* read and did not hold it, or the end of the
  file. A change derived across a gap carries the unread versions it was derived
  across, so an event can never say *when* inside that span it happened — only
  that it had happened by the version that showed it. And a definition whose
  file's latest versions are unread has no ending at all, only an
  :attr:`DefinitionLife.unresolved` tail that says so.
* **A deletion is only ever read from a version that was read.** The corollary of
  the rule above: ``deleted`` is never inferred from a missing row, because a
  missing row is exactly what a version nobody could read produces.

Nothing here is stored. A history is derived on the spot from rows that can
always be rebuilt, so a rule that changes is a rule that takes effect on the next
read rather than a migration. That is also why the derivation repeats the
comparison the pass cached instead of trusting it: the cache is keyed on the
pass's identity rule, and this layer has to be able to say what it derived.
``tests/test_definition_history.py`` holds the two to each other — every stored
``change_type`` is checked against the event derived for it.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime

from codearchaeology import lifecycle
from codearchaeology.analysis import open_analysis
from codearchaeology.lifecycle import Lifecycle, build_lifecycles
from codearchaeology.storage import (
    DefinitionVersion,
    FileVersion,
    read_definitions_for_paths,
    read_stored_commits,
    read_versions_for_paths,
)

# What the snapshots show about a definition. The first two are the words the
# AST pass stores on a row, and they have to stay those words: the derivation and
# the cached comparison are checked against each other. ``deleted`` is the third
# because a definition that is gone has no row to carry it — it exists only in
# the comparison of two versions that were both read.
CREATED = "created"
MODIFIED = "modified"
DELETED = "deleted"

# Why a version of a file could not be read. A parse failure carries the parser's
# own words instead; these two are the cases where there are none.
NOT_STORED = "no version was stored for this commit"
NOT_IN_TREE = "the file was not in this commit"


@dataclass(frozen=True, slots=True)
class BlindSpot:
    """One version of a file whose structure nobody could read.

    ``reason`` is what is known about why: the parse failure's own words, or
    :data:`NOT_STORED` when no version was stored for that commit at all, or
    :data:`NOT_IN_TREE` when the file was not in that commit — which is not the
    end of the file when the file came back later.
    """

    commit_sha: str
    path: str
    committed_at: datetime
    reason: str
    lineno: int | None = None
    offset: int | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One version of a file that was read, and the definitions it held."""

    commit_sha: str
    path: str
    committed_at: datetime
    definitions: tuple[DefinitionVersion, ...]


@dataclass(frozen=True, slots=True)
class DefinitionEvent:
    """One thing the snapshots show about one definition.

    For ``created`` and ``modified``, the position and the fingerprint are the
    ones the definition had at ``commit_sha``. For ``deleted`` there are none: the
    definition was not there to be measured, and where it last stood is on the
    event before this one.

    ``previous_commit_sha`` is the version this one was compared against — the
    nearest earlier version of the same file that could be read, or ``None`` for
    the first version of the file's life. ``blind_spots`` are the versions
    between the two that nobody could read: empty when they are neighbours, and
    the reason the event cannot say when inside the span it happened.
    """

    change: str
    commit_sha: str
    path: str
    committed_at: datetime
    lineno: int | None
    end_lineno: int | None
    decorators: tuple[str, ...]
    fingerprint: str | None
    previous_commit_sha: str | None
    blind_spots: tuple[BlindSpot, ...]


@dataclass(frozen=True, slots=True)
class DefinitionLife:
    """One definition's history inside one file's life.

    ``occurrence`` is which definition of that name this is, counted in the order
    the file lists them. It is 0 for a name that appears once, which is nearly
    every name; it is there because one file can define the same name twice — a
    name defined again under an ``if``, a fallback in an ``except`` — and then the
    name alone cannot tell two lives apart. The *n*-th definition of a name
    continues the *n*-th life, which is the same rule the pass compares by.

    ``unresolved`` is the versions after the last one that could be read, when the
    definition was still there in that last readable version. It is empty when the
    file was read to its end, and empty when a deletion was seen — a life that was
    seen to end is not reopened by versions nobody read. A non-empty
    ``unresolved`` is the honest form of "and then the file went dark": the life
    may have continued or ended in there, and nothing here will claim to know
    which.
    """

    kind: str
    qualname: str
    occurrence: int
    events: tuple[DefinitionEvent, ...]
    unresolved: tuple[BlindSpot, ...] = ()

    @property
    def deleted(self) -> DefinitionEvent | None:
        """The event that ended the life, or ``None``.

        ``None`` means no deletion was ever *seen*. It does not mean the
        definition is still there: read ``unresolved`` for the versions after the
        last one that could be read.
        """
        last = self.events[-1]
        return last if last.change == DELETED else None


@dataclass(frozen=True, slots=True)
class FileHistory:
    """One file's life: every version of it, and the lives it contained.

    ``versions`` holds the file's versions in the order they happened, each one
    either a :class:`Snapshot` that was read or a :class:`BlindSpot` that was not,
    so a reader can see exactly where the file went dark and how long for. The
    commit that ended the file's life is in there too, as the version where the
    file was not in the tree. ``path_history`` is every name the file carried,
    which is why a history is reached by any of them.
    """

    path_history: tuple[str, ...]
    versions: tuple[Snapshot | BlindSpot, ...]
    lives: tuple[DefinitionLife, ...]


def load_histories(repository_root, database, path: str) -> tuple[FileHistory, ...]:
    """Read one path's stored snapshots and derive the lives in them.

    One history per file life the path took part in. A name that was used, given
    up and used again carries two, and they are two different files as far as the
    evidence goes — the identity is scoped to a life, not to a name.

    The path is the one git reports: relative to the repository root, with
    forward slashes. A path that is not Python has no stored versions, so its
    history is a file life of blind spots and no lives; a caller that wants to
    say so before asking can look at the suffix.
    """
    with open_analysis(repository_root, database) as connection:
        commits = read_stored_commits(connection)
        lifecycles = tuple(
            life for life in build_lifecycles(commits) if path in life.path_history
        )
        if not lifecycles:
            return ()

        # Every name any of these lives carried, because a version row is keyed
        # by the name the file had at that commit.
        names = tuple(
            sorted({name for life in lifecycles for name in life.path_history})
        )
        versions = read_versions_for_paths(connection, names)
        definitions = read_definitions_for_paths(connection, names)

    return build_histories(lifecycles, versions, definitions)


def build_histories(
    lifecycles: Iterable[Lifecycle],
    versions: Mapping[tuple[str, str], FileVersion],
    definitions: Mapping[tuple[str, str], tuple[DefinitionVersion, ...]],
) -> tuple[FileHistory, ...]:
    """Derive every definition's life in every file life given.

    *versions* and *definitions* are stored rows keyed by the commit and the path
    they describe. Rows belonging to versions outside these lives are ignored, so
    a caller may hand over more than is needed.
    """
    return tuple(_history(life, versions, definitions) for life in lifecycles)


def _history(
    life: Lifecycle,
    versions: Mapping[tuple[str, str], FileVersion],
    definitions: Mapping[tuple[str, str], tuple[DefinitionVersion, ...]],
) -> FileHistory:
    """Walk one file's life and derive the lives of the definitions in it."""
    seen: list[Snapshot | BlindSpot] = []
    events: dict[tuple[str, str, int], list[DefinitionEvent]] = {}
    order: list[tuple[str, str, int]] = []
    previous: Snapshot | None = None
    pending: list[BlindSpot] = []

    for position, event in enumerate(life.events):
        if event.change_type == lifecycle.DELETED:
            if position + 1 == len(life.events):
                _end_the_file(events, order, previous, pending, event)
                # The commit that ended the life is a version of the file's life
                # in which the file was not in the tree — the same fact a deletion
                # in the middle records, and what makes "what was in this file at
                # that commit" answerable for it. It is not a gap between two
                # comparisons (the deletions above carry those), so it joins the
                # file's versions and not the pending gap.
                seen.append(
                    BlindSpot(
                        commit_sha=event.commit_sha,
                        path=event.path,
                        committed_at=event.committed_at,
                        reason=NOT_IN_TREE,
                    )
                )
                break
            # The file was not in that commit and came back later — a hole in
            # the middle of the life, not its end.
            blind = BlindSpot(
                commit_sha=event.commit_sha,
                path=event.path,
                committed_at=event.committed_at,
                reason=NOT_IN_TREE,
            )
            seen.append(blind)
            pending.append(blind)
            continue

        step = _read(event, versions, definitions)
        seen.append(step)
        if isinstance(step, BlindSpot):
            pending.append(step)
            continue

        # The blind spots are handed to every event of this step, and the same
        # tuple is shared by all of them: the gap is one fact about the file, not
        # one per definition.
        blind_spots = tuple(pending)
        for key, definition_event in _step(previous, step, blind_spots):
            _record(events, order, key, definition_event)
        previous = step
        pending.clear()

    return FileHistory(
        path_history=life.path_history,
        versions=tuple(seen),
        lives=_lives(events, order, previous, pending),
    )


def _read(
    event: lifecycle.LifecycleEvent,
    versions: Mapping[tuple[str, str], FileVersion],
    definitions: Mapping[tuple[str, str], tuple[DefinitionVersion, ...]],
) -> Snapshot | BlindSpot:
    """One version of the file: the snapshot it was, or why nobody could read it."""
    key = (event.commit_sha, event.path)
    version = versions.get(key)
    if version is None:
        return BlindSpot(
            commit_sha=event.commit_sha,
            path=event.path,
            committed_at=event.committed_at,
            reason=NOT_STORED,
        )
    if version.parse_error is not None:
        return BlindSpot(
            commit_sha=event.commit_sha,
            path=event.path,
            committed_at=event.committed_at,
            reason=version.parse_error,
            lineno=version.error_lineno,
            offset=version.error_offset,
        )
    return Snapshot(
        commit_sha=event.commit_sha,
        path=event.path,
        committed_at=event.committed_at,
        definitions=definitions.get(key, ()),
    )


def _step(
    previous: Snapshot | None,
    current: Snapshot,
    blind_spots: tuple[BlindSpot, ...],
) -> Iterator[tuple[tuple[str, str, int], DefinitionEvent]]:
    """Everything the move from *previous* to *current* shows about definitions.

    ``previous`` is the nearest earlier version of the same file that could be
    read, or ``None`` at the start of the file's life. Comparing against that
    version rather than against the one before it in time is what lets a change
    be derived across a version nobody could read — and it is why the blind spots
    it spans are handed to the event rather than dropped.

    Two definitions can share a name, so the *n*-th definition of a name is
    compared against the *n*-th one before it, in the order they appear. A name
    that appears fewer times than it did is not a renumbering: the extras are
    gone.
    """
    before: dict[tuple[str, str], list[DefinitionVersion]] = {}
    for row in () if previous is None else previous.definitions:
        before.setdefault((row.kind, row.qualname), []).append(row)

    previous_commit_sha = None if previous is None else previous.commit_sha
    matched: dict[tuple[str, str], int] = {}
    for row in current.definitions:
        key = (row.kind, row.qualname)
        occurrence = matched.get(key, 0)
        matched[key] = occurrence + 1

        earlier = before.get(key)
        if earlier is None or occurrence >= len(earlier):
            change = CREATED
        elif earlier[occurrence].fingerprint == row.fingerprint:
            # Unchanged is a fact the snapshots hold, not an event: the life
            # goes through this version without anything happening to it.
            continue
        else:
            change = MODIFIED

        yield (key[0], key[1], occurrence), DefinitionEvent(
            change=change,
            commit_sha=current.commit_sha,
            path=current.path,
            committed_at=current.committed_at,
            lineno=row.lineno,
            end_lineno=row.end_lineno,
            decorators=row.decorators,
            fingerprint=row.fingerprint,
            previous_commit_sha=previous_commit_sha,
            blind_spots=blind_spots,
        )

    for key, rows in before.items():
        for occurrence in range(matched.get(key, 0), len(rows)):
            yield (key[0], key[1], occurrence), DefinitionEvent(
                change=DELETED,
                commit_sha=current.commit_sha,
                path=current.path,
                committed_at=current.committed_at,
                lineno=None,
                end_lineno=None,
                decorators=(),
                fingerprint=None,
                previous_commit_sha=previous_commit_sha,
                blind_spots=blind_spots,
            )


def _end_the_file(
    events: dict[tuple[str, str, int], list[DefinitionEvent]],
    order: list[tuple[str, str, int]],
    previous: Snapshot | None,
    pending: list[BlindSpot],
    event: lifecycle.LifecycleEvent,
) -> None:
    """Close every life that was still there when the file went away.

    The file's own deletion is the evidence: whatever the last version that could
    be read held, it is not there any more, because the file is not. That is a
    deletion seen, not a deletion guessed — but if the versions between the two
    went unread, the event carries them, because whether the definition lasted
    until the file's end is not something this can know.
    """
    if previous is None:
        return

    blind_spots = tuple(pending)
    counted: dict[tuple[str, str], int] = {}
    for row in previous.definitions:
        key = (row.kind, row.qualname)
        occurrence = counted.get(key, 0)
        counted[key] = occurrence + 1

        _record(
            events,
            order,
            (key[0], key[1], occurrence),
            DefinitionEvent(
                change=DELETED,
                commit_sha=event.commit_sha,
                path=event.path,
                committed_at=event.committed_at,
                lineno=None,
                end_lineno=None,
                decorators=(),
                fingerprint=None,
                previous_commit_sha=previous.commit_sha,
                blind_spots=blind_spots,
            ),
        )


def _record(
    events: dict[tuple[str, str, int], list[DefinitionEvent]],
    order: list[tuple[str, str, int]],
    key: tuple[str, str, int],
    event: DefinitionEvent,
) -> None:
    """Add one event to one life, and note the life the first time it is seen."""
    if key not in events:
        events[key] = []
        order.append(key)
    events[key].append(event)


def _lives(
    events: dict[tuple[str, str, int], list[DefinitionEvent]],
    order: list[tuple[str, str, int]],
    previous: Snapshot | None,
    pending: list[BlindSpot],
) -> tuple[DefinitionLife, ...]:
    """The lives of one file, in the order their names first appeared."""
    # The versions after the last one that could be read. They belong to every
    # life that was still there in that version — and to no other: a life that
    # was seen to end is not reopened by versions nobody read.
    unresolved = () if previous is None else tuple(pending)

    lives = []
    for kind, qualname, occurrence in order:
        history = tuple(events[(kind, qualname, occurrence)])
        lives.append(
            DefinitionLife(
                kind=kind,
                qualname=qualname,
                occurrence=occurrence,
                events=history,
                unresolved=() if history[-1].change == DELETED else unresolved,
            )
        )
    return tuple(lives)
