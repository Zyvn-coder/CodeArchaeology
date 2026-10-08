"""Store what people know about a project, apart from what the tool derived.

A memory is a statement a person made about this project, admitted by that
person's explicit act, kept with the subject it is about and the evidence it was
made from. It is not a fact: the tool did not derive it, cannot recompute it, and
must never present it as something it checked. The definition, the boundary
against evidence and interpretation, and the rules the acts obey are frozen in
``docs/v0.5-design.md``; the tables are frozen in
``docs/v0.5-storage-design.md``.

**This is the first thing in the database that is not a cache.** Every evidence
table can be rebuilt by reading git again, and ``storage.py`` says so in its
first paragraph; a memory row cannot be rebuilt from anything. That is why the
two tables here are deliberately **not** in ``storage.TABLES``: that tuple is
what a schema rebuild drops, so keeping memory out of it is the whole of the
mechanism that makes a memory survive one. ``drop_tables`` cannot touch what it
is not given, and a list can be read and checked where a condition inside the
drop cannot.

**A database holds one repository's memories.** The identity is the resolved
repository path — the same string ``meta.repository_root`` holds and
``open_analysis`` compares — so the memory layer inherits the evidence layer's
rule instead of inventing a second one. The first memory written fixes the
database's repository; a write from another path is refused, a read filters to
the current repository and reports what it hid, and ``adopt`` is the explicit act
that says "these are the same project, at a new path".

**Its own version stamp.** ``memory_schema_version`` moves independently of
``schema_version``, which stays where v0.3 left it: a change to memory's shape
must not rebuild the evidence, and an evidence rebuild must not touch memory.

**What this module does not do.** It does not resolve a citation against the
evidence — that belongs to the act, which is the command line's job — and it
does not render, decide a state, or talk to a model. It persists what an act
accepted, and it reads it back.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.storage import get_meta, set_meta

# The version of the memory tables' shape. It is stamped in ``meta`` by this
# module and by nothing else, so the evidence rebuild never has to know that
# memory exists.
MEMORY_SCHEMA_VERSION = "1"
MEMORY_VERSION_KEY = "memory_schema_version"

# Where ``adopt`` records that it happened, so a store that was moved does not
# look as though it was always this repository's.
ADOPTED_FROM_KEY = "memory_adopted_from"
ADOPTED_AT_KEY = "memory_adopted_at"

# The two tables this module owns, children first, the way ``storage.TABLES`` is
# ordered. They are not in that tuple, and a test holds the two apart.
MEMORY_TABLES = ("memory_citations", "memories")

# What a memory is about, and what it may cite. The schema holds the same lists
# in a CHECK, and a test keeps the constants, the schema and v0.4's evidence
# kinds from drifting apart.
SUBJECT_KINDS = ("repository", "path", "definition", "commit")
CITATION_KINDS = ("commit", "file", "definition", "range", "cochange", "absence")

ACTIVE = "active"
SUPERSEDED = "superseded"
INVALIDATED = "invalidated"
STATES = (ACTIVE, SUPERSEDED, INVALIDATED)

# A memory is a paragraph. The cap is the same one a commit message is shown
# under in the model's view, and it is enforced here so that no stored row can
# be too long for that view later — the view drops whole memories and never cuts
# a statement, because half a statement is a different claim.
STATEMENT_CHARACTERS = 4000

# How much of an id a refusal lists when a prefix matches several, the way
# ``commit.py`` lists candidate shas: longer than the display form, because a
# reader is being asked to tell them apart.
# How many ids one child read may carry. SQLite's default ceiling on the
# parameters of a statement is 999, and this leaves room for the rest of the
# statement's own parameters.
CHILD_BATCH = 500

CANDIDATE_ID_LENGTH = 12
CANDIDATES_SHOWN = 5

ID_CHARACTERS = frozenset("0123456789abcdef-")

# The statements the freeze wrote, kept as a tuple rather than a script so that
# they run in one transaction: a table without its stamp would be a shape nobody
# can read, and the stamp without its tables a claim about nothing.
MEMORY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS memories (
        memory_id          TEXT    PRIMARY KEY,
        repository_path    TEXT    NOT NULL,
        statement          TEXT    NOT NULL
                                   CHECK (length(trim(statement)) > 0),
        author_name        TEXT    CHECK (author_name IS NULL
                                          OR length(trim(author_name)) > 0),
        author_email       TEXT    CHECK (author_email IS NULL
                                          OR length(trim(author_email)) > 0),
        admitted_at        TEXT    NOT NULL,
        admitted_epoch     INTEGER NOT NULL,
        subject_kind       TEXT    NOT NULL
                                   CHECK (subject_kind IN ('repository', 'path',
                                                           'definition', 'commit')),
        subject_path       TEXT,
        subject_qualname   TEXT,
        subject_commit_sha TEXT,
        since_commit_sha   TEXT,
        since_date         TEXT    CHECK (since_date IS NULL OR since_date GLOB
                                          '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        state              TEXT    NOT NULL
                                   CHECK (state IN ('active', 'superseded',
                                                    'invalidated')),
        ended_at           TEXT,
        ended_epoch        INTEGER,
        end_reason         TEXT,
        superseded_by      TEXT    REFERENCES memories(memory_id),

        CHECK (
            (subject_kind = 'repository'
                 AND subject_path IS NULL AND subject_qualname IS NULL
                 AND subject_commit_sha IS NULL)
         OR (subject_kind = 'path'
                 AND subject_path IS NOT NULL AND subject_qualname IS NULL
                 AND subject_commit_sha IS NULL)
         OR (subject_kind = 'definition'
                 AND subject_path IS NOT NULL AND subject_qualname IS NOT NULL
                 AND subject_commit_sha IS NULL)
         OR (subject_kind = 'commit'
                 AND subject_path IS NULL AND subject_qualname IS NULL
                 AND subject_commit_sha IS NOT NULL)
        ),
        CHECK (since_commit_sha IS NULL OR since_date IS NULL),
        CHECK (state = 'active'
               OR (ended_at IS NOT NULL AND ended_epoch IS NOT NULL)),
        CHECK (state <> 'active'
               OR (ended_at IS NULL AND ended_epoch IS NULL
                   AND end_reason IS NULL AND superseded_by IS NULL)),
        CHECK (state <> 'superseded' OR superseded_by IS NOT NULL),
        CHECK (state <> 'invalidated'
               OR (end_reason IS NOT NULL AND length(trim(end_reason)) > 0)),
        CHECK (superseded_by IS NULL OR superseded_by <> memory_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_citations (
        memory_id TEXT    NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
        position  INTEGER NOT NULL,
        kind      TEXT    NOT NULL
                          CHECK (kind IN ('commit', 'file', 'definition',
                                          'range', 'cochange', 'absence')),
        ref       TEXT    NOT NULL CHECK (length(trim(ref)) > 0),
        PRIMARY KEY (memory_id, position),
        UNIQUE (memory_id, kind, ref)
    )
    """,
    "CREATE INDEX IF NOT EXISTS memories_repository"
    " ON memories (repository_path, admitted_epoch)",
    "CREATE INDEX IF NOT EXISTS memories_subject_path ON memories (subject_path)",
    "CREATE INDEX IF NOT EXISTS memories_superseded_by ON memories (superseded_by)",
    "CREATE INDEX IF NOT EXISTS memory_citations_ref ON memory_citations (kind, ref)",
)

