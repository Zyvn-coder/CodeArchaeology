"""Build a small, deterministic Git repository for the test suite.

The history it creates::

    A  Initial commit
    B  Move app module into the core package   (the file is renamed)
    |\
    | C  Add caching                           (branch feature/caching)
    D |  Fix login bug                         (main)
    |/
    M  Merge branch 'feature/caching'
    F  Add logo and unicode module, drop legacy helper

Six commits, one rename, one deletion and one merge commit. Author, committer
and timestamps are all fixed, so the commit hashes come out identical on every
run.

The module is also runnable, which is handy when you want a repository to poke
at by hand::

    uv run python tests/sample_repo.py D:/tmp/playground
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

AUTHOR_NAME = "Ada Lovelace"
AUTHOR_EMAIL = "ada@example.com"

INITIAL_DATE = "2024-03-01T09:00:00+00:00"
RENAME_DATE = "2024-03-03T09:00:00+00:00"
CACHE_DATE = "2024-03-05T09:00:00+00:00"
FIX_DATE = "2024-03-06T09:00:00+00:00"
MERGE_DATE = "2024-03-08T09:00:00+00:00"
FINAL_DATE = "2024-03-10T09:00:00+00:00"
LATER_DATE = "2024-03-12T09:00:00+00:00"

# One timestamp shared by every commit of the same-second fixture. A rebase, a
# scripted import and a converted history all produce timestamps like this.
SAME_SECOND = "2024-06-01T12:00:00+00:00"

README = """\
# sample project

