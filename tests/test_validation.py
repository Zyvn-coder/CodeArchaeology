"""Tests for the validator: one case per rule it enforces.

This file is the contract in executable form. Every row of the table below is a
way an answer can be wrong, and each one is refused for its own reason — a
missing field, a field the schema does not have, an id that names nothing, a
citation pointing at a commit that does not exist.

Two things are worth saying about the shape of it. The table is exhaustive on
purpose: a validator is only as good as the list of things it rejects, and a rule
with no case is a rule that could be deleted without anything going red. And the
messages are checked, not just the fact of refusal — "rejected" with no reason is
what a user cannot act on.

The one case that is not about a malformed answer is ``confidence``: the tool
derives it, so a model that reports its own is answering a question it was not
asked, and the refusal says so rather than dropping the field quietly.
"""

import json
from pathlib import Path

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.context import build_context
from codearchaeology.explanation import COMMIT, FILE, Confidence
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.storage import connect
from codearchaeology.validation import DERIVED_KEY_MESSAGE, ExplanationError, validate
from sample_repo import build_cochange_repo, build_sample_repo

FIXED = "Fix login bug"


@pytest.fixture(scope="module")
def context(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture's fix commit, analyzed and read."""
    repository = build_sample_repo(tmp_path_factory.mktemp("validation"))
    database = repository.parent / "validation.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    connection = connect(database)
    try:
        sha = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (FIXED,)
        ).fetchone()["sha"]
    finally:
        connection.close()

    return build_context(repository, database, sha)


def _sha(context) -> str:
    return context.sha


def _answer(context, **changes) -> dict:
    """A valid answer about the fixture, with fields replaced as asked."""
    document = {
        "summary": "The commit modified core/app.py [oc1].",
        "observed_changes": [
            {
                "id": "oc1",
                "statement": "core/app.py was modified in this commit",
                "evidence": ["ev1", "ev2"],
            }
        ],
        "evidence": [
            {"id": "ev1", "kind": "file", "ref": "core/app.py", "detail": "touched"},
            {"id": "ev2", "kind": "commit", "ref": _sha(context)},
        ],
        "possible_reasons": [
            {"statement": "This is consistent with the login change", "based_on": ["oc1"]}
        ],
        "uncertainty": [],
    }
    document.update(changes)
    return document


def _reject(context, document, message: str):
    with pytest.raises(ExplanationError) as raised:
        validate(json.dumps(document), context)
    assert message in str(raised.value), str(raised.value)
    return raised.value


# The answer that should be accepted.


def test_a_well_formed_answer_is_accepted(context):
    explanation = validate(json.dumps(_answer(context)), context)

    assert explanation.summary == "The commit modified core/app.py [oc1]."
    assert [change.id for change in explanation.observed_changes] == ["oc1"]
    assert explanation.possible_reasons[0].based_on == ("oc1",)


def test_a_fenced_block_is_unwrapped_rather_than_refused(context):
    """Returning JSON in a fence is a habit, not a wrong answer."""
    fenced = f"```json\n{json.dumps(_answer(context))}\n```"

    assert validate(fenced, context).summary.startswith("The commit modified")


def test_an_answer_with_no_candidate_is_accepted(context):
    """Thin evidence is a correct answer, and an empty list is how it is said."""
    explanation = validate(
        json.dumps(_answer(context, possible_reasons=[])), context
    )

    assert explanation.possible_reasons == ()


def test_a_shortened_sha_names_the_same_commit(context):
    """Git takes prefixes and so does every command in this tool."""
    document = _answer(context)
    document["evidence"][1]["ref"] = _sha(context)[:8]

    assert validate(json.dumps(document), context).evidence[1].ref == _sha(context)[:8]


# What confidence is, and is not.


