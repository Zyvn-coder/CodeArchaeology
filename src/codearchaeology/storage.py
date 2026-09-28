"""Store commit history in SQLite.

The database is a cache over a repository, not the system of record: every row
here can be rebuilt by reading git again. That is why the schema only holds
facts git reported, and nothing that only a later version of the tool could
compute.

Schema version 2 is four tables:

* ``meta``           key/value pairs, including the schema version
* ``commits``        one row per commit
* ``commit_parents`` one row per parent, in order
* ``commit_files``   one row per file touched by a commit

Parents get their own table rather than a space-separated column so that
"find every merge commit" stays an ordinary grouped query instead of a string
match.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from codearchaeology.history import Commit, FileChange

SCHEMA_VERSION = "2"

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
    change_type   TEXT    NOT NULL,
    added_lines   INTEGER,
    deleted_lines INTEGER,
    similarity    INTEGER,
    PRIMARY KEY (commit_sha, path)
);

CREATE INDEX IF NOT EXISTS commit_parents_parent ON commit_parents (parent_sha);
CREATE INDEX IF NOT EXISTS commit_files_path ON commit_files (path);
"""

TABLES = ("commit_files", "commit_parents", "commits", "meta")


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
    """Remove every stored commit, keeping the schema and the meta rows.

    A full rescan calls this first: once a rebase or an amend has removed
    commits from git, nothing else would ever take them out of the database.
    """
    with connection:
        connection.execute("DELETE FROM commit_files")
        connection.execute("DELETE FROM commit_parents")
        connection.execute("DELETE FROM commits")


def write_commits(connection: sqlite3.Connection, commits) -> None:
    """Write *commits*, replacing whatever is already stored for their shas."""
    rows = list(commits)
    shas = [(commit.sha,) for commit in rows]

    with connection:
        # Children first. The foreign keys are enforced, so a commit cannot be
        # removed while rows in commit_parents or commit_files still point at
        # it. Doing this also makes writing the same commit twice a no-op
        # instead of a duplicate-key error.
        connection.executemany("DELETE FROM commit_files WHERE commit_sha = ?", shas)
        connection.executemany("DELETE FROM commit_parents WHERE commit_sha = ?", shas)
        connection.executemany("DELETE FROM commits WHERE sha = ?", shas)

        connection.executemany(
            "INSERT INTO commits (sha, author_name, author_email, authored_at,"
            " committed_at, committed_epoch, message) VALUES (?, ?, ?, ?, ?, ?, ?)",
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
