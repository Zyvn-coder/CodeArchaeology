"""The boundary: what the tool does about each way a model can be wrong.

Seven ways, named by the user, and the honest answer for each is one of three
things. This file is that answer in executable form, because both mistakes are
expensive — believing a defence exists where none does, and believing none exists
where one does.

| The way it can be wrong | What the tool does |
|---|---|
| an unknown stated as a fact | nothing. The limit, pinned below. |
| a commit that does not exist | **refuses the answer** |
| evidence that is not in the bundle | **refuses the answer** |
| correlation stated as a cause | prints a sentence under the candidate |
| co-change stated as a dependency | prints a sentence under that candidate |
| a structural change stated as intent | no field for it, and a sentence where it lands |
| not admitting that it does not know | an empty candidate list is a correct answer |

The refusals are the validator's and they are exhaustive in
``tests/test_validation.py``. What is here is the *adversarial* form — an answer
written the way a confident model writes one, fluent and fully cited, rather than
a single field mutated — and the three modes where the honest answer is a sentence
rather than a check.

**A sentence is not a defence against a model that means to mislead.** It is a
defence against a reader taking a candidate for a finding, which is the failure
this layer can actually prevent — and the one the user's rule is about: the model
may be wrong, but the reader must not be left unable to see what it stood on.
"""

import json
from pathlib import Path

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.context import build_context
from codearchaeology.explanation import (
    CANDIDATES_NOTE,
    INSTRUCTIONS,
    ONLY_CORRELATION,
    render,
)
from codearchaeology.storage import connect
from codearchaeology.validation import ExplanationError, validate
from sample_repo import build_cochange_repo, build_sample_repo


def _context_of(repository, database, message: str):
    connection = connect(database)
    try:
        sha = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (message,)
        ).fetchone()["sha"]
    finally:
        connection.close()
    return build_context(repository, database, sha)


@pytest.fixture(scope="module")
def moved(tmp_path_factory: pytest.TempPathFactory):
    """``a`` and ``b`` move together twice; the third commit also creates ``c``.

    Its third commit is the useful one: the pair ``a.py -> b.py`` is in its
    evidence and the reverse is not, so a candidate built on the statistic can be
    built on something real.
    """
    repository = build_cochange_repo(tmp_path_factory.mktemp("boundaries-moved"))
    database = repository.parent / "moved.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    return _context_of(repository, database, "Edit a and create c")


@pytest.fixture(scope="module")
def read(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture's fix commit: a diff, definitions, no co-change."""
    repository = build_sample_repo(tmp_path_factory.mktemp("boundaries-read"))
    database = repository.parent / "read.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    return _context_of(repository, database, "Fix login bug")


def _refused(context, answer: dict, message: str) -> str:
    with pytest.raises(ExplanationError) as raised:
        validate(json.dumps(answer), context)
    assert message in str(raised.value), str(raised.value)
    return str(raised.value)


# One: an unknown stated as a fact.


def test_an_unknown_stated_as_a_fact_is_not_caught(moved):
    """The limit, and the most important thing in this file to know.

    Every citation resolves — the commit is real, the file is real — and the
    sentence is still about something the repository does not hold. Nothing
    structural tells it apart from a true one, and no wording in a validator ever
    will. The defence is not a check: it is that the claim is printed beside the
    evidence it cites, so a reader can see for themselves that the evidence does
    not say it.
    """
    answer = {
        "summary": "The author was under time pressure here [oc1].",
        "observed_changes": [
            {
                "id": "oc1",
                "statement": "The author was under time pressure when writing this",
                "evidence": ["ev1"],
            }
        ],
        "evidence": [{"id": "ev1", "kind": "commit", "ref": moved.sha}],
        "possible_reasons": [],
        "uncertainty": [],
    }

    block = render(validate(json.dumps(answer), moved))

    assert "under time pressure" in block
    assert moved.sha in block, "the evidence is printed beside the claim that used it"


# Two: a commit that does not exist.


def test_a_fabricated_commit_is_refused(moved):
    """The one thing a confident model cannot get away with.

    A made-up sha can only be checked against the bundle, and the bundle is
    exactly what it is held against — so a fluent answer with one invented
    citation in it is refused whole rather than trimmed.
    """
    answer = {
        "summary": "A later commit removed this [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "the file was rewritten", "evidence": ["ev1"]}
        ],
        "evidence": [{"id": "ev1", "kind": "commit", "ref": "f" * 40}],
        "possible_reasons": [],
        "uncertainty": [],
    }

    _refused(moved, answer, "is not in the evidence for this commit")