# The states a memory can be read in, and the two that mean it has ended.
_ABSENT = "absent"
_CURRENT = "current"
_UNSTAMPED = "unstamped"
_UNREADABLE = "unreadable"
_MISSING_TABLES = "missing_tables"


class MemoryStoreError(RuntimeError):
    """Raised when the store refuses an act, or cannot be read as it stands."""


class MemoryNotFound(LookupError):
    """Raised when a memory id, or a prefix of one, names no single memory."""


@dataclass(frozen=True, slots=True)
class Subject:
    """What a memory is about: the project, a path, a definition, or a commit.

    The four kinds are the schema's, and each fills exactly the fields it needs:
    a definition is a path and a qualified name, because a name alone does not
    name a definition (v0.3's identity rule), and a path subject is the file's
    name as git writes it, including a name it no longer carries.
    """

    kind: str
    path: str | None = None
    qualname: str | None = None
    commit_sha: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    """One pointer from a memory to the evidence it was made from.

    The kind and the form are v0.4's, unchanged, so the whole tool points at
    evidence in one way. Whether a citation still resolves is not stored: it is
    worked out when a memory is read, because a commit a rewrite removed does
    not make the citation wrong, only unresolvable.
    """

    kind: str
    ref: str


@dataclass(frozen=True, slots=True)
class Memory:
    """One stored memory, whole.

    ``superseded_by`` is stored; ``supersedes`` is derived from it, because
    storing both directions would be two rows saying one thing and free to
    disagree. ``admitted_at`` and ``ended_at`` are facts about the store;
    ``since_commit_sha`` and ``since_date`` are the author's claim about the
    project, and absent means unknown rather than "from the beginning".
    """

    memory_id: str
    repository_path: str
    statement: str
    author_name: str | None
    author_email: str | None
    admitted_at: datetime
    subject: Subject
    since_commit_sha: str | None
    since_date: str | None
    state: str
    ended_at: datetime | None
    end_reason: str | None
    superseded_by: str | None
    supersedes: str | None
    citations: tuple[Citation, ...]


