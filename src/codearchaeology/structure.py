"""The structure view: what one Python file held, and how its definitions changed.

This is the reading end of the AST layer. The derivation in
``definition_history`` works out what happened; this module decides how to say
it, and the lines it prints are the whole of what the derivation knows.

The command has two faces, and each has a block and a JSON:

``structure <path>``
    One file version — the definitions it held, with their kind, their lines,
    their decorators and what changed about each of them.
``structure <path> --history``
    How each definition in the file changed over the file's whole life.

Three things the rendering refuses to round off:

* **A version nobody could read does not print an empty listing.** It says
  ``AST analysis unavailable`` and why — the parser's own words, that no version
  was stored for that commit, or that the file was not in it. "Nothing was there"
  and "nothing could be read" must not look alike, in the block or in the JSON.
* **A change derived across a version nobody could read says so**, on the line
  under it, naming the versions in between. The event line can only claim that
  the change had happened *by* the version that showed it.
* **A definition whose file was never read again is given no ending.** Its last
  line says the versions after it could not be read, instead of a deletion that
  was never seen.

The versions that could not be read are listed once, under the history's header,
with the reason each one gave. Without that list a gap that no event happened to
span would leave no trace in the output at all.

``change_type`` on a definition row is the pass's cached comparison against the
nearest version of the same file that could be read, so a version that was
compared across unreadable ones says which version it was compared against. The
JSON carries the same two fields — ``compared_with`` and ``blind_spots`` — for
the same reason.

Both JSON blocks follow the v0.2 rule: stdout is the JSON and nothing else, and
everything a reader needs to know about *why* the data looks the way it does is a
field, not a sentence. Notices that are not data go to stderr, where the command
puts them.
"""

import json

from rich import box
from rich.table import Table

from codearchaeology.definition_history import (
    CREATED,
    DELETED,
    MODIFIED,
    NOT_IN_TREE,
    NOT_STORED,
    BlindSpot,
    DefinitionEvent,
    DefinitionLife,
    DefinitionVersion,
    FileHistory,
    Snapshot,
)
from codearchaeology.definitions import FUNCTION_SCOPE
from codearchaeology.formatting import SHORT_SHA_LENGTH, shorten

LABELS = ("History:", "Versions:", "Version:", "Definitions:")
LABEL_WIDTH = max(len(label) for label in LABELS) + 1

# An event sits two spaces in, under the definition it belongs to; a note about
# an event sits under the event, far enough in to read as part of it.
EVENT_INDENT = "  "
NOTE_INDENT = "      "

# What each change is, in the sentence that says when it happened. The event
# line already carries the word itself; this is for the note under it, where
# "so when it changed is not known" reads better than repeating the noun.
WHEN = {CREATED: "appeared", MODIFIED: "changed", DELETED: "went"}


def build_history(history: FileHistory) -> str:
    """Render one file's life and the definitions in it."""
    lines = [history.path_history[-1]]
    if len(history.path_history) > 1:
        lines.append(_line("History:", " -> ".join(history.path_history)))

    read = sum(1 for version in history.versions if isinstance(version, Snapshot))
    unread = [version for version in history.versions if isinstance(version, BlindSpot)]
    lines.append(_line("Versions:", f"{read} read, {len(unread)} could not be read"))
    for spot in unread:
        lines.append(f"{EVENT_INDENT}{_short(spot.commit_sha)}  {spot.reason}")

    lines.append("")
    if not history.lives:
        lines.append(_nothing(history, read))
        return "\n".join(lines)

    for position, life in enumerate(history.lives):
        if position:
            lines.append("")
        lines.extend(_life(life))
    return "\n".join(lines)


def nothing_read_note(histories, path: str) -> str | None:
    """Why no definition history can be shown, or ``None`` when some can.

    A file whose versions were never read has no history, and the two reasons for
    that are worth telling apart: the AST pass has not run, or it ran and every
    version of this file failed. The first is the ordinary state of a repository
    that has only been analyzed, so it names the command that fixes it.
    """
    versions = [version for history in histories for version in history.versions]
    if any(isinstance(version, Snapshot) for version in versions):
        return None

    unread = [version for version in versions if isinstance(version, BlindSpot)]
    if all(spot.reason == NOT_STORED for spot in unread):
        return f"no version of {path} has been read; run 'archaeology ast' first"

    # Every version that was stored failed. The first failure is named because it
    # is the one a reader would want to look at, and its own words say more than
    # "could not be read" does.
    first = unread[0]
    return (
        f"no version of {path} could be read;"
        f" the first failure was {_short(first.commit_sha)}: {first.reason}"
    )


