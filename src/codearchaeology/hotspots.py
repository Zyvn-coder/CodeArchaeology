"""Rank files by how often they change.

A hotspot is a place that changes a lot. It is **not** a place that matters.
The same count can come from core code, from code that keeps breaking, from
requirements that keep moving, from a refactor in progress, or from a file that
is simply edited often. Nothing here can tell those apart, so nothing here
guesses: it counts what git reported and leaves the reading to whoever asked.

Two things shape the count, and both are git's doing rather than ours.

* A rename git did not recognise splits one file into a death and a birth, so
  that file is scattered across rows instead of standing as one, and it can drop
  off a list it belongs on. The similarity scores are stored, so how close each
  rename came to the threshold is still visible after the fact.
* Merge commits report no file changes at all, so work that was merged is
  counted on the branch that made it and not again on the merge.

Ranking is by the number of commits that touched a file, which is the steadier
of the two signals: additions and deletions are dominated by generated files and
move with the rename threshold. Both are reported, so a caller who wants the
other ranking can sort by it.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from codearchaeology.lifecycle import Lifecycle
from codearchaeology.statistics import summarize


@dataclass(frozen=True, slots=True)
class Hotspot:
    """One file, and how much of the history touched it."""

    current_path: str
    path_history: tuple[str, ...]
    commits: int
    additions: int
    deletions: int

    @property
    def churn(self) -> int:
        """Lines added and removed together."""
        return self.additions + self.deletions


def rank_hotspots(
    lives: Iterable[Lifecycle], include_deleted: bool = False
) -> tuple[Hotspot, ...]:
    """Rank *lives* by how many commits touched them, most first.

    Deleted files are left out by default. A hotspot is a place, and a file that
    is gone is no longer one, however busy it was. The history still holds them,
    so ``include_deleted=True`` brings them back for anyone who wants to look at
    where the churn used to be.

    Files are counted by identity rather than by name, so a file that was renamed
    appears once with its whole history instead of once per name it ever had.
    """
    rows = []
    for life in lives:
        if not include_deleted and not life.is_alive:
            continue

        statistics = summarize(life)
        rows.append(
            Hotspot(
                current_path=life.current_path,
                path_history=life.path_history,
                commits=statistics.commits,
                additions=statistics.additions,
                deletions=statistics.deletions,
            )
        )

    # The path breaks ties so that two runs over the same history produce the
    # same list, whatever order the lives arrived in.
    rows.sort(key=lambda row: (-row.commits, row.current_path))
    return tuple(rows)