def prepare_memory(connection: sqlite3.Connection) -> None:
    """Make the memory tables ready to be written to.

    Creates them and stamps the version when they are not there, leaves them
    alone when they are, and refuses two things it must not guess about: tables
    with no stamp (a shape this tool did not write) and a stamp from a newer
    tool (a shape this tool does not know). Neither refusal touches the
    evidence: an unreadable memory store is not a reason to stop reading a
    repository.
    """
    if not _meta_present(connection):
        # Memories are kept beside an analysis: a subject and a citation are
        # resolved against the stored history, and a database this tool has
        # never written has none. Refusing here also keeps this module from
        # creating an evidence table of its own.
        raise MemoryStoreError(
            f"{_file(connection)} holds no analysis; memories are kept beside"
            f" one, so run 'archaeology analyze' first"
        )

    state = _state(connection)
    if state in (_UNSTAMPED, _UNREADABLE, _MISSING_TABLES):
        raise MemoryStoreError(_unreadable(connection, state))

    with connection:
        for statement in MEMORY_SCHEMA:
            connection.execute(statement)
        set_meta(connection, MEMORY_VERSION_KEY, MEMORY_SCHEMA_VERSION)


def admit(
    connection: sqlite3.Connection,
    *,
    repository_path,
    statement: str,
    subject: Subject,
    author_name: str | None = None,
    author_email: str | None = None,
    since_commit_sha: str | None = None,
    since_date: str | None = None,
    citations=(),
    admitted_at: datetime | None = None,
) -> Memory:
    """Write one new memory, with ``state`` active.

    The repository is the one this act is for, and it is the string
    ``open_analysis`` compares — resolved, and never case-folded, so the memory
    layer adds no third rule about what a repository is.

    ``admitted_at`` is the tool's to set and is not a caller's parameter in
    practice; it is here so that a test can place two memories in a known order
    rather than depending on how fast a machine admits them.
    """
    repository = _writable_repository(connection, repository_path)
    fields = _checked(
        statement=statement,
        subject=subject,
        author_name=author_name,
        author_email=author_email,
        since_commit_sha=since_commit_sha,
        since_date=since_date,
        citations=citations,
    )
    when = _moment(admitted_at)

    with connection:
        memory_id = _write(connection, repository=repository, when=when, **fields)

    return _load(connection, memory_id)


def supersede(
    connection: sqlite3.Connection,
    memory_id: str,
    *,
    repository_path,
    statement: str,
    subject: Subject,
    author_name: str | None = None,
    author_email: str | None = None,
    since_commit_sha: str | None = None,
    since_date: str | None = None,
    citations=(),
    admitted_at: datetime | None = None,
) -> Memory:
    """Admit a successor and close the memory it replaces, in one transaction.

    The successor is a memory like any other — same subject rules, same citation
    rules — and the old row keeps its place: its span closes, it names its
    successor, and it is never deleted, because the end of a rule's life is what
    a reader of an old commit wants to find. A memory that has already ended
    stays ended; the message says to record a new one instead.
    """
    repository = _writable_repository(connection, repository_path)
    # The database holds one repository's memories, so this memory is this
    # repository's: the check that it belongs here is the one above, and a
    # second one here could never fire.
    old = find_memory(connection, memory_id)
    if old.state != ACTIVE:
        raise MemoryStoreError(
            f"memory {_short(old.memory_id)} is {old.state}; a memory that has"
            f" ended stays ended — record a new one instead"
        )

    fields = _checked(
        statement=statement,
        subject=subject,
        author_name=author_name,
        author_email=author_email,
        since_commit_sha=since_commit_sha,
        since_date=since_date,
        citations=citations,
    )
    when = _moment(admitted_at)

    with connection:
        successor_id = _write(connection, repository=repository, when=when, **fields)
        connection.execute(
            "UPDATE memories SET state = ?, ended_at = ?, ended_epoch = ?,"
            " superseded_by = ? WHERE memory_id = ?",
            (SUPERSEDED, when.isoformat(), _epoch(when), successor_id, old.memory_id),
        )

    return _load(connection, successor_id)