def _life(life: DefinitionLife) -> list[str]:
    """One definition: its events, and what each of them cannot say."""
    heading = f"{life.qualname}  {life.kind}"
    if life.occurrence:
        heading += f"  ({_ordinal(life.occurrence + 1)} of that name)"

    lines = [heading]
    for event in life.events:
        lines.append(EVENT_INDENT + _event(event))
        note = _note(event)
        if note:
            lines.append(NOTE_INDENT + note)

    if life.unresolved:
        # The definition was still there in the last version that could be read,
        # and the file went dark after it. There is no ending to print, and
        # printing one would be inventing it.
        names = ", ".join(_short(spot.commit_sha) for spot in life.unresolved)
        lines.append(
            NOTE_INDENT
            + f"no ending: {names} could not be read, so whether it is still"
            f" there is not known"
        )
    return lines


def _event(event: DefinitionEvent) -> str:
    line = f"{event.change:<9}{_moment(event.committed_at, event.commit_sha)}"
    if event.lineno is not None:
        line += f"  line {event.lineno}"
    return line


def _note(event: DefinitionEvent) -> str | None:
    """What the event cannot say, when it was derived across a version nobody read.

    Empty when the two versions are neighbours, which is the ordinary case: the
    change happened at the version that showed it, and the line says so.
    """
    if not event.blind_spots:
        return None

    names = ", ".join(_short(spot.commit_sha) for spot in event.blind_spots)
    if event.previous_commit_sha is None:
        # Nothing before it could be read, so this is the first version that
        # *showed* the definition. Whether it existed earlier is not known, and
        # "created" must not be read as proof that it did not.
        return f"first seen here; {names} could not be read, so it may be older"

    previous = _short(event.previous_commit_sha)
    return (
        f"at some point after {previous}; {names} could not be read,"
        f" so when it {WHEN[event.change]} is not known"
    )


def _nothing(history: FileHistory, read: int) -> str:
    """Why there is nothing to list."""
    if read:
        return "No definitions in the versions that were read."
    if all(
        spot.reason == NOT_STORED
        for spot in history.versions
        if isinstance(spot, BlindSpot)
    ):
        return "No definitions: no version of this file has been read."
    return "No definitions: no version of this file could be read."


