"""Store commit history in SQLite.

The database is a cache over a repository, not the system of record: every row
here can be rebuilt by reading git again. That is why the schema only holds
facts git reported, and nothing that only a later version of the tool could
compute.

Schema version 4 is six tables:

* ``meta``                key/value pairs, including the schema version
* ``commits``             one row per commit
* ``commit_parents``      one row per parent, in order
* ``commit_files``        one row per file touched by a commit
* ``file_versions``       one row per version of a Python file the AST pass read
* ``definition_versions`` one row per definition that version contained

Parents get their own table rather than a space-separated column so that
"find every merge commit" stays an ordinary grouped query instead of a string
match.

The last two tables are v0.3's. They hold what the parse found — the definitions
one version of one file contained — and **not** the identities a later unit
derives from them. There is no ``function_id``, no ``lifetime_id``, no
``same_as_previous``, no ``renamed_from`` and no ``moved_from`` here, because a
table of facts must not be where a derivation gets frozen: those answers depend
on rules the tool may still change, and a row that cannot be recomputed is a row
that cannot be corrected. ``tests/test_ast_storage.py`` holds the column lists to
that promise.

Two rules keep the pair honest, and both are tested:

* **A definition that is gone gets no row.** ``definition_versions`` is a
  snapshot of what one version contained, so its absence is the evidence. There
  is no ``deleted`` row, and ``change_type`` is not allowed to hold one.
* **A version that could not be parsed is not an empty version.** It has a
  ``file_versions`` row carrying a ``parse_error`` and no definitions at all, so
  "nothing was there" and "nothing could be read" stay two different things.
  Reading the second as the first would turn a syntax error into a deleted
  function.

A third rule is about vocabulary, and it applies to both change-type columns.
``commit_files.change_type`` holds git's own letter from the ``--raw`` status
field, and ``definition_versions.change_type`` holds one of the three comparison
words; a ``CHECK`` on each says so. The view translates a letter into a word
(``commit.py`` names ``D`` as "deleted"), and that word cannot be written back:
the storage may hold what git and the parser observed, never the tool's reading
of it. Version 4 added the first of those two constraints — it changes no row,
only what may be written into one.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codearchaeology.history import Commit, FileChange

SCHEMA_VERSION = "4"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commits (
    sha             TEXT PRIMARY KEY,
    author_name     TEXT NOT NULL,
    author_email    TEXT NOT NULL,
    authored_at     TEXT NOT NULL,
    committed_at    TEXT NOT NULL,
    committed_epoch INTEGER NOT NULL,
    message         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commit_parents (
    commit_sha TEXT    NOT NULL REFERENCES commits(sha),
    position   INTEGER NOT NULL,
    parent_sha TEXT    NOT NULL,
    PRIMARY KEY (commit_sha, position)
);

CREATE TABLE IF NOT EXISTS commit_files (
    commit_sha    TEXT    NOT NULL REFERENCES commits(sha),
    path          TEXT    NOT NULL,
    old_path      TEXT,
    change_type   TEXT    NOT NULL
                          CHECK (change_type IN ('A', 'C', 'D', 'M', 'R', 'T',
                                                 'U', 'X', 'B')),
    added_lines   INTEGER,
    deleted_lines INTEGER,
    similarity    INTEGER,
    PRIMARY KEY (commit_sha, path)
);

CREATE INDEX IF NOT EXISTS commit_parents_parent ON commit_parents (parent_sha);
CREATE INDEX IF NOT EXISTS commit_files_path ON commit_files (path);

CREATE TABLE IF NOT EXISTS file_versions (
    commit_sha        TEXT NOT NULL REFERENCES commits(sha),
    path              TEXT NOT NULL,
    content_sha       TEXT NOT NULL,
    parsed_at_version TEXT NOT NULL,
    parse_error       TEXT,
    error_lineno      INTEGER,
    error_offset      INTEGER,
    PRIMARY KEY (commit_sha, path)
);

CREATE TABLE IF NOT EXISTS definition_versions (
    commit_sha  TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    position    INTEGER NOT NULL,
    kind        TEXT    NOT NULL
                        CHECK (kind IN ('function', 'async function', 'class')),
    qualname    TEXT    NOT NULL,
    change_type TEXT    NOT NULL
                        CHECK (change_type IN ('created', 'modified', 'unchanged')),
    fingerprint TEXT    NOT NULL,
    lineno      INTEGER NOT NULL,
    end_lineno  INTEGER NOT NULL,
    decorators  TEXT    NOT NULL,
    PRIMARY KEY (commit_sha, path, position),
    FOREIGN KEY (commit_sha, path) REFERENCES file_versions(commit_sha, path)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS file_versions_path ON file_versions (path);
CREATE INDEX IF NOT EXISTS definition_versions_qualname
    ON definition_versions (path, qualname);
"""