def invalidate(
    connection: sqlite3.Connection,
    memory_id: str,
    *,
    repository_path,
    reason: str,
    ended_at: datetime | None = None,
) -> Memory:
    """End a memory without a successor, with the reason it ended.

    The reason is required and is not a formality: "it was wrong" and "it
    stopped applying" are different sentences, and a reader needs to know which
    one they are looking at. A memory that has already ended stays ended.
    """
    _writable_repository(connection, repository_path)
    # This memory is this repository's for the reason the comment in
    # ``supersede`` gives: the database holds one repository's memories.
    old = find_memory(connection, memory_id)
    if old.state != ACTIVE:
        raise MemoryStoreError(
            f"memory {_short(old.memory_id)} is {old.state}; a memory that has"
            f" ended stays ended — record a new one instead"
        )

    reason = (reason or "").strip()
    if not reason:
        raise MemoryStoreError("a memory is invalidated with a reason; this one is empty")

    when = _moment(ended_at)
    with connection:
        connection.execute(
            "UPDATE memories SET state = ?, ended_at = ?, ended_epoch = ?,"
            " end_reason = ? WHERE memory_id = ?",
            (INVALIDATED, when.isoformat(), _epoch(when), reason, old.memory_id),
        )

    return _load(connection, old.memory_id)


def adopt(connection: sqlite3.Connection, *, from_path, to_path) -> int:
    """Take over the memories made about *from_path*, as *to_path*'s.

    This is the one way out of a moved repository, and it is deliberately
    explicit: the path has to be given in full, because typing it is the
    confirmation, and the act requires the person to name what is being adopted.
    Nothing about a memory changes but its repository; the act is recorded in
    ``meta`` so the store says that it happened.

    Refused when the path matches nothing, when it is already this repository,
    and when the destination already holds memories: a database holds one
    repository's memories, and merging two sets is a question this version
    answers by refusing rather than by guessing.
    """
    # Not ``_writable_repository``: that refuses a database holding another
    # repository's memories, and this act exists precisely to repair that state.
    prepare_memory(connection)
    source = repository_identity(from_path)
    destination = repository_identity(to_path)

    if source == destination:
        raise MemoryStoreError(
            f"{source} is already this repository's path; there is nothing to adopt"
        )

    moving = _count(connection, "repository_path = ?", (source,))
    if not moving:
        raise MemoryStoreError(f"nothing in this database was made about {source}")

    if _count(connection, "repository_path = ?", (destination,)):
        raise MemoryStoreError(
            f"this database already holds memories made about {destination};"
            f" this version does not merge two repositories' memories"
        )

    with connection:
        connection.execute(
            "UPDATE memories SET repository_path = ? WHERE repository_path = ?",
            (destination, source),
        )
        set_meta(connection, ADOPTED_FROM_KEY, source)
        set_meta(connection, ADOPTED_AT_KEY, _now().isoformat())

    return moving


def find_memory(connection: sqlite3.Connection, prefix: str) -> Memory:
    """Return the one memory whose id starts with *prefix*.

    A prefix is enough, the way it is for a commit sha, and the three refusals
    are the ones ``commit.py`` already gives: not an id at all, names nothing,
    names several.
    """
    # What the person typed is checked first, so a malformed prefix is told what
    # it is rather than being answered about a store it never reached.
    wanted = (prefix or "").strip().lower()
    if not wanted or any(character not in ID_CHARACTERS for character in wanted):
        raise MemoryNotFound(
            f"{prefix!r} is not a memory id; use one from 'archaeology memory list'"
        )

    if not _readable(connection):
        raise MemoryNotFound(f"no memory starts with {prefix!r}")

    where = "memory_id LIKE ?"
    parameters = (f"{wanted}%",)
    rows = connection.execute(
        f"SELECT * FROM memories WHERE {where} ORDER BY memory_id", parameters
    ).fetchall()
    if not rows:
        raise MemoryNotFound(f"no memory starts with {prefix!r}")
    if len(rows) > 1:
        raise MemoryNotFound(
            f"{prefix!r} matches {len(rows)} memories: {_candidates(rows)};"
            f" give more characters"
        )

    return _assembled(connection, rows)[0]


