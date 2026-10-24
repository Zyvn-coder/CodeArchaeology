"""The answer: what the model is asked for, and how it is shown.

Two jobs, and the third one — deciding whether an answer is usable — belongs to
``validation.py``, which is a unit of its own because it is the part that does not
trust the model.

**Ask.** :func:`build_prompt` puts the instructions around the evidence. The
instructions carry no facts — every fact is in the context — so what the offline
path prints and what the model is shown are the same evidence, except where the
commit is too large to send whole: ``selection`` reduces the model's view and
states every row it left out, and the offline path keeps printing the bundle.

**Show.** :func:`render` prints the three kinds under three headings, so the seam
between what was observed and what is only possible is in the structure rather
than in the wording. The heading is the only thing telling a reader which is
which, which is why the model cannot put a candidate under ``Observed``: the
renderer decides where each list goes.

The block opens by saying what the whole of it is — an interpretation of the
evidence and not a record of what happened — because that is the contract this
layer is held to: what the tool derives is a fact with a test behind it, and what
a model writes from those facts is a reading of them. The renderer states it,
the validator enforces what can be enforced, and `docs/v0.4-problem-definition.md`
§12 is where the distinction is written down.

The types below are the schema itself. ``validation`` fills them in from the
model's text; this module never reads that text.
"""

import hashlib
import json
from dataclasses import dataclass

from codearchaeology.context import build_object as context_object

OBSERVED = "Observed"
POSSIBLE = "Possible"
UNKNOWN = "Unknown"

# What a citation may name, one word per section of the bundle. The check that a
# citation names something real is ``validation``'s; these are the words both
# modules agree on, and each one points at a part of the context a reader can go
# and look at.
COMMIT = "commit"
FILE = "file"
DEFINITION = "definition"
RANGE = "range"
COCHANGE = "cochange"
ABSENCE = "absence"
# v0.6's range unit. It is not called ``range`` because that kind has meant a
# changed span in one file since this schema was written, and one word holding
# two meanings is the ambiguity the validator exists to refuse.
CHANGESET = "changeset"

EVIDENCE_KINDS = (COMMIT, FILE, DEFINITION, RANGE, COCHANGE, ABSENCE, CHANGESET)

# How the three kinds that are not a single name are written, so that a citation
# can be copied exactly and checked against the bundle rather than parsed.
# ``range`` is a changed span in a file and ``cochange`` is one direction of a
# co-change pair — the direction matters, because the statistic is not symmetric.
RANGE_FORM = "<path>:<start>-<end>"
COCHANGE_FORM = "<path> -> <partner>"
CHANGESET_FORM = "<base>..<tip>"

# The absences the evidence contract names — four from v0.4, the binary one the
# contract's v0.6 amendment adds — under the context builder's names, plus the
# one a definition's disappearance is reported as. The bundle's own absence
# vocabulary and the answer's must stay one list: the day the bundle can show a
# kind the answer cannot name is the day an honest answer is refused.
UNCERTAINTY_KINDS = (
    "merge_no_diff",
    "parse_failed",
    "no_version_stored",
    "not_in_this_commit",
    "binary_no_text",
    "definition_gone",
)

# The two sentences that stand where a check cannot. Both are the project's own
# device — the sentence that prevents the one misreading, printed where the
# misreading would happen, the way `files` ends by saying what a deleted row
# means and `cochange` ends by saying what a score is not.
#
# ``INTERPRETATION_NOTE`` opens the block because the misreading it prevents is
# available before the first word is read: that an answer about a commit is a
# record of that commit. It is not, and the difference is exactly what the
# citations are for — so the sentence says which half is checked and which half
# is the model's, rather than asking the reader to be suitably sceptical.
#
# ``CANDIDATES_NOTE`` is printed whenever there is a candidate at all, because
# every one of them is a reading. ``ONLY_CORRELATION`` is printed only for a
# candidate whose entire support is a statistic, which is the shape a reader is
# most likely to take for a finding.
INTERPRETATION_NOTE = (
    "This is an interpretation of the evidence, not a record of what happened:"
    " the citations below are checked against the bundle, and the sentences"
    " around them are not."
)
CANDIDATES_NOTE = (
    "A candidate is a reading of the evidence above, not a finding: moving"
    " together is not a dependency, and a definition appearing or changing is not"
    " a statement about what the author meant."
)
ONLY_CORRELATION = (
    "everything behind this one is a statistic over the file's history — that they"
    " move together, which is a correlation and not a dependency"
)

