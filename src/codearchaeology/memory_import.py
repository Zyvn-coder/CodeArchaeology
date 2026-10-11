"""Put a repository's memories back from the file a person carried.

The freeze is ``docs/v0.5.2-import-design.md``, and this module is what it
decided: the smallest import that makes a ``memory export`` file usable again —
*this* repository's memories, into *this* repository's store, with every field
written exactly as the file carries it and nothing else.

**What it is not, and the shape of the whole module.** It does not merge two
projects' memories, does not import a fork's into another repository, does not
rewrite a path nobody typed, and does not re-resolve evidence. A file that names
another repository is refused by name unless a person asserts the move with
``--from`` — ``adopt``'s device, and the only path change the act accepts.

**The four rules that shape every line below.**

* **The file is the unit of success.** One malformed line, one conflicting row,
  one dangling lifecycle link refuses the whole file and writes nothing. There
  is no "import the rest": a store that is part file and part store has no
  record of which is which.
* **An import that inserts nothing writes nothing at all** — no rows, no
  ``meta``, no tables, no stamp, no write transaction — so running it twice
  leaves the store identical at both levels, its rows and its file's bytes.
* **A row is never edited.** The plan has two write shapes (insert a row that is
  absent; write nothing for a row that is identical) and this module contains no
  ``UPDATE`` or ``DELETE`` against the memory tables at all.
* **The transaction is opened explicitly.** Python's ``sqlite3`` starts its
  implicit transaction only at the first DML statement, so a ``CREATE TABLE``
  executed before one is committed outside the transaction — measured, and the
  reason :func:`write_import` issues ``BEGIN`` itself and never uses
  ``executescript``.

**The store the act may find.** ``_ABSENT`` — no memory tables and no stamp,
which is what a freshly analyzed store looks like — is the ordinary case: the
tables and the stamp are created *inside* the write transaction, so a failure
takes them with it and cannot leave a store that refuses every later read.
``_UNSTAMPED``, ``_UNREADABLE`` and ``_MISSING_TABLES`` are refused with
``memory``'s own sentences before anything is written. ``prepare_memory`` and
``storage.set_meta`` are never called on this path: both write, and ``set_meta``
commits — which would end the transaction in the middle of it.

**What the act records about itself.** Two ``meta`` keys, mirroring ``adopt``'s
pair, and only when a row was inserted: the path the file was read from and the
moment. An import that skipped everything leaves no trace, which is the
idempotence contract stated as a property of the bytes rather than of the rows.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from codearchaeology import memory
from codearchaeology.memory import (
    CITATION_KINDS,
    MEMORY_SCHEMA,
    MEMORY_SCHEMA_VERSION,
    MEMORY_VERSION_KEY,
    MemoryStoreError,
    STATES,
    STATEMENT_CHARACTERS,
    SUBJECT_KINDS,
    foreign_memories,
    repository_identity,
)
from codearchaeology.memory_export import FORMAT_VERSION, VERSION_KEY

# The two keys the act records about itself, mirroring ``adopt``'s pair. They
# name the last import act and not a log: a per-act history would be a new
# table, and a new table is a schema change this unit does not need.
IMPORTED_FROM_KEY = "memory_imported_from"
IMPORTED_AT_KEY = "memory_imported_at"

# The header's fields and a row's, in the order the writer emits them. A key
# that is not here is refused, because a field nobody reads is a claim nobody
# verified — the rule the format's own freeze applies to the header, applied to
# the whole line.
HEADER_KEYS = (VERSION_KEY, "repository", "count", "exported_at")
ROW_KEYS = (
    "memory_id",
    "repository",
    "statement",
    "author",
    "admitted_at",
    "state",
    "subject",
    "since",
    "ended_at",
    "end_reason",
    "superseded_by",
    "citations",
)

# The fields each subject kind carries, exactly — ``memory._checked_subject``'s
# rule, stated as a table so the reader can refuse both a missing and an extra
# one by name.
SUBJECT_FIELDS = {
    "repository": (),
    "path": ("path",),
    "definition": ("path", "qualname"),
    "commit": ("commit",),
}

# The characters a commit sha holds: ``commit.HEXADECIMAL``'s set, written out
# rather than imported, because the reader is a pure function of the file's text
# and does not reach into the evidence layer. ``storage.find_commits`` uses the
# value as a ``LIKE`` pattern and says so itself — the prefix "has to hold sha
# characters and nothing else".
SHA_CHARACTERS = frozenset("0123456789abcdefABCDEF")


class MemoryImportError(RuntimeError):
    """Raised when a file cannot be imported, with the sentence for the person."""


@dataclass(frozen=True, slots=True)
class Header:
    """The file's first line: what it is, and what it is about."""

    version: str
    repository: str
    count: int
    exported_at: str


