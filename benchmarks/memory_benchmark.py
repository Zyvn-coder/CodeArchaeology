"""Measure what a store of memories costs to read and to write, at scale.

Run it by hand, not in CI::

    uv run python benchmarks/memory_benchmark.py --sizes 10 100 1000 10000
    uv run python benchmarks/memory_benchmark.py --sizes 100000 --workdir D:/tmp/memory

Assertions on wall-clock time fail on a busy machine and teach people to re-run
them, which is the rule this project's other benchmarks already follow.

It answers one question — **what grows, and with what** — with one row per size:

* **`create`** is the per-admission cost of a batch, so it says whether writing
  gets dearer as the table grows rather than only what one write costs.
* **`list`** and **`list --json`** are the two read shapes a person uses: the
  block shows twenty rows and the JSON carries every one. They are measured
  apart because they do different amounts of work for a reason, and a reader
  should be able to see which of the two they are paying for.
* **`show`** is one memory by prefix, which should not care how many others
  there are.
* **`section`** is what ``explain`` pays: the memories related to one commit,
  selected and split. It reads the repository's rows and checks the few it
  shows.
* **size** is the file, so a memory's cost in bytes is visible beside its cost
  in time.

The fixture is written straight into the tables rather than through the acts —
a hundred thousand admissions is minutes of setup to measure nothing — and
``create`` is timed separately, on the same store, so the write path is still
measured through its own code.
"""

import argparse
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from codearchaeology.analysis import analyze  # noqa: E402
from codearchaeology.cli import app  # noqa: E402
from codearchaeology.context import build_context  # noqa: E402
from codearchaeology.memory import MEMORY_SCHEMA, prepare_memory  # noqa: E402
from codearchaeology.memory_checks import Evidence  # noqa: E402
from codearchaeology.memory_section import build_section  # noqa: E402
from codearchaeology.storage import connect, set_meta  # noqa: E402
from sample_repo import build_sample_repo  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

SIZES = (10, 100, 1_000, 10_000, 100_000)
FIXED = "Fix login bug"

# Every tenth memory carries a citation and every tenth has a start, so the
# tables and the temporal rule both have rows to work on rather than an empty
# path that would make the read look cheap for the wrong reason.
CITATION_EVERY = 10
SINCE_EVERY = 10

STATEMENT = "Statement {number} about how this project is put together."


def fill(database: Path, repository: Path, count: int, first: str) -> None:
    """Write *count* memories, oldest first, in one transaction.

    Straight into the tables: this is the fixture, and the act that a person
    uses is timed separately. The rows are shaped like real ones — a path
    subject, some with citations, some with a start — because a fixture of empty
    statements would make every read look cheap.
    """
    rows = []
    citations = []
    for number in range(count):
        memory_id = str(uuid4())
        since = first if number % SINCE_EVERY == 0 else None
        rows.append(
            (
                memory_id,
                str(repository),
                STATEMENT.format(number=number),
                "Ada Lovelace",
                "ada@example.com",
                "2024-06-01T09:00:00+00:00",
                1717232400 + number,
                "path",
                "core/app.py",
                None,
                None,
                since,
                None,
                "active",
            )
        )
        if number % CITATION_EVERY == 0:
            citations.append((memory_id, 0, "file", "core/app.py"))

    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO memories (memory_id, repository_path, statement, author_name,"
            " author_email, admitted_at, admitted_epoch, subject_kind, subject_path,"
            " subject_qualname, subject_commit_sha, since_commit_sha, since_date, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.executemany(
            "INSERT INTO memory_citations (memory_id, position, kind, ref)"
            " VALUES (?, ?, ?, ?)",
            citations,
        )


def timed(action, times: int = 1) -> float:
    """The best of *times* runs, in seconds — the machine's noise, not the work."""
    best = None
    for _ in range(times):
        started = time.perf_counter()
        action()
        elapsed = time.perf_counter() - started
        best = elapsed if best is None else min(best, elapsed)
    return best


def measure(workdir: Path, count: int) -> dict:
    """One size: build the store, then time every read and the write."""
    repository = build_sample_repo(workdir / f"repo-{count}")
    database = workdir / f"memory-{count}.db"
    analyze(repository, database)

    connection = connect(database)
    try:
        prepare_memory(connection)
        first = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", ("Initial commit",)
        ).fetchone()["sha"]
    finally:
        connection.close()

    fill(database, repository, count, first)

    runner = CliRunner()
    arguments = [str(repository), "--db", str(database)]
    shown = _first_id(database)
    context = build_context(repository, database, _sha_of(database, FIXED))

    connection = connect(database)
    try:
        evidence = Evidence(connection, repository, database)
        found = {
            "create": timed(
                lambda: _one_more(connection, repository, count), times=20
            ),
            "list": timed(
                lambda: runner.invoke(app, ["memory", "list", *arguments]), times=3
            ),
            "json": timed(
                lambda: runner.invoke(app, ["memory", "list", "--json", *arguments]),
                times=3,
            ),
            "show": timed(
                lambda: runner.invoke(app, ["memory", "show", shown, *arguments]),
                times=3,
            ),
            "section": timed(
                lambda: build_section(evidence, context, repository), times=3
            ),
        }
    finally:
        connection.close()

    return {
        "memories": count,
        "size": database.stat().st_size,
        **found,
    }


def _one_more(connection, repository: Path, number: int) -> None:
    """One admission through the act a person uses, not through the fixture."""
    from codearchaeology.memory import Subject, admit

    admit(
        connection,
        repository_path=repository,
        statement=f"One more statement, number {number}.",
        subject=Subject("path", path="core/app.py"),
    )


def _sha_of(database: Path, message: str) -> str:
    connection = connect(database)
    try:
        return _sha(connection, message)
    finally:
        connection.close()


def _sha(connection, message: str) -> str:
    return connection.execute(
        "SELECT sha FROM commits WHERE message = ?", (message,)
    ).fetchone()["sha"]


def _first_id(database: Path) -> str:
    connection = connect(database)
    try:
        return connection.execute(
            "SELECT memory_id FROM memories ORDER BY admitted_epoch LIMIT 1"
        ).fetchone()["memory_id"][:8]
    finally:
        connection.close()


def report(rows: list[dict]) -> None:
    print()
    print(
        f"{'Memories':>9} {'DB':>9} {'create':>10} {'list':>10} {'list --json':>12}"
        f" {'show':>9} {'section':>9}"
    )
    print("-" * 72)
    for row in rows:
        print(
            f"{row['memories']:>9,} {_size(row['size']):>9} {_ms(row['create']):>10}"
            f" {_ms(row['list']):>10} {_ms(row['json']):>12} {_ms(row['show']):>9}"
            f" {_ms(row['section']):>9}"
        )
    print()
    print("create is one admission, timed over a batch of twenty; the rest are the")
    print("whole command or the whole call, best of three.")


def _ms(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.1f}ms"
    return f"{seconds:.2f}s"


def _size(count: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f}{unit}" if unit == "B" else f"{count:.1f}{unit}"
        count /= 1024
    return f"{count:.1f}GB"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=list(SIZES), help="how many memories"
    )
    parser.add_argument(
        "--workdir", type=Path, default=None, help="where to build the fixtures"
    )
    arguments = parser.parse_args()

    workdir = arguments.workdir or Path(tempfile.mkdtemp(prefix="memory-benchmark-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"working in {workdir}")

    rows = []
    for count in arguments.sizes:
        print(f"building {count:,} memories ...", flush=True)
        rows.append(measure(workdir, count))
        report(rows)

    if arguments.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
