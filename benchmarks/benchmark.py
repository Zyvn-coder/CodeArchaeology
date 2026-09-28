"""Measure the four operations the tool is built around, at whatever scale.

Run it by hand, not in CI::

    uv run python benchmarks/benchmark.py --commits 20000
    uv run python benchmarks/benchmark.py --commits 20000 100000 200000

It answers one question — where does the time go as a repository grows — and it
is deliberately not a test. There is no pass or fail here: a wall-clock bound
would fail on a busy CI machine and teach people to re-run it, and the number
that matters is the shape of the curve, not any single value.

**What it measures, and what it does not.** The history is generated, so its
shape is fixed and stated: files are created and then edited in place, five per
commit, one line changed each time. A real repository has larger diffs, more
renames and a longer tail of file sizes, and all three move these numbers. Read
the curve, not the absolute values.

**Why four operations and not one.** ``hotspots`` and ``file history`` both need
every file's life, and a life can only be rebuilt by walking the whole commit
graph, so the two are expected to cost about the same as each other and rather
more than the timeline. If that ever stops being true, something changed that
nobody decided.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from codearchaeology.analysis import analyze
from codearchaeology.file import build_block, find_files
from codearchaeology.hotspots import rank_hotspots
from codearchaeology.lifecycle import load_lifecycles
from codearchaeology.relationships import load_commits_touching
from codearchaeology.storage import connect
from codearchaeology.timeline import build_table, load_timeline

DEFAULT_FILES = 200
DEFAULT_TOUCHED = 5
DEFAULT_REPEAT = 3
LINES_PER_FILE = 22

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
    """One revision of one file: a fixed size, changed by one line."""
    body = f"# module {index}\n"
    body += "".join(f"line {number}\n" for number in range(LINES_PER_FILE - 2))
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


def measure(repo: Path, database: Path, repeat: int) -> dict[str, float]:
    """Time each operation once the database exists."""
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


def sizes(repo: Path, database: Path) -> tuple[int, int]:
    connection = connect(database)
    try:
        commits = connection.execute("SELECT COUNT(*) AS n FROM commits").fetchone()["n"]
        changes = connection.execute(
            "SELECT COUNT(*) AS n FROM commit_files"
        ).fetchone()["n"]
    finally:
        connection.close()
    return commits, changes


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

    timings = measure(repo, database, repeat)
    stored_commits, stored_changes = sizes(repo, database)

    return {
        "commits": stored_commits,
        "changes": stored_changes,
        "build": building,
        "reused": reused,
        "repo_mb": _tree_size(repo) / 1e6,
        "db_mb": database.stat().st_size / 1e6,
        "timings": timings,
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


def report(results: list[dict]) -> None:
    operations = list(results[0]["timings"])
    width = max(len(name) for name in operations) + 1

    print()
    print(
        f"{'commits':>9} {'changes':>9} {'dir':>7} {'db':>7}  "
        + "".join(f"{name:>{width}}" for name in operations)
    )
    for row in results:
        print(
            f"{row['commits']:>9} {row['changes']:>9} "
            f"{row['repo_mb']:>6.1f}M {row['db_mb']:>6.1f}M  "
            + "".join(f"{row['timings'][name]:>{width - 1}.2f}s" for name in operations)
        )

    print()
    print("dir   is the whole repository directory, .git included.")
    print("db    is the analysis database.")
    print("Times are the fastest of several runs, not an average: every source of")
    print("noise can only make a run slower than the work itself.")
    print()
    print("The history is generated, so its shape is fixed: files are created and")
    print("then edited in place, one line at a time. Real repositories have larger")
    print("diffs, more renames and a longer tail of file sizes, and all three move")
    print("these numbers. Read the curve, not the absolute values.")


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
        print(f"  {state}, {row['changes']} file changes", flush=True)
        results.append(row)

    report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
