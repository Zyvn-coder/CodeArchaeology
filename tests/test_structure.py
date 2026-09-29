"""Tests for the structure view: the lines, and the command that prints them.

The fixtures are the same real repositories the derivation is tested on. What is
under test here is what a reader sees: which events are listed, what the header
counts, and what the lines say when the history does not know something.
"""

from pathlib import Path

import json

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.definition_history import load_histories
from codearchaeology.storage import connect
from codearchaeology.structure import build_history, nothing_read_note
from sample_repo import (
    LATER_DATE,
    build_definition_repo,
    build_gap_repo,
    build_interleaved_repo,
    build_lifecycle_repo,
    build_sample_repo,
    build_single_commit_repo,
    git_output,
)

PYTHON_FILE = "app.py"

# A file that has never parsed, so every version of it is a failure and the
# command has nothing to show.
BROKEN = '''\
def login(user, password)
    return user == "admin"
'''

ONLY_COMMENTS = '''\
# Nothing here is a definition.
# Not even this.
'''

# A class with methods, a decorated function, and a function inside a function:
# the shapes the listing has to tell apart.
DECORATED = '''\
"""Decorated things."""


class Service:
    @property
    def name(self):
        return "service"

    @staticmethod
    async def fetch(url):
        return url


@app.route("/x")
def handler():
    def helper():
        return 1

    return helper
'''

# One file, two definitions sharing a name, and a third one between them: the
# only shape in which the heading has to say which definition of that name it is.
TWINS = '''\
def f():
    return 1


def g():
    return 2


def f():
    return 3
'''

TWINS_SECOND_GONE = '''\
def f():
    return 10


def g():
    return 2
'''


def _shas(repository: Path) -> list[str]:
    """Every commit git has, oldest first."""
    return git_output(repository, "rev-list", "--reverse", "HEAD").split()


def _commit_python(repository: Path, source: str, message: str) -> None:
    """Replace the fixture's Python file and commit it."""
    (repository / PYTHON_FILE).write_text(source, encoding="utf-8", newline="\n")
    git_output(repository, "add", "--all")
    git_output(repository, "commit", "--message", message, timestamp=LATER_DATE)


def _scanned(tmp_path: Path, builder, name: str):
    """Build one fixture, analyze and AST-scan it, and return it with its database."""
    repository = builder(tmp_path / name)
    database = tmp_path / f"{name}.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    return repository, database


def _block(repository: Path, database: Path, path: str = PYTHON_FILE) -> str:
    """The lines the command prints for one path, when it carries one history."""
    histories = load_histories(repository, database, path)
    assert len(histories) == 1, f"{path} carries {len(histories)} file lives"
    return build_history(histories[0])


def _run(repository: Path, database: Path, *arguments: str):
    """Invoke the command the way a user would, with the fixture's database."""
    return CliRunner().invoke(
        app,
        ["structure", *arguments, str(repository), "--db", str(database)],
    )


def _json(repository: Path, database: Path, *arguments: str) -> dict:
    """Invoke the command with --json and hand back what it printed."""
    result = _run(repository, database, *arguments, "--json")
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def definitions(tmp_path: Path):
    """The definition fixture: six commits, one file, every kind of change."""
    return _scanned(tmp_path, build_definition_repo, "definitions")


@pytest.fixture
def gap(tmp_path: Path):
    """The fixture whose files go dark: one gap, three endings."""
    return _scanned(tmp_path, build_gap_repo, "gap")


def test_the_block_lists_the_changes_and_never_lists_unchanged(definitions) -> None:
    repository, database = definitions
    block = _block(repository, database)

    assert "login  function" in block
    assert "  created  2024-05-01 09:00:00 +0000  92490d11  line 4" in block
    assert "  modified 2024-05-02 09:00:00 +0000  becad94f  line 4" in block
    assert "  deleted  2024-05-04 09:00:00 +0000  00455bfc" in block
    # The fixture holds helper in every version, and only its creation is a line:
    # `unchanged` is a fact the snapshots hold, not something that happened.
    assert block.count("helper  function") == 1
    assert "helper  function\n  created  2024-05-01 09:00:00 +0000  92490d11  line 8\n\n" in block
    assert "unchanged" not in block


def test_the_header_counts_the_versions_and_names_the_one_it_could_not_read(
    definitions,
) -> None:
    repository, database = definitions
    block = _block(repository, database)

    assert "Versions:    5 read, 1 could not be read" in block
    assert "  4cd03f6d  SyntaxError: expected ':'" in block


