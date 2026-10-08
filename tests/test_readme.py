"""Replay the READMEs' console blocks and hold them to the real output.

The READMEs' examples are the command line's contract: a reader copies a line
and expects what the block shows. Nothing else in the suite reads those blocks,
so a change to a table's columns, a JSON key or a sentence could ship with the
documentation describing the previous behaviour — and no test would fail.

**What this checks, and what it deliberately does not.** It runs each block
against the fixtures and compares the output line for line, and it parses every
``--json`` block so a shape that stopped being valid JSON is caught. It does not
test the behaviour behind the examples: those have their own tests, and this one
would become a second suite if it tried to cover them. The principle is to catch
what drifts — the exact bytes a reader sees — and leave the rest alone.

**How a run is made to look like the block.** The blocks are written against a
repository at ``~/projects/sample-project`` and the default cache directory, and
a fixture lives in a temporary directory whose database name is a digest of its
path. So each scenario points ``CODEARCHAEOLOGY_CACHE_DIR`` at a directory of
its own, builds its database where the default lookup will find it, and the
checker replaces the real paths with the documented ones before comparing. The
blocks carry no ``--db``, which is exactly why this works: the command resolves
the database itself, and the scenario decides what that resolves to.

**The states.** The documentation demonstrates five, so there are five
scenarios: a repository that has been analyzed, the same one after the AST pass,
the same one where the analysis stops short of HEAD, the separation demo's
seven-commit repository, and the repository whose Python file stops parsing. A
block is replayed against the one its own text names, and the database is put
back to that state before each block, because commands write.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shlex
import shutil
import subprocess

import pytest

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cache import database_path
from codearchaeology.cli import app
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.memory import (
    Citation,
    Subject,
    admit,
    invalidate,
    read_memories,
)
from codearchaeology.storage import connect
from codearchaeology.provider import (
    BASE_URL_VARIABLE,
    CONTEXT_LIMIT_VARIABLE,
    FALLBACK_KEY_VARIABLE,
    KEY_VARIABLE,
    MODEL_VARIABLE,
    OUTPUT_LIMIT_VARIABLE,
    RETRIES_VARIABLE,
    TIMEOUT_VARIABLE,
)

# Every variable that decides whether `explain` talks to a model. The checker
# takes them all out of the child environment, so the block that shows the
# offline path shows it on every machine rather than only on one with no
# endpoint configured.
AI_VARIABLES = (
    BASE_URL_VARIABLE,
    MODEL_VARIABLE,
    KEY_VARIABLE,
    FALLBACK_KEY_VARIABLE,
    TIMEOUT_VARIABLE,
    RETRIES_VARIABLE,
    CONTEXT_LIMIT_VARIABLE,
    OUTPUT_LIMIT_VARIABLE,
)
from sample_repo import (
    APP_AFTER,
    AUTHOR_EMAIL,
    AUTHOR_NAME,
    LATER_DATE,
    _commit,
    _write_file,
    add_commit,
    build_broken_repo,
    build_sample_repo,
    git_output,
)

ROOT = Path(__file__).parent.parent

DOCUMENTED_REPOSITORY = "/home/you/projects/sample-project"
# The command lines write the repository the short way a shell takes, so the
# routing looks for this form; the substitutions below use the expanded one,
# because that is what the output prints.
BROKEN_ARGUMENT = "~/projects/broken-project"
# The path a moved repository's memories name, and the checkout no longer has.
# Only the block that shows `memory adopt` writes it, and it is documented for
# the same reason the repository's own path is: the fixture is elsewhere.
OLD_ARGUMENT = "~/old/sample-project"
DOCUMENTED_OLD_REPOSITORY = "/home/you/old/sample-project"
DOCUMENTED_DATABASE = "/home/you/.cache/codearchaeology/82d48b376e387fde.db"

# The file the `memory export` block writes into the checkout. Copying the
# database back is not enough for it: a file is not a database, and both READMEs
# carry the same block, so the second one would meet what the first one wrote and
# be shown a refusal instead of what its own text says.
DOCUMENTED_EXPORT_FILE = "memories.jsonl"

BLOCK = re.compile(r"```console\n(.*?)```", re.S)
COMMAND = re.compile(r"^\$ (archaeology [^\n]*)$", re.M)

# The ids the memory blocks show. A memory's id is generated when it is
# admitted, so the block that creates one cannot write down what it will print:
# the checker holds the shape of that line and puts the value back to the one
# the documentation uses. The fixture's own memories are put back by value, so
# two memories still read as two.
DOCUMENTED_CREATED_ID = "3f2a9c1e-8b47-4d6a-9f10-2c5e7a0b41d9"
DOCUMENTED_MEMORY_IDS = (
    "b7d4e2f1-9c3a-4e58-8f02-1a6c5d9b7e34",  # the file memory, newest
    "5e8a1c47-3f92-4b6d-a1e8-7c2f4d9a6b53",  # the project memory
    "a1f3d8c2-6b40-4c19-9e75-2d8f0a3b5c61",  # the one that was invalidated
)
MEMORY_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


class Scenario:
    """One fixture state, and the commands the documentation runs against it.

    ``build`` is called with the database path the default lookup will produce,
    so a block's command — which carries no ``--db`` — finds what the scenario
    prepared.
    """

    def __init__(self, workdir: Path, build, builder=build_sample_repo) -> None:
        self.workdir = workdir
        self.cache = workdir / "cache"
        self.cache.mkdir()
        # Resolved, because the tool resolves the repository before printing it
        # and before naming the database, and a substitution has to match the
        # form the output actually carries.
        self.repository = builder(workdir / "repo").resolve()

        previous = os.environ.get("CODEARCHAEOLOGY_CACHE_DIR")
        os.environ["CODEARCHAEOLOGY_CACHE_DIR"] = str(self.cache)
        try:
            self.database = database_path(self.repository)
            # A builder may return the path its memories were made about and the
            # checkout no longer has — the state a moved repository leaves. The
            # block that shows `memory adopt` names it, so it is documented the
            # way the repository's own path is.
            self.foreign = build(self.repository, self.database)
        finally:
            if previous is None:
                os.environ.pop("CODEARCHAEOLOGY_CACHE_DIR", None)
            else:
                os.environ["CODEARCHAEOLOGY_CACHE_DIR"] = previous

        # A snapshot of the state this scenario is about, because commands
        # write: `analyze` and `ast` both change the database, so a block that
        # ran the pass would leave the next block reading a state its own text
        # does not describe. Each block gets the state back before it runs.
        self.pristine = workdir / "pristine.db"
        shutil.copy(self.database, self.pristine)

        # What the memory blocks show, put back to the documented ids: the
        # scenario reads the memories it just made and pairs them with the ids
        # the documentation uses, newest first, which is the order they print.
        self.memory_ids: dict[str, str] = {}
        if self.database.is_file():
            self.memory_ids = _documented_ids(self)

    def restore(self) -> None:
        """Put the database back to the state this scenario is about.

        Commands write — ``analyze`` and ``ast`` both change the database — so a
        block that ran the pass would leave the next block reading a state its
        own text does not describe. Called once per block, before its first
        command: a block with several commands means them to build on each
        other, which is the whole point of the separation demo.
        """
        shutil.copy(self.pristine, self.database)
        exported = self.repository / DOCUMENTED_EXPORT_FILE
        if exported.is_file():
            exported.unlink()

    def _real(self, argument: str, back: dict) -> str:
        """One documented argument put back to the value this fixture has.

        Three things are written the documented way and are not the fixture's:
        a memory's id (generated when it was admitted), the repository's path,
        and the path a moved repository's memories name.
        """
        if argument in back:
            return back[argument]
        if argument.startswith("~/projects/"):
            return str(self.repository)
        if self.foreign and argument.startswith("~/old/"):
            return self.foreign
        return argument

    def run(self, command: str) -> subprocess.CompletedProcess:
        """Run one documented command against this scenario's fixture.

        The block writes the repository as ``~/projects/sample-project``; the
        fixture is elsewhere, so the path is put back before running. Nothing
        else is changed — the arguments are the block's own, in its own order.
        """
        # Split the way a shell would, because a block's command is a shell
        # command: a memory's statement is prose and has to be quoted, and a
        # plain split would hand the tool its first word.
        arguments = shlex.split(command)
        assert arguments[0] == "archaeology"

        # A block may address a memory by the id it shows, and a fixture's ids
        # are generated — so the documented one is put back to the real one
        # here, the way the repository's path is.
        back = {
            documented: actual for actual, documented in self.memory_ids.items()
        }
        back.update(
            {
                documented[:SHORT_SHA_LENGTH]: actual[:SHORT_SHA_LENGTH]
                for actual, documented in self.memory_ids.items()
            }
        )
        arguments = [self._real(argument, back) for argument in arguments]
        environment = dict(
            os.environ,
            CODEARCHAEOLOGY_CACHE_DIR=str(self.cache),
            # The blocks were written on a terminal this wide; the checker has
            # no terminal at all, so the width is set rather than measured. Rich
            # would otherwise fall back to 80 and truncate every message column.
            COLUMNS="110",
        )
        # The blocks show the offline path, and it is the only one that can be
        # replayed: a model's answer is not the same twice, and a block that
        # promised one would be a block that fails on a busy day. A developer
        # with an endpoint configured would otherwise see the online path here —
        # the block would fail on their machine and nowhere else, which is the
        # worst kind of red.
        for name in AI_VARIABLES:
            environment.pop(name, None)

        return subprocess.run(
            ["uv", "run", "archaeology", *arguments[1:]],
            # Inside the repository, because the blocks that carry no path —
            # `archaeology timeline --limit 2` — are written for a reader who is
            # already standing in it.
            cwd=self.repository,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


def _analyzed(repository: Path, database: Path) -> None:
    analyze(repository, database)


def _analyzed_and_read(repository: Path, database: Path) -> None:
    analyze(repository, database)
    run_ast_pass(repository, database)


def _stale(repository: Path, database: Path) -> None:
    """Analyze the six commits, then let the repository gain a seventh.

    The block this state exists for shows what a command says when the snapshot
    is behind: the note on stderr, and the stored history on stdout.
    """
    analyze(repository, database)
    add_commit(repository, "add a health check")


def _separated(repository: Path, database: Path) -> None:
    """The separation demo's state, built the way the demo itself builds it.

    Analyze the six commits, read every version, then let the repository gain a
    commit that edits ``core/app.py``, then analyze again. The last commit has
    no structure until ``ast`` runs a second time, which is what the block
    shows — so this cannot be built by handing the finished repository to
    ``analyze`` once.
    """
    analyze(repository, database)
    run_ast_pass(repository, database)
    _add_health_check(repository)
    analyze(repository, database)


def _add_health_check(repository: Path) -> None:
    """Add the commit the demo's block shows, editing a file the pass reads."""
    _write_file(repository, "core/app.py", APP_AFTER + "\n\ndef health():\n    return True\n")
    _commit(repository, LATER_DATE, "add a health check")