def test_the_confidence_is_counted_from_what_the_candidate_rests_on(context):
    document = _answer(context)
    document["observed_changes"].append(
        {
            "id": "oc2",
            "statement": "the file was read as Python",
            "evidence": ["ev1"],
        }
    )
    document["possible_reasons"][0]["based_on"] = ["oc1", "oc2"]

    reason = validate(json.dumps(document), context).possible_reasons[0]

    assert reason.confidence == Confidence(
        observed_changes=2, commits=(_sha(context)[:SHORT_SHA_LENGTH],)
    )


def test_a_confidence_from_the_model_is_refused(context):
    """The field the model was told not to send, sent anyway.

    Dropping it quietly would be the worst of both: the answer shown would not be
    the answer that was checked, and the model would never learn that it had
    misread the task.
    """
    document = _answer(context)
    document["possible_reasons"][0]["confidence"] = 0.9

    message = str(_reject(context, document, DERIVED_KEY_MESSAGE))
    assert "the model does not write" in message


def test_a_top_level_confidence_is_refused_too(context):
    _reject(context, _answer(context, confidence=0.5), DERIVED_KEY_MESSAGE)


# Pass one: the text is not what was asked for.


def test_text_that_is_not_json_is_refused(context):
    with pytest.raises(ExplanationError) as raised:
        validate("The author was refactoring, I think.", context)
    assert "is not JSON" in str(raised.value)


def test_a_json_array_is_not_a_document(context):
    with pytest.raises(ExplanationError) as raised:
        validate("[]", context)
    assert "not a JSON object" in str(raised.value)


# Pass two: the shape.


@pytest.mark.parametrize(
    ("name", "mutate", "message"),
    [
        (
            "no summary",
            lambda d, c: d.pop("summary"),
            "has no summary",
        ),
        (
            "summary that is not text",
            lambda d, c: d.update(summary=12),
            "has no summary",
        ),
        (
            "whitespace for a summary",
            lambda d, c: d.update(summary="   "),
            "empty summary",
        ),
        (
            "observed_changes that is not a list",
            lambda d, c: d.update(observed_changes="oc1"),
            "is not a list",
        ),
        (
            "an observed change that is not an object",
            lambda d, c: d.update(observed_changes=["oc1"]),
            "is not an object",
        ),
        (
            "an observed change with no id",
            lambda d, c: d["observed_changes"][0].pop("id"),
            "has no 'id'",
        ),
        (
            "an observed change with no statement",
            lambda d, c: d["observed_changes"][0].pop("statement"),
            "has no 'statement'",
        ),
        (
            "evidence ids that are not a list",
            lambda d, c: d["observed_changes"][0].update(evidence="ev1"),
            "is not a list of ids",
        ),
        (
            "two observed changes sharing an id",
            lambda d, c: d["observed_changes"].append(dict(d["observed_changes"][0])),
            "share an id",
        ),
        (
            "two evidence entries sharing an id",
            lambda d, c: d["evidence"].append(dict(d["evidence"][0])),
            "share an id",
        ),
        (
            "an evidence entry with no kind",
            lambda d, c: d["evidence"][0].pop("kind"),
            "has no 'kind'",
        ),
        (
            "an evidence entry with no ref",
            lambda d, c: d["evidence"][0].pop("ref"),
            "has no 'ref'",
        ),
        (
            "an evidence kind that is not one of the four",
            lambda d, c: d["evidence"][0].update(kind="vibe"),
            "is not one of",
        ),
        (
            "an uncertainty kind that is not one of the five",
            lambda d, c: d.update(uncertainty=[{"kind": "maybe", "detail": "x"}]),
            "is not one of",
        ),
        (
            "an evidence detail that is not text",
            lambda d, c: d["evidence"][0].update(detail=7),
            "is not text",
        ),
        (
            "a field the schema does not have",
            lambda d, c: d.update(conclusion="it was a bugfix"),
            "the schema does not have",
        ),
        (
            "a field inside an observed change the schema does not have",
            lambda d, c: d["observed_changes"][0].update(certainty="high"),
            "the schema does not have",
        ),
        (
            "a field inside a reason the schema does not have",
            lambda d, c: d["possible_reasons"][0].update(weight=3),
            "the schema does not have",
        ),
    ],
)
def test_the_schema_refuses_what_it_does_not_describe(context, name, mutate, message):
    document = _answer(context)
    mutate(document, context)
    _reject(context, document, message)