def read_memories(
    connection: sqlite3.Connection,
    repository_path,
    *,
    include_ended: bool = False,
    limit: int | None = None,
) -> tuple[Memory, ...]:
    """The memories kept for one repository, newest admission first.

    Only this repository's: another repository's memories are hidden here and
    reported by :func:`foreign_memories`, because a read that quietly showed
    them would be the mixing this whole layer exists to prevent.

    ``limit`` is for the reads that print a slice — a list shows twenty of a
    store that may hold a hundred thousand — and it stops the *read*, not only
    the printing: building every row's record to show twenty of them is work
    nobody asked for. :func:`count_memories` is the other half, and the two
    share one selection so a count and a read cannot disagree about what is
    being counted.
    """
    if not _readable(connection):
        return ()

    where, parameters = _selection(repository_path, include_ended)
    tail = " ORDER BY admitted_epoch DESC, admitted_at DESC, memory_id"
    asked = parameters
    if limit is not None:
        tail += " LIMIT ?"
        asked = parameters + (limit,)

    rows = connection.execute(
        f"SELECT * FROM memories WHERE {where}{tail}", asked
    ).fetchall()
    if not rows:
        return ()

    # The children are read with the selection's own parameters and not the
    # limit's: they are what the rows that were read hold, not another slice.
    return _assembled(connection, rows)


def count_memories(
    connection: sqlite3.Connection,
    repository_path,
    *,
    include_ended: bool = False,
) -> int:
    """How many memories one repository has, counted the way they are read."""
    if not _readable(connection):
        return 0

    where, parameters = _selection(repository_path, include_ended)
    return connection.execute(
        f"SELECT COUNT(*) AS held FROM memories WHERE {where}", parameters
    ).fetchone()["held"]


def _selection(repository_path, include_ended: bool) -> tuple[str, tuple]:
    """What one repository's memories are, in one place for the two readers.

    A read and a count have to agree about the rows they are talking about: a
    list that showed twenty of a hundred and said so must have counted the same
    hundred it read from.
    """
    where = "repository_path = ?"
    parameters: tuple = (repository_identity(repository_path),)
    if not include_ended:
        where += " AND state = ?"
        parameters += (ACTIVE,)
    return where, parameters


def foreign_memories(
    connection: sqlite3.Connection, repository_path
) -> tuple[int, tuple[str, ...]]:
    """How many memories this database holds for other repositories, and where.

    The read side reports these rather than showing or dropping them in silence:
    "none found" and "none shown" are different answers, which is the same rule
    the co-change counts already follow.
    """
    if not _readable(connection):
        return (0, ())

    rows = connection.execute(
        "SELECT repository_path, COUNT(*) AS held FROM memories"
        " WHERE repository_path <> ? GROUP BY repository_path"
        " ORDER BY repository_path",
        (repository_identity(repository_path),),
    ).fetchall()
    return (sum(row["held"] for row in rows), tuple(row["repository_path"] for row in rows))


# What an act accepts, checked before anything is written.


def _checked(
    *,
    statement,
    subject,
    author_name,
    author_email,
    since_commit_sha,
    since_date,
    citations,
) -> dict:
    """Everything an act hands to the writer, refused here if it is wrong.

    The schema holds the same rules as CHECKs, and this is not a duplicate of
    them: a CHECK raises a constraint error naming a column, and a person
    reading a refusal needs a sentence naming what they typed. Both exist on
    purpose — the sentence for the person, the constraint for the row.
    """
    text = (statement or "").strip()
    if not text:
        raise MemoryStoreError("a memory is a statement, and this one is empty")
    if len(text) > STATEMENT_CHARACTERS:
        raise MemoryStoreError(
            f"a memory is at most {STATEMENT_CHARACTERS} characters, and this one"
            f" is {len(text)}"
        )

    since_commit = _text_or_none(since_commit_sha)
    since_day = _checked_date(since_date)
    if since_commit and since_day:
        # The schema refuses this with a CHECK, and the sentence is here for the
        # same reason every other one is: a constraint error names a column, and
        # a person who gave both needs to be told which two things they gave.
        raise MemoryStoreError(
            "a memory's start is one thing: a commit or a date, not both"
        )

    return {
        "statement": text,
        "subject": _checked_subject(subject),
        "author_name": _text_or_none(author_name),
        "author_email": _text_or_none(author_email),
        "since_commit_sha": since_commit,
        "since_date": since_day,
        "citations": _checked_citations(citations),
    }