FILE_MEMORY_STATEMENT = (
    "We keep the login helper in one module: the split state machine caused an"
    " incident."
)
PROJECT_MEMORY_STATEMENT = "We do not add runtime dependencies casually."
STALE_MEMORY_STATEMENT = "The health check belonged in core/app.py."
STALE_MEMORY_REASON = "it moved into its own module"


def _with_memories(repository: Path, database: Path) -> None:
    """The state the memory blocks are written against.

    Analyzed, read, and with three memories in it: one about the project with
    nothing attached, one about a file with the commit it was made from, and one
    that has been invalidated — the state a list only shows when it is asked for
    it. The times are given rather than taken, because a block shows them and a
    block that showed the clock would fail on a busy day; the ids cannot be given
    — the tool generates them — so the checker puts those back.
    """
    analyze(repository, database)
    run_ast_pass(repository, database)
    head = git_output(repository, "rev-parse", "HEAD").strip()
    first = git_output(repository, "rev-list", "--max-parents=0", "HEAD").strip()

    connection = connect(database)
    try:
        stale = admit(
            connection,
            repository_path=repository,
            statement=STALE_MEMORY_STATEMENT,
            subject=Subject("path", path="core/app.py"),
            author_name=AUTHOR_NAME,
            author_email=AUTHOR_EMAIL,
            admitted_at=datetime(2024, 6, 1, 7, 0, tzinfo=timezone.utc),
        )
        invalidate(
            connection,
            stale.memory_id,
            repository_path=repository,
            reason=STALE_MEMORY_REASON,
            ended_at=datetime(2024, 6, 1, 7, 30, tzinfo=timezone.utc),
        )
        admit(
            connection,
            repository_path=repository,
            statement=PROJECT_MEMORY_STATEMENT,
            subject=Subject("repository"),
            author_name=AUTHOR_NAME,
            author_email=AUTHOR_EMAIL,
            admitted_at=datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),
        )
        admit(
            connection,
            repository_path=repository,
            statement=FILE_MEMORY_STATEMENT,
            subject=Subject("path", path="core/app.py"),
            author_name=AUTHOR_NAME,
            author_email=AUTHOR_EMAIL,
            since_commit_sha=first,
            admitted_at=datetime(2024, 6, 1, 9, 0, tzinfo=timezone.utc),
            citations=[
                Citation("commit", head[:SHORT_SHA_LENGTH]),
                Citation("file", "core/app.py"),
            ],
        )
    finally:
        connection.close()


