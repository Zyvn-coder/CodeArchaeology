"""Run the AST pass: the stored history in, the structure of every version out.

The walk is the same one the file lifecycle already does — commits oldest first,
a file's versions one after another, renames following the file rather than
breaking it — and on top of it each version is read, parsed and compared with the
version before it. What comes out is one row per file version and one row per
definition it held.

Three things a reader of the stored rows has to know:

* **``change_type`` compares against the last version that could be read.** A
  version that fails to parse is recorded as a failure and gets no definition
  rows at all — never a set of deletions, because "nothing could be read" and
  "nothing was there" are different facts. The next version that parses is
  compared against the last one that parsed, so an ``unchanged`` can span a
  failure. It means "this is what the nearest readable version held", not
  "nothing happened in between", and the history layer must not read it as an
  unbroken chain.
* **A definition is identified by its kind and its qualified name, and by
  nothing else.** A function that was renamed is one definition disappearing and
  another appearing, and the pass says exactly that rather than deciding the two
  are related. A definition that is gone simply has no row: absence is the
  evidence, and nothing is written to spell it out.
* **A re-run does not parse what it has already parsed.** A version whose stored
  ``content_sha`` and ``parsed_at_version`` are the ones it is about to work out
  is reused as it stands, so running the pass twice costs a read and no parsing.
  The rows are written again either way, which is what makes a re-run the way to
  pick up a change in how the tool reads code.

Versions are written in batches, each batch one transaction, so an interrupted
run leaves whole versions behind rather than half of one.
"""

import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from codearchaeology.analysis import open_analysis
from codearchaeology.cache import database_path
from codearchaeology.definitions import ParseFailure, definitions_of, interpreter_version
from codearchaeology.history import Commit
from codearchaeology.lifecycle import DELETED, Lifecycle, build_lifecycles
from codearchaeology.objects import ObjectReader, file_version_id
from codearchaeology.storage import (
    CREATED,
    MODIFIED,
    UNCHANGED,
    DefinitionVersion,
    FileVersion,
    read_definition_versions,
    read_file_versions,
    read_stored_commits,
    write_ast_batch,
)

PYTHON_SUFFIX = ".py"

# How many file versions go into one transaction. Large enough that committing is
# not the cost of the run, small enough that an interrupted pass keeps what it
# finished instead of starting over.
BATCH = 500


@dataclass(frozen=True, slots=True)
class AstPassResult:
    """What the pass did, for reporting back to the user."""

    repository_root: Path
    database: Path
    file_versions: int
    definitions: int
    parsed: int
    reused: int
    failed: int
    skipped: int


@dataclass
class _Counts:
    """Tallying while the walk runs, so the summary needs no second pass."""

    file_versions: int = 0
    definitions: int = 0
    parsed: int = 0
    reused: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class _Facts:
    """One definition, from a parse or from a stored row, without its change type."""

    position: int
    kind: str
    qualname: str
    fingerprint: str
    decorators: tuple[str, ...]
    lineno: int
    end_lineno: int


@dataclass(frozen=True, slots=True)
class _Failure:
    """Why one version could not be read."""

    reason: str
    lineno: int | None
    offset: int | None


@dataclass(frozen=True, slots=True)
class _Worked:
    """One version, worked out and waiting to be written."""

    file_version: FileVersion
    definitions: tuple[DefinitionVersion, ...]
    # ``None`` when it could not be read, which is also what tells the walk not
    # to compare the next version against this one.
    facts: tuple[_Facts, ...] | None = None


def run_ast_pass(repository_root, database=None) -> AstPassResult:
    """Read every Python file version the stored history has, and store its structure.

    The history is read from the database, so ``analyze`` has to have run first;
    a repository that has never been analyzed gets the same error every other
    reading command gives.
    """
    repository_root = Path(repository_root).resolve()
    if database is None:
        database = database_path(repository_root)
    database = Path(database)

    counts = _Counts()
    with open_analysis(repository_root, database) as connection:
        commits = read_stored_commits(connection)
        stored = read_file_versions(connection)

        with ObjectReader(repository_root) as reader:
            batch: list[tuple[FileVersion, tuple[DefinitionVersion, ...]]] = []
            for worked in _walk(connection, reader, commits, stored, counts):
                batch.append((worked.file_version, worked.definitions))
                if len(batch) >= BATCH:
                    write_ast_batch(connection, batch)
                    batch.clear()
            if batch:
                write_ast_batch(connection, batch)

    return AstPassResult(
        repository_root=repository_root,
        database=database,
        file_versions=counts.file_versions,
        definitions=counts.definitions,
        parsed=counts.parsed,
        reused=counts.reused,
        failed=counts.failed,
        skipped=counts.skipped,
    )


