"""The contract the command line and the two READMEs make with a program.

The v0.4 release audit checked the JSON of every reading command by hand, end to
end, and found one gap — ``commit`` has no JSON form. This file is that check as
a test, so the next command that appears with a different shape, or loses a key,
fails the build instead of being found by the next audit. Three contracts live
here:

- **The JSON contract.** Every reading command prints exactly one JSON document
  whose top-level keys are the ones the READMEs show, and the one command
  without a JSON form is pinned as the gap the documentation states.
- **The exit contract.** A refusal is ``Error: <sentence>`` on stderr, exit 1,
  and nothing on stdout — which is what keeps ``--json`` parseable when a note
  is due.
- **The documentation contract.** The three statements a v0.5 reader has to be
  able to find in both READMEs: that a memory cannot be rebuilt from anything,
  that a memory is never evidence, and that a model never writes one.
"""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app

# The walker that knows a group's commands are commands too. It lives beside the
# README checker because that is where the drift it catches first appeared.
from test_readme import _registered_commands

runner = CliRunner()

ROOT = Path(__file__).parent.parent

STATEMENT = "We keep the login helper in one module: the split state machine caused an incident."

# The keys `docs/v0.5-cli-design.md` §8 freezes for one memory, and the same
# object in all three places it appears: `memory show --json`, inside `memory
# list --json`, and inside `explain --json`'s `memory` section.
MEMORY_KEYS = {
    "memory_id",
    "repository",
    "statement",
    "author",
    "admitted_at",
    "state",
    "subject",
    "since",
    "ended_at",
    "end_reason",
    "superseded_by",
    "supersedes",
    "citations",
}

# What `structure <path> --json` prints for a version that parses. The nulls are
# the unreadable-file columns, which are present whether or not there is a
# failure — a shape that changes with the answer is a shape a program cannot
# rely on.
STRUCTURE_KEYS = {
    "path",
    "path_history",
    "commit_sha",
    "committed_at",
    "compared_with",
    "state",
    "reason",
    "parse_error",
    "error_lineno",
    "error_offset",
    "blind_spots",
    "definitions",
}

# The three statements, one phrase each, in the language they are written in.
# They are pinned rather than trusted because each one is the whole of a rule a
# reader acts on, and prose is the easiest thing in this project to lose in an
# edit.
DOCUMENTED = {
    "README.md": (
        "cannot be rebuilt from anything",  # the cache exception
        "never treated as evidence itself",  # memory is not evidence
        "the model never writes memory",  # AI is not memory
    ),
    "README.zh-CN.md": (
        "什么也重建不了",  # 缓存例外
        "永远不把这些话当成证据",  # memory 不是证据
        "模型永远不写 memory",  # AI 不是 memory
    ),
}


@pytest.fixture
def contract(sample_repo: Path, tmp_path: Path):
    """An analyzed repository with its structure read and one memory in it.

    Built through the tool — `analyze`, `ast`, `memory create` — so the state
    and the blocks that document it cannot disagree about what is there.
    """
    database = tmp_path / "history.db"
    analyze(sample_repo, database)
    run_ast_pass(sample_repo, database)

    commits = _commits(sample_repo, database)
    head = commits[0]["sha"]
    created = _run(
        sample_repo,
        database,
        "memory",
        "create",
        STATEMENT,
        "--about-path",
        "core/app.py",
        "--cite-commit",
        head,
        "--cite-file",
        "core/app.py",
    )
    assert created.exit_code == 0, created.stderr

    return sample_repo, database


def _run(repository: Path, database: Path, *arguments: str):
    return runner.invoke(
        app, [*arguments, str(repository), "--db", str(database)]
    )


def _commits(repository: Path, database: Path) -> list[dict]:
    result = _run(repository, database, "timeline", "--json")
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)["commits"]


def _reading_commands(repository: Path, database: Path) -> dict[str, tuple]:
    """Every reading command that takes ``--json``, and the keys it documents.

    A function of the fixture rather than a constant, because two of the
    commands address a commit and the fixture's shas are its own.
    """
    commits = _commits(repository, database)
    head = commits[0]["sha"][:8]
    merge = next(
        commit["sha"][:8]
        for commit in commits
        if commit["message"].startswith("Merge")
    )
    return {
        "timeline": (("timeline", "--json"), {"repository", "head_sha", "commits"}),
        "hotspots": (("hotspots", "--json"), {"repository", "head_sha", "files"}),
        "files": (("files", "--json"), {"repository", "head_sha", "files"}),
        "file": (("file", "core/app.py", "--json"), {"path", "files"}),
        "structure": (("structure", "core/app.py", "--json"), STRUCTURE_KEYS),
        "structure --history": (
            ("structure", "core/app.py", "--history", "--json"),
            {"path", "files"},
        ),
        "cochange": (
            ("cochange", "core/app.py", "--json"),
            {"repository", "head_sha", "path", "files"},
        ),
        "explain": (
            ("explain", head, "--json"),
            {"commit", "state", "explanation", "evidence", "memory"},
        ),
        "memory list": (
            ("memory", "list", "--json"),
            {"repository", "head_sha", "memories"},
        ),
        # The one reading command that is not about the evidence: the section is
        # absent when nothing is related, which the next test holds.
        "explain (nothing related)": (
            ("explain", merge, "--json"),
            {"commit", "state", "explanation", "evidence"},
        ),
    }