TABLES = (
    "definition_versions",
    "file_versions",
    "commit_files",
    "commit_parents",
    "commits",
    "meta",
)

# What a definition row says happened to it in this version of this file. A
# definition that is gone has no row at all, so there is no ``deleted`` here: the
# absence of a row is what a later unit reads as a deletion, and it can only read
# it that way for a version that parsed. The schema holds the same three words in
# a CHECK constraint; a test keeps the two lists from drifting apart.
CREATED = "created"
MODIFIED = "modified"
UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class FileVersion:
    """One version of one Python file, and what reading it produced.

    A version that could not be read keeps the reason instead of definitions, and
    that is the whole of it: there is no third state, so a query can never
    mistake "could not be parsed" for "contained nothing".
    """

    commit_sha: str
    path: str
    content_sha: str
    parsed_at_version: str
    parse_error: str | None = None
    error_lineno: int | None = None
    error_offset: int | None = None


@dataclass(frozen=True, slots=True)
class DefinitionVersion:
    """One definition, as one version of one file contained it.

    A snapshot, not a change: every definition that version held has a row, and
    one that had already gone has none. ``lineno`` and ``end_lineno`` are always
    known, because a row only exists for a definition that is there.

    ``position`` is the order it appeared in the file, and it is part of the key
    rather than a convenience: two definitions in one file can share a qualified
    name — a name defined again under an ``if``, a fallback in an ``except`` —
    and a key on the name alone would keep one of them and drop the other.
    """

    commit_sha: str
    path: str
    position: int
    kind: str
    qualname: str
    change_type: str
    fingerprint: str
    decorators: tuple[str, ...]
    lineno: int
    end_lineno: int


def connect(database) -> sqlite3.Connection:
    """Open *database*, creating the file if it does not exist yet."""
    connection = sqlite3.connect(Path(database))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    """Create the tables if they are missing and stamp the schema version."""
    connection.executescript(SCHEMA)
    set_meta(connection, "schema_version", SCHEMA_VERSION)


def read_schema_version(connection: sqlite3.Connection) -> str | None:
    """Return the version stamped in the database, or ``None`` if there is none.

    A file that has just been created has no tables at all, which is a different
    thing from a database written by an older version of the tool.
    """
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    return get_meta(connection, "schema_version") if exists else None


def drop_tables(connection: sqlite3.Connection) -> None:
    """Remove every table, leaving an empty database file."""
    with connection:
        for table in TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {table}")


def prepare_database(connection: sqlite3.Connection) -> None:
    """Make the database ready to be written to, rebuilding it if it is old.

    A database written by another schema version is thrown away rather than
    migrated. It is a cache over a repository, so every row in it can be read
    out of git again, and there is nothing worth writing migration code to keep.
    """
    # Read before creating. create_schema stamps the version unconditionally, so
    # checking afterwards would find a fresh version on an old set of tables and
    # conclude that all is well.
    stored = read_schema_version(connection)
    if stored is not None and stored != SCHEMA_VERSION:
        drop_tables(connection)
    create_schema(connection)


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
    )
    connection.commit()


