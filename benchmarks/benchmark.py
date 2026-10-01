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

**The pass is run three times, and the three runs are the point.** *cold* is a
database with no AST rows at all: every version is read, parsed and written.
*warm* is the same database immediately afterwards: every version is already
stored, so the run should cost a read and no work — and what it costs instead is
what the reuse path is worth measuring for. *partial* appends ``--partial``
commits to the repository and runs ``analyze`` and the pass again: the ideal is
that the new commits are the only versions parsed and the only rows written, and
the gap between that and what actually happens is the number this table exists to
show. ``rows written`` is counted by the wrapper around ``write_ast_batch``, so
the three runs can be compared on what they stored and not only on what they
cost.

**Memory is the process's own peak working set**, read from the operating system
rather than estimated from row counts: the pass holds a record for every
definition in the history, and whether a machine can run it is a question about
the process. The figure is the peak since the process started, so the scales are
run smallest first and the largest number is the one to plan with.

**What the AST layer costs to store** is measured rather than predicted: the row
counts, the database size before and after the pass, and the same rows counted
the way a change log would have stored them. A change log holds only what
changed; a snapshot holds every definition of every version, which is why the two
counts differ and why the difference depends on how much churn a history has
rather than on a constant.
"""

import argparse
import ast as ast_module
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from codearchaeology import analysis as analysis_module
from codearchaeology import ast_pass as ast_pass_module
from codearchaeology import objects as objects_module
from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cochange import analyze_cochange, load_cochange_commits
from codearchaeology.definition_history import load_histories
from codearchaeology.definitions import interpreter_version
from codearchaeology.file import build_block, find_files
from codearchaeology.hotspots import rank_hotspots
from codearchaeology.lifecycle import build_lifecycles, load_lifecycles
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
DEFAULT_PARTIAL = 100
DEFAULT_COCHANGE_SAMPLE = 5
FUNCTIONS_PER_FILE = 3
# One file in this many is written so that it cannot be parsed.
BROKEN_EVERY = 10

# How many commits to buffer before handing them to git. One write per commit
# would spend more time in the pipe than in git.
STREAM_CHUNK = 500

# The phases a run of the pass is reported in. Fixed rather than read off the
# first result, so a phase that stopped being measured shows up as a column of
# zeros instead of disappearing from the table.
PASS_PHASES = (
    ("read", "read"),
    ("versions read", "v.read"),
    ("definitions read", "d.read"),
    ("parse", "parse"),
    ("extract", "extract"),
    ("compare", "compare"),
    ("insert", "insert"),
    ("total", "total"),
)


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


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def _windows_peak() -> int | None:
        """The process's peak working set, through the kernel's own export.

        The prototypes are set explicitly, and that is not tidiness: without
        ``argtypes`` ctypes passes the process handle as a C int, the call
        returns zero, and the failure reads as "this machine has no memory
        information" rather than as the binding mistake it is.
        """
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = getattr(kernel32, "K32GetProcessMemoryInfo", None)
        if function is None:
            return None
        function.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        function.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = kernel32.GetCurrentProcess()
        if not function(handle, ctypes.byref(counters), counters.cb):
            return None
        return counters.PeakWorkingSetSize


def rss_peak_mb() -> float | None:
    """The process's peak working set, as the operating system reports it.

    Read rather than estimated: the pass holds a record for every definition in
    the history, and whether a machine can run it is a question about the
    process, not about the row counts. Windows reports it through the kernel and
    Unix through ``resource``; a platform with neither gets ``None`` rather than
    a guess, and the report prints a dash.

    The figure is the peak *since the process started*, so the scales are run
    smallest first and the largest number is the one to plan with.
    """
    if sys.platform == "win32":
        peak = _windows_peak()
        return None if peak is None else peak / 1e6

    try:
        import resource

        # Kilobytes on Linux, bytes on macOS. This project is measured on Linux
        # and on Windows, so the Linux reading is the one that has to be right.
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except (ImportError, AttributeError):
        return None


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


def _entries(start: int, count: int, files: int, touched: int, first_parent: str | None):
    """The ``fast-import`` commands for *count* commits numbered from *start*.

    *first_parent* is what the first of them is built on: ``None`` for a history
    this process is creating from nothing, and the sha a repository already ends
    at when commits are appended to it. The marks are local to a stream, which is
    why an appended stream starts its own numbering.
    """
    parent = first_parent
    mark = 0

    for number in range(start, start + count):
        mark += 1
        when = 1700000000 + number * 60
        message = f"Change {number}\n".encode()

        yield f"commit refs/heads/main\nmark :{mark}\n".encode()
        yield f"author Ada <ada@example.com> {when} +0000\n".encode()
        yield f"committer Ada <ada@example.com> {when} +0000\n".encode()
        yield f"data {len(message)}\n".encode() + message
        if parent is not None:
            yield f"from {parent}\n".encode()

        for offset in range(touched):
            index = (number + offset) % files
            body = _file_body(index, number)
            # A blob command may not appear inside a commit, so the contents go
            # inline on the M line.
            yield (
                f"M 100644 inline pkg{index // 20}/mod{index}.py\ndata {len(body)}\n".encode()
                + body
                + b"\n"
            )

        parent = f":{mark}"


def _import(repo: Path, entries) -> None:
    """Feed *entries* to one ``git fast-import``, in chunks as they are built.

    The stream is fed as it is built rather than assembled in memory: at two
    hundred thousand commits the whole thing is a few hundred megabytes, and
    holding it in a list only to hand it over is a way to measure the memory of
    the benchmark instead of the memory of the tool.
    """
    process = subprocess.Popen(
        ["git", "fast-import", "--quiet"],
        cwd=repo,
        stdin=subprocess.PIPE,
    )
    assert process.stdin is not None

    pending: list[bytes] = []
    commits = 0
    for chunk in entries:
        pending.append(chunk)
        if chunk.startswith(b"commit "):
            commits += 1
            if commits % STREAM_CHUNK == 0:
                process.stdin.write(b"".join(pending))
                pending.clear()
    if pending:
        process.stdin.write(b"".join(pending))

    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("git fast-import failed")

    # fast-import writes objects and refs, not the working tree.
    git(repo, "reset", "--hard", "HEAD")


def build_history(repo: Path, commits: int, files: int, touched: int) -> None:
    """Write *commits* commits into *repo* with ``git fast-import``."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "--initial-branch", "main")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "commit.gpgsign", "false")
    _import(repo, _entries(0, commits, files, touched, None))


