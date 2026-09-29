"""Measure the operations the tool is built around, and the AST pass's phases.

Run it by hand, not in CI::

    uv run python benchmarks/benchmark.py --commits 1000 10000
    uv run python benchmarks/benchmark.py --commits 100000 --files 200

It answers one question — where does the time go as a repository grows — and it
is deliberately not a test. There is no pass or fail here: a wall-clock bound
would fail on a busy CI machine and teach people to re-run it, and the number
that matters is the shape of the curve, not any single value.

**The shape of the generated history**, because every number here depends on it.
Files are created and then edited in place, ``--touched`` per commit, and each
edit flips one function's operator, so a version has one or two modified
definitions and the rest unchanged. One file in ten is written so that it cannot
be parsed: that is what gives the pass a failure rate to report instead of a
sentence claiming everything worked. Read the curve, not the absolute values.

**The AST pass is measured in phases**, because a single total would hide the
interesting part. Reading, parsing, walking the tree, comparing and inserting are
five different things, and only one of them is the parser. ``parse`` is measured
by wrapping ``ast.parse`` for the duration of the run, ``extract`` is what
``definitions_of`` costs on top of it, and ``read``, ``compare`` and ``insert``
are measured by wrapping the pass's own calls. **Every wrapper counts its calls**
and the report prints the count beside the time: a wrapper that stopped being
called would otherwise report a phase that got infinitely fast.

**What the AST layer costs to store** is measured rather than predicted: the row
counts, the database size before and after the pass, and the same rows counted
the way a change log would have stored them. A change log holds only what
changed; a snapshot holds every definition of every version, which is why the two
counts differ and why the difference depends on how much churn a history has
rather than on a constant.
"""

import argparse
import ast as ast_module
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from codearchaeology import ast_pass as ast_pass_module
from codearchaeology import objects as objects_module
from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.definition_history import load_histories
from codearchaeology.definitions import interpreter_version
from codearchaeology.file import build_block, find_files
from codearchaeology.hotspots import rank_hotspots
from codearchaeology.lifecycle import load_lifecycles
from codearchaeology.relationships import load_commits_touching
from codearchaeology.storage import connect
from codearchaeology.structure import (
    build_history as build_history_block,
    build_version,
    definitions_in,
    select_version,
)
from codearchaeology.timeline import build_table, load_timeline

DEFAULT_FILES = 200
DEFAULT_TOUCHED = 5
DEFAULT_REPEAT = 3
FUNCTIONS_PER_FILE = 3
# One file in this many is written so that it cannot be parsed.
BROKEN_EVERY = 10

# How many commits to buffer before handing them to git. One write per commit
# would spend more time in the pipe than in git.
STREAM_CHUNK = 500


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=repo, capture_output=True, text=True, check=True
    )
    return completed.stdout


def commit_count(repo: Path) -> int:
    if not (repo / ".git").is_dir():
        return 0
    try:
        return int(git(repo, "rev-list", "--count", "HEAD").strip())
    except subprocess.CalledProcessError:
        return 0


def _file_body(index: int, edit: int) -> bytes:
    """One revision of one file: valid Python, one function changed per edit.

    Valid Python matters for the half of this benchmark that measures the AST
    layer. A file that does not parse gives the pass nothing to walk, nothing to
    compare and nothing to insert, so a fixture of unparseable files would report
    a fast pass and prove nothing.
    """
    if index % BROKEN_EVERY == 0:
        return (
            f"# module {index}\ndef broken_{index}(value)\n    return {edit}\n".encode()
        )

    body = f"# module {index}\n"
    for number in range(FUNCTIONS_PER_FILE):
        operator = "-" if number == edit % FUNCTIONS_PER_FILE else "+"
        body += f"def function_{number}(value):\n"
        body += f"    return value {operator} {number}\n\n\n"

    body += "class Holder:\n"
    body += "    def get(self):\n        return 0\n\n"
    body += "    def put(self, value):\n        return value\n\n\n"
    body += f"# edit {edit}\n"
    return body.encode()


