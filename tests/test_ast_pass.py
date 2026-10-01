"""Tests for the pass that turns the stored history into definitions.

The fixtures are real repositories and the rows are read back with plain SQL: the
questions here are what the history contained and what the pass made of it, and a
reader of ours would be answering its own question.
"""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology import ast_pass as ast_pass_module
from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import (
    ANALYZER_VERSION,
    _already_stored,
    producer_version,
    run_ast_pass,
)
from codearchaeology.cli import app
from codearchaeology.definitions import interpreter_version
from codearchaeology.storage import (
    CREATED,
    MODIFIED,
    UNCHANGED,
    FileVersion,
    connect,
)
from sample_repo import (
    LATER_DATE,
    build_definition_repo,
    build_single_commit_repo,
    git_output,
)

PYTHON_FILE = "app.py"

TWO_SAME_NAMES = '''\
def f():
    return 1


def g():
    return 2


def f():
    return 3
'''

TWO_SAME_NAMES_FIRST_CHANGED = '''\
def f():
    return 10


def g():
    return 2


def f():
    return 3
'''

ONLY_COMMENTS = '''\
# Nothing here is a definition.
# Not even this.
'''


def _shas(repository: Path) -> list[str]:
    """Every commit git has, oldest first."""
    return git_output(repository, "rev-list", "--reverse", "HEAD").split()


def _changes(connection: sqlite3.Connection, path: str, commit_sha: str) -> dict:
    """qualname -> change_type for one stored file version, in file order."""
    return {
        row["qualname"]: row["change_type"]
        for row in connection.execute(
            "SELECT qualname, change_type FROM definition_versions"
            " WHERE path = ? AND commit_sha = ? ORDER BY position",
            (path, commit_sha),
        )
    }


def _version(connection: sqlite3.Connection, path: str, commit_sha: str):
    return connection.execute(
        "SELECT * FROM file_versions WHERE path = ? AND commit_sha = ?",
        (path, commit_sha),
    ).fetchone()


def _rows(connection: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT * FROM definition_versions ORDER BY commit_sha, path, position"
        )
    ]


def _commit_python(repository: Path, source: str, message: str) -> None:
    """Replace the fixture's Python file and commit it."""
    (repository / PYTHON_FILE).write_text(source, encoding="utf-8", newline="\n")
    git_output(repository, "add", "--all")
    git_output(repository, "commit", "--message", message, timestamp=LATER_DATE)


@pytest.fixture
def passed(tmp_path: Path):
    """The definition fixture, analyzed and passed, with the pass's own result."""
    repository = build_definition_repo(tmp_path / "definitions")
    database = tmp_path / "definitions.db"
    analyze(repository, database)
    return repository, database, run_ast_pass(repository, database)


def test_every_python_version_in_the_history_is_stored(passed) -> None:
    _, database, result = passed
    assert result.file_versions == 6
    assert result.definitions == 13
    assert result.failed == 1
    assert (result.skipped, result.reused) == (0, 0)


def test_the_first_version_has_every_definition_created(passed) -> None:
    repository, database, _ = passed
    connection = connect(database)
    try:
        assert _changes(connection, PYTHON_FILE, _shas(repository)[0]) == {
            "login": CREATED,
            "helper": CREATED,
        }
    finally:
        connection.close()


def test_a_rewritten_function_is_modified_and_the_others_are_not(passed) -> None:
    repository, database, _ = passed
    connection = connect(database)
    try:
        assert _changes(connection, PYTHON_FILE, _shas(repository)[1]) == {
            "login": MODIFIED,
            "helper": UNCHANGED,
        }
    finally:
        connection.close()


def test_an_added_function_is_created(passed) -> None:
    repository, database, _ = passed
    connection = connect(database)
    try:
        assert _changes(connection, PYTHON_FILE, _shas(repository)[2]) == {
            "login": UNCHANGED,
            "helper": UNCHANGED,
            "logout": CREATED,
        }
    finally:
        connection.close()