def _moved(repository: Path, database: Path) -> str:
    """The state a repository that moved leaves, built the way a move happens.

    The checkout is made, analyzed, written to, and then renamed — so the block
    shows a real state rather than a hand-made one — and the evidence is
    re-pointed at the new path afterwards, which is the middle step of the
    recipe. What is left is the state `memory adopt` exists to repair: rows
    naming a path this checkout no longer has.

    Returns that path, so the block that names it can be written the documented
    way like every other path here.
    """
    old = (repository.parent / "old").resolve()
    analyze(repository, database)

    connection = connect(database)
    try:
        for statement, subject, admitted in (
            (
                PROJECT_MEMORY_STATEMENT,
                Subject("repository"),
                datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),
            ),
            (
                FILE_MEMORY_STATEMENT,
                Subject("path", path="core/app.py"),
                datetime(2024, 6, 1, 9, 0, tzinfo=timezone.utc),
            ),
        ):
            admit(
                connection,
                repository_path=old,
                statement=statement,
                subject=subject,
                author_name=AUTHOR_NAME,
                author_email=AUTHOR_EMAIL,
                admitted_at=admitted,
            )
    finally:
        connection.close()

    return str(old)


def _documented_ids(scenario: "Scenario") -> dict[str, str]:
    """The memories a scenario made, paired with the ids the blocks show.

    Read back out of the store rather than handed back by the builder, so a
    scenario stays a function of ``(repository, database)`` like every other
    one, and the ids are the ones the tool actually wrote.
    """
    connection = connect(scenario.database)
    try:
        found = read_memories(connection, scenario.repository, include_ended=True)
    finally:
        connection.close()
    return {
        memory.memory_id: documented
        for memory, documented in zip(found, DOCUMENTED_MEMORY_IDS)
    }


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    return Scenario(tmp_path_factory.mktemp("readme-analyzed"), _analyzed)


