"""Tests for ``archaeology explain``: the whole pipeline, and no network.

Every test here is offline. The command's provider comes from the environment, so
the online path is exercised by pointing it at a local HTTP server that answers
from a script — which is the only way a networked feature can be tested on a
machine with no network, and the reason the architecture freeze put the provider
behind an interface in the first place.

Three things are under test beyond "it runs":

* **The evidence is the same bytes on both paths.** What the offline path prints
  is what the model is sent, which is the rule that keeps them from drifting.
* **A citation that names nothing is refused.** That check is the one that makes
  Evidence First real at this layer; a shape check alone would accept a confident
  answer citing a commit that does not exist.
* **A broken answer is never repaired.** It is asked for once more and then
  refused, and nothing of it is printed — because a partial explanation with a
  note is worse than none, the note being the part that gets skipped.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology import provider as provider_module
from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.context import build_context, build_json as build_context_json
from codearchaeology.explanation import INTERPRETATION_NOTE
from codearchaeology.storage import connect
from test_provider import _completion, _endpoint

runner = CliRunner()

FIXED = "Fix login bug"
INITIAL = "Initial commit"


@pytest.fixture(scope="module")
def explained(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture, analyzed and read, with the database beside it."""
    repository = build_sample(tmp_path_factory.mktemp("explain"))
    database = repository.parent / "explain.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    return repository, database


def build_sample(destination: Path) -> Path:
    from sample_repo import build_sample_repo

    return build_sample_repo(destination)


def _sha(database: Path, message: str) -> str:
    connection = connect(database)
    try:
        row = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (message,)
        ).fetchone()
    finally:
        connection.close()
    return row["sha"]


def _run(repository, database, sha, *flags, **environment):
    arguments = [
        "explain",
        sha[:8],
        str(repository),
        "--db",
        str(database),
        *flags,
    ]
    return runner.invoke(app, arguments, env=environment or None)


def _answer(sha: str, *, path: str = "core/app.py", reason: bool = True) -> str:
    """A well-formed answer about the fixture's fix commit."""
    document = {
        "summary": f"The commit changed {path}, adding six lines [oc1].",
        "observed_changes": [
            {
                "id": "oc1",
                "statement": f"{path} was modified in this commit",
                "evidence": ["ev1", "ev2"],
            }
        ],
        "evidence": [
            {"id": "ev1", "kind": "file", "ref": path, "detail": "touched here"},
            {"id": "ev2", "kind": "commit", "ref": sha},
        ],
        "possible_reasons": (
            [{"statement": "This is consistent with the login change", "based_on": ["oc1"]}]
            if reason
            else []
        ),
        "uncertainty": [],
    }
    return json.dumps(document)


# The offline path.


def test_with_no_model_configured_the_evidence_is_the_answer(explained):
    """Core First, made structural: no key, no endpoint, no network, and the
    command still answers with everything the tool knows."""
    repository, database = explained
    sha = _sha(database, FIXED)

    result = _run(repository, database, sha)

    assert result.exit_code == 0
    assert result.stdout.strip() == build_context_json(
        build_context(repository, database, sha)
    ).strip()


def test_the_note_about_the_missing_model_goes_to_stderr(explained):
    """stdout has to stay the evidence alone, for whatever reads it."""
    repository, database = explained
    result = _run(repository, database, _sha(database, FIXED))

    assert "CODEARCHAEOLOGY_AI_BASE_URL" in result.stderr
    assert "CODEARCHAEOLOGY_AI_BASE_URL" not in result.stdout


# The online path, against a local endpoint.


def test_a_configured_model_gets_the_evidence_and_its_answer_is_shown(explained):
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0
    assert "Observed (1)" in result.stdout
    assert "Possible (1)" in result.stdout
    assert "Unknown (0)" in result.stdout
    assert "core/app.py was modified in this commit" in result.stdout


def test_what_the_model_is_sent_is_what_the_offline_path_prints(explained):
    """The architecture freeze's rule: one bundle, two paths, no drift.

    If these two ever differ, a reader can no longer put an answer beside its
    input and check one against the other.
    """
    repository, database = explained
    sha = _sha(database, FIXED)
    evidence = build_context_json(build_context(repository, database, sha))

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    sent = server.requests[0]["body"]["messages"][0]["content"]
    assert evidence in sent
    assert "You are explaining one commit" in sent


