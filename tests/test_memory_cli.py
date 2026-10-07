"""Tests for the memory commands: the first slice a person can use with no model.

``memory create``, ``memory list`` and ``memory show`` are Core First made
visible — a memory is written, kept and read back without anything reaching a
network — and the rules they obey are Unit 3's (`docs/v0.5-cli-design.md`).
Everything a command refuses is here with the sentence it refuses with, because
the refusal is what a person acts on.
"""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.memory import (
    ACTIVE,
    Subject as MemorySubject,
    admit,
    invalidate,
    read_memories,
    supersede,
)
from codearchaeology.storage import connect
from sample_repo import (
    INITIAL_DATE,
    LATER_DATE,
    _commit,
    _configure,
    _write_file,
    add_commit,
    build_cochange_repo,
    build_single_commit_repo,
    git_output,
)

runner = CliRunner()

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture
def database(sample_repo: Path, tmp_path: Path) -> Path:
    """An analyzed repository whose structure has been read.

    Per test rather than per module, because the memory commands write: a shared
    database would let one test read what another left behind.
    """
    path = tmp_path / "history.db"
    analyze(sample_repo, path)
    run_ast_pass(sample_repo, path)
    return path


@pytest.fixture
def plain_database(sample_repo: Path, tmp_path: Path) -> Path:
    """The same repository with only the git facts read — no structure."""
    path = tmp_path / "plain.db"
    analyze(sample_repo, path)
    return path


def _run(repository: Path, database: Path, *arguments: str):
    return runner.invoke(
        app, ["memory", *arguments, str(repository), "--db", str(database)]
    )


def _create(repository: Path, database: Path, *arguments: str):
    return _run(repository, database, "create", *arguments)


def _stored(database: Path, repository: Path):
    connection = connect(database)
    try:
        return read_memories(connection, repository, include_ended=True)
    finally:
        connection.close()


# --- writing one -------------------------------------------------------------


def test_create_writes_a_memory_and_prints_what_it_wrote(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo,
        database,
        "We keep authentication in one module.",
        "--about-path",
        "core/app.py",
        "--cite-file",
        "core/app.py",
    )

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Memory      ")
    assert UUID.match(lines[0].removeprefix("Memory      "))
    assert lines[1] == "Subject     core/app.py"
    assert lines[2] == "Evidence    1 citation"
    assert lines[3].startswith("Database    ")

    written = _stored(database, sample_repo)
    assert len(written) == 1
    assert written[0].statement == "We keep authentication in one module."
    assert written[0].state == ACTIVE
    assert [citation.kind for citation in written[0].citations] == ["file"]


def test_create_records_the_identity_the_repository_gives(
    sample_repo: Path, database: Path
) -> None:
    """The author is the git configuration's, and never a guess."""
    _create(sample_repo, database, "A statement.", "--about-repository")

    written = _stored(database, sample_repo)[0]
    assert (written.author_name, written.author_email) == (
        "Ada Lovelace",
        "ada@example.com",
    )


def test_create_says_when_nothing_was_attached(
    sample_repo: Path, database: Path
) -> None:
    result = _create(sample_repo, database, "A rule.", "--about-repository")
    assert "Evidence    none attached" in result.stdout
    assert _stored(database, sample_repo)[0].citations == ()


def test_create_resolves_a_commit_subject_to_its_full_sha(
    sample_repo: Path, database: Path
) -> None:
    sha = git_output(sample_repo, "rev-parse", "HEAD").strip()

    result = _create(
        sample_repo, database, "About the last commit.", "--about-commit", sha[:8]
    )

    assert result.exit_code == 0, result.stderr
    subject = _stored(database, sample_repo)[0].subject
    assert subject.kind == "commit"
    assert subject.commit_sha == sha


def test_create_reads_the_statement_from_stdin(
    sample_repo: Path, database: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "memory",
            "create",
            "-",
            "--about-repository",
            str(sample_repo),
            "--db",
            str(database),
        ],
        input="A statement that came down a pipe.\n",
    )

    assert result.exit_code == 0, result.stderr
    assert _stored(database, sample_repo)[0].statement == (
        "A statement that came down a pipe."
    )


def test_create_needs_a_subject(sample_repo: Path, database: Path) -> None:
    result = _create(sample_repo, database, "A statement with no subject.")

    assert result.exit_code == 1
    assert "a memory is about one thing" in result.stderr
    assert result.stdout == ""