@pytest.fixture(scope="module")
def analyzed_and_read(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    return Scenario(tmp_path_factory.mktemp("readme-read"), _analyzed_and_read)


@pytest.fixture(scope="module")
def stale(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    return Scenario(tmp_path_factory.mktemp("readme-stale"), _stale)


@pytest.fixture(scope="module")
def separated(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    return Scenario(tmp_path_factory.mktemp("readme-separated"), _separated)


@pytest.fixture(scope="module")
def broken(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    """The repository whose Python file stops parsing.

    Its own builder, because the block asks about ``broken.py``: a sample
    repository has no such file, and pointing the block at one would have made
    the check pass on a message the command never printed.
    """
    return Scenario(
        tmp_path_factory.mktemp("readme-broken"),
        _analyzed_and_read,
        builder=build_broken_repo,
    )


@pytest.fixture(scope="module")
def memories(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    """The state the memory blocks are written against: two memories kept."""
    return Scenario(tmp_path_factory.mktemp("readme-memories"), _with_memories)


@pytest.fixture(scope="module")
def moved(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    """The state a moved repository leaves: memories naming the old path."""
    return Scenario(tmp_path_factory.mktemp("readme-moved"), _moved)


def _blocks() -> list[tuple[str, str]]:
    """Every console block in both READMEs that runs an ``archaeology`` command.

    Blocks that run something else — the install instructions, the benchmark —
    are left out: this checker replays the tool's own commands, and a block that
    clones a repository or runs a benchmark is not part of that contract.
    """
    found = []
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for index, body in enumerate(BLOCK.findall(text)):
            commands = COMMAND.findall(body)
            if commands and all(
                command.startswith("archaeology ") for command in commands
            ):
                found.append((f"{name}:{index}", body))
    return found


def _commands(body: str) -> list[str]:
    return COMMAND.findall(body)


def _scenario_for(body: str) -> str:
    """Which scenario a block is written against, read off the block itself.

    Routing is by what the block shows rather than by its position: the broken
    block asks about ``broken.py``, the stale one prints the note about an
    analysis that stops short, the demo's one shows a commit with no stored
    structure, and the blocks that read the AST layer need the pass to have run.

    ``ast`` itself is the exception among those: the block shows the pass
    *doing* its work — ``6 parsed, 0 reused`` — so it runs against the state
    before it, or the command would report what it had already read.
    """
    if BROKEN_ARGUMENT in body:
        return "broken"
    if body.startswith("$ archaeology memory adopt"):
        return "moved"
    if body.startswith("$ archaeology memory"):
        return "memories"
    if "stops at" in body:
        return "stale"
    if "no version was stored" in body:
        return "separated"
    if body.startswith("$ archaeology ast"):
        return "analyzed"
    if body.startswith("$ archaeology structure"):
        return "analyzed_and_read"
    if body.startswith("$ archaeology explain"):
        if any(documented in body for documented in DOCUMENTED_MEMORY_IDS):
            # The block that shows the memory section names a memory, so it can
            # only be written against a database that holds one.
            return "memories"
        # The bundle carries the definitions this commit changed, so the block
        # is written against a history the AST pass has already read.
        return "analyzed_and_read"
    return "analyzed"


def _escaped(value: str) -> str:
    """The path as a JSON string's contents, in the tool's own escaping.

    ``ensure_ascii=False`` because that is what every JSON output in this tool
    uses: a path holding non-ASCII characters stays readable rather than turning
    into escapes, and a substitution written the other way would silently miss.
    """
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _squeeze(line: str) -> str:
    """A line with the terminal's business taken out of it.

    Two things are presentation rather than content, and both differ between the
    terminal a block was written on and the one the checker runs: the spaces
    that pad a table column to the available width, and the horizontal rule
    whose length is that width. Runs of spaces collapse to one, and a rule
    collapses to a single character — a line that stopped being a rule, or
    stopped being there at all, still fails.

    Everything else is compared as written, so a changed word, number or key
    still fails. The blocks were written across more than one terminal width,
    which is why this is needed at all; what the block is a contract for is the
    words and the numbers.
    """
    stripped = line.strip()
    if stripped and set(stripped) == {"─"}:
        return "─"
    return " ".join(line.split())


def _normalise(text: str, scenario: Scenario) -> list[str]:
    """The real output, with the machine-specific parts put back to documented ones.

    The fixture's path and the database's name — a digest of that path — are
    replaced with the values the documentation uses. Both appear in text output
    and inside JSON strings, so each form is replaced in both spellings.
    Trailing whitespace goes too, because Rich pads a table to the console width
    and a block cannot know that width.
    """
    real_repository = str(scenario.repository)
    text = text.replace(str(scenario.database), DOCUMENTED_DATABASE)
    for form in (real_repository, real_repository.replace("\\", "/")):
        text = text.replace(form, DOCUMENTED_REPOSITORY)
        text = text.replace(_escaped(form), DOCUMENTED_REPOSITORY)
    if scenario.foreign:
        for form in (scenario.foreign, scenario.foreign.replace("\\", "/")):
            text = text.replace(form, DOCUMENTED_OLD_REPOSITORY)
            text = text.replace(_escaped(form), DOCUMENTED_OLD_REPOSITORY)

    # A path the tool prints by joining the repository to a name — the file an
    # export wrote — carries the machine's separator, and the documented path
    # has none. Normalised after the substitutions, because the join is only
    # visible once the repository half has become the documented one.
    text = text.replace(f"{DOCUMENTED_REPOSITORY}\\", f"{DOCUMENTED_REPOSITORY}/")

    # The memory ids, by value for the ones the fixture made and by shape for
    # the one a block creates — that id does not exist until the command runs.
    for actual, documented in scenario.memory_ids.items():
        text = text.replace(actual, documented)
        text = text.replace(actual[:SHORT_SHA_LENGTH], documented[:SHORT_SHA_LENGTH])

    documented_ids = set(scenario.memory_ids.values()) | {DOCUMENTED_CREATED_ID}
    text = MEMORY_ID.sub(
        lambda match: (
            match.group(0)
            if match.group(0) in documented_ids
            else DOCUMENTED_CREATED_ID
        ),
        text,
    )

    return [line.rstrip() for line in text.splitlines() if line.strip()]


def _expected(body: str) -> list[str]:
    without_commands = COMMAND.sub("", body)
    return [line.rstrip() for line in without_commands.splitlines() if line.strip()]


def _difference(expected: list[str], actual: list[str]) -> str:
    lines = []
    for index in range(max(len(expected), len(actual))):
        want = expected[index] if index < len(expected) else "<missing>"
        got = actual[index] if index < len(actual) else "<missing>"
        if want != got:
            lines.append(f"  line {index}:\n    block: {want!r}\n    real:  {got!r}")
        if len(lines) == 5:
            break
    return "\n".join(lines)


def _id(value) -> str:
    """A short, readable test id: the block's name, not its whole text."""
    if isinstance(value, str) and value.startswith(("README.md:", "README.zh-CN.md:")):
        return value
    return ""


def _matches(expected: list[str], actual: list[str]) -> bool:
    """Whether the real output is what the block shows.

    A block may elide its middle with a line of ``...``: the timeline's
    ``--limit 2`` example writes one where the table rows would be. That is the
    only licence the checker gives — the lines before and after the ellipsis are
    compared as usual, so the header above it and the count below it are still
    held to the real output.
    """
    if "..." not in expected:
        return [_squeeze(line) for line in expected] == [
            _squeeze(line) for line in actual
        ]

    index = expected.index("...")
    before = [_squeeze(line) for line in expected[:index]]
    after = [_squeeze(line) for line in expected[index + 1 :]]
    squeezed = [_squeeze(line) for line in actual]

    if len(squeezed) < len(before) + len(after):
        return False
    return squeezed[: len(before)] == before and squeezed[-len(after) :] == after


@pytest.mark.parametrize("name,body", _blocks(), ids=_id)
def test_every_console_block_matches_the_real_output(
    name, body, analyzed, analyzed_and_read, stale, separated, broken, memories, moved
) -> None:
    scenarios = {
        "analyzed": analyzed,
        "analyzed_and_read": analyzed_and_read,
        "stale": stale,
        "separated": separated,
        "broken": broken,
        "memories": memories,
        "moved": moved,
    }
    scenario = scenarios[_scenario_for(body)]

    commands = _commands(body)
    assert commands, f"{name} has no command line"

    scenario.restore()
    actual = []
    for command in commands:
        completed = scenario.run(command)
        assert completed.returncode == 0, (
            f"{name}: {command!r} exited {completed.returncode}\n"
            f"scenario={_scenario_for(body)} repo={scenario.repository} db={scenario.database}\n"
            f"{completed.stderr}"
        )
        if "--json" in command:
            # A JSON block that stopped parsing is the drift this catches first.
            json.loads(completed.stdout)
        # stderr first, because that is where a note lands and the blocks show
        # it above the output it warns about.
        actual.extend(_normalise(completed.stderr + completed.stdout, scenario))

    expected = _expected(body)
    assert _matches(expected, actual), (
        f"{name}: the block and the command disagree\n"
        + _difference(expected, actual)
    )


def _registered_commands() -> list[str]:
    """Every command the application registers, by the name the command line uses.

    Groups count, and so does what is inside them. A sub-application's commands
    are invisible to ``registered_commands``, so without the second loop the six
    acts of a `memory` group could ship documented nowhere while this test
    stayed green — which is exactly the drift it was written to catch.
    """
    names = []
    for command in app.registered_commands:
        name = command.name or getattr(command.callback, "__name__", None)
        if name:
            names.append(name.replace("_", "-"))
    for group in app.registered_groups:
        for command in group.typer_instance.registered_commands:
            name = command.name or getattr(command.callback, "__name__", None)
            if name:
                names.append(f"{group.name} {name.replace('_', '-')}")
    return sorted(names)


def test_every_registered_command_is_documented_in_both_readmes() -> None:
    """A command that exists and is not written down is the drift this catches.

    The co-change command sat in exactly that state for a release: built, tested,
    and absent from both READMEs, so a reader could not find out that it existed.
    The block checker replays the commands the READMEs *do* show; this one holds
    the other end — that the set of commands and the set of sections are the same
    set.
    """
    commands = _registered_commands()
    assert len(commands) >= 9, commands

    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        missing = [
            command
            for command in commands
            if not re.search(rf"archaeology {re.escape(command)}(?=\s|$)", text)
        ]
        assert not missing, f"{name} documents no {missing}"