# Pass three: what the answer names.


@pytest.mark.parametrize(
    ("name", "mutate", "message"),
    [
        (
            "a path the commit never touched",
            lambda d, c: d["evidence"][0].update(ref="src/never_existed.py"),
            "is not in the evidence for this commit",
        ),
        (
            "a commit that does not exist",
            lambda d, c: d["evidence"][1].update(ref="0" * 40),
            "is not in the evidence for this commit",
        ),
        (
            "a definition this commit did not change",
            lambda d, c: d["evidence"].append(
                {"id": "ev3", "kind": "definition", "ref": "never_defined"}
            ),
            "is not in the evidence for this commit",
        ),
        (
            "an absence this commit does not have",
            lambda d, c: d["evidence"].append(
                {"id": "ev3", "kind": "absence", "ref": "parse_failed"}
            ),
            "is not in the evidence for this commit",
        ),
        (
            "a path used as a commit",
            lambda d, c: d["evidence"][1].update(ref="core/app.py"),
            "is not in the evidence for this commit",
        ),
        (
            "an evidence id no entry defines",
            lambda d, c: d["observed_changes"][0].update(evidence=["ev9"]),
            "which no evidence entry defines",
        ),
        (
            "an observed change citing nothing",
            lambda d, c: d["observed_changes"][0].update(evidence=[]),
            "cites no evidence",
        ),
        (
            "a reason resting on nothing",
            lambda d, c: d["possible_reasons"][0].update(based_on=[]),
            "rests on nothing",
        ),
        (
            "a reason resting on something that is not an observed change",
            lambda d, c: d["possible_reasons"][0].update(based_on=["ev1"]),
            "is not an observed change",
        ),
        (
            "a summary citing an id that does not exist",
            lambda d, c: d.update(summary="The commit changed things [oc7]."),
            "is not an observed change",
        ),
        (
            "a summary citing nothing at all",
            lambda d, c: d.update(summary="The commit changed core/app.py."),
            "cites no observed change",
        ),
    ],
)
def test_the_semantics_refuse_what_the_bundle_does_not_hold(
    context, name, mutate, message
):
    document = _answer(context)
    mutate(document, context)
    _reject(context, document, message)


def test_a_candidate_in_the_summary_is_refused(context):
    """Where a hedged guess would read as a finding."""
    document = _answer(context)
    document["summary"] = (
        "The commit modified core/app.py [oc1]."
        " This is consistent with the login change."
    )

    _reject(context, document, "appears in the summary")


# What the check cannot see, said out loud.


def test_the_check_does_not_see_a_sentence_that_overstates_its_citation(context):
    """The limit, pinned by a test so nobody has to rediscover it.

    ``core/app.py`` was modified, so this cites something real — and the sentence
    is still not something the bundle supports. No program catches that, which is
    why it is written into the schema's §6 and into this file.
    """
    document = _answer(context)
    document["observed_changes"][0]["statement"] = (
        "core/app.py was rewritten from scratch to fix a security hole"
    )

    explanation = validate(json.dumps(document), context)

    assert explanation.observed_changes[0].statement.endswith("security hole")


def test_a_kind_and_a_ref_from_different_entries_do_not_combine(context):
    """The check is on the pair, not on the two halves separately.

    ``core/app.py`` is a real path and the sha is a real commit, and neither of
    them is a commit called ``core/app.py``. A validator that checked the halves
    against one combined set of known strings would accept this.
    """
    document = _answer(context)
    document["evidence"][0]["kind"] = COMMIT

    _reject(context, document, "is not in the evidence for this commit")