def _walk(
    connection,
    reader: ObjectReader,
    commits: Iterable[Commit],
    stored: dict[tuple[str, str], FileVersion],
    counts: _Counts,
) -> Iterator[_Worked]:
    """Work out every version to store, in the order the files lived them.

    Each file's versions come one after another, because that is the order the
    comparison needs: a version is compared against the last version of the same
    file that could be read. A file that was renamed stays one file here — the
    lifecycle walk follows it, so the version after a rename is compared against
    the version before it rather than looking like a fresh start.
    """
    version = interpreter_version()

    for life in build_lifecycles(commits):
        previous: tuple[_Facts, ...] | None = None
        for event in life.events:
            if event.change_type == DELETED or not event.path.endswith(PYTHON_SUFFIX):
                continue

            found = reader.read(file_version_id(event.commit_sha, event.path))
            if found is None or found.kind != "blob":
                # A path that is not in that commit, or that names a directory.
                # Counted rather than dropped quietly, and no row is written: a
                # version nobody could read is not a version that was empty.
                counts.skipped += 1
                continue

            cached = stored.get((event.commit_sha, event.path))
            facts, failure = _obtain(connection, found, version, cached, counts)
            definitions: tuple[DefinitionVersion, ...] = ()
            if failure is None:
                definitions = _rows(event.commit_sha, event.path, facts, previous)
                previous = facts

            counts.file_versions += 1
            counts.definitions += len(definitions)
            yield _Worked(
                file_version=FileVersion(
                    commit_sha=event.commit_sha,
                    path=event.path,
                    content_sha=found.sha,
                    parsed_at_version=version,
                    parse_error=None if failure is None else failure.reason,
                    error_lineno=None if failure is None else failure.lineno,
                    error_offset=None if failure is None else failure.offset,
                ),
                definitions=definitions,
                facts=facts if failure is None else None,
            )


def _obtain(
    connection,
    found,
    version: str,
    cached: FileVersion | None,
    counts: _Counts,
) -> tuple[tuple[_Facts, ...], _Failure | None]:
    """The facts of one version: read now, or taken from what is already stored.

    A stored version is reusable exactly when it describes the same bytes read by
    the same interpreter. Anything else — different content, a different Python —
    is parsed again, because the recorded facts say who produced them and a
    different producer is not entitled to their answer.
    """
    if (
        cached is not None
        and cached.content_sha == found.sha
        and cached.parsed_at_version == version
    ):
        counts.reused += 1
        if cached.parse_error is not None:
            return (), _Failure(
                cached.parse_error, cached.error_lineno, cached.error_offset
            )
        rows = read_definition_versions(connection, cached.commit_sha, cached.path)
        return tuple(_facts_of_row(row) for row in rows), None

    result = definitions_of(found.content)
    if isinstance(result, ParseFailure):
        counts.failed += 1
        return (), _Failure(result.reason, result.lineno, result.offset)

    counts.parsed += 1
    return tuple(_facts_of_parse(result)), None


def _facts_of_parse(definitions) -> Iterator[_Facts]:
    """The facts of definitions that were just read, in the order they appear."""
    for position, definition in enumerate(definitions):
        yield _Facts(
            position=position,
            kind=definition.kind,
            qualname=definition.qualname,
            fingerprint=definition.fingerprint,
            decorators=definition.decorators,
            lineno=definition.lineno,
            # The parser reports an end for every node it built from source, so
            # this guard is for the shape of the type rather than for a case that
            # happens: a definition that ends nowhere ends where it starts.
            end_lineno=(
                definition.lineno
                if definition.end_lineno is None
                else definition.end_lineno
            ),
        )


def _facts_of_row(row: DefinitionVersion) -> _Facts:
    """The facts of a definition that was already stored."""
    return _Facts(
        position=row.position,
        kind=row.kind,
        qualname=row.qualname,
        fingerprint=row.fingerprint,
        decorators=row.decorators,
        lineno=row.lineno,
        end_lineno=row.end_lineno,
    )


def _rows(
    commit_sha: str,
    path: str,
    facts: tuple[_Facts, ...],
    previous: tuple[_Facts, ...] | None,
) -> tuple[DefinitionVersion, ...]:
    """The definition rows of one version, with what changed about each."""
    return tuple(
        DefinitionVersion(
            commit_sha=commit_sha,
            path=path,
            position=fact.position,
            kind=fact.kind,
            qualname=fact.qualname,
            change_type=change_type,
            fingerprint=fact.fingerprint,
            decorators=fact.decorators,
            lineno=fact.lineno,
            end_lineno=fact.end_lineno,
        )
        for fact, change_type in zip(facts, _change_types(facts, previous))
    )


def _change_types(
    current: tuple[_Facts, ...], previous: tuple[_Facts, ...] | None
) -> tuple[str, ...]:
    """What each definition of this version is, next to the version before it.

    ``previous`` is the last version of the same file that could be read, or
    ``None`` when there is none — the first readable version of a file, where
    everything in it is created.

    The identity is the kind and the qualified name. Two definitions can share
    one (a name defined again under an ``if``, a fallback in an ``except``), so
    the *n*-th definition with an identity is compared against the *n*-th one
    before it, in the order they appear.
    """
    if previous is None:
        return (CREATED,) * len(current)

    before: dict[tuple[str, str], list[str]] = {}
    for fact in previous:
        before.setdefault((fact.kind, fact.qualname), []).append(fact.fingerprint)

    seen: dict[tuple[str, str], int] = {}
    change_types = []
    for fact in current:
        identity = (fact.kind, fact.qualname)
        index = seen.get(identity, 0)
        seen[identity] = index + 1

        earlier = before.get(identity)
        if earlier is None or index >= len(earlier):
            change_types.append(CREATED)
        elif earlier[index] == fact.fingerprint:
            change_types.append(UNCHANGED)
        else:
            change_types.append(MODIFIED)
    return tuple(change_types)