A tiny repository used by the CodeArchaeology test suite.
"""

APP_BEFORE = '''\
"""Minimal application entry point."""


def login(username, password):
    return username == "admin" and password == "secret"


def main():
    print(login("admin", "secret"))


if __name__ == "__main__":
    main()
'''

APP_AFTER = '''\
"""Minimal application entry point."""


def login(username, password):
    if not username or not password:
        return False
    return username == "admin" and password == "secret"


def logout(username):
    print(f"{username} logged out")


def main():
    print(login("admin", "secret"))


if __name__ == "__main__":
    main()
'''

CACHE = '''\
"""A tiny in-memory cache."""


class Cache:
    def __init__(self):
        self._values = {}

    def get(self, key):
        return self._values.get(key)

    def set(self, key, value):
        self._values[key] = value
'''

LEGACY = '''\
"""Helpers kept around for the old call sites."""


def parse(text):
    return text.strip().split(",")
'''

# The NUL byte is what makes git classify a file as binary.
LOGO_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24

UNICODE_MODULE = '''\
"""文本处理工具。"""


def 拼接(左边, 右边):
    return 左边 + 右边
'''

PLAIN = '''\
"""A file that is only ever edited."""

VERSION = 1
'''

PLAIN_EDITED = '''\
"""A file that is only ever edited."""

VERSION = 2


def version():
    return VERSION
'''

APP = '''\
"""The application entry point."""
'''

APP_RECREATED = '''\
"""A second entry point, unrelated to the first."""


def main():
    print("hello")
'''

KEPT = '''\
"""A helper that gets renamed away."""
'''

KEPT_RECREATED = '''\
"""A different helper that reuses the old name."""
'''

# One file, edited three times, so that "the last edit" is a different commit
# from "the first edit". Nothing else in the suite edits a file twice, which
# means nothing else can tell those two apart.
EDITED_ONCE = '''\
"""A file that is edited several times."""

VERSION = 1
'''

EDITED_TWICE = '''\
"""A file that is edited several times."""

VERSION = 2
'''

EDITED_THRICE = '''\
"""A file that is edited several times."""

VERSION = 3


def version():
    return VERSION
'''

# Born with no bytes at all. Git reports zero added and zero deleted lines for
# it rather than the dashes it uses for a binary file, which is the boundary the
# is_binary rule turns on.
EMPTY = ""

FILLED = '''\
"""A file that was born empty and filled in later."""

VALUE = 1
'''

# Twenty lines, of which a later rewrite replaces twelve. That leaves too little
# for git's default 50% similarity, so the rename below is reported as a
# deletion plus an addition. The ratio is written as code rather than as text so
# it cannot drift when someone edits a line.
HEAVY = "".join(f"heavy line {number}\n" for number in range(20))

HEAVY_REWRITTEN = "".join(
    f"rewritten line {number}\n" if number < 12 else f"heavy line {number}\n"
    for number in range(20)
)


def git_output(repo, *args, timestamp=None):
    """Run ``git`` inside *repo* and return its stdout.

    Passing *timestamp* freezes the author, the committer and the clock, which
    is what keeps the generated hashes reproducible.
    """
    env = None
    if timestamp is not None:
        env = os.environ.copy()
        env.update(
            GIT_AUTHOR_NAME=AUTHOR_NAME,
            GIT_AUTHOR_EMAIL=AUTHOR_EMAIL,
            GIT_COMMITTER_NAME=AUTHOR_NAME,
            GIT_COMMITTER_EMAIL=AUTHOR_EMAIL,
            GIT_AUTHOR_DATE=timestamp,
            GIT_COMMITTER_DATE=timestamp,
        )

    completed = subprocess.run(
        ["git", *args],
        cwd=Path(repo),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _configure(repo) -> None:
    """The settings every fixture repository needs, in one place.

    No CRLF rewriting and no signing, because both would change what git writes.
    And an identity of its own: commits here run with the author in the
    environment, so they are the same everywhere — but a memory records the
    identity the repository's *configuration* gives, and without this a fixture
    would inherit whatever machine built it.
    """
    git_output(repo, "config", "core.autocrlf", "false")
    git_output(repo, "config", "commit.gpgsign", "false")
    git_output(repo, "config", "user.name", AUTHOR_NAME)
    git_output(repo, "config", "user.email", AUTHOR_EMAIL)


def _write_file(repo, relative_path, content):
    path = Path(repo) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")


def _commit(repo, timestamp, message):
    git_output(repo, "add", "--all")
    git_output(repo, "commit", "--message", message, timestamp=timestamp)


def _day(number: int) -> str:
    """A fixed timestamp for the lifecycle fixture, one per commit."""
    return f"2024-05-{number:02d}T09:00:00+00:00"


# Three files of a hundred lines, rewritten by a different amount while being
# renamed. Forty rewritten lines still clears git's default 50% similarity;
# fifty and eighty do not. See build_rename_boundary_repo.
SWEEP_LINES = 100
SWEEP_REWRITES = {"under": 40, "over": 50, "mostly": 80}


def _sweep_file(name: str, rewritten: int = 0) -> str:
    """A hundred-line file, with the first *rewritten* lines replaced."""
    lines = [
        f"{name} line {number} with a body of text\n" for number in range(SWEEP_LINES)
    ]
    for number in range(rewritten):
        lines[number] = f"REWRITTEN {name} line {number} with other text\n"
    return "".join(lines)


def build_sample_repo(destination):
    """Create the fixture repository at *destination* and return its path."""
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "README.md", README)
    _write_file(repo, "app.py", APP_BEFORE)
    _write_file(repo, "legacy.py", LEGACY)
    _commit(repo, INITIAL_DATE, "Initial commit")

    (repo / "core").mkdir(exist_ok=True)
    (repo / "app.py").rename(repo / "core" / "app.py")
    _commit(repo, RENAME_DATE, "Move app module into the core package")

    git_output(repo, "switch", "--create", "feature/caching")
    _write_file(repo, "core/cache.py", CACHE)
    _commit(repo, CACHE_DATE, "Add caching")

    git_output(repo, "switch", "main")
    _write_file(repo, "core/app.py", APP_AFTER)
    _commit(repo, FIX_DATE, "Fix login bug")

    git_output(
        repo,
        "merge",
        "--no-ff",
        "--message",
        "Merge branch 'feature/caching'",
        "feature/caching",
        timestamp=MERGE_DATE,
    )

    _write_file(repo, "assets/logo.png", LOGO_PNG)
    _write_file(repo, "工具/文本.py", UNICODE_MODULE)
    (repo / "legacy.py").unlink()
    _commit(repo, FINAL_DATE, "Add logo and unicode module, drop legacy helper")

    return repo


def build_single_commit_repo(destination, message="Only commit"):
    """Create a repository with one commit, for tests that need a second one."""
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "only.py", "x = 1\n")
    _commit(repo, INITIAL_DATE, message)

    return repo


def build_lifecycle_repo(destination):
    """Create the repository that exercises the file lifecycle rules.

    The history it creates::

        1  create app.py, kept.py, heavy.py, plain.py
        2  rename app.py -> src/app.py
        3  rename src/app.py -> src/core/app.py, and edit plain.py
        4  delete src/core/app.py
        5  create a second app.py
        6  rename kept.py -> reused.py
        7  create a second kept.py
        8  rename heavy.py -> renamed.py while rewriting most of it

    Eight commits, and between them they cover a file that is only edited, one
    rename, a chain of two renames, a deletion, a name reused after a deletion,
    a name reused after a rename, and a rename git refuses to recognise because
    too little of the content survived.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "app.py", APP)
    _write_file(repo, "kept.py", KEPT)
    _write_file(repo, "heavy.py", HEAVY)
    _write_file(repo, "plain.py", PLAIN)
    _commit(repo, _day(1), "Create four files")

    (repo / "src").mkdir(exist_ok=True)
    (repo / "app.py").rename(repo / "src" / "app.py")
    _commit(repo, _day(2), "Move app.py into the src package")

    (repo / "src" / "core").mkdir(exist_ok=True)
    (repo / "src" / "app.py").rename(repo / "src" / "core" / "app.py")
    _write_file(repo, "plain.py", PLAIN_EDITED)
    _commit(repo, _day(3), "Move app.py into src/core and edit plain.py")

    (repo / "src" / "core" / "app.py").unlink()
    _commit(repo, _day(4), "Delete the app module")

    _write_file(repo, "app.py", APP_RECREATED)
    _commit(repo, _day(5), "Create a second app.py")

    (repo / "kept.py").rename(repo / "reused.py")
    _commit(repo, _day(6), "Rename kept.py to reused.py")

    _write_file(repo, "kept.py", KEPT_RECREATED)
    _commit(repo, _day(7), "Create a second kept.py")

    (repo / "heavy.py").rename(repo / "renamed.py")
    _write_file(repo, "renamed.py", HEAVY_REWRITTEN)
    _commit(repo, _day(8), "Rename heavy.py while rewriting most of it")

    return repo


