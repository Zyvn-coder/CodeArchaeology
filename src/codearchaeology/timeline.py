"""Load a stored history and shape it for the terminal."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from rich import box
from rich.table import Table

from codearchaeology.analysis import open_analysis
from codearchaeology.formatting import SHORT_SHA_LENGTH, shorten
from codearchaeology.history import Commit
from codearchaeology.storage import get_meta, read_stored_commits

DATE_WIDTH = len("2024-03-01")
AUTHOR_WIDTH = 12
FILES_WIDTH = 5
CHANGES_WIDTH = 10
MESSAGE_MINIMUM_WIDTH = 16

COLUMN_PADDING = 10
COLUMN_SLACK = 5
FIXED_WIDTH = (
    SHORT_SHA_LENGTH
    + DATE_WIDTH
    + AUTHOR_WIDTH
    + FILES_WIDTH
    + CHANGES_WIDTH
    + COLUMN_PADDING
    + COLUMN_SLACK
)


@dataclass(frozen=True, slots=True)
class TimelineRow:
    """One commit, as the timeline shows it."""

    sha: str
    date: str
    author: str
    files_changed: int
    insertions: int
    deletions: int
    message: str


@dataclass(frozen=True, slots=True)
class Timeline:
    """A repository's stored history, newest commit first."""

    repository_root: Path
    head_sha: str
    commits: tuple[Commit, ...]

    def rows(self, limit: int | None = None) -> tuple[TimelineRow, ...]:
        """Return the newest *limit* rows, or all of them when *limit* is None.

        A row carries the full sha. Shortening it belongs to the table, because
        the same rows are also what ``--json`` hands to other programs, and a
        shortened sha is not a usable lookup key.
        """
        selected = self.commits if limit is None else self.commits[:limit]
        return tuple(_row(commit) for commit in selected)

    def as_json(self, limit: int | None = None) -> str:
        return json.dumps(
            {
                "repository": str(self.repository_root),
                "head_sha": self.head_sha,
                "commits": [asdict(row) for row in self.rows(limit)],
            },
            indent=2,
            ensure_ascii=False,
        )


def load_timeline(repository_root, database) -> Timeline:
    """Read the history stored in *database* for *repository_root*."""
    with open_analysis(repository_root, database) as connection:
        head_sha = get_meta(connection, "head_sha") or ""
        commits = tuple(read_stored_commits(connection))

    return Timeline(
        repository_root=Path(repository_root).resolve(),
        head_sha=head_sha,
        commits=commits,
    )


def build_table(rows, width: int) -> Table:
    """Render rows as a Rich table, one line per commit.

    The message is cut down to whatever the terminal has left over, so that
    every cell already fits and Rich has no reason to squeeze anything.
    """
    message_width = max(MESSAGE_MINIMUM_WIDTH, width - FIXED_WIDTH)

    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    table.add_column("SHA", no_wrap=True, min_width=SHORT_SHA_LENGTH)
    table.add_column("DATE", no_wrap=True, min_width=DATE_WIDTH)
    table.add_column("AUTHOR", no_wrap=True, min_width=AUTHOR_WIDTH)
    table.add_column("FILES", justify="right", no_wrap=True, min_width=FILES_WIDTH)
    table.add_column("+/-", justify="right", no_wrap=True, min_width=CHANGES_WIDTH)
    table.add_column("MESSAGE", no_wrap=True, overflow="ellipsis")

    for row in rows:
        table.add_row(
            row.sha[:SHORT_SHA_LENGTH],
            row.date,
            shorten(row.author, AUTHOR_WIDTH),
            str(row.files_changed),
            f"+{row.insertions}/-{row.deletions}",
            shorten(row.message, message_width),
        )
    return table


def _row(commit: Commit) -> TimelineRow:
    return TimelineRow(
        sha=commit.sha,
        date=f"{commit.committed_at:%Y-%m-%d}",
        author=commit.author_name,
        files_changed=len(commit.changes),
        insertions=sum(change.added_lines or 0 for change in commit.changes),
        deletions=sum(change.deleted_lines or 0 for change in commit.changes),
        message=commit.message.splitlines()[0] if commit.message else "",
    )
