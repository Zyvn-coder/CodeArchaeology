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
    git_output(repo, "config", "core.autocrlf", "false")
    git_output(repo, "config", "commit.gpgsign", "false")

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
    git_output(repo, "config", "core.autocrlf", "false")
    git_output(repo, "config", "commit.gpgsign", "false")

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
    git_output(repo, "config", "core.autocrlf", "false")
    git_output(repo, "config", "commit.gpgsign", "false")

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
    git_output(repo, "config", "core.autocrlf", "false")
    git_output(repo, "config", "commit.gpgsign", "false")

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


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = Path(tempfile.mkdtemp(prefix="sample-repo-"))
    print(build_sample_repo(target))