def build_cochange_repo(destination):
    """Create the repository whose co-change numbers can be worked out by hand.

    The history it creates::

        1  create a.py and b.py
        2  edit a.py and b.py
        3  edit a.py and create c.py

    Three commits and three files, and between them they are the whole of the
    statistic's shape: a pair that repeats (a and b, twice), a pair that happens
    once (a and c), and two files that are each alone in one of their commits
    (b in commit 1, c in commit 3 — each shares a commit with the other, but
    only through a).

    The numbers, by hand::

        a  analyzed 3 commits  ->  b shared 2, score 2/3;  c shared 1, score 1/3
        b  analyzed 2 commits  ->  a shared 2, score 2/2 = 1
        c  analyzed 1 commit   ->  a shared 1, score 1/1 = 1

    With the default minimum of two shared commits, ``c`` has nothing to show
    and ``a`` has one hidden pair; with ``min_shared=1`` all three pairs are
    shown. Both are asserted in ``test_cochange.py``.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "a.py", "a = 1\n")
    _write_file(repo, "b.py", "b = 1\n")
    _commit(repo, _day(1), "Create a and b")

    _write_file(repo, "a.py", "a = 2\n")
    _write_file(repo, "b.py", "b = 2\n")
    _commit(repo, _day(2), "Edit a and b together")

    _write_file(repo, "a.py", "a = 3\n")
    _write_file(repo, "c.py", "c = 1\n")
    _commit(repo, _day(3), "Edit a and create c")

    return repo


def build_broken_repo(destination):
    """Create the repository whose Python file stops parsing.

    The history it creates::

        1  broken.py holds one function, and it reads
        2  the same file gains a syntax error

    Two commits and one file, and the file is the point: the second version
    cannot be parsed at all, so the tool has to say "not known" rather than
    report a deletion it cannot see or print a file that held nothing. The
    README's ``structure broken.py`` blocks run against this repository, which
    is why the file is five lines long and the error is where it is: the
    missing-colon line puts the parser's own report at line 5, column 12, and
    the block quotes that wording.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "broken.py", BROKEN_BEFORE)
    _commit(repo, _day(1), "Add broken.py")

    _write_file(repo, "broken.py", BROKEN_AFTER)
    _commit(repo, _day(2), "Break broken.py")

    return repo


