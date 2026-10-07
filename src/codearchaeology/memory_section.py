"""Which memories belong beside a commit, and how they are shown.

``explain`` reads one commit. The evidence for it is built deterministically by
``context.py``; this module builds the second thing that goes beside it — the
memories related to that commit — and it is the only place memory and evidence
are put next to each other.

**They stay apart.** The section is not a key of the evidence bundle and not a
part of it: the bundle is what the tool derived, this is what people stated, and
the prompt, the block and the JSON all keep the two under separate labels. A
memory is never citable as evidence, which costs nothing to enforce because
``validation`` checks a citation against the bundle and memory is not in it
(Unit 1 §11.3).

**A memory is related to a commit in one of six ways**, most specific first:
the commit is its subject; a definition this commit changed is; a path this
commit touched is, under the name it has now or any name it had before; it cites
this commit; or its subject is the repository, which is the least specific
subject there is. A memory that matches several takes the most specific one.
Ordering is by that, then by the most recent admission, and the list is capped —
and **every memory dropped is counted**, because a model reading a truncated
list as complete is the failure the v0.4 selection discipline exists for.

**Two groups, and they are not merged.** A memory is *in force* at a commit when
its start is known and at or before that commit and it had not ended by then —
the storage freeze's rule, compared by time rather than by ancestry, with the
limit named there. Everything else related is shown under the second heading
rather than hidden: hiding knowledge is worse than labelling it, and labelling
is worse than merging a rule from 2026 into the reasons for a commit from 2023.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from codearchaeology.context import build_object as context_object
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.memory import INVALIDATED, SUPERSEDED, Memory, read_memories
from codearchaeology.memory_checks import (
    Evidence,
    since_moment,
)
from codearchaeology.memory_view import (
    NOTE,
    Shown,
    author_line,
    memory_object,
    resolution_word,
    shown,
    subject_line,
)

# How many memories a section carries. Small, like the context builder's own
# caps, and for the same reason: the section is an input to a model and a
# convenience to a reader, not a listing — `memory list` is where a whole store
# is read. Everything left out is counted rather than dropped in silence.
CAP = 5

# The ways a memory can be related to the commit being read, most specific
# first. The numbers are ranks, not a vocabulary: a memory takes the lowest one
# it matches, and the order they are written in is the order they are shown in.
COMMIT_SUBJECT = 0
DEFINITION_SUBJECT = 1
PATH_SUBJECT = 2
CITES_COMMIT = 3
REPOSITORY_SUBJECT = 4

# The tool's own minimum for a commit prefix — seven characters, the threshold
# `commit`, `validation` and the memory checks all use. A shorter one is not an
# address, it is a coincidence waiting to match something.
SHA_PREFIX = 7

HEADING = "Memory"
IN_FORCE = "in force at this commit"
NOT_IN_FORCE = "related, not provably in force"

# What the model is told the section is, in as many words, before it reads it.
# The three things it states are the three the definition turns on: a person
# stated this, the tool did not derive it and cannot check it, and the citations
# beside each one *are* checked — which is exactly the difference between the
# statement and the evidence above it.
PROMPT_HEADING = """\
What people stated about this project. These are statements, kept by the tool:
it did not derive them and cannot check them. The citations beside each one are
checked against the evidence above; a statement with none has no evidence behind
it at all.
"""


@dataclass(frozen=True, slots=True)
class Section:
    """The memories related to one commit, split the way the design requires.

    ``omitted`` is how many related memories the cap left out, and it is not
    optional: a section that quietly showed five of forty would read as the
    whole of what is known about the commit.
    """

    in_force: tuple[Shown, ...]
    not_provably_in_force: tuple[Shown, ...]
    omitted: int = 0

    @property
    def all(self) -> tuple[Shown, ...]:
        """Every memory the section shows, in the order they were selected."""
        return self.in_force + self.not_provably_in_force

    @property
    def count(self) -> int:
        return len(self.in_force) + len(self.not_provably_in_force)

    @property
    def empty(self) -> bool:
        return self.count == 0


def build_section(
    evidence: Evidence, context, repository_path, *, cap: int = CAP
) -> "Section | None":
    """The memories related to *context*'s commit, or ``None`` when there are none.

    ``None`` rather than an empty section, because the design's rule is that an
    absent section is the statement "this commit's files carry no stored
    memory" — and it is what keeps the offline output of a repository with no
    memories byte-identical to v0.4's.

    Ended memories are read as well as active ones: reading an old commit means
    reading what was in force *then*, and a rule that has since been superseded
    is the most interesting thing an old commit can be shown with.
    """
    memories = read_memories(evidence.connection, repository_path, include_ended=True)
    related = [
        (rank, memory)
        for memory in memories
        if (rank := _relation(context, memory)) is not None
    ]
    if not related:
        return None

    ordered = sorted(
        related,
        key=lambda pair: (
            pair[0],
            -pair[1].admitted_at.timestamp(),
            pair[1].memory_id,
        ),
    )
    chosen = ordered[:cap]
    # One pass, one temporal check each: the rule reads a commit for a
    # since-commit, and asking it twice per memory would be two reads.
    in_force: list[Shown] = []
    later: list[Shown] = []
    for _, memory in chosen:
        entry = shown(evidence, memory)
        group = in_force if _in_force(evidence, memory, context) else later
        group.append(entry)

    return Section(
        in_force=tuple(in_force),
        not_provably_in_force=tuple(later),
        omitted=len(ordered) - len(chosen),
    )


def section_object(section: "Section | None") -> "dict | None":
    """The section as JSON, or ``None`` when there is nothing to show.

    One object for all four paths — the offline JSON, the model's prompt, the
    envelope and the block — and each memory in it is the canonical memory
    object, so a program reading a memory here reads the same shape it reads
    from ``memory show``.
    """
    if section is None or section.empty:
        return None

    found = {
        "in_force": [memory_object(entry) for entry in section.in_force],
        "not_provably_in_force": [
            memory_object(entry) for entry in section.not_provably_in_force
        ],
    }
    if section.omitted:
        found["selection"] = {"shown": section.count, "omitted": section.omitted}
    return found


def context_json(context, section: "Section | None") -> str:
    """The offline answer: the bundle object, with the memory key beside it.

    The key is added here rather than in ``context.py`` because the evidence
    layer must not know that memory exists. The bundle is built exactly as v0.4
    built it and this puts a second thing next to it, so with nothing to show
    the bytes are the ones ``context.build_json`` has always printed.
    """
    document = context_object(context)
    memories = section_object(section)
    if memories is not None:
        document["memory"] = memories
    return json.dumps(document, indent=2, ensure_ascii=False)


def prompt_section(section: "Section | None") -> str:
    """What the model is shown of the memories, after the evidence.

    A section of its own rather than a key of the bundle: the separation the
    phase is built on has to be visible in the prompt, because that is the one
    place the model could take a statement for a fact.
    """
    memories = section_object(section)
    if memories is None:
        return ""
    return (
        f"\n{PROMPT_HEADING}\nThe memories:\n\n"
        f"{json.dumps(memories, indent=2, ensure_ascii=False)}\n"
    )


def related_ids(section: "Section | None") -> frozenset[str]:
    """The ids the answer may relate itself to — what the model was shown.

    The check is against the section and not against the store: an answer has to
    rest on what it was given, and a memory the model never saw is refused
    exactly as a citation of an unseen row is.
    """
    if section is None:
        return frozenset()
    return frozenset(entry.memory.memory_id for entry in section.all)


def render_section(section: "Section", notes: "dict[str, str] | None" = None) -> str:
    """The section as the block shows it, after the answer.

    Each row states its span, so a reader cannot take a rule from 2026 for the
    reason of a commit from 2023; the statement is the stored text and the tool
    prints it, never the model; and when the answer related itself to a memory,
    the model's own sentence is printed under that statement, marked as the
    reading it is. A group with nothing in it prints no heading — the count in
    the heading above already says it.
    """
    related = dict(notes or {})
    lines = [
        f"{HEADING} ({len(section.in_force)} in force,"
        f" {len(section.not_provably_in_force)} related)",
        "",
    ]
    for heading, entries in (
        (IN_FORCE, section.in_force),
        (NOT_IN_FORCE, section.not_provably_in_force),
    ):
        if not entries:
            continue
        lines.append(f"  {heading}")
        for entry in entries:
            lines.extend(_row(entry, related.get(entry.memory.memory_id)))
        lines.append("")

    if section.omitted:
        lines.append(_dropped(section.omitted))
        lines.append("")

    lines.append(f"  {NOTE}")
    return "\n".join(lines)


def related_notes(explanation) -> dict[str, str]:
    """The model's sentences, by the memory each one is about."""
    return {item.memory_id: item.note for item in explanation.related_memory}