@dataclass(frozen=True, slots=True)
class Row:
    """One memory line, validated, with every field the store keeps.

    The two derived integers are not here: they are the same instants in
    another spelling, and :func:`_epoch_of` rebuilds them at the moment of the
    write (design §6.1).
    """

    memory_id: str
    repository: str
    statement: str
    author_name: str | None
    author_email: str | None
    admitted_at: str
    subject_kind: str
    subject_path: str | None
    subject_qualname: str | None
    subject_commit_sha: str | None
    since_commit_sha: str | None
    since_date: str | None
    state: str
    ended_at: str | None
    end_reason: str | None
    superseded_by: str | None
    citations: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Document:
    """A whole file: its header, its rows, and the name it was read as."""

    name: str
    header: Header
    rows: tuple[Row, ...]


@dataclass(frozen=True, slots=True)
class Plan:
    """What the import would do, decided before the write transaction opens."""

    inserts: tuple[Row, ...]
    skipped: int


@dataclass(frozen=True, slots=True)
class Report:
    """What the command says about the act, for the block and for ``--json``."""

    file: str
    file_repository: str
    repository: str
    format_version: str
    count_in_file: int
    imported: int
    skipped: int
    dry_run: bool


# Reading the file: pure functions of its text, so a test can reach every rule
# without a database and the command line never parses anything itself.


def read_file(path) -> Document:
    """The file at *path*, read, decoded and validated.

    The three ways reading fails are sentences rather than tracebacks, for the
    reason the design's §9.3 gives: a person who typed a path needs to be told
    what is wrong with the path, and the file's name is the one thing every
    refusal can name.
    """
    name = str(path)
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise MemoryImportError(
            f"{name} could not be read: {error.strerror or error}"
        ) from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MemoryImportError(
            f"{name} is not UTF-8 (byte 0x{raw[error.start]:02x} at offset"
            f" {error.start})"
        ) from None
    return read_document(text, name=name)


def read_document(text: str, *, name: str) -> Document:
    """One file's text as a header and rows, or a refusal naming the line.

    **What is tolerated on read, and why it cannot change what a file means**:
    trailing whitespace at the end of a line, and a missing final newline. The
    writer always emits one, and refusing a hand-copied file over its last byte
    would be refusing a difference that means nothing. A ``\\r\\n`` file
    therefore parses, because the ``\\r`` is trailing whitespace — but no line's
    *content* may be empty, and the format's own freeze says a blank line is not
    part of it.
    """
    if text.startswith("\ufeff"):
        raise MemoryImportError(
            f"{name} begins with a byte-order mark; this format is UTF-8 without"
            f" one"
        )
    if not text.strip():
        raise MemoryImportError(
            f"{name} is empty; a file this format writes always has a header line"
        )

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    header = _read_header(lines[0], name=name)
    rows: list[Row] = []
    seen: set[str] = set()
    for index, line in enumerate(lines[1:], 2):
        row = _read_row(line, index, name=name)
        # The file's own duplicate is a corrupt file, and it is not the same
        # thing as an id the store already holds: the first is the file
        # disagreeing with itself, the second is a meeting between two sets.
        if row.memory_id in seen:
            raise MemoryImportError(
                f"{name}: line {index}: memory {row.memory_id} is given twice in"
                f" this file"
            )
        seen.add(row.memory_id)
        rows.append(row)

    if header.count != len(rows):
        raise MemoryImportError(
            f"{name} says {header.count} memories and carries {len(rows)}; the"
            f" file is truncated or was edited"
        )
    return Document(name=name, header=header, rows=tuple(rows))


def _read_header(line: str, *, name: str) -> Header:
    """The first line, or the refusal that says it is not the header.

    The header is identified **by position and by shape**: it carries the
    version key and no ``memory_id``. A line that is both or neither is a
    corrupt file, which is the format's own rule rather than this module's.
    """
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        value = None
    if (
        not isinstance(value, dict)
        or VERSION_KEY not in value
        or "memory_id" in value
    ):
        raise MemoryImportError(f"{name}: line 1 is not this format's header")

    unknown = sorted(set(value) - set(HEADER_KEYS))
    if unknown:
        raise MemoryImportError(
            f"{name}: line 1 carries an unknown field {unknown[0]!r}"
        )
    missing = [key for key in HEADER_KEYS if key not in value]
    if missing:
        raise MemoryImportError(f"{name}: line 1 is missing {missing[0]!r}")

    version = value[VERSION_KEY]
    if version != FORMAT_VERSION:
        raise MemoryImportError(
            f"{name} was written in format version {version!r}; this tool reads"
            f" {FORMAT_VERSION!r}"
        )

    count = value["count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise MemoryImportError(
            f"{name}: line 1: count must be a whole number of memories"
        )

    exported_at = value["exported_at"]
    if not isinstance(exported_at, str) or not _is_a_time(exported_at):
        raise MemoryImportError(f"{name}: line 1: exported_at is not a timestamp")

    repository = value["repository"]
    if not isinstance(repository, str) or not repository.strip():
        raise MemoryImportError(
            f"{name}: line 1: repository must be the path the memories were made"
            f" about"
        )
    _refuse_a_lone_surrogate(repository, index=1, name=name, what="repository")

    return Header(
        version=version,
        repository=repository,
        count=count,
        exported_at=exported_at,
    )