def test_a_renamed_function_is_one_gone_and_one_created(passed) -> None:
    """No identity is invented for it: the pass says what it can see."""
    repository, database, _ = passed
    connection = connect(database)
    try:
        changes = _changes(connection, PYTHON_FILE, _shas(repository)[3])
        assert changes == {
            "authenticate": CREATED,
            "helper": UNCHANGED,
            "logout": UNCHANGED,
        }
        assert "login" not in changes
    finally:
        connection.close()


def test_a_version_that_could_not_be_parsed_is_recorded_without_definitions(
    passed,
) -> None:
    repository, database, _ = passed
    connection = connect(database)
    try:
        row = _version(connection, PYTHON_FILE, _shas(repository)[4])
        assert row["parse_error"].startswith("SyntaxError: ")
        assert row["error_lineno"] is not None
        assert _changes(connection, PYTHON_FILE, _shas(repository)[4]) == {}
    finally:
        connection.close()


def test_the_next_version_is_compared_across_a_failure(passed) -> None:
    """The failure is not a set of deletions, and it is not a fresh start either."""
    repository, database, _ = passed
    connection = connect(database)
    try:
        changes = _changes(connection, PYTHON_FILE, _shas(repository)[5])
        assert changes == {
            "authenticate": UNCHANGED,
            "helper": UNCHANGED,
            "logout": UNCHANGED,
        }
        assert CREATED not in changes.values()
    finally:
        connection.close()


def test_running_the_pass_twice_parses_nothing_the_second_time(passed) -> None:
    repository, database, first = passed
    connection = connect(database)
    try:
        before = _rows(connection)
    finally:
        connection.close()

    again = run_ast_pass(repository, database)

    assert (again.parsed, again.reused) == (0, first.file_versions)
    assert again.file_versions == first.file_versions
    assert again.definitions == first.definitions

    connection = connect(database)
    try:
        assert _rows(connection) == before
    finally:
        connection.close()


def test_a_version_whose_content_changed_is_parsed_again(passed) -> None:
    repository, database, first = passed
    _commit_python(repository, TWO_SAME_NAMES, "a different file")
    analyze(repository, database)

    again = run_ast_pass(repository, database)

    assert again.parsed == 1, "the new version is the only one that is new"
    assert again.reused == first.file_versions


def _batches_written(monkeypatch) -> list[list]:
    """Every batch the pass hands to the writer, so a run that wrote nothing shows.

    A count of the calls is the only way to see the difference from outside: a
    pass that rewrote identical rows would leave exactly the same rows behind.
    """
    batches: list[list] = []
    original = ast_pass_module.write_ast_batch

    def counted(connection, rows):
        batches.append(list(rows))
        return original(connection, rows)

    monkeypatch.setattr(ast_pass_module, "write_ast_batch", counted)
    return batches


def test_a_second_pass_writes_nothing_at_all(passed, monkeypatch) -> None:
    """Reuse saves the write as well as the parse.

    Every version is the one already stored, so every row the pass would write is
    a row that is already there — and deleting and re-inserting them is work with
    no effect. Measured before this existed: 73 s of a 112 s second pass at
    50,000 commits, spent to store what was already stored.
    """
    repository, database, first = passed
    batches = _batches_written(monkeypatch)

    again = run_ast_pass(repository, database)

    assert again.reused == first.file_versions
    assert (again.parsed, again.failed) == (0, 0)
    assert batches == []


def test_only_the_versions_that_are_new_are_written(passed, monkeypatch) -> None:
    """The partial case: a history that grew by one version writes one version."""
    repository, database, first = passed
    _commit_python(repository, TWO_SAME_NAMES, "a different file")
    analyze(repository, database)
    batches = _batches_written(monkeypatch)

    again = run_ast_pass(repository, database)

    assert (again.parsed, again.reused) == (1, first.file_versions)
    assert [len(batch) for batch in batches] == [1]