# Five lines, so the missing colon is reported on line 5, and the offending
# token starts at column 12. The README's structure blocks quote that position.
BROKEN_BEFORE = '''\
def ok():
    return 1
'''

BROKEN_AFTER = '''\
def ok():
    return 1


def broken(:
    pass
'''


def build_edit_history_repo(destination):
    """Create the repository that edits one file over and over.

    The history it creates::

        1  create edited.py, empty.py and untouched.py
        2  edit edited.py
        3  edit edited.py again
        4  edit edited.py a third time
        5  fill empty.py

    Five commits, and the first four exist for one reason: ``edited.py`` is the
    only file in the whole suite that is modified more than once, so it is the
    only one that can tell "the last edit" apart from "the first edit". Every
    other fixture has at most a single modification, which means a last-modified
    time taken from the wrong end would pass everywhere else.

    ``untouched.py`` is born with no bytes and never touched, and ``empty.py``
    is born empty and filled in later. They are the boundary of the binary rule:
    git reports zero lines for an empty file and dashes for a binary one, so a
    file with no lines must not be mistaken for a file git cannot count.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "edited.py", EDITED_ONCE)
    _write_file(repo, "empty.py", EMPTY)
    _write_file(repo, "untouched.py", EMPTY)
    _commit(repo, _day(1), "Create three files, two of them empty")

    _write_file(repo, "edited.py", EDITED_TWICE)
    _commit(repo, _day(2), "Edit edited.py")

    _write_file(repo, "edited.py", EDITED_THRICE)
    _commit(repo, _day(3), "Edit edited.py again")

    _write_file(repo, "edited.py", EDITED_THRICE + "\n# one more\n")
    _commit(repo, _day(4), "Edit edited.py a third time")

    _write_file(repo, "empty.py", FILLED)
    _commit(repo, _day(5), "Fill in the empty file")

    return repo


def build_deep_history_repo(destination, edits: int = 8):
    """Create a repository that edits one file more often than a window holds.

    The context keeps a bounded window of the commits before this one that
    touched each file, and every other fixture stops well short of filling it —
    which means a window of any size at all would pass on them, and the count of
    what was left out would be zero in every test that reads it. This one is
    longer than the window on purpose: a cap nothing fills is a cap nothing
    checks.

    *edits* commits touch ``deep.py`` after the one that creates it, so the last
    of them has *edits* earlier commits to draw a window from.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "deep.py", _sweep_file("deep", 0))
    _commit(repo, _day(1), "Create deep.py")

    for number in range(1, edits + 1):
        _write_file(repo, "deep.py", _sweep_file("deep", number))
        _commit(repo, _day(number + 1), f"Edit deep.py, pass {number}")

    return repo


# The wide fixture: one commit touching more files than the model's view holds,
# with each file edited in a different number of places so that the files kept by
# a size rule and the files kept by path order are different sets.
WIDE_FILES = 30
WIDE_HISTORY = 6
WIDE_SPREAD = 60
WIDE_SMALL_MESSAGE = "Create the first module"
WIDE_ADD_MESSAGE = "Add the other modules"
WIDE_MESSAGE = "Rewrite every module"


def _wide_path(index: int) -> str:
    return f"mod{index:02d}.py"


def _wide_file(index: int, edit: int, hunks: int = 0) -> str:
    """One version of one file in the wide fixture: real Python, edited in places.

    ``hunks`` is how many separate lines this version changes, spaced two apart so
    that ``--unified=0`` reports them as separate hunks rather than as one run.
    The fixture's last commit gives file *index* one more changed line than the
    file before it, which is what makes a size rule and a path rule keep
    different sets — a fixture where every file changed equally could not tell the
    two apart, and would pass whichever one the code happened to do.
    """
    lines = [f"# module {index}"]
    for number in range(WIDE_SPREAD):
        value = edit if hunks and number % 2 == 0 and number // 2 < hunks else 0
        lines.append(f"value_{number} = {value}")
    lines.append(f"def function_{index}(value):")
    lines.append(f"    return value + {edit}")
    return "\n".join(lines) + "\n"


