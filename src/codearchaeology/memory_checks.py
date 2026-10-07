"""Hold a memory's subject and its citations against the evidence.

A memory is a person's statement, and nothing here judges it. What is checked is
only what it *points at*: the subject it says it is about, and the citations it
was made from. Unit 2 (`docs/v0.5-storage-design.md` §4) froze the rules, and
this module is where they are carried out — at admission, where a citation that
names nothing refuses the act, and at read, where the same rules are run again
and their outcome is reported rather than repaired.

**The direction is one way.** This module reads the evidence tables, git and the
co-change analysis; nothing in the evidence layer reads memory. A citation is a
historical reference and not a live link, so a commit a rewrite removed makes a
citation *unresolvable* and never wrong: it stays in the row, and the read says
so.

**Two of the six rules are weaker than the rest, and the freeze says so rather
than hiding it.** A ``definition`` citation resolves when a definition with that
name appears anywhere in the history, because a memory is not about one version
of one file; a ``range`` citation is a span of one commit's diff and therefore
needs a ``commit`` citation beside it — without one there is nothing to hold it
against, and the act refuses it with that sentence.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codearchaeology.cochange import analyze_cochange, load_cochange_commits
from codearchaeology.commit import CommitNotFound, load_commit
from codearchaeology.context import diff_ranges
from codearchaeology.file import normalise
from codearchaeology.memory import Citation, Memory, MemoryStoreError, Subject
from codearchaeology.storage import find_commits

# The four absences the bundle names. ``context.py`` holds the same four words
# and a test keeps the two lists from drifting apart.
ABSENCE_KINDS = (
    "merge_no_diff",
    "parse_failed",
    "no_version_stored",
    "not_in_this_commit",
)

# What a citation's state is when a memory is read back. Three words, because
# "not checked" and "checked and gone" are different answers and a list must not
# imply the second by staying silent.
RESOLVED = "resolved"
UNRESOLVED = "unresolved"
NOT_CHECKED = "not_checked"

# The kinds a list re-checks. ``range`` costs a diff and ``cochange`` costs a
# walk of the history for each citation, so a list leaves those to ``memory
# show`` and says ``not_checked`` on the row. ``absence`` is on this list
# because it is the cheapest of the six rather than one of the dear ones: it is
# a word held against the tool's own four words, with no repository in it, so a
# list reporting it as unchecked would say "we did not look" about the one
# citation that cannot have moved.
CHEAP_KINDS = ("commit", "file", "definition", "absence")

RANGE_FORM = re.compile(r"^(?P<path>.+):(?P<start>\d+)-(?P<end>\d+)$")
COCHANGE_SEPARATOR = " -> "


@dataclass(frozen=True, slots=True)
class Evidence:
    """Where the evidence a memory is held against lives.

    Three things, because the rules need three: the open database for the
    indexed lookups, the repository for the one item the database does not hold
    (a commit's diff), and the database path because the commands that read git
    open their own connection rather than being handed one.
    """

    connection: sqlite3.Connection
    repository_root: Path
    database: Path


def resolve_subject(evidence: Evidence, subject: Subject) -> Subject:
    """The subject as the store holds it, refused when it names nothing.

    A memory nothing can be found by is a memory nobody will read: the subject
    is what a commit's files are matched against, so a path that never appeared
    in the history, a definition that was never read and a commit that is not
    stored are all refused here. What comes back is the resolved form — a
    normalised path, a full commit sha — so what is stored is what the store
    actually holds.
    """
    if subject.kind == "repository":
        return subject

    if subject.kind == "path":
        path = normalise(subject.path)
        if not _path_holds(evidence, path):
            raise MemoryStoreError(_nothing_touched(path))
        return Subject("path", path=path)

    if subject.kind == "definition":
        path = normalise(subject.path)
        if not _path_holds(evidence, path):
            raise MemoryStoreError(_nothing_touched(path))
        if not _definition_under(evidence, path, subject.qualname):
            raise MemoryStoreError(_no_such_definition(evidence, path, subject.qualname))
        return Subject("definition", path=path, qualname=subject.qualname)

    commit = load_commit(evidence.repository_root, evidence.database, subject.commit_sha)
    return Subject("commit", commit_sha=commit.sha)


def resolve_since_commit(evidence: Evidence, ref: str) -> str:
    """The commit a memory's ``since`` names, resolved the way a subject is.

    A start is the author's claim about the project, and a start that names
    nothing is refused at admission like everything else a memory points at: a
    memory is not made to say it has held since a commit the store does not
    hold. What is stored is the full sha, and a commit a *later* rewrite removes
    is another matter — it is not repaired, and the read then says the span
    cannot be shown to cover anything (Unit 2 §6).
    """
    return load_commit(evidence.repository_root, evidence.database, ref).sha


def resolve_citations(evidence: Evidence, citations) -> tuple[Citation, ...]:
    """Hold every citation against the store, refusing the act on the first miss.

    Nothing is written that did not mean something when it was written, and the
    refusal names the citation and what would make it resolvable — a definition
    whose structure has not been read says so, a range with no commit beside it
    says why it needs one.
    """
    commit_ref = _cited_commit(citations)
    for citation in citations:
        if not _holds(evidence, citation, commit_ref):
            raise MemoryStoreError(_refusal(evidence, citation, commit_ref))
    return tuple(citations)


def subject_resolution(evidence: Evidence, subject: Subject) -> str:
    """Whether the thing a memory is about can still be found, checked again.

    The other half of "checked twice": a citation is re-checked and reported,
    and so is the subject. A memory whose file a rewrite removed is kept and the
    reader is told, because the alternative is a block printing a subject as
    though it were still there — and a subject is what a memory is found by, so
    a reader who is not told cannot tell "gone" from "never looked for"
    (Unit 1 §7.2, Unit 2 §4.3).

    **All four kinds are one indexed query**, so unlike a range or a co-change
    citation there is no ``not_checked`` subject: every view checks it.
    """
    if subject.kind == "repository":
        # The project is what the read is for: a memory is only ever read under
        # the repository it was written about, so its least specific subject is
        # the one thing that cannot stop resolving.
        return RESOLVED
    if subject.kind == "path":
        return RESOLVED if _path_holds(evidence, subject.path) else UNRESOLVED
    if subject.kind == "definition":
        # The act's own rule, so what was admitted under it reads under it: a
        # stored definition implies its path is in the history, and a definition
        # that was renamed is a different definition — v0.3's identity rule,
        # which makes this the ordinary case and not an error.
        return (
            RESOLVED
            if _definition_under(evidence, subject.path, subject.qualname)
            else UNRESOLVED
        )
    return RESOLVED if _commit_holds(evidence, subject.commit_sha) else UNRESOLVED


def resolutions(
    evidence: Evidence, citations, *, deep: bool = True
) -> tuple[str, ...]:
    """What happened when each citation was held against the store, once more.

    A citation that stopped resolving is reported and never dropped: a rewrite
    that removed a commit does not make the memory wrong, and the tool does not
    edit what a person wrote to match a repository that moved.
    """
    commit_ref = _cited_commit(citations)
    found = []
    for citation in citations:
        if not deep and citation.kind not in CHEAP_KINDS:
            found.append(NOT_CHECKED)
            continue
        found.append(
            RESOLVED if _holds(evidence, citation, commit_ref) else UNRESOLVED
        )
    return tuple(found)


def since_moment(evidence: Evidence, memory: Memory) -> datetime | None:
    """The moment the commit a memory's ``since`` names was committed.

    The comparison the temporal rule makes is by time — the storage freeze's own
    words — so the reader is shown where the time came from rather than asked to
    trust it, and the section's rule compares against the same value. A commit
    the history no longer holds gives ``None``, which is what makes a memory
    whose start has gone "not provably in force" rather than in force.
    """
    if memory.since_commit_sha is None:
        return None
    matches = find_commits(evidence.connection, memory.since_commit_sha[:7])
    return matches[0].committed_at if matches else None


def since_date_of(evidence: Evidence, memory: Memory) -> str | None:
    """The date that moment falls on, in the committer's own offset."""
    moment = since_moment(evidence, memory)
    return None if moment is None else moment.date().isoformat()


# The six rules, one function each.


def _holds(evidence: Evidence, citation: Citation, commit_ref: str | None) -> bool:
    if citation.kind == "commit":
        return _commit_holds(evidence, citation.ref)
    if citation.kind == "file":
        return _path_holds(evidence, normalise(citation.ref))
    if citation.kind == "definition":
        return _definition_anywhere(evidence, citation.ref)
    if citation.kind == "range":
        return _range_holds(evidence, citation.ref, commit_ref)
    if citation.kind == "cochange":
        return _cochange_holds(evidence, citation.ref)
    return citation.ref in ABSENCE_KINDS


def _path_holds(evidence: Evidence, path: str) -> bool:
    return (
        evidence.connection.execute(
            "SELECT 1 FROM commit_files WHERE path = ? OR old_path = ? LIMIT 1",
            (path, path),
        ).fetchone()
        is not None
    )


def _definition_under(evidence: Evidence, path: str, qualname: str) -> bool:
    return (
        evidence.connection.execute(
            "SELECT 1 FROM definition_versions WHERE path = ? AND qualname = ?"
            " LIMIT 1",
            (path, qualname),
        ).fetchone()
        is not None
    )


def _definition_anywhere(evidence: Evidence, qualname: str) -> bool:
    """A definition citation resolves by name, under any path.

    The weak half of the vocabulary, and the freeze states the weakness: a
    memory is not about one version of one file, so the name is held against
    every definition the history holds. A subject, which *is* about one file,
    requires the path as well.
    """
    return (
        evidence.connection.execute(
            "SELECT 1 FROM definition_versions WHERE qualname = ? LIMIT 1",
            (qualname,),
        ).fetchone()
        is not None
    )


def _range_holds(evidence: Evidence, ref: str, commit_ref: str | None) -> bool:
    span = _range(ref)
    if span is None or commit_ref is None:
        return False
    commit = _cited_commit_object(evidence, commit_ref)
    if commit is None:
        return False
    ranges = diff_ranges(evidence.repository_root, commit)
    if ranges is None:
        return False
    return (span[1], span[2]) in [
        (found.start, found.end) for found in ranges.get(normalise(span[0]), ())
    ]


def _cochange_holds(evidence: Evidence, ref: str) -> bool:
    path, _, partner = ref.partition(COCHANGE_SEPARATOR)
    if not path or not partner:
        return False
    stored = load_cochange_commits(evidence.repository_root, evidence.database)
    for report in analyze_cochange(stored, normalise(path)):
        if any(found.path == normalise(partner) for found in report.co_changes):
            return True
    return False


# Reading the refs, and the refusals.


def _cited_commit(citations) -> str | None:
    """The first commit a set of citations names, which a range is held against."""
    for citation in citations:
        if citation.kind == "commit":
            return citation.ref
    return None


def _commit_holds(evidence: Evidence, ref: str) -> bool:
    """A commit citation resolves on a prefix of at least seven characters.

    The same threshold v0.4's validator uses, and for the same reason: a shorter
    prefix is not an address, it is a coincidence waiting to match something.
    """
    prefix = _prefix(ref)
    if len(prefix) < 7:
        return False
    return bool(find_commits(evidence.connection, prefix))


def _cited_commit_object(evidence: Evidence, ref: str):
    """The commit a range is held against, or ``None`` when it is not stored."""
    try:
        return load_commit(evidence.repository_root, evidence.database, ref)
    except CommitNotFound:
        return None


def _range(ref: str) -> tuple[str, int, int] | None:
    match = RANGE_FORM.match(ref)
    if match is None:
        return None
    return (match.group("path"), int(match.group("start")), int(match.group("end")))


def _prefix(ref: str) -> str:
    """The part of a sha git would accept as a prefix.

    ``find_commits`` turns the value into a ``LIKE`` pattern, so a ref holding
    anything but sha characters must not reach it — the same reason
    ``commit.py`` checks before it queries.
    """
    return "".join(character for character in ref if character in "0123456789abcdefABCDEF")


def _nothing_touched(path: str) -> str:
    return (
        f"nothing in the stored history touched {path};"
        f" use 'archaeology files' to see what is there"
    )


def _no_such_definition(evidence: Evidence, path: str, qualname: str) -> str:
    if not _any_definitions(evidence):
        return (
            f"the stored history has no structure in it, so {qualname!r} cannot be"
            f" checked; run 'archaeology ast' first"
        )
    return (
        f"{path} never held a definition called {qualname!r};"
        f" use 'archaeology structure {path} --history' to see what it held"
    )


def _any_definitions(evidence: Evidence) -> bool:
    return (
        evidence.connection.execute(
            "SELECT 1 FROM definition_versions LIMIT 1"
        ).fetchone()
        is not None
    )


def _refusal(evidence: Evidence, citation: Citation, commit_ref: str | None) -> str:
    """Why a citation did not hold, in the terms of what was written."""
    ref = citation.ref
    if citation.kind == "commit":
        return (
            f"the cited commit {ref!r} is not in the stored history;"
            f" use one from 'archaeology timeline'"
        )
    if citation.kind == "file":
        return f"the cited file {ref!r} was never touched: {_nothing_touched(ref)}"
    if citation.kind == "definition":
        if not _any_definitions(evidence):
            return (
                f"the stored history has no structure in it, so the definition"
                f" {ref!r} cannot be checked; run 'archaeology ast' first"
            )
        return f"no stored version of any file held a definition called {ref!r}"
    if citation.kind == "range":
        if commit_ref is None:
            return (
                f"the range {ref!r} is a span of one commit's diff, so the memory"
                f" has to cite the commit too"
            )
        if _range(ref) is None:
            return f"{ref!r} is not a range; a range is written <path>:<start>-<end>"
        return (
            f"the span {ref!r} is not in the diff of {commit_ref!r} — the diff may"
            f" have been recomputed since, or the commit may be a merge, which git"
            f" prints no diff for"
        )
    if citation.kind == "cochange":
        if COCHANGE_SEPARATOR not in ref:
            return (
                f"{ref!r} is not a co-change pair; a pair is written"
                f" <path> -> <other path>"
            )
        return (
            f"{ref!r} is not in the co-change analysis of that file — the pair may"
            f" share too few commits to be reported"
        )
    return (
        f"{ref!r} is not one of the absence kinds; they are"
        f" {', '.join(ABSENCE_KINDS)}"
    )