def test_a_stored_row_the_rules_would_not_write_is_written_again(
    passed, monkeypatch
) -> None:
    """Skipping the write must not be able to freeze a wrong comparison.

    ``change_type`` is worked out against the version before this one, so a
    history that moved under a stored row — a rename that no longer follows the
    same chain, a threshold that changed — moves the comparison without moving
    this version's own bytes. The skip is therefore a comparison and not an
    assumption: the version is reused, and it is still written, because what
    would be written is not what is stored.
    """
    repository, database, first = passed
    first_commit = _shas(repository)[0]
    connection = connect(database)
    try:
        with connection:
            connection.execute(
                "UPDATE definition_versions SET change_type = ?"
                " WHERE path = ? AND commit_sha = ?",
                (UNCHANGED, PYTHON_FILE, first_commit),
            )
    finally:
        connection.close()
    batches = _batches_written(monkeypatch)

    again = run_ast_pass(repository, database)

    assert again.reused == first.file_versions, "the version is still reused"
    assert [len(batch) for batch in batches] == [1], "and it is written back"
    connection = connect(database)
    try:
        assert _changes(connection, PYTHON_FILE, first_commit) == {
            "login": CREATED,
            "helper": CREATED,
        }
    finally:
        connection.close()


def test_the_skip_compares_the_version_row_as_well_as_the_definitions() -> None:
    """The helper's whole rule, including the half the pass cannot produce.

    A reused version always has the row that is already stored, so the check on
    the row itself is unreachable through the pass — and it is what makes the
    helper's question ("is the database already holding what would be written?")
    answerable without knowing why it was asked.
    """
    stored = FileVersion(
        commit_sha="a" * 40,
        path=PYTHON_FILE,
        content_sha="b" * 40,
        parsed_at_version=producer_version(),
    )
    assert _already_stored(stored, stored, None, ())
    assert _already_stored(stored, stored, (), ())
    assert not _already_stored(stored, replace(stored, content_sha="c" * 40), (), ())
    assert not _already_stored(None, stored, (), ())


def test_a_file_with_no_definitions_still_has_a_version(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "empty")
    _commit_python(repository, ONLY_COMMENTS, "no definitions here")
    database = tmp_path / "empty.db"
    analyze(repository, database)

    run_ast_pass(repository, database)

    connection = connect(database)
    try:
        row = _version(connection, PYTHON_FILE, _shas(repository)[1])
        assert row["parse_error"] is None
        assert _changes(connection, PYTHON_FILE, _shas(repository)[1]) == {}
    finally:
        connection.close()


def test_two_definitions_with_the_same_name_are_compared_in_order(
    tmp_path: Path,
) -> None:
    """The n-th definition with an identity is compared with the n-th one before it."""
    repository = build_single_commit_repo(tmp_path / "same-names")
    _commit_python(repository, TWO_SAME_NAMES, "two definitions named f")
    _commit_python(repository, TWO_SAME_NAMES_FIRST_CHANGED, "the first one changes")
    database = tmp_path / "same-names.db"
    analyze(repository, database)

    run_ast_pass(repository, database)

    connection = connect(database)
    try:
        rows = connection.execute(
            "SELECT qualname, change_type, lineno FROM definition_versions"
            " WHERE path = ? AND commit_sha = ? ORDER BY position",
            (PYTHON_FILE, _shas(repository)[2]),
        ).fetchall()
        assert [(row["qualname"], row["change_type"]) for row in rows] == [
            ("f", MODIFIED),
            ("g", UNCHANGED),
            ("f", UNCHANGED),
        ]
    finally:
        connection.close()


def test_only_python_files_are_visited(sample_repo: Path, tmp_path: Path) -> None:
    database = tmp_path / "sample.db"
    analyze(sample_repo, database)

    run_ast_pass(sample_repo, database)

    connection = connect(database)
    try:
        paths = {
            row["path"] for row in connection.execute("SELECT DISTINCT path FROM file_versions")
        }
        assert paths == {
            "app.py",
            "core/app.py",
            "core/cache.py",
            "legacy.py",
            "工具/文本.py",
        }
    finally:
        connection.close()