def test_a_real_commit_from_another_repository_is_refused(moved):
    """A sha that exists somewhere is not a sha that exists here.

    This is the case a check against "does this look like a sha" would pass, and
    the reason the allowed set is built out of the bundle instead.
    """
    answer = {
        "summary": "A later commit removed this [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "the file was rewritten", "evidence": ["ev1"]}
        ],
        "evidence": [
            {"id": "ev1", "kind": "commit", "ref": "e83c5163316f89bfbde7d9ab23ca2e25604af290"}
        ],
        "possible_reasons": [],
        "uncertainty": [],
    }

    _refused(moved, answer, "is not in the evidence for this commit")


# Three: evidence that is not in the bundle.


def test_evidence_that_was_never_defined_is_refused(moved):
    """The id half of the same rule: a citation has to resolve to something."""
    answer = {
        "summary": "a.py was modified [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "a.py was modified", "evidence": ["ev1", "ev9"]}
        ],
        "evidence": [{"id": "ev1", "kind": "file", "ref": "a.py"}],
        "possible_reasons": [],
        "uncertainty": [],
    }

    _refused(moved, answer, "which no evidence entry defines")


def test_an_observed_change_that_cites_nothing_is_refused(moved):
    """An observation with no citation is a claim nothing stands behind."""
    answer = {
        "summary": "a.py was modified [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "a.py was modified", "evidence": []}
        ],
        "evidence": [{"id": "ev1", "kind": "file", "ref": "a.py"}],
        "possible_reasons": [],
        "uncertainty": [],
    }

    _refused(moved, answer, "cites no evidence")


# Four and five: correlation, and co-change read as a dependency.


def test_a_cause_built_on_a_statistic_is_marked(moved):
    """Not caught — marked, and the marking is the whole defence.

    The candidate says "because", which is a causal claim the evidence does not
    make; every citation behind it is real. What the tool can do is refuse to let
    the candidate stand alone in the block, and say what a candidate is.
    """
    answer = {
        "summary": "a.py was modified [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "a.py was modified here", "evidence": ["ev1", "ev2"]}
        ],
        "evidence": [
            {"id": "ev1", "kind": "commit", "ref": moved.sha},
            {"id": "ev2", "kind": "cochange", "ref": "a.py -> b.py"},
        ],
        "possible_reasons": [
            {
                "statement": "b.py changed because a.py did, in the same commits",
                "based_on": ["oc1"],
            }
        ],
        "uncertainty": [],
    }

    block = render(validate(json.dumps(answer), moved))

    assert "because" in block
    assert CANDIDATES_NOTE in block
    assert ONLY_CORRELATION not in block, "this one also cites the commit itself"


def test_a_candidate_standing_on_nothing_but_a_statistic_says_so(moved):
    """The sharper sentence, and it fires only when it is true.

    A candidate whose entire support is a co-change pair is the shape a reader is
    most likely to take for a finding, and the one the tool can describe exactly:
    everything behind it is a statistic over the file's history.
    """
    answer = {
        "summary": "a.py was modified [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "a.py and b.py move together", "evidence": ["ev1"]}
        ],
        "evidence": [{"id": "ev1", "kind": "cochange", "ref": "a.py -> b.py"}],
        "possible_reasons": [
            {"statement": "a.py depends on b.py", "based_on": ["oc1"]}
        ],
        "uncertainty": [],
    }

    block = render(validate(json.dumps(answer), moved))

    assert "depends on" in block
    assert ONLY_CORRELATION in block
    assert CANDIDATES_NOTE in block