def test_create_refuses_two_subjects(sample_repo: Path, database: Path) -> None:
    result = _create(
        sample_repo,
        database,
        "A statement.",
        "--about-repository",
        "--about-path",
        "core/app.py",
    )

    assert result.exit_code == 1
    assert "a memory is about one thing" in result.stderr


def test_create_refuses_a_definition_without_its_path(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo, database, "A statement.", "--about-definition", "login"
    )

    assert result.exit_code == 1
    assert "give --about-path too" in result.stderr


def test_create_refuses_a_path_the_history_never_touched(
    sample_repo: Path, database: Path
) -> None:
    result = _create(sample_repo, database, "A statement.", "--about-path", "nope.py")

    assert result.exit_code == 1
    assert "nothing in the stored history touched nope.py" in result.stderr
    assert "archaeology files" in result.stderr


def test_create_refuses_a_commit_that_is_not_stored(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo, database, "A statement.", "--about-commit", "abcdef12"
    )

    assert result.exit_code == 1
    assert "no stored commit starts with 'abcdef12'" in result.stderr


def test_create_refuses_a_definition_whose_structure_was_not_read(
    sample_repo: Path, plain_database: Path
) -> None:
    result = _create(
        sample_repo,
        plain_database,
        "A statement.",
        "--about-path",
        "core/app.py",
        "--about-definition",
        "login",
    )

    assert result.exit_code == 1
    assert "run 'archaeology ast' first" in result.stderr


def test_create_accepts_a_definition_the_pass_has_read(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo,
        database,
        "The login helper stays small.",
        "--about-path",
        "core/app.py",
        "--about-definition",
        "login",
    )

    assert result.exit_code == 0, result.stderr
    assert _stored(database, sample_repo)[0].subject.qualname == "login"


def test_create_refuses_an_empty_statement(
    sample_repo: Path, database: Path
) -> None:
    result = _create(sample_repo, database, "   ", "--about-repository")

    assert result.exit_code == 1
    assert "this one is empty" in result.stderr


def test_create_refuses_a_statement_over_the_cap(
    sample_repo: Path, database: Path
) -> None:
    result = _create(sample_repo, database, "x" * 4001, "--about-repository")

    assert result.exit_code == 1
    assert "at most 4000 characters" in result.stderr


def test_create_needs_an_analysis(sample_repo: Path, tmp_path: Path) -> None:
    result = _create(
        sample_repo, tmp_path / "nothing.db", "A statement.", "--about-repository"
    )

    assert result.exit_code == 1
    assert "run 'archaeology analyze" in result.stderr


def test_create_refuses_a_database_that_holds_another_repository(
    sample_repo: Path, database: Path, tmp_path: Path
) -> None:
    other = build_single_commit_repo(tmp_path / "other")

    result = _create(other, database, "A statement.", "--about-repository")

    assert result.exit_code == 1
    assert str(sample_repo) in result.stderr


# --- the citations -----------------------------------------------------------


def test_create_keeps_the_citations_it_resolved(
    sample_repo: Path, database: Path
) -> None:
    sha = git_output(sample_repo, "rev-parse", "HEAD").strip()

    result = _create(
        sample_repo,
        database,
        "The rename happened here.",
        "--about-path",
        "core/app.py",
        "--cite-commit",
        sha[:8],
        "--cite-file",
        "core/app.py",
        "--cite-absence",
        "parse_failed",
    )

    assert result.exit_code == 0, result.stderr
    assert "Evidence    3 citations" in result.stdout
    assert [citation.kind for citation in _stored(database, sample_repo)[0].citations] == [
        "commit",
        "file",
        "absence",
    ]


def test_create_refuses_a_commit_citation_that_is_not_stored(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo,
        database,
        "A statement.",
        "--about-repository",
        "--cite-commit",
        "abcdef12",
    )

    assert result.exit_code == 1
    assert "is not in the stored history" in result.stderr


def test_create_refuses_a_file_citation_that_was_never_touched(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo,
        database,
        "A statement.",
        "--about-repository",
        "--cite-file",
        "nope.py",
    )

    assert result.exit_code == 1
    assert "was never touched" in result.stderr


def test_create_refuses_a_range_without_a_commit(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo,
        database,
        "A statement.",
        "--about-repository",
        "--cite-range",
        "core/app.py:1-2",
    )

    assert result.exit_code == 1
    assert "has to cite the commit too" in result.stderr