def _checked_subject(subject: Subject) -> Subject:
    if not isinstance(subject, Subject):
        raise MemoryStoreError("a memory is about one thing, given as a subject")
    if subject.kind not in SUBJECT_KINDS:
        raise MemoryStoreError(
            f"{subject.kind!r} is not a subject kind; the kinds are"
            f" {', '.join(SUBJECT_KINDS)}"
        )

    path = _text_or_none(subject.path)
    qualname = _text_or_none(subject.qualname)
    commit_sha = _text_or_none(subject.commit_sha)
    if subject.kind == "repository":
        if path or qualname or commit_sha:
            raise MemoryStoreError("a repository subject carries nothing else")
    elif subject.kind == "path":
        if not path or qualname or commit_sha:
            raise MemoryStoreError("a path subject is one path and nothing else")
    elif subject.kind == "definition":
        if not path or not qualname or commit_sha:
            raise MemoryStoreError(
                "a definition subject is a path and a qualified name — a name"
                " alone does not name a definition"
            )
    else:
        if not commit_sha or path or qualname:
            raise MemoryStoreError("a commit subject is one commit sha and nothing else")

    return Subject(kind=subject.kind, path=path, qualname=qualname, commit_sha=commit_sha)


def _checked_citations(citations) -> tuple[Citation, ...]:
    found: list[Citation] = []
    seen: set[tuple[str, str]] = set()
    for citation in citations:
        if not isinstance(citation, Citation):
            raise MemoryStoreError("a citation is a kind and a reference")
        if citation.kind not in CITATION_KINDS:
            raise MemoryStoreError(
                f"{citation.kind!r} is not a citation kind; the kinds are"
                f" {', '.join(CITATION_KINDS)}"
            )
        ref = (citation.ref or "").strip()
        if not ref:
            raise MemoryStoreError(f"a {citation.kind} citation with no reference says nothing")
        if (citation.kind, ref) in seen:
            raise MemoryStoreError(f"the {citation.kind} citation {ref!r} is given twice")
        seen.add((citation.kind, ref))
        found.append(Citation(kind=citation.kind, ref=ref))

    # The stored order is canonical rather than the order the flags appeared in:
    # different options do not keep their relative order on a command line, so
    # the order is fixed here and every run writes the same rows.
    found.sort(key=lambda citation: (CITATION_KINDS.index(citation.kind), citation.ref))
    return tuple(found)


def _checked_date(value) -> str | None:
    """A ``since`` date: a real day, written exactly as the schema stores it.

    Two checks and not one, because they catch different things: the parse
    refuses a day that does not exist, and the round trip refuses a spelling
    the column's own ``GLOB`` would not keep — ``20240306`` is a date Python
    reads and a shape the schema would reject, which would otherwise surface as
    a constraint error rather than a sentence.
    """
    text = _text_or_none(value)
    if text is None:
        return None
    try:
        moment = date.fromisoformat(text)
    except ValueError:
        moment = None
    if moment is None or moment.isoformat() != text:
        raise MemoryStoreError(
            f"{text!r} is not a date; a since date is written YYYY-MM-DD"
        )
    return text


def _text_or_none(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Reading, and the state of the store.


def _state(connection: sqlite3.Connection) -> str:
    """Which shape the memory tables are in, before anything reads a row.

    The stamp and the tables have to agree, and each way of disagreeing is its
    own state: a stamp with no tables is a store claiming knowledge it no longer
    has, and tables with no stamp are a shape this tool did not write. The one
    that matters is the first — reading it as "no memories" would tell somebody
    who wrote a hundred that they never wrote any.
    """
    stamp = _stamp(connection)
    if stamp == MEMORY_SCHEMA_VERSION:
        return _CURRENT if _tables_present(connection) else _MISSING_TABLES
    if stamp is not None:
        return _UNREADABLE
    if _tables_present(connection):
        return _UNSTAMPED
    return _ABSENT


def _meta_present(connection: sqlite3.Connection) -> bool:
    """Whether the evidence schema is there at all."""
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        is not None
    )


def _stamp(connection: sqlite3.Connection) -> str | None:
    """The memory version stamp, or ``None`` when there is none to read.

    A database that has never been analyzed has no ``meta`` table at all, and
    that is not an error for a read: it is a store with nothing in it yet. A
    write refuses it in :func:`prepare_memory`, because there is no analysis to
    keep memories beside.
    """
    return get_meta(connection, MEMORY_VERSION_KEY) if _meta_present(connection) else None


def _tables_present(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
        MEMORY_TABLES,
    ).fetchall()
    return len(rows) == len(MEMORY_TABLES)


