"""Tests for the memory section ``explain`` carries, offline and to the model.

Two things are under test, and they are the two halves of the phase's contract:
**which memories belong beside a commit** — the six relations, the order, the
cap and the two temporal groups — and **that memory never becomes evidence**.
The second is the one that would be invisible if it broke: an answer that cited
a memory as an observation would read exactly like an answer that did not.

The rules are Unit 1 §9.2, §11.3 and §15.3 and Unit 3 §8.4, §9.3 and §10.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.context import build_context, build_json as build_context_json
from codearchaeology.explanation import build_prompt
from codearchaeology.memory import Subject, admit, prepare_memory
from codearchaeology.memory_section import CAP
from codearchaeology.storage import connect
from sample_repo import build_sample_repo, git_output
from test_provider import _completion, _endpoint

runner = CliRunner()

FIXED = "Fix login bug"
INITIAL = "Initial commit"
FINAL = "Add logo and unicode module, drop legacy helper"


@pytest.fixture
def database(sample_repo: Path, tmp_path: Path) -> Path:
    """The sample fixture, analyzed and read, with its database beside it.

    Per test rather than per module: every test here writes memories, and a
    shared database would let one test read what another left behind.
    """
    path = tmp_path / "history.db"
    analyze(sample_repo, path)
    run_ast_pass(sample_repo, path)
    return path


def _sha(database: Path, message: str) -> str:
    connection = connect(database)
    try:
        return connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (message,)
        ).fetchone()["sha"]
    finally:
        connection.close()


def _create(repository: Path, database: Path, *arguments: str):
    return runner.invoke(
        app, ["memory", "create", *arguments, str(repository), "--db", str(database)]
    )


def _explain(repository: Path, database: Path, sha: str, *flags: str, **environment):
    return runner.invoke(
        app,
        ["explain", sha[:8], str(repository), "--db", str(database), *flags],
        env=environment or None,
    )


def _memory_ids(database: Path, repository: Path) -> list[str]:
    """Every memory of the repository, oldest admission first."""
    connection = connect(database)
    try:
        return [
            row["memory_id"]
            for row in connection.execute(
                "SELECT memory_id FROM memories WHERE repository_path = ?"
                " ORDER BY admitted_epoch, admitted_at",
                (str(Path(repository).resolve()),),
            )
        ]
    finally:
        connection.close()


def _admit(
    database: Path,
    repository: Path,
    statement: str,
    *,
    subject: Subject,
    since: str | None = None,
    citations=(),
):
    """Write a memory through the store, for what the command line cannot say.

    A ``since`` is the author's claim about the project and the acts take it —
    ``create``'s flags do not include it, which is the one gap Unit 8 found and
    left for the user to decide about (see ``PROGRESS.md``). The section's
    temporal rule is reachable either way, and these tests reach it this way.
    """
    connection = connect(database)
    try:
        prepare_memory(connection)
        return admit(
            connection,
            repository_path=repository,
            statement=statement,
            subject=subject,
            since_commit_sha=since,
            citations=citations,
            author_name="Ada Lovelace",
            author_email="ada@example.com",
        )
    finally:
        connection.close()


def _section(result) -> dict:
    return json.loads(result.stdout)["memory"]


def _answer(sha: str, *, related: list | None = None) -> str:
    document = {
        "summary": "The commit changed core/app.py, adding six lines [oc1].",
        "observed_changes": [
            {
                "id": "oc1",
                "statement": "core/app.py was modified in this commit",
                "evidence": ["ev1"],
            }
        ],
        "evidence": [{"id": "ev1", "kind": "file", "ref": "core/app.py"}],
        "possible_reasons": [],
        "uncertainty": [],
    }
    if related is not None:
        document["related_memory"] = related
    return json.dumps(document)


# --- which memories are related ----------------------------------------------


def test_a_memory_about_a_path_the_commit_touched_is_related(
    sample_repo: Path, database: Path
) -> None:
    _create(
        sample_repo, database, "Login lives here.", "--about-path", "core/app.py"
    )

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert [entry["subject"]["path"] for entry in section["not_provably_in_force"]] == [
        "core/app.py"
    ]


def test_a_memory_about_a_name_the_file_had_before_is_related(
    sample_repo: Path, database: Path
) -> None:
    """v0.2's identity rule, applied to relevance: the file, not the string.

    ``app.py`` was renamed to ``core/app.py`` before the commit being read. A
    memory filed under the old name is a memory about the same file, and a
    section that matched paths literally would lose exactly the knowledge that
    outlived the rename.
    """
    _create(sample_repo, database, "The old name.", "--about-path", "app.py")

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert [entry["subject"]["path"] for entry in section["not_provably_in_force"]] == [
        "app.py"
    ]


def test_a_memory_about_a_definition_the_commit_changed_is_related(
    sample_repo: Path, database: Path
) -> None:
    _create(
        sample_repo,
        database,
        "Login is the only door in.",
        "--about-path",
        "core/app.py",
        "--about-definition",
        "login",
    )

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert section["not_provably_in_force"][0]["subject"]["qualname"] == "login"


def test_a_memory_about_the_commit_itself_is_related(
    sample_repo: Path, database: Path
) -> None:
    sha = _sha(database, FIXED)
    _create(sample_repo, database, "About this very commit.", "--about-commit", sha[:8])

    section = _section(_explain(sample_repo, database, sha, "--json"))

    assert section["not_provably_in_force"][0]["subject"] == {
        "kind": "commit",
        "commit": sha,
        "resolution": "resolved",
    }


def test_a_memory_that_cites_the_commit_is_related(
    sample_repo: Path, database: Path
) -> None:
    """The relation the evidence makes, not the subject: it was made from this."""
    sha = _sha(database, FIXED)
    _create(
        sample_repo,
        database,
        "Written after reading this commit.",
        "--about-path",
        "assets/logo.png",
        "--cite-commit",
        sha[:8],
    )

    section = _section(_explain(sample_repo, database, sha, "--json"))

    assert section["not_provably_in_force"][0]["citations"] == [
        {"kind": "commit", "ref": sha[:8], "resolution": "resolved"}
    ]


def test_a_memory_about_the_repository_is_related(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "We ship on Fridays.", "--about-repository")

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert section["not_provably_in_force"][0]["subject"] == {
        "kind": "repository",
        "resolution": "resolved",
    }


def test_a_memory_about_another_file_is_not_related(
    sample_repo: Path, database: Path
) -> None:
    """The negative case, without which every test above would pass on a
    selection that related everything to everything."""
    _create(sample_repo, database, "About the logo.", "--about-path", "assets/logo.png")

    result = _explain(sample_repo, database, _sha(database, FIXED))

    assert "memory" not in json.loads(_explain(
        sample_repo, database, _sha(database, FIXED), "--json"
    ).stdout)
    assert "Memory (" not in result.stdout


# --- the order, and the cap --------------------------------------------------


def test_the_most_specific_subject_comes_first(
    sample_repo: Path, database: Path
) -> None:
    """One memory per relation, written least specific first, read most first."""
    sha = _sha(database, FIXED)
    _create(sample_repo, database, "The project.", "--about-repository")
    _create(
        sample_repo,
        database,
        "A citation.",
        "--about-path",
        "assets/logo.png",
        "--cite-commit",
        sha[:8],
    )
    _create(sample_repo, database, "A path.", "--about-path", "core/app.py")
    _create(
        sample_repo,
        database,
        "A definition.",
        "--about-path",
        "core/app.py",
        "--about-definition",
        "login",
    )
    _create(sample_repo, database, "The commit.", "--about-commit", sha[:8])

    section = _section(_explain(sample_repo, database, sha, "--json"))

    assert [entry["statement"] for entry in section["not_provably_in_force"]] == [
        "The commit.",
        "A definition.",
        "A path.",
        "A citation.",
        "The project.",
    ]


def test_memories_of_the_same_rank_come_newest_first(
    sample_repo: Path, database: Path
) -> None:
    for number in range(3):
        _create(sample_repo, database, f"Statement {number}.", "--about-path", "core/app.py")

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert [entry["statement"] for entry in section["not_provably_in_force"]] == [
        "Statement 2.",
        "Statement 1.",
        "Statement 0.",
    ]


def test_the_cap_leaves_the_rest_out_and_counts_them(
    sample_repo: Path, database: Path
) -> None:
    """A truncated list that reads as complete is the failure this rule is for."""
    for number in range(CAP + 2):
        _create(sample_repo, database, f"Statement {number}.", "--about-path", "core/app.py")

    result = _explain(sample_repo, database, _sha(database, FIXED), "--json")
    section = _section(result)

    assert len(section["not_provably_in_force"]) == CAP
    assert section["selection"] == {"shown": CAP, "omitted": 2}
    assert [entry["statement"] for entry in section["not_provably_in_force"]] == [
        f"Statement {number}." for number in (6, 5, 4, 3, 2)
    ]


def test_the_block_says_how_many_were_left_out(
    sample_repo: Path, database: Path
) -> None:
    """The count in the block form, where there is no object to carry it.

    The block is the model path's form; the offline answer is the bundle object,
    and there the count is ``memory.selection`` instead — which the test above
    pins. Both are the same rule: every dropped memory is counted somewhere.
    """
    sha = _sha(database, FIXED)
    for number in range(CAP + 1):
        _create(sample_repo, database, f"Statement {number}.", "--about-path", "core/app.py")

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _explain(
            sample_repo,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0, result.stderr
    assert "1 more related memory was not shown." in result.stdout


def test_a_section_that_was_not_capped_carries_no_selection(
    sample_repo: Path, database: Path
) -> None:
    """Absent is a statement: nothing was dropped, so nothing is said about it."""
    _create(sample_repo, database, "One statement.", "--about-path", "core/app.py")

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert "selection" not in section


# --- in force, and not provably in force -------------------------------------


def test_a_memory_whose_start_is_known_and_earlier_is_in_force(
    sample_repo: Path, database: Path
) -> None:
    _admit(
        database,
        sample_repo,
        "Login lives in core/app.py.",
        subject=Subject("path", path="core/app.py"),
        since=_sha(database, INITIAL),
    )

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert [entry["statement"] for entry in section["in_force"]] == [
        "Login lives in core/app.py."
    ]
    assert section["not_provably_in_force"] == []


def test_the_command_line_can_now_make_an_in_force_memory(
    sample_repo: Path, database: Path
) -> None:
    """The gap Unit 8 found, closed: `--since-commit` is a person's to give.

    Before this flag existed the temporal rule was implemented, tested and
    unreachable — every memory written through the command line had an unknown
    start and landed in the second group. This is the test that says a person
    can now put one in the first.
    """
    first = git_output(sample_repo, "rev-list", "--max-parents=0", "HEAD").strip()
    created = _create(
        sample_repo,
        database,
        "Login lives in core/app.py.",
        "--about-path",
        "core/app.py",
        "--since-commit",
        first[:8],
    )
    assert created.exit_code == 0, created.stderr

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert [entry["statement"] for entry in section["in_force"]] == [
        "Login lives in core/app.py."
    ]
    assert section["in_force"][0]["since"]["commit"] == first
    assert section["not_provably_in_force"] == []


def test_a_start_after_the_commit_is_not_in_force(
    sample_repo: Path, database: Path
) -> None:
    """A rule from a later commit is not the reason for this one."""
    _admit(
        database,
        sample_repo,
        "Written after the fact.",
        subject=Subject("path", path="core/app.py"),
        since=_sha(database, FINAL),
    )

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert section["in_force"] == []
    assert len(section["not_provably_in_force"]) == 1


def test_a_memory_with_no_start_is_not_provably_in_force(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "Nobody said when.", "--about-path", "core/app.py")

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    assert section["in_force"] == []
    assert section["not_provably_in_force"][0]["since"] is None


def test_a_memory_that_ended_before_the_commit_is_not_in_force(
    sample_repo: Path, database: Path
) -> None:
    """Ended before this commit, so it was not in force when the code was written.

    The end is the store's own clock — these fixtures commit in 2024 and the
    memory is ended now — so an ended memory is always "ended after" a fixture
    commit and lands in force. That is the rule, and the test says so: what is
    excluded is a memory ended *before* the commit being read, which the storage
    freeze's comparison is written to catch.
    """
    memory = _admit(
        database,
        sample_repo,
        "Superseded later.",
        subject=Subject("path", path="core/app.py"),
        since=_sha(database, INITIAL),
    )
    runner.invoke(
        app,
        [
            "memory",
            "invalidate",
            memory.memory_id[:8],
            "--reason",
            "it stopped applying",
            str(sample_repo),
            "--db",
            str(database),
        ],
    )

    section = _section(_explain(sample_repo, database, _sha(database, FIXED), "--json"))

    # In force at that commit, and printed as history rather than as a rule.
    assert len(section["in_force"]) == 1
    assert section["in_force"][0]["state"] == "invalidated"


def test_a_superseded_memory_is_shown_as_history_with_its_successor(
    sample_repo: Path, database: Path
) -> None:
    """§9.2: never as a current rule, and its successor is named."""
    old = _admit(
        database,
        sample_repo,
        "Login lives in core/app.py.",
        subject=Subject("path", path="core/app.py"),
        since=_sha(database, INITIAL),
    )
    replaced = runner.invoke(
        app,
        [
            "memory",
            "supersede",
            old.memory_id[:8],
            "Login moved into its own module.",
            "--about-path",
            "core/app.py",
            str(sample_repo),
            "--db",
            str(database),
        ],
    )
    assert replaced.exit_code == 0, replaced.stderr
    successor = _memory_ids(database, sample_repo)[1]
    sha = _sha(database, FIXED)

    section = _section(_explain(sample_repo, database, sha, "--json"))
    assert section["in_force"][0]["state"] == "superseded"
    assert section["in_force"][0]["superseded_by"] == successor

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _explain(
            sample_repo,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert "in force at this commit" in result.stdout
    assert "superseded 2026-" in result.stdout
    assert f"replaced by {successor[:8]}" in result.stdout


def test_a_since_commit_a_rewrite_removed_is_not_in_force(
    tmp_path: Path,
) -> None:
    """The start cannot be found, so the span cannot be shown to cover anything."""
    from sample_repo import build_single_commit_repo, add_commit

    repository = build_single_commit_repo(tmp_path / "rewritten")
    add_commit(repository, "a second commit")
    database = tmp_path / "rewritten.db"
    analyze(repository, database)
    second = git_output(repository, "rev-parse", "HEAD").strip()

    _admit(
        database,
        repository,
        "Said after the second commit.",
        subject=Subject("repository"),
        since=second,
    )
    git_output(repository, "reset", "--hard", "HEAD~1")
    analyze(repository, database)

    section = _section(
        _explain(repository, database, git_output(repository, "rev-parse", "HEAD").strip(), "--json")
    )

    assert section["in_force"] == []
    assert len(section["not_provably_in_force"]) == 1


# --- the two paths, and the bytes that must not move -------------------------


def test_with_no_memories_the_offline_output_is_byte_identical(
    sample_repo: Path, database: Path
) -> None:
    """Acceptance criterion 5: the change is additive where it can be.

    A repository with no memories prints exactly what v0.4 printed, in both
    forms, because the `memory` key is absent when there is nothing to show.
    """
    sha = _sha(database, FIXED)

    assert _explain(sample_repo, database, sha).stdout.strip() == build_context_json(
        build_context(sample_repo, database, sha)
    ).strip()

    envelope = json.loads(_explain(sample_repo, database, sha, "--json").stdout)
    assert "memory" not in envelope
    assert envelope["explanation"] is None
    assert envelope["evidence"]["commit"]["sha"] == sha


def test_the_offline_block_carries_the_memory_key(
    sample_repo: Path, database: Path
) -> None:
    """The offline answer is the bundle object, with one added key beside it."""
    _create(sample_repo, database, "Login lives here.", "--about-path", "core/app.py")

    document = json.loads(_explain(sample_repo, database, _sha(database, FIXED)).stdout)

    assert set(document) == {
        "commit",
        "file_changes",
        "lifecycle",
        "ast_changes",
        "cochange",
        "history",
        "absences",
        "bounds",
        "memory",
    }
    assert document["memory"]["not_provably_in_force"][0]["statement"] == "Login lives here."


def test_the_prompt_is_the_bytes_it_always_was_with_no_memories(
    sample_repo: Path, database: Path
) -> None:
    """The model's evidence stays exactly the bundle when there is nothing else."""
    sha = _sha(database, FIXED)
    evidence = build_context_json(build_context(sample_repo, database, sha))

    assert build_prompt(evidence) == build_prompt(evidence, "")
    assert "The memories:" not in build_prompt(evidence)


