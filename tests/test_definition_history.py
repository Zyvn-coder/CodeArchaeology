"""Tests for the histories derived from the stored snapshots.

The fixtures are real repositories, analyzed and AST-scanned the way a user runs
them: what a definition's life looks like is a question about a history, and a
hand-built one would be answering its own question.

Commit indices come from ``git rev-list --reverse``, so "index 3" is the fourth
commit of the fixture and stays the fourth when a commit is added at the end.
"""

from pathlib import Path

import pytest

from codearchaeology import storage
from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.definition_history import (
    CREATED,
    DELETED,
    MODIFIED,
    NOT_IN_TREE,
    NOT_STORED,
    BlindSpot,
    DefinitionLife,
    FileHistory,
    Snapshot,
    load_histories,
)
from codearchaeology.storage import connect
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

ONLY_COMMENTS = '''\
# Nothing here is a definition.
# Not even this.
'''

# One file, two definitions sharing a name, and a third one between them. The
# name alone cannot tell the two ``f``s apart, so the *n*-th rule is the only
# thing that can — which is what these three versions pin down.
TWINS = '''\
def f():
    return 1


def g():
    return 2


def f():
    return 3
'''

TWINS_FIRST_CHANGED = '''\
def f():
    return 10


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
    """Build one fixture, analyze and AST-scan it, and return it with its commits."""
    repository = builder(tmp_path / name)
    database = tmp_path / f"{name}.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    return repository, database, _shas(repository)


def _only(repository: Path, database: Path, path: str = PYTHON_FILE) -> FileHistory:
    """The one history a path carries, for the fixtures that give it one."""
    histories = load_histories(repository, database, path)
    assert len(histories) == 1, f"{path} carries {len(histories)} file lives"
    return histories[0]


def _life(history: FileHistory, qualname: str, occurrence: int = 0) -> DefinitionLife:
    matches = [
        life
        for life in history.lives
        if life.qualname == qualname and life.occurrence == occurrence
    ]
    assert len(matches) == 1, f"no single life for {qualname}#{occurrence}"
    return matches[0]


def _changes(life: DefinitionLife, shas: list[str]) -> list[tuple[str, int]]:
    """The life's events as (what happened, the index of the commit it was seen at)."""
    return [(event.change, shas.index(event.commit_sha)) for event in life.events]


def _blind_spots(history: FileHistory) -> list[BlindSpot]:
    return [version for version in history.versions if isinstance(version, BlindSpot)]


def _snapshots(history: FileHistory) -> list[Snapshot]:
    return [version for version in history.versions if isinstance(version, Snapshot)]


@pytest.fixture
def definitions(tmp_path: Path):
    """The definition fixture: six commits, one file, every kind of change."""
    return _scanned(tmp_path, build_definition_repo, "definitions")


@pytest.fixture
def gap(tmp_path: Path):
    """The fixture whose files go dark: one gap, three endings."""
    return _scanned(tmp_path, build_gap_repo, "gap")


def test_the_first_version_creates_every_definition_it_holds(definitions) -> None:
    repository, database, shas = definitions
    history = _only(repository, database)

    login = _life(history, "login")
    first = login.events[0]
    assert (first.change, first.commit_sha) == (CREATED, shas[0])
    assert (first.lineno, first.end_lineno) == (4, 5)
    assert first.previous_commit_sha is None
    assert first.blind_spots == ()
    assert _changes(_life(history, "helper"), shas) == [(CREATED, 0)]


def test_a_rewritten_definition_is_modified_where_it_was_rewritten(definitions) -> None:
    repository, database, shas = definitions
    history = _only(repository, database)

    changed = _life(history, "login").events[1]
    assert (changed.change, changed.commit_sha) == (MODIFIED, shas[1])
    assert changed.previous_commit_sha == shas[0]
    assert changed.blind_spots == ()
    assert changed.path == PYTHON_FILE
    assert changed.lineno == 4
    # The definition that was not touched has nothing to say about that version.
    assert _changes(_life(history, "helper"), shas) == [(CREATED, 0)]


def test_an_added_definition_is_created_where_it_was_added(definitions) -> None:
    repository, database, shas = definitions
    history = _only(repository, database)
    assert _changes(_life(history, "logout"), shas) == [(CREATED, 2)]


def test_an_unchanged_definition_is_not_an_event(definitions) -> None:
    """``unchanged`` is a fact the snapshots hold, not something that happened."""
    repository, database, shas = definitions
    history = _only(repository, database)

    helper = _life(history, "helper")
    assert _changes(helper, shas) == [(CREATED, 0)]
    # It is in every version of the file, and only its creation is an event.
    assert len(_snapshots(history)) == 5


def test_a_renamed_function_is_one_death_and_one_birth(definitions) -> None:
    repository, database, shas = definitions
    history = _only(repository, database)

    assert _changes(_life(history, "authenticate"), shas) == [(CREATED, 3)]

    login = _life(history, "login")
    assert _changes(login, shas) == [(CREATED, 0), (MODIFIED, 1), (DELETED, 3)]
    gone = login.events[-1]
    # Nothing was there to measure, and nothing claims the two names are one
    # function under a new name.
    assert (gone.lineno, gone.end_lineno, gone.fingerprint, gone.decorators) == (
        None,
        None,
        None,
        (),
    )
    assert gone.previous_commit_sha == shas[2]


def test_a_version_that_could_not_be_parsed_is_a_blind_spot(definitions) -> None:
    repository, database, shas = definitions
    history = _only(repository, database)

    blind = _blind_spots(history)
    assert [spot.commit_sha for spot in blind] == [shas[4]]
    assert blind[0].reason.startswith("SyntaxError")
    assert blind[0].lineno == 4
    assert blind[0].path == PYTHON_FILE
    # Every version of the file is in the history, in the order it happened.
    assert [version.commit_sha for version in history.versions] == shas


def test_the_version_after_a_failure_is_compared_across_it(definitions) -> None:
    repository, database, shas = definitions
    history = _only(repository, database)

    # F holds what D held, so nothing happened to any definition there — and the
    # failure at E is not read as a version that emptied the file.
    assert all(
        event.commit_sha != shas[5] for life in history.lives for event in life.events
    )
    for name in ("authenticate", "helper", "logout"):
        assert _life(history, name).deleted is None


def test_the_derived_events_agree_with_the_stored_comparison(definitions) -> None:
    """The pass's cached comparison and this derivation must tell one story."""
    repository, database, _ = definitions
    history = _only(repository, database)

    derived = {
        (event.commit_sha, life.kind, life.qualname, life.occurrence): event.change
        for life in history.lives
        for event in life.events
    }

    connection = connect(database)
    try:
        rows = connection.execute(
            "SELECT commit_sha, kind, qualname, change_type FROM definition_versions"
            " WHERE path = ? ORDER BY commit_sha, position",
            (PYTHON_FILE,),
        ).fetchall()
    finally:
        connection.close()

    assert rows, "the fixture has to have stored rows to compare against"
    counted: dict[tuple[str, str, str], int] = {}
    unchanged = 0
    for row in rows:
        counter = (row["commit_sha"], row["kind"], row["qualname"])
        occurrence = counted.get(counter, 0)
        counted[counter] = occurrence + 1
        identity = (row["commit_sha"], row["kind"], row["qualname"], occurrence)
        if row["change_type"] == storage.UNCHANGED:
            unchanged += 1
            assert identity not in derived, "unchanged is a fact, not an event"
        else:
            assert derived.get(identity) == row["change_type"]

    # Both directions, with the one kind of event that cannot have a row: a
    # deletion, whose absence from the table is the whole of its evidence.
    deletions = sum(1 for change in derived.values() if change == DELETED)
    assert deletions == 1
    assert len(derived) - deletions == len(rows) - unchanged


def test_a_version_with_no_stored_row_is_a_gap(definitions) -> None:
    repository, database, shas = definitions
    connection = connect(database)
    try:
        with connection:
            connection.execute(
                "DELETE FROM file_versions WHERE commit_sha = ? AND path = ?",
                (shas[2], PYTHON_FILE),
            )
    finally:
        connection.close()

    history = _only(repository, database)
    assert [spot.commit_sha for spot in _blind_spots(history)] == [shas[2], shas[4]]
    assert _blind_spots(history)[0].reason == NOT_STORED

    # Last seen at B, gone at D — and the version in between is named rather
    # than guessed over.
    login = _life(history, "login")
    assert _changes(login, shas) == [(CREATED, 0), (MODIFIED, 1), (DELETED, 3)]
    assert login.events[-1].previous_commit_sha == shas[1]
    assert [spot.commit_sha for spot in login.events[-1].blind_spots] == [shas[2]]


def test_a_file_that_was_never_scanned_says_so(tmp_path: Path) -> None:
    repository = build_definition_repo(tmp_path / "unscanned")
    database = tmp_path / "unscanned.db"
    analyze(repository, database)

    history = _only(repository, database)
    assert history.lives == ()
    assert {spot.reason for spot in _blind_spots(history)} == {NOT_STORED}
    assert len(_blind_spots(history)) == 6


def test_a_path_the_history_never_carried_has_no_history(definitions) -> None:
    repository, database, _ = definitions
    assert load_histories(repository, database, "nowhere.py") == ()


def test_a_rename_carries_the_definitions_across_the_names(tmp_path: Path) -> None:
    repository, database, shas = _scanned(tmp_path, build_sample_repo, "sample")
    history = _only(repository, database, "core/app.py")

    assert history.path_history == ("app.py", "core/app.py")
    login = _life(history, "login")
    assert _changes(login, shas) == [(CREATED, 0), (MODIFIED, 3)]
    # Each event names the name the file had at that commit.
    assert [event.path for event in login.events] == ["app.py", "core/app.py"]
    assert _life(history, "logout").events[0].path == "core/app.py"


def test_a_name_used_again_is_a_second_history(tmp_path: Path) -> None:
    repository, database, shas = _scanned(tmp_path, build_lifecycle_repo, "lifecycle")

    histories = load_histories(repository, database, "app.py")

    assert [history.path_history for history in histories] == [
        ("app.py", "src/app.py", "src/core/app.py"),
        ("app.py",),
    ]
    # The first file was nothing but a docstring, and the second holds a
    # definition that is created there rather than deleted in the first.
    assert histories[0].lives == ()
    assert _changes(_life(histories[1], "main"), shas) == [(CREATED, 4)]


def test_a_file_that_is_not_python_has_no_lives(tmp_path: Path) -> None:
    repository, database, _ = _scanned(tmp_path, build_sample_repo, "sample")
    history = _only(repository, database, "README.md")

    assert history.lives == ()
    assert [spot.reason for spot in _blind_spots(history)] == [NOT_STORED]


def test_a_deletion_across_a_gap_names_the_gap(gap) -> None:
    repository, database, shas = gap
    history = _only(repository, database, "vanished.py")

    login = _life(history, "login")
    # Not "deleted at B", and not a deletion at C taken as an unbroken history:
    # last seen at A, gone by C, with B named as the version nobody read.
    assert _changes(login, shas) == [(CREATED, 0), (DELETED, 2)]
    gone = login.events[-1]
    assert gone.previous_commit_sha == shas[0]
    assert [spot.commit_sha for spot in gone.blind_spots] == [shas[1]]
    assert gone.blind_spots[0].reason.startswith("SyntaxError")
    # helper was there before and after, and the gap is not an event for it.
    assert _changes(_life(history, "helper"), shas) == [(CREATED, 0)]


def test_a_definition_whose_file_never_parses_again_has_no_ending(gap) -> None:
    repository, database, shas = gap
    history = _only(repository, database, "dark.py")

    for name in ("login", "helper"):
        life = _life(history, name)
        assert _changes(life, shas) == [(CREATED, 0)]
        assert life.deleted is None
        assert [spot.commit_sha for spot in life.unresolved] == [shas[1]]
        assert life.unresolved[0].reason.startswith("SyntaxError")
    assert [spot.commit_sha for spot in _blind_spots(history)] == [shas[1]]


def test_a_file_deleted_after_a_gap_closes_its_definitions(gap) -> None:
    repository, database, shas = gap
    history = _only(repository, database, "gone.py")

    for name in ("login", "helper"):
        life = _life(history, name)
        assert _changes(life, shas) == [(CREATED, 0), (DELETED, 2)]
        gone = life.events[-1]
        assert gone.previous_commit_sha == shas[0]
        assert [spot.commit_sha for spot in gone.blind_spots] == [shas[1]]
        # A life that was seen to end is not reopened by versions nobody read.
        assert life.unresolved == ()


def test_a_file_absent_from_one_commit_is_a_gap_not_an_ending(tmp_path: Path) -> None:
    """A delete on one branch and an edit on another, interleaved by the clock."""
    repository, database, shas = _scanned(
        tmp_path, build_interleaved_repo, "interleaved"
    )
    history = _only(repository, database)

    assert [(spot.commit_sha, spot.reason) for spot in _blind_spots(history)] == [
        (shas[1], NOT_IN_TREE)
    ]

    login = _life(history, "login")
    assert _changes(login, shas) == [(CREATED, 0), (MODIFIED, 2)]
    assert login.events[-1].previous_commit_sha == shas[0]
    assert [spot.commit_sha for spot in login.events[-1].blind_spots] == [shas[1]]
    assert login.unresolved == ()


def test_two_definitions_with_one_name_keep_two_lives(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "twins")
    _commit_python(repository, TWINS, "two definitions named f")
    _commit_python(repository, TWINS_FIRST_CHANGED, "the first one changes")
    _commit_python(repository, TWINS_SECOND_GONE, "the second one goes")
    database = tmp_path / "twins.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    shas = _shas(repository)

    history = _only(repository, database)

    assert [life.qualname for life in history.lives] == ["f", "g", "f"]
    assert _changes(_life(history, "f", 0), shas) == [(CREATED, 1), (MODIFIED, 2)]
    assert _changes(_life(history, "f", 1), shas) == [(CREATED, 1), (DELETED, 3)]
    assert _changes(_life(history, "g"), shas) == [(CREATED, 1)]


def test_a_file_with_no_definitions_has_a_history_with_no_lives(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "comments")
    _commit_python(repository, ONLY_COMMENTS, "nothing to find")
    _commit_python(repository, ONLY_COMMENTS + "\n# and one more\n", "still nothing")
    database = tmp_path / "comments.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    history = _only(repository, database)
    # A version that was read and held nothing is a snapshot, not a blind spot,
    # and a file of nothing but comments has a history with no definitions in it.
    assert len(_snapshots(history)) == 2
    assert _blind_spots(history) == []
    assert history.lives == ()
