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
* **A re-run does not parse what it has already parsed, and does not write what
  it already stored.** A version whose stored ``content_sha`` and
  ``parsed_at_version`` are the ones it is about to work out is reused as it
  stands, so running the pass twice costs a read and no parsing — and the write
  is skipped as well, once the rows it would write are the rows that are already
  there. What still costs is the read of every blob, which is what tells the pass
  whether a version is the one that was stored. A re-run is still the way to pick
  up a change in how the tool reads code: such a change belongs to the analyzer,
  so it bumps ``ANALYZER_VERSION`` and every stored row then fails the reuse
  test.
* **``parsed_at_version`` names the producer, not just the interpreter.** It is
  ``3.13.5+1``: the parser's version and the analyzer's, because the stored
  fingerprints and comparisons are the analyzer's answer as much as the parser's.
  A change to how a definition is rendered or compared bumps the second number,
  and every stored row then fails the reuse test instead of being reused under a
  rule that no longer exists.

Versions are written in batches, each batch one transaction, so an interrupted
run leaves whole versions behind rather than half of one.

The indexes on the two tables are **not** taken out of the way for the write, and
that was measured rather than assumed: the walk writes a file's versions one after
another, which is the order an index on ``(path, ...)`` keeps its keys in, so the
write is an append to those b-trees rather than a scattering over them. An A/B on
the pass's own path — two interleaved runs each way — put dropping them level with
keeping them at both scales (12.4 s against 12.9 s at 10,000 commits, 103.9 s
against 103.2 s at 50,000, the sign of the difference changing between the two). A
replay that wrote the rows by commit instead had reported a 59× difference, and
PROGRESS.md records why that replay was measuring its own row order.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from codearchaeology.analysis import open_analysis
from codearchaeology.cache import database_path
from codearchaeology.definitions import ParseFailure, definitions_of, interpreter_version
from codearchaeology.history import Commit
from codearchaeology.lifecycle import DELETED, build_lifecycles
from codearchaeology.objects import ObjectReader, file_version_id
from codearchaeology.storage import (
    CREATED,
    MODIFIED,
    UNCHANGED,
    DefinitionVersion,
    FileVersion,
    read_all_definition_versions,
    read_file_versions,
    read_stored_commits,
    write_ast_batch,
)

PYTHON_SUFFIX = ".py"

# What the stored rows were produced by, over and above the interpreter. Bump
# this whenever the pass starts answering differently for the same bytes and the
# same Python — a change to how a definition's structure is rendered, to the
# fingerprint, or to the comparison. Without it, a re-run would reuse rows that
# the current code would not have written, and the stored `change_type` would
# quietly keep meaning the old rule.
#
# It is part of `parsed_at_version` rather than a column of its own: one value
# says who produced a row, and the reuse rule compares it whole. A database
# written by an older version of the tool therefore re-reads its versions
# instead of reusing answers the current analyzer is not entitled to.
ANALYZER_VERSION = "1"

# How many file versions go into one transaction. Large enough that committing is
# not the cost of the run, small enough that an interrupted pass keeps what it
# finished instead of starting over.
BATCH = 500


