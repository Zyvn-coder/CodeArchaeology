"""Check what the model said before any of it is shown.

This module is the answer to "do not trust the model". It is three passes, in the
order the pipeline names them, and each one is allowed to reject the whole answer:

**Parse.** The text is a JSON object. A fenced block is unwrapped first, because
returning one is a habit rather than a wrong answer; what is inside is still
checked in full.

**Schema.** It has the shape that was asked for, and *only* that shape. A missing
field, a field of the wrong type, a duplicate id and an unknown key are all
refusals. The unknown key is the one worth explaining: a field the schema does not
have is a claim nothing checked, and silently dropping it would mean the answer
shown is not the answer that was verified. ``confidence`` gets its own message
because it is the field most likely to arrive: the tool derives it from
``based_on``, and a model that reports its own is answering a question it was not
asked.

**Semantics.** Every citation names something the bundle actually holds. This is
the pass that makes Evidence First real here rather than decorative — a shape
check alone accepts a confident answer citing a commit that does not exist. The
set of what is allowed is built from the context, so a citation is held against
this commit's evidence rather than against a rule about what a sha looks like.

The same pass holds ``related_memory`` against the ids the model was shown. That
is the one way an answer may point at a memory, and it is checked against the
section rather than the store: a memory the model never saw is refused exactly as
an unseen row's citation is. Nothing else here knows what a memory is — the
caller passes the ids, and memory is never part of the bundle a citation is
checked against.

What none of the three can see is written down in the schema's §6 and repeated
here so nobody has to go looking: a sentence that stays inside a citation it does
name and overstates it anyway is not something a program can catch. The check
covers what can be checked, and says where it stops.
"""

import json
import re

from codearchaeology.context import CommitContext
from codearchaeology.explanation import (
    ABSENCE,
    COCHANGE,
    COMMIT,
    DEFINITION,
    EVIDENCE_KINDS,
    FILE,
    RANGE,
    UNCERTAINTY_KINDS,
    Confidence,
    Evidence,
    Explanation,
    ObservedChange,
    PossibleReason,
    RelatedMemory,
    Uncertainty,
)
from codearchaeology.formatting import SHORT_SHA_LENGTH

# The ids the summary cites, as ``[oc1]``. Bracketed so that a check can find
# them without having to understand the sentence they sit in.
CITATION = re.compile(r"\[([A-Za-z][A-Za-z0-9_-]*)\]")

# Every key the schema has, at every level. A key outside these sets is refused
# rather than ignored: the answer that is shown has to be the answer that was
# checked, and a field nobody reads is a claim nobody verified.
TOP_LEVEL = frozenset(
    {
        "summary",
        "observed_changes",
        "evidence",
        "possible_reasons",
        "uncertainty",
        "related_memory",
    }
)
CHANGE_KEYS = frozenset({"id", "statement", "evidence"})
EVIDENCE_KEYS = frozenset({"id", "kind", "ref", "detail"})
REASON_KEYS = frozenset({"statement", "based_on"})
UNCERTAINTY_KEYS = frozenset({"kind", "detail"})
RELATED_KEYS = frozenset({"memory_id", "note"})

# The one key that gets a message of its own, because it is the one most likely
# to arrive and the one whose presence means the model misunderstood the task.
DERIVED_KEY = "confidence"

DERIVED_KEY_MESSAGE = (
    "the answer reports 'confidence', which the model does not write: the tool"
    " counts it from what each candidate rests on. Remove it and answer again."
)


class ExplanationError(RuntimeError):
    """Raised when the model's answer is not usable as it stands."""


def validate(
    text: str, context: CommitContext, memory_ids: "frozenset[str] | set[str]" = frozenset()
) -> Explanation:
    """Check the model's answer and derive what the tool owns.

    Raises :class:`ExplanationError` naming the check that failed. Nothing is
    repaired: an answer with a citation that names nothing is refused whole,
    because printing the rest of it would present a broken answer as a complete
    one.

    ``memory_ids`` is what the model was shown of the memory section, and it is
    the whole of what an answer may relate itself to — the same rule a citation
    is held to. It is a set of ids rather than the section itself so that this
    module never has to know what a memory is: memory is not evidence, and the
    pass that checks the evidence has no business reading the store.
    """
    document = _parsed(text)
    _schema(document)
    return _semantics(document, context, memory_ids)