def _read_row(line: str, index: int, *, name: str) -> Row:
    """One memory line, every field checked the way the store would check it.

    Each rule mirrors either the schema's CHECK or the acts' own ``_checked``,
    so that **a file can never smuggle in a row the acts would have refused** —
    the per-row CHECKs are still the safety net underneath, but a constraint
    error names a column and a person needs a sentence naming the line.
    """
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        value = None
    if (
        not isinstance(value, dict)
        or "memory_id" not in value
        or VERSION_KEY in value
    ):
        raise MemoryImportError(
            f"{name}: line {index} is neither the header nor a memory"
        )

    unknown = sorted(set(value) - set(ROW_KEYS))
    if unknown:
        raise MemoryImportError(
            f"{name}: line {index} carries an unknown field {unknown[0]!r}"
        )
    missing = [key for key in ROW_KEYS if key not in value]
    if missing:
        raise MemoryImportError(f"{name}: line {index} is missing {missing[0]!r}")

    memory_id = _read_memory_id(value["memory_id"], index=index, name=name)

    repository = value["repository"]
    if not isinstance(repository, str) or not repository.strip():
        raise MemoryImportError(
            f"{name}: line {index}: repository must be the path the memory was"
            f" made about"
        )
    _refuse_a_lone_surrogate(repository, index=index, name=name, what="repository")

    statement = value["statement"]
    if not isinstance(statement, str):
        raise MemoryImportError(f"{name}: line {index}: statement must be text")
    if not statement.strip():
        raise MemoryImportError(f"{name}: line {index}: the statement is empty")
    _refuse_a_lone_surrogate(statement, index=index, name=name, what="the statement")
    # The cap is on the value that is written, and this path writes the file's
    # statement verbatim — so the verbatim length is what is checked. A
    # statement padded to 5001 characters is one character after stripping, and
    # a check on the trim would let a row past the store's own cap (§5.2, §10).
    if len(statement) > STATEMENT_CHARACTERS:
        raise MemoryImportError(
            f"{name}: line {index}: a statement is at most {STATEMENT_CHARACTERS}"
            f" characters, and this one is {len(statement)}"
        )

    author_name, author_email = _read_author(value["author"], index=index, name=name)

    admitted_at = _read_time(
        value["admitted_at"], index=index, name=name, what="admitted_at"
    )
    ended_at = _read_time(
        value["ended_at"], index=index, name=name, what="ended_at", optional=True
    )

    state = value["state"]
    if state not in STATES:
        raise MemoryImportError(
            f"{name}: line {index}: {state!r} is not a state; the states are"
            f" {', '.join(STATES)}"
        )

    subject_kind, subject_path, subject_qualname, subject_commit_sha = _read_subject(
        value["subject"], index=index, name=name
    )
    since_commit_sha, since_date = _read_since(value["since"], index=index, name=name)

    end_reason = value["end_reason"]
    if end_reason is not None and not isinstance(end_reason, str):
        raise MemoryImportError(
            f"{name}: line {index}: end_reason must be text or null"
        )
    if end_reason is not None:
        _refuse_a_lone_surrogate(end_reason, index=index, name=name, what="end_reason")

    superseded_by = value["superseded_by"]
    if superseded_by is not None and not isinstance(superseded_by, str):
        raise MemoryImportError(
            f"{name}: line {index}: superseded_by must be a memory id or null"
        )

    _read_lifecycle(
        state=state,
        ended_at=ended_at,
        end_reason=end_reason,
        superseded_by=superseded_by,
        memory_id=memory_id,
        index=index,
        name=name,
    )

    return Row(
        memory_id=memory_id,
        repository=value["repository"],
        statement=statement,
        author_name=author_name,
        author_email=author_email,
        admitted_at=admitted_at,
        subject_kind=subject_kind,
        subject_path=subject_path,
        subject_qualname=subject_qualname,
        subject_commit_sha=subject_commit_sha,
        since_commit_sha=since_commit_sha,
        since_date=since_date,
        state=state,
        ended_at=ended_at,
        end_reason=end_reason,
        superseded_by=superseded_by,
        citations=_read_citations(value["citations"], index=index, name=name),
    )


def _read_memory_id(value, *, index: int, name: str) -> str:
    """The id, in the one spelling the tool writes: canonical, lowercase, whole.

    ``uuid.UUID`` reads several spellings, so the round trip is the check: an id
    that does not come back out as itself is a spelling this tool never wrote,
    and a junk id would make ``memory show <prefix>`` ambiguous forever.
    """
    if not isinstance(value, str):
        raise MemoryImportError(f"{name}: line {index}: memory_id must be text")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        parsed = None
    if parsed is None or str(parsed) != value:
        raise MemoryImportError(
            f"{name}: line {index}: {value!r} is not a memory id"
        )
    return value


