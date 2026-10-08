"""Render a memory: the block a person reads, and the object a program reads.

One shape in three places — the list, the single memory, and the section
``explain`` prints — so a reader never has to work out which of them they got.
The rules are Unit 3's (`docs/v0.5-cli-design.md` §8 and §9); what is decided
here is only how the words fall on the page.

:func:`shown` is here too, because the record and its rendering are one shape:
it is the one place a memory is read back — subject, citations, since-commit —
so the commands and the section cannot answer differently about the same row.

**The block ends by saying what a memory is.** That is the project's own device —
``files`` ends by saying what a deleted row means, ``cochange`` by saying what a
score is not — and the misreading it prevents is the one this whole layer is
responsible for: a memory is a person's statement, the tool did not derive it,
and a citation that checks out does not make the sentence true. The sentence
names all three, and it is printed wherever a memory is shown to a person.

**A statement is never cut silently.** A list prints the beginning of a long
statement and says that it did, naming the command that prints the rest; the JSON
never cuts at all. Half a statement reads as the whole claim, which is the same
failure as a silently truncated diff.

**The subject is re-checked, and it is the one check every view pays for.** A
memory about a file a rewrite removed is kept and marked: the block and the list
both say the thing it is about is no longer in this history, and the object
carries the same word beside the subject. All four subject kinds are one indexed
query, so where a range or a co-change citation can read ``not checked`` in a
list, a subject never does — it is checked wherever a memory is shown.
"""

import json
from dataclasses import dataclass

from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.memory import Memory, Subject
from codearchaeology.memory_checks import (
    NOT_CHECKED,
    RESOLVED,
    UNRESOLVED,
    resolutions,
    since_date_of,
    subject_resolution,
)

NOTE = (
    "A memory is what a person stated about this project: the tool did not derive"
    " it and cannot check it. The citations are checked; the statements are not."
)

# How much of a statement a list prints before it stops, and how much of a sha
# it prints beside it. Both are presentation: the JSON carries the whole
# statement and the whole id.
STATEMENT_WIDTH = 160
INDENT = "   "

# The three states a citation can be read back in, in the words a person reads.
RESOLUTION_WORDS = {
    "resolved": "resolved",
    UNRESOLVED: "no longer in this history",
    NOT_CHECKED: "not checked",
}


@dataclass(frozen=True, slots=True)
class Shown:
    """One memory with what was found when it was read back.

    ``resolutions`` has one entry per citation, in the memory's own order, and
    neither it nor ``subject_resolution`` is optional: a memory rendered without
    one would print its citations or its subject as though they had been
    checked, which is the one thing the read side must not imply.
    """

    memory: Memory
    resolutions: tuple[str, ...]
    subject_resolution: str
    since_date: str | None = None


def shown(evidence, memory: Memory, *, deep: bool = True) -> Shown:
    """Read one memory back: its subject, its citations and its since-commit.

    The one place the record is assembled, so the commands and the section
    ``explain`` prints cannot answer differently about the same memory.
    ``deep`` is the citations' business alone: a range costs a diff and a
    co-change pair costs a walk of the history, so a list leaves those two and
    says so; the subject is a single indexed query and is always checked.
    """
    return Shown(
        memory=memory,
        resolutions=resolutions(evidence, memory.citations, deep=deep),
        subject_resolution=subject_resolution(evidence, memory.subject),
        since_date=since_date_of(evidence, memory),
    )


def resolution_word(state: str) -> str:
    """A citation's or a subject's state in the words a person reads."""
    return RESOLUTION_WORDS.get(state, state)


def subject_line(subject: Subject) -> str:
    """What a memory is about, in one line."""
    if subject.kind == "repository":
        return "the project"
    if subject.kind == "definition":
        return f"{subject.path} :: {subject.qualname}"
    if subject.kind == "commit":
        return f"commit {subject.commit_sha[:SHORT_SHA_LENGTH]}"
    return subject.path or ""


def author_line(memory: Memory) -> str:
    """Who stated it, or the word for not knowing.

    An author the tool could not determine is ``unknown`` — a word rather than a
    blank, because a blank reads as a field somebody forgot.
    """
    if memory.author_name and memory.author_email:
        return f"{memory.author_name} <{memory.author_email}>"
    if memory.author_name:
        return memory.author_name
    if memory.author_email:
        return f"<{memory.author_email}>"
    return "unknown"


def build_list(entries, *, hidden: int = 0) -> str:
    """The memories as the terminal shows them, newest admission first."""
    if not entries:
        return "\n".join(
            ["Memories (0)", "", "No memories are kept for this project.", "", NOTE]
        )

    lines = [f"Memories ({len(entries)})", ""]
    for position, entry in enumerate(entries, start=1):
        lines.append(f"{position}. {_subject_line(entry)}")
        lines.extend(f"{INDENT}{line}" for line in _statement_lines(entry.memory))
        lines.append(f"{INDENT}{_state_line(entry.memory)}")
        lines.append(f"{INDENT}evidence: {_evidence_line(entry)}")
        lines.append("")

    if hidden:
        plural = "memory" if hidden == 1 else "memories"
        lines.append(f"{hidden} more {plural}. Use --all to see them.")
        lines.append("")

    lines.append(NOTE)
    return "\n".join(lines)


