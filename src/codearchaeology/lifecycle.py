"""Rebuild each file's life from the stored history.

A commit log records events, not identities. Git reports that ``app.py`` was
deleted and ``src/app.py`` was added, and only rename detection ties the two
together. This module walks the events in the order they happened and follows
those ties, so a file keeps one identity across its renames instead of being
split into a death and a birth.

The walk keeps a mapping from path to the life currently occupying it. Four
rules cover everything git can report:

``A path``
    A new life starts. ``A`` means the path did not exist in the parent, so
    whatever held that path before must already have ended.
``M path``
    The life holding the path gains an event.
``R old new``
    The life holding ``old`` moves to ``new``. Nothing is born and nothing
    dies, which is what keeps a rename from looking like a deletion.
``D path``
    The life holding the path ends.

Recreating a path after a delete starts a second life, and so does reusing a
name that an earlier life carried away through a rename. Both fall out of the
rules above rather than needing a special case.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from codearchaeology.analysis import open_analysis
from codearchaeology.history import Commit
from codearchaeology.storage import read_stored_commits

CREATED = "A"
MODIFIED = "M"
RENAMED = "R"
DELETED = "D"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """One thing that happened to a file, and the commit that did it."""

    commit_sha: str
    committed_at: datetime
    change_type: str
    path: str
    old_path: str | None
    added_lines: int | None
    deleted_lines: int | None


@dataclass(frozen=True, slots=True)
class Lifecycle:
    """One file's events, oldest first, from its birth to its death."""

    events: tuple[LifecycleEvent, ...]

    @property
    def path_history(self) -> tuple[str, ...]:
        """Every name this file carried, in order, without repeats.

        Only runs of the same name collapse. A file renamed away and later
        renamed back has three names in its history, not two, and the middle one
        is not a repeat of the first.
        """
        history: list[str] = []
        for event in self.events:
            if not history or history[-1] != event.path:
                history.append(event.path)
        return tuple(history)

    @property
    def current_path(self) -> str:
        """The name the file had at its last recorded event."""
        return self.events[-1].path

    @property
    def created(self) -> LifecycleEvent:
        """The first event, which is the file's birth as far as git reports."""
        return self.events[0]

    @property
    def deleted(self) -> LifecycleEvent | None:
        """The event that ended the file, or ``None`` while it is still there.

        Read from the last event rather than from any deletion anywhere in the
        list: a file deleted on one branch and edited on another, with the two
        interleaved by time, has a deletion in the middle and is still alive.
        """
        last = self.events[-1]
        return last if last.change_type == DELETED else None

    @property
    def is_alive(self) -> bool:
        return self.deleted is None

    @property
    def modifications(self) -> int:
        """How many events changed the file's content in place."""
        return sum(1 for event in self.events if event.change_type == MODIFIED)

    @property
    def renames(self) -> int:
        return sum(1 for event in self.events if event.change_type == RENAMED)


def build_lifecycles(commits: Iterable[Commit]) -> tuple[Lifecycle, ...]:
    """Rebuild every file's life, oldest life first.

    The input may be in any order; it is walked oldest commit first, which is
    the order the events actually happened in.
    """
    lives: list[list[LifecycleEvent]] = []
    living: dict[str, list[LifecycleEvent]] = {}
    seen: dict[str, list[LifecycleEvent]] = {}

    def start() -> list[LifecycleEvent]:
        events: list[LifecycleEvent] = []
        lives.append(events)
        return events

    for commit in sorted(commits, key=lambda commit: commit.committed_at):
        for change in commit.changes:
            if change.change_type == CREATED:
                events = start()
            else:
                source = change.old_path if change.change_type == RENAMED else change.path
                # The path is unoccupied, which happens when the commits of two
                # branches are interleaved by time. The file did exist in the
                # parent, so the event belongs to whichever life last held that
                # path rather than to a new one born out of nothing.
                events = living.get(source)
                if events is None:
                    events = seen.get(source)
                if events is None:
                    events = start()

            events.append(
                LifecycleEvent(
                    commit_sha=commit.sha,
                    committed_at=commit.committed_at,
                    change_type=change.change_type,
                    path=change.path,
                    old_path=change.old_path,
                    added_lines=change.added_lines,
                    deleted_lines=change.deleted_lines,
                )
            )

            if change.change_type == DELETED:
                living.pop(change.path, None)
                continue

            if change.change_type == RENAMED:
                living.pop(change.old_path, None)
            living[change.path] = events
            seen[change.path] = events

    return tuple(Lifecycle(events=tuple(events)) for events in lives)


def load_lifecycles(repository_root, database) -> tuple[Lifecycle, ...]:
    """Read the history stored in *database* and rebuild every file's life."""
    with open_analysis(repository_root, database) as connection:
        commits = read_stored_commits(connection)
    return build_lifecycles(commits)