def test_a_rename_is_shown_once_in_the_header(tmp_path: Path) -> None:
    repository, database = _scanned(tmp_path, build_sample_repo, "sample")
    block = _block(repository, database, "core/app.py")

    assert block.startswith("core/app.py\nHistory:     app.py -> core/app.py\n")
    # The definitions are carried across the rename, so nothing is created or
    # deleted at it — and the events do not repeat the name they happened under.
    assert "created  2024-03-01 09:00:00 +0000  392cc0db  line 4" in block
    assert "modified 2024-03-06 09:00:00 +0000  8eaff71d  line 4" in block
    assert "deleted" not in block


def test_a_deletion_across_a_gap_says_that_when_it_went_is_not_known(gap) -> None:
    """The user's §5.7: not "deleted at B", and not a certain deletion at C."""
    repository, database = gap
    block = _block(repository, database, "vanished.py")

    assert "  deleted  2024-05-03 09:00:00 +0000  fddd0720" in block
    assert (
        "      at some point after 296267d7; 2a5a6fec could not be read,"
        " so when it went is not known" in block
    )
    # helper was there before and after, and the gap is not a line for it.
    assert "helper  function\n  created  2024-05-01 09:00:00 +0000  296267d7  line 8" in block
    assert block.endswith("line 8")


def test_a_definition_whose_file_never_parsed_again_has_no_ending(gap) -> None:
    repository, database = gap
    block = _block(repository, database, "dark.py")

    assert (
        "      no ending: 2a5a6fec could not be read, so whether it is still"
        " there is not known" in block
    )
    # Nothing was seen to end it, so no deletion is printed for it.
    assert "deleted" not in block


def test_a_creation_whose_earlier_versions_were_never_read_may_be_older(
    definitions,
) -> None:
    """A leading gap: "created" is the first version that showed it, no more."""
    repository, database = definitions
    shas = _shas(repository)
    connection = connect(database)
    try:
        with connection:
            connection.execute(
                "DELETE FROM file_versions WHERE commit_sha = ? AND path = ?",
                (shas[0], PYTHON_FILE),
            )
    finally:
        connection.close()

    block = _block(repository, database)

    assert (
        f"      first seen here; {shas[0][:8]} could not be read, so it may be older"
        in block
    )
    assert "at some point after None" not in block


def test_a_file_absent_from_one_commit_says_the_file_was_not_there(
    tmp_path: Path,
) -> None:
    repository, database = _scanned(tmp_path, build_interleaved_repo, "interleaved")
    block = _block(repository, database)

    assert "  029d5715  the file was not in this commit" in block
    assert (
        "      at some point after 40f3e0f4; 029d5715 could not be read,"
        " so when it changed is not known" in block
    )


def test_two_definitions_with_one_name_are_told_apart(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "twins")
    _commit_python(repository, TWINS, "two definitions named f")
    _commit_python(repository, TWINS_SECOND_GONE, "the second one goes")
    database = tmp_path / "twins.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    block = _block(repository, database)

    assert "f  function\n" in block
    assert "f  function  (2nd of that name)\n" in block
    # The second one is the one that went, and it went at the version that was
    # read without it.
    assert "f  function  (2nd of that name)\n  created" in block
    assert block.count("  deleted  ") == 1


def test_a_file_with_no_definitions_says_there_are_none(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "comments")
    _commit_python(repository, ONLY_COMMENTS, "nothing to find")
    database = tmp_path / "comments.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    block = _block(repository, database)

    assert "Versions:    1 read, 0 could not be read" in block
    assert block.endswith("No definitions in the versions that were read.")


def test_a_name_used_twice_prints_both_histories(tmp_path: Path) -> None:
    repository, database = _scanned(tmp_path, build_lifecycle_repo, "lifecycle")

    result = _run(repository, database, "app.py", "--history")

    assert result.exit_code == 0, result.stderr
    assert "src/core/app.py\nHistory:     app.py -> src/app.py -> src/core/app.py" in result.stdout
    assert "\napp.py\n" in result.stdout
    assert "main  function" in result.stdout


def test_the_command_prints_the_history(definitions) -> None:
    repository, database = definitions

    result = _run(repository, database, PYTHON_FILE, "--history")

    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("app.py\nVersions:    5 read, 1 could not be read")
    assert "authenticate  function" in result.stdout


def test_the_command_prints_the_structure(definitions) -> None:
    """The default version is the file's latest one."""
    repository, database = definitions

    result = _run(repository, database, PYTHON_FILE)

    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("app.py\nVersion:     2024-05-06 09:00:00 +0000")
    assert "Definitions: 3 (3 functions)" in result.stdout
    assert "KIND" in result.stdout and "DEFINITION" in result.stdout
    assert "authenticate" in result.stdout and "unchanged" in result.stdout


def test_the_command_refuses_a_file_that_is_not_python(tmp_path: Path) -> None:
    repository, database = _scanned(tmp_path, build_sample_repo, "sample")

    result = _run(repository, database, "README.md", "--history")

    assert result.exit_code == 1
    assert "not a Python file" in result.stderr
    assert not result.stdout