def test_the_model_is_shown_the_section_and_may_relate_to_one(
    sample_repo: Path, database: Path
) -> None:
    sha = _sha(database, FIXED)
    _create(sample_repo, database, "Login lives here.", "--about-path", "core/app.py")
    memory_id = _memory_ids(database, sample_repo)[0]

    with _endpoint(
        {
            "status": 200,
            "body": _completion(
                _answer(
                    sha,
                    related=[
                        {"memory_id": memory_id, "note": "the answer keeps login in one place"}
                    ],
                )
            ),
        }
    ) as server:
        result = _explain(
            sample_repo,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 0, result.stderr
    assert "Memory (0 in force, 1 related)" in result.stdout
    assert "related by the model: the answer keeps login in one place" in result.stdout
    # The statement is the store's bytes, and the model's sentence is marked as
    # the reading rather than printed as part of it.
    assert result.stdout.index("Login lives here.") < result.stdout.index("related by the model")


def test_the_prompt_carries_the_section_under_its_own_heading(
    sample_repo: Path, database: Path
) -> None:
    """The model is told what a memory is, and the bundle is not where it goes."""
    sha = _sha(database, FIXED)
    _create(sample_repo, database, "Login lives here.", "--about-path", "core/app.py")

    with _endpoint({"status": 200, "body": _completion(_answer(sha))}) as server:
        result = _explain(
            sample_repo,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )
        sent = server.requests[0]["body"]["messages"][0]["content"]

    assert result.exit_code == 0, result.stderr
    assert "What people stated about this project" in sent
    assert "Login lives here." in sent
    # The section is not inside the bundle: the evidence the model is shown is
    # the bytes v0.4 sent, and memory is a second labelled block after it.
    evidence, _, memories = sent.partition("The memories:")
    assert "Login lives here." not in evidence
    assert '"in_force"' in memories


def test_an_answer_that_relates_to_a_memory_it_was_not_shown_is_refused(
    sample_repo: Path, database: Path
) -> None:
    """Checked against the section, not the store — the same rule as a citation."""
    sha = _sha(database, FIXED)
    _create(sample_repo, database, "Login lives here.", "--about-path", "core/app.py")
    invented = "3f2a9c1e-8b47-4d6a-9f10-2c5e7a0b41d9"

    with _endpoint(
        {"status": 200, "body": _completion(_answer(sha, related=[{"memory_id": invented, "note": "x"}]))},
        {"status": 200, "body": _completion(_answer(sha, related=[{"memory_id": invented, "note": "x"}]))},
    ) as server:
        result = _explain(
            sample_repo,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert "is not in the memory section this answer was shown" in result.stderr
    assert "Observed" not in result.stdout


def test_a_memory_cannot_be_cited_as_evidence(
    sample_repo: Path, database: Path
) -> None:
    """The semantic pass refuses it for free, and this is the test that says so.

    Memory is not in the bundle, so a `file` citation whose ref is a memory's
    path is fine and a citation of the memory itself is not — there is no kind
    under which one can be named.
    """
    sha = _sha(database, FIXED)
    _create(sample_repo, database, "Login lives here.", "--about-path", "core/app.py")
    memory_id = _memory_ids(database, sample_repo)[0]

    document = json.loads(_answer(sha))
    document["evidence"] = [
        {"id": "ev1", "kind": "file", "ref": memory_id}
    ]
    document["observed_changes"][0]["evidence"] = ["ev1"]
    with _endpoint(
        {"status": 200, "body": _completion(json.dumps(document))},
        {"status": 200, "body": _completion(json.dumps(document))},
    ) as server:
        result = _explain(
            sample_repo,
            database,
            sha,
            CODEARCHAEOLOGY_AI_BASE_URL=server.base_url,
            CODEARCHAEOLOGY_AI_MODEL="a-model",
        )

    assert result.exit_code == 1
    assert "which is not in the evidence for this commit" in result.stderr


def test_an_unreadable_memory_store_is_a_note_and_the_evidence_still_prints(
    sample_repo: Path, database: Path
) -> None:
    """A newer memory stamp refuses memory, and never refuses an explanation."""
    connection = connect(database)
    try:
        from codearchaeology.memory import MEMORY_VERSION_KEY
        from codearchaeology.storage import set_meta

        prepare_memory(connection)
        set_meta(connection, MEMORY_VERSION_KEY, "9")
    finally:
        connection.close()

    result = _explain(sample_repo, database, _sha(database, FIXED), "--json")

    assert result.exit_code == 0, result.stderr
    assert "newer tool" in result.stderr
    document = json.loads(result.stdout)
    assert "memory" not in document
    assert document["evidence"]["commit"]["sha"] == _sha(database, FIXED)
