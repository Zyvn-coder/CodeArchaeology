"""The core does not need a network, and the AI layer is not part of it.

The user's requirement, in their words: *even if AI is completely unavailable, the
reading commands must work; AI is an upper layer and must not pollute the core.*

Two ways of holding that, and both are here because either alone can be satisfied
while the other is broken.

**Structurally.** Every module in the package is read, and the one that may import
a networking module is named. The provider is that one — it is the whole point of
it — and everything else has to be usable on a machine with no network at all.
This is the architecture freeze's §3 promise, held over the package rather than
over one module, and it is the check that fails when a later unit reaches for
`urllib` one layer too high.

**Behaviourally.** The reading commands are run with every way of opening a socket
taken away, and they have to succeed. A structural check can be satisfied by
importing the network lazily; this one cannot. It is also the closest a test gets
to the machine this is for: no network, no key, no endpoint, and the tool still
answers.

The environment is deliberately poisoned in these tests — an endpoint is set to a
host that cannot resolve — so a command that quietly *tried* would fail here rather
than on somebody's laptop.
"""

import ast
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.storage import connect
from sample_repo import build_sample_repo

runner = CliRunner()

SOURCE = Path(__file__).resolve().parent.parent / "src" / "codearchaeology"
PROVIDER = "provider.py"

# The modules that can open a connection. The standard library's, plus the two
# clients a project like this reaches for first.
NETWORK_MODULES = frozenset(
    {
        "urllib",
        "http",
        "socket",
        "ssl",
        "smtplib",
        "ftplib",
        "poplib",
        "imaplib",
        "telnetlib",
        "xmlrpc",
        "webbrowser",
        "requests",
        "httpx",
        "aiohttp",
    }
)

# A host that cannot resolve, so a command that tried to reach it would fail.
POISONED = {
    "CODEARCHAEOLOGY_AI_BASE_URL": "http://no-such-host.invalid/v1",
    "CODEARCHAEOLOGY_AI_MODEL": "a-model",
    "CODEARCHAEOLOGY_AI_API_KEY": "not-a-real-key",
    "CODEARCHAEOLOGY_AI_TIMEOUT": "1",
    "CODEARCHAEOLOGY_AI_MAX_RETRIES": "0",
}


def _imports(path: Path) -> set[str]:
    """The top-level modules *path* imports, read out of its own source."""
    tree = ast.parse(path.read_bytes())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    return imported


def _modules() -> list[Path]:
    return sorted(SOURCE.glob("*.py"))


def test_the_package_is_where_the_test_thinks_it_is():
    """A guard on the guard: a glob that found nothing would pass everything."""
    names = {path.name for path in _modules()}

    assert PROVIDER in names
    assert {"timeline.py", "hotspots.py", "structure.py", "cochange.py"} <= names


def test_only_the_provider_reaches_for_a_network():
    """The check that fails when a later unit imports `urllib` too high up."""
    offenders = {
        path.name: sorted(_imports(path) & NETWORK_MODULES)
        for path in _modules()
        if path.name != PROVIDER and _imports(path) & NETWORK_MODULES
    }

    assert not offenders, f"these modules reach for a network: {offenders}"


def test_the_provider_is_the_one_that_does():
    """The other half, so the check above cannot pass by checking nothing.

    If the provider ever stopped importing a networking module, the test above
    would go on passing while testing an empty set — which is the failure trap 34
    is about, in the one place it would be least visible.
    """
    assert _imports(SOURCE / PROVIDER) & NETWORK_MODULES


def test_the_modules_the_reading_commands_use_are_offline():
    """Named one by one, so a failure says which layer was broken.

    The set is the read path: what a command reads, what it derives from that,
    and what it prints. The AI layer is above all of it and none of these import
    it — `cli.py` is not in the list because it is the one place allowed to know
    that a provider exists, and it only calls one when the environment names one.
    """
    read_path = (
        "timeline.py",
        "hotspots.py",
        "file.py",
        "commit.py",
        "structure.py",
        "cochange.py",
        "lifecycle.py",
        "relationships.py",
        "statistics.py",
        "definition_history.py",
        "definitions.py",
        "ast_pass.py",
        "objects.py",
        "history.py",
        "storage.py",
        "analysis.py",
        "cache.py",
        "formatting.py",
        "context.py",
    )

    for name in read_path:
        path = SOURCE / name
        assert path.is_file(), f"{name} is gone; the list is out of date"
        assert not _imports(path) & NETWORK_MODULES, name