INSTRUCTIONS = """\
You are explaining one commit of a Python repository, using only the evidence
below. Answer with a single JSON object and nothing else.

The evidence is what a tool read out of git and out of its own analysis. It is
the whole of what you may use.

Rules, and an answer that breaks one is rejected rather than corrected:

1. Report only what the evidence holds. A claim that cannot be pointed at
   something in the evidence is not to be made at all.
2. Never state why the author made the change. The reason is not in the
   evidence, and no wording makes it available.
3. A candidate reason is a candidate. Put it in `possible_reasons`, never in
   `summary`, and name the observed changes it rests on.
4. If the evidence is thin, say so in `uncertainty` rather than filling the gap.
5. Do not predict, score or judge.
6. Send exactly the fields below and no others. In particular do not report a
   confidence: the tool counts how much evidence each candidate rests on, and a
   number from you is not something it can check.
7. The evidence may carry a `selection`. It means the commit was too large to
   send whole: each list then holds its largest entries, and every count in that
   block says how many were left out of the list it sits under. A file row
   carrying `ranges_total` holds only its first spans. Never read a list as
   complete when a `selection` is present, and never make a claim about what is
   not shown.
8. After the evidence there may be a `memories` section. It is what people
   stated about this project: the tool keeps those statements, did not derive
   them, and cannot check them. **They are not evidence.** Never cite one in
   `evidence`, never put one in `observed_changes`, and never write one into
   `uncertainty` as something that was determined. The one place a memory
   belongs is `possible_reasons`, where a candidate is offered and not asserted.
9. Never present a memory as something that was observed. "The history shows X"
   is a claim about the evidence; if X is only in a memory, say the people
   involved recorded it, and let it be a candidate reason.
10. If a memory bears on your answer, name it in `related_memory` by its
    `memory_id`, with one sentence saying what the answer takes from it. An id
    that is not in the section you were shown is a rejected answer. `related_memory`
    may be empty, and it must be empty when nothing in the section bears on this
    commit.
11. The evidence may be for one commit or for a range of commits, and it says
    which under `commit` or `range`. In a range the commits are listed oldest
    first; a path may appear under several commits and its row is the whole of
    what the set did to it; and each file's `diff` is the net diff of the range
    — what the whole change did between its two endpoints. A path the set
    touched that the net diff holds nothing for carries one of the named
    absences saying why. Never read a range as one commit, and never present
    "these commits form one change" as anything but a candidate reason.
12. A `diff` whose row says `truncated` holds only the first part of its text,
    and `characters_total` says the whole size. Never treat a bounded diff as
    the whole one, and never make a claim about the lines that were cut.

Answer with this shape:

{
  "summary": "prose, and every sentence carries the ids it restates, like [oc1]",
  "observed_changes": [
    {"id": "oc1",
     "statement": "one sentence, in the file's own terms",
     "evidence": ["ev1"]}
  ],
  "evidence": [
    {"id": "ev1",
     "kind": "commit | file | definition | range | cochange | absence",
     "ref": "copied exactly from the evidence below, in the form for its kind",
     "detail": "optional, what this citation shows"}
  ],
  "possible_reasons": [
    {"statement": "one sentence, a candidate and not a finding",
     "based_on": ["oc1"]}
  ],
  "uncertainty": [
    {"kind": "one of the absence kinds above, or 'definition_gone'",
     "detail": "what could not be determined"}
  ],
  "related_memory": [
    {"memory_id": "copied from the memories section, if there is one",
     "note": "one sentence, what this answer takes from that statement"}
  ]
}

The forms a `ref` takes, one per kind:

  commit      a commit sha, as written below (a prefix of one is accepted)
  changeset   a revision range, written `<base>..<tip>`, as the `range` section names it
  file        a path, as written below
  definition  a qualified name, as written below
  range       a changed span, written `<path>:<start>-<end>`
  cochange    one direction of a pair, written `<path> -> <other path>`
  absence     one of the absence kinds named below

Every `ref` must be a value that appears in the evidence below, copied exactly. An
id that names nothing is a rejected answer. Cite the narrowest thing that carries
the claim: a `range` when the claim is about where the change landed, a
`cochange` when it is about what moves together, a `definition` when it is about a
function or class. `possible_reasons` may be empty, and that is a correct answer
when the evidence supports no candidate.
"""