def _read_author(value, *, index: int, name: str):
    """``author``: null, or a name and an email with at least one of them.

    The writer's own shape (``memory_view.author_object`` returns ``None`` when
    both are ``None``), so the "at least one" rule refuses a line the tool could
    not have written rather than a line a person meant.
    """
    if value is None:
        return (None, None)
    if not isinstance(value, dict):
        raise MemoryImportError(
            f"{name}: line {index}: author must be an object with a name and an"
            f" email, or null"
        )
    unknown = sorted(set(value) - {"name", "email"})
    if unknown:
        raise MemoryImportError(
            f"{name}: line {index}: author carries an unknown field {unknown[0]!r}"
        )
    for key in ("name", "email"):
        if key not in value:
            raise MemoryImportError(f"{name}: line {index}: author is missing {key!r}")

    author_name = _read_text_or_none(
        value["name"], index=index, name=name, what="author's name"
    )
    author_email = _read_text_or_none(
        value["email"], index=index, name=name, what="author's email"
    )
    if author_name is None and author_email is None:
        raise MemoryImportError(
            f"{name}: line {index}: an author with no name and no email says"
            f" nothing; use null"
        )
    return (author_name, author_email)


def _read_text_or_none(value, *, index: int, name: str, what: str):
    """A value that is either null or text with something in it, kept verbatim."""
    if value is None:
        return None
    return _required_text(value, index=index, name=name, what=what)


def _required_text(value, *, index: int, name: str, what: str) -> str:
    """A value that has to be text with something in it — null is not allowed.

    The fields a shape cannot do without: a path subject's path, a definition's
    name, a commit, a start. Null in one of those is a shape the writer cannot
    produce, and refusing it here is what keeps the schema's CHECKs from being
    the thing that says so — a constraint error names a column, and a person
    needs a sentence naming the line.
    """
    if not isinstance(value, str) or not value.strip():
        raise MemoryImportError(
            f"{name}: line {index}: {what} must be text with something in it"
        )
    _refuse_a_lone_surrogate(value, index=index, name=name, what=what)
    return value


