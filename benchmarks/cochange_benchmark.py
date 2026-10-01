"""Measure the co-change analysis as the repository grows.

Run it by hand, not in CI::

    uv run python benchmarks/cochange_benchmark.py
    uv run python benchmarks/cochange_benchmark.py --scales 500 10000
    uv run python benchmarks/cochange_benchmark.py --workdir D:/tmp/cochange-bench

It answers one question — what the analysis costs, and where the cost goes —
and it is deliberately not a test. There is no pass or fail here: a wall-clock
bound would fail on a busy CI machine and teach people to re-run it.

**What is measured, and why each number is on the table.**

* **commits and files** are the size of the input.
* **file changes** is the size of ``commit_files``, which is the row count the
  analysis actually reads.
* **pairs generated** is the number of pairs the *naive* algorithm would have
  to count — the sum of ``n(n-1)/2`` over every commit. It is printed beside
  the run because it is the number the per-file algorithm avoids materialising,
  and the gap between it and the next column is the whole point of the design.
* **distinct pairs** is the union of every file's answer over a sample of the
  files. It is a *lower bound* on the true pair count, because only
  ``--sample`` files are asked; the column says how many were.
* **walk / analysis / peak** separate the three costs: building the identity
  model, the per-file queries, and the memory the whole thing holds. The walk
  is paid once per run; the analysis is paid per file.
* **git analyze** is ``analyze``'s own time on the same repository, so the
  co-change cost can be read against the cost of filling the database at all.

The histories are generated here rather than taken from the test suite, the way
the other benchmark does it: a file is created by the first commit that touches
it and edited by later ones, with no renames and no deletions, so the commit
sizes are uniform and the curve is about scale rather than about rules. Read the
shape of the curve, not the absolute values.
"""

import argparse
import gc
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from codearchaeology.analysis import analyze
from codearchaeology.cochange import (
    analyze_cochange,
    load_cochange_commits,
)
from codearchaeology.lifecycle import build_lifecycles
from codearchaeology.storage import connect

DEFAULT_SCALES = (500, 10_000, 50_000)
DEFAULT_FILES = 200
DEFAULT_TOUCHED = 5
DEFAULT_SAMPLE = 50
# How many functions one version of one file holds. Fixed, so the AST layer's
# row counts are arithmetic rather than a property of the edit.
FUNCTIONS_PER_FILE = 3


def _file_body(index: int, edit: int) -> bytes:
    """One revision of one file: valid Python holding definitions.

    It has to hold *definitions*, not just an assignment: this benchmark's
    histories are also what the AST pass is measured against, and a file with
    nothing to find gives the pass nothing to store — the first version of this
    generator wrote ``# file N`` and ``value = M``, and a 50,000-commit run of it
    produced 250,195 file versions and zero definition rows, which made every
    timing of the AST layer measure an empty path.
    """
    body = f"# module {index}\n"
    for number in range(FUNCTIONS_PER_FILE):
        operator = "-" if number == edit % FUNCTIONS_PER_FILE else "+"
        body += f"def function_{number}(value):\n"
        body += f"    return value {operator} {number}\n\n\n"
    body += f"# edit {edit}\n"
    return body.encode()


def _stream(commits: int, files: int, touched: int) -> bytes:
    """A ``git fast-import`` stream that builds the whole history at once.

    The first commit creates every file and each later one touches *touched* of
    them, moving through the file list in order. That makes the commit sizes
    exactly uniform — one birth commit and a run of edits — so the pair count is
    a property of the shape rather than of a random draw.
    """
    out: list[bytes] = []
    parent = None
    mark = 0

    for number in range(commits):
        mark += 1
        when = 1_700_000_000 + number * 60
        message = f"change {number}\n".encode()
        out.append(f"commit refs/heads/main\nmark :{mark}\n".encode())
        out.append(f"author A <a@e> {when} +0000\ncommitter A <a@e> {when} +0000\n".encode())
        out.append(f"data {len(message)}\n".encode() + message)
        if parent is not None:
            out.append(f"from :{parent}\n".encode())

        if number == 0:
            picks = list(range(files))
        else:
            start = (number * touched) % files
            picks = sorted({(start + offset) % files for offset in range(touched)})

        for index in picks:
            body = _file_body(index, number)
            out.append(
                f"M 100644 inline pkg{index // 10}/mod{index}.py\ndata {len(body)}\n".encode()
                + body
                + b"\n"
            )
        parent = mark

    return b"".join(out)