@dataclass(frozen=True, slots=True)
class Evidence:
    """One citation, naming something the bundle holds."""

    id: str
    kind: str
    ref: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ObservedChange:
    """One thing that happened, and the citations behind it."""

    id: str
    statement: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Confidence:
    """How much evidence a candidate rests on — counted, never reported.

    Filled in by the validator from ``based_on``, so the model does not write it
    and cannot inflate it. It measures how much evidence there is, not how good
    the reason is, and the rendering says so in as many words.
    """

    observed_changes: int
    commits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PossibleReason:
    """A candidate, with what it rests on."""

    statement: str
    based_on: tuple[str, ...]
    confidence: Confidence


@dataclass(frozen=True, slots=True)
class Uncertainty:
    """Something that could not be determined, and which kind of absence it is."""

    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class RelatedMemory:
    """One memory an answer related itself to, and the model's sentence about it.

    The id is the model's to name and the tool's to check: it has to be one the
    model was shown. The note is the reading, never the record — the statement
    itself is printed from the store, by the tool.
    """

    memory_id: str
    note: str


@dataclass(frozen=True, slots=True)
class Explanation:
    """One answer, checked and ready to print."""

    summary: str
    observed_changes: tuple[ObservedChange, ...]
    evidence: tuple[Evidence, ...]
    possible_reasons: tuple[PossibleReason, ...]
    uncertainty: tuple[Uncertainty, ...]
    related_memory: tuple[RelatedMemory, ...] = ()


def build_prompt(context_json: str, memories: str = "") -> str:
    """The instructions and the evidence, as one request.

    The evidence is handed over exactly as it was built: the bundle itself when
    it fits, and the reduced view ``selection`` made when it does not. The
    offline path prints the bundle either way, so a citation can be checked
    against it whatever the model was sent.

    ``memories`` is the memory section, already rendered by
    ``memory_section.prompt_section``. It is appended rather than merged: the
    bundle stays exactly what v0.4 froze, and the section is the second,
    separately-labelled input the phase adds. With no memories the prompt is the
    bytes it has always been.
    """
    return f"{INSTRUCTIONS}\nThe evidence:\n\n{context_json}\n{memories}"


EXPLAINED = "explained"
EVIDENCE_ONLY = "evidence_only"