def append_history(repo: Path, start: int, count: int, files: int, touched: int) -> None:
    """Add *count* commits to a repository that already has *start* of them.

    The partial-history measurement needs a repository that *grew*, not one that
    was rebuilt: the cost of a pass when a hundred commits arrive at the end of a
    hundred thousand is the question, and rebuilding the fixture would answer a
    different one. The new commits continue from the commit the branch is at,
    named by sha rather than by ref — fast-import refuses to build a branch on
    itself, which is what ``from refs/heads/main`` on the branch being written
    amounts to.
    """
    tip = git(repo, "rev-parse", "HEAD").strip()
    _import(repo, _entries(start, count, files, touched, tip))


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
        # Rows handed to a wrapper that counts them — ``write_ast_batch`` takes a
        # batch of versions, so the count is the size of what the pass wrote.
        self.rows: dict[str, int] = {}
        self._wrapped: list[tuple[object, str, object]] = []

    def wrap(self, owner, name: str, phase: str, count_rows: bool = False) -> None:
        original = getattr(owner, name)

        def timed(*arguments, **keywords):
            start = time.perf_counter()
            try:
                return original(*arguments, **keywords)
            finally:
                spent = time.perf_counter() - start
                self.times[phase] = self.times.get(phase, 0.0) + spent
                self.calls[phase] = self.calls.get(phase, 0) + 1
                if count_rows and len(arguments) > 1:
                    self.rows[phase] = self.rows.get(phase, 0) + len(arguments[1])

        setattr(owner, name, timed)
        self._wrapped.append((owner, name, original))

    def restore(self) -> None:
        for owner, name, original in self._wrapped:
            setattr(owner, name, original)
        self._wrapped.clear()