def get_meta(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def clear_history(connection: sqlite3.Connection) -> None:
    """Remove every stored commit and everything built on it.

    This is the whole-database wipe, and it is **not** what a rescan does. A
    rescan writes the history git has now and removes only the commits git no
    longer has, which leaves the AST rows of the commits that stayed exactly
    where they were — see :func:`write_commits`. This function is kept for the
    case where everything is meant to go; the schema rebuild uses
    :func:`drop_tables` instead, because it has to remove the tables themselves.
    """
    with connection:
        connection.execute("DELETE FROM definition_versions")
        connection.execute("DELETE FROM file_versions")
        connection.execute("DELETE FROM commit_files")
        connection.execute("DELETE FROM commit_parents")
        connection.execute("DELETE FROM commits")


def write_commits(connection: sqlite3.Connection, commits) -> None:
    """Make the stored history exactly *commits*.

    The commits given are written, and any commit the database holds that is not
    among them is removed with everything built on it. That is the only way a
    file version or a definition ever disappears from here.

    The AST rows of the commits that stay are left alone. They are keyed on the
    sha, so a rescan of an unchanged history leaves them exactly as true as they
    were, and parsing those files again would cost minutes to learn the same
    thing.
    """
    rows = list(commits)
    shas = [(commit.sha,) for commit in rows]

    with connection:
        # Children first. The foreign keys are enforced, so a commit cannot be
        # removed while rows in commit_parents or commit_files still point at
        # it. Doing this also makes writing the same commit twice a no-op
        # instead of a duplicate-key error.
        connection.executemany("DELETE FROM commit_files WHERE commit_sha = ?", shas)
        connection.executemany("DELETE FROM commit_parents WHERE commit_sha = ?", shas)

        # An update rather than a delete followed by an insert: a file version
        # names its commit, and the foreign key would refuse to let the commit's
        # row go while those rows are still there. What is written is the same
        # either way — a sha fixes the commit it names.
        connection.executemany(
            "INSERT INTO commits (sha, author_name, author_email, authored_at,"
            " committed_at, committed_epoch, message) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(sha) DO UPDATE SET"
            " author_name = excluded.author_name,"
            " author_email = excluded.author_email,"
            " authored_at = excluded.authored_at,"
            " committed_at = excluded.committed_at,"
            " committed_epoch = excluded.committed_epoch,"
            " message = excluded.message",
            [
                (
                    commit.sha,
                    commit.author_name,
                    commit.author_email,
                    commit.authored_at.isoformat(),
                    commit.committed_at.isoformat(),
                    int(commit.committed_at.timestamp()),
                    commit.message,
                )
                for commit in rows
            ],
        )
        connection.executemany(
            "INSERT INTO commit_parents (commit_sha, position, parent_sha)"
            " VALUES (?, ?, ?)",
            [
                (commit.sha, position, parent)
                for commit in rows
                for position, parent in enumerate(commit.parents)
            ],
        )
        connection.executemany(
            "INSERT INTO commit_files (commit_sha, path, old_path, change_type,"
            " added_lines, deleted_lines, similarity) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    commit.sha,
                    change.path,
                    change.old_path,
                    change.change_type,
                    change.added_lines,
                    change.deleted_lines,
                    change.similarity,
                )
                for commit in rows
                for change in commit.changes
            ],
        )

        # Last, so that it sees the finished table: whatever is still stored and
        # was not just written is a commit git no longer has.
        _remove_absent_commits(connection, {commit.sha for commit in rows})


def _remove_absent_commits(connection: sqlite3.Connection, present: set[str]) -> None:
    """Delete the stored commits that are not in *present*, and what hangs off them.

    The difference is worked out in Python rather than as a ``NOT IN`` over a list
    of placeholders: a repository of two hundred thousand commits would run into
    SQLite's ceiling on how many parameters one statement may take.

    The file versions go first, and the definitions of those versions follow them
    through the cascade on the foreign key. Deleting the versions first is what
    keeps a definition from outliving the version it describes.
    """
    gone = [
        (row["sha"],)
        for row in connection.execute("SELECT sha FROM commits")
        if row["sha"] not in present
    ]
    if not gone:
        return

    connection.executemany("DELETE FROM file_versions WHERE commit_sha = ?", gone)
    connection.executemany("DELETE FROM commit_files WHERE commit_sha = ?", gone)
    connection.executemany("DELETE FROM commit_parents WHERE commit_sha = ?", gone)
    connection.executemany("DELETE FROM commits WHERE sha = ?", gone)


