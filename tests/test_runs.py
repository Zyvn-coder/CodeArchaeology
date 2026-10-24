"""Tests for ``runs``: the record of what was asked of a model, and only that.

Option E's whole shape is under test here. A row is written exactly when an
explanation is printed — not offline, not on a refusal, not when the provider
never answered — and it holds the five ruled facts about the asking plus the
unit, never a sentence the model wrote. The table is not evidence: it keeps its
own stamp, it is not one of ``storage``'s tables, and the evidence does not
move when a row lands.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.context import build_context, build_json, build_object
from codearchaeology.explanation import bundle_sha256, prompt_sha256
from codearchaeology.runs import (
    RUNS_VERSION_KEY,
    TABLE,
    read_runs,
)
from codearchaeology.storage import connect, get_meta, read_schema_version
from sample_repo import build_sample_repo, git_output
from test_provider import _completion, _endpoint
from test_span_cli import _answer

runner = CliRunner()


@pytest.fixture(scope="module")
def runs_repo(tmp_path_factory: pytest.TempPathFactory):
    repository = build_sample_repo(tmp_path_factory.mktemp("runs"))
    database = repository.parent / "runs.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    return repository, database


def _sha(runs_repo, message: str) -> str:
    repository, database = runs_repo
    connection = connect(database)
    try:
        row = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (message,)
        ).fetchone()
    finally:
        connection.close()
    return row["sha"]


def _rows(runs_repo):
    repository, database = runs_repo
    connection = connect(database)
    try:
        return read_runs(connection)
    finally:
        connection.close()


def _explain(runs_repo, revision, *extra, replies=None, model="a-model"):
    repository, database = runs_repo
    endpoint = _endpoint(*(replies or []))
    with endpoint as server:
        return runner.invoke(
            app,
            ["explain", revision, str(repository), "--db", str(database), *extra],
            env={
                "CODEARCHAEOLOGY_AI_BASE_URL": server.base_url,
                "CODEARCHAEOLOGY_AI_MODEL": model,
            },
        )


def _good_answer(sha: str) -> str:
    return _answer("oc1", "commit", sha)


def test_a_printed_explanation_writes_exactly_the_ruled_facts(runs_repo):
    repository, database = runs_repo
    sha = _sha(runs_repo, "Fix login bug")
    before = _rows(runs_repo)

    result = _explain(
        runs_repo, sha[:8], "--json", replies=[{"status": 200, "body": _completion(_good_answer(sha))}]
    )

    assert result.exit_code == 0, result.stderr
    document = json.loads(result.stdout)
    rows = _rows(runs_repo)
    assert len(rows) == len(before) + 1

    row = rows[0]
    assert row.unit_kind == "commit"
    assert row.unit_spec == sha
    assert row.bundle_sha256 == document["bundle_sha256"]
    assert row.bundle_sha256 == bundle_sha256(build_object(build_context(repository, database, sha)))
    assert row.provider  # the endpoint's URL, whatever the machine made it
    assert row.model == "a-model"
    assert row.prompt_sha256 == prompt_sha256()
    datetime.fromisoformat(row.created_at)  # a moment, in a form that parses


def test_offline_and_no_model_write_nothing(runs_repo, monkeypatch):
    """The checker's own convention: the AI variables come out of the
    environment, so the offline path is offline on every machine."""
    from test_readme import AI_VARIABLES

    for name in AI_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    repository, database = runs_repo
    sha = _sha(runs_repo, "Fix login bug")
    before = len(_rows(runs_repo))

    offline = runner.invoke(
        app, ["explain", sha[:8], str(repository), "--db", str(database)]
    )
    assert offline.exit_code == 0

    waved_off = _explain(
        runs_repo,
        sha[:8],
        "--no-model",
        replies=[{"status": 200, "body": _completion(_good_answer(sha))}],
    )
    assert waved_off.exit_code == 0

    assert len(_rows(runs_repo)) == before


def test_an_answer_that_never_checked_out_writes_nothing(runs_repo):
    sha = _sha(runs_repo, "Fix login bug")
    before = len(_rows(runs_repo))
    broken = _answer("oc1", "commit", "0" * 40)

    result = _explain(
        runs_repo,
        sha[:8],
        replies=[
            {"status": 200, "body": _completion(broken)},
            {"status": 200, "body": _completion(broken)},
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert len(_rows(runs_repo)) == before


def test_a_provider_that_never_answered_writes_nothing(runs_repo):
    sha = _sha(runs_repo, "Fix login bug")
    before = len(_rows(runs_repo))

    result = _explain(
        runs_repo,
        sha[:8],
        replies=[{"status": 500, "body": b"{}"}] * 3,
    )

    assert result.exit_code == 1
    assert len(_rows(runs_repo)) == before


def test_a_range_run_records_the_range(runs_repo):
    repository, database = runs_repo
    root = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()
    head = git_output(repository, "rev-parse", "HEAD").strip()
    before = len(_rows(runs_repo))

    result = _explain(
        runs_repo,
        f"{root[:8]}..{head[:8]}",
        replies=[{"status": 200, "body": _completion(_answer("oc1", "changeset", f"{root}..{head}"))}],
    )

    assert result.exit_code == 0, result.stderr
    rows = _rows(runs_repo)
    assert len(rows) == before + 1
    assert rows[0].unit_kind == "range"
    assert rows[0].unit_spec == f"{root}..{head}"


def test_the_evidence_does_not_move_when_a_row_lands(runs_repo):
    repository, database = runs_repo
    sha = _sha(runs_repo, "Fix login bug")
    before = build_json(build_context(repository, database, sha))

    connection = connect(database)
    try:
        from codearchaeology.runs import record

        record(
            connection,
            unit_kind="commit",
            unit_spec=sha,
            bundle_sha256="0" * 64,
            provider="http://recorded",
            model="recorded-model",
            prompt_sha256="0" * 64,
            created_at="2026-01-01T00:00:00+00:00",
        )
    finally:
        connection.close()

    assert build_json(build_context(repository, database, sha)) == before


def test_the_stamp_is_its_own_and_the_evidence_schema_stands(runs_repo):
    from codearchaeology.runs import RUNS_SCHEMA_VERSION
    from codearchaeology import storage

    repository, database = runs_repo
    connection = connect(database)
    try:
        assert TABLE not in storage.TABLES
        assert get_meta(connection, RUNS_VERSION_KEY) == RUNS_SCHEMA_VERSION
        assert read_schema_version(connection) == "4"
    finally:
        connection.close()


def test_a_store_this_tool_cannot_trust_is_a_note_not_a_refusal(runs_repo):
    sha = _sha(runs_repo, "Fix login bug")
    repository, database = runs_repo
    connection = connect(database)
    try:
        from codearchaeology.storage import set_meta

        set_meta(connection, RUNS_VERSION_KEY, "99")
    finally:
        connection.close()

    try:
        result = _explain(
            runs_repo,
            sha[:8],
            replies=[{"status": 200, "body": _completion(_good_answer(sha))}],
        )
    finally:
        # The database is the module's, shared with every test after this one;
        # the untrusted stamp was this test's doing and this test puts it back.
        connection = connect(database)
        try:
            from codearchaeology.storage import set_meta

            set_meta(connection, RUNS_VERSION_KEY, "1")
        finally:
            connection.close()

    assert result.exit_code == 0, result.stderr
    assert "Note:" in result.stderr
    assert "interpretation of the evidence" in result.stdout


def test_runs_lists_newest_first_with_the_json_shape(runs_repo):
    repository, database = runs_repo
    sha = _sha(runs_repo, "Fix login bug")
    final = _sha(runs_repo, "Add logo and unicode module, drop legacy helper")
    _explain(runs_repo, sha[:8], replies=[{"status": 200, "body": _completion(_good_answer(sha))}])
    _explain(
        runs_repo,
        final[:8],
        replies=[{"status": 200, "body": _completion(_good_answer(final))}],
    )

    listed = runner.invoke(app, ["runs", str(repository), "--db", str(database), "--json"])
    assert listed.exit_code == 0, listed.stderr
    document = json.loads(listed.stdout)
    assert set(document) == {"repository", "head_sha", "runs"}
    specs = [row["unit_spec"] for row in document["runs"]]
    assert final in specs
    assert specs.index(final) < specs.index(sha)  # newest first

    limited = runner.invoke(
        app, ["runs", str(repository), "--db", str(database), "--json", "--limit", "1"]
    )
    assert len(json.loads(limited.stdout)["runs"]) == 1

    tabled = runner.invoke(app, ["runs", str(repository), "--db", str(database)])
    assert tabled.exit_code == 0
    assert "never what was answered" in tabled.stdout


def test_a_database_with_no_runs_reads_as_empty(tmp_path: Path):
    repository = build_sample_repo(tmp_path / "fresh")
    database = tmp_path / "fresh.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    result = runner.invoke(
        app, ["runs", str(repository), "--db", str(database), "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["runs"] == []

    plain = runner.invoke(app, ["runs", str(repository), "--db", str(database)])
    assert plain.exit_code == 0
    assert "No runs recorded" in plain.stdout