def test_the_command_refuses_a_path_the_history_never_carried(definitions) -> None:
    repository, database = definitions

    result = _run(repository, database, "nowhere.py", "--history")

    assert result.exit_code == 1
    assert "nothing in the stored history touched nowhere.py" in result.stderr


def test_the_command_says_to_run_ast_first(tmp_path: Path) -> None:
    repository = build_definition_repo(tmp_path / "unscanned")
    database = tmp_path / "unscanned.db"
    analyze(repository, database)

    result = _run(repository, database, PYTHON_FILE, "--history")

    assert result.exit_code == 1
    assert "run 'archaeology ast' first" in result.stderr


def test_the_command_names_the_first_failure_when_no_version_could_be_read(
    tmp_path: Path,
) -> None:
    repository = build_single_commit_repo(tmp_path / "broken")
    _commit_python(repository, BROKEN, "a file that never parses")
    database = tmp_path / "broken.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    result = _run(repository, database, PYTHON_FILE, "--history")

    assert result.exit_code == 1
    assert "no version of app.py could be read" in result.stderr
    assert "the first failure was" in result.stderr
    assert "SyntaxError" in result.stderr


def test_nothing_read_note_is_none_when_something_was_read(definitions) -> None:
    repository, database = definitions
    histories = load_histories(repository, database, PYTHON_FILE)
    assert nothing_read_note(histories, PYTHON_FILE) is None


# --- the structure of one version -------------------------------------------


def test_the_listing_shows_the_kind_the_lines_and_the_decorators(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "decorated")
    _commit_python(repository, DECORATED, "decorated definitions")
    database = tmp_path / "decorated.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    result = _run(repository, database, PYTHON_FILE)

    assert result.exit_code == 0, result.stderr
    assert "Definitions: 5 (1 class, 3 functions, 1 async function, 2 of them methods)" in result.stdout
    assert "class" in result.stdout and "async function" in result.stdout
    assert "@property" in result.stdout and "@app.route" in result.stdout
    # A function inside a function is not a method, and its name says so.
    assert "handler.<locals>" in result.stdout  # shortened to fit the column


def test_a_version_that_could_not_be_parsed_says_so_instead_of_listing_nothing(
    definitions,
) -> None:
    """The user's §7.2: zero definitions would read as a version that held none."""
    repository, database = definitions
    shas = _shas(repository)

    result = _run(repository, database, PYTHON_FILE, "--commit", shas[4][:8])

    assert result.exit_code == 0, result.stderr
    assert "AST analysis unavailable: parse failed" in result.stdout
    assert "SyntaxError: expected ':'" in result.stdout
    assert "Definitions:" not in result.stdout
    assert "KIND" not in result.stdout


def test_a_version_that_was_never_stored_says_to_run_ast(tmp_path: Path) -> None:
    repository = build_definition_repo(tmp_path / "unscanned")
    database = tmp_path / "unscanned.db"
    analyze(repository, database)

    result = _run(repository, database, PYTHON_FILE)

    assert result.exit_code == 0, result.stderr
    assert "AST analysis unavailable: no version was stored for this commit" in result.stdout
    assert "run 'archaeology ast'" in result.stdout


def test_a_file_that_is_gone_says_the_file_was_not_in_the_commit(gap) -> None:
    repository, database = gap

    result = _run(repository, database, "gone.py")

    assert result.exit_code == 0, result.stderr
    assert "AST analysis unavailable: the file was not in this commit" in result.stdout


def test_a_version_that_was_read_and_held_nothing_says_so(tmp_path: Path) -> None:
    """The other half of §7.2: this one *was* read, and it really held nothing."""
    repository = build_single_commit_repo(tmp_path / "comments")
    _commit_python(repository, ONLY_COMMENTS, "nothing to find")
    database = tmp_path / "comments.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    result = _run(repository, database, PYTHON_FILE)

    assert result.exit_code == 0, result.stderr
    assert "No definitions: the version was read and held none." in result.stdout
    assert "unavailable" not in result.stdout


def test_the_change_types_say_what_they_were_compared_against(definitions) -> None:
    """``change_type`` is a cached comparison, and this says against what."""
    repository, database = definitions

    result = _run(repository, database, PYTHON_FILE)

    assert (
        "Change types are against 00455bfc; 4cd03f6d in between could not be read"
        in result.stdout
    )


def test_commit_picks_the_version_to_show(definitions) -> None:
    repository, database = definitions
    shas = _shas(repository)

    result = _run(repository, database, PYTHON_FILE, "--commit", shas[2][:8])

    assert result.exit_code == 0, result.stderr
    assert f"Version:     2024-05-03 09:00:00 +0000  {shas[2][:8]}" in result.stdout
    assert "logout" in result.stdout and "created" in result.stdout
    assert "authenticate" not in result.stdout