# Pass one: the text.


def _parsed(text: str) -> dict:
    """The JSON object the model was asked for, or a failure saying what came."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", stripped).strip()
    try:
        document = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise ExplanationError(f"the answer is not JSON: {error}") from None
    if not isinstance(document, dict):
        raise ExplanationError("the answer is not a JSON object")
    return document


# Pass two: the shape.


def _schema(document: dict) -> None:
    """Refuse anything the schema does not describe, at every level.

    All of it is checked before any of it is read, so a refusal names the first
    thing wrong with the answer rather than the first thing the reader happened
    to need.
    """
    _no_unknown(document, TOP_LEVEL, "the answer")
    if not isinstance(document.get("summary"), str):
        raise ExplanationError("the answer has no summary")

    for position, entry in enumerate(_list(document, "observed_changes")):
        where = f"observed_changes[{position}]"
        entry = _mapping(entry, where)
        _no_unknown(entry, CHANGE_KEYS, where)
        _text(entry, "id", where)
        _text(entry, "statement", where)
        _strings(entry, "evidence", where)

    for position, entry in enumerate(_list(document, "evidence")):
        where = f"evidence[{position}]"
        entry = _mapping(entry, where)
        _no_unknown(entry, EVIDENCE_KEYS, where)
        _text(entry, "id", where)
        kind = _text(entry, "kind", where)
        if kind not in EVIDENCE_KINDS:
            raise ExplanationError(
                f"{where} is of kind {kind!r}, which is not one of"
                f" {', '.join(EVIDENCE_KINDS)}"
            )
        _text(entry, "ref", where)
        if "detail" in entry and not isinstance(entry["detail"], str):
            raise ExplanationError(f"{where}.detail is not text")

    for position, entry in enumerate(_list(document, "possible_reasons")):
        where = f"possible_reasons[{position}]"
        entry = _mapping(entry, where)
        _no_unknown(entry, REASON_KEYS, where)
        _text(entry, "statement", where)
        _strings(entry, "based_on", where)

    for position, entry in enumerate(_list(document, "uncertainty")):
        where = f"uncertainty[{position}]"
        entry = _mapping(entry, where)
        _no_unknown(entry, UNCERTAINTY_KEYS, where)
        kind = _text(entry, "kind", where)
        if kind not in UNCERTAINTY_KINDS:
            raise ExplanationError(
                f"{where} is of kind {kind!r}, which is not one of"
                f" {', '.join(UNCERTAINTY_KINDS)}"
            )
        if "detail" in entry and not isinstance(entry["detail"], str):
            raise ExplanationError(f"{where}.detail is not text")

    for position, entry in enumerate(_list(document, "related_memory")):
        where = f"related_memory[{position}]"
        entry = _mapping(entry, where)
        _no_unknown(entry, RELATED_KEYS, where)
        _text(entry, "memory_id", where)
        _text(entry, "note", where)


def _no_unknown(entry: dict, allowed: frozenset, where: str) -> None:
    unknown = [key for key in entry if key not in allowed]
    if not unknown:
        return
    if DERIVED_KEY in unknown:
        raise ExplanationError(DERIVED_KEY_MESSAGE)
    raise ExplanationError(
        f"{where} has {', '.join(sorted(repr(key) for key in unknown))},"
        f" which the schema does not have; it has"
        f" {', '.join(sorted(allowed))}"
    )


# Pass three: what it names.


def _semantics(
    document: dict, context: CommitContext, memory_ids: "frozenset[str] | set[str]"
) -> Explanation:
    evidence = _evidence(document)
    known = _known(context)
    for item in evidence:
        if not _names_something(item, known):
            raise ExplanationError(
                f"evidence {item.id!r} cites {item.ref!r}, which is not in the"
                f" evidence for this commit"
            )

    by_id = {item.id: item for item in evidence}
    observed = _observed(document, by_id)
    reasons = _reasons(document, observed, by_id)
    summary = _summary(document, observed)
    _no_reason_in_the_summary(summary, reasons)

    return Explanation(
        summary=summary,
        observed_changes=observed,
        evidence=evidence,
        possible_reasons=reasons,
        uncertainty=_uncertainty(document),
        related_memory=_related(document, memory_ids),
    )


def _related(
    document: dict, memory_ids: "frozenset[str] | set[str]"
) -> tuple[RelatedMemory, ...]:
    """The memories the answer related itself to, held against what it was shown.

    A memory is not in the bundle, so this is the only way an answer may point at
    one — and the id has to be one the model actually saw. An id it did not is
    refused for the reason an unseen row's citation is: an answer has to rest on
    what it was given, not on something that happens to be true elsewhere.
    """
    found = []
    for position, item in enumerate(_list(document, "related_memory")):
        where = f"related_memory[{position}]"
        entry = _mapping(item, where)
        _no_unknown(entry, RELATED_KEYS, where)
        memory_id = _text(entry, "memory_id", where)
        if memory_id not in memory_ids:
            raise ExplanationError(
                f"{where} names memory {memory_id!r}, which is not in the memory"
                f" section this answer was shown"
            )
        found.append(
            RelatedMemory(memory_id=memory_id, note=_text(entry, "note", where))
        )

    if len({item.memory_id for item in found}) != len(found):
        raise ExplanationError("two related memories share an id")
    return tuple(found)


def _known(context: CommitContext) -> dict[str, set[str]]:
    """Everything a citation is allowed to name, read out of the context.

    Built from the context rather than from a rule about what a sha looks like,
    so a citation is checked against what this commit's evidence actually holds.
    The two kinds that are not a single name are built in the same form the
    prompt asks for, which is what makes "copied exactly" checkable rather than a
    matter of the model's formatting.

    A ``range`` is one changed span in one file, so a claim about where the
    change landed cannot cite a whole file and call it precise. A ``cochange`` is
    one direction of a pair, because the statistic is not symmetric: "this file's
    commits also touch that one" and the reverse are different numbers.
    """
    commits = {context.sha}
    paths = {change.path for change in context.changes}
    definitions = {definition.qualname for definition in context.definitions}
    ranges = {
        f"{change.path}:{span.start}-{span.end}"
        for change in context.changes
        for span in change.ranges
    }
    cochanges = {
        f"{report.path} -> {partner.path}"
        for report in context.cochange
        for partner in report.partners
    }

    for entry in context.history:
        paths.add(entry.path)
        commits.update(commit.sha for commit in entry.commits)
    for life in context.lifecycle:
        paths.add(life.path)
        paths.update(life.path_history)
    for report in context.cochange:
        paths.add(report.path)
        paths.update(partner.path for partner in report.partners)
    for definition in context.definitions:
        if definition.previous_commit_sha:
            commits.add(definition.previous_commit_sha)

    return {
        COMMIT: commits,
        FILE: paths,
        DEFINITION: definitions,
        RANGE: ranges,
        COCHANGE: cochanges,
        ABSENCE: {absence.kind for absence in context.absences},
    }


def _names_something(item: Evidence, known: dict[str, set[str]]) -> bool:
    """Whether a citation names something the bundle holds.

    A shortened commit sha counts: git itself takes a prefix, every command in
    this tool takes one, and a prefix names the same commit rather than an
    invented one. Nothing else is matched loosely — a path or a definition name
    has to be the one the evidence wrote.
    """
    if item.ref in known[item.kind]:
        return True
    if item.kind == COMMIT and len(item.ref) >= 7:
        return any(commit.startswith(item.ref) for commit in known[COMMIT])
    return False


def _evidence(document: dict) -> tuple[Evidence, ...]:
    found = []
    for position, item in enumerate(_list(document, "evidence")):
        entry = _mapping(item, f"evidence[{position}]")
        found.append(
            Evidence(
                id=_text(entry, "id", f"evidence[{position}]"),
                kind=_text(entry, "kind", f"evidence[{position}]"),
                ref=_text(entry, "ref", f"evidence[{position}]"),
                detail=str(entry.get("detail") or ""),
            )
        )
    if len({item.id for item in found}) != len(found):
        raise ExplanationError("two evidence entries share an id")
    return tuple(found)


def _observed(document: dict, by_id: dict[str, Evidence]) -> tuple[ObservedChange, ...]:
    found = []
    for position, item in enumerate(_list(document, "observed_changes")):
        entry = _mapping(item, f"observed_changes[{position}]")
        where = f"observed_changes[{position}]"
        citations = _strings(entry, "evidence", where)
        if not citations:
            raise ExplanationError(f"{where} cites no evidence")
        for citation in citations:
            if citation not in by_id:
                raise ExplanationError(
                    f"{where} cites {citation!r}, which no evidence entry defines"
                )
        found.append(
            ObservedChange(
                id=_text(entry, "id", where),
                statement=_text(entry, "statement", where),
                evidence=citations,
            )
        )
    if len({item.id for item in found}) != len(found):
        raise ExplanationError("two observed changes share an id")
    return tuple(found)


def _reasons(
    document: dict, observed: tuple[ObservedChange, ...], by_id: dict[str, Evidence]
) -> tuple[PossibleReason, ...]:
    ids = {item.id for item in observed}
    found = []
    for position, item in enumerate(_list(document, "possible_reasons")):
        entry = _mapping(item, f"possible_reasons[{position}]")
        where = f"possible_reasons[{position}]"
        based_on = _strings(entry, "based_on", where)
        if not based_on:
            raise ExplanationError(f"{where} rests on nothing, so it is not offered")
        for name in based_on:
            if name not in ids:
                raise ExplanationError(
                    f"{where} rests on {name!r}, which is not an observed change"
                )
        found.append(
            PossibleReason(
                statement=_text(entry, "statement", where),
                based_on=based_on,
                confidence=_confidence(based_on, observed, by_id),
            )
        )
    return tuple(found)


def _confidence(
    based_on: tuple[str, ...],
    observed: tuple[ObservedChange, ...],
    by_id: dict[str, Evidence],
) -> Confidence:
    """Count what a candidate rests on, and say which commits that came from.

    Derived here rather than asked of the model: a field the model cannot write
    is a field the model cannot inflate, which is what keeps a confidence
    traceable to something a reader can check.
    """
    by_observed = {item.id: item for item in observed}
    commits: list[str] = []
    for name in based_on:
        for citation in by_observed[name].evidence:
            entry = by_id[citation]
            if entry.kind == COMMIT and entry.ref not in commits:
                commits.append(entry.ref)

    return Confidence(
        observed_changes=len(based_on),
        commits=tuple(commit[:SHORT_SHA_LENGTH] for commit in commits),
    )


def _summary(document: dict, observed: tuple[ObservedChange, ...]) -> str:
    text = document["summary"]
    if not text.strip():
        raise ExplanationError("the answer has an empty summary")

    ids = {item.id for item in observed}
    cited = CITATION.findall(text)
    if not cited:
        raise ExplanationError(
            "the summary cites no observed change, so nothing in it can be checked"
        )
    for name in cited:
        if name not in ids:
            raise ExplanationError(
                f"the summary cites {name!r}, which is not an observed change"
            )
    return text.strip()


def _no_reason_in_the_summary(summary: str, reasons: tuple[PossibleReason, ...]) -> None:
    """Refuse a candidate that was smuggled into the summary.

    The weak half of the separation, and it is worth being honest about which
    half it is: this catches a reason repeated verbatim, and it cannot catch a
    reason reworded to fit inside a sentence that cites an observed change. The
    other half of the rule is structural — a reason has a field of its own — and
    the rest is a reader's judgement, which no check replaces.
    """
    for reason in reasons:
        statement = reason.statement.strip().rstrip(".")
        if statement and statement.lower() in summary.lower():
            raise ExplanationError(
                "a possible reason appears in the summary, where only what was"
                " observed may go"
            )


def _uncertainty(document: dict) -> tuple[Uncertainty, ...]:
    found = []
    for position, item in enumerate(_list(document, "uncertainty")):
        entry = _mapping(item, f"uncertainty[{position}]")
        found.append(
            Uncertainty(
                kind=_text(entry, "kind", f"uncertainty[{position}]"),
                detail=str(entry.get("detail") or ""),
            )
        )
    return tuple(found)


# Reading the document, shape by shape.


def _list(document: dict, name: str) -> list:
    value = document.get(name, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExplanationError(f"{name!r} is not a list")
    return value


def _mapping(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise ExplanationError(f"{where} is not an object")
    return value


def _text(entry: dict, name: str, where: str) -> str:
    value = entry.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ExplanationError(f"{where} has no {name!r}")
    return value.strip()


def _strings(entry: dict, name: str, where: str) -> tuple[str, ...]:
    value = entry.get(name, [])
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ExplanationError(f"{where}.{name} is not a list of ids")
    return tuple(item.strip() for item in value)
