"""Measure what each database index costs, and what it buys.

Run it by hand, not in CI::

    uv run python benchmarks/benchmark.py --commits 50000 --workdir D:/tmp/u17
    uv run python benchmarks/index_benchmark.py --workdir D:/tmp/u17 --scale 50000
    uv run python benchmarks/index_benchmark.py --workdir D:/tmp/u17 --scale 50000 \
        --candidate "commits(committed_epoch)"

It answers one question — **which indexes earn their place** — and it answers it
with three numbers per index rather than one, because "an index makes reads
faster" is only a third of the fact:

* **what the queries lose without it.** Every read the tool issues is timed with
  the index present and with it dropped, and the plans are captured beside the
  times so it is visible *whether a query uses the index at all*. An index no plan
  mentions buys nothing, whatever it costs. The report prints only the plans that
  changed, which is the shortest way to say what depends on what.
* **what the database saves.** Each probe is a copy that has been vacuumed, and
  the control — the same copy, vacuumed, with no index touched — is the size a
  probe's size is read against. A vacuum rebuilds every table and reclaims the
  free pages the pass's own delete-and-insert leaves behind, so without the
  control an index would be charged for the vacuum's work.
* **what a write pays.** The same rows are written into a stripped copy with the
  index present and with it dropped, so the difference is what maintaining the
  index costs per insert.

Every column in the read table is therefore a vacuumed copy, the control
included, and the control is also the write's reference.

A ``--candidate`` is measured the other way round — created on a copy instead of
dropped — and reported in the same table, so "should we add this one?" is answered
with the same three numbers as "should we keep this one?".

The rows for the write side are read out of the database the scale fixture built
and written back through the same ``write_ast_batch`` the pass uses, in the same
batches of 500 and **in the order the pass writes them** — see
:func:`load_pairs`, which is where that order is argued. It is a replay rather
than a full pass: the git reads and the parsing are identical either way, and
repeating them once per index would cost minutes to measure nothing. Read the
*difference*, not the absolute number.
"""

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from benchmark import _table
from codearchaeology.lifecycle import build_lifecycles
from codearchaeology.storage import (
    connect,
    find_commits,
    find_commits_touching,
    read_all_definition_versions,
    read_definition_versions,
    read_definitions_for_paths,
    read_file_versions,
    read_stored_commits,
    read_versions_for_paths,
    write_ast_batch,
    write_commits,
)

DEFAULT_REPEAT = 3
BATCH = 500
CANDIDATE_NAME = "candidate_index"


def queries(path: str, commit_sha: str) -> list[tuple[str, object]]:
    """The reads the tool actually issues, as the functions that issue them.

    Named after the command that pays them rather than after the table they touch:
    the question is what a user waits for. The functions are called rather than
    their SQL copied, because a copy of a statement can drift from the statement
    the tool runs and the plan is only interesting for the real one.
    """
    return [
        ("timeline / files", lambda c: read_stored_commits(c)),
        ("commit", lambda c: find_commits(c, commit_sha[:8])),
        ("file (by name)", lambda c: find_commits_touching(c, path)),
        ("pass: versions", lambda c: read_file_versions(c)),
        ("pass: definitions", lambda c: read_all_definition_versions(c)),
        ("structure: versions", lambda c: read_versions_for_paths(c, [path])),
        ("structure: definitions", lambda c: read_definitions_for_paths(c, [path])),
        (
            "structure: one version",
            lambda c: read_definition_versions(c, commit_sha, path),
        ),
    ]


def plans(connection: sqlite3.Connection, query) -> list[str]:
    """The plans SQLite chose for the statements *query* issues.

    Captured with a trace callback instead of copied from the code, so the plan
    belongs to the statement that actually ran. Parameters are unbound in the
    EXPLAIN, which can change a plan that depends on the value; none of these do,
    and the times beside them are the check on that.
    """
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        query(connection)
    finally:
        connection.set_trace_callback(None)

    chosen = []
    for statement in statements:
        detail = connection.execute(f"EXPLAIN QUERY PLAN {statement}").fetchall()
        chosen.append(" | ".join(row["detail"] for row in detail))
    return chosen