# The six relations, and the temporal rule.


def _relation(context, memory: Memory) -> "int | None":
    """How this memory is related to the commit, or ``None`` when it is not.

    The order of the tests is the order of the ranks, so a memory that matches
    more than one takes the most specific it matches.
    """
    subject = memory.subject
    if subject.kind == "commit" and _names(subject.commit_sha, context.sha):
        return COMMIT_SUBJECT
    if subject.kind == "definition" and subject.qualname in _definitions_named(context):
        return DEFINITION_SUBJECT
    if subject.kind == "path" and subject.path in _paths_touched(context):
        return PATH_SUBJECT
    if _cites_commit(memory, context.sha):
        return CITES_COMMIT
    if subject.kind == "repository":
        return REPOSITORY_SUBJECT
    return None


def _names(ref: "str | None", sha: str) -> bool:
    """Whether *ref* is a commit prefix naming *sha*."""
    return ref is not None and len(ref) >= SHA_PREFIX and sha.startswith(ref)


def _paths_touched(context) -> set[str]:
    """Every name the commit touched, including the names those files had before.

    A rename counts under both of its names, and a file's earlier names count
    too: a memory about ``app.py`` is a memory about the file the lifecycle
    follows, which is the whole point of v0.2's identity rule. A path that was
    deleted is still a path this commit touched.
    """
    paths: set[str] = set()
    for change in context.changes:
        paths.add(change.path)
        if change.old_path:
            paths.add(change.old_path)
    for life in context.lifecycle:
        paths.add(life.path)
        paths.update(life.path_history)
    return paths