def build_history(repo: Path, commits: int, files: int, touched: int) -> None:
    """Write *commits* commits into *repo* with ``git fast-import``.

    The stream is fed to git as it is built rather than assembled in memory: at
    two hundred thousand commits the whole thing is a few hundred megabytes, and
    holding it in a list only to hand it over is a way to measure the memory of
    the benchmark instead of the memory of the tool.
    """
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "--initial-branch", "main")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "commit.gpgsign", "false")

    process = subprocess.Popen(
        ["git", "fast-import", "--quiet"],
        cwd=repo,
        stdin=subprocess.PIPE,
    )
    assert process.stdin is not None

    parent = None
    mark = 0
    pending: list[bytes] = []

    def flush() -> None:
        if pending:
            process.stdin.write(b"".join(pending))
            pending.clear()

    for number in range(commits):
        mark += 1
        when = 1700000000 + number * 60
        message = f"Change {number}\n".encode()

        pending.append(f"commit refs/heads/main\nmark :{mark}\n".encode())
        pending.append(f"author Ada <ada@example.com> {when} +0000\n".encode())
        pending.append(f"committer Ada <ada@example.com> {when} +0000\n".encode())
        pending.append(f"data {len(message)}\n".encode() + message)
        if parent is not None:
            pending.append(f"from :{parent}\n".encode())

        for offset in range(touched):
            index = (number + offset) % files
            body = _file_body(index, number)
            # A blob command may not appear inside a commit, so the contents go
            # inline on the M line.
            pending.append(
                f"M 100644 inline pkg{index // 20}/mod{index}.py\ndata {len(body)}\n".encode()
                + body
                + b"\n"
            )

        parent = mark
        if number % STREAM_CHUNK == 0:
            flush()

    flush()
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("git fast-import failed")

    # fast-import writes objects and refs, not the working tree.
    git(repo, "reset", "--hard", "HEAD")


def best_of(repeat: int, operation):
    """Run *operation* *repeat* times and keep the fastest.

    The fastest rather than the average: the thing being measured is the work
    the code does, and every source of noise — another process, a cold cache, a
    background update — can only make a run slower than that.
    """
    times = []
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = operation()
        times.append(time.perf_counter() - start)
    return min(times), result


class Phases:
    """Times the AST pass's phases by wrapping the names it calls them through.

    The pass imports what it uses by name, so replacing the name in its module is
    enough to time that call without changing a line of the pass. Each wrapper
    counts its calls as well as its time, and the report prints the count: a
    wrapper that stopped being called would otherwise look like a phase that got
    faster, which is the one failure a benchmark must not have.
    """

    def __init__(self) -> None:
        self.times: dict[str, float] = {}
        self.calls: dict[str, int] = {}
        self._wrapped: list[tuple[object, str, object]] = []

    def wrap(self, owner, name: str, phase: str) -> None:
        original = getattr(owner, name)

        def timed(*arguments, **keywords):
            start = time.perf_counter()
            try:
                return original(*arguments, **keywords)
            finally:
                spent = time.perf_counter() - start
                self.times[phase] = self.times.get(phase, 0.0) + spent
                self.calls[phase] = self.calls.get(phase, 0) + 1

        setattr(owner, name, timed)
        self._wrapped.append((owner, name, original))

    def restore(self) -> None:
        for owner, name, original in self._wrapped:
            setattr(owner, name, original)
        self._wrapped.clear()


def measure(repo: Path, database: Path, repeat: int) -> dict[str, float]:
    """Time the git-side operations once the database exists."""
    timings: dict[str, float] = {}

    timings["analyze"], _ = best_of(repeat, lambda: analyze(repo, database))

    def timeline():
        stored = load_timeline(repo, database)
        rows = stored.rows(20)
        return build_table(rows, 100)

    timings["timeline"], _ = best_of(repeat, timeline)

    def hotspots():
        lives = load_lifecycles(repo, database)
        return rank_hotspots(lives)

    timings["hotspots"], ranked = best_of(repeat, hotspots)

    # The command reads the whole history to find one file, because a life can
    # only be rebuilt by walking the commit graph.
    target = ranked[0].current_path

    def file_history():
        lives = load_lifecycles(repo, database)
        found = find_files(lives, target)
        return "\n".join(build_block(life) for life in found)

    timings["file history"], _ = best_of(repeat, file_history)

    # The same question asked of the name rather than of the file. It is a query
    # against commit_files and never rebuilds a life, which is why it is here:
    # the gap between the two lines is what a stored lifecycle would buy.
    timings["file history (by name)"], _ = best_of(
        repeat, lambda: load_commits_touching(repo, database, target)
    )

    return timings


