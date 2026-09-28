"""Read commit history out of a Git repository.

``read_commits`` runs one ``git log`` per call; ``parse_commits`` is the pure
function that turns its output into objects, which is what most of the tests
exercise.

Everything below was checked against real git output rather than assumed:

* ``--raw`` and ``--numstat`` can be combined and are printed as two sections
  per commit. ``--name-status`` cannot: combined with ``--numstat`` it silently
  wins and the counts disappear, so ``--raw`` carries the change type here.
* ``-z`` makes git separate paths with NUL and print them verbatim, so a path
  holding non-ASCII characters comes through as UTF-8 instead of being quoted.
* ``git log`` prints no diff at all for a merge commit, so those commits come
  back with an empty ``changes``. Their parents are recorded, so the diff can be
  recomputed later without re-reading the repository.
* ``--numstat`` prints ``-`` for both counters of a binary file, which becomes
  ``None`` here.
* In the ``--raw`` section a rename is the status field followed by two paths;
  in ``--numstat`` it is an empty path field followed by two paths. Both
  sections are joined on ``(old_path, path)``.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

COMMIT_MARKER = "\x1e"
FIELD_SEPARATOR = "\x1f"
RECORD_SEPARATOR = "\x00"

# git expands %x1e, %x1f and %x00 into those bytes. They are spelled as escapes
# here rather than as the characters themselves because a command line argument
# is a NUL-terminated string in the operating system, so a literal NUL can never
# be passed to a process.
COMMIT_FORMAT = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%B%x00"

LOG_ARGUMENTS = (
    "--no-pager",
    "log",
    "--raw",
    "--numstat",
    "-M",
    "-z",
    f"--format={COMMIT_FORMAT}",
)

NUMSTAT_PATTERN = re.compile(r"^(\d+|-)\t(\d+|-)\t")


class GitError(RuntimeError):
    """Raised when git cannot be run or exits with an error."""


@dataclass(frozen=True, slots=True)
class FileChange:
    """One file touched by a commit."""

    path: str
    change_type: str
    added_lines: int | None
    deleted_lines: int | None
    old_path: str | None = None

    @property
    def is_binary(self) -> bool:
        """Binary files have no line counts, so git reports ``-`` for both."""
        return self.added_lines is None and self.deleted_lines is None


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit, with the files it touched."""

    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: datetime
    committed_at: datetime
    message: str
    changes: tuple[FileChange, ...]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


def read_commits(repo, revision: str = "HEAD") -> list[Commit]:
    """Return the commits reachable from *revision*, newest first."""
    return parse_commits(_run_git(repo, *LOG_ARGUMENTS, revision))


def parse_commits(output: bytes) -> list[Commit]:
    """Turn the output of the ``git log`` call above into :class:`Commit`s."""
    text = output.decode("utf-8", errors="surrogateescape")
    return [
        _parse_commit(chunk) for chunk in text.split(COMMIT_MARKER) if chunk.strip()
    ]


def read_head_sha(repo) -> str:
    """Return the commit that ``HEAD`` points at."""
    return _run_git(repo, "--no-pager", "rev-parse", "HEAD").decode("ascii").strip()


def find_repository_root(path) -> Path | None:
    """Return the working tree root that contains *path*, or ``None``.

    ``None`` means git does not consider *path* to be inside a repository at
    all, which is a different situation from a repository that exists but has no
    commits yet.
    """
    completed = _run_git_unchecked(
        path, "--no-pager", "rev-parse", "--show-toplevel"
    )
    if completed.returncode != 0:
        return None
    root = completed.stdout.decode("utf-8", errors="surrogateescape").strip()
    return Path(root).resolve()


def _run_git_unchecked(repo, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=Path(repo),
        capture_output=True,
    )


def _run_git(repo, *args: str) -> bytes:
    completed = _run_git_unchecked(repo, *args)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git: {detail}")
    return completed.stdout


def _parse_commit(chunk: str) -> Commit:
    header, *tokens = chunk.split(RECORD_SEPARATOR)
    fields = header.split(FIELD_SEPARATOR)
    if len(fields) != 7:
        raise ValueError(f"expected 7 commit header fields, got {len(fields)}")
    sha, parents, author_name, author_email, authored, committed, message = fields

    diff_tokens = [token for token in tokens if token]
    if diff_tokens:
        # git puts a newline between the formatted header and the first entry.
        diff_tokens[0] = diff_tokens[0].lstrip("\n")

    change_types: dict[tuple[str | None, str], str] = {}
    line_counts: dict[tuple[str | None, str], tuple[int | None, int | None]] = {}

    index = 0
    while index < len(diff_tokens):
        token = diff_tokens[index]
        if token.startswith(":"):
            change_type, key, index = _parse_raw_entry(diff_tokens, index)
            change_types[key] = change_type
        elif NUMSTAT_PATTERN.match(token):
            counts, key, index = _parse_numstat_entry(diff_tokens, index)
            line_counts[key] = counts
        else:
            raise ValueError(f"unexpected git output: {token!r}")

    if change_types.keys() != line_counts.keys():
        raise ValueError("git reported different files in --raw and --numstat")

    changes = []
    for key, change_type in change_types.items():
        old_path, path = key
        added, deleted = line_counts[key]
        changes.append(
            FileChange(
                path=path,
                change_type=change_type,
                added_lines=added,
                deleted_lines=deleted,
                old_path=old_path,
            )
        )

    return Commit(
        sha=sha,
        parents=tuple(parents.split()),
        author_name=author_name,
        author_email=author_email,
        authored_at=datetime.fromisoformat(authored),
        committed_at=datetime.fromisoformat(committed),
        message=message.rstrip("\n"),
        changes=tuple(changes),
    )


def _parse_raw_entry(tokens: list[str], index: int):
    fields = tokens[index][1:].split(" ")
    if len(fields) != 5:
        raise ValueError(f"unexpected --raw entry: {tokens[index]!r}")

    change_type = fields[4][0]
    index += 1

    old_path = None
    if change_type in "RC":
        old_path = tokens[index]
        index += 1

    path = tokens[index]
    return change_type, (old_path, path), index + 1


def _parse_numstat_entry(tokens: list[str], index: int):
    added, deleted, path = tokens[index].split("\t", 2)
    index += 1

    old_path = None
    if not path:
        old_path, path = tokens[index], tokens[index + 1]
        index += 2

    return (_to_line_count(added), _to_line_count(deleted)), (old_path, path), index


def _to_line_count(value: str) -> int | None:
    return None if value == "-" else int(value)