def time_queries(connection: sqlite3.Connection, entries, repeat: int) -> dict[str, float]:
    """The fastest of *repeat* runs of each query, in seconds."""
    times = {}
    for name, query in entries:
        best = None
        for _ in range(repeat):
            start = time.perf_counter()
            query(connection)
            spent = time.perf_counter() - start
            best = spent if best is None else min(best, spent)
        times[name] = best
    return times


def index_names(connection: sqlite3.Connection) -> list[str]:
    """The indexes the schema declares, in the order they were created.

    ``sql IS NOT NULL`` leaves out the ones SQLite creates for a primary key or a
    UNIQUE constraint: those are not a decision anybody can revisit here, and
    dropping one is not the experiment.
    """
    return [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            " ORDER BY rowid"
        )
    ]


def index_tables(connection: sqlite3.Connection) -> dict[str, str]:
    """Which table each declared index is on, read from the schema itself.

    It decides which of the two writes an index is measured against, so it is
    read rather than listed here: a list would be a second copy of the schema,
    and the two would drift the day an index moved to another table.
    """
    return {
        row["name"]: row["tbl_name"]
        for row in connection.execute(
            "SELECT name, tbl_name FROM sqlite_master"
            " WHERE type = 'index' AND sql IS NOT NULL"
        )
    }


def index_definitions(connection: sqlite3.Connection) -> dict[str, str]:
    """The ``CREATE INDEX`` statement of each declared index, as SQLite stored it.

    Needed to put an index back after it has been dropped, and read from the
    schema for the same reason the tables are: the statement in the schema is the
    one the database was built with, and a copy of it here could differ.
    """
    return {
        row["name"]: row["sql"]
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master"
            " WHERE type = 'index' AND sql IS NOT NULL"
        )
    }


def copy_database(source: Path, destination: Path) -> None:
    """A byte copy, so changing an index in it cannot touch the original."""
    destination.unlink(missing_ok=True)
    shutil.copyfile(source, destination)


def vacuum_size(database: Path) -> float:
    """The file's size in megabytes, after a vacuum has reclaimed what it can."""
    connection = connect(database)
    try:
        connection.execute("VACUUM")
    finally:
        connection.close()
    return database.stat().st_size / 1e6


def stripped(source: Path, destination: Path) -> None:
    """A copy of *source* with the AST layer emptied, ready to be written into.

    The commits and their file changes stay: a file version names its commit and
    the foreign key is enforced, so the rows being replayed need the history they
    belong to. The rows themselves are what the replay writes.
    """
    copy_database(source, destination)
    connection = connect(destination)
    try:
        with connection:
            connection.execute("DELETE FROM definition_versions")
            connection.execute("DELETE FROM file_versions")
        connection.execute("VACUUM")
    finally:
        connection.close()


def stripped_history(source: Path, destination: Path) -> None:
    """A copy of *source* with everything emptied, ready to be written into.

    The other half of the write: the commits, their parents and their file
    changes, which ``analyze`` rewrites in full every time it runs. An index on
    one of those tables cannot be measured by the AST replay, which never touches
    them — its write cost belongs to this one.
    """
    copy_database(source, destination)
    connection = connect(destination)
    try:
        with connection:
            connection.execute("DELETE FROM definition_versions")
            connection.execute("DELETE FROM file_versions")
            connection.execute("DELETE FROM commit_files")
            connection.execute("DELETE FROM commit_parents")
            connection.execute("DELETE FROM commits")
        connection.execute("VACUUM")
    finally:
        connection.close()


def replay(database: Path, pairs) -> float:
    """Write *pairs* into *database* the way the pass does, and time it.

    Batches of 500, one transaction per batch — the pass's own shape — and the
    rows in the pass's own order. The only difference between two runs of this
    function is the index set of the database it writes into.
    """
    connection = connect(database)
    try:
        start = time.perf_counter()
        batch = []
        for pair in pairs:
            batch.append(pair)
            if len(batch) >= BATCH:
                write_ast_batch(connection, batch)
                batch.clear()
        if batch:
            write_ast_batch(connection, batch)
        return time.perf_counter() - start
    finally:
        connection.close()