def _readable(connection: sqlite3.Connection) -> bool:
    """Whether the memories can be read, refused when the shape is unknown.

    A store with no tables has no memories rather than an error — that is what a
    repository nobody has written a memory about looks like. A store whose shape
    this tool does not know is a refusal, because guessing at rows written by
    another version is how a reader ends up believing something that was never
    said.
    """
    state = _state(connection)
    if state == _ABSENT:
        return False
    if state in (_UNSTAMPED, _UNREADABLE, _MISSING_TABLES):
        raise MemoryStoreError(_unreadable(connection, state))
    return True


def _unreadable(connection: sqlite3.Connection, state: str) -> str:
    where = _file(connection)
    if state == _UNSTAMPED:
        return (
            f"{where} has memory tables but no memory schema version; this tool"
            f" did not write them, so it cannot know their shape. Copy the file"
            f" before changing it"
        )
    if state == _MISSING_TABLES:
        return (
            f"{where} says it holds memories of schema version"
            f" {MEMORY_SCHEMA_VERSION} and the tables are not there; this tool"
            f" did not leave it that way, and reading it as an empty store would"
            f" say somebody never wrote what they did. Copy the file before"
            f" changing it"
        )
    stamp = _stamp(connection)
    return (
        f"{where} was written with memory schema version {stamp}, and this"
        f" version of archaeology reads {MEMORY_SCHEMA_VERSION}; its memories"
        f" were written by a newer tool. The evidence in it can still be read"
    )


def _file(connection: sqlite3.Connection) -> str:
    """The database file behind a connection, for a message that can be acted on.

    Read by position rather than by name: this runs on a refusal path, and a
    refusal must not depend on the connection having been opened with the row
    factory this package's ``connect`` sets.
    """
    for row in connection.execute("PRAGMA database_list"):
        if row[1] == "main":
            return row[2] or "this database"
    return "this database"


def _writable_repository(connection: sqlite3.Connection, repository_path) -> str:
    """The repository a write is for, refused when the database holds another's.

    One database holds one repository's memories. The first write fixes it, and
    a later write from a different path is refused rather than mixed in: a
    report after storing would be too late, because the mixed state would exist
    and every later read would have to filter it.
    """
    prepare_memory(connection)
    repository = repository_identity(repository_path)
    held, paths = foreign_memories(connection, repository)
    if held:
        raise MemoryStoreError(
            f"this database holds {held} memories made about {', '.join(paths)},"
            f" and a database holds one repository's memories. Use this"
            f" repository's own database, or run 'archaeology memory adopt"
            f" --from {paths[0]}' if the repository moved"
        )
    return repository


