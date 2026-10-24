"""The run record: what was asked of a model, and never what it said.

This is option E of the persistence ruling — the run's provenance, not its
text. A row is written exactly when an explanation is printed: the unit it
explained, the endpoint and the model that answered, the hash of the evidence
bundle the answer was built from, the hash of the prompt's instructions, and
the moment. Not one sentence a model wrote is stored anywhere, which is the
first half of the ruling; the second half is what the row is *for* — "was this
explanation produced from the bundle I have now?" is answered by re-explaining
and comparing two hashes, which is the only checkable meaning "replay" has.

The table is not evidence. It keeps its own version stamp beside the evidence
schema's (the way the memory layer keeps ``memory_schema_version``), it is not
one of ``storage``'s tables, and nothing in the evidence layer reads it: a run
is history about the tool, the way an exit status is, not history about the
repository.

The name is ``explain_runs`` rather than anything with "provenance" in it
because that word already means memory's subject-and-citation re-checking in
this project (v0.5 Unit 7), and a second thing wearing it would blur the first.
"""

import sqlite3
from dataclasses import dataclass

from codearchaeology.storage import get_meta, set_meta

RUNS_SCHEMA_VERSION = "1"
RUNS_VERSION_KEY = "explain_runs_schema_version"

TABLE = "explain_runs"

_CREATE = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_kind TEXT NOT NULL,
    unit_spec TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


class RunsStoreError(RuntimeError):
    """Raised when the run table cannot be written or trusted.

    The command that hits this carries on and prints the answer it already
    paid for, with the refusal as a note: the record is metadata about a run,
    and metadata nobody can write is not a reason to hide a checked answer.
    """


@dataclass(frozen=True, slots=True)
class Run:
    """One printed explanation, as the five ruled facts and its unit.

    ``unit_kind`` is ``commit`` or ``range``; ``unit_spec`` is the sha or
    ``base..tip`` with both shas resolved. ``bundle_sha256`` is the same value
    the envelope of that run carried, so a stored row and a fresh run can be
    compared without re-deriving anything but the bundle.
    """

    unit_kind: str
    unit_spec: str
    bundle_sha256: str
    provider: str
    model: str
    prompt_sha256: str
    created_at: str


def prepare_runs(connection: sqlite3.Connection) -> None:
    """Make the run table ready to be written, leaving the evidence alone.

    Creates the table and stamps the version when they are not there, and is a
    no-op when they are. Two shapes are refused rather than guessed about: a
    table this tool did not stamp, and a stamp from a newer tool. The stamp
    moves independently of the evidence schema's, because a run row is not
    evidence and must never be reachable as though it were.
    """
    present = _table_present(connection)
    stamp = get_meta(connection, RUNS_VERSION_KEY) if present else None

    if present and stamp != RUNS_SCHEMA_VERSION:
        raise RunsStoreError(
            f"the run table in {_file(connection)} carries stamp {stamp!r},"
            f" and this tool writes {RUNS_SCHEMA_VERSION!r};"
            f" its rows are not this tool's to interpret"
        )

    with connection:
        connection.execute(_CREATE)
        set_meta(connection, RUNS_VERSION_KEY, RUNS_SCHEMA_VERSION)


def record(
    connection: sqlite3.Connection,
    *,
    unit_kind: str,
    unit_spec: str,
    bundle_sha256: str,
    provider: str,
    model: str,
    prompt_sha256: str,
    created_at: str,
) -> None:
    """Append one run. Exactly the ruled facts, and nothing a model wrote."""
    prepare_runs(connection)
    with connection:
        connection.execute(
            f"INSERT INTO {TABLE} (unit_kind, unit_spec, bundle_sha256, provider,"
            " model, prompt_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (unit_kind, unit_spec, bundle_sha256, provider, model, prompt_sha256, created_at),
        )


def read_runs(connection: sqlite3.Connection, limit: int = 20) -> tuple[Run, ...]:
    """The newest runs, newest first.

    A database the table was never written to has none, which is the ordinary
    state of a repository nobody has explained with a model yet — an empty
    answer, not an error.
    """
    if not _table_present(connection):
        return ()
    rows = connection.execute(
        f"SELECT unit_kind, unit_spec, bundle_sha256, provider, model,"
        f" prompt_sha256, created_at FROM {TABLE} ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return tuple(
        Run(
            unit_kind=row["unit_kind"],
            unit_spec=row["unit_spec"],
            bundle_sha256=row["bundle_sha256"],
            provider=row["provider"],
            model=row["model"],
            prompt_sha256=row["prompt_sha256"],
            created_at=row["created_at"],
        )
        for row in rows
    )


def _table_present(connection: sqlite3.Connection) -> bool:
    return bool(
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (TABLE,),
        ).fetchone()
    )


def _file(connection: sqlite3.Connection) -> str:
    for row in connection.execute("PRAGMA database_list"):
        return row[2] or ":memory:"
    return ":memory:"
