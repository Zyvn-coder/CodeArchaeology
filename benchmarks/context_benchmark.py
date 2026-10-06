"""Measure how large the explanation context gets, and which section carries it.

Run it by hand, not in CI::

    uv run python benchmarks/context_benchmark.py
    uv run python benchmarks/context_benchmark.py --only deep-diff

The question is the one Unit 12 asks — a commit that is large in one of the ways
a commit can be large — and the answer has to be per section, because the total
alone says a bundle is too big without saying what made it so. Six shapes, each
isolating one axis:

* ``typical`` — five files, a couple of hunks each, inside a long history, so
  the two context sections (co-change and the earlier commits) are full. This is
  the row the other five are read against.
* ``many-files`` — one commit touching two hundred files.
* ``bulk-commit`` — one commit touching a thousand files, the shape the
  co-change layer already refuses to analyse.
* ``deep-diff`` — one file rewritten in five hundred separate places.
* ``many-definitions`` — one file with three hundred functions changed.
* ``everything`` — two hundred files, twenty hunks and ten definitions each, as
  a plausible worst case that is not a synthetic single-axis extreme.

**The fixture's history is generated, and that matters to the numbers.** The
files are real Python so the AST layer has something to store, the target commit
changes separate lines so each change is its own hunk under ``--unified=0``, and
every earlier commit touches a rotating subset so the co-change statistics are
built from a history rather than from one commit. Read the shape, not the
absolute values: a repository with different churn will produce different
numbers, and the point of the table is which section grows and how.

``bytes`` is the section's own JSON as it appears in the bundle, and ``tokens``
is the tool's own estimator (four characters per token, stated as the estimate it
is) applied to those bytes. The last row is the whole bundle, which is what a
provider would be asked to send.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.context import build_context, build_json, build_object
from codearchaeology.provider import estimate_tokens
from codearchaeology.selection import build_view

AUTHOR_NAME = "Fixture"
AUTHOR_EMAIL = "fixture@example.com"

# The order the bundle's own builder writes them in, so a table's rows line up
# with the object a reader would print.
SECTIONS = (
    "commit",
    "file_changes",
    "lifecycle",
    "ast_changes",
    "cochange",
    "history",
    "absences",
    "bounds",
)


class Shape:
    """What the target commit looks like, and what history it sits in."""

    def __init__(self, name, files, hunks, definitions, history=200, touched=None):
        self.name = name
        self.files = files
        self.hunks = hunks
        self.definitions = definitions
        self.history = history
        # Enough files per commit that each file is touched about ten times
        # before the target, which is what fills the earlier-commit window and
        # gives the co-change layer a history to count. A fixture whose earlier
        # commits rewrote identical bytes reports an empty window and a co-change
        # section of nothing — and both numbers are part of the answer, so the
        # generator is written to produce them.
        self.touched = touched if touched is not None else max(5, files // 20)


SHAPES = (
    Shape("typical", files=5, hunks=2, definitions=2),
    Shape("many-files", files=200, hunks=1, definitions=1),
    Shape("bulk-commit", files=1000, hunks=1, definitions=1),
    Shape("deep-diff", files=1, hunks=500, definitions=0),
    Shape("many-definitions", files=1, hunks=0, definitions=300),
    Shape("everything", files=200, hunks=20, definitions=10),
)


def _body(index: int, hunks: int, definitions: int, edit: int) -> bytes:
    """One revision of one file: real Python, edited in *edit* separate places.

    The module-level lines are spaced three apart so that changing several of
    them produces several hunks under ``--unified=0`` rather than one contiguous
    one — a diff's *shape* is what this fixture is for, and a run of adjacent
    changes would measure the same thing as a single change of a longer line.
    """
    lines = [f"# module {index}"]
    for number in range(hunks * 3):
        value = edit if number % 3 == 0 and number // 3 < hunks else 0
        lines.append(f"value_{number} = {value}")

    for number in range(definitions):
        value = edit if number < definitions else 0
        lines.append(f"def function_{number}(value):")
        lines.append(f"    return value + {value}")

    return ("\n".join(lines) + "\n").encode()


def _stream(shape: Shape) -> bytes:
    """A ``git fast-import`` stream: a history, then the target commit.

    Every commit before the target touches a rotating subset of the files, which
    is what gives the co-change layer something to count. The target commit is
    the last one and touches all of them.
    """
    out: list[bytes] = []
    parent = None
    mark = 0
    commits = shape.history + 1

    for number in range(commits):
        mark += 1
        when = 1700000000 + number * 60
        message = (
            f"Target commit\n".encode()
            if number == commits - 1
            else f"Change {number}\n".encode()
        )
        out.append(f"commit refs/heads/main\nmark :{mark}\n".encode())
        out.append(f"author {AUTHOR_NAME} <{AUTHOR_EMAIL}> {when} +0000\n".encode())
        out.append(f"committer {AUTHOR_NAME} <{AUTHOR_EMAIL}> {when} +0000\n".encode())
        out.append(f"data {len(message)}\n".encode() + message)
        if parent is not None:
            out.append(f"from :{parent}\n".encode())

        target = number == commits - 1
        indexes = (
            range(shape.files)
            if target
            else [(number + offset) % shape.files for offset in range(shape.touched)]
        )
        for index in indexes:
            # Every commit writes different bytes, including the earlier ones:
            # a history whose commits rewrote what was already there is a history
            # git records nothing for, and the earlier-commit window and the
            # co-change counts would both come out empty.
            body = _body(index, shape.hunks, shape.definitions, number + 1)
            out.append(
                f"M 100644 inline pkg{index // 10}/mod{index}.py\ndata {len(body)}\n".encode()
                + body
                + b"\n"
            )

        parent = mark

    return b"".join(out)


def _repository(shape: Shape, destination: Path) -> Path:
    repository = destination / "repo"
    repository.mkdir(parents=True)
    for arguments in (
        ("init", "--initial-branch", "main"),
        ("config", "core.autocrlf", "false"),
        ("config", "commit.gpgsign", "false"),
    ):
        subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)

    completed = subprocess.run(
        ["git", "fast-import", "--quiet"],
        cwd=repository,
        input=_stream(shape),
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git fast-import failed: {completed.stderr.decode()}")
    return repository


def _sizes(bundle: dict) -> dict[str, tuple[int, int]]:
    """Each section's bytes and estimated tokens, as the bundle writes it."""
    sizes = {}
    for name in SECTIONS:
        text = json.dumps(bundle[name], indent=2, ensure_ascii=False)
        sizes[name] = (len(text.encode("utf-8")), estimate_tokens(text))
    return sizes