def test_create_refuses_an_unknown_absence_kind(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo,
        database,
        "A statement.",
        "--about-repository",
        "--cite-absence",
        "vanished",
    )

    assert result.exit_code == 1
    assert "not one of the absence kinds" in result.stderr


# --- when the statement has held since ---------------------------------------


def test_create_records_a_start_commit(
    sample_repo: Path, database: Path
) -> None:
    """The author's claim about the project, resolved to an address.

    A prefix is enough, as it is for every sha in the tool, and what is stored
    is the full one — the same rule the subject and the citations obey.
    """
    first = git_output(sample_repo, "rev-list", "--max-parents=0", "HEAD").strip()
    _create(
        sample_repo,
        database,
        "Login lives here.",
        "--about-path",
        "core/app.py",
        "--since-commit",
        first[:8],
    )

    memory = _stored(database, sample_repo)[0]
    assert memory.since_commit_sha == first
    assert memory.since_date is None

    shown = _run(sample_repo, database, "show", memory.memory_id[:8])
    assert f"Since       {first[:12]} (2024-03-01)" in shown.stdout


def test_create_records_a_start_date(
    sample_repo: Path, database: Path
) -> None:
    _create(
        sample_repo,
        database,
        "Login lives here.",
        "--about-path",
        "core/app.py",
        "--since-date",
        "2024-03-01",
    )

    memory = _stored(database, sample_repo)[0]
    assert memory.since_date == "2024-03-01"
    assert memory.since_commit_sha is None

    shown = _run(sample_repo, database, "show", memory.memory_id[:8])
    assert "Since       2024-03-01" in shown.stdout


def test_create_refuses_two_starts(
    sample_repo: Path, database: Path
) -> None:
    """A memory has one start, and the sentence says which two things arrived."""
    first = git_output(sample_repo, "rev-list", "--max-parents=0", "HEAD").strip()

    result = _create(
        sample_repo,
        database,
        "Login lives here.",
        "--about-path",
        "core/app.py",
        "--since-commit",
        first[:8],
        "--since-date",
        "2024-03-01",
    )

    assert result.exit_code == 1
    assert "one thing: a commit or a date, not both" in result.stderr
    assert _stored(database, sample_repo) == ()


def test_create_refuses_a_start_commit_that_is_not_stored(
    sample_repo: Path, database: Path
) -> None:
    """The tool's existing refusal, because it is the same situation a subject is."""
    result = _create(
        sample_repo,
        database,
        "Login lives here.",
        "--about-path",
        "core/app.py",
        "--since-commit",
        "deadbeef",
    )

    assert result.exit_code == 1
    assert "no stored commit starts with 'deadbeef'" in result.stderr
    assert _stored(database, sample_repo) == ()


def test_create_refuses_a_start_date_that_is_not_a_day(
    sample_repo: Path, database: Path
) -> None:
    """Three spellings, and the two that are not days are refused for real.

    ``2024-3-6`` is the shape the schema would not keep, ``20240306`` is a date
    Python reads and the column's own ``GLOB`` would not, and ``2024-13-45`` is
    a day that does not exist — a check on the shape alone would keep the last
    one, and a start that never happened would then compare as if it had.
    """
    for spelling in ("2024-3-6", "20240306", "2024-13-45"):
        result = _create(
            sample_repo,
            database,
            "Login lives here.",
            "--about-path",
            "core/app.py",
            "--since-date",
            spelling,
        )
        assert result.exit_code == 1, spelling
        assert "is not a date; a since date is written YYYY-MM-DD" in result.stderr

    assert _stored(database, sample_repo) == ()


def test_supersede_takes_the_same_two_flags(
    sample_repo: Path, database: Path
) -> None:
    """Shared option objects, so the successor can state its own start."""
    _create(sample_repo, database, "The first statement.", "--about-path", "core/app.py")
    old = _stored(database, sample_repo)[0]

    replaced = _run(
        sample_repo,
        database,
        "supersede",
        old.memory_id[:8],
        "The second statement.",
        "--about-path",
        "core/app.py",
        "--since-date",
        "2024-03-06",
    )

    assert replaced.exit_code == 0, replaced.stderr
    successor = _stored(database, sample_repo)[0]
    assert successor.since_date == "2024-03-06"
    assert successor.supersedes == old.memory_id


def test_create_refuses_the_same_citation_twice(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo,
        database,
        "A statement.",
        "--about-repository",
        "--cite-file",
        "core/app.py",
        "--cite-file",
        "core/app.py",
    )

    assert result.exit_code == 1
    assert "given twice" in result.stderr


