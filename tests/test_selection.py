"""Tests for ``selection``: what the model is shown when a commit is too large.

The unit this covers measured the bundle before it changed anything, and what it
found is why this module exists: a commit touching a thousand files is about
297,000 estimated tokens, and a commit touching two hundred with a real history
behind them is about 124,000. Neither fits a model, and the context builder is
right not to cap them — a file list that stopped early would hide the answer to
the question being asked, and a reader can scroll.

So the reduction happens to the model's view and not to the tool's evidence, and
four things are under test:

* **The evidence is not reduced.** What the offline path prints and what
  ``explain --json`` carries stay the bundle, and the view is derived from it
  rather than replacing it. This is the unit's whole rule.
* **A commit that fits is sent whole.** When nothing has to be dropped the view
  is the bundle object itself, so Units 5 to 11's "one bundle, two paths" still
  holds for every commit that was never too large.
* **A selection is stated, never silent.** Every count is held against the bundle
  it came from, and a file whose spans were cut says so on its own row — because
  a model answering about a partial bundle while believing it holds the whole one
  is the failure the architecture freeze names.
* **The answer is checked against what the model saw.** A citation of a row that
  was left out is refused, even though the bundle holds it.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.context import (
    build_context,
    build_json as build_context_json,
    build_object,
)
from codearchaeology.explanation import build_prompt
from codearchaeology.provider import estimate_tokens
from codearchaeology.selection import (
    ABSENCES,
    DEFINITIONS,
    FILE_CHANGES,
    HISTORY_WINDOW,
    MESSAGE_CHARACTERS,
    RANGES_PER_FILE,
    build_view,
)
from codearchaeology.storage import connect
from codearchaeology.validation import ExplanationError, validate
from test_provider import _completion, _endpoint

runner = CliRunner()

# The largest view the benchmark builds, over the widest shape it has: two
# hundred files, twenty hunks and ten definitions each, which comes to about
# 11,300 estimated tokens. The fixture here is narrower than that, so this is a
# guard on the constants rather than a measurement — raise `FILE_CHANGES` far
# enough and it fails, which is when somebody should look at the benchmark again.
VIEW_CEILING = 12_000


@pytest.fixture(scope="module")
def wide(tmp_path_factory: pytest.TempPathFactory):
    """The wide fixture, analyzed and read, with the database beside it."""
    from sample_repo import build_wide_commit_repo

    repository = build_wide_commit_repo(tmp_path_factory.mktemp("selection"))
    database = repository.parent / "wide.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    return repository, database


def _sha(database: Path, message: str) -> str:
    connection = connect(database)
    try:
        row = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", (message,)
        ).fetchone()
    finally:
        connection.close()
    return row["sha"]


def _context(repository, database, message):
    return build_context(repository, database, _sha(database, message))


def _kept_paths(view) -> set[str]:
    """Every path the view names, from all four places a path can appear."""
    found = {row["path"] for row in view.object["file_changes"]}
    found |= {life["path"] for life in view.object["lifecycle"]}
    found |= {entry["path"] for entry in view.object["history"]}
    for report in view.object["cochange"]:
        found.add(report["path"])
        found |= {partner["path"] for partner in report["partners"]}
    return found


def _answer(sha: str, path: str) -> str:
    """A well-formed answer citing one file."""
    return json.dumps(
        {
            "summary": f"The commit changed {path} [oc1].",
            "observed_changes": [
                {
                    "id": "oc1",
                    "statement": f"{path} was modified in this commit",
                    "evidence": ["ev1", "ev2"],
                }
            ],
            "evidence": [
                {"id": "ev1", "kind": "file", "ref": path},
                {"id": "ev2", "kind": "commit", "ref": sha},
            ],
            "possible_reasons": [],
            "uncertainty": [],
        }
    )


# The rule: the evidence is reduced for the model and for nobody else.


def test_the_evidence_is_not_reduced_by_being_selected(wide):
    """Unit 12's whole rule, in one test.

    The view is smaller — that is the point of the layer — and the bundle is the
    same bytes it was before the view was built, which is what keeps every
    citation checkable against the evidence the reader can see.
    """
    repository, database = wide
    context = _context(repository, database, "Rewrite every module")
    before = build_context_json(context)
    whole = build_object(context)

    view = build_view(context)

    assert view.selection is not None
    assert len(view.object["file_changes"]) < len(whole["file_changes"])
    assert build_context_json(context) == before
    assert len(build_object(context)["file_changes"]) == len(whole["file_changes"])


def test_a_commit_that_fits_is_shown_whole(wide):
    """Nothing dropped, nothing changed: the view is the bundle itself.

    The identity is exact rather than approximate, which is what makes the two
    paths unable to drift on any commit that was never too large.
    """
    repository, database = wide
    context = _context(repository, database, "Create the first module")

    view = build_view(context)

    assert view.selection is None
    assert view.note is None
    assert view.json == build_context_json(context)
    assert view.object == build_object(context)


def test_the_offline_path_prints_the_whole_evidence(wide):
    """Core First, and the fact layer: no model, no reduction, every file."""
    repository, database = wide
    context = _context(repository, database, "Rewrite every module")

    result = runner.invoke(
        app,
        ["explain", context.sha[:8], str(repository), "--db", str(database)],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == build_context_json(context).strip()
    assert len(json.loads(result.stdout)["file_changes"]) == 30


def test_the_json_carries_the_whole_evidence_and_says_what_the_model_saw(wide):
    """A program checking an answer needs both: the evidence, and how much of it
    the model was given."""
    repository, database = wide
    context = _context(repository, database, "Rewrite every module")

    with _endpoint({"status": 200, "body": _completion(_answer(context.sha, "mod10.py"))}) as server:
        result = runner.invoke(
            app,
            [
                "explain",
                context.sha[:8],
                str(repository),
                "--db",
                str(database),
                "--json",
            ],
            env={
                "CODEARCHAEOLOGY_AI_BASE_URL": server.base_url,
                "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            },
        )

    assert result.exit_code == 0
    document = json.loads(result.stdout)
    assert len(document["evidence"]["file_changes"]) == 30
    assert document["selection"]["file_changes"] == {
        "shown": FILE_CHANGES,
        "omitted": 30 - FILE_CHANGES,
    }


def test_the_command_says_in_a_sentence_what_the_model_was_shown(wide):
    """On stderr, so stdout stays the answer, and it names the counts."""
    repository, database = wide
    context = _context(repository, database, "Rewrite every module")

    with _endpoint({"status": 200, "body": _completion(_answer(context.sha, "mod10.py"))}) as server:
        result = runner.invoke(
            app,
            ["explain", context.sha[:6], str(repository), "--db", str(database)],
            env={
                "CODEARCHAEOLOGY_AI_BASE_URL": server.base_url,
                "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            },
        )

    assert result.exit_code == 0
    assert f"{FILE_CHANGES} of 30 file changes" in result.stderr
    assert f"{FILE_CHANGES} of 30 file changes" not in result.stdout


# What is kept, and why those rows.


def test_the_file_changes_kept_are_the_largest_ones(wide):
    """The rule is size, and the fixture makes it tellable from path order.

    File *index* changed ``index + 2`` lines, so the largest twenty are the last
    twenty by name and they are listed largest first. A path-order rule would keep
    the first twenty, and a rule that kept the right rows in the bundle's order
    would list them the other way round — which is why the assertion is on the
    sequence rather than on the set.
    """
    repository, database = wide
    view = build_view(_context(repository, database, "Rewrite every module"))

    kept = [row["path"] for row in view.object["file_changes"]]

    assert kept == [f"mod{index:02d}.py" for index in range(29, 9, -1)]
    assert view.selection["file_changes"] == {
        "shown": FILE_CHANGES,
        "omitted": 30 - FILE_CHANGES,
    }


def test_the_definitions_are_capped_and_a_tie_goes_by_name(wide):
    """Every function in this fixture spans the same two lines, so the order is
    the tie-breaker — which has to be a name and not whatever SQLite returned."""
    repository, database = wide
    view = build_view(_context(repository, database, "Rewrite every module"))

    kept = [row["qualname"] for row in view.object["ast_changes"]]

    assert kept == [f"function_{index}" for index in range(DEFINITIONS)]
    assert view.selection["ast_changes"] == {
        "shown": DEFINITIONS,
        "omitted": 30 - DEFINITIONS,
    }


def test_a_files_spans_are_cut_and_its_own_row_says_how_many_there_were(wide):
    """The count is on the row rather than only in the block: a file showing five
    spans and one that has exactly five look alike otherwise."""
    repository, database = wide
    view = build_view(_context(repository, database, "Rewrite every module"))

    rows = {row["path"]: row for row in view.object["file_changes"]}
    largest = rows["mod29.py"]

    assert len(largest["ranges"]) == RANGES_PER_FILE
    assert largest["ranges_total"] == 31
    assert view.selection["ranges"]["per_file"] == RANGES_PER_FILE
    assert view.selection["ranges"]["omitted"] > 0


def test_a_row_whose_spans_were_not_cut_carries_no_total(wide):
    """The other half of the same rule: a field that appeared on every row would
    stop meaning "this one was cut"."""
    repository, database = wide
    view = build_view(_context(repository, database, "Add the other modules"))

    assert all("ranges_total" not in row for row in view.object["file_changes"])
    assert view.selection["ranges"]["omitted"] == 0
    assert view.selection["file_changes"]["omitted"] == 29 - FILE_CHANGES


def test_the_commit_message_is_cut_at_the_ceiling(wide):
    """No fixture writes a message this long, so one is put on a real context:
    the section is the last unbounded one and a bound nothing fills is a bound
    nothing checks."""
    repository, database = wide
    context = _context(repository, database, "Create the first module")

    view = build_view(replace(context, message="x" * (MESSAGE_CHARACTERS + 1000)))

    assert len(view.object["commit"]["message"]) == MESSAGE_CHARACTERS
    assert view.selection["message"] == {
        "characters_shown": MESSAGE_CHARACTERS,
        "characters_omitted": 1000,
    }


def test_the_earlier_commit_window_is_narrowed_and_the_bounds_say_so(wide):
    """The one thing the view re-cuts rather than drops rows from.

    ``bounds`` is where the bundle already says what it capped, so the view's
    window is stated there — and the count is recomputed for the rows the view
    actually holds, so it cannot describe a set it does not have.
    """
    repository, database = wide
    context = _context(repository, database, "Rewrite every module")

    view = build_view(context)

    assert build_object(context)["bounds"]["history_commits"] == 5
    assert view.object["bounds"]["history_commits"] == HISTORY_WINDOW
    assert all(
        len(entry["commits"]) == HISTORY_WINDOW for entry in view.object["history"]
    )
    assert view.object["bounds"]["history_commits_omitted"] == sum(
        max(0, entry["total"] - HISTORY_WINDOW) for entry in view.object["history"]
    )


def test_the_life_and_the_earlier_commits_follow_the_files_that_were_kept(wide):
    """Context about a file is not sent for a file that was not shown."""
    repository, database = wide
    view = build_view(_context(repository, database, "Rewrite every module"))

    kept = {row["path"] for row in view.object["file_changes"]}

    assert {life["path"] for life in view.object["lifecycle"]} == kept
    assert {entry["path"] for entry in view.object["history"]} == kept


# Stated, never silent.


def test_every_count_says_what_it_showed_and_what_it_left_out(wide):
    """Every count is held against the bundle section it came from, so a number
    that drifted from the list it describes fails here."""
    repository, database = wide
    context = _context(repository, database, "Rewrite every module")
    whole = build_object(context)

    view = build_view(context)

    for name, section in (
        ("file_changes", "file_changes"),
        ("ast_changes", "ast_changes"),
        ("lifecycle", "lifecycle"),
        ("history", "history"),
        ("absences", "absences"),
    ):
        entry = view.selection[name]
        assert entry["shown"] + entry["omitted"] == len(whole[section])
        assert entry["shown"] == len(view.object[section])
    assert view.selection["ranges"]["shown"] == sum(
        len(row["ranges"]) for row in view.object["file_changes"]
    )


def test_the_note_names_what_the_model_was_shown(wide):
    repository, database = wide
    view = build_view(_context(repository, database, "Rewrite every module"))

    assert f"the largest {FILE_CHANGES} of 30 file changes" in view.note
    assert f"{DEFINITIONS} of 30 AST changes" in view.note
    assert "each file's life and recent history" in view.note
    assert "no model configured" in view.note


def test_the_prompt_carries_the_selection_and_the_rule_for_reading_it(wide):
    """The model is told what the block means, because a block it has to infer
    the meaning of is a block it can read the wrong way."""
    repository, database = wide
    view = build_view(_context(repository, database, "Rewrite every module"))

    prompt = build_prompt(view.json)

    assert json.loads(prompt.split("The evidence:", 1)[1]) == view.object
    assert "`selection`" in prompt
    assert "ranges_total" in prompt


# The answer is checked against what the model saw.


def test_a_citation_of_a_file_that_was_left_out_is_refused(wide):
    """The tighter of the two checks, and the honest one: an answer has to rest
    on what it was given, not on something that happens to be true elsewhere.

    The path is found rather than written down, because which files the view
    names depends on the co-change section as well as on the cap.
    """
    repository, database = wide
    context = _context(repository, database, "Rewrite every module")
    view = build_view(context)
    named = _kept_paths(view)
    left_out = [
        row["path"]
        for row in build_object(context)["file_changes"]
        if row["path"] not in named
    ]
    assert left_out, "the fixture must leave a file out of the view"

    answer = _answer(context.sha, left_out[0])

    with pytest.raises(ExplanationError) as raised:
        validate(answer, view.context)

    assert left_out[0] in str(raised.value)
    # The same answer is fine against the bundle, which is what makes this a
    # check about the view rather than about the answer.
    assert validate(answer, context).observed_changes


# Determinism, and the ceiling.


def test_the_same_context_selects_the_same_rows(wide):
    """Two builds from the same database, byte for byte. The ordering rule has
    three tie-breakers precisely so that this holds on a repository where every
    size is equal."""
    repository, database = wide
    sha = _sha(database, "Rewrite every module")

    first = build_view(build_context(repository, database, sha))
    second = build_view(build_context(repository, database, sha))

    assert first.json == second.json


def test_the_view_of_a_large_commit_is_bounded(wide):
    """The number this unit exists for: the view is bounded by the constants and
    not by the size of the commit. The benchmark carries the worst shape; this
    carries the guard."""
    repository, database = wide
    context = _context(repository, database, "Rewrite every module")

    view = build_view(context)

    assert estimate_tokens(view.json) < VIEW_CEILING
    assert estimate_tokens(view.json) < estimate_tokens(build_context_json(context))
    assert len(view.object["absences"]) <= ABSENCES