def build_wide_commit_repo(destination, files=WIDE_FILES, history=WIDE_HISTORY):
    """Create a repository whose last commit is too large to send to a model.

    The context builder's rule is that a commit's own facts are not capped, and
    this is the fixture that turns that rule into a number: the last commit
    touches *files* files, so its bundle runs to tens of thousands of estimated
    tokens while the view built from it does not.

    The first commit touches a single file, which is the other half of what the
    selection layer has to get right: a commit that fits is sent whole. The
    commits in between edit every module, so the earlier-commit window and the
    co-change statistics have a history to be built from rather than an empty one.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, _wide_path(0), _wide_file(0, edit=1))
    _commit(repo, _day(1), WIDE_SMALL_MESSAGE)

    for index in range(1, files):
        _write_file(repo, _wide_path(index), _wide_file(index, edit=2))
    _commit(repo, _day(2), WIDE_ADD_MESSAGE)

    for number in range(3, history + 3):
        for index in range(files):
            _write_file(repo, _wide_path(index), _wide_file(index, edit=number))
        _commit(repo, _day(number), f"Edit every module, pass {number - 2}")

    for index in range(files):
        _write_file(
            repo,
            _wide_path(index),
            _wide_file(index, edit=history + 3, hunks=index + 1),
        )
    _commit(repo, _day(history + 3), WIDE_MESSAGE)

    return repo


LARGE_COMMITS = 1000
LARGE_FILES = 60
LARGE_TOUCHED = 5
LARGE_LINES = 22
# What one version of one file holds: three functions, a class and its two
# methods. The AST layer's totals are this times the number of versions, which is
# what makes them arithmetic rather than measurement.
LARGE_FUNCTIONS = 3
LARGE_DEFINITIONS = LARGE_FUNCTIONS + 3


def _large_file(index: int, edit: int) -> bytes:
    """One revision of one file in the large fixture: valid Python, edited once.

    A fixed size and a fixed shape — ``LARGE_LINES`` lines and
    ``LARGE_DEFINITIONS`` definitions — with one function's operator flipped per
    edit, so a version has one modified definition and the rest unchanged. The
    v0.2 tests read this fixture for its line counts; the v0.3 ones read it for
    its rows, and a file that does not parse would give them none.
    """
    body = f"# module {index}\n"
    for number in range(LARGE_FUNCTIONS):
        operator = "-" if number == edit % LARGE_FUNCTIONS else "+"
        body += f"def function_{number}(value):\n"
        body += f"    return value {operator} {number}\n\n\n"

    body += "class Holder:\n"
    body += "    def get(self):\n        return 0\n\n"
    body += "    def put(self, value):\n        return value\n\n\n"
    body += f"# edit {edit}\n"
    return body.encode()


def _large_stream(commits: int, files: int, touched: int) -> bytes:
    """A ``git fast-import`` stream that builds the whole history at once.

    The contents are inline in the stream rather than sent as separate ``blob``
    commands, because fast-import rejects a ``blob`` inside a commit. The extra
    newline after each body is the one fast-import allows after ``data``, not
    part of the file.
    """
    out: list[bytes] = []
    parent = None
    mark = 0

    for number in range(commits):
        mark += 1
        when = 1700000000 + number * 60
        message = f"Change {number}\n".encode()

        out.append(f"commit refs/heads/main\nmark :{mark}\n".encode())
        out.append(f"author {AUTHOR_NAME} <{AUTHOR_EMAIL}> {when} +0000\n".encode())
        out.append(f"committer {AUTHOR_NAME} <{AUTHOR_EMAIL}> {when} +0000\n".encode())
        out.append(f"data {len(message)}\n".encode() + message)
        if parent is not None:
            out.append(f"from :{parent}\n".encode())

        for offset in range(touched):
            index = (number + offset) % files
            body = _large_file(index, number)
            out.append(
                f"M 100644 inline pkg{index // 10}/mod{index}.py\ndata {len(body)}\n".encode()
                + body
                + b"\n"
            )

        parent = mark

    return b"".join(out)


def build_large_repo(
    destination,
    commits: int = LARGE_COMMITS,
    files: int = LARGE_FILES,
    touched: int = LARGE_TOUCHED,
):
    """Create a repository with a thousand commits and five thousand changes.

    Each file is created by the first commit that touches it and edited by every
    later one, so the history is one birth and a run of edits per file, with no
    renames and no deletions. The size is the point, not the rules: this is what
    catches a walk that is accidentally quadratic, or a query that stops working
    once there is more than a handful of rows.

    ``git fast-import`` builds it in about a quarter of a second. A thousand
    ``git commit`` calls would take minutes, which is the difference between a
    test that is kept and a test that gets deleted for being slow.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    completed = subprocess.run(
        ["git", "fast-import", "--quiet"],
        cwd=repo,
        input=_large_stream(commits, files, touched),
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git fast-import failed: {completed.stderr.decode()}")

    # fast-import writes objects and refs, not the working tree, so the checkout
    # is done afterwards. The history is all the tests read, but a repository
    # whose files are missing from disk is a trap for whoever runs it by hand.
    git_output(repo, "reset", "--hard", "HEAD")

    return repo


def build_same_second_repo(destination):
    """Create a repository whose commits all carry the same timestamp.

    Three commits inside one second: a file is created, renamed and then edited.
    A rebase, a scripted import or a converted history all produce timestamps
    like this. Walking the history by time alone would leave the order up to
    whoever passed it in, and the stored history arrives newest first, so the
    rename would be replayed before the creation it belongs to.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "app.py", APP)
    _commit(repo, SAME_SECOND, "Create app.py")

    (repo / "src").mkdir(exist_ok=True)
    (repo / "app.py").rename(repo / "src" / "app.py")
    _commit(repo, SAME_SECOND, "Move app.py into the src package")

    _write_file(repo, "src/app.py", APP_RECREATED)
    _commit(repo, SAME_SECOND, "Edit app.py")

    return repo


def build_rename_boundary_repo(destination):
    """Create the repository that walks up to git's rename threshold.

    Two commits: three hundred-line files are created, then all three are renamed
    in a single commit while a different number of lines is rewritten.

    ``under`` keeps sixty lines, which clears the default 50% similarity, so git
    reports a rename. ``over`` keeps fifty and ``mostly`` keeps twenty, which do
    not, so git reports a deletion and an addition instead. Both sides of the
    threshold sit in the same commit, which also checks that git pairs each
    deletion with the right addition when several are in flight at once.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    for name in SWEEP_REWRITES:
        _write_file(repo, f"{name}.py", _sweep_file(name))
    _commit(repo, _day(1), "Create three files of a hundred lines")

    for name, rewritten in SWEEP_REWRITES.items():
        (repo / f"{name}.py").rename(repo / f"{name}_after.py")
        _write_file(repo, f"{name}_after.py", _sweep_file(name, rewritten))
    _commit(repo, _day(2), "Rename all three, rewriting a different amount each")

    return repo


def add_commit(repo, message, timestamp=LATER_DATE):
    """Append one commit to an existing repository and return its sha.

    A test that needs the repository to move past an analysis uses this. It
    writes to the caller's own repository rather than to the shared fixture, so
    the fixture stays at its six commits for every other test.
    """
    _write_file(repo, "extra.txt", f"{message}\n")
    _commit(repo, timestamp, message)
    return git_output(repo, "rev-parse", "HEAD").strip()


DEFINITION_V1 = '''"""A module whose functions change."""


def login(user):
    return user == "admin"


def helper(value):
    return value + 1
'''

DEFINITION_V2 = '''"""A module whose functions change."""


def login(user, password):
    return user == "admin" and password == "secret"


def helper(value):
    return value + 1
'''

DEFINITION_V3 = DEFINITION_V2 + '''

def logout(user):
    print(user)
'''

# The same body under a new name. The pass must read this as one definition
# disappearing and another appearing, never as a rename.
DEFINITION_V4 = '''"""A module whose functions change."""


def authenticate(user, password):
    return user == "admin" and password == "secret"


def helper(value):
    return value + 1


def logout(user):
    print(user)
'''

# A missing colon: this version of the file cannot be read at all.
DEFINITION_BROKEN = '''"""A module whose functions change."""


def login(user, password)
    return user == "admin"
'''


def build_definition_repo(destination):
    """Create a repository whose one Python file changes in every way that matters.

    Six commits, one file::

        A  login and helper
        B  login rewritten               (login modified, helper unchanged)
        C  logout added                  (logout created)
        D  login renamed to authenticate (one gone, one created)
        E  the file stops parsing        (a recorded failure, not a set of deletions)
        F  back to what D held           (unchanged, compared across E)
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    versions = (
        DEFINITION_V1,
        DEFINITION_V2,
        DEFINITION_V3,
        DEFINITION_V4,
        DEFINITION_BROKEN,
        DEFINITION_V4,
    )
    for number, content in enumerate(versions, start=1):
        _write_file(repo, "app.py", content)
        _commit(repo, _day(number), f"definition change {number}")

    return repo


# DEFINITION_V1 with login taken out, which is what a file can come back as
# after a version nobody could read.
DEFINITION_V1_WITHOUT_LOGIN = '''"""A module whose functions change."""


def helper(value):
    return value + 1
'''


def build_gap_repo(destination):
    """Create the repository where a file's versions go dark.

    Three commits and three files, each going dark in a different way::

        1  vanished.py, dark.py and gone.py each hold login and helper
        2  all three stop parsing
        3  vanished.py is read again and no longer holds login, dark.py is
           untouched so it is still dark, and gone.py is deleted

    One gap, three endings, and each is the case the history layer has to get
    right: a deletion that happened *somewhere* inside the gap, a definition
    whose file was never read again so its end is unknown, and a definition that
    went with the file.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    for name in ("vanished.py", "dark.py", "gone.py"):
        _write_file(repo, name, DEFINITION_V1)
    _commit(repo, _day(1), "Create three files holding the same two definitions")

    for name in ("vanished.py", "dark.py", "gone.py"):
        _write_file(repo, name, DEFINITION_BROKEN)
    _commit(repo, _day(2), "Break all three files")

    _write_file(repo, "vanished.py", DEFINITION_V1_WITHOUT_LOGIN)
    (repo / "gone.py").unlink()
    _commit(repo, _day(3), "Drop login while the files were dark, and delete gone.py")

    return repo


def build_interleaved_repo(destination):
    """Create the repository where a file is deleted on one branch and edited on another.

    Four commits::

        A  app.py holds login and helper
        |\\
        B |  delete app.py            (branch gone)
        | C  rewrite login            (main)
        |/
        M  merge branch 'gone', keeping app.py

    The delete comes first in time, so the file's life reads: born, deleted,
    edited — a hole in the middle of one life rather than an end followed by a
    second one. Git only reports that shape when a delete on one branch and an
    edit on another are interleaved by the clock, which the fixed timestamps
    arrange.
    """
    repo = Path(destination)
    repo.mkdir(parents=True, exist_ok=True)

    git_output(repo, "init", "--initial-branch", "main")
    _configure(repo)

    _write_file(repo, "app.py", DEFINITION_V1)
    _commit(repo, _day(1), "Create app.py")

    git_output(repo, "switch", "--create", "gone")
    (repo / "app.py").unlink()
    _commit(repo, _day(2), "Delete app.py on the branch")

    git_output(repo, "switch", "main")
    _write_file(repo, "app.py", DEFINITION_V2)
    _commit(repo, _day(3), "Rewrite login on main")

    # Keeping our whole tree is what a person resolving this conflict would do:
    # the branch deleted the file, main changed it, and the file stays. The
    # ``ours`` strategy is the only one that resolves a delete against an edit,
    # which the ``ours`` option of the default strategy does not.
    git_output(
        repo,
        "merge",
        "--no-ff",
        "-s",
        "ours",
        "--message",
        "Merge branch 'gone', keeping app.py",
        "gone",
        timestamp=_day(4),
    )

    return repo


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = Path(tempfile.mkdtemp(prefix="sample-repo-"))
    print(build_sample_repo(target))
