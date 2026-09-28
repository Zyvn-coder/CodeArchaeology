"""Add up one file's life into the numbers that describe it.

Everything here is a sum over the events git reported. Nothing is inferred, and
that has a consequence worth stating plainly: how much a file added and deleted
depends on whether git recognised its renames. A file renamed while most of its
content changed is reported as a deletion plus an addition, which turns one
file's ``+140/-40`` into a dead file at ``+100/-100`` and a separate new one at
``+100/-0``. The net change survives that, because it is only the final line
count minus the initial one, but the gross numbers do not.
"""

from dataclasses import dataclass
from datetime import datetime

from codearchaeology.lifecycle import MODIFIED, RENAMED, Lifecycle, LifecycleEvent


@dataclass(frozen=True, slots=True)
class FileStatistics:
    """The numbers that summarise one file's life."""

    created_at: datetime
    created_sha: str
    last_modified_at: datetime | None
    last_modified_sha: str | None
    commits: int
    modifications: int
    renames: int
    additions: int
    deletions: int
    is_deleted: bool
    binary_changes: int

    @property
    def net_change(self) -> int:
        """The lines the file gained over its life.

        Derived rather than stored, so it can never disagree with the two
        numbers it comes from. For a text file this is also its line count at
        the last event, since every added line was counted and every deleted one
        subtracted.
        """
        return self.additions - self.deletions


def summarize(lifecycle: Lifecycle) -> FileStatistics:
    """Add up *lifecycle* into the numbers that describe it."""
    changes = [event for event in lifecycle.events if _changed_content(event)]
    last = changes[-1] if changes else None

    return FileStatistics(
        created_at=lifecycle.created.committed_at,
        created_sha=lifecycle.created.commit_sha,
        last_modified_at=None if last is None else last.committed_at,
        last_modified_sha=None if last is None else last.commit_sha,
        commits=len(lifecycle.events),
        modifications=len(changes),
        renames=lifecycle.renames,
        additions=sum(event.added_lines or 0 for event in lifecycle.events),
        deletions=sum(event.deleted_lines or 0 for event in lifecycle.events),
        is_deleted=not lifecycle.is_alive,
        binary_changes=sum(1 for event in lifecycle.events if event.is_binary),
    )


def _changed_content(event: LifecycleEvent) -> bool:
    """Whether the event changed the file's lines.

    A modification always did. A rename did only when it carried line changes
    with it: a pure rename leaves the content alone, while one that rewrote
    lines is a modification that happens to be a rename as well. A birth and a
    death are not counted, which is why a file that was created and never
    touched has no last-modified time at all rather than one equal to its birth.
    """
    if event.change_type == MODIFIED:
        return True
    if event.change_type == RENAMED:
        return (event.added_lines or 0) + (event.deleted_lines or 0) > 0
    return False