def test_create_refuses_a_cochange_pair_that_is_not_reported(
    sample_repo: Path, database: Path
) -> None:
    result = _create(
        sample_repo,
        database,
        "A statement.",
        "--about-repository",
        "--cite-cochange",
        "core/app.py -> nope.py",
    )

    assert result.exit_code == 1
    assert "is not in the co-change analysis" in result.stderr


def test_a_range_is_held_against_the_diff_of_the_commit_beside_it(
    tmp_path: Path,
) -> None:
    """The spans are known by hand: two lines added, then a third.

    The second commit's diff is ``@@ -2,0 +3 @@``, so its one span is 3-3 —
    written out here rather than read back out of the check being tested.
    """
    repository = tmp_path / "known"
    repository.mkdir()
    git_output(repository, "init", "--initial-branch", "main")
    _configure(repository)
    _write_file(repository, "auth.py", "one\ntwo\n")
    _commit(repository, INITIAL_DATE, "add auth")
    _write_file(repository, "auth.py", "one\ntwo\nthree\n")
    _commit(repository, LATER_DATE, "add a third line")

    database = tmp_path / "known.db"
    analyze(repository, database)
    sha = git_output(repository, "rev-parse", "HEAD").strip()

    accepted = _create(
        repository,
        database,
        "The third line is where this landed.",
        "--about-path",
        "auth.py",
        "--cite-commit",
        sha[:8],
        "--cite-range",
        "auth.py:3-3",
    )
    assert accepted.exit_code == 0, accepted.stderr

    refused = _create(
        repository,
        database,
        "A span of the first commit's diff.",
        "--about-path",
        "auth.py",
        "--cite-commit",
        sha[:8],
        "--cite-range",
        "auth.py:1-2",
    )
    assert refused.exit_code == 1
    assert "is not in the diff of" in refused.stderr


def test_create_accepts_a_cochange_pair_the_analysis_reports(
    tmp_path: Path,
) -> None:
    repository = build_cochange_repo(tmp_path / "cochange")
    database = tmp_path / "cochange.db"
    analyze(repository, database)

    result = _create(
        repository,
        database,
        "These two move together.",
        "--about-path",
        "a.py",
        "--cite-cochange",
        "a.py -> b.py",
    )

    assert result.exit_code == 0, result.stderr


# --- listing -----------------------------------------------------------------


def test_list_says_when_nothing_is_kept(
    sample_repo: Path, database: Path
) -> None:
    result = _run(sample_repo, database, "list")

    assert result.exit_code == 0
    assert "Memories (0)" in result.stdout
    assert "No memories are kept for this project." in result.stdout
    assert "the tool did not derive it and cannot check it" in result.stdout


def test_list_shows_the_newest_first_and_names_the_evidence(
    sample_repo: Path, database: Path
) -> None:
    _create(
        sample_repo,
        database,
        "The older one.",
        "--about-path",
        "core/app.py",
        "--cite-file",
        "core/app.py",
    )
    _create(sample_repo, database, "The newer one.", "--about-repository")

    result = _run(sample_repo, database, "list")

    assert result.exit_code == 0
    assert result.stdout.index("The newer one.") < result.stdout.index("The older one.")
    assert "1. the project" in result.stdout
    assert "2. core/app.py" in result.stdout
    assert "evidence: none attached" in result.stdout
    assert "evidence: file core/app.py" in result.stdout


def test_list_hides_what_has_ended_unless_it_is_asked_for(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "An ended memory.", "--about-repository")
    _end(sample_repo, database, "not true any more")

    kept = _run(sample_repo, database, "list")
    assert "Memories (0)" in kept.stdout

    ended = _run(sample_repo, database, "list", "--include-ended")
    assert "An ended memory." in ended.stdout
    assert "invalidated" in ended.stdout
    assert "not true any more" in ended.stdout


def test_list_limits_and_says_how_many_are_hidden(
    sample_repo: Path, database: Path
) -> None:
    for number in range(3):
        _create(sample_repo, database, f"Statement {number}.", "--about-repository")

    result = _run(sample_repo, database, "list", "--limit", "2")

    assert "Memories (2)" in result.stdout
    assert "1 more memory. Use --all to see them." in result.stdout

    every = _run(sample_repo, database, "list", "--all")
    assert "Memories (3)" in every.stdout
    assert "more memory" not in every.stdout