def test_every_reading_command_prints_the_documented_json(contract) -> None:
    repository, database = contract

    for name, (arguments, keys) in _reading_commands(repository, database).items():
        result = _run(repository, database, *arguments)

        assert result.exit_code == 0, f"{name}: {result.stderr}"
        # The whole of stdout is parsed, so a second document, a stray line or a
        # note would fail here rather than being skipped over.
        document = json.loads(result.stdout)
        assert set(document) == keys, f"{name} prints {sorted(document)}"


def test_one_memory_is_one_shape_in_all_three_places(contract) -> None:
    """`memory show`, `memory list` and `explain` print the same object.

    A program that learns one shape has learned them all, which is the sentence
    `docs/v0.5-cli-design.md` §8.1 makes and this test holds.
    """
    repository, database = contract

    listed = json.loads(_run(repository, database, "memory", "list", "--json").stdout)
    shown = json.loads(
        _run(
            repository,
            database,
            "memory",
            "show",
            listed["memories"][0]["memory_id"],
            "--json",
        ).stdout
    )
    explained = json.loads(
        _run(
            repository,
            database,
            "explain",
            listed["memories"][0]["citations"][0]["ref"],
            "--json",
        ).stdout
    )

    assert set(listed["memories"][0]) == MEMORY_KEYS
    assert set(shown) == MEMORY_KEYS
    # The memory is related to the commit it cites; it has no start, so it sits
    # in the second group — and it is the same object there as in the other two.
    related = explained["memory"]["in_force"] + explained["memory"]["not_provably_in_force"]
    assert len(related) == 1
    assert set(related[0]) == MEMORY_KEYS
    assert shown["memory_id"] == listed["memories"][0]["memory_id"]


def test_commit_still_has_no_json_form(contract) -> None:
    """The one interface gap both READMEs state, pinned so it cannot drift.

    If `commit` gains a `--json`, the sentence and this test have to change
    together — which is the point of writing the gap down rather than leaving it
    to be rediscovered.
    """
    repository, database = contract
    head = _commits(repository, database)[0]["sha"]

    result = _run(repository, database, "commit", head, "--json")

    # Click's own usage-error code rather than the tool's 0 and 1: an unknown
    # option never reaches the tool.
    assert result.exit_code != 0
    assert "No such option: --json" in result.stderr


def test_a_refusal_is_a_sentence_on_stderr_and_nothing_on_stdout(contract) -> None:
    repository, database = contract

    for arguments in (("file", "nope.py", "--json"), ("memory", "show", "not-an-id")):
        result = _run(repository, database, *arguments)

        assert result.exit_code == 1, arguments
        assert result.stdout == "", arguments
        assert result.stderr.startswith("Error: "), arguments


def test_every_command_has_working_help() -> None:
    """`--help` is the interface's own documentation, and it has to work.

    Every registered command and every command inside a group, invoked the way a
    person would, with no repository and no database: help must not need either.
    The list comes from the README checker's walker rather than a second copy of
    it, because that walker is the thing that knows a sub-application's commands
    exist, and two copies would be two things to keep in step.
    """
    for name in _registered_commands():
        result = runner.invoke(app, [*name.split(), "--help"])

        assert result.exit_code == 0, f"{name}: {result.stderr}"
        assert "Usage" in result.stdout, name


def test_the_three_statements_are_in_both_readmes() -> None:
    for name, phrases in DOCUMENTED.items():
        text = _squeezed((ROOT / name).read_text(encoding="utf-8"))

        assert phrases, "the table is empty, so this check would pass by looking for nothing"
        for phrase in phrases:
            assert _squeezed(phrase) in text, f"{name} no longer says {phrase!r}"


def _squeezed(text: str) -> str:
    """The text with its whitespace taken out, because prose is wrapped.

    A phrase that spans a line break is still the phrase, in either language; a
    checker that failed on the wrap would be a checker people edit the sentence
    to satisfy rather than a checker that keeps the sentence.
    """
    return re.sub(r"\s+", "", text)