def measure_ast(repo: Path, database: Path, repeat: int) -> dict:
    """Run the pass with its phases timed, then time the queries over its rows.

    The pass is run once, on a database with no AST rows in it: a second run
    reuses what the first one stored and would measure the cache rather than the
    work. The re-run is timed separately, because "what does it cost when nothing
    changed" is a question of its own.
    """
    phases = Phases()
    # ast.parse is what definitions_of calls, so wrapping the standard library's
    # name for the length of the run is what splits the parser from the walk.
    phases.wrap(ast_module, "parse", "parse")
    phases.wrap(ast_pass_module, "definitions_of", "definitions")
    phases.wrap(ast_pass_module, "_change_types", "compare")
    phases.wrap(ast_pass_module, "write_ast_batch", "insert")
    phases.wrap(objects_module.ObjectReader, "read", "read")

    try:
        start = time.perf_counter()
        result = run_ast_pass(repo, database)
        total = time.perf_counter() - start
    finally:
        phases.restore()

    timings = {
        "read": phases.times.get("read", 0.0),
        "parse": phases.times.get("parse", 0.0),
        # What the walk over the tree costs, over and above the parse it does
        # first: definitions_of includes ast.parse, so this is the difference.
        "extract": phases.times.get("definitions", 0.0)
        - phases.times.get("parse", 0.0),
        "compare": phases.times.get("compare", 0.0),
        "insert": phases.times.get("insert", 0.0),
        "total": total,
        "again": 0.0,
    }
    calls = dict(phases.calls)

    timings["again"], again = best_of(1, lambda: run_ast_pass(repo, database))

    connection = connect(database)
    try:
        target = connection.execute(
            "SELECT path FROM file_versions GROUP BY path"
            " ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()["path"]
    finally:
        connection.close()

    def structure():
        histories = load_histories(repo, database, target)
        history, version = select_version(histories)
        return build_version(history, version) + str(len(definitions_in(version)))

    timings["structure"], _ = best_of(repeat, structure)

    def structure_history():
        return build_history_block(load_histories(repo, database, target)[0])

    timings["structure --history"], _ = best_of(repeat, structure_history)

    return {"timings": timings, "calls": calls, "result": result, "again": again}


def shape(database: Path, result, db_after_analyze: int, again) -> dict:
    """What the AST layer stored, and what it cost to store it."""
    connection = connect(database)
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()[
                "n"
            ]
            for table in (
                "commits",
                "commit_files",
                "file_versions",
                "definition_versions",
            )
        }
        counts["changed"] = connection.execute(
            "SELECT COUNT(*) AS n FROM definition_versions"
            " WHERE change_type != 'unchanged'"
        ).fetchone()["n"]
        interpreters = connection.execute(
            "SELECT DISTINCT parsed_at_version FROM file_versions"
        ).fetchall()
    finally:
        connection.close()

    return {
        **counts,
        "interpreters": [row["parsed_at_version"] for row in interpreters],
        "db_analyze_mb": db_after_analyze / 1e6,
        "db_mb": database.stat().st_size / 1e6,
        "parsed": result.parsed,
        # From the second run, where every version is the one already worked
        # out: the first run reuses nothing, so its own count says nothing.
        "reused": again.reused,
        "failed": result.failed,
        "skipped": result.skipped,
    }


def run_scale(commits: int, files: int, touched: int, repeat: int, workdir: Path):
    repo = workdir / f"repo-{commits}"
    database = workdir / f"history-{commits}.db"

    start = time.perf_counter()
    if commit_count(repo) == commits:
        building = 0.0
        reused = True
    else:
        if repo.exists():
            shutil.rmtree(repo)
        build_history(repo, commits, files, touched)
        building = time.perf_counter() - start
        reused = False

    # A database left over from an earlier run would make the pass look free:
    # every version would be reused and no parsing would happen at all.
    if database.exists():
        database.unlink()

    timings = measure(repo, database, repeat)
    db_after_analyze = database.stat().st_size
    ast = measure_ast(repo, database, repeat)
    stored = shape(database, ast["result"], db_after_analyze, ast["again"])

    return {
        "commits": stored["commits"],
        "changes": stored["commit_files"],
        "build": building,
        "reused": reused,
        "repo_mb": _tree_size(repo) / 1e6,
        "stored": stored,
        "timings": timings,
        "ast": ast["timings"],
        "calls": ast["calls"],
    }


def _tree_size(repo: Path) -> int:
    total = 0
    for path in repo.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows))
        for column in range(len(headers))
    ]
    print(
        "  ".join(
            headers[column].rjust(widths[column]) for column in range(len(headers))
        )
    )
    for row in rows:
        print(
            "  ".join(row[column].rjust(widths[column]) for column in range(len(headers)))
        )