def timed_pass(repo: Path, database: Path) -> dict:
    """Run the pass once with its phases timed, and report what it cost.

    The wrappers cover both halves of the pass: the git reads (``read``), the
    three reads of what is already stored, the parse and the walk over the tree,
    the comparison, and the write. ``insert`` also counts the versions handed to
    it, so a run that wrote nothing can be told from one that wrote everything
    without reading the database afterwards.
    """
    phases = Phases()
    # ast.parse is what definitions_of calls, so wrapping the standard library's
    # name for the length of the run is what splits the parser from the walk.
    phases.wrap(ast_module, "parse", "parse")
    phases.wrap(ast_pass_module, "definitions_of", "definitions")
    phases.wrap(ast_pass_module, "_change_types", "compare")
    phases.wrap(ast_pass_module, "write_ast_batch", "insert", count_rows=True)
    phases.wrap(objects_module.ObjectReader, "read", "read")
    phases.wrap(ast_pass_module, "read_stored_commits", "history read")
    phases.wrap(ast_pass_module, "read_file_versions", "versions read")
    phases.wrap(ast_pass_module, "read_all_definition_versions", "definitions read")

    try:
        start = time.perf_counter()
        result = run_ast_pass(repo, database)
        total = time.perf_counter() - start
    finally:
        phases.restore()

    times = dict(phases.times)
    # What the walk over the tree costs, over and above the parse it does first:
    # definitions_of includes ast.parse, so this is the difference.
    times["extract"] = times.get("definitions", 0.0) - times.get("parse", 0.0)
    times["total"] = total
    return {
        "times": times,
        "calls": dict(phases.calls),
        "rows": dict(phases.rows),
        "result": result,
        "total": total,
    }


def measure(repo: Path, database: Path, repeat: int, cochange_sample: int) -> dict:
    """Time the git-side operations once the database exists."""
    timings: dict[str, float] = {}

    # The total is the fastest of several runs; the split inside it is the mean
    # over the same runs, because the wrapper accumulates and the write happens
    # once per run. They answer different questions: how long `analyze` takes,
    # and how much of that is writing rows rather than reading git.
    phases = Phases()
    phases.wrap(analysis_module, "write_commits", "insert commits")
    try:
        timings["analyze"], _ = best_of(repeat, lambda: analyze(repo, database))
        calls = phases.calls.get("insert commits", 0)
        timings["analyze: insert"] = (
            phases.times.get("insert commits", 0.0) / calls if calls else 0.0
        )
    finally:
        phases.restore()

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

    # Co-change is the read-side question with the largest answer, and it is
    # asked of the busiest file: the model is rebuilt from the whole history and
    # the query is answered from the busiest file's sample, so this is the worst
    # case a user can hit with one command.
    def cochange_command():
        stored = load_cochange_commits(repo, database)
        return analyze_cochange(stored, target)

    timings["cochange"], _ = best_of(repeat, cochange_command)

    # Asking about several files in one process. The model is rebuilt inside
    # every call, because that is what the public function does, so this is the
    # price of a repository-wide question rather than of one command. Measured
    # once: it is a secondary figure and the runs would add minutes at the top
    # scale.
    stored = load_cochange_commits(repo, database)
    lives = build_lifecycles(stored)
    wanted = sorted({life.current_path for life in lives})[:cochange_sample]
    start = time.perf_counter()
    for name in wanted:
        analyze_cochange(stored, name)
    timings[f"cochange x{len(wanted)}"] = time.perf_counter() - start

    return timings


def measure_ast(
    repo: Path, database: Path, repeat: int, partial: int, files: int, touched: int
) -> dict:
    """Run the pass three times, then time the queries over what it stored.

    *cold* is a database with no AST rows in it: what the work costs. *warm* is
    the same database immediately afterwards, where every version is already
    stored: the reuse path, and the question is what it still pays. *grown* is
    the case a user is in every time they pull — a few commits appended to a long
    history — and the ideal is that the new versions are the only ones parsed and
    the only rows written.

    The queries over the rows are timed after all three, because reading what the
    pass wrote is a different question from writing it.
    """
    cold = timed_pass(repo, database)
    warm = timed_pass(repo, database)

    start = time.perf_counter()
    append_history(repo, commit_count(repo), partial, files, touched)
    appended = time.perf_counter() - start

    start = time.perf_counter()
    analyze(repo, database)
    reanalyzed = time.perf_counter() - start

    grown = timed_pass(repo, database)

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

    def structure_history():
        return build_history_block(load_histories(repo, database, target)[0])

    timings = {}
    timings["structure"], _ = best_of(repeat, structure)
    timings["structure --history"], _ = best_of(repeat, structure_history)

    return {
        "cold": cold,
        "warm": warm,
        "grown": grown,
        "appended": appended,
        "reanalyzed": reanalyzed,
        "partial": partial,
        "target": target,
        "timings": timings,
    }