def replay_history(database: Path, commits) -> float:
    """Write *commits* into *database* the way ``analyze`` does, and time it.

    One call for the whole history, one transaction — that is the shape
    ``write_commits`` has, and the shape the rescan pays for.
    """
    connection = connect(database)
    try:
        start = time.perf_counter()
        write_commits(connection, commits)
        return time.perf_counter() - start
    finally:
        connection.close()


def load_pairs(database: Path):
    """Every stored version with its definitions, in the order the pass writes them.

    The order matters more than it looks. An index is maintained row by row, and
    what that costs depends on whether the keys arrive in the order the index
    keeps them: a b-tree fed in its own order is appended to, and one fed in
    another order is scattered over. The pass writes a file's versions one after
    another — the walk visits lives, and each life's events in order — so the keys
    of an index on ``(path, ...)`` arrive clustered and the keys of one on
    ``(commit_sha, ...)`` arrive scattered.

    A replay in *storage* order (by commit, then path) inverts both, and the first
    version of this script did exactly that: it reported that maintaining
    ``definition_versions_qualname`` through a write cost 65.2 s against 1.1 s to
    build it afterwards, and an A/B on the pass's own path showed the two within
    1% of each other (12.4 s against 12.5 s at 10,000 commits). The measurement
    was of the order, not of the index.
    """
    connection = connect(database)
    try:
        versions = read_file_versions(connection)
        definitions = read_all_definition_versions(connection)
        commits = read_stored_commits(connection)
    finally:
        connection.close()

    ordered = []
    for life in build_lifecycles(commits):
        for event in life.events:
            key = (event.commit_sha, event.path)
            if key in versions:
                ordered.append((versions[key], definitions.get(key, ())))

    # Anything the walk did not reach — a version whose path is not a Python file
    # is not stored, but a table can hold rows no life owns — goes in after the
    # walk's order rather than being left out of the replay.
    seen = {(version.commit_sha, version.path) for version, _ in ordered}
    for key in sorted(versions):
        if key not in seen:
            ordered.append((versions[key], definitions.get(key, ())))
    return ordered


def load_commits(database: Path):
    """Every stored commit, in the order the store hands them over."""
    connection = connect(database)
    try:
        return read_stored_commits(connection)
    finally:
        connection.close()


def measure_change(
    database: Path,
    ddl: str,
    entries,
    pairs,
    commits,
    repeat: int,
    workdir: Path,
    scale: int,
    rebuild: str | None = None,
) -> dict:
    """Re-measure the reads, the size and both writes with one index changed.

    *ddl* is what to run against the copy — a DROP for an index that exists, a
    CREATE for one that does not, and a no-op for the control that changes
    nothing. The copy is vacuumed in every case, the control included: a vacuum
    rebuilds every table and reclaims the free pages the pass's own
    delete-and-insert leaves behind, so a probe compared against an un-vacuumed
    original would charge the index for the vacuum's work and measure its reads
    on a layout the probes do not have.

    Two writes, because the indexes live on two sets of tables: the AST replay
    maintains the indexes on the versions and their definitions, and the history
    replay maintains the ones on the commits, their parents and their file
    changes. An index is only ever read against the write that touches it.

    *rebuild* is the index's own ``CREATE INDEX``, timed after the replay on a
    table that now holds every row: it is the other way to end up with the index,
    and the number that says whether dropping it for the load and rebuilding it
    afterwards is cheaper than maintaining it row by row.
    """
    probe = workdir / f"index-probe-{scale}.db"

    copy_database(database, probe)
    connection = connect(probe)
    try:
        connection.execute(ddl)
    finally:
        connection.close()
    vacuumed_mb = vacuum_size(probe)
    connection = connect(probe)
    try:
        chosen = {name: plans(connection, query) for name, query in entries}
        times = time_queries(connection, entries, repeat)
    finally:
        connection.close()

    stripped(database, probe)
    connection = connect(probe)
    try:
        connection.execute(ddl)
    finally:
        connection.close()
    wrote_ast = replay(probe, pairs)

    rebuilt = None
    if rebuild is not None:
        connection = connect(probe)
        try:
            start = time.perf_counter()
            connection.execute(rebuild)
            rebuilt = time.perf_counter() - start
        finally:
            connection.close()

    stripped_history(database, probe)
    connection = connect(probe)
    try:
        connection.execute(ddl)
    finally:
        connection.close()
    wrote_history = replay_history(probe, commits)
    probe.unlink(missing_ok=True)

    return {
        "vacuumed_mb": vacuumed_mb,
        "write_ast": wrote_ast,
        "write_history": wrote_history,
        "rebuild": rebuilt,
        "times": times,
        "plans": chosen,
    }


