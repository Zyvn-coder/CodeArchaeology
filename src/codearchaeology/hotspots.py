"""Rank files by how often they change, and list what the history contained.

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

**The ranking and the inventory are two different questions**, and the two
commands that show them say so. ``hotspots`` asks *which files are busiest*: it
ranks living files, because a hotspot is a place and a file that is gone is no
longer one. ``files`` asks *what the repository contains*: it lists every file
the history ever held, deleted ones included, ordered by path the way a
directory listing is. The same numbers ride along in both, and they must agree.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rich import box
from rich.table import Table

from codearchaeology.formatting import shorten
from codearchaeology.lifecycle import Lifecycle
from codearchaeology.statistics import summarize

PATH_MINIMUM_WIDTH = 20
COMMITS_WIDTH = len("COMMITS")
LINES_WIDTH = len("+LINES")
COLUMN_PADDING = 10
COLUMN_SLACK = 5
FIXED_WIDTH = COMMITS_WIDTH + LINES_WIDTH * 2 + COLUMN_PADDING + COLUMN_SLACK

STATE_WIDTH = len("deleted")
INVENTORY_FIXED_WIDTH = FIXED_WIDTH + STATE_WIDTH

ALIVE = "alive"
DELETED = "deleted"


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


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """One file the history contained, alive or not."""

    current_path: str
    path_history: tuple[str, ...]
    state: str
    commits: int
    additions: int
    deletions: int
    born: int

    @property
    def churn(self) -> int:
        """Lines added and removed together."""
        return self.additions + self.deletions


def build_inventory(lives: Iterable[Lifecycle]) -> tuple[InventoryEntry, ...]:
    """List every file the history contained, ordered by path.

    Nothing is filtered: a file that is gone is as much a fact about the
    repository as one that is still there, and an inventory that dropped it
    would be answering a different question. The ``state`` word says which is
    which, so a reader never has to infer it.

    The order is the path's, the way a directory listing is ordered, because the
    question here is *what is there* rather than *what is busiest*. The birth
    ordinal is the last key and is only reachable when two lives share a current
    path — a name deleted and later taken again — so even that case is
    deterministic.
    """
    entries = []
    for born, life in enumerate(lives):
        statistics = summarize(life)
        entries.append(
            InventoryEntry(
                current_path=life.current_path,
                path_history=life.path_history,
                state=ALIVE if life.is_alive else DELETED,
                commits=statistics.commits,
                additions=statistics.additions,
                deletions=statistics.deletions,
                born=born,
            )
        )

    entries.sort(key=lambda entry: (entry.current_path, entry.born))
    return tuple(entries)


def build_table(rows: Iterable[Hotspot], width: int) -> Table:
    """Render the ranking as a Rich table, one line per file.

    The path is cut to whatever the terminal has left over, for the same reason
    the timeline cuts its message: a long path would otherwise squeeze the other
    columns out of existence. The cut width is a ceiling, not a floor, so a
    repository of short names gets a narrow column rather than a wide gutter.
    """
    path_width = max(PATH_MINIMUM_WIDTH, width - FIXED_WIDTH)

    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    table.add_column("FILE", no_wrap=True, min_width=PATH_MINIMUM_WIDTH)
    table.add_column("COMMITS", justify="right", no_wrap=True, min_width=COMMITS_WIDTH)
    table.add_column("+LINES", justify="right", no_wrap=True, min_width=LINES_WIDTH)
    table.add_column("-LINES", justify="right", no_wrap=True, min_width=LINES_WIDTH)

    for row in rows:
        table.add_row(
            shorten(row.current_path, path_width),
            str(row.commits),
            str(row.additions),
            str(row.deletions),
        )
    return table


def build_object(row: Hotspot) -> dict:
    """One row as the fields other programs read.

    ``path_history`` rides along because the row is counted by identity: a path
    on its own does not say which names the count covers, so a reader could not
    tell whether two rows are one file or two.
    """
    return {
        "path": row.current_path,
        "commits": row.commits,
        "additions": row.additions,
        "deletions": row.deletions,
        "path_history": list(row.path_history),
    }


def build_json(rows: Iterable[Hotspot], repository_root, head_sha: str) -> str:
    """The ranking as JSON, for the ``hotspots`` command.

    ``files`` and ``hotspots`` no longer share this shape: they answer different
    questions now, and the inventory carries a ``state`` the ranking cannot have.
    See :func:`build_inventory_json`.
    """
    return json.dumps(
        {
            "repository": str(Path(repository_root).resolve()),
            "head_sha": head_sha,
            "files": [build_object(row) for row in rows],
        },
        indent=2,
        ensure_ascii=False,
    )


def build_inventory_table(rows: Iterable[InventoryEntry], width: int) -> Table:
    """Render the inventory as a Rich table, one line per file.

    ``STATE`` sits second because it is the fact that changes what the row
    means: a deleted file's numbers describe a life that has ended, and a reader
    scanning the table needs to see that before the numbers, not after them. The
    path is the only column that can be long, so it is the only one that gives
    when the terminal is narrow.
    """
    path_width = max(PATH_MINIMUM_WIDTH, width - INVENTORY_FIXED_WIDTH)

    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    table.add_column("FILE", no_wrap=True, min_width=PATH_MINIMUM_WIDTH)
    table.add_column("STATE", no_wrap=True)
    table.add_column("COMMITS", justify="right", no_wrap=True, min_width=COMMITS_WIDTH)
    table.add_column("+LINES", justify="right", no_wrap=True, min_width=LINES_WIDTH)
    table.add_column("-LINES", justify="right", no_wrap=True, min_width=LINES_WIDTH)

    for row in rows:
        table.add_row(
            shorten(row.current_path, path_width),
            row.state,
            str(row.commits),
            str(row.additions),
            str(row.deletions),
        )
    return table


def build_inventory_object(row: InventoryEntry) -> dict:
    """One inventory row as the fields other programs read.

    ``state`` is the word the table prints, so a program and a person read the
    same fact in the same vocabulary. ``path_history`` rides along for the same
    reason the ranking carries it: a row is counted by identity, and without the
    chain a renamed file and two unrelated ones cannot be told apart.
    """
    return {
        "path": row.current_path,
        "state": row.state,
        "commits": row.commits,
        "additions": row.additions,
        "deletions": row.deletions,
        "path_history": list(row.path_history),
    }


def build_inventory_json(
    rows: Iterable[InventoryEntry], repository_root, head_sha: str
) -> str:
    """The inventory as JSON, for the ``files`` command.

    Every row, sliced by nothing: ``--limit`` is a terminal convenience, and a
    program that asked for the inventory asked for all of it. The array is named
    for what it holds, the way the ranking's is.
    """
    return json.dumps(
        {
            "repository": str(Path(repository_root).resolve()),
            "head_sha": head_sha,
            "files": [build_inventory_object(row) for row in rows],
        },
        indent=2,
        ensure_ascii=False,
    )


DELETED_FILES_NOTE = (
    "Deleted files are in the list on purpose: this is what the history"
    " contains, not what the working tree contains."
)