def test_list_json_carries_every_memory_whatever_the_limit(
    sample_repo: Path, database: Path
) -> None:
    for number in range(3):
        _create(sample_repo, database, f"Statement {number}.", "--about-repository")

    result = _run(sample_repo, database, "list", "--json", "--limit", "1")

    assert result.exit_code == 0, result.stderr
    document = json.loads(result.stdout)
    assert len(document["memories"]) == 3


def test_list_json_has_the_documented_shape(
    sample_repo: Path, database: Path
) -> None:
    _create(
        sample_repo,
        database,
        "A statement.",
        "--about-path",
        "core/app.py",
        "--cite-file",
        "core/app.py",
    )

    document = json.loads(_run(sample_repo, database, "list", "--json").stdout)

    assert set(document) == {"repository", "head_sha", "memories"}
    assert document["repository"] == str(sample_repo.resolve())
    memory = document["memories"][0]
    assert set(memory) == {
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
    assert memory["author"] == {"name": "Ada Lovelace", "email": "ada@example.com"}
    assert memory["subject"] == {
        "kind": "path",
        "path": "core/app.py",
        "resolution": "resolved",
    }
    assert memory["since"] is None
    assert memory["citations"] == [
        {"kind": "file", "ref": "core/app.py", "resolution": "resolved"}
    ]


def test_list_marks_a_citation_it_did_not_check(
    tmp_path: Path,
) -> None:
    """A list re-checks the cheap kinds and says so about the rest."""
    repository = build_cochange_repo(tmp_path / "cochange")
    database = tmp_path / "cochange.db"
    analyze(repository, database)
    _create(
        repository,
        database,
        "These two move together.",
        "--about-path",
        "a.py",
        "--cite-cochange",
        "a.py -> b.py",
    )

    result = _run(repository, database, "list")

    assert "cochange a.py -> b.py (not checked)" in result.stdout


def test_a_citation_a_rewrite_removed_is_reported_not_dropped(
    tmp_path: Path,
) -> None:
    """The memory keeps what the person wrote; the read says it cannot resolve it."""
    repository = build_single_commit_repo(tmp_path / "rewritten")
    add_commit(repository, "a second commit")
    database = tmp_path / "rewritten.db"
    analyze(repository, database)
    sha = git_output(repository, "rev-parse", "HEAD").strip()

    created = _create(
        repository,
        database,
        "Made from that commit.",
        "--about-repository",
        "--cite-commit",
        sha[:8],
    )
    assert created.exit_code == 0, created.stderr

    git_output(repository, "reset", "--hard", "HEAD~1")
    analyze(repository, database)

    listed = _run(repository, database, "list")
    assert f"commit {sha[:8]} (no longer in this history)" in listed.stdout

    memory_id = json.loads(_run(repository, database, "list", "--json").stdout)[
        "memories"
    ][0]["memory_id"]
    detail = _run(repository, database, "show", memory_id)
    assert "no longer in this history" in detail.stdout
    # The citation is still the one the person wrote: nothing was repaired.
    assert _stored(database, repository)[0].citations[0].ref == sha[:8]


def test_list_reports_memories_made_about_another_repository(
    sample_repo: Path, tmp_path: Path
) -> None:
    """The takeover `analyze --db` can do, and what the read says about it.

    This is the one way the tool itself can leave a database holding two
    repositories' knowledge: a second repository analyzes into the same file.
    Nothing is hidden and nothing is shown as the current repository's.
    """
    other = build_single_commit_repo(tmp_path / "other")
    database = tmp_path / "shared.db"
    analyze(sample_repo, database)
    _create(sample_repo, database, "About the sample repository.", "--about-repository")

    analyze(other, database)

    result = _run(other, database, "list")

    assert result.exit_code == 0, result.stderr
    assert "Memories (0)" in result.stdout
    assert "also holds memories made about" in result.stderr
    assert "(1 memory)" in result.stderr
    assert str(sample_repo.resolve()) in result.stderr


# --- reading one back --------------------------------------------------------


def test_show_prints_the_memory_in_full(
    sample_repo: Path, database: Path
) -> None:
    sha = git_output(sample_repo, "rev-parse", "HEAD").strip()
    _create(
        sample_repo,
        database,
        "We keep authentication in one module.",
        "--about-path",
        "core/app.py",
        "--cite-commit",
        sha[:8],
        "--cite-file",
        "core/app.py",
    )
    memory = _stored(database, sample_repo)[0]

    result = _run(sample_repo, database, "show", memory.memory_id[:8])

    assert result.exit_code == 0, result.stderr
    assert f"Memory      {memory.memory_id}" in result.stdout
    assert f"Repository  {sample_repo.resolve()}" in result.stdout
    assert "Subject     core/app.py" in result.stdout
    assert "Author      Ada Lovelace <ada@example.com>" in result.stdout
    assert "State       active" in result.stdout
    assert "Since       unknown" in result.stdout
    assert "We keep authentication in one module." in result.stdout
    assert "Evidence (2)" in result.stdout
    assert "commit" in result.stdout and "resolved" in result.stdout
    assert "Lifecycle   nothing supersedes it" in result.stdout
    assert "the tool did not derive it and cannot check it" in result.stdout


def test_show_says_when_nothing_was_attached(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "A rule.", "--about-repository")
    memory = _stored(database, sample_repo)[0]

    result = _run(sample_repo, database, "show", memory.memory_id)

    assert "Evidence    none attached" in result.stdout


def test_show_names_the_commit_a_memory_started_at(
    tmp_path: Path,
) -> None:
    repository = build_single_commit_repo(tmp_path / "since")
    database = tmp_path / "since.db"
    analyze(repository, database)
    connection = connect(database)
    try:
        written = admit(
            connection,
            repository_path=repository,
            statement="This has been the rule since the first commit.",
            subject=MemorySubject("repository"),
            since_commit_sha=git_output(repository, "rev-parse", "HEAD").strip(),
        )
    finally:
        connection.close()

    result = _run(repository, database, "show", written.memory_id)

    assert "Since       " in result.stdout
    assert "2024-03-01" in result.stdout


def test_show_refuses_an_id_that_names_nothing(
    sample_repo: Path, database: Path
) -> None:
    result = _run(sample_repo, database, "show", "abcdef12")

    assert result.exit_code == 1
    assert "no memory starts with 'abcdef12'" in result.stderr


def test_show_refuses_a_prefix_that_is_not_an_id(
    sample_repo: Path, database: Path
) -> None:
    result = _run(sample_repo, database, "show", "not-an-id")

    assert result.exit_code == 1
    assert "is not a memory id" in result.stderr


def test_show_json_is_the_object_alone(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "A statement.", "--about-repository")
    memory = _stored(database, sample_repo)[0]

    document = json.loads(_run(sample_repo, database, "show", memory.memory_id, "--json").stdout)

    assert document["memory_id"] == memory.memory_id
    assert document["statement"] == "A statement."
    assert document["subject"] == {"kind": "repository", "resolution": "resolved"}
    assert document["citations"] == []


def test_show_names_the_memory_that_replaced_it(
    sample_repo: Path, database: Path
) -> None:
    _create(sample_repo, database, "The first statement.", "--about-repository")
    connection = connect(database)
    try:
        old = read_memories(connection, sample_repo)[0]
        successor = supersede(
            connection,
            old.memory_id,
            repository_path=sample_repo,
            statement="The second statement.",
            subject=MemorySubject("repository"),
        )
    finally:
        connection.close()

    result = _run(sample_repo, database, "show", old.memory_id)

    assert result.exit_code == 0, result.stderr
    assert f"superseded by {successor.memory_id[:8]}" in result.stdout
    assert "State       superseded" in result.stdout


# --- the group itself --------------------------------------------------------


def test_the_group_lists_the_acts_it_has() -> None:
    # A width, because rich wraps the group's own sentence to the terminal and
    # the phrase this asserts would otherwise be split across two lines.
    result = runner.invoke(app, ["memory", "--help"], env={"COLUMNS": "110"})

    assert result.exit_code == 0
    for act in ("create", "list", "show"):
        assert act in result.stdout
    assert "never treated as evidence" in result.stdout


def test_memory_commands_are_core_first(sample_repo: Path, database: Path) -> None:
    """No model, no key, no endpoint — and the whole loop still works."""
    created = _create(sample_repo, database, "A statement.", "--about-repository")
    listed = _run(sample_repo, database, "list")
    shown = _run(sample_repo, database, "show", _stored(database, sample_repo)[0].memory_id)

    assert [result.exit_code for result in (created, listed, shown)] == [0, 0, 0]


def _end(repository: Path, database: Path, reason: str) -> None:
    """End the one memory a test wrote, through the module's own act."""
    connection = connect(database)
    try:
        found = read_memories(connection, repository)
        assert len(found) == 1, found
        invalidate(
            connection, found[0].memory_id, repository_path=repository, reason=reason
        )
    finally:
        connection.close()