# Provenance: every section of the bundle a claim can point at.


@pytest.fixture(scope="module")
def moved(tmp_path_factory: pytest.TempPathFactory):
    """The hand-computable fixture, where ``a`` and ``b`` move together twice.

    Its third commit is the one the direction test needs: by then ``b`` has not
    moved with ``a`` as often as ``a`` has with ``b``, so the pair is in the
    bundle one way round and not the other — which is what makes "cite the
    direction the statistic came from" a rule a test can hold.
    """
    repository = build_cochange_repo(tmp_path_factory.mktemp("validation-moved"))
    database = repository.parent / "moved.db"
    analyze(repository, database)

    connection = connect(database)
    try:
        sha = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", ("Edit a and create c",)
        ).fetchone()["sha"]
    finally:
        connection.close()

    return build_context(repository, database, sha)


def _moved_answer(context, **changes) -> dict:
    document = {
        "summary": "a.py changed where its lines were rewritten [oc1].",
        "observed_changes": [
            {
                "id": "oc1",
                "statement": "a.py was modified, and b.py usually moves with it",
                "evidence": ["ev1", "ev2", "ev3"],
            }
        ],
        "evidence": [
            {"id": "ev1", "kind": "file", "ref": "a.py"},
            {"id": "ev2", "kind": "range", "ref": "a.py:1-1"},
            {"id": "ev3", "kind": "cochange", "ref": "a.py -> b.py"},
        ],
        "possible_reasons": [
            {"statement": "This is consistent with the pair", "based_on": ["oc1"]}
        ],
        "uncertainty": [],
    }
    document.update(changes)
    return document


def test_every_section_of_the_bundle_can_be_cited(moved):
    """Commit, file, definition, the diff's spans, co-change and the absences.

    The point of the list being complete is that a claim never has to point at
    something coarser than what it is about: "the change landed here" has a
    span to cite, and "these move together" has a pair.
    """
    explanation = validate(json.dumps(_moved_answer(moved)), moved)

    assert sorted(item.kind for item in explanation.evidence) == [
        "cochange",
        "file",
        "range",
    ]


def test_a_span_the_change_did_not_land_in_is_refused(moved):
    """A precise-looking claim that no diff supports is worse than a vague one."""
    document = _moved_answer(moved)
    document["evidence"][1]["ref"] = "a.py:100-200"

    _reject(moved, document, "is not in the evidence for this commit")


def test_a_span_of_a_file_this_commit_did_not_touch_is_refused(moved):
    """``b.py`` is a real file with real spans in this repository — at the commits
    that touched it. This one did not, so its spans are not in this bundle."""
    document = _moved_answer(moved)
    document["evidence"][1]["ref"] = "b.py:1-1"

    _reject(moved, document, "is not in the evidence for this commit")


def test_a_pair_cited_the_wrong_way_round_is_refused(moved):
    """The statistic is directional, so the citation has to be too.

    ``b.py -> a.py`` is a real pair in this repository — it is in the bundle at
    earlier commits — and it is not in *this* commit's evidence, which is the
    difference the check exists to keep.
    """
    document = _moved_answer(moved)
    document["evidence"][2]["ref"] = "b.py -> a.py"

    _reject(moved, document, "is not in the evidence for this commit")


def test_a_pair_that_is_not_in_the_bundle_is_refused(moved):
    document = _moved_answer(moved)
    document["evidence"][2]["ref"] = "a.py -> never_touched.py"

    _reject(moved, document, "is not in the evidence for this commit")


def test_a_file_cannot_be_cited_as_a_span(moved):
    """The kinds are not interchangeable, even where the strings overlap."""
    document = _moved_answer(moved)
    document["evidence"][1]["kind"] = FILE

    _reject(moved, document, "is not in the evidence for this commit")