def write_file_versions(connection: sqlite3.Connection, versions) -> None:
    """Write *versions*, replacing whatever is already stored for them.

    Replacing rather than refusing, so that a pass re-run over the same commits
    ends in the same state instead of failing on its own earlier work.

    Write the versions before the definitions that belong to them: a definition
    row names a file version, and the database enforces that it exists. A caller
    that has both to write wants :func:`write_ast_batch`, which does the pair in
    one transaction.
    """
    with connection:
        _replace_file_versions(connection, versions)


def write_definition_versions(connection: sqlite3.Connection, versions) -> None:
    """Write *versions*, replacing whatever is already stored for their versions.

    The decorators go in as JSON. A separator character would be ambiguous the
    moment a decorator's rendering contains it, and JSON escapes instead.

    A definition that is gone from this version is simply not among the rows; it
    gets no row of its own, and no ``deleted`` marker either. A caller that has
    to tell "it is not there" from "the version could not be read" has to ask
    :class:`FileVersion`, which is where that answer lives.
    """
    with connection:
        _replace_definition_versions(connection, versions)


def write_ast_batch(connection: sqlite3.Connection, rows) -> None:
    """Write file versions and their definitions together, in one transaction.

    *rows* is a sequence of ``(FileVersion, definitions)`` pairs. One transaction
    for the pair, because a version whose own row has been replaced while its
    definitions are still the previous run's says two things at once — and that
    is the state the AST pass must never leave behind, however it is interrupted.

    The indexes on the two tables are left where they are. They were measured:
    the pass writes a file's versions one after another, which is the order an
    index on ``(path, ...)`` keeps its keys in, so maintaining them through the
    write costs the same as building them again afterwards — level at both 10,000
    and 50,000 commits, in an A/B with the sign of the difference changing between
    the two.
    """
    pairs = list(rows)
    with connection:
        _replace_file_versions(connection, [version for version, _ in pairs])
        _replace_definition_versions(
            connection,
            [definition for _, definitions in pairs for definition in definitions],
        )


def _replace_file_versions(connection: sqlite3.Connection, versions) -> None:
    """Write the file version rows. The caller owns the transaction."""
    rows = list(versions)
    keys = [(version.commit_sha, version.path) for version in rows]

    # The definitions of a version are taken with it, through the cascade on the
    # foreign key. Without that, a second run of the pass would leave the first
    # run's definitions behind, and every caller would have to remember to delete
    # them in the right order.
    connection.executemany(
        "DELETE FROM file_versions WHERE commit_sha = ? AND path = ?", keys
    )
    connection.executemany(
        "INSERT INTO file_versions (commit_sha, path, content_sha,"
        " parsed_at_version, parse_error, error_lineno, error_offset)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                version.commit_sha,
                version.path,
                version.content_sha,
                version.parsed_at_version,
                version.parse_error,
                version.error_lineno,
                version.error_offset,
            )
            for version in rows
        ],
    )


def _replace_definition_versions(connection: sqlite3.Connection, versions) -> None:
    """Write the definition rows. The caller owns the transaction."""
    rows = list(versions)
    keys = sorted({(version.commit_sha, version.path) for version in rows})

    connection.executemany(
        "DELETE FROM definition_versions WHERE commit_sha = ? AND path = ?", keys
    )
    connection.executemany(
        "INSERT INTO definition_versions (commit_sha, path, position, kind,"
        " qualname, change_type, fingerprint, lineno, end_lineno, decorators)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                version.commit_sha,
                version.path,
                version.position,
                version.kind,
                version.qualname,
                version.change_type,
                version.fingerprint,
                version.lineno,
                version.end_lineno,
                json.dumps(list(version.decorators), ensure_ascii=False),
            )
            for version in rows
        ],
    )