def _load(connection: sqlite3.Connection, memory_id: str) -> Memory:
    row = connection.execute(
        "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    return _assembled(connection, [row])[0]


def _assembled(connection: sqlite3.Connection, rows) -> tuple[Memory, ...]:
    """Rows as records, with their citations and their derived reverse links.

    **The children are read by the ids of the rows that were read**, in batches,
    and this is a measurement rather than a preference. The first version
    repeated the selection as a subquery — ``storage._assemble``'s shape, whose
    reason is SQLite's ceiling on the parameters of one statement — and at a
    hundred thousand memories a *sliced* read (a list showing twenty of them)
    paid 495ms to find the citations of those twenty, because the subquery
    scanned the whole store for each of the two child reads. Batches keep the
    statement below the ceiling and the work proportional to the rows.
    """
    ids = [row["memory_id"] for row in rows]
    citations = _citations(connection, ids)
    supersedes = _supersedes(connection, ids)
    return tuple(
        _memory(
            row,
            citations.get(row["memory_id"], ()),
            supersedes.get(row["memory_id"]),
        )
        for row in rows
    )


def _citations(
    connection: sqlite3.Connection, ids
) -> dict[str, tuple[Citation, ...]]:
    grouped: dict[str, list[Citation]] = {}
    for batch in _batches(ids):
        for row in connection.execute(
            "SELECT * FROM memory_citations WHERE memory_id IN"
            f" ({_marks(batch)}) ORDER BY memory_id, position",
            batch,
        ):
            grouped.setdefault(row["memory_id"], []).append(
                Citation(kind=row["kind"], ref=row["ref"])
            )
    return {memory_id: tuple(found) for memory_id, found in grouped.items()}


def _supersedes(connection: sqlite3.Connection, ids) -> dict[str, str]:
    """The reverse of ``superseded_by``: which memory a successor replaced.

    The rows that carry the link are the *old* ones, so this is read from the
    other side: a row that was read is a successor when some other row names it,
    and that row's id is what it replaced.
    """
    found: dict[str, str] = {}
    for batch in _batches(ids):
        for row in connection.execute(
            "SELECT memory_id, superseded_by FROM memories WHERE superseded_by IN"
            f" ({_marks(batch)})",
            batch,
        ):
            found[row["superseded_by"]] = row["memory_id"]
    return found


def _batches(ids):
    """*ids* in pieces small enough for one statement's parameters."""
    for start in range(0, len(ids), CHILD_BATCH):
        yield ids[start : start + CHILD_BATCH]


def _marks(batch) -> str:
    return ", ".join("?" * len(batch))


def _memory(row, citations, supersedes) -> Memory:
    return Memory(
        memory_id=row["memory_id"],
        repository_path=row["repository_path"],
        statement=row["statement"],
        author_name=row["author_name"],
        author_email=row["author_email"],
        admitted_at=datetime.fromisoformat(row["admitted_at"]),
        subject=Subject(
            kind=row["subject_kind"],
            path=row["subject_path"],
            qualname=row["subject_qualname"],
            commit_sha=row["subject_commit_sha"],
        ),
        since_commit_sha=row["since_commit_sha"],
        since_date=row["since_date"],
        state=row["state"],
        ended_at=None if row["ended_at"] is None else datetime.fromisoformat(row["ended_at"]),
        end_reason=row["end_reason"],
        superseded_by=row["superseded_by"],
        supersedes=supersedes,
        citations=citations,
    )


def _write(
    connection: sqlite3.Connection,
    *,
    repository: str,
    when: datetime,
    statement: str,
    subject: Subject,
    author_name: str | None,
    author_email: str | None,
    since_commit_sha: str | None,
    since_date: str | None,
    citations: tuple[Citation, ...],
) -> str:
    """Insert one memory and its citations. The caller owns the transaction."""
    memory_id = str(uuid4())
    connection.execute(
        "INSERT INTO memories (memory_id, repository_path, statement, author_name,"
        " author_email, admitted_at, admitted_epoch, subject_kind, subject_path,"
        " subject_qualname, subject_commit_sha, since_commit_sha, since_date, state,"
        " ended_at, ended_epoch, end_reason, superseded_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
        (
            memory_id,
            repository,
            statement,
            author_name,
            author_email,
            when.isoformat(),
            _epoch(when),
            subject.kind,
            subject.path,
            subject.qualname,
            subject.commit_sha,
            since_commit_sha,
            since_date,
            ACTIVE,
        ),
    )
    connection.executemany(
        "INSERT INTO memory_citations (memory_id, position, kind, ref)"
        " VALUES (?, ?, ?, ?)",
        [
            (memory_id, position, citation.kind, citation.ref)
            for position, citation in enumerate(citations)
        ],
    )
    return memory_id


def _count(connection: sqlite3.Connection, where: str, parameters: tuple) -> int:
    return connection.execute(
        f"SELECT COUNT(*) AS held FROM memories WHERE {where}", parameters
    ).fetchone()["held"]


def repository_identity(path) -> str:
    """The repository identity, as the evidence side writes and compares it.

    Public because two things outside this module need the *same* string: the
    export file names the repository it is about, and that name has to be the
    one on the rows, byte for byte, or a file could disagree with itself. One
    expression, one home — the alternative is a second copy of ``resolve()``
    somewhere else, which is how the two drift.
    """
    return str(Path(path).resolve())


def _now() -> datetime:
    """The current moment, with the machine's offset.

    The microseconds are kept, and they are not decoration: a script that
    records several memories in a row would otherwise have them all admitted in
    one second and ordered by their random ids, which is an order nobody chose.
    The blocks a person reads print to the second; the stored value is the
    finer one, because it is the one the order comes from.
    """
    return datetime.now().astimezone()


def _moment(value: datetime | None) -> datetime:
    return _now() if value is None else value


def _epoch(when: datetime) -> int:
    return int(when.timestamp())


def _short(memory_id: str) -> str:
    return memory_id[:SHORT_SHA_LENGTH]


def _candidates(rows) -> str:
    shown = [row["memory_id"][:CANDIDATE_ID_LENGTH] for row in rows[:CANDIDATES_SHOWN]]
    if len(rows) > CANDIDATES_SHOWN:
        shown.append(f"and {len(rows) - CANDIDATES_SHOWN} more")
    return ", ".join(shown)