def producer_version() -> str:
    """What a stored row was produced by: the interpreter and the analyzer.

    ``3.13.5+1`` — the parser's version and the analyzer's, which are two
    different things that both have to match before a row may be reused.
    """
    return f"{interpreter_version()}+{ANALYZER_VERSION}"


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
    """One version, worked out and waiting to be written.

    The pair is what the writer needs and all it gets: a file version row and the
    definition rows that belong to it. The facts the walk carried while working
    the version out stay in the walk, because the next version's comparison is
    their only reader.
    """

    file_version: FileVersion
    definitions: tuple[DefinitionVersion, ...]


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
        # One query for every stored definition, rather than one per reused
        # version: the second run reuses all of them, and the per-version query
        # cost more than the parsing it saved. Named for the module it must not
        # shadow — `definitions` is imported above and `_obtain` calls into it.
        stored_definitions = read_all_definition_versions(connection)

        with ObjectReader(repository_root) as reader:
            batch: list[tuple[FileVersion, tuple[DefinitionVersion, ...]]] = []
            for worked in _walk(
                connection, reader, commits, stored, stored_definitions, counts
            ):
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
    stored_definitions: dict[tuple[str, str], tuple[DefinitionVersion, ...]],
    counts: _Counts,
) -> Iterator[_Worked]:
    """Work out every version to store, in the order the files lived them.

    Each file's versions come one after another, because that is the order the
    comparison needs: a version is compared against the last version of the same
    file that could be read. A file that was renamed stays one file here — the
    lifecycle walk follows it, so the version after a rename is compared against
    the version before it rather than looking like a fresh start.

    A version the database already holds exactly is not yielded at all: the
    caller writes what it is given, and handing it a version whose rows are
    already stored would delete and re-insert them for no change. Everything else
    is counted here, so the summary describes the history the pass walked rather
    than the rows it happened to write.
    """
    version = producer_version()

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
            rows = stored_definitions.get((event.commit_sha, event.path))
            facts, failure, reused = _obtain(rows, found, version, cached, counts)
            definitions: tuple[DefinitionVersion, ...] = ()
            if failure is None:
                definitions = _rows(event.commit_sha, event.path, facts, previous)
                previous = facts

            counts.file_versions += 1
            counts.definitions += len(definitions)

            file_version = FileVersion(
                commit_sha=event.commit_sha,
                path=event.path,
                content_sha=found.sha,
                parsed_at_version=version,
                parse_error=None if failure is None else failure.reason,
                error_lineno=None if failure is None else failure.lineno,
                error_offset=None if failure is None else failure.offset,
            )
            if reused and _already_stored(cached, file_version, rows, definitions):
                continue

            yield _Worked(file_version=file_version, definitions=definitions)


def _already_stored(
    cached: FileVersion | None,
    version: FileVersion,
    rows: tuple[DefinitionVersion, ...] | None,
    definitions: tuple[DefinitionVersion, ...],
) -> bool:
    """Whether the database holds exactly what this version would be written as.

    A reused version is one whose bytes and producer the stored row already
    names, so the row is the answer and the only thing that can still differ is
    the comparison: ``change_type`` is worked out against the version *before*
    this one, and a history that moved under a stored row — a commit inserted
    between two others, a rename that no longer follows the same chain — moves
    that comparison without moving this version's own content. Comparing what
    would be written against what is stored is what makes skipping the write
    equivalent to doing it, and a version whose rows differ is written as before.

    ``rows`` is ``None`` when nothing was stored for the version, which is the
    same thing as a version that held no definitions: it was stored holding none.
    """
    if cached != version:
        return False
    return (rows or ()) == definitions


def _obtain(
    rows: tuple[DefinitionVersion, ...] | None,
    found,
    version: str,
    cached: FileVersion | None,
    counts: _Counts,
) -> tuple[tuple[_Facts, ...], _Failure | None, bool]:
    """The facts of one version: read now, or taken from what is already stored.

    A stored version is reusable exactly when it describes the same bytes read by
    the same producer — the same interpreter running the same analyzer. Anything
    else is parsed again, because the recorded facts say who produced them and a
    different producer is not entitled to their answer.

    The third value is whether the facts came from the store, which is what the
    walk needs to know before it can decide that there is nothing to write: a
    reused version is the only kind whose rows can already be there.

    *rows* is that version's stored definitions, already read. It is ``None``
    both when nothing was stored and when the version was stored holding none,
    which needs no telling apart: a reused version with no rows has no facts,
    and that is the same answer either way.
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
            ), True
        return tuple(_facts_of_row(row) for row in rows or ()), None, True

    result = definitions_of(found.content)
    if isinstance(result, ParseFailure):
        counts.failed += 1
        return (), _Failure(result.reason, result.lineno, result.offset), False

    counts.parsed += 1
    return tuple(_facts_of_parse(result)), None, False


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