def bundle_sha256(bundle: dict) -> str:
    """The evidence object's own hash, over its canonical serialisation.

    Sorted keys and no padding, so the hash is a function of the object alone
    and not of the order a reader happened to build it in — which is what makes
    "was this explanation produced from the bundle I have now?" a comparison of
    two facts rather than of two printings. The same unit and the same database
    give the same hash, run after run; a different hash means the evidence
    moved.
    """
    canonical = json.dumps(
        bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_object(
    explanation: "Explanation | None",
    context,
    selection: dict | None = None,
    memory: dict | None = None,
) -> dict:
    """The whole answer for a program, whichever kind of answer it is.

    One shape in both states, with ``state`` saying which one it is, because the
    two are not the same thing and a reader must not have to work out which it
    got: without a model there is no explanation, and the evidence is the answer.
    The same reason ``file --json`` is always an object holding a list.

    ``confidence`` is here and not in the model's document: it is derived from
    ``based_on`` by the validator, and a program reading this should get the same
    count a reader sees rather than a number the model chose.

    The evidence travels beside the explanation so that an answer can be checked
    against its input without a second call. ``selection`` is what the model was
    shown of it when the commit was too large to send whole: the evidence here is
    always the bundle, and a program that wants to know how much of it the model
    saw reads this rather than assuming the two are the same.

    ``memory`` is the third thing, and it is a sibling of ``evidence`` rather
    than a part of it: the bundle under ``evidence`` is what v0.4 built, byte for
    byte, and a program reading it gets exactly what it got before. The key is
    **absent when there is nothing to show**, which is the rule ``selection``
    already follows — an absent key is a statement, and its absence says this
    commit's files carry no stored memory.

    ``bundle_sha256`` is the fourth thing and always present: the hash of the
    evidence object under it, so a program holding an answer can compare its
    input against a stored run row without a second command.
    """
    found = {
        "commit": context.sha,
        "state": EXPLAINED if explanation is not None else EVIDENCE_ONLY,
        "explanation": None if explanation is None else _explanation_object(explanation),
        "evidence": context_object(context),
    }
    if selection is not None:
        found["selection"] = selection
    if memory is not None:
        found["memory"] = memory
    found["bundle_sha256"] = bundle_sha256(found["evidence"])
    return found


def build_json(
    explanation: "Explanation | None",
    context,
    selection: dict | None = None,
    memory: dict | None = None,
) -> str:
    """The answer as JSON — the bytes a program reads, and only those.

    Everything a reader would need told to them goes to stderr instead, so a
    caller can parse stdout without filtering it first.
    """
    return json.dumps(
        build_object(explanation, context, selection, memory),
        indent=2,
        ensure_ascii=False,
    )


def build_span_envelope(
    explanation: "Explanation | None",
    span,
    selection: dict | None = None,
    memory: dict | None = None,
) -> dict:
    """The range's envelope: the one shape, with ``range`` naming the unit.

    Every key means what it means for a commit — one shape, ``state`` saying
    which answer it holds, the evidence whole beside it — except the first,
    because a range is not a commit and the key that names the unit must not
    say it is one. ``range`` holds ``<base>..<tip>`` with both shas resolved,
    the same form a ``changeset`` citation takes.
    """
    from codearchaeology.span import build_span_object as span_object

    found = {
        "range": f"{span.base}..{span.tip}",
        "state": EXPLAINED if explanation is not None else EVIDENCE_ONLY,
        "explanation": None if explanation is None else _explanation_object(explanation),
        "evidence": span_object(span),
    }
    if selection is not None:
        found["selection"] = selection
    if memory is not None:
        found["memory"] = memory
    found["bundle_sha256"] = bundle_sha256(found["evidence"])
    return found


def build_span_envelope_json(
    explanation: "Explanation | None",
    span,
    selection: dict | None = None,
    memory: dict | None = None,
) -> str:
    """The range's envelope as JSON — stdout for a program, notes elsewhere."""
    return json.dumps(
        build_span_envelope(explanation, span, selection, memory),
        indent=2,
        ensure_ascii=False,
    )


def _explanation_object(explanation: Explanation) -> dict:
    return {
        "summary": explanation.summary,
        "observed_changes": [
            {
                "id": change.id,
                "statement": change.statement,
                "evidence": list(change.evidence),
            }
            for change in explanation.observed_changes
        ],
        "evidence": [
            {
                "id": item.id,
                "kind": item.kind,
                "ref": item.ref,
                "detail": item.detail,
            }
            for item in explanation.evidence
        ],
        "possible_reasons": [
            {
                "statement": reason.statement,
                "based_on": list(reason.based_on),
                "confidence": {
                    "observed_changes": reason.confidence.observed_changes,
                    "commits": list(reason.confidence.commits),
                },
            }
            for reason in explanation.possible_reasons
        ],
        "uncertainty": [
            {"kind": item.kind, "detail": item.detail}
            for item in explanation.uncertainty
        ],
        "related_memory": [
            {"memory_id": item.memory_id, "note": item.note}
            for item in explanation.related_memory
        ],
    }


def render(explanation: Explanation) -> str:
    """The answer as the terminal shows it.

    The three headings are the three kinds, and they are the whole of what tells
    a reader which list is which. A reason cannot be printed under ``Observed``
    because this function decides where each list goes, not the model.

    **It opens by saying what the whole block is** — an interpretation, with the
    citations checked and the sentences not — because that is the one thing a
    reader has to know before the first claim rather than after it. The evidence
    this block is a reading of is reproducible and this is not, and a reader who
    takes the two for the same kind of thing has been misled by the tool rather
    than by the model.

    **Every claim carries its citations, expanded.** Ids alone would be a chain
    the reader has to reconstruct from the JSON, and the point of this layer is
    that nobody is left not knowing what a claim rests on: the evidence behind a
    reason is printed under it, and the evidence behind an observed change under
    that. The ids stay, so the two can be tied together.
    """
    lines = [INTERPRETATION_NOTE, "", explanation.summary, ""]
    by_id = {item.id: item for item in explanation.evidence}

    lines.append(f"{OBSERVED} ({len(explanation.observed_changes)})")
    for change in explanation.observed_changes:
        lines.append(f"  {change.statement}  [{change.id}]")
        _citations(lines, (by_id[name] for name in change.evidence if name in by_id))
    if not explanation.observed_changes:
        lines.append("  (nothing the evidence supports)")
    lines.append("")

    lines.append(f"{POSSIBLE} ({len(explanation.possible_reasons)})")
    for reason in explanation.possible_reasons:
        lines.append(f"  {reason.statement}")
        lines.append(f"    rests on {_support(reason)}")
        entries = list(_resting_on(reason, explanation, by_id))
        _citations(lines, entries)
        if entries and all(item.kind == COCHANGE for item in entries):
            # Fires only when it is true, the way the file view prints a binary
            # count only when there is one. A candidate standing on nothing but a
            # statistic is the case a reader is most likely to take for a finding.
            lines.append(f"    {ONLY_CORRELATION}")
    if not explanation.possible_reasons:
        lines.append("  (the evidence supports no candidate)")
    else:
        lines.append(f"  {CANDIDATES_NOTE}")
    lines.append("")

    lines.append(f"{UNKNOWN} ({len(explanation.uncertainty)})")
    for item in explanation.uncertainty:
        lines.append(f"  {item.kind}: {item.detail}")
    if not explanation.uncertainty:
        lines.append("  (nothing was recorded as missing)")

    return "\n".join(lines)


def _resting_on(
    reason: PossibleReason, explanation: Explanation, by_id: dict[str, Evidence]
):
    """Every citation behind a candidate, once, whichever observation brought it.

    The union rather than the observations' own lists: a reader asking what a
    reason is built on wants the evidence, and following three ids back to three
    other lists to find out is exactly the reconstruction this saves them.
    """
    observed = {item.id: item for item in explanation.observed_changes}
    seen: dict[str, Evidence] = {}
    for name in reason.based_on:
        for citation in observed[name].evidence:
            if citation in by_id:
                seen.setdefault(citation, by_id[citation])
    return seen.values()


def _citations(lines: list[str], entries) -> None:
    """One line per citation, sorted so the same answer prints the same bytes.

    The kind comes first and is padded, so a reader can group them by eye: the
    commit, the files, the spans the change landed in, and what moves alongside.
    """
    for item in sorted(entries, key=lambda entry: (entry.kind, entry.ref)):
        detail = f"  {item.detail}" if item.detail else ""
        lines.append(f"    {item.kind:<11}{item.ref}{detail}")


def _support(reason: PossibleReason) -> str:
    """What a candidate rests on, said as the count it is rather than a score.

    The second clause is not padding: a reader who takes the number for a
    judgement about the reason would be reading a count as a finding, which is
    the one thing this layer exists to keep apart.
    """
    count = reason.confidence.observed_changes
    changes = "observed change" if count == 1 else "observed changes"
    return (
        f"{count} {changes} ({', '.join(reason.based_on)})"
        f" — how much evidence there is, not how good the reason is"
    )