def test_the_confidence_is_counted_by_the_tool_not_reported_by_the_model(explained):
    """The field the model cannot write is the field it cannot inflate."""
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert "rests on 1 observed change" in result.stdout
    assert "not how good the reason is" in result.stdout


def test_the_block_opens_by_saying_what_it_is(explained):
    """The contract, printed where a reader meets the answer.

    An answer about a commit reads like a record of that commit, and that is the
    one misreading this layer is responsible for preventing — so the sentence
    comes before the first claim rather than after it, the way `files` ends by
    saying what a deleted row means rather than leaving it to the reader.
    """
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0
    assert result.stdout.startswith(INTERPRETATION_NOTE)
    assert "not a record of what happened" in result.stdout


def test_the_statement_is_not_in_the_json(explained):
    """It is prose for a reader, like the two sentences about candidates.

    A program gets the answer and the citations it rests on; the tool's framing
    of them is not a field, because a caller that wants to say what the document
    is can say it better than the document can.
    """
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _run(
            repository,
            database,
            sha,
            "--json",
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0
    assert INTERPRETATION_NOTE not in result.stdout
    assert "not a record of what happened" not in result.stdout


def test_a_commit_with_no_candidate_prints_an_empty_possible_list(explained):
    """An empty list is a correct answer, and it has to look like one."""
    repository, database = explained
    sha = _sha(database, INITIAL)

    with _endpoint(
        {
            "status": 200,
            "body": _completion(
                _answer(sha, path="app.py", reason=False).replace(
                    "adding six lines", "creating the file"
                )
            ),
        }
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0
    assert "Possible (0)" in result.stdout
    assert "the evidence supports no candidate" in result.stdout


# A citation that names nothing.


def test_a_citation_that_names_nothing_is_refused(explained):
    """The check that makes Evidence First real here rather than decorative."""
    repository, database = explained
    sha = _sha(database, FIXED)
    invented = _answer(sha, path="src/never_existed.py")

    with _endpoint(
        {"status": 200, "body": _completion(invented)},
        {"status": 200, "body": _completion(invented)},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert "src/never_existed.py" in result.stderr
    assert "not in the" in result.stderr


def test_a_broken_answer_is_asked_for_once_more_and_no_further(explained):
    """The model may answer properly the second time; a third try is paying for
    the same mistake again."""
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint(
        {"status": 200, "body": _completion(_answer(sha, path="invented.py"))},
        {"status": 200, "body": _completion(_answer(sha))},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0
    assert len(server.requests) == 2
    assert "Observed (1)" in result.stdout


def test_a_refused_answer_prints_none_of_itself(explained):
    """No partial explanation, and the reason is on stderr where it belongs."""
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint(
        {"status": 200, "body": _completion(_answer(sha, path="invented.py"))},
        {"status": 200, "body": _completion(_answer(sha, path="invented.py"))},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "not shown" in result.stderr


def test_a_candidate_smuggled_into_the_summary_is_refused(explained):
    """The summary is where a hedged guess reads as a finding."""
    repository, database = explained
    sha = _sha(database, FIXED)
    smuggled = json.loads(_answer(sha))
    smuggled["summary"] = (
        "The commit changed core/app.py [oc1]. This is consistent with the login"
        " change."
    )

    with _endpoint(
        {"status": 200, "body": _completion(json.dumps(smuggled))},
        {"status": 200, "body": _completion(json.dumps(smuggled))},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert "appears in the summary" in result.stderr


def test_a_reason_resting_on_nothing_is_refused(explained):
    repository, database = explained
    sha = _sha(database, FIXED)
    empty = json.loads(_answer(sha))
    empty["possible_reasons"][0]["based_on"] = []

    with _endpoint(
        {"status": 200, "body": _completion(json.dumps(empty))},
        {"status": 200, "body": _completion(json.dumps(empty))},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert "rests on nothing" in result.stderr


def test_an_answer_that_is_not_json_is_a_model_failure(explained):
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint(
        {"status": 200, "body": _completion("I think the author was refactoring.")},
        {"status": 200, "body": _completion("Still not JSON.")},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert "not JSON" in result.stderr


def test_a_summary_that_cites_nothing_is_refused(explained):
    """A sentence with no citation is a sentence with nothing behind it."""
    repository, database = explained
    sha = _sha(database, FIXED)
    uncited = json.loads(_answer(sha))
    uncited["summary"] = "The commit changed core/app.py."

    with _endpoint(
        {"status": 200, "body": _completion(json.dumps(uncited))},
        {"status": 200, "body": _completion(json.dumps(uncited))},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert "cites no observed change" in result.stderr


# The provider's own failures reach the user as one line.


def test_an_endpoint_that_refuses_ends_the_command(explained):
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 401, "body": b"no"}) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
            CODEARCHAEOLOGY_AI_MAX_RETRIES="0",
        )

    assert result.exit_code == 1
    assert "401" in result.stderr
    assert result.stdout == ""


# Every way the model side can be unavailable, at the level the user sees it:
# an exit code, nothing on stdout, and a sentence on stderr.


@pytest.mark.parametrize(
    ("name", "replies", "expected"),
    [
        ("rate limited", [{"status": 429, "body": b"slow down"}] * 3, "429"),
        ("the model is unavailable", [{"status": 503, "body": b"busy"}] * 3, "503"),
        (
            "the endpoint answers with something that is not a completion",
            [{"status": 200, "body": b"<html>proxy says no</html>"}],
            "not a completion",
        ),
    ],
)
def test_an_endpoint_that_cannot_answer_ends_the_command_cleanly(
    explained, monkeypatch, name, replies, expected
) -> None:
    """The failure modes the user listed, each ending the same way.

    A user does not see an exception type; they see an exit code, whatever landed
    on stdout, and a sentence. Half an answer or a traceback would both be worse
    than the refusal, so all three are asserted for each mode.
    """
    repository, database = explained
    sha = _sha(database, FIXED)
    monkeypatch.setattr(
        provider_module, "_backoff", lambda attempt, retry_after: 0.0
    )

    with _endpoint(*replies) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert expected in result.stderr
    assert "Traceback" not in result.stderr


def test_a_mistyped_endpoint_is_a_sentence_not_a_traceback(explained):
    """The most likely first-run mistake, and the worst shape to fail in.

    A host written the way hosts are written everywhere else — no scheme — used
    to reach the user as a ``ValueError`` from inside urllib.
    """
    repository, database = explained
    result = _run(
        repository,
        database,
        _sha(database, FIXED),
        CODEARCHAEOLOGY_AI_BASE_URL="api.example.com/v1",
        CODEARCHAEOLOGY_AI_MODEL="a-model",
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "CODEARCHAEOLOGY_AI_BASE_URL" in result.stderr
    assert "needs a scheme" in result.stderr
    assert "Traceback" not in result.stderr


def test_an_unknown_commit_is_refused_before_anything_else(explained):
    repository, database = explained
    result = _run(repository, database, "deadbeef")

    assert result.exit_code == 1
    assert "no stored commit" in result.stderr


# The JSON: one shape in both states, and stdout stays parseable.


def test_the_json_with_no_model_says_which_kind_of_answer_it_is(explained):
    """The two states are not the same thing, so the reader is told which.

    A program that had to work out whether it got an explanation or the evidence
    would be a program that can be wrong — the same reason ``file --json`` is
    always an object holding a list.
    """
    repository, database = explained
    sha = _sha(database, FIXED)

    result = _run(repository, database, sha, "--json")

    assert result.exit_code == 0
    document = json.loads(result.stdout)
    assert document["state"] == "evidence_only"
    assert document["explanation"] is None
    assert document["commit"] == sha
    assert document["evidence"]["commit"]["sha"] == sha


def test_the_json_carries_the_evidence_it_was_built_from(explained):
    """So an answer can be checked against its input without a second call."""
    repository, database = explained
    sha = _sha(database, FIXED)

    document = json.loads(_run(repository, database, sha, "--json").stdout)

    assert document["evidence"] == json.loads(
        build_context_json(build_context(repository, database, sha))
    )


def test_the_json_note_still_goes_to_stderr(explained):
    """stdout has to be parseable without filtering it first."""
    repository, database = explained
    result = _run(repository, database, _sha(database, FIXED), "--json")

    json.loads(result.stdout)
    assert "CODEARCHAEOLOGY_AI_BASE_URL" in result.stderr


def test_the_json_of_an_explanation_carries_what_the_block_shows(explained):
    """The JSON carries the same facts, not a summary of them.

    Including the count the model does not write: a program should get the number
    a reader sees, not a number the model chose.
    """
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _run(
            repository,
            database,
            sha,
            "--json",
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0
    document = json.loads(result.stdout)
    assert document["state"] == "explained"
    assert document["explanation"]["summary"] == (
        "The commit changed core/app.py, adding six lines [oc1]."
    )
    assert document["explanation"]["observed_changes"][0]["id"] == "oc1"
    assert document["explanation"]["possible_reasons"][0]["confidence"] == {
        "observed_changes": 1,
        "commits": [sha[:8]],
    }
    assert document["evidence"]["commit"]["sha"] == sha


def test_the_json_and_the_block_hold_the_same_answer(explained):
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        as_json = _run(
            repository,
            database,
            sha,
            "--json",
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )
    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        as_block = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    document = json.loads(as_json.stdout)
    summary = document["explanation"]["summary"]
    statement = document["explanation"]["observed_changes"][0]["statement"]
    assert summary in as_block.stdout
    assert statement in as_block.stdout
    assert "core/app.py was modified in this commit" in as_block.stdout


def test_a_refused_answer_prints_no_json_either(explained):
    """Half a document is worse than none: a parser would read it as complete."""
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint(
        {"status": 200, "body": _completion(_answer(sha, path="invented.py"))},
        {"status": 200, "body": _completion(_answer(sha, path="invented.py"))},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            "--json",
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "invented.py" in result.stderr


# Provenance: every claim carries what it rests on.


def _lines(result) -> list[str]:
    return [line.strip() for line in result.stdout.splitlines()]


def test_every_claim_carries_the_evidence_it_rests_on(explained):
    """The unit's whole point: nobody is left not knowing what a claim rests on.

    The ids alone would be a chain the reader has to reconstruct from the JSON.
    The citations are printed under the claim that uses them, and printed again
    under the reason built on them — which is the union, so the reader does not
    have to follow ids back to find out what a candidate is standing on.
    """
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    lines = _lines(result)
    assert "core/app.py was modified in this commit  [oc1]" in lines
    files = [line for line in lines if line.startswith("file") and "core/app.py" in line]
    commits = [line for line in lines if line.startswith("commit") and sha in line]
    assert len(files) == 2, "once under the claim, once under the reason built on it"
    assert len(commits) == 2


def test_the_reason_names_the_observations_it_was_built_from(explained):
    repository, database = explained
    sha = _sha(database, FIXED)

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert any(
        line.startswith("rests on 1 observed change (oc1)") for line in _lines(result)
    )


def test_the_span_the_change_landed_in_can_be_cited_and_is_shown(explained):
    """The diff is in the bundle, so a claim about where the change landed can
    point at the span rather than at the whole file."""
    repository, database = explained
    sha = _sha(database, FIXED)
    document = json.loads(_answer(sha))
    document["evidence"].append(
        {"id": "ev3", "kind": "range", "ref": "core/app.py:5-6", "detail": "rewritten"}
    )
    document["observed_changes"][0]["evidence"].append("ev3")
    document["summary"] = "The change landed at core/app.py:5-6 [oc1]."

    with _endpoint(
        {"status": 200, "body": _completion(json.dumps(document))}
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0
    assert any(
        line.startswith("range") and "core/app.py:5-6" in line for line in _lines(result)
    )


def test_a_span_the_change_did_not_land_in_ends_the_command(explained):
    """A precise-looking citation no diff supports is refused like any other."""
    repository, database = explained
    sha = _sha(database, FIXED)
    document = json.loads(_answer(sha))
    document["evidence"].append(
        {"id": "ev3", "kind": "range", "ref": "core/app.py:900-1000"}
    )
    document["observed_changes"][0]["evidence"].append("ev3")

    with _endpoint(
        {"status": 200, "body": _completion(json.dumps(document))},
        {"status": 200, "body": _completion(json.dumps(document))},
    ) as server:
        result = _run(
            repository,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "core/app.py:900-1000" in result.stderr