def _refuse_a_lone_surrogate(value: str, *, index: int, name: str, what: str) -> None:
    """Refuse text SQLite cannot bind, before the store is opened.

    ``json`` reads the escape ``"\\ud800"`` back into a lone surrogate, which is
    a Python string and not text a UTF-8 file can carry — and ``sqlite3`` raises
    ``UnicodeEncodeError`` when it binds one (measured), a traceback where this
    format promises a sentence. The writer cannot produce one, so the reader
    refuses it by name.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise MemoryImportError(
            f"{name}: line {index}: {what} carries a lone surrogate, which is"
            f" not text this format can write"
        ) from None


def _read_time(value, *, index: int, name: str, what: str, optional: bool = False):
    """A time string the writer could have produced: it parses, it round-trips,
    and it carries the machine's offset.

    The same device the store already uses for ``since_date``: the parse refuses
    a moment that does not exist, and the round trip refuses a spelling the
    writer would not have produced — ``…Z`` and ``20261011`` are both read by
    ``fromisoformat`` and neither is written by ``isoformat``. A row whose
    ``admitted_at`` did not parse would make every later ``memory list`` raise,
    and import is the last place that can refuse it cheaply.

    The offset is part of that spelling: every moment this tool writes is
    ``datetime.now().astimezone().isoformat()``. A naive one would be read as
    the *importing* machine's clock — the same file would order differently on
    two machines — and on Windows a naive moment before 1970 has no epoch at all
    (``timestamp()`` raises ``OSError``; measured).
    """
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _is_a_time(value) or not _round_trips(value):
        raise MemoryImportError(
            f"{name}: line {index}: {what} is not a timestamp this tool wrote"
        )
    if datetime.fromisoformat(value).tzinfo is None:
        raise MemoryImportError(
            f"{name}: line {index}: {what} has no time zone offset, and this tool"
            f" writes every moment with one"
        )
    return value


def _is_a_time(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _round_trips(value: str) -> bool:
    try:
        return datetime.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _read_subject(value, *, index: int, name: str):
    """The subject: one of four shapes, exactly the fields its kind carries."""
    if not isinstance(value, dict):
        raise MemoryImportError(
            f"{name}: line {index}: a subject is an object with a kind"
        )
    kind = value.get("kind")
    if kind not in SUBJECT_KINDS:
        raise MemoryImportError(
            f"{name}: line {index}: {kind!r} is not a subject kind; the kinds are"
            f" {', '.join(SUBJECT_KINDS)}"
        )
    wanted = SUBJECT_FIELDS[kind]
    unknown = sorted(set(value) - set(wanted) - {"kind"})
    if unknown:
        raise MemoryImportError(
            f"{name}: line {index}: a {kind} subject carries an unknown field"
            f" {unknown[0]!r}"
        )
    missing = [key for key in wanted if key not in value]
    if missing:
        raise MemoryImportError(
            f"{name}: line {index}: a {kind} subject needs {missing[0]!r}"
        )
    if kind == "repository":
        return ("repository", None, None, None)
    if kind == "path":
        return (
            "path",
            _required_text(
                value["path"], index=index, name=name, what="a subject's path"
            ),
            None,
            None,
        )
    if kind == "definition":
        return (
            "definition",
            _required_text(
                value["path"], index=index, name=name, what="a subject's path"
            ),
            _required_text(
                value["qualname"], index=index, name=name, what="a subject's name"
            ),
            None,
        )
    return (
        "commit",
        None,
        None,
        _required_text(
            value["commit"], index=index, name=name, what="a subject's commit"
        ),
    )


def _read_since(value, *, index: int, name: str):
    """``since``: null, or exactly one of a commit and a date."""
    if value is None:
        return (None, None)
    if not isinstance(value, dict) or len(value) != 1:
        raise MemoryImportError(
            f"{name}: line {index}: a memory's start is one thing: a commit or a"
            f" date, not both"
        )
    if "commit" in value:
        start = _required_text(
            value["commit"], index=index, name=name, what="a start commit"
        )
        # ``memory_checks.since_moment`` reads this value as a prefix for
        # ``storage.find_commits``, whose own docstring says the prefix "has to
        # hold sha characters and nothing else" — ``"%"`` would match every
        # stored commit and put a fabricated date beside the memory. The acts
        # resolve the commit before they store it, so anything else is a
        # spelling the writer does not produce.
        if any(character not in SHA_CHARACTERS for character in start):
            raise MemoryImportError(
                f"{name}: line {index}: {start!r} is not a commit sha; a memory's"
                f" start is the commit this tool resolved"
            )
        return (start, None)
    if "date" not in value:
        raise MemoryImportError(
            f"{name}: line {index}: a memory's start is a commit or a date"
        )
    day = value["date"]
    if not isinstance(day, str) or not _is_a_day(day):
        raise MemoryImportError(
            f"{name}: line {index}: {day!r} is not a date; a since date is written"
            f" YYYY-MM-DD"
        )
    return (None, day)


def _is_a_day(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _read_lifecycle(
    *, state, ended_at, end_reason, superseded_by, memory_id, index, name
) -> None:
    """The state and its consequences, mirroring the schema's CHECKs.

    Each refusal names the contradiction rather than the column, because a
    person reading it needs to know which two things they said that cannot both
    be true.
    """
    if state == "active":
        if ended_at is not None or end_reason is not None or superseded_by is not None:
            raise MemoryImportError(
                f"{name}: line {index}: an active memory has no end, and this one"
                f" carries one"
            )
    elif state == "superseded":
        if ended_at is None or superseded_by is None or end_reason is not None:
            raise MemoryImportError(
                f"{name}: line {index}: a superseded memory ends with a successor"
                f" and no reason, and this one does not"
            )
    else:
        if (
            ended_at is None
            or not (end_reason or "").strip()
            or superseded_by is not None
        ):
            raise MemoryImportError(
                f"{name}: line {index}: an invalidated memory ends with a reason"
                f" and no successor, and this one does not"
            )
    if superseded_by == memory_id:
        raise MemoryImportError(
            f"{name}: line {index}: memory {memory_id} names itself as its"
            f" successor"
        )


def _read_citations(value, *, index: int, name: str) -> tuple[tuple[str, str], ...]:
    """The citations, in the order the file carries them — the position order.

    The order is preserved rather than re-sorted: it *is* the position the store
    writes, so a file that came from an export carries the store's own order,
    and a hand-edited file that carries another one is stored as it stands.
    """
    if not isinstance(value, list):
        raise MemoryImportError(f"{name}: line {index}: citations must be a list")
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"kind", "ref"}:
            raise MemoryImportError(
                f"{name}: line {index}: a citation is an object with a kind and a"
                f" reference"
            )
        kind = entry["kind"]
        if kind not in CITATION_KINDS:
            raise MemoryImportError(
                f"{name}: line {index}: {kind!r} is not a citation kind; the kinds"
                f" are {', '.join(CITATION_KINDS)}"
            )
        ref = entry["ref"]
        if not isinstance(ref, str) or not ref.strip():
            raise MemoryImportError(
                f"{name}: line {index}: a {kind} citation with no reference says"
                f" nothing"
            )
        _refuse_a_lone_surrogate(
            ref, index=index, name=name, what="a citation's reference"
        )
        # The duplicate rule is the acts' own (``memory._checked_citations``),
        # and the acts read the stripped reference: two spellings of one
        # reference are one reference. The value itself is still stored as the
        # file wrote it.
        if (kind, ref.strip()) in seen:
            raise MemoryImportError(
                f"{name}: line {index}: the {kind} citation {ref.strip()!r} is"
                f" given twice"
            )
        seen.add((kind, ref.strip()))
        found.append((kind, ref))
    return tuple(found)


# Planning: what the file would do to this store, decided before any write.


def plan_import(
    connection: sqlite3.Connection,
    document: Document,
    *,
    repository_path,
    from_path=None,
) -> Plan:
    """The insert/skip plan for *document*, or the refusal that stops it.

    Everything here reads: the file has been parsed, the store's rows for the
    ids the file names are read, and the decision is made without a single
    write. That is what lets ``--dry-run`` be the same code with the write
    skipped, and it is what makes the refusal sentences possible — by the time
    the transaction opens, there is nothing left to decide.
    """
    repository = repository_identity(repository_path)
    header = document.header
    name = document.name

    # **The identity, before the store is read at all.** Without ``--from`` the
    # file must name this repository; with it, the path a person typed must be
    # exactly the path the file names — two sources that must agree, which is
    # stricter than ``adopt``, whose only source is the rows.
    if from_path is not None:
        asserted = repository_identity(from_path)
        if asserted != header.repository:
            raise MemoryImportError(
                f"{name} names {header.repository}, and --from names {asserted};"
                f" they must be the same path"
            )
    elif header.repository != repository:
        raise MemoryImportError(
            f"{name} names {header.repository}, and this repository is"
            f" {repository}; if it moved, name the path it was made about with"
            f" --from"
        )

    for row in document.rows:
        if row.repository != header.repository:
            raise MemoryImportError(
                f"{name}: memory {row.memory_id} names {row.repository}, and the"
                f" header names {header.repository}; the file disagrees with"
                f" itself"
            )

    # ``_readable`` is False only for a store with no memory tables at all, and
    # raises memory's own sentence for the shapes this tool did not write
    # (unstamped, a newer stamp, a stamp whose tables are gone).
    readable = memory._readable(connection)

    # The store's own law: a database holds one repository's memories, and a
    # write into a store that holds another's is refused rather than mixed in.
    # The sentence is ``memory._writable_repository``'s, repeated here because
    # reaching for that function would call ``prepare_memory``, and that writes.
    held, paths = foreign_memories(connection, repository)
    if held:
        raise MemoryStoreError(_mixed_store(held, paths))

    ids = [row.memory_id for row in document.rows]
    # The successors the file names are read too: §7.3's closure is "the file's
    # rows together with the store's", and a successor an earlier import wrote is
    # the case it names. Reading only the file's own ids would refuse a file that
    # carries an old row whose successor the store already holds.
    file_ids = set(ids)
    named = sorted({row.superseded_by for row in document.rows if row.superseded_by})
    wanted = ids + [value for value in named if value not in file_ids]
    store_rows, store_citations = (
        _store_rows(connection, wanted) if readable else ({}, {})
    )

    inserts: list[Row] = []
    skipped = 0
    for row in document.rows:
        stored = store_rows.get(row.memory_id)
        if stored is None:
            inserts.append(row)
            continue
        difference = _first_difference(
            row, stored, store_citations.get(row.memory_id, ())
        )
        if difference is None:
            skipped += 1
            continue
        # The whole file, not this row: the tool cannot know which version of a
        # memory is newer, and neither a replace nor a silent skip is a rule
        # this project can defend.
        raise MemoryImportError(
            f"{name}: memory {row.memory_id} is already here and differs in"
            f" {difference}"
        )

    # Closure: every link must resolve to a row that will exist when the
    # transaction commits — the file's rows together with the store's. This is
    # the sentence that replaces what would otherwise be a raw foreign-key
    # failure at commit time.
    known = set(store_rows) | {row.memory_id for row in document.rows}
    for row in document.rows:
        if row.superseded_by is not None and row.superseded_by not in known:
            raise MemoryImportError(
                f"{name}: memory {row.memory_id} names successor"
                f" {row.superseded_by}, which is in neither the file nor this"
                f" store"
            )
    _refuse_a_cycle(document.rows, store_rows, name=name)

    return Plan(inserts=tuple(inserts), skipped=skipped)


def _mixed_store(held: int, paths) -> str:
    """``memory._writable_repository``'s sentence, for a store that is mixed.

    A function rather than a literal at the raise, because the two sentences
    have to stay one sentence: this is the same refusal every other write gives,
    and a person who meets it must not have to work out whether the two
    commands mean the same thing.
    """
    return (
        f"this database holds {held} memories made about {', '.join(paths)},"
        f" and a database holds one repository's memories. Use this"
        f" repository's own database, or run 'archaeology memory adopt"
        f" --from {paths[0]}' if the repository moved"
    )


def _store_rows(connection: sqlite3.Connection, ids):
    """The store's rows and citation lists for the ids the file names.

    Batched the way the store's own child reads are (``memory.CHILD_BATCH``):
    one statement per five hundred ids keeps it under SQLite's ceiling on the
    parameters of a statement, which is the reason that constant exists.
    """
    rows: dict[str, sqlite3.Row] = {}
    citations: dict[str, list[tuple[str, str]]] = {}
    for start in range(0, len(ids), memory.CHILD_BATCH):
        batch = ids[start : start + memory.CHILD_BATCH]
        marks = ", ".join("?" * len(batch))
        for row in connection.execute(
            f"SELECT * FROM memories WHERE memory_id IN ({marks})", batch
        ):
            rows[row["memory_id"]] = row
        for row in connection.execute(
            "SELECT memory_id, kind, ref FROM memory_citations WHERE memory_id IN"
            f" ({marks}) ORDER BY memory_id, position",
            batch,
        ):
            citations.setdefault(row["memory_id"], []).append((row["kind"], row["ref"]))
    return rows, {key: tuple(found) for key, found in citations.items()}


def _first_difference(row: Row, stored, citations) -> str | None:
    """The first compared field that differs, or ``None`` when the row is here.

    The field set is the design's §6.1 table: every column of ``memories`` but
    the key, the act's ``repository_path`` and the two derived epochs, plus the
    citation list in ``position`` order. The comparison is exact string
    equality on what the store holds — no normalisation, because the round-trip
    rules have already refused every spelling the writer would not have made.
    """
    if row.statement != stored["statement"]:
        return "statement"
    if (row.author_name, row.author_email) != (
        stored["author_name"],
        stored["author_email"],
    ):
        return "author"
    if row.admitted_at != stored["admitted_at"]:
        return "admitted_at"
    if (
        row.subject_kind,
        row.subject_path,
        row.subject_qualname,
        row.subject_commit_sha,
    ) != (
        stored["subject_kind"],
        stored["subject_path"],
        stored["subject_qualname"],
        stored["subject_commit_sha"],
    ):
        return "subject"
    if (row.since_commit_sha, row.since_date) != (
        stored["since_commit_sha"],
        stored["since_date"],
    ):
        return "since"
    if row.state != stored["state"]:
        return "state"
    if row.ended_at != stored["ended_at"]:
        return "ended_at"
    if row.end_reason != stored["end_reason"]:
        return "end_reason"
    if row.superseded_by != stored["superseded_by"]:
        return "superseded_by"
    if row.citations != citations:
        return "citations"
    return None


def _refuse_a_cycle(rows, store_rows, *, name: str) -> None:
    """Refuse a supersede chain that closes on itself.

    The acts cannot write one — a memory is superseded once, and only an active
    row can be superseded — so a cycle is a shape this tool did not write, and
    the file's own links are where one could arrive from. The read side would
    survive it; the act refuses it rather than storing a shape nothing can
    produce.
    """
    links = {row.memory_id: row.superseded_by for row in rows if row.superseded_by}
    for start in links:
        seen: set[str] = set()
        node = start
        previous = None
        while node is not None:
            if node in seen:
                raise MemoryImportError(
                    f"{name}: the successors of memory {start} close on {node}"
                    f" through {previous}; a supersede chain does not come back to"
                    f" itself"
                )
            seen.add(node)
            previous = node
            if node in links:
                node = links[node]
            else:
                stored = store_rows.get(node)
                node = stored["superseded_by"] if stored is not None else None


# Writing: one transaction, opened explicitly, with nothing left to decide.


def write_import(
    connection: sqlite3.Connection,
    plan: Plan,
    *,
    repository,
    source: str,
    imported_at: datetime,
) -> int:
    """Write the plan's rows, or write nothing at all.

    **The no-op writes nothing**: no rows, no ``meta``, no tables, no stamp, and
    no transaction — so a second import of the same file leaves the store
    identical at both levels, and a test can hold it to the file's own bytes.

    **The transaction is opened here and not by ``with connection:``.** Python's
    ``sqlite3`` starts its implicit transaction only at the first DML statement,
    so a ``CREATE TABLE`` executed before one would be committed outside the
    transaction — measured, and the reason the DDL for a store that has no
    tables yet happens *inside* this block. A failure then takes the tables and
    the stamp with it, instead of leaving a store whose tables exist and whose
    stamp does not: ``memory._state`` reads that as ``_UNSTAMPED`` and refuses
    every later read.

    ``executescript`` is not used for the same reason: it commits the open
    transaction first, so the statements are executed one by one.

    **A failure leaves as a sentence.** ``sqlite3.Error`` out of this block — a
    locked store, a full disk, a constraint the checks above did not foresee —
    is translated here, because §10 promises the CLI's error surface and the
    CLI's ``except`` arms do not name ``sqlite3.Error``.
    """
    if not plan.inserts:
        return 0

    # Setting ``isolation_level`` to ``None`` commits whatever is open
    # (measured), so a caller's pending transaction would be committed silently
    # — refuse instead. No command-line path reaches this: the CLI plans on a
    # connection with nothing pending.
    if connection.in_transaction:
        raise MemoryImportError(
            "this import opens its own transaction, and this connection already"
            " has one open"
        )

    isolation = connection.isolation_level
    connection.isolation_level = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _create_tables_when_absent(connection)
            # The foreign key on ``superseded_by`` is immediate, and a file's
            # rows have no dependency order — a successor's id is random. The
            # closure check above is what produces the sentence; this is what
            # lets the rows be inserted in the file's own order.
            connection.execute("PRAGMA defer_foreign_keys = ON")
            for row in plan.inserts:
                connection.execute(
                    _INSERT,
                    (
                        row.memory_id,
                        repository,
                        row.statement,
                        row.author_name,
                        row.author_email,
                        row.admitted_at,
                        _epoch_of(row.admitted_at),
                        row.subject_kind,
                        row.subject_path,
                        row.subject_qualname,
                        row.subject_commit_sha,
                        row.since_commit_sha,
                        row.since_date,
                        row.state,
                        row.ended_at,
                        None if row.ended_at is None else _epoch_of(row.ended_at),
                        row.end_reason,
                        row.superseded_by,
                    ),
                )
                connection.executemany(
                    "INSERT INTO memory_citations (memory_id, position, kind, ref)"
                    " VALUES (?, ?, ?, ?)",
                    [
                        (row.memory_id, position, kind, ref)
                        for position, (kind, ref) in enumerate(row.citations)
                    ],
                )
            # Plain statements, never ``storage.set_meta``: that commits, and a
            # commit here would end the transaction in the middle of it.
            connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (IMPORTED_FROM_KEY, source),
            )
            connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (IMPORTED_AT_KEY, imported_at.isoformat()),
            )
            connection.execute("COMMIT")
        except BaseException:
            # Best-effort: a connection that cannot roll back is broken anyway,
            # and masking the original failure would hide why it broke.
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    except sqlite3.Error as error:
        # A locked store, a full disk, a constraint the checks above did not
        # foresee: SQLite's own error, said as the sentence §10 promises — the
        # CLI's arms do not catch ``sqlite3.Error``, so it is translated here.
        raise MemoryImportError(f"the store could not be written: {error}") from error
    finally:
        connection.isolation_level = isolation
    return len(plan.inserts)


def _create_tables_when_absent(connection: sqlite3.Connection) -> None:
    """Make a store that has no memory tables ready for the rows.

    ``memory._readable`` is False for exactly one state — no tables and no stamp
    — and that is the ordinary case A: a freshly analyzed store. The DDL is
    ``memory.MEMORY_SCHEMA``, executed statement by statement, and the stamp is
    written beside it, all inside the caller's transaction.
    """
    if memory._readable(connection):
        return
    for statement in MEMORY_SCHEMA:
        connection.execute(statement)
    connection.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (MEMORY_VERSION_KEY, MEMORY_SCHEMA_VERSION),
    )


def _epoch_of(text: str) -> int:
    """The stored integer for a time string: ``memory._epoch``'s own expression.

    The file carries the string and not the integer, so this recomputes it —
    the same instant in the other spelling, never a value the file supplied.
    The reader has already refused a moment without an offset; the check is
    repeated here because ``timestamp()`` on a naive one reads the local clock,
    and on Windows before 1970 it raises ``OSError`` — a traceback where this
    format promises a sentence.
    """
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise MemoryImportError(
            f"{text!r} has no time zone offset, and a moment without one cannot"
            f" be placed on a clock"
        )
    return int(moment.timestamp())


_INSERT = (
    "INSERT INTO memories (memory_id, repository_path, statement, author_name,"
    " author_email, admitted_at, admitted_epoch, subject_kind, subject_path,"
    " subject_qualname, subject_commit_sha, since_commit_sha, since_date, state,"
    " ended_at, ended_epoch, end_reason, superseded_by)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def build_json(report: Report) -> str:
    """The act's report as one JSON object, the shape the design froze.

    Eight fields and no more: no list of ids (a program that wants them asks
    ``memory list --json``) and no timestamps (they are in ``meta``, and a JSON
    field nobody reads is the field the freeze refused in the header).
    """
    return json.dumps(
        {
            "file": report.file,
            "file_repository": report.file_repository,
            "repository": report.repository,
            "format_version": report.format_version,
            "count_in_file": report.count_in_file,
            "imported": report.imported,
            "skipped": report.skipped,
            "dry_run": report.dry_run,
        },
        indent=2,
        ensure_ascii=False,
    )