def _definitions_named(context) -> set[str]:
    """The definitions this commit's own changes name.

    Matched by qualified name, which is the looseness the ``definition``
    citation already has and the storage freeze states: a memory is not about
    one version of one file.
    """
    return {definition.qualname for definition in context.definitions}


def _cites_commit(memory: Memory, sha: str) -> bool:
    return any(
        citation.kind == "commit" and _names(citation.ref, sha)
        for citation in memory.citations
    )


def _in_force(evidence: Evidence, memory: Memory, context) -> bool:
    """The storage freeze's rule, exactly (``docs/v0.5-storage-design.md`` §6).

    The start is known and at or before the commit, and the memory had not ended
    by then. Both comparisons are by time rather than by ancestry, which is the
    decision the freeze made with its cost named; a start the store can no
    longer find — a since-commit a rewrite removed — is not provably in force.
    """
    committed = datetime.fromisoformat(context.committed_at)

    if memory.since_commit_sha:
        moment = since_moment(evidence, memory)
        if moment is None or moment > committed:
            return False
    elif memory.since_date:
        # Compared as the commit recorded it: the first ten characters of
        # committed_at, which is the date in the committer's own offset, at day
        # granularity and inclusive at both ends of the day.
        if memory.since_date > context.committed_at[:10]:
            return False
    else:
        return False

    return memory.ended_at is None or memory.ended_at >= committed


# The lines a row is made of.


def _row(entry: Shown, note: "str | None") -> list[str]:
    memory = entry.memory
    lines = [
        f"    {subject_line(memory.subject)}  {memory.memory_id[:SHORT_SHA_LENGTH]}"
        f"  {_span(memory)}"
    ]
    lines.extend(f"      {line}" for line in memory.statement.splitlines())
    lines.append(
        f"      stated by {author_line(memory)}, admitted"
        f" {memory.admitted_at.date().isoformat()}"
    )
    lines.append(f"      evidence: {_evidence(entry)}")
    if note:
        # The statement above is the stored text; this is the model's sentence
        # about it, marked so a reader can tell which of the two they are
        # reading. A model never paraphrases a memory into the record.
        lines.append(f"      related by the model: {note}")
    return lines


def _span(memory: Memory) -> str:
    """When the statement held, in the terms the store actually keeps.

    An ended memory says what ended it rather than claiming a start it may not
    have: "in force since" is a claim about the project, and a memory whose
    author gave no start has no such claim to make.
    """
    if memory.state == SUPERSEDED:
        return (
            f"superseded {_date(memory.ended_at)}, replaced by"
            f" {memory.superseded_by[:SHORT_SHA_LENGTH]}"
        )
    if memory.state == INVALIDATED:
        return f"invalidated {_date(memory.ended_at)}: {memory.end_reason}"
    if memory.since_commit_sha:
        return f"in force since {memory.since_commit_sha[:SHORT_SHA_LENGTH + 4]}"
    if memory.since_date:
        return f"in force since {memory.since_date}"
    return "start unknown"


def _evidence(entry: Shown) -> str:
    """The citations with their state, every one of them.

    Unlike a list's one-line summary, the section states the resolution of each
    citation even when it resolves: this section is what the model reasons over,
    and "checked and there" is as much a fact about a statement as "checked and
    gone".
    """
    if not entry.memory.citations:
        return "no evidence attached"
    return ", ".join(
        f"{citation.kind} {citation.ref} ({resolution_word(resolution)})"
        for citation, resolution in zip(
            entry.memory.citations, entry.resolutions, strict=True
        )
    )


def _dropped(omitted: int) -> str:
    if omitted == 1:
        return "  1 more related memory was not shown."
    return f"  {omitted} more related memories were not shown."


def _date(when) -> str:
    return "unknown" if when is None else when.date().isoformat()