def measure(database: Path, scale: int, repeat: int, workdir: Path, candidates):
    """Everything the report needs, for one scale's database."""
    connection = connect(database)
    try:
        names = index_names(connection)
        tables = index_tables(connection)
        definitions = index_definitions(connection)
        sizes = {
            table: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()[0]
            for table in (
                "commits",
                "commit_files",
                "file_versions",
                "definition_versions",
            )
        }
        # A path and a commit that exist, so the queries have something to find
        # rather than being timed on an empty answer. The path comes from
        # ``definition_versions`` rather than from ``file_versions`` because a
        # file that held nothing — one that could not be parsed, or one of
        # nothing but comments — is a version with no definitions, and the
        # busiest of those would measure the definitions query on an empty
        # answer while looking like a real row.
        path = connection.execute(
            "SELECT path FROM definition_versions GROUP BY path"
            " ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()["path"]
        commit_sha = connection.execute(
            "SELECT commit_sha FROM file_versions WHERE path = ? LIMIT 1", (path,)
        ).fetchone()["commit_sha"]
        entries = queries(path, commit_sha)
        original_mb = database.stat().st_size / 1e6
    finally:
        connection.close()

    pairs = load_pairs(database)
    commits = load_commits(database)

    def changed(ddl: str, rebuild: str | None = None) -> dict:
        return measure_change(
            database, ddl, entries, pairs, commits, repeat, workdir, scale, rebuild
        )

    # The control changes no index and is vacuumed like every probe, so it is
    # what the probes are compared against: its size is the reference a size
    # delta is read from, its reads are the column the others are read against,
    # and its two writes are what the same rows cost with every index in place.
    # Without it a vacuum's own reclaiming would be charged to whichever index
    # happened to be dropped.
    control = changed("SELECT 1")
    readings = [
        {
            "index": name,
            "table": tables[name],
            **changed(f"DROP INDEX {name}", rebuild=definitions[name]),
        }
        for name in names
    ]
    added = [
        {
            "index": f"{definition} (candidate)",
            "table": definition.split("(")[0].strip(),
            **changed(f"CREATE INDEX {CANDIDATE_NAME} ON {definition}"),
        }
        for definition in candidates
    ]
    # The other way to end up with a candidate index: write the rows first and
    # build it over the finished table. It is the same probe as the control with
    # a CREATE timed at the end, so it says whether the candidate is cheaper to
    # maintain row by row or to build once.
    built_after = [
        {
            "index": f"{definition} (built after the load)",
            "table": definition.split("(")[0].strip(),
            **changed(
                "SELECT 1", rebuild=f"CREATE INDEX {CANDIDATE_NAME} ON {definition}"
            ),
        }
        for definition in candidates
    ]

    return {
        "names": names,
        "sizes": sizes,
        "path": path,
        "original_mb": original_mb,
        "control": control,
        "readings": readings,
        "added": added,
        "built_after": built_after,
    }


# The tables the AST replay writes. Everything else a declared index sits on is
# maintained by the history replay, which is what `analyze` pays.
AST_TABLES = ("file_versions", "definition_versions")


def _write_key(reading: dict) -> str:
    """Which replay maintains this index: the one that writes its table."""
    return "write_ast" if reading["table"] in AST_TABLES else "write_history"

def _write_delta(control: dict, reading: dict) -> float:
    key = _write_key(reading)
    return reading[key] - control[key]


def _write_base(control: dict, reading: dict) -> float:
    return control[_write_key(reading)]


def _plans_that_changed(measured: dict, reading: dict) -> None:
    """Print the plans this index change moved, and nothing else.

    A query whose plan is the same either way is a query that does not care about
    this index, which is exactly the sentence the table cannot say.
    """
    for name, statements in reading["plans"].items():
        before = measured["control"]["plans"][name]
        for index, (was, now) in enumerate(zip(before, statements)):
            if was != now:
                print(f"    {name}[{index}]")
                print(f"      {was}")
                print(f"      -> {now}")


def report(measured: dict, scale: int) -> None:
    control = measured["control"]
    print()
    print(f"the database — {scale:,} commits, {measured['original_mb']:.1f} MB")
    _table(
        ["table", "rows"],
        [[table, f"{count:,}"] for table, count in measured["sizes"].items()],
    )
    print(f"  asked about: {measured['path']}")
    print(
        f"  {measured['original_mb']:.1f} MB is the file as the pass left it;"
        f" {control['vacuumed_mb']:.1f} MB is the same file vacuumed with every index"
    )
    print("  in place. That difference is the free pages the pass's own delete and")
    print("  insert leaves behind, and it belongs to no index.")

    print()
    print("the plans, with every index in place")
    for name, statements in control["plans"].items():
        print(f"  {name}")
        for detail in statements:
            print(f"    {detail}")

    print()
    print("reads, seconds — fastest of several, with and without each index")
    columns = list(control["plans"])
    width = max(len(name) for name in columns) + 1
    print(f"{'changed index':>28} " + "".join(f"{name:>{width}}" for name in columns))
    print(
        f"{'(none)':>28} "
        + "".join(f"{control['times'][name]:>{width - 1}.3f}s" for name in columns)
    )
    for reading in measured["readings"] + measured["added"]:
        print(
            f"{reading['index']:>28} "
            + "".join(f"{reading['times'][name]:>{width - 1}.3f}s" for name in columns)
        )
    print("  Every column is a vacuumed copy, the control included, so a row is read")
    print("  against the same layout the others are.")

    print()
    print("what each index costs")
    _table(
        ["index", "table", "size MB", "write s", "write %", "rebuild s"],
        [
            [
                reading["index"],
                reading["table"],
                f"{control['vacuumed_mb'] - reading['vacuumed_mb']:.1f}",
                f"{_write_delta(control, reading):+.1f}",
                f"{100 * _write_delta(control, reading) / _write_base(control, reading):+.0f}%",
                "-" if reading["rebuild"] is None else f"{reading['rebuild']:.1f}",
            ]
            for reading in measured["readings"]
            + measured["added"]
            + measured["built_after"]
        ],
    )
    print("  size MB   what the vacuumed file loses when the index is dropped")
    print("  write s   the same rows written again without it, against the write that")
    print("            touches it, with every index in place:")
    print(
        f"            {control['write_ast']:.1f}s for the AST layer,"
        f" {control['write_history']:.1f}s for the git facts"
    )
    print("  rebuild s the index built again over the finished table — the other way")
    print("            to end up with it, and the cheaper one if it is below write s")
    print("  A negative write is the noise of a b-tree that is one index lighter;")
    print("  read it as 'no measurable cost', not as a saving.")

    print()
    print("the plans each change moved")
    for reading in measured["readings"]:
        print(f"  without {reading['index']}")
        _plans_that_changed(measured, reading)
    for reading in measured["added"]:
        print(f"  with {reading['index']}")
        _plans_that_changed(measured, reading)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scale", type=int, default=50_000)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="An index to try adding, as the columns it is on:"
        ' e.g. "commits(committed_epoch)". Repeatable.',
    )
    arguments = parser.parse_args(argv)

    workdir = arguments.workdir
    scale = arguments.scale
    database = workdir / f"history-{scale}.db"
    if not database.exists():
        print(
            f"missing {database}: build the fixture first with"
            f" benchmarks/benchmark.py --commits {scale} --workdir {workdir}",
            file=sys.stderr,
        )
        return 2

    measured = measure(database, scale, arguments.repeat, workdir, arguments.candidate)
    report(measured, scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