def read_file_versions(connection: sqlite3.Connection) -> dict[tuple[str, str], FileVersion]:
    """Every stored file version, keyed by the commit and path it describes.

    The AST pass reads this once at the start to see what it can reuse: a version
    whose content and interpreter are the ones already stored does not have to be
    parsed again.
    """
    return {
        (row["commit_sha"], row["path"]): _file_version(row)
        for row in connection.execute("SELECT * FROM file_versions")
    }


def read_versions_for_paths(
    connection: sqlite3.Connection, paths
) -> dict[tuple[str, str], FileVersion]:
    """The stored versions of the given paths, keyed by the commit and the path.

    A file that was renamed carried several names, so a caller asking about one
    file has to name every name that file had. Those names are a handful at most,
    which is what keeps this inside SQLite's ceiling on how many parameters one
    statement may take — a list of commits would not be, which is why nothing here
    is ever queried by one.
    """
    names = list(paths)
    if not names:
        return {}
    return {
        (row["commit_sha"], row["path"]): _file_version(row)
        for row in connection.execute(
            f"SELECT * FROM file_versions WHERE path IN ({_placeholders(names)})",
            names,
        )
    }


def read_definition_versions(
    connection: sqlite3.Connection, commit_sha: str, path: str
) -> tuple[DefinitionVersion, ...]:
    """The definitions one stored file version held, in the order they appeared."""
    return tuple(
        _definition_version(row)
        for row in connection.execute(
            "SELECT * FROM definition_versions WHERE commit_sha = ? AND path = ?"
            " ORDER BY position",
            (commit_sha, path),
        )
    )


def read_definitions_for_paths(
    connection: sqlite3.Connection, paths
) -> dict[tuple[str, str], tuple[DefinitionVersion, ...]]:
    """The stored definitions of the given paths, grouped by the version holding them.

    Each version's definitions come back in the order they appeared in the file,
    which is the order a definition of a repeated name is counted in.
    """
    names = list(paths)
    if not names:
        return {}
    grouped: dict[tuple[str, str], list[DefinitionVersion]] = {}
    for row in connection.execute(
        f"SELECT * FROM definition_versions WHERE path IN ({_placeholders(names)})"
        " ORDER BY commit_sha, path, position",
        names,
    ):
        grouped.setdefault((row["commit_sha"], row["path"]), []).append(
            _definition_version(row)
        )
    return {key: tuple(rows) for key, rows in grouped.items()}


def read_all_definition_versions(
    connection: sqlite3.Connection,
) -> dict[tuple[str, str], tuple[DefinitionVersion, ...]]:
    """Every stored definition, grouped by the version that held it.

    One query for the whole table, which is what the AST pass needs: on a
    second run it reuses every version and would otherwise ask for one version's
    definitions at a time — measured at 250,000 queries and 9.8 s on a history
    of that many versions, against one query here.

    The rows come back ordered by version and position, so each version's
    definitions are in the order they appeared in the file. That order is what
    tells two definitions sharing a qualified name apart, and what the *n*-th
    occurrence rule counts in.

    A version that held no definitions has no key in the answer, which is the
    same absence the table itself carries: a version that was read and held
    nothing is not the same thing as one that could not be read, and that
    difference lives on :class:`FileVersion` rather than here.
    """
    grouped: dict[tuple[str, str], list[DefinitionVersion]] = {}
    for row in connection.execute(
        "SELECT * FROM definition_versions ORDER BY commit_sha, path, position"
    ):
        grouped.setdefault((row["commit_sha"], row["path"]), []).append(
            _definition_version(row)
        )
    return {key: tuple(rows) for key, rows in grouped.items()}


def _placeholders(names) -> str:
    """One ``?`` per name, for a statement whose values are all parameters."""
    return ", ".join("?" * len(names))


def _file_version(row) -> FileVersion:
    """One row of ``file_versions`` as a record."""
    return FileVersion(
        commit_sha=row["commit_sha"],
        path=row["path"],
        content_sha=row["content_sha"],
        parsed_at_version=row["parsed_at_version"],
        parse_error=row["parse_error"],
        error_lineno=row["error_lineno"],
        error_offset=row["error_offset"],
    )