def test_the_sharper_sentence_does_not_fire_on_a_fact_about_this_commit(moved):
    """A sentence that fired every time would be noise, and noise is not read."""
    answer = {
        "summary": "a.py was modified [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "a.py was modified", "evidence": ["ev1"]}
        ],
        "evidence": [{"id": "ev1", "kind": "range", "ref": "a.py:1-1"}],
        "possible_reasons": [{"statement": "This is a small change", "based_on": ["oc1"]}],
        "uncertainty": [],
    }

    block = render(validate(json.dumps(answer), moved))

    assert ONLY_CORRELATION not in block


# Six: a structural change stated as intent.


def test_there_is_no_field_for_a_motive(read):
    """Half of the answer to intent, and it is structural.

    The schema has nowhere to put a reason for the change, so an answer that
    invents one as a field of its own is refused rather than quietly carrying it.
    """
    answer = {
        "summary": "login was modified [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "login was modified", "evidence": ["ev1"]}
        ],
        "evidence": [{"id": "ev1", "kind": "definition", "ref": "login"}],
        "possible_reasons": [],
        "uncertainty": [],
        "conclusion": "the author wanted to make it testable",
    }

    _refused(read, answer, "the schema does not have")


def test_a_motive_smuggled_into_a_candidate_is_marked_not_caught(read):
    """The other half, and the honest one.

    "The author split it to make it testable" cites a definition that really did
    change, so the answer validates. The tool cannot tell it from a sentence the
    evidence does support — and what it does instead is print, under the
    candidate, what a candidate is.
    """
    answer = {
        "summary": "login was modified and logout was created [oc1].",
        "observed_changes": [
            {
                "id": "oc1",
                "statement": "login was modified and logout was created",
                "evidence": ["ev1", "ev2"],
            }
        ],
        "evidence": [
            {"id": "ev1", "kind": "definition", "ref": "login"},
            {"id": "ev2", "kind": "definition", "ref": "logout"},
        ],
        "possible_reasons": [
            {
                "statement": "The author split the login flow to make it testable",
                "based_on": ["oc1"],
            }
        ],
        "uncertainty": [],
    }

    block = render(validate(json.dumps(answer), read))

    assert "to make it testable" in block
    assert CANDIDATES_NOTE in block
    assert "not a statement about what the author meant" in block


# Seven: not admitting that it does not know.


def test_an_answer_that_admits_it_does_not_know_is_a_correct_answer(read):
    """The tool never asks for a candidate, and says so where the reader looks.

    Thin evidence is a real state and the answer to it is an empty list — printed
    as its own sentence rather than left as a blank a reader would take for a
    failure.
    """
    answer = {
        "summary": "logout was created and login was modified [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "logout was created", "evidence": ["ev1"]}
        ],
        "evidence": [{"id": "ev1", "kind": "definition", "ref": "logout"}],
        "possible_reasons": [],
        "uncertainty": [
            {
                "kind": "no_version_stored",
                "detail": "the README was not read, so nothing is known about it",
            }
        ],
    }

    block = render(validate(json.dumps(answer), read))

    assert "Possible (0)" in block
    assert "(the evidence supports no candidate)" in block
    assert CANDIDATES_NOTE not in block, "there is no candidate to mark"


def test_the_uncertainty_it_admits_is_carried_through_to_the_reader(read):
    answer = {
        "summary": "logout was created [oc1].",
        "observed_changes": [
            {"id": "oc1", "statement": "logout was created", "evidence": ["ev1"]}
        ],
        "evidence": [{"id": "ev1", "kind": "definition", "ref": "logout"}],
        "possible_reasons": [],
        "uncertainty": [
            {"kind": "parse_failed", "detail": "legacy.py did not parse"}
        ],
    }

    block = render(validate(json.dumps(answer), read))

    assert "Unknown (1)" in block
    assert "parse_failed: legacy.py did not parse" in block


# What the model is told, which is not the same as what is enforced.


def test_the_prompt_carries_the_rules_even_though_telling_is_not_enforcing(read):
    """Every rule above is in the instructions, and none of them is a defence.

    This test exists so that dropping one goes red — a rule the prompt no longer
    carries is a rule the model is not even being asked to follow, and everything
    else in this file is what happens when it does not.
    """
    assert "Never state why the author made the change" in INSTRUCTIONS
    assert "Do not predict, score or judge" in INSTRUCTIONS
    assert "say so in `uncertainty` rather than filling the gap" in INSTRUCTIONS