def report(results: list[dict]) -> None:
    print()
    print("stored — what the history became")
    _table(
        [
            "commits",
            "changes",
            "versions",
            "definitions",
            "changed",
            "ratio",
            "db git",
            "db +ast",
        ],
        [
            [
                str(row["commits"]),
                str(row["changes"]),
                str(row["stored"]["file_versions"]),
                str(row["stored"]["definition_versions"]),
                str(row["stored"]["changed"]),
                _ratio(row["stored"]),
                f"{row['stored']['db_analyze_mb']:.1f}M",
                f"{row['stored']['db_mb']:.1f}M",
            ]
            for row in results
        ],
    )
    print("  changed  the rows a change log would have stored, instead of all of them")
    print("  ratio    definitions stored per row a change log would have kept")

    print()
    print("parsed — what the pass made of them")
    _table(
        ["versions", "parsed", "failed", "skipped", "reused", "failure rate"],
        [
            [
                str(row["stored"]["file_versions"]),
                str(row["stored"]["parsed"]),
                str(row["stored"]["failed"]),
                str(row["stored"]["skipped"]),
                str(row["stored"]["reused"]),
                _rate(row["stored"]),
            ]
            for row in results
        ],
    )

    print()
    print("AST pass — seconds")
    operations = list(results[0]["ast"])
    width = max(len(name) for name in operations) + 1
    print(f"{'commits':>9} " + "".join(f"{name:>{width}}" for name in operations))
    for row in results:
        print(
            f"{row['commits']:>9} "
            + "".join(f"{row['ast'][name]:>{width - 1}.2f}s" for name in operations)
        )

    print()
    print("AST pass — how many times each phase ran")
    calls = list(results[0]["calls"])
    width = max(len(name) for name in calls) + 1
    print(f"{'commits':>9} " + "".join(f"{name:>{width}}" for name in calls))
    for row in results:
        print(
            f"{row['commits']:>9} "
            + "".join(f"{row['calls'][name]:>{width}d}" for name in calls)
        )
    print("  read      git cat-file --batch, one call per file version")
    print("  parse     ast.parse, wrapped for the length of the run")
    print("  extract   definitions_of on top of that parse: the walk over the tree")
    print("  compare   the pass's own comparison against the previous version")
    print("  insert    write_ast_batch, one call per batch of 500 versions")
    print("  total     the whole pass, wall clock")
    print("  again     a second pass over the same rows: nothing parsed, all reused")
    print("  The counts are the guard against a wrapper that stopped being called:")
    print("  a phase of 0.00s and 0 calls is a broken measurement, not a fast one.")

    print()
    print("reads — seconds")
    operations = list(results[0]["timings"])
    width = max(len(name) for name in operations) + 1
    print(f"{'commits':>9} " + "".join(f"{name:>{width}}" for name in operations))
    for row in results:
        print(
            f"{row['commits']:>9} "
            + "".join(f"{row['timings'][name]:>{width - 1}.2f}s" for name in operations)
        )

    print()
    print("dir    is the whole repository directory, .git included.")
    print("db git is the analysis database after analyze; db +ast adds the AST layer.")
    print("Times are the fastest of several runs, not an average: every source of")
    print("noise can only make a run slower than the work itself. The AST pass is the")
    print("exception: it runs once, because a second run would measure the cache.")
    print(f"parsed_at_version on every row is {interpreter_version()}.")


def _ratio(stored: dict) -> str:
    if not stored["changed"]:
        return "-"
    return f"{stored['definition_versions'] / stored['changed']:.2f}x"


def _rate(stored: dict) -> str:
    versions = stored["file_versions"]
    if not versions:
        return "-"
    return f"{100 * stored['failed'] / versions:.1f}%"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--commits",
        type=int,
        nargs="+",
        default=[20_000],
        help="How many commits to build, one repository per value.",
    )
    parser.add_argument("--files", type=int, default=DEFAULT_FILES)
    parser.add_argument("--touched", type=int, default=DEFAULT_TOUCHED)
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="Where to keep the generated repositories. Reused between runs.",
    )
    arguments = parser.parse_args(argv)

    workdir = arguments.workdir or Path(tempfile.mkdtemp(prefix="codearchaeology-bench-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"workdir {workdir}")

    results = []
    for commits in arguments.commits:
        print(f"building {commits} commits...", flush=True)
        row = run_scale(
            commits, arguments.files, arguments.touched, arguments.repeat, workdir
        )
        state = "reused" if row["reused"] else f"built in {row['build']:.1f}s"
        print(
            f"  {state}, {row['changes']} file changes,"
            f" {row['stored']['file_versions']} versions,"
            f" {row['stored']['definition_versions']} definitions",
            flush=True,
        )
        results.append(row)

    report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
