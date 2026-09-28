"""The single-file view: one file's life, and the numbers it added up to.

The block it prints is the whole of what the model knows about one file: the
names it carried, when it appeared, when it was last changed, what it cost in
lines, and whether it is still there. Every value comes from a commit that git
reported, so a reader can go and look.

The JSON is the same facts in the same names, for programs instead of eyes.
Both carry everything the model has, so neither can be read as a summary that
left something out.
"""

import json
from collections.abc import Iterable
from datetime import datetime

from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.lifecycle import Lifecycle
from codearchaeology.statistics import summarize

LABELS = (
    "Created:",
    "Last modified:",
    "Deleted:",
    "History:",
    "Commits:",
    "Modifications:",
    "Renames:",
    "Binary changes:",
    "Additions:",
    "Deletions:",
    "Net change:",
)
LABEL_WIDTH = max(len(label) for label in LABELS) + 1


def normalise(path: str) -> str:
    """Put a path into the form git writes them in.

    Git always uses forward slashes, a Windows user types backslashes, and a
    shell completion may hand over a leading ``./``. None of that changes which
    file is meant, so it is settled here rather than in every caller.
    """
    return path.replace("\\", "/").removeprefix("./")


def find_files(lives, path: str) -> tuple[Lifecycle, ...]:
    """Every life that ever carried *path*, oldest first.

    A name can belong to more than one file: one that was deleted and later
    created again, or one renamed away whose name was taken back afterwards.
    All of them come back. Returning only the newest would silently hide the
    others, which is the mistake the lifecycle model exists to avoid.
    """
    return tuple(life for life in lives if path in life.path_history)


def build_block(lifecycle: Lifecycle) -> str:
    """Render one file's life as the lines the command prints."""
    statistics = summarize(lifecycle)
    lines = [lifecycle.current_path]

    if len(lifecycle.path_history) > 1:
        lines.append(_line("History:", " -> ".join(lifecycle.path_history)))

    lines.append("")
    lines.append(
        _line("Created:", _moment(statistics.created_at, statistics.created_sha))
    )
    lines.append(
        _line(
            "Last modified:",
            "-"
            if statistics.last_modified_at is None
            else _moment(statistics.last_modified_at, statistics.last_modified_sha),
        )
    )
    if statistics.is_deleted:
        ended = lifecycle.deleted
        lines.append(_line("Deleted:", _moment(ended.committed_at, ended.commit_sha)))

    lines.append("")
    lines.append(_line("Commits:", str(statistics.commits)))
    lines.append(_line("Modifications:", str(statistics.modifications)))
    lines.append(_line("Renames:", str(statistics.renames)))
    if statistics.binary_changes:
        # Printed only when it has something to say: git gives no line counts for
        # a binary file, so without this the zeroes above would read as "nothing
        # ever happened here".
        lines.append(_line("Binary changes:", str(statistics.binary_changes)))
    lines.append(_line("Additions:", str(statistics.additions)))
    lines.append(_line("Deletions:", str(statistics.deletions)))

    lines.append("")
    lines.append(_line("Net change:", f"{statistics.net_change:+d}"))

    return "\n".join(lines)


def build_object(lifecycle: Lifecycle) -> dict:
    """One file's life as the fields other programs read.

    ``path`` is the name the file has now, not the one it was asked about: a
    life is followed across its renames, so the two differ exactly when the file
    was renamed. The names it carried are all in ``path_history``.

    ``deleted`` is the flag, so a reader that only wants to know whether the file
    is still there does not have to infer it from a null date.
    """
    statistics = summarize(lifecycle)
    ended = lifecycle.deleted

    return {
        "path": lifecycle.current_path,
        "commits": statistics.commits,
        "additions": statistics.additions,
        "deletions": statistics.deletions,
        "renames": statistics.renames,
        "deleted": statistics.is_deleted,
        "created_at": statistics.created_at.isoformat(),
        "created_sha": statistics.created_sha,
        "last_modified_at": _timestamp(statistics.last_modified_at),
        "last_modified_sha": statistics.last_modified_sha,
        "deleted_at": _timestamp(None if ended is None else ended.committed_at),
        "deleted_sha": None if ended is None else ended.commit_sha,
        "modifications": statistics.modifications,
        "binary_changes": statistics.binary_changes,
        "path_history": list(lifecycle.path_history),
        "net_change": statistics.net_change,
    }


def build_json(lives: Iterable[Lifecycle], path: str) -> str:
    """The whole answer for *path*, as JSON.

    The answer is always an object holding a list, even when the name belongs to
    a single file. A name can belong to several, and the shape must not depend on
    which repository it was pointed at: a reader would have to work out which of
    the two it got before it could read either.

    ``path`` at the top is what was asked for, and each entry's ``path`` is what
    that file is called now.
    """
    return json.dumps(
        {"path": path, "files": [build_object(life) for life in lives]},
        indent=2,
        ensure_ascii=False,
    )


def _line(label: str, value: str) -> str:
    return f"{label:<{LABEL_WIDTH}}{value}"


def _moment(when: datetime, sha: str) -> str:
    return f"{when:%Y-%m-%d %H:%M:%S %z}  {sha[:SHORT_SHA_LENGTH]}"


def _timestamp(when: datetime | None) -> str | None:
    """A moment in the form other programs parse, or ``None`` when there is none.

    Not ``str(when)``: that drops the UTC offset, and a commit's time is only
    meaningful with it.
    """
    return None if when is None else when.isoformat()