def build_show(shown: Shown, successor: Memory | None = None) -> str:
    """One memory in full, with every citation checked and its lifecycle named."""
    memory = shown.memory
    lines = [
        f"Memory      {memory.memory_id}",
        f"Repository  {memory.repository_path}",
        f"Subject     {_subject_line(shown)}",
        f"Author      {author_line(memory)}",
        f"Admitted    {_moment(memory.admitted_at)}",
        f"State       {memory.state}",
        f"Since       {_since_line(shown)}",
        "",
    ]
    lines.extend(_statement_lines(memory))
    lines.append("")

    if memory.citations:
        lines.append(f"Evidence ({len(memory.citations)})")
        for citation, resolution in zip(
            memory.citations, shown.resolutions, strict=True
        ):
            lines.append(
                f"  {citation.kind:<12}{citation.ref}  {_resolution(resolution)}"
            )
    else:
        lines.append("Evidence    none attached")

    lines.append("")
    lines.append(f"Lifecycle   {_lifecycle_line(memory, successor)}")
    lines.append("")
    lines.append(NOTE)
    return "\n".join(lines)


def memory_object(shown: Shown) -> dict:
    """One memory as the object every memory output carries.

    The statement is whole and the id is whole: this is the form a program
    reads, and a program that asked for a memory asked for all of it.
    """
    memory = shown.memory
    return {
        "memory_id": memory.memory_id,
        "repository": memory.repository_path,
        "statement": memory.statement,
        "author": author_object(memory),
        "admitted_at": memory.admitted_at.isoformat(),
        "state": memory.state,
        "subject": _subject_object(memory.subject, shown.subject_resolution),
        "since": _since_object(shown),
        "ended_at": None if memory.ended_at is None else memory.ended_at.isoformat(),
        "end_reason": memory.end_reason,
        "superseded_by": memory.superseded_by,
        "supersedes": memory.supersedes,
        "citations": [
            {"kind": citation.kind, "ref": citation.ref, "resolution": resolution}
            for citation, resolution in zip(
                memory.citations, shown.resolutions, strict=True
            )
        ],
    }


def build_list_json(entries, *, repository, head_sha: str, foreign=()) -> str:
    """Every memory of the repository, whatever ``--limit`` said.

    ``--limit`` is a terminal convenience and a program that asked for the list
    asked for all of it — the rule ``files --json`` already follows. The
    other repositories' memories are reported rather than shown or dropped: a
    database holds one repository's memories, and a program should be able to
    tell that this one holds more than it is reading.
    """
    found = {
        "repository": str(repository),
        "head_sha": head_sha,
        "memories": [memory_object(entry) for entry in entries],
    }
    count, paths = foreign
    if count:
        found["other_repositories"] = {"count": count, "paths": list(paths)}
    return json.dumps(found, indent=2, ensure_ascii=False)


def build_show_json(shown: Shown) -> str:
    """One memory as JSON — the object, and nothing wrapped around it."""
    return json.dumps(memory_object(shown), indent=2, ensure_ascii=False)


# The lines a block is made of.


def _statement_lines(memory: Memory) -> list[str]:
    """The statement, whole or cut with the cut stated.

    The marker names the command that prints the rest, so a reader who wants the
    whole claim has somewhere to go rather than a sentence that quietly stopped.
    """
    text = memory.statement
    if len(text) > STATEMENT_WIDTH:
        text = (
            text[:STATEMENT_WIDTH].rstrip()
            + f"\n\u2026 use 'archaeology memory show"
            f" {memory.memory_id[:SHORT_SHA_LENGTH]}' for the whole statement"
        )
    return text.splitlines()


def _state_line(memory: Memory) -> str:
    """The one line that says what this memory is, and since when."""
    if memory.state == "invalidated":
        return (
            f"{_short(memory.memory_id)}  invalidated {_moment(memory.ended_at)}:"
            f" {memory.end_reason}"
        )
    if memory.state == "superseded":
        return (
            f"{_short(memory.memory_id)}  superseded {_moment(memory.ended_at)},"
            f" replaced by {_short(memory.superseded_by)}"
        )
    line = f"{_short(memory.memory_id)}  active  admitted {_moment(memory.admitted_at)}"
    if memory.since_commit_sha:
        line += f"  since {memory.since_commit_sha[:SHORT_SHA_LENGTH + 4]}"
    elif memory.since_date:
        line += f"  since {memory.since_date}"
    return line


