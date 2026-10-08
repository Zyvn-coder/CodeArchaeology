"""Write a repository's memories to a file a person can carry.

A memory lives in exactly one place — a database named after a digest of the
repository's path, in the operating system's cache directory — and until this
module there was nothing the tool offered that could get one out. What it writes
is a text file that is a faithful copy of a repository's memories: something a
person can back up, keep beside a checkout, or hand to somebody else. The freeze
is ``docs/v0.5.1-portability-design.md``; this is what it decided, and the code
looks like this because of it.

**A row carries what the store holds, and nothing the store works out.** That
one sentence decides four exclusions, and each has a different answer behind it:

* ``subject.resolution`` and ``citations[].resolution`` — the evidence layer
  answers them, against **this** repository's stored history. Carried into
  another repository they would be an answer to a different question, which is
  worse than a missing one.
* ``since.commit_date`` — it is the cited commit's recorded date, read out of
  ``commits``. The repository the file travels to re-reads it, or cannot.
* ``supersedes`` — it is the reverse of ``superseded_by``. The store keeps one
  direction so that two rows cannot disagree about it, and a file is storage.

What remains is what the store actually keeps, and it is built by the same three
functions ``memory_view`` uses for every other rendering of a memory — so a field
added to that object cannot go missing here, and the four above are absent
because they are added *beside* the rendering rather than held in the memory.

**The consequence is the property worth having: this module reads no evidence.**
No commit, no file version, no definition, no statistic. It cannot be misled by a
stale analysis, it performs no citation check, and its cost is a scan of the two
memory tables. Export is therefore the thing a person can still reach for when
the analysis is what is broken.

**The bytes are the same on every platform.** UTF-8 without a BOM, ``\\n``
between the lines and after the last one, and a file opened with ``newline=""``
so no translation layer can reach it: a file whose bytes depend on where it was
written is a file whose diffs are noise, and this one is meant to be diffed. One
memory per line is guaranteed by JSON itself — a statement containing a newline
is escaped — which is the property the whole format rests on.

**Ascending by ``memory_id``**, so that the order is a function of the set rather
than of the order things were written in. Admission order cannot serve: two
memories can share a timestamp, which is why the store keeps microseconds and
still does not promise a total order on the clock. A UUID is unique by
construction.
"""

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from codearchaeology.memory import Memory
from codearchaeology.memory_view import author_object, since_object, subject_object

# The version of the file's shape, and the key a reader checks it by. It is the
# file's own version and not the tables': a format and a table shape are two
# contracts, which is the same reason the store stamps its shape separately from
# the evidence schema's.
FORMAT_VERSION = "1"
VERSION_KEY = "memory_export_version"

# What ends every line, on every platform. Named rather than inlined because it
# is part of the format and not a formatting choice.
ENDING = "\n"


class MemoryExportError(RuntimeError):
    """Raised when the file cannot be written as asked."""


def header_line(*, repository: str, count: int, exported_at: datetime) -> dict:
    """The first line: what the file is, and what it is about.

    Four fields and each has a job. The version is the reader's check; the
    repository is what an import act names, so it can say "these were made about
    *that* path" without parsing every row; the count is the integrity check, so
    that a truncated file is loud rather than silent; and the moment is for a
    person reading the file later.

    The tool's own version is *not* here. The format version is the
    compatibility contract and the tool version is not, and the reader would
    ignore it — which is the field nobody verified, and the reason
    ``docs/v0.4-explanation-schema.md`` §6 refuses an unknown field in a model's
    answer rather than dropping it quietly.
    """
    return {
        VERSION_KEY: FORMAT_VERSION,
        "repository": repository,
        "count": count,
        "exported_at": exported_at.isoformat(),
    }


def memory_line(memory: Memory) -> dict:
    """One memory as the file carries it.

    The key order is ``memory_view.memory_object``'s, for the fields the two
    share, so that a person putting a line beside ``memory show --json`` reads
    them in the same sequence and finds the four absences where they would have
    been.
    """
    return {
        "memory_id": memory.memory_id,
        "repository": memory.repository_path,
        "statement": memory.statement,
        "author": author_object(memory),
        "admitted_at": memory.admitted_at.isoformat(),
        "state": memory.state,
        "subject": subject_object(memory.subject),
        "since": since_object(memory),
        "ended_at": None if memory.ended_at is None else memory.ended_at.isoformat(),
        "end_reason": memory.end_reason,
        "superseded_by": memory.superseded_by,
        "citations": [
            {"kind": citation.kind, "ref": citation.ref}
            for citation in memory.citations
        ],
    }


def build_document(
    memories: Iterable[Memory], *, repository: str, exported_at: datetime
) -> str:
    """The whole file, as one string.

    ``exported_at`` is an argument rather than read from the clock in here so
    that the bytes are a function of their arguments: a test can pin them, and
    the determinism the design promises is checkable rather than merely
    asserted.

    ``repository`` is passed in and not derived from the rows because a store
    with no memories still has to say what it is about — an empty export that
    named nothing would be an anonymous file, and "this project had no memories
    when I exported" is a useful thing for a backup to say.
    """
    ordered = sorted(memories, key=lambda memory: memory.memory_id)
    lines = [
        _as_line(
            header_line(
                repository=repository, count=len(ordered), exported_at=exported_at
            )
        )
    ]
    lines.extend(_as_line(memory_line(memory)) for memory in ordered)
    return "".join(f"{line}{ENDING}" for line in lines)


def write_document(document: str, target: Path, *, replace: bool) -> None:
    """Write *document* to *target*, refusing to replace a file unless asked.

    The refusal lives here rather than in the command so that the rule has one
    home and a test can reach it without a command line. **This tool has no
    interactive prompt anywhere** — ``docs/v0.5-cli-design.md`` §6.6 makes that a
    rule — so the safety cannot be a question. It is a sentence and a flag: the
    destructive case is a typo, a refused export costs one flag, and a destroyed
    file costs a file.

    The parent directory is not created. Every other path the tool writes to is
    one it chose itself; this one is a path a person typed, and making
    directories out of a typo is guessing rather than helping. A path that cannot
    be written is refused with the reason.
    """
    if target.exists() and not replace:
        raise MemoryExportError(
            f"{target} exists; this command does not replace a file it did not"
            f" write. Give another path, or pass --force to replace it"
        )
    try:
        target.write_text(document, encoding="utf-8", newline="")
    except OSError as error:
        raise MemoryExportError(
            f"{target} could not be written: {error.strerror or error}"
        ) from None


def _as_line(value: dict) -> str:
    """One JSON value on one line, with non-ASCII kept as itself.

    The default separators, not the compact ones: the file is read by people as
    well as by programs, and a space after a colon costs two bytes on a line that
    is already a whole memory long.
    """
    return json.dumps(value, ensure_ascii=False)