@pytest.fixture(scope="module")
def read_side(tmp_path_factory: pytest.TempPathFactory):
    """The sample fixture, analyzed and read, with a sha to ask about."""
    repository = build_sample_repo(tmp_path_factory.mktemp("offline"))
    database = repository.parent / "offline.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    connection = connect(database)
    try:
        sha = connection.execute(
            "SELECT sha FROM commits WHERE message = ?", ("Fix login bug",)
        ).fetchone()["sha"]
    finally:
        connection.close()

    return repository, database, sha


@pytest.fixture
def no_network(monkeypatch):
    """Take away every way of opening a socket.

    Not a mock and not a firewall: while this is in place nothing in the process
    can resolve a name or open a connection, so a command that reaches for one
    fails here rather than in a user's terminal.

    The failure is an ``OSError`` because that is what an unreachable network
    looks like from inside the standard library — the same thing a machine with
    its network off produces. Raising something else would be testing a failure
    the tool never sees, which is a test that passes without proving the path.
    """

    def refuse(*arguments: object, **keywords: object) -> None:
        raise OSError("the network is not available")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def _command_lines(repository: Path, sha: str) -> dict:
    """Every command that must answer with no network, and its arguments."""
    root = str(repository)
    return {
        "timeline": ["timeline", root],
        "hotspots": ["hotspots", root],
        "files": ["files", root],
        "file": ["file", "core/app.py", root],
        "commit": ["commit", sha[:8], root],
        "structure": ["structure", "core/app.py", root],
        "structure --history": ["structure", "core/app.py", "--history", root],
        "cochange": ["cochange", "core/app.py", root],
    }


@pytest.mark.parametrize(
    "name",
    [
        "timeline",
        "hotspots",
        "files",
        "file",
        "commit",
        "structure",
        "structure --history",
        "cochange",
    ],
)
def test_every_reading_command_works_with_no_network(
    read_side, no_network, name: str
) -> None:
    """The user's requirement, run rather than asserted.

    The environment names an endpoint that cannot resolve, so a command that
    quietly tried to use it would fail — and every one of these has to succeed
    anyway, because none of them knows the AI layer exists.
    """
    repository, database, sha = read_side
    arguments = _command_lines(repository, sha)[name]
    result = runner.invoke(
        app, [*arguments, "--db", str(database)], env=dict(POISONED)
    )

    assert result.exit_code == 0, result.stderr or result.output
    assert result.stdout.strip(), "a command that answers with nothing is not working"


def test_explain_with_an_endpoint_that_cannot_be_reached_fails_cleanly(
    read_side, no_network
) -> None:
    """The one command that is *supposed* to need the network, and what it owes
    a user when it cannot have it.

    ``explain`` is not in the list above on purpose: with an endpoint configured
    it tries to use it, which is the whole point of configuring one. What it must
    not do is fail with a traceback, print half an answer, or exit 0.
    """
    repository, database, sha = read_side

    result = runner.invoke(
        app,
        ["explain", sha[:8], str(repository), "--db", str(database)],
        env=dict(POISONED),
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "could not be reached" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.output


def test_the_ai_variables_are_set_and_ignored(read_side, no_network) -> None:
    """The poisoned environment is real, so the tests above mean something.

    Without this, an endpoint that happened to be unset would make every one of
    them pass for the wrong reason.
    """
    assert POISONED["CODEARCHAEOLOGY_AI_BASE_URL"].endswith(".invalid/v1")


def test_explain_with_no_endpoint_configured_still_answers(read_side, no_network):
    """The offline path, on a machine with nothing: no key, no endpoint, no
    socket, and the evidence is still the answer."""
    repository, database, sha = read_side

    result = runner.invoke(
        app,
        ["explain", sha[:8], str(repository), "--db", str(database)],
        env={},
    )

    assert result.exit_code == 0
    assert result.stdout.lstrip().startswith("{")