def measure(shape: Shape, root: Path):
    repository = _repository(shape, root)
    database = root / "history.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    context = build_context(repository, database, commit)
    bundle = build_object(context)
    return context, bundle, _sizes(bundle)


def report(shape: Shape, context, bundle: dict, sizes: dict) -> None:
    whole = build_json(context)
    total = len(whole.encode("utf-8"))
    view = build_view(context)
    shown = view.json
    print(f"\n=== {shape.name} ===")
    print(
        f"files touched {len(bundle['file_changes'])}   "
        f"ast changes {len(bundle['ast_changes'])}   "
        f"ranges {sum(len(change['ranges']) for change in bundle['file_changes'])}   "
        f"whole bundle {total:,} bytes / ~{estimate_tokens(whole):,} tokens"
    )
    print(
        f"  model's view      {len(shown.encode('utf-8')):>12,}"
        f"{estimate_tokens(shown):>12,}"
        f"   {len(shown.encode('utf-8')) / total:>6.1%} of the bundle"
    )
    if view.selection is not None:
        for name, entry in view.selection.items():
            if name == "note" or not entry.get("omitted"):
                continue
            print(f"    {name}: {entry}")
    print(f"  {'section':<16}{'bytes':>12}{'tokens':>12}{'share':>8}")
    for name in SECTIONS:
        size, tokens = sizes[name]
        print(f"  {name:<16}{size:>12,}{tokens:>12,}{size / total:>7.1%}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", help="run only these shapes")
    parser.add_argument("--keep", action="store_true", help="keep the generated repositories")
    arguments = parser.parse_args(argv)

    shapes = [s for s in SHAPES if not arguments.only or s.name in arguments.only]
    root = Path(tempfile.mkdtemp(prefix="context-benchmark-"))
    try:
        for shape in shapes:
            where = root / shape.name
            where.mkdir()
            context, bundle, sizes = measure(shape, where)
            report(shape, context, bundle, sizes)
    finally:
        if arguments.keep:
            print(f"\nrepositories kept in {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
