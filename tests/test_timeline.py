"""Tests for the timeline view and its command."""

import io
import json
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from codearchaeology.analysis import AnalysisError, analyze
from codearchaeology.cli import app
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.history import read_head_sha
from codearchaeology.timeline import Timeline, build_table, load_timeline
from sample_repo import build_single_commit_repo

runner = CliRunner()

FIXTURE_COMMITS = 6
WIDE_MESSAGE = "宽" * 200


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "history.db"


@pytest.fixture
def stored(sample_repo: Path, database: Path) -> Timeline:
    analyze(sample_repo, database)
    return load_timeline(sample_repo.resolve(), database)


def _render(rows, width: int = 80) -> str:
    """Render rows at a fixed width so the test cannot depend on the terminal."""
    stream = io.StringIO()
    Console(file=stream, width=width).print(build_table(rows, width))
    return stream.getvalue()


def test_loads_the_stored_history(stored: Timeline, sample_repo: Path) -> None:
    assert stored.repository_root == sample_repo.resolve()
    assert len(stored.commits) == FIXTURE_COMMITS
    assert stored.head_sha in {commit.sha for commit in stored.commits}


def test_missing_database_asks_for_an_analysis(sample_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(AnalysisError, match="archaeology analyze"):
        load_timeline(sample_repo.resolve(), tmp_path / "absent.db")


def test_database_holding_another_repository_is_rejected(
    sample_repo: Path, tmp_path: Path, database: Path
) -> None:
    analyze(build_single_commit_repo(tmp_path / "other"), database)

    with pytest.raises(AnalysisError, match="holds"):
        load_timeline(sample_repo.resolve(), database)


def test_rows_are_newest_first(stored: Timeline) -> None:
    rows = stored.rows()

    assert rows[0].message == "Add logo and unicode module, drop legacy helper"
    assert rows[-1].message == "Initial commit"


def test_row_fields(stored: Timeline) -> None:
    oldest = stored.rows()[-1]

    assert oldest.date == "2024-03-01"
    assert oldest.author == "Ada Lovelace"
    assert oldest.files_changed == 3
    assert oldest.insertions == 21
    assert oldest.deletions == 0
    assert len(oldest.sha) == 40


def test_row_keeps_only_the_first_message_line(tmp_path: Path) -> None:
    repository = build_single_commit_repo(
        tmp_path / "body", message="Subject line\n\nA body that is not the subject."
    )
    database = tmp_path / "body.db"
    analyze(repository, database)

    rows = load_timeline(repository.resolve(), database).rows()

    assert rows[0].message == "Subject line"


def test_limit_takes_the_newest_commits(stored: Timeline) -> None:
    assert stored.rows(2) == stored.rows()[:2]
    assert len(stored.rows(2)) == 2


def test_json_has_the_documented_shape(stored: Timeline) -> None:
    document = json.loads(stored.as_json())

    assert document["head_sha"] == stored.head_sha
    assert len(document["commits"]) == FIXTURE_COMMITS
    assert document["commits"][-1] == {
        "sha": stored.commits[-1].sha,
        "date": "2024-03-01",
        "author": "Ada Lovelace",
        "files_changed": 3,
        "insertions": 21,
        "deletions": 0,
        "message": "Initial commit",
    }


def test_json_respects_the_limit(stored: Timeline) -> None:
    document = json.loads(stored.as_json(2))

    assert len(document["commits"]) == 2


def test_command_prints_a_table(sample_repo: Path, database: Path) -> None:
    analyze(sample_repo, database)

    result = runner.invoke(app, ["timeline", str(sample_repo), "--db", str(database)])

    assert result.exit_code == 0
    assert "Initial commit" in result.stdout
    assert "2024-03-01" in result.stdout
    assert read_head_sha(sample_repo)[:SHORT_SHA_LENGTH] in result.stdout


def test_command_limit_says_how_many_are_hidden(
    sample_repo: Path, database: Path
) -> None:
    analyze(sample_repo, database)

    result = runner.invoke(
        app,
        ["timeline", str(sample_repo), "--db", str(database), "--limit", "2"],
    )

    assert result.exit_code == 0
    assert "Initial commit" not in result.stdout
    assert "4 more commits" in result.stdout


def test_command_all_shows_every_commit(sample_repo: Path, database: Path) -> None:
    analyze(sample_repo, database)

    result = runner.invoke(
        app,
        ["timeline", str(sample_repo), "--db", str(database), "--limit", "2", "--all"],
    )

    assert result.exit_code == 0
    assert "Initial commit" in result.stdout
    assert "more commits" not in result.stdout


def test_command_json_output_stays_parseable(sample_repo: Path, database: Path) -> None:
    analyze(sample_repo, database)

    result = runner.invoke(
        app, ["timeline", str(sample_repo), "--db", str(database), "--json"]
    )

    assert result.exit_code == 0
    assert len(json.loads(result.stdout)["commits"]) == FIXTURE_COMMITS


def test_command_asks_for_an_analysis_when_there_is_none(
    sample_repo: Path, database: Path
) -> None:
    result = runner.invoke(app, ["timeline", str(sample_repo), "--db", str(database)])

    assert result.exit_code == 1
    assert "archaeology analyze" in result.stderr


def test_table_gives_way_in_the_message_not_in_the_sha(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "wide", message=WIDE_MESSAGE)
    database = tmp_path / "wide.db"
    analyze(repository, database)
    row = load_timeline(repository.resolve(), database).rows()[0]

    rendered = _render([row])

    assert row.sha[:SHORT_SHA_LENGTH] in rendered
    assert row.date in rendered


def test_command_keeps_the_sha_readable_next_to_a_wide_message(tmp_path: Path) -> None:
    repository = build_single_commit_repo(tmp_path / "wide", message=WIDE_MESSAGE)
    database = tmp_path / "wide.db"
    analyze(repository, database)

    result = runner.invoke(
        app,
        ["timeline", str(repository), "--db", str(database)],
        env={"COLUMNS": "80"},
    )

    assert result.exit_code == 0
    assert read_head_sha(repository)[:SHORT_SHA_LENGTH] in result.stdout