def _evidence_line(entry: Shown) -> str:
    """The citations, with a word on any that is not simply there.

    A citation that stopped resolving and one that was not checked are marked,
    because an unmarked list reads as "all of these are fine" — and "none
    attached" is said in words rather than shown as an empty list.
    """
    if not entry.memory.citations:
        return "none attached"

    parts = []
    for citation, resolution in zip(
        entry.memory.citations, entry.resolutions, strict=True
    ):
        mark = ""
        if resolution != "resolved":
            mark = f" ({_resolution(resolution)})"
        parts.append(f"{citation.kind} {citation.ref}{mark}")
    return ", ".join(parts)


def _since_line(shown: Shown) -> str:
    """When the statement started holding, or the word for not knowing.

    A start the author did not give is ``unknown`` and never the admission time:
    the two are different facts, one about the project and one about the store.
    """
    memory = shown.memory
    if memory.since_commit_sha:
        if shown.since_date:
            return f"{memory.since_commit_sha[:SHORT_SHA_LENGTH + 4]} ({shown.since_date})"
        return memory.since_commit_sha[:SHORT_SHA_LENGTH + 4]
    if memory.since_date:
        return memory.since_date
    return "unknown"


def _lifecycle_line(memory: Memory, successor: Memory | None) -> str:
    """What this memory replaced, what replaced it, and how it ended."""
    parts = []
    if memory.supersedes:
        parts.append(f"supersedes {_short(memory.supersedes)}")
    if memory.superseded_by:
        if successor is None:
            parts.append(f"superseded by {_short(memory.superseded_by)}")
        else:
            parts.append(
                f"superseded by {_short(memory.superseded_by)},"
                f" admitted {_moment(successor.admitted_at)}"
            )
    if memory.state == "invalidated":
        parts.append(f"ended {_moment(memory.ended_at)}: {memory.end_reason}")
    return "; ".join(parts) if parts else "nothing supersedes it"


def author_object(memory: Memory) -> dict | None:
    """Who stated it, or ``None`` when the tool could not tell.

    Public, with :func:`subject_object` and :func:`since_object` below, for one
    reason: the export file carries the same fields as this object, and the one
    thing that must not happen is two modules each deciding what a memory's
    fields are. Export imports these instead of restating them, so a field added
    here cannot go missing there — and the fields it must *not* have are added
    by the three private helpers below rather than being dropped there.
    """
    if memory.author_name is None and memory.author_email is None:
        return None
    return {"name": memory.author_name, "email": memory.author_email}


def subject_object(subject: Subject) -> dict:
    """What a memory is about, and nothing about whether it still resolves."""
    if subject.kind == "definition":
        return {"kind": "definition", "path": subject.path, "qualname": subject.qualname}
    if subject.kind == "commit":
        return {"kind": "commit", "commit": subject.commit_sha}
    if subject.kind == "path":
        return {"kind": "path", "path": subject.path}
    return {"kind": "repository"}


def since_object(memory: Memory) -> dict | None:
    """When the author said the statement started holding, or ``None``.

    ``None`` is unknown and never "from the beginning". The cited commit's own
    date is not here: it is read out of the stored commits, so it belongs beside
    the rendering that compares times and not in a file that leaves the machine.
    """
    if memory.since_commit_sha:
        return {"commit": memory.since_commit_sha}
    if memory.since_date:
        return {"date": memory.since_date}
    return None


def _subject_line(shown: Shown) -> str:
    """What a memory is about, with the word for a thing that is not there.

    The same marker a citation carries, because it is the same answer: what this
    points at cannot be found in the stored history any more. A resolved subject
    prints exactly as it always did, which is the point — the marker is a fact
    about the repository, not decoration.
    """
    line = subject_line(shown.memory.subject)
    if shown.subject_resolution == RESOLVED:
        return line
    return f"{line}  ({_resolution(shown.subject_resolution)})"


def _subject_object(subject: Subject, resolution: str) -> dict:
    """The subject with the answer to "does this still point at something".

    The base is :func:`subject_object`'s, so the two cannot disagree about what
    a subject is; what this adds is the one field the evidence layer answers.
    """
    found = subject_object(subject)
    found["resolution"] = resolution
    return found


def _since_object(shown: Shown) -> dict | None:
    """The start of a memory's span, and where the time came from.

    A commit carries its own recorded date beside it, because the temporal rule
    compares by time and a reader should be able to see what was compared.
    """
    found = since_object(shown.memory)
    if found is not None and "commit" in found:
        found["commit_date"] = shown.since_date
    return found


def _resolution(state: str) -> str:
    return resolution_word(state)


def _moment(when) -> str:
    """A time as the blocks print it, or the word for one that is not there."""
    if when is None:
        return "unknown"
    return when.strftime("%Y-%m-%d %H:%M:%S %z")


def _short(memory_id: str | None) -> str:
    return "" if memory_id is None else memory_id[:SHORT_SHA_LENGTH]