def test_a_pure_rename_keeps_its_definitions_unchanged(
    sample_repo: Path, tmp_path: Path
) -> None:
    """The rename is the same file continuing, so nothing in it was created."""
    database = tmp_path / "rename.db"
    analyze(sample_repo, database)

    run_ast_pass(sample_repo, database)

    connection = connect(database)
    try:
        after_rename = _shas(sample_repo)[1]
        assert _changes(connection, "core/app.py", after_rename) == {
            "login": UNCHANGED,
            "main": UNCHANGED,
        }
    finally:
        connection.close()


def test_a_function_changed_after_a_rename_is_modified(
    sample_repo: Path, tmp_path: Path
) -> None:
    database = tmp_path / "rename-then-edit.db"
    analyze(sample_repo, database)

    run_ast_pass(sample_repo, database)

    connection = connect(database)
    try:
        fixed = _shas(sample_repo)[3]
        assert _changes(connection, "core/app.py", fixed) == {
            "login": MODIFIED,
            "logout": CREATED,
            "main": UNCHANGED,
        }
    finally:
        connection.close()


def test_a_deleted_file_gets_no_deletion_row(sample_repo: Path, tmp_path: Path) -> None:
    database = tmp_path / "deleted.db"
    analyze(sample_repo, database)

    run_ast_pass(sample_repo, database)

    connection = connect(database)
    try:
        rows = connection.execute(
            "SELECT * FROM definition_versions WHERE path = 'legacy.py'"
        ).fetchall()
        assert [row["change_type"] for row in rows] == [CREATED]
        assert "deleted" not in {
            row["change_type"]
            for row in connection.execute("SELECT change_type FROM definition_versions")
        }
    finally:
        connection.close()


def test_an_analysis_keeps_what_the_pass_stored(passed) -> None:
    repository, database, first = passed

    analyze(repository, database)

    connection = connect(database)
    try:
        assert len(_rows(connection)) == first.definitions
        assert (
            connection.execute("SELECT COUNT(*) FROM file_versions").fetchone()[0]
            == first.file_versions
        )
    finally:
        connection.close()


def test_the_ast_command_fills_the_database(tmp_path: Path) -> None:
    repository = build_definition_repo(tmp_path / "cli")
    database = tmp_path / "cli.db"
    analyze(repository, database)

    result = CliRunner().invoke(app, ["ast", str(repository), "--db", str(database)])

    assert result.exit_code == 0, result.stderr
    assert "Versions" in result.stdout
    assert "could not be parsed" in result.stdout

    connection = connect(database)
    try:
        assert _rows(connection)
    finally:
        connection.close()


def test_the_ast_command_says_to_analyze_first(tmp_path: Path) -> None:
    repository = build_definition_repo(tmp_path / "cli-missing")
    database = tmp_path / "nothing.db"

    result = CliRunner().invoke(app, ["ast", str(repository), "--db", str(database)])

    assert result.exit_code == 1
    assert "analyze" in result.stderr


# --- hardening: the two commands, over and over, against real git -----------


def _stored(connection: sqlite3.Connection) -> tuple[int, list[tuple]]:
    return (
        connection.execute("SELECT COUNT(*) AS n FROM file_versions").fetchone()["n"],
        _rows(connection),
    )


def test_the_whole_cycle_can_be_run_again_without_losing_anything(passed) -> None:
    """The user's §8.6: analyze twice, with the AST pass in between."""
    repository, database, _ = passed
    connection = connect(database)
    try:
        before = _stored(connection)
    finally:
        connection.close()

    analyze(repository, database)
    again = run_ast_pass(repository, database)

    # Nothing to parse: every version is the one that was already worked out.
    assert (again.parsed, again.reused) == (0, 6)

    connection = connect(database)
    try:
        assert _stored(connection) == before
    finally:
        connection.close()


