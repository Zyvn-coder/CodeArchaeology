"""Read file contents out of a Git repository, one process for the whole run.

Every other module that needs git runs it once per question and reads the answer.
This one cannot: a scan of a large repository asks for hundreds of thousands of
file versions, and a ``git show`` per file would spend the run starting
processes. Instead one ``git cat-file --batch`` is kept open and fed one object
at a time. Measured on this machine: about 7,000 objects a second, against 664
file versions a second for the parsing that consumes them, so the reading is not
the part that costs.

Four things about the protocol were measured rather than assumed, and each one is
a way of getting it wrong:

* **The answer is not line-based.** A header line gives the size, and exactly that
  many bytes follow — bytes that may contain newlines and NULs, and may look like
  a header themselves. A reader that went by lines would lose a file at its first
  blank line.
* **An empty object still ends with a separator.** ``<sha> blob 0\\n`` is followed
  by the same newline that ends every other answer.
* **A missing object is an answer, not an error.** git echoes the request and
  writes ``missing``, and the process carries on. An empty or nonsense request
  gets that same answer, which is why a request holding a newline is refused
  here: git would read it as two requests, and every later answer would belong to
  somebody else's question.
* **Requests cannot be written ahead of the answers.** Writing three thousand
  requests before reading any answer fills git's stdout pipe, git stops reading,
  and the write fails with ``BrokenPipeError``. One request, one answer.

Closing stdin is the whole shutdown: git exits with status 0 and prints nothing.

The identity of what comes back is part of the answer, not something worked out
afterwards. ``git cat-file`` reports the object id it resolved a request to, so a
file version named ``<commit>:<path>`` comes back with that blob's own id beside
its bytes — the id git has, never a hash computed here. That is what lets the AST
pass record a content identity for every version it reads without asking git a
second time and without re-reading the repository to find out which content a
version held.
"""

import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from codearchaeology.history import GitError

MISSING = b"missing"

# Long enough for git to exit on end of input, which it does at once, and short
# enough that a wedged process cannot hold up the end of a scan.
CLOSE_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class GitObject:
    """One object, exactly as git reported it.

    ``sha`` is git's own object id for what came back. Asking for a file version
    — ``<commit>:<path>``, which :func:`file_version_id` writes — is how a caller
    learns which blob that version held, and the id arrives in the header of the
    answer rather than being recomputed from the bytes.

    ``kind`` is git's word for the type: a path that names a directory resolves
    to a ``tree``, and a caller that only wants file contents can say so by
    checking it. ``size`` is the length git reported, which the reader has already
    checked against what it actually read.
    """

    sha: str
    kind: str
    size: int
    content: bytes


class ObjectReader:
    """A single ``git cat-file --batch`` process, kept open for many reads.

    Use it as a context manager: the process starts when the reader is built and
    ends when the block does. Reading after the process died on its own raises
    :class:`GitError` rather than returning something that looks like content.
    """

    def __init__(self, repository_root):
        self.repository_root = Path(repository_root)
        # Exposed rather than hidden, because the caller that needs to know
        # whether the process is still there — a test, or a scan deciding whether
        # to report a crash — should be able to ask instead of guess.
        self.process = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=self.repository_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def __enter__(self) -> "ObjectReader":
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    def close(self) -> None:
        """End the process. Safe to call more than once."""
        if self.process.stdin is not None and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                # The process is already gone, so there is no input left to end
                # and nobody to tell. The wait below is what matters.
                pass
        try:
            self.process.wait(timeout=CLOSE_TIMEOUT)
        except subprocess.TimeoutExpired:
            # End of input is what tells git to stop. A process that is blocked
            # writing to a pipe nobody reads would never see it, and waiting for
            # that is waiting forever.
            self.process.kill()
            self.process.wait()

    def read(self, object_id: str) -> GitObject | None:
        """Return the object *object_id* names, or ``None`` if git has no such object.

        *object_id* is anything ``git cat-file`` takes: a blob sha, or a file
        version written as ``<commit>:<path>`` (see :func:`file_version_id`). What
        comes back is what git reported — the id it resolved the request to, the
        object's type and size, and its bytes — so a caller that asked for a file
        version learns which blob that version held without asking again.
        """
        self._request(object_id)
        header = self.process.stdout.readline()
        if not header:
            raise self._stopped()

        fields = header.split()
        if fields and fields[-1] == MISSING:
            return None
        if len(fields) != 3 or not fields[2].isdigit():
            raise GitError(f"git cat-file answered {object_id!r} with {header!r}")

        sha = fields[0].decode("ascii", errors="replace")
        kind = fields[1].decode("ascii", errors="replace")
        size = int(fields[2])
        # The separator is read with the content, so that one read has to be
        # right for the next answer to start where it should.
        payload = self.process.stdout.read(size + 1)
        if len(payload) != size + 1:
            raise self._stopped()
        if payload[-1:] != b"\n":
            raise GitError(f"git cat-file lost its place after {object_id!r}")
        return GitObject(sha=sha, kind=kind, size=size, content=payload[:-1])

    def read_many(self, object_ids: Iterable[str]) -> Iterator[GitObject | None]:
        """Read every id in *object_ids*, in order, over this one process."""
        for object_id in object_ids:
            yield self.read(object_id)

    def _request(self, object_id: str) -> None:
        if "\n" in object_id or "\r" in object_id:
            raise ValueError(f"not an object id: {object_id!r}")
        # surrogateescape, because that is how a path read out of git was decoded.
        # Encoding it any other way fails on the bytes git reported verbatim.
        try:
            self.process.stdin.write(
                object_id.encode("utf-8", "surrogateescape") + b"\n"
            )
            self.process.stdin.flush()
        except OSError:
            # A process that died is met here rather than at the read: writing to
            # the pipe of a process that is gone fails first, and a broken pipe
            # says nothing about what the caller asked for.
            raise self._stopped() from None

    def _stopped(self) -> GitError:
        """The error for a process that stopped answering in the middle of a read."""
        self.process.wait()
        detail = self.process.stderr.read().decode("utf-8", errors="replace").strip()
        message = f"git cat-file stopped answering (exit {self.process.returncode})"
        return GitError(f"{message}: {detail}" if detail else message)


def read_blob(repository_root, blob_sha: str) -> bytes | None:
    """Read one object's content, with a process of its own.

    For more than a handful of objects use :class:`ObjectReader` or
    :func:`read_objects`: one process per call is the shape this module exists to
    avoid. A caller that wants the object's id as well as its bytes wants
    :meth:`ObjectReader.read`, which returns both.
    """
    with ObjectReader(repository_root) as reader:
        found = reader.read(blob_sha)
    return None if found is None else found.content


def read_objects(
    repository_root, object_ids: Iterable[str]
) -> Iterator[GitObject | None]:
    """Read many objects over one process, in order.

    Lazy: the process starts when the first answer is asked for and ends when the
    last one has been read, so a caller that stops early ends it too.
    """
    with ObjectReader(repository_root) as reader:
        yield from reader.read_many(object_ids)


def file_version_id(commit_sha: str, path: str) -> str:
    """Name one file version the way git does: the commit, a colon, the path.

    This is what makes "which blob did this version hold" a single question with
    a single answer. The read comes back with the blob's own object id beside its
    bytes, so the AST pass can store a content identity for every version it
    parses without re-reading the history and without hashing anything itself.
    """
    return f"{commit_sha}:{path}"