def build_repository(repository: Path, commits: int, files: int, touched: int) -> None:
    """Build the history at *repository* with one fast-import call.

    Named ``build_repository`` rather than ``build_history`` on purpose: the
    structure view already owns that name, and trap 23 is the record of what
    happens when a benchmark gives its own word to the tool's function.
    """
    repository.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--initial-branch", "main", "-q", str(repository)],
        check=True,
        capture_output=True,
    )
    completed = subprocess.run(
        ["git", "fast-import", "--quiet"],
        cwd=repository,
        input=_stream(commits, files, touched),
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git fast-import failed: {completed.stderr.decode()}")
    subprocess.run(
        ["git", "reset", "--hard", "-q", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def naive_pairs(connection) -> int:
    """The pairs an all-pairs algorithm would count, from the stored rows.

    Not run here — it is arithmetic over the commit sizes, and the point of the
    number is that the implementation does not have to do it.
    """
    total = 0
    for row in connection.execute(
        "SELECT count(*) AS n FROM commit_files GROUP BY commit_sha"
    ):
        total += row["n"] * (row["n"] - 1) // 2
    return total


def file_count(connection) -> int:
    """Distinct paths the history ever named, renames included."""
    return connection.execute(
        "SELECT count(*) FROM ("
        " SELECT path FROM commit_files"
        " UNION SELECT old_path FROM commit_files WHERE old_path IS NOT NULL)"
    ).fetchone()[0]


def measure(commits: int, files: int, touched: int, sample: int, workdir: Path):
    """Build one repository, analyze it, and time the co-change pass."""
    repository = workdir / f"repo-{commits}"
    database = workdir / f"repo-{commits}.db"

    built = 0.0
    if not repository.exists():
        start = time.perf_counter()
        build_repository(repository, commits=commits, files=files, touched=touched)
        built = time.perf_counter() - start

    start = time.perf_counter()
    analyze(repository, database)
    analyzed = time.perf_counter() - start

    connection = connect(database)
    try:
        rows = connection.execute("SELECT count(*) FROM commit_files").fetchone()[0]
        pairs = naive_pairs(connection)
        paths = file_count(connection)
    finally:
        connection.close()

    # The one-call cost: load, build the identity model, answer for one file.
    # This is what a single `archaeology cochange <file>` pays.
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    stored = load_cochange_commits(repository, database)
    lives = build_lifecycles(stored)
    wanted = sorted({life.current_path for life in lives})[:sample]
    reports = analyze_cochange(stored, wanted[0])
    one_call = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # The sample cost: the same model, every file asked about in turn. The
    # model is rebuilt per call, because that is what the public function does;
    # the column is the price of asking about a whole repository, not of one
    # command.
    start = time.perf_counter()
    pairs_seen: set[tuple[str, str]] = set()
    for name in wanted:
        for report in analyze_cochange(stored, name):
            for row in report.co_changes:
                pairs_seen.add((report.path, row.path))
    sampled = time.perf_counter() - start

    return {
        "commits": commits,
        "files": paths,
        "changes": rows,
        "naive_pairs": pairs,
        "distinct_pairs": len(pairs_seen),
        "sample": len(wanted),
        "build": built,
        "analyze": analyzed,
        "one_call": one_call,
        "sampled": sampled,
        "per_query": sampled / len(wanted) if wanted else 0.0,
        "peak": peak / 1e6,
        "lives": len(lives),
        "first": wanted[0],
        "first_pairs": len(reports[0].co_changes) if reports else 0,
    }


def report(results: list[dict]) -> None:
    print()
    print("the history")
    _table(
        ["commits", "files", "changes", "lives"],
        [
            [
                f"{row['commits']:,}",
                f"{row['files']:,}",
                f"{row['changes']:,}",
                f"{row['lives']:,}",
            ]
            for row in results
        ],
    )

    print()
    print("pairs — what an all-pairs algorithm would count, and what the per-file one saw")
    _table(
        ["commits", "naive pairs", "distinct pairs", "files asked"],
        [
            [
                f"{row['commits']:,}",
                f"{row['naive_pairs']:,}",
                f"{row['distinct_pairs']:,}",
                f"{row['sample']:,}",
            ]
            for row in results
        ],
    )

    print()
    print("time, seconds")
    _table(
        ["commits", "git analyze", "one command", "per query", "sample total", "peak MB"],
        [
            [
                f"{row['commits']:,}",
                f"{row['analyze']:.2f}",
                f"{row['one_call']:.3f}",
                f"{row['per_query']:.3f}",
                f"{row['sampled']:.1f}",
                f"{row['peak']:.0f}",
            ]
            for row in results
        ],
    )

    print()
    print("  one command is what a single `archaeology cochange <file>` pays:")
    print("  load the commits, build the identity model, answer for one file.")
    print("  per query and sample total rebuild that model for every file, because")
    print("  that is what the public function does — they are the price of asking")
    print("  about a whole repository, not of one command. distinct pairs is a")
    print("  lower bound: only the sampled files were asked.")


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scales",
        type=int,
        nargs="+",
        default=list(DEFAULT_SCALES),
        help="How many commits to build, one repository per value.",
    )
    parser.add_argument("--files", type=int, default=DEFAULT_FILES)
    parser.add_argument("--touched", type=int, default=DEFAULT_TOUCHED)
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE,
        help="How many files to ask about when counting distinct pairs.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="Where to keep the generated repositories. Reused between runs.",
    )
    arguments = parser.parse_args(argv)

    workdir = arguments.workdir or Path(tempfile.mkdtemp(prefix="codearchaeology-cochange-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"workdir {workdir}")

    results = []
    for commits in arguments.scales:
        print(f"building {commits} commits...", flush=True)
        row = measure(
            commits, arguments.files, arguments.touched, arguments.sample, workdir
        )
        state = "reused" if row["build"] == 0.0 else f"built in {row['build']:.1f}s"
        print(
            f"  {state}, {row['changes']:,} file changes,"
            f" {row['naive_pairs']:,} naive pairs",
            flush=True,
        )
        results.append(row)

    report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