def _ordinal(number: int) -> str:
    """``2nd``, ``3rd``, ``11th`` — which definition of a repeated name this is."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _line(label: str, value: str) -> str:
    return f"{label:<{LABEL_WIDTH}}{value}"


def _short(sha: str) -> str:
    return sha[:SHORT_SHA_LENGTH]


def _moment(when, sha: str) -> str:
    return f"{when:%Y-%m-%d %H:%M:%S %z}  {_short(sha)}"


# --- the structure view: one file version -----------------------------------

# What the block says instead of a listing when a version could not be read. The
# first is the user's own phrasing; the other two name the case in the same
# shape, and the parser's own words go on the line under it.
PARSE_FAILED = "parse failed"
NO_VERSION = "no version was stored for this commit"
NOT_IN_THIS_COMMIT = "the file was not in this commit"

# The kind words, in the order they are counted, with both of their plurals.
KIND_NAMES = (
    ("class", "class", "classes"),
    ("function", "function", "functions"),
    ("async function", "async function", "async functions"),
)

KIND_WIDTH = len("async function")
LINES_WIDTH = 7
CHANGE_WIDTH = len("unchanged")
DEFINITION_MINIMUM_WIDTH = 18
# Wide enough for the decorators a file usually has (``@staticmethod`` is 13
# cells, ``@app.route("/x")`` is 17). A longer one wraps under itself rather than
# squeezing the columns that every row carries.
DECORATORS_MINIMUM_WIDTH = 18
# Rich's own padding between five columns, and the slack that keeps the total
# inside the terminal: the widths below add up to at least this much less than
# the console, so no column is ever squeezed below its minimum.
COLUMN_PADDING = 10
COLUMN_SLACK = 6
FIXED_WIDTH = (
    KIND_WIDTH
    + LINES_WIDTH
    + CHANGE_WIDTH
    + DECORATORS_MINIMUM_WIDTH
    + COLUMN_PADDING
    + COLUMN_SLACK
)


def select_version(histories, commit_sha: str | None = None):
    """The version to show: the file's newest one, or the one at *commit_sha*.

    Returns ``(history, version)``, or ``None`` when there is no such version —
    which for *commit_sha* means the commit is in the stored history but did not
    change this file, and for the default means the file's lives hold no version
    at all.

    A version nobody could read is a version like any other and comes back as it
    is. What the command prints for it is the reason it could not be read, which
    is the answer to "what was in this file at that commit".
    """
    if commit_sha is not None:
        for history in histories:
            for version in history.versions:
                if version.commit_sha == commit_sha:
                    return history, version
        return None

    # The newest life's newest version: a name can belong to more than one file,
    # and "app.py" without a commit means the one that is called that now.
    for history in reversed(histories):
        if history.versions:
            return history, history.versions[-1]
    return None


def definitions_in(version) -> tuple[DefinitionVersion, ...]:
    """The definitions of a version that was read, in the order they appeared.

    Empty for a version nobody could read — and that must not be read as "the
    file held nothing". The block and the JSON both say which of the two it was;
    this function is not where that is decided.
    """
    return version.definitions if isinstance(version, Snapshot) else ()


def build_version(history: FileHistory, version) -> str:
    """The lines above the listing: which version this is, and what it held.

    A version nobody could read stops here, with the reason. Printing a listing
    of zero definitions for it would read as a version that held none, which is
    the one thing this view must never say.
    """
    lines = [version.path]
    if len(history.path_history) > 1:
        lines.append(_line("History:", " -> ".join(history.path_history)))
    lines.append(_line("Version:", _moment(version.committed_at, version.commit_sha)))

    if isinstance(version, BlindSpot):
        lines.append("")
        lines.append(f"AST analysis unavailable: {_reason(version)}")
        if version.reason == NOT_STORED:
            lines.append("  run 'archaeology ast' to read the file versions git holds")
        elif version.reason != NOT_IN_TREE:
            lines.append(f"  {_failure(version)}")
        return "\n".join(lines)

    if not version.definitions:
        lines.append("")
        lines.append("No definitions: the version was read and held none.")
        return "\n".join(lines)

    lines.append(_line("Definitions:", _counts(version.definitions)))
    compared_with, blind = _comparison(history, version)
    note = _comparison_note(compared_with, blind)
    if note:
        lines.append(_line("", note))
    return "\n".join(lines)


def build_version_table(definitions, width: int) -> Table:
    """The listing: one line per definition, in the order the file had them.

    The kind is the stored word, and a method is a function whose enclosing scope
    is a class in the same file — which the qualified name already shows. No
    second naming scheme is invented for it here.
    """
    definition_width = max(DEFINITION_MINIMUM_WIDTH, width - FIXED_WIDTH)

    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    table.add_column("KIND", no_wrap=True, min_width=KIND_WIDTH)
    table.add_column("DEFINITION", no_wrap=True, min_width=definition_width)
    table.add_column("LINES", no_wrap=True, justify="right", min_width=LINES_WIDTH)
    # The order is the one the user listed the fields in, and the decorators come
    # before the change because a long one is shortened rather than allowed to
    # squeeze the column every row has.
    table.add_column("DECORATORS", min_width=DECORATORS_MINIMUM_WIDTH)
    table.add_column("CHANGE", no_wrap=True, min_width=CHANGE_WIDTH)

    for definition in definitions:
        table.add_row(
            definition.kind,
            shorten(definition.qualname, definition_width),
            _line_range(definition),
            ", ".join(f"@{name}" for name in definition.decorators),
            definition.change_type,
        )
    return table


def build_version_json(history: FileHistory, version) -> str:
    """The whole answer for one file version, as JSON.

    ``state`` is ``read`` or ``unavailable``, so that a reader never has to infer
    "nobody could read this" from an empty list; ``reason`` says which kind of
    unavailable, and ``parse_error`` carries the parser's own words when there
    are any.

    ``compared_with`` and ``blind_spots`` belong to the ``change_type`` field of
    each definition: the words are the pass's cached comparison against the
    nearest version that could be read, and these two say which version that was
    and what stood between the two.
    """
    payload = {
        "path": version.path,
        "path_history": list(history.path_history),
        "commit_sha": version.commit_sha,
        "committed_at": version.committed_at.isoformat(),
        **_state_fields(version),
        "compared_with": None,
        "blind_spots": [],
        "definitions": [],
    }

    if isinstance(version, Snapshot):
        compared_with, blind = _comparison(history, version)
        payload["compared_with"] = compared_with
        payload["blind_spots"] = [spot.commit_sha for spot in blind]
        payload["definitions"] = [
            _definition_object(definition) for definition in version.definitions
        ]
    return json.dumps(payload, indent=2, ensure_ascii=False)


def build_history_json(histories, path: str) -> str:
    """Every life *path* took part in, with its versions and its definitions' lives.

    ``change`` on an event is what the query layer derived from two snapshots —
    ``deleted`` is never a stored word, and the fields beside it are the two
    versions it was derived from.
    """
    return json.dumps(
        {"path": path, "files": [_file_object(history) for history in histories]},
        indent=2,
        ensure_ascii=False,
    )


def _file_object(history: FileHistory) -> dict:
    return {
        "path_history": list(history.path_history),
        "versions": [_version_object(version) for version in history.versions],
        "lives": [_life_object(life) for life in history.lives],
    }


def _version_object(version) -> dict:
    return {
        "commit_sha": version.commit_sha,
        "path": version.path,
        "committed_at": version.committed_at.isoformat(),
        **_state_fields(version),
    }


def _life_object(life: DefinitionLife) -> dict:
    return {
        "qualname": life.qualname,
        "kind": life.kind,
        "occurrence": life.occurrence,
        "events": [_event_object(event) for event in life.events],
        "unresolved": [spot.commit_sha for spot in life.unresolved],
    }


def _event_object(event: DefinitionEvent) -> dict:
    return {
        "change": event.change,
        "commit_sha": event.commit_sha,
        "path": event.path,
        "committed_at": event.committed_at.isoformat(),
        "lineno": event.lineno,
        "end_lineno": event.end_lineno,
        "decorators": list(event.decorators),
        "previous_commit_sha": event.previous_commit_sha,
        "blind_spots": [spot.commit_sha for spot in event.blind_spots],
    }


def _definition_object(definition: DefinitionVersion) -> dict:
    return {
        "qualname": definition.qualname,
        "kind": definition.kind,
        "lineno": definition.lineno,
        "end_lineno": definition.end_lineno,
        "decorators": list(definition.decorators),
        "change_type": definition.change_type,
    }


def _state_fields(version) -> dict:
    """How to read a version: the state, the reason, and the parser's own words."""
    if isinstance(version, Snapshot):
        return {
            "state": "read",
            "reason": None,
            "parse_error": None,
            "error_lineno": None,
            "error_offset": None,
        }

    reason = _reason(version)
    failed = reason == PARSE_FAILED
    return {
        "state": "unavailable",
        "reason": reason,
        "parse_error": version.reason if failed else None,
        "error_lineno": version.lineno if failed else None,
        "error_offset": version.offset if failed else None,
    }


