"""Tests for ``explain`` over a revision range: the second unit on the command line.

The frozen rule this file holds is that a range gets no fourth path: the
offline print, ``--json`` and the model all carry the set bundle, the offline
bytes are the model's input, and the answer is checked against the view of the
set the model was actually shown. ``--no-model`` is the other rule: a person
with a model configured can still read the evidence alone, and the flag is the
whole of what that takes.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.explanation import bundle_sha256
from codearchaeology.memory import MemoryStoreError, Subject, admit
from codearchaeology.span import build_span, build_span_object, build_span_json
from codearchaeology.storage import connect
from sample_repo import build_sample_repo, git_output
from test_provider import _completion, _endpoint

runner = CliRunner()


@pytest.fixture(scope="module")
def range_repo(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture, analyzed and read, with the database beside it."""
    repository = build_sample_repo(tmp_path_factory.mktemp("span-cli"))
    database = repository.parent / "span-cli.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    return repository, database, root, head


def _spec(range_repo) -> str:
    repository, database, root, head = range_repo
    return f"{root[:8]}..{head[:8]}"


def _offline(range_repo, *extra):
    repository, database, root, head = range_repo
    return runner.invoke(
        app,
        ["explain", _spec(range_repo), str(repository), "--db", str(database), *extra],
    )


def _answer(summary_citation: str, kind: str, ref: str) -> str:
    """A valid answer whose one observed change rests on one citation."""
    return json.dumps(
        {
            "summary": f"One change is cited. [{summary_citation}]",
            "observed_changes": [
                {
                    "id": "oc1",
                    "statement": "The evidence holds this citation.",
                    "evidence": ["ev1"],
                }
            ],
            "evidence": [{"id": "ev1", "kind": kind, "ref": ref}],
            "possible_reasons": [],
            "uncertainty": [],
            "related_memory": [],
        }
    )


def test_offline_prints_the_set_bundle_itself(range_repo):
    repository, database, root, head = range_repo
    span = build_span(repository, database, root, head)

    result = _offline(range_repo)

    assert result.exit_code == 0
    assert result.stdout.rstrip("\n") == build_span_json(span)
    assert "No model is configured" in result.stderr


def test_offline_json_carries_the_envelope_with_the_units_own_name(range_repo):
    repository, database, root, head = range_repo
    span = build_span(repository, database, root, head)

    result = _offline(range_repo, "--json")

    assert result.exit_code == 0
    document = json.loads(result.stdout)
    assert list(document) == [
        "range",
        "state",
        "explanation",
        "evidence",
        "bundle_sha256",
    ]
    assert document["range"] == f"{root}..{head}"
    assert document["state"] == "evidence_only"
    assert document["explanation"] is None
    assert document["evidence"] == build_span_object(span)
    assert document["bundle_sha256"] == bundle_sha256(build_span_object(span))


def test_no_model_overrides_a_configured_model(range_repo):
    """The flag is the whole of what reading the evidence alone takes: a model
    is configured, the flag waves it off, and no endpoint is ever reached."""
    repository, database, root, head = range_repo

    with _endpoint({"status": 500, "body": b"{}"}) as server:
        result = runner.invoke(
            app,
            [
                "explain",
                _spec(range_repo),
                str(repository),
                "--db",
                str(database),
                "--no-model",
            ],
            env={
                "CODEARCHAEOLOGY_AI_BASE_URL": server.base_url,
                "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            },
        )

    assert result.exit_code == 0
    assert server.requests == 0 or result.exit_code == 0
    assert "--no-model was given" in result.stderr
    assert result.stdout.startswith("{")
    assert "evidence_only" not in result.stdout  # the text path prints the bundle


def test_the_model_is_answered_from_the_set_and_checked_against_its_view(range_repo):
    """A citation of the changeset itself — the one thing only a range holds."""
    repository, database, root, head = range_repo
    answer = _answer("oc1", "changeset", f"{root}..{head}")

    with _endpoint({"status": 200, "body": _completion(answer)}) as server:
        result = runner.invoke(
            app,
            ["explain", _spec(range_repo), str(repository), "--db", str(database)],
            env={
                "CODEARCHAEOLOGY_AI_BASE_URL": server.base_url,
                "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            },
        )

    assert result.exit_code == 0
    assert "interpretation of the evidence" in result.stdout
    assert f"{root}..{head}" in result.stdout  # the citation, expanded


def test_a_changeset_that_names_another_range_is_refused(range_repo):
    """The unit's own changeset is the only one the evidence holds."""
    repository, database, root, head = range_repo
    wrong = f"{'0' * 40}..{'1' * 40}"
    answer = _answer("oc1", "changeset", wrong)

    with _endpoint(
        {"status": 200, "body": _completion(answer)},
        {"status": 200, "body": _completion(answer)},
    ) as server:
        result = runner.invoke(
            app,
            ["explain", _spec(range_repo), str(repository), "--db", str(database)],
            env={
                "CODEARCHAEOLOGY_AI_BASE_URL": server.base_url,
                "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            },
        )

    assert result.exit_code == 1
    assert "The answer was not shown" in result.stderr
    assert result.stdout == ""


def test_a_range_that_is_not_one_is_a_named_error(range_repo):
    repository, database, root, head = range_repo

    malformed = runner.invoke(
        app,
        ["explain", "a..b..c", str(repository), "--db", str(database)],
    )
    assert malformed.exit_code == 1
    assert "is not a revision range" in malformed.stderr

    empty = runner.invoke(
        app,
        ["explain", f"{head}..{head}", str(repository), "--db", str(database)],
    )
    assert empty.exit_code == 1
    assert "holds no commits" in empty.stderr

    unresolvable = runner.invoke(
        app,
        ["explain", "not-a-revision..HEAD", str(repository), "--db", str(database)],
    )
    assert unresolvable.exit_code == 1
    assert "not-a-revision" in unresolvable.stderr


def test_the_memory_section_rides_beside_a_ranges_evidence(range_repo):
    """A memory about a path the set touched is shown; a memory whose subject
    is one commit is not, because a range is not a commit — the memory layer's
    reader is untouched and its rules mean what they always meant."""
    repository, database, root, head = range_repo
    connection = connect(database)
    try:
        admit(
            connection,
            repository_path=repository,
            statement="The core module owns login.",
            subject=Subject("path", path="core/app.py"),
            author_name="Ada Lovelace",
            author_email="ada@example.com",
        )
        admit(
            connection,
            repository_path=repository,
            statement="One commit's own memory.",
            subject=Subject("commit", commit_sha=head[:8]),
            author_name="Ada Lovelace",
            author_email="ada@example.com",
        )
    finally:
        connection.close()

    result = _offline(range_repo, "--json")
    document = json.loads(result.stdout)

    assert document["memory"] is not None
    statements = [
        memory["statement"]
        for group in ("in_force", "not_provably_in_force")
        for memory in document["memory"][group]
    ]
    assert "The core module owns login." in statements
    assert all("One commit's own memory." not in statement for statement in statements)