def shape(database: Path, db_after_analyze: int) -> dict:
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
    finally:
        connection.close()

    return {
        **counts,
        "db_analyze_mb": db_after_analyze / 1e6,
        "db_mb": database.stat().st_size / 1e6,
    }


def _remove_tree(path: Path) -> None:
    """Delete a directory tree, including the files git left read-only.

    git marks the packs it writes read-only, and ``shutil.rmtree`` stops at the
    first of them on Windows with "access denied" on a file nobody has open —
    which reads as a permissions problem rather than as the attribute it is. The
    attribute is cleared on every entry first, because both the pack and its
    ``.idx`` carry it. A fixture that has to be rebuilt is the only thing that
    needs this, which is why it went unnoticed until the grown run left the
    repositories one commit count away from being reused.
    """
    for root, directories, files in os.walk(path):
        for name in directories + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path)


def run_scale(
    commits: int,
    files: int,
    touched: int,
    repeat: int,
    partial: int,
    cochange_sample: int,
    workdir: Path,
):
    repo = workdir / f"repo-{commits}"
    database = workdir / f"history-{commits}.db"

    start = time.perf_counter()
    if commit_count(repo) == commits:
        building = 0.0
        reused = True
    else:
        # A repository left over from an earlier run has the partial commits
        # appended to it, so the count no longer matches and the fixture is
        # rebuilt: the measurement has to start from exactly *commits*.
        if repo.exists():
            _remove_tree(repo)
        build_history(repo, commits, files, touched)
        building = time.perf_counter() - start
        reused = False

    # A database left over from an earlier run would make the pass look free:
    # every version would be reused and no parsing would happen at all.
    if database.exists():
        database.unlink()

    measured = measure(repo, database, repeat, cochange_sample)
    db_after_analyze = database.stat().st_size
    ast = measure_ast(repo, database, repeat, partial, files, touched)
    stored = shape(database, db_after_analyze)

    return {
        "commits": stored["commits"],
        "changes": stored["commit_files"],
        "build": building,
        "reused": reused,
        "repo_mb": _tree_size(repo) / 1e6,
        "stored": stored,
        # One table for every read-side question: the ones against the git facts
        # and the two against the AST layer, which the pass has to have run for.
        "timings": {**measured, **ast["timings"]},
        "ast": ast,
        "peak_mb": rss_peak_mb(),
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
            "peak MB",
        ],
        [
            [
                f"{row['commits']:,}",
                f"{row['changes']:,}",
                f"{row['stored']['file_versions']:,}",
                f"{row['stored']['definition_versions']:,}",
                f"{row['stored']['changed']:,}",
                _ratio(row["stored"]),
                f"{row['stored']['db_analyze_mb']:.1f}M",
                f"{row['stored']['db_mb']:.1f}M",
                "-" if row["peak_mb"] is None else f"{row['peak_mb']:.0f}",
            ]
            for row in results
        ],
    )
    print("  changed  the rows a change log would have stored, instead of all of them")
    print("  ratio    definitions stored per row a change log would have kept")
    print("  peak MB  the process's peak working set, since it started: the scales")
    print("           run smallest first, so the largest figure is the one to plan with")
    print("  The rows are counted after the grown run, so the history here is")
    print("  --commits plus --partial commits: the state the database ended in.")

    print()
    print("AST pass — seconds, one row per run")
    _table(
        ["commits", "run", *[short for _, short in PASS_PHASES]],
        [
            [
                f"{row['commits']:,}",
                run,
                *[
                    f"{row['ast'][run]['times'].get(name, 0.0):.2f}"
                    for name, _ in PASS_PHASES
                ],
            ]
            for row in results
            for run in ("cold", "warm", "grown")
        ],
    )
    print("  read      git cat-file --batch, one call per file version")
    print("  v.read    read_file_versions: the stored version rows, one query")
    print("  d.read    read_all_definition_versions: the stored definitions")
    print("  parse     ast.parse, wrapped for the length of the run")
    print("  extract   definitions_of on top of that parse: the walk over the tree")
    print("  compare   the pass's own comparison against the previous version")
    print("  insert    write_ast_batch, one call per batch of 500 versions")
    print("  total     the whole pass, wall clock")
    print("  cold      a database with no AST rows in it: everything parsed")
    print("  warm      the same database again: every version reused")
    print("  grown     --partial commits appended, analyze run, then the pass")

    print()
    print("AST pass — what each run made of the history")
    _table(
        ["commits", "run", "versions", "parsed", "reused", "written", "failed", "append s", "analyze s"],
        [
            [
                f"{row['commits']:,}",
                run,
                f"{row['ast'][run]['result'].file_versions:,}",
                f"{row['ast'][run]['result'].parsed:,}",
                f"{row['ast'][run]['result'].reused:,}",
                f"{row['ast'][run]['rows'].get('insert', 0):,}",
                f"{row['ast'][run]['result'].failed:,}",
                f"{row['ast']['appended']:.1f}" if run == "grown" else "-",
                f"{row['ast']['reanalyzed']:.1f}" if run == "grown" else "-",
            ]
            for row in results
            for run in ("cold", "warm", "grown")
        ],
    )
    print("  versions  the file versions this run walked")
    print("  written   the versions handed to write_ast_batch: the rows it stored")
    print("  append s  how long git took to add the partial commits")
    print("  analyze s the rescan of the longer history, before the pass")

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
    print("  analyze: insert is the mean of write_commits over the same runs, the")
    print("  rest of analyze is git extraction; cochange is one command against the")
    print("  busiest file, and cochange xN asks N files in one process, rebuilding")
    print("  the identity model per call because that is what the function does.")

    print()
    print("dir    is the whole repository directory, .git included.")
    print("db git is the analysis database after analyze; db +ast adds the AST layer.")
    print("Times are the fastest of several runs, not an average: every source of")
    print("noise can only make a run slower than the work itself. The three pass runs")
    print("are the exception: each happens once, in that order.")
    print(f"parsed_at_version on every row is {interpreter_version()}.")