def _reason(version: BlindSpot) -> str:
    """Why a version could not be read, in the words the block uses."""
    if version.reason == NOT_STORED:
        return NO_VERSION
    if version.reason == NOT_IN_TREE:
        return NOT_IN_THIS_COMMIT
    return PARSE_FAILED


def _failure(version: BlindSpot) -> str:
    """The parser's own words, and where it stopped when it said where."""
    if version.lineno is None:
        return version.reason
    if version.offset is None:
        return f"{version.reason} (line {version.lineno})"
    return f"{version.reason} (line {version.lineno}, column {version.offset})"


def _counts(definitions) -> str:
    """``3 (1 class, 2 functions, 1 of them a method)``."""
    counted = {kind: 0 for kind, _, _ in KIND_NAMES}
    for definition in definitions:
        counted[definition.kind] = counted.get(definition.kind, 0) + 1

    classes = {row.qualname for row in definitions if row.kind == "class"}
    methods = sum(
        1
        for row in definitions
        if row.kind != "class" and _enclosing(row.qualname) in classes
    )

    parts = [
        f"{counted[kind]} {singular if counted[kind] == 1 else plural}"
        for kind, singular, plural in KIND_NAMES
        if counted[kind]
    ]
    if methods == 1:
        parts.append("1 of them a method")
    elif methods:
        parts.append(f"{methods} of them methods")
    return f"{len(definitions)} ({', '.join(parts)})"


def _enclosing(qualname: str) -> str:
    """The scope a qualified name sits in: ``Cache.get`` is in ``Cache``.

    A function defined inside another function is not a method, and Python marks
    that case in the name itself: ``outer.<locals>.inner`` sits in ``outer``,
    which is not a class.
    """
    if FUNCTION_SCOPE in qualname:
        return qualname.split(FUNCTION_SCOPE)[0]
    return qualname.rpartition(".")[0]


def _line_range(definition: DefinitionVersion) -> str:
    """``4-5``, or ``4`` when the definition is one line long."""
    if definition.end_lineno == definition.lineno:
        return str(definition.lineno)
    return f"{definition.lineno}-{definition.end_lineno}"


def _comparison(
    history: FileHistory, version: Snapshot
) -> tuple[str | None, tuple[BlindSpot, ...]]:
    """The version this one was compared against, and the blind spots in between.

    ``change_type`` compares against the nearest earlier version of the same file
    that could be read, so this is the version those words are about. When the
    two are not neighbours, the words cannot say *when* in between the change
    happened, and the blind spots are the reason.
    """
    compared_with: str | None = None
    blind: list[BlindSpot] = []
    for step in history.versions:
        if step.commit_sha == version.commit_sha:
            break
        if isinstance(step, Snapshot):
            compared_with = step.commit_sha
            blind = []
        else:
            blind.append(step)
    return compared_with, tuple(blind)


def _comparison_note(compared_with: str | None, blind) -> str | None:
    """The line that says what the change types were compared against."""
    if not blind:
        return None
    names = ", ".join(_short(spot.commit_sha) for spot in blind)
    if compared_with is None:
        return (
            f"Change types are against nothing readable: {names} could not be"
            f" read, so `created` may be older"
        )
    return (
        f"Change types are against {_short(compared_with)}; {names} in between"
        f" could not be read"
    )
