"""The detail view of a single commit."""

from rich import box
from rich.table import Table

from codearchaeology.analysis import open_analysis
from codearchaeology.formatting import shorten
from codearchaeology.history import Commit, FileChange
from codearchaeology.storage import find_commits

CHANGE_NAMES = {
    "A": "added",
    "C": "copied",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "T": "type changed",
    "U": "unmerged",
    "X": "unknown",
}

CANDIDATES_SHOWN = 5
CANDIDATE_SHA_LENGTH = 12

CHANGE_WIDTH = max(len(name) for name in CHANGE_NAMES.values())
COUNTERS_WIDTH = 12
PATH_MINIMUM_WIDTH = 20
COLUMN_PADDING = 6
COLUMN_SLACK = 5
PATH_FIXED_WIDTH = (
    CHANGE_WIDTH + COUNTERS_WIDTH + COLUMN_PADDING + COLUMN_SLACK
)

HEXADECIMAL = frozenset("0123456789abcdefABCDEF")


class CommitNotFound(LookupError):
    """Raised when a sha prefix does not name exactly one stored commit."""


def load_commit(repository_root, database, prefix: str) -> Commit:
    """Return the one stored commit whose sha starts with *prefix*."""
    if not prefix or any(character not in HEXADECIMAL for character in prefix):
        raise CommitNotFound(
            f"{prefix!r} is not a commit sha;"
            f" use one from 'archaeology timeline'"
        )

    with open_analysis(repository_root, database) as connection:
        matches = find_commits(connection, prefix)

    if not matches:
        raise CommitNotFound(f"no stored commit starts with {prefix!r}")
    if len(matches) > 1:
        raise CommitNotFound(
            f"{prefix!r} matches {len(matches)} commits: {_candidates(matches)};"
            f" give more characters"
        )
    return matches[0]


def line_counters(change: FileChange) -> str:
    """Return ``+12/-3``, or ``-`` for a binary file, which has no counts."""
    if change.is_binary:
        return "-"
    return f"+{change.added_lines}/-{change.deleted_lines}"


def change_name(change: FileChange) -> str:
    """Return the word for a change type, e.g. ``renamed``."""
    return CHANGE_NAMES.get(change.change_type, change.change_type)


def build_file_table(changes, width: int) -> Table:
    """Render a commit's files as a Rich table, one line per file.

    The path is cut to whatever the terminal has left over, for the same reason
    the timeline cuts its message: a long path would otherwise squeeze the other
    columns out of existence.
    """
    path_width = max(PATH_MINIMUM_WIDTH, width - PATH_FIXED_WIDTH)

    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    table.add_column("FILE", no_wrap=True, min_width=path_width)
    table.add_column("+/-", justify="right", no_wrap=True, min_width=COUNTERS_WIDTH)
    table.add_column("CHANGE", no_wrap=True, min_width=CHANGE_WIDTH)

    for change in changes:
        table.add_row(
            shorten(_path(change), path_width),
            line_counters(change),
            change_name(change),
        )
    return table


def _candidates(matches) -> str:
    shown = [
        commit.sha[:CANDIDATE_SHA_LENGTH] for commit in matches[:CANDIDATES_SHOWN]
    ]
    if len(matches) > CANDIDATES_SHOWN:
        shown.append(f"and {len(matches) - CANDIDATES_SHOWN} more")
    return ", ".join(shown)


def _path(change: FileChange) -> str:
    if change.old_path is None:
        return change.path
    return f"{change.old_path} \u2192 {change.path}"
