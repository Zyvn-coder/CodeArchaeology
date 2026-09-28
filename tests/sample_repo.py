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


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = Path(tempfile.mkdtemp(prefix="sample-repo-"))
    print(build_sample_repo(target))