def test_commit_refuses_a_commit_that_did_not_change_the_file(tmp_path: Path) -> None:
    repository, database = _scanned(tmp_path, build_sample_repo, "sample")
    shas = _shas(repository)
    merge = shas[4]

    result = _run(repository, database, "core/app.py", "--commit", merge[:8])

    assert result.exit_code == 1
    assert f"no version of core/app.py at {merge[:8]}" in result.stderr
    assert "--history" in result.stderr


def test_commit_refuses_a_prefix_that_names_no_stored_commit(definitions) -> None:
    repository, database = definitions

    result = _run(repository, database, PYTHON_FILE, "--commit", "ffffffff")

    assert result.exit_code == 1
    assert "no stored commit starts with 'ffffffff'" in result.stderr


def test_history_and_commit_together_are_refused(definitions) -> None:
    repository, database = definitions

    result = _run(repository, database, PYTHON_FILE, "--history", "--commit", "abc")

    assert result.exit_code == 1
    assert "use one or the other" in result.stderr


# --- the JSON ---------------------------------------------------------------


def test_the_json_carries_the_definitions_and_the_state(definitions) -> None:
    repository, database = definitions
    shas = _shas(repository)

    payload = _json(repository, database, PYTHON_FILE)

    assert payload["path"] == PYTHON_FILE
    assert payload["path_history"] == [PYTHON_FILE]
    assert payload["commit_sha"] == shas[5]
    assert payload["committed_at"] == "2024-05-06T09:00:00+00:00"
    assert payload["state"] == "read"
    assert payload["reason"] is None
    assert payload["parse_error"] is None
    assert payload["compared_with"] == shas[3]
    assert payload["blind_spots"] == [shas[4]]
    assert [definition["qualname"] for definition in payload["definitions"]] == [
        "authenticate",
        "helper",
        "logout",
    ]
    assert payload["definitions"][0] == {
        "qualname": "authenticate",
        "kind": "function",
        "lineno": 4,
        "end_lineno": 5,
        "decorators": [],
        "change_type": "unchanged",
    }


def test_the_json_of_a_version_that_could_not_be_parsed_carries_the_error(
    definitions,
) -> None:
    """The user's §7.2: the failure is a field, not a sentence."""
    repository, database = definitions
    shas = _shas(repository)

    payload = _json(repository, database, PYTHON_FILE, "--commit", shas[4][:8])

    assert payload["state"] == "unavailable"
    assert payload["reason"] == "parse failed"
    assert payload["parse_error"] == "SyntaxError: expected ':'"
    assert payload["error_lineno"] == 4
    assert payload["definitions"] == []


def test_the_json_of_a_file_that_is_gone_says_the_file_was_not_there(gap) -> None:
    repository, database = gap

    payload = _json(repository, database, "gone.py")

    assert payload["state"] == "unavailable"
    assert payload["reason"] == "the file was not in this commit"
    assert payload["parse_error"] is None
    assert payload["definitions"] == []


def test_the_json_goes_to_stdout_and_the_notice_to_stderr(definitions) -> None:
    """The user's §7.4: stdout is the JSON and nothing else."""
    repository, database = definitions
    _commit_python(repository, TWINS, "a commit the analysis has not seen")

    result = _run(repository, database, PYTHON_FILE, "--json")

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "read"
    # The notice about the analysis being behind goes to stderr, where it cannot
    # be mistaken for part of the JSON.
    assert "analysis stops at" in result.stderr


def test_the_history_json_carries_the_derived_changes(definitions) -> None:
    """The user's §7.3: ``deleted`` is derived, and this shows what from."""
    repository, database = definitions
    shas = _shas(repository)

    payload = _json(repository, database, PYTHON_FILE, "--history")

    assert payload["path"] == PYTHON_FILE
    (file,) = payload["files"]
    assert file["path_history"] == [PYTHON_FILE]

    # Every version is in there, with the state of the ones nobody could read.
    assert [version["commit_sha"] for version in file["versions"]] == shas
    assert file["versions"][4]["state"] == "unavailable"
    assert file["versions"][4]["parse_error"] == "SyntaxError: expected ':'"

    login = next(life for life in file["lives"] if life["qualname"] == "login")
    assert [event["change"] for event in login["events"]] == [
        "created",
        "modified",
        "deleted",
    ]
    gone = login["events"][-1]
    assert gone["commit_sha"] == shas[3]
    assert gone["previous_commit_sha"] == shas[2]
    assert gone["blind_spots"] == []

    # The deletion the fixture derives across the failure is derived from the
    # two versions around it, and says so.
    vanished = _json(repository, database, PYTHON_FILE, "--history")
    assert vanished["files"][0]["lives"][0]["events"][0]["change"] == "created"