def _ratio(stored: dict) -> str:
    if not stored["changed"]:
        return "-"
    return f"{stored['definition_versions'] / stored['changed']:.2f}x"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--commits",
        type=int,
        nargs="+",
        default=[20_000],
        help="How many commits to build, one repository per value."
        " Give them smallest first: the memory figure is the process's peak.",
    )
    parser.add_argument("--files", type=int, default=DEFAULT_FILES)
    parser.add_argument("--touched", type=int, default=DEFAULT_TOUCHED)
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument(
        "--partial",
        type=int,
        default=DEFAULT_PARTIAL,
        help="How many commits to append for the grown run.",
    )
    parser.add_argument(
        "--cochange-sample",
        type=int,
        default=DEFAULT_COCHANGE_SAMPLE,
        help="How many files the co-change sample asks about, in one process.",
    )
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
            commits,
            arguments.files,
            arguments.touched,
            arguments.repeat,
            arguments.partial,
            arguments.cochange_sample,
            workdir,
        )
        state = "reused" if row["reused"] else f"built in {row['build']:.1f}s"
        print(
            f"  {state}, {row['changes']} file changes,"
            f" {row['stored']['file_versions']} versions,"
            f" {row['stored']['definition_versions']} definitions",
            flush=True,
        )
        print(
            f"  pass cold {row['ast']['cold']['total']:.1f}s,"
            f" warm {row['ast']['warm']['total']:.1f}s"
            f" (wrote {row['ast']['warm']['rows'].get('insert', 0)} versions),"
            f" grown {row['ast']['grown']['total']:.1f}s"
            f" (parsed {row['ast']['grown']['result'].parsed},"
            f" wrote {row['ast']['grown']['rows'].get('insert', 0)})",
            flush=True,
        )
        results.append(row)

    report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
