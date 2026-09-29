"""Tests for reading object contents out of a repository.

The blobs under test are written with ``git hash-object -w`` rather than
committed: this unit reads objects, and a history would only be in the way.
"""

import gc
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from codearchaeology import objects
from codearchaeology.history import GitError
from codearchaeology.objects import (
    GitObject,
    ObjectReader,
    file_version_id,
    read_blob,
    read_objects,
)
from sample_repo import git_output

TEXT = b"hello\n"
EMPTY = b""
BINARY = b"a\x00b\n"
LARGE = b"x" * (5 * 1024 * 1024)
# A blob whose first line looks like a header. A reader that went by lines would
# take it for the answer to the next request and lose its place.
DECEPTIVE = b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef blob 3\nnot content\n"

CONTENTS = {
    "text": TEXT,
    "empty": EMPTY,
    "binary": BINARY,
    "large": LARGE,
    "deceptive": DECEPTIVE,
}


@dataclass(frozen=True, slots=True)
class BlobRepository:
    """A repository holding the blobs under test, and their shas by name."""

    root: Path
    shas: dict[str, str]


class _CountingGit:
    """Stands in for the ``subprocess`` module and counts the processes started."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen] = []

    def __getattr__(self, name):
        """Everything else — ``PIPE`` and whatever else is asked for — is the real one."""
        return getattr(subprocess, name)

    def Popen(self, *args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        self.processes.append(process)
        return process

    @property
    def started(self) -> int:
        return len(self.processes)


@pytest.fixture(scope="module")
def blobs(tmp_path_factory: pytest.TempPathFactory) -> BlobRepository:
    repository = tmp_path_factory.mktemp("blobs")
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch", "main"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    shas = {}
    for name, content in CONTENTS.items():
        completed = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repository,
            input=content,
            capture_output=True,
            check=True,
        )
        shas[name] = completed.stdout.decode("ascii").strip()
    return BlobRepository(root=repository, shas=shas)


def _read(blobs: BlobRepository, name: str) -> bytes | None:
    return read_blob(blobs.root, blobs.shas[name])


def read_blob_object(repository: Path, object_id: str) -> GitObject:
    """Read one object through a reader of its own, with its identity."""
    with ObjectReader(repository) as reader:
        found = reader.read(object_id)
    assert found is not None, object_id
    return found


def test_a_normal_blob(blobs: BlobRepository) -> None:
    assert _read(blobs, "text") == TEXT


def test_an_empty_blob_is_empty_and_not_missing(blobs: BlobRepository) -> None:
    assert _read(blobs, "empty") == b""


def test_a_binary_blob_comes_back_byte_for_byte(blobs: BlobRepository) -> None:
    assert _read(blobs, "binary") == BINARY


def test_a_missing_object_is_an_answer_rather_than_an_error(
    blobs: BlobRepository,
) -> None:
    assert read_blob(blobs.root, "0" * 40) is None


def test_a_large_blob(blobs: BlobRepository) -> None:
    assert _read(blobs, "large") == LARGE


def test_content_that_looks_like_a_header_does_not_move_the_reader(
    blobs: BlobRepository,
) -> None:
    """The answer is a length, not a set of lines."""
    with ObjectReader(blobs.root) as reader:
        assert reader.read(blobs.shas["deceptive"]).content == DECEPTIVE
        assert reader.read(blobs.shas["text"]).content == TEXT


def test_a_name_resolves_to_the_object_it_points_at(sample_repo: Path) -> None:
    """The header then carries the sha, not the name that was asked for."""
    assert read_blob(sample_repo, "HEAD:README.md").startswith(b"# sample project")


def test_a_read_reports_the_id_git_resolved(sample_repo: Path) -> None:
    """The identity comes from git, and is checked against git rather than trusted."""
    found = read_blob_object(sample_repo, "HEAD:README.md")
    assert found.sha == git_output(sample_repo, "rev-parse", "HEAD:README.md").strip()
    assert found.kind == "blob"
    assert found.size == len(found.content)


def test_a_file_version_names_its_blob(sample_repo: Path) -> None:
    """What the AST pass needs: one read gives the content and which blob it was."""
    head = git_output(sample_repo, "rev-parse", "HEAD").strip()
    version = file_version_id(head, "README.md")
    found = read_blob_object(sample_repo, version)

    assert found.sha == git_output(sample_repo, "rev-parse", version).strip()
    assert found.content == (sample_repo / "README.md").read_bytes()


def test_a_directory_resolves_to_a_tree(sample_repo: Path) -> None:
    """Which is why the type comes back: a file and a directory are not the same."""
    assert read_blob_object(sample_repo, "HEAD:core").kind == "tree"


def test_reading_a_blob_by_its_own_id_reports_that_id(blobs: BlobRepository) -> None:
    found = read_blob_object(blobs.root, blobs.shas["text"])
    assert (found.sha, found.kind, found.content) == (blobs.shas["text"], "blob", TEXT)


def test_many_objects_come_back_in_order(blobs: BlobRepository) -> None:
    wanted = [blobs.shas["text"], blobs.shas["empty"], blobs.shas["binary"], "0" * 40]
    found = list(read_objects(blobs.root, wanted))
    assert [None if item is None else item.content for item in found] == [
        TEXT,
        EMPTY,
        BINARY,
        None,
    ]
    assert [None if item is None else item.sha for item in found] == [
        blobs.shas["text"],
        blobs.shas["empty"],
        blobs.shas["binary"],
        None,
    ]


def test_the_process_ends_with_the_block(blobs: BlobRepository) -> None:
    with ObjectReader(blobs.root) as reader:
        reader.read(blobs.shas["text"])
        assert reader.process.poll() is None
    assert reader.process.returncode == 0


def test_closing_twice_is_safe(blobs: BlobRepository) -> None:
    reader = ObjectReader(blobs.root)
    reader.close()
    reader.close()
    assert reader.process.poll() is not None


def test_a_process_that_died_is_reported(blobs: BlobRepository) -> None:
    with ObjectReader(blobs.root) as reader:
        reader.process.kill()
        reader.process.wait()
        with pytest.raises(GitError):
            reader.read(blobs.shas["text"])


def test_an_object_id_holding_a_newline_is_refused(blobs: BlobRepository) -> None:
    """git would read it as two requests, and every answer after it would be wrong."""
    with ObjectReader(blobs.root) as reader:
        with pytest.raises(ValueError):
            reader.read("deadbeef\ndeadbeef")
        assert reader.read(blobs.shas["text"]).content == TEXT


def test_reading_many_objects_starts_one_process(
    blobs: BlobRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the module: fifty objects, one git."""
    counter = _CountingGit()
    monkeypatch.setattr(objects, "subprocess", counter)

    reader = read_objects(blobs.root, [blobs.shas["text"]] * 50)
    assert counter.started == 0, "the process should not start before it is needed"
    assert [item.content for item in reader] == [TEXT] * 50
    assert counter.started == 1


def test_a_process_per_call_is_what_the_single_reader_does(
    blobs: BlobRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which is how the counter above is able to fail: it counts what it is shown."""
    counter = _CountingGit()
    monkeypatch.setattr(objects, "subprocess", counter)
    for _ in range(3):
        read_blob(blobs.root, blobs.shas["text"])
    assert counter.started == 3


def test_abandoning_the_reader_ends_the_process(
    blobs: BlobRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that stops early does not leave a git behind."""
    counter = _CountingGit()
    monkeypatch.setattr(objects, "subprocess", counter)

    answers = read_objects(blobs.root, [blobs.shas["text"]] * 50)
    assert next(answers).content == TEXT
    del answers
    gc.collect()

    assert counter.started == 1
    assert counter.processes[0].poll() is not None