def test_a_rewrite_keeps_the_ast_of_the_commits_that_stayed(tmp_path: Path) -> None:
    """An amended history must not throw away the parsing of the commits it kept."""
    repository = build_definition_repo(tmp_path / "rewritten")
    database = tmp_path / "rewritten.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    kept = _shas(repository)[:5]
    rewritten = _shas(repository)[5]

    git_output(
        repository,
        "commit",
        "--amend",
        "--message",
        "definition change 6, amended",
        timestamp=LATER_DATE,
    )
    analyze(repository, database)

    connection = connect(database)
    try:
        # The five commits git still has kept their versions and definitions;
        # the one that was rewritten has neither, until the pass runs again.
        assert (
            connection.execute("SELECT COUNT(*) AS n FROM file_versions")
            .fetchone()["n"]
            == 5
        )
        assert len(_rows(connection)) == 10
        assert connection.execute(
            "SELECT 1 FROM file_versions WHERE commit_sha = ?", (kept[0],)
        ).fetchone()
        assert not connection.execute(
            "SELECT 1 FROM file_versions WHERE commit_sha = ?", (rewritten,)
        ).fetchone()
    finally:
        connection.close()

    again = run_ast_pass(repository, database)

    assert again.file_versions == 6
    connection = connect(database)
    try:
        assert len(_rows(connection)) == 13
        assert connection.execute(
            "SELECT 1 FROM file_versions WHERE commit_sha = ?", (_shas(repository)[5],)
        ).fetchone()
    finally:
        connection.close()


def test_a_row_read_by_another_interpreter_is_read_again(passed) -> None:
    """The user's §8.5: one interpreter's rows are not another's evidence."""
    repository, database, _ = passed
    connection = connect(database)
    try:
        with connection:
            connection.execute("UPDATE file_versions SET parsed_at_version = '3.10.0'")
    finally:
        connection.close()

    again = run_ast_pass(repository, database)

    # One version of the fixture does not parse, so five are read again and
    # the sixth fails again — nothing is reused from the other interpreter.
    assert (again.reused, again.parsed, again.failed) == (0, 5, 1)

    connection = connect(database)
    try:
        stored = {
            row["parsed_at_version"]
            for row in connection.execute("SELECT parsed_at_version FROM file_versions")
        }
        assert stored == {producer_version()}
    finally:
        connection.close()


def test_a_row_written_by_another_analyzer_is_read_again(passed) -> None:
    """Same bytes, same interpreter, different analyzer: not the same evidence.

    The stored fingerprints and `change_type` values are the analyzer's answer
    as much as the parser's, so a row produced by an older analyzer must not be
    reused under a rule that has since changed. The marker is the second half of
    `parsed_at_version`, and this is the test that says so.
    """
    repository, database, _ = passed
    connection = connect(database)
    try:
        with connection:
            # The interpreter stays; only the analyzer's half of the version
            # moves, which is exactly the case a bare interpreter string would
            # have let through.
            connection.execute(
                "UPDATE file_versions SET parsed_at_version = ?",
                (f"{interpreter_version()}+0",),
            )
    finally:
        connection.close()

    again = run_ast_pass(repository, database)

    assert (again.reused, again.parsed, again.failed) == (0, 5, 1)

    connection = connect(database)
    try:
        stored = {
            row["parsed_at_version"]
            for row in connection.execute("SELECT parsed_at_version FROM file_versions")
        }
        assert stored == {producer_version()}
    finally:
        connection.close()


def test_the_producer_version_names_both_halves() -> None:
    """``3.13.5+1``: what parsed it and what analyzed it."""
    assert producer_version() == f"{interpreter_version()}+{ANALYZER_VERSION}"
    assert ANALYZER_VERSION.isdigit()