def _definition_version(row) -> DefinitionVersion:
    """One row of ``definition_versions`` as a record."""
    return DefinitionVersion(
        commit_sha=row["commit_sha"],
        path=row["path"],
        position=row["position"],
        kind=row["kind"],
        qualname=row["qualname"],
        change_type=row["change_type"],
        fingerprint=row["fingerprint"],
        decorators=tuple(json.loads(row["decorators"])),
        lineno=row["lineno"],
        end_lineno=row["end_lineno"],
    )


def read_stored_commits(connection: sqlite3.Connection) -> list[Commit]:
    """Return everything in the database, newest commit first.

    The order comes from ``committed_epoch``, which is the one thing stored
    twice on purpose: the ISO text keeps the offset the author's clock had, and
    the integer sorts chronologically no matter which offsets are mixed
    together.
    """
    return _assemble(connection, "")


def find_commits(connection: sqlite3.Connection, prefix: str) -> list[Commit]:
    """Return the stored commits whose sha starts with *prefix*, newest first.

    The prefix is used as a LIKE pattern, so it has to hold sha characters and
    nothing else. Callers check that before getting here.
    """
    return _assemble(connection, "WHERE sha LIKE ?", (f"{prefix}%",))


def find_commits_touching(connection: sqlite3.Connection, path: str) -> list[Commit]:
    """Return the stored commits that touched *path*, newest first.

    A rename counts under both of the names it involves. The commit that moved
    ``old`` to ``new`` touched ``old`` by taking it away and ``new`` by putting
    it there, and the row keeps the two paths in different columns, so both are
    matched.

    This answers for a name as written. A file that was renamed keeps one
    history under several names, and that question belongs to the lifecycle.

    Both columns are matched, so this scans ``commit_files`` rather than seeking
    through the index on ``path``. At fifty-seven thousand rows that costs about
    a twentieth of a second, which is the price of not indexing ``old_path`` for
    a query that is asked once per file rather than once per row.
    """
    return _assemble(
        connection,
        "WHERE sha IN (SELECT commit_sha FROM commit_files"
        " WHERE path = ? OR old_path = ?)",
        (path, path),
    )


def _assemble(connection: sqlite3.Connection, where: str, parameters=()) -> list[Commit]:
    """Select commits and attach each one's parents and files.

    The children are fetched with a subquery repeating the same selection rather
    than a list of placeholders, which would run into SQLite's ceiling on how
    many parameters a single statement may take once a repository is large.
    """
    commit_rows = connection.execute(
        f"SELECT * FROM commits {where} ORDER BY committed_epoch DESC", parameters
    ).fetchall()
    if not commit_rows:
        return []

    selection = f"SELECT sha FROM commits {where}"

    parents: dict[str, list[tuple[int, str]]] = {}
    for row in connection.execute(
        "SELECT commit_sha, position, parent_sha FROM commit_parents"
        f" WHERE commit_sha IN ({selection})",
        parameters,
    ):
        parents.setdefault(row["commit_sha"], []).append(
            (row["position"], row["parent_sha"])
        )

    files: dict[str, list[FileChange]] = {}
    for row in connection.execute(
        f"SELECT * FROM commit_files WHERE commit_sha IN ({selection}) ORDER BY path",
        parameters,
    ):
        files.setdefault(row["commit_sha"], []).append(
            FileChange(
                path=row["path"],
                change_type=row["change_type"],
                added_lines=row["added_lines"],
                deleted_lines=row["deleted_lines"],
                old_path=row["old_path"],
                similarity=row["similarity"],
            )
        )

    stored = []
    for row in commit_rows:
        stored.append(
            Commit(
                sha=row["sha"],
                parents=tuple(
                    parent for _, parent in sorted(parents.get(row["sha"], []))
                ),
                author_name=row["author_name"],
                author_email=row["author_email"],
                authored_at=datetime.fromisoformat(row["authored_at"]),
                committed_at=datetime.fromisoformat(row["committed_at"]),
                message=row["message"],
                changes=tuple(files.get(row["sha"], [])),
            )
        )
    return stored
