"""Provenance: what a memory points at, held against the evidence.

A memory points at evidence — a commit, a span of its diff, a definition the AST
pass read, a file — or at nothing at all, and **never at another memory**. This
file holds the three things the provenance unit is about: the direction (the
evidence layer never reads memory, and a memory is never evidence for a memory),
the two things that can go missing underneath a memory (its subject and its
citations), and the rule that neither is ever repaired or dropped.

The rules are Unit 1 §7 and §13.2 and Unit 2 §4; the vocabulary is v0.4's,
unchanged, so the whole tool points at evidence in one way.
"""

import ast
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codearchaeology.analysis import analyze
from codearchaeology.ast_pass import run_ast_pass
from codearchaeology.cli import app
from codearchaeology.explanation import EVIDENCE_KINDS
from codearchaeology.memory import (
    CITATION_KINDS,
    Subject,
    prepare_memory,
    read_memories,
)
from codearchaeology.storage import connect
from sample_repo import (
    INITIAL_DATE,
    LATER_DATE,
    _commit,
    _configure,
    _write_file,
    add_commit,
    build_single_commit_repo,
    git_output,
)

runner = CliRunner()

SOURCE = Path(__file__).resolve().parent.parent / "src" / "codearchaeology"

# The memory side, and the command line that wires it to the acts. Everything
# else in the package is the evidence layer, and none of it may reach for these.
# ``memory_section.py`` is on this list and not on the other side of the line:
# it reads the store to build the section ``explain`` carries, so it *is* the
# memory side, and the rule it must not break is the one this test holds — that
# nothing of the evidence layer ever reads it back.
MEMORY_SIDE = (
    "cli.py",
    "memory.py",
    "memory_checks.py",
    "memory_export.py",
    "memory_section.py",
    "memory_view.py",
)


@pytest.fixture
def database(sample_repo: Path, tmp_path: Path) -> Path:
    """An analyzed repository whose structure has been read.

    Per test rather than per module: the acts write, and a shared database would
    let one test read what another left behind.
    """
    path = tmp_path / "history.db"
    analyze(sample_repo, path)
    run_ast_pass(sample_repo, path)
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


def _citation_rows(database: Path) -> list[tuple]:
    """Every citation row as the store holds it, position included."""
    connection = connect(database)
    try:
        return [
            (row["memory_id"], row["position"], row["kind"], row["ref"])
            for row in connection.execute(
                "SELECT * FROM memory_citations ORDER BY memory_id, position"
            )
        ]
    finally:
        connection.close()


def _imports(path: Path) -> set[str]:
    """The modules *path* imports, read out of its own source."""
    tree = ast.parse(path.read_bytes())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


# --- the direction -----------------------------------------------------------


def test_the_evidence_layer_never_reads_memory() -> None:
    """Unit 1 §13.2 rule 1, held the way ``test_offline`` holds the network rule.

    Memory cites evidence, and no evidence module may look back: not a table, not
    a query, not a bundle section, not a selection. The imports are the only form
    this can be checked in, so the check is here — and the two guards below it
    say the scan found the modules it thinks it did and can see the import it is
    looking for.
    """
    modules = sorted(SOURCE.glob("*.py"))
    names = {path.name for path in modules}

    # A guard on the guard: a glob that found nothing would pass everything.
    assert {"context.py", "storage.py", "cochange.py", "explanation.py"} <= names
    assert set(MEMORY_SIDE) <= names

    offenders = {}
    for path in modules:
        if path.name in MEMORY_SIDE:
            continue
        reached = sorted(
            name for name in _imports(path) if name.startswith("codearchaeology.memory")
        )
        if reached:
            offenders[path.name] = reached

    assert not offenders, f"the evidence layer reached for memory: {offenders}"

    # The other half, so the check above cannot pass by looking for nothing: the
    # command line imports every memory module there is, and the scan sees them.
    # Written against the directory rather than a list, so a memory module added
    # later cannot quietly sit outside the check.
    memory_modules = {
        f"codearchaeology.{path.stem}" for path in SOURCE.glob("memory*.py")
    }
    assert memory_modules
    assert {
        name for name in _imports(SOURCE / "cli.py") if name.startswith("codearchaeology.memory")
    } == memory_modules


def test_the_only_edge_between_two_memories_is_the_lifecycle_one(
    database: Path,
) -> None:
    """Memory → memory exists, and it is not a citation.

    Two columns of the whole memory schema can hold a memory id, and the database
    itself says which they are: the owner column of ``memory_citations``, which
    says whose citation a row is, and ``memories.superseded_by``, which is the
    lifecycle link Unit 1 allows. No citation kind names a memory, so there is no
    way to write the chain the phase forbids — a memory borrowing another
    memory's evidence instead of pointing at the evidence itself.
    """
    connection = connect(database)
    try:
        prepare_memory(connection)
        links = connection.execute("PRAGMA foreign_key_list(memories)").fetchall()
        owners = connection.execute("PRAGMA foreign_key_list(memory_citations)").fetchall()
    finally:
        connection.close()

    assert [(row["table"], row["from"]) for row in links] == [("memories", "superseded_by")]
    assert [(row["table"], row["from"]) for row in owners] == [("memories", "memory_id")]
    assert CITATION_KINDS == EVIDENCE_KINDS
    assert "memory" not in CITATION_KINDS


def test_a_memory_id_is_not_evidence_of_any_kind(
    sample_repo: Path, database: Path
) -> None:
    """The prohibition, tried from every direction a person could try it.

    A memory's id is a UUID, and every one of the six kinds is held against the
    evidence before anything is written — so there is no kind under which one
    memory's id means something. Each attempt is refused with the sentence its
    kind already gives, and the store is exactly as it was.
    """
    first = _create(sample_repo, database, "Something a person said.", "--about-repository")
    assert first.exit_code == 0, first.stderr
    memory_id = _stored(database, sample_repo)[0].memory_id

    attempts = (
        ("--cite-commit", memory_id, "is not in the stored history"),
        ("--cite-file", memory_id, "was never touched"),
        ("--cite-definition", memory_id, "no stored version of any file held a definition"),
        ("--cite-range", f"{memory_id}:1-1", "has to cite the commit too"),
        ("--cite-cochange", f"{memory_id} -> other.py", "is not in the co-change analysis"),
        ("--cite-absence", memory_id, "is not one of the absence kinds"),
    )
    for flag, value, sentence in attempts:
        refused = _create(
            sample_repo, database, "Pointing at a memory.", "--about-repository", flag, value
        )
        assert refused.exit_code == 1, (flag, refused.stdout)
        assert sentence in refused.stderr, (flag, refused.stderr)

    assert len(_stored(database, sample_repo)) == 1


def test_a_successor_carries_its_own_evidence_and_inherits_none(
    sample_repo: Path, database: Path
) -> None:
    """The lifecycle edge carries no evidence across it.

    This is the chain the phase forbids, in its most plausible disguise: a
    successor admitted with nothing cited, standing next to the memory it
    replaced, which cited a commit and a file. If a successor inherited them, the
    new statement would look grounded by evidence gathered for the old one — and
    the reader would have no way to tell.
    """
    sha = git_output(sample_repo, "rev-parse", "HEAD").strip()
    _create(
        sample_repo,
        database,
        "The old statement.",
        "--about-path",
        "core/app.py",
        "--cite-commit",
        sha[:8],
        "--cite-file",
        "core/app.py",
    )
    old = _stored(database, sample_repo)[0]
    assert len(old.citations) == 2

    replaced = _run(
        sample_repo,
        database,
        "supersede",
        old.memory_id[:8],
        "The new statement.",
        "--about-path",
        "core/app.py",
    )
    assert replaced.exit_code == 0, replaced.stderr

    successor, replaced_memory = _stored(database, sample_repo)
    assert successor.supersedes == old.memory_id
    assert successor.citations == ()
    assert replaced_memory.citations == old.citations

    shown = _run(sample_repo, database, "show", successor.memory_id)
    assert "Evidence    none attached" in shown.stdout
    document = json.loads(
        _run(sample_repo, database, "show", successor.memory_id, "--json").stdout
    )
    assert document["citations"] == []
    assert document["supersedes"] == old.memory_id


def test_every_relation_a_memory_may_point_at_holds(
    tmp_path: Path,
) -> None:
    """Unit 1's five: a commit, a span of its diff, a definition, a file, and none.

    One memory carries four of them — the commit, the range held against that
    commit's diff, the definition the AST pass read, and the file — and a second
    carries none. All five resolve at admission and again at read, which is what
    makes a relation a live check rather than a stored claim. The spans are known
    by hand: the second commit's diff of ``auth.py`` is ``@@ -2,0 +3 @@``, so its
    one span is 3-3.
    """
    repository = tmp_path / "known"
    repository.mkdir()
    git_output(repository, "init", "--initial-branch", "main")
    _configure(repository)
    _write_file(repository, "auth.py", "one\ntwo\n")
    _write_file(repository, "mod.py", "def login(user):\n    return user == 'admin'\n")
    _commit(repository, INITIAL_DATE, "add auth and the module")
    _write_file(repository, "auth.py", "one\ntwo\nthree\n")
    _commit(repository, LATER_DATE, "add a third line")

    database = tmp_path / "known.db"
    analyze(repository, database)
    run_ast_pass(repository, database)
    sha = git_output(repository, "rev-parse", "HEAD").strip()

    pointed = _create(
        repository,
        database,
        "Made from all four.",
        "--about-path",
        "auth.py",
        "--cite-commit",
        sha[:8],
        "--cite-range",
        "auth.py:3-3",
        "--cite-definition",
        "login",
        "--cite-file",
        "mod.py",
    )
    assert pointed.exit_code == 0, pointed.stderr

    bare = _create(repository, database, "Made from nothing.", "--about-repository")
    assert bare.exit_code == 0, bare.stderr

    document = json.loads(_run(repository, database, "list", "--json").stdout)
    by_subject = {memory["subject"]["kind"]: memory for memory in document["memories"]}
    pointed_memory, bare_memory = by_subject["path"], by_subject["repository"]

    # The list re-checks the cheap kinds and says so about the one it left to
    # `show` — the range — which is the honest half of the same contract.
    listed = {
        citation["kind"]: citation["resolution"]
        for citation in pointed_memory["citations"]
    }
    assert listed == {
        "commit": "resolved",
        "range": "not_checked",
        "definition": "resolved",
        "file": "resolved",
    }

    detailed = json.loads(
        _run(repository, database, "show", pointed_memory["memory_id"], "--json").stdout
    )
    assert {citation["kind"] for citation in detailed["citations"]} == {
        "commit",
        "range",
        "definition",
        "file",
    }
    assert {citation["resolution"] for citation in detailed["citations"]} == {"resolved"}
    assert detailed["subject"]["resolution"] == "resolved"

    assert bare_memory["citations"] == []
    assert "Evidence    none attached" in _run(
        repository, database, "show", bare_memory["memory_id"]
    ).stdout


# --- the subject, when the repository moves underneath it --------------------


def test_a_subject_a_rewrite_removed_is_marked_and_kept(tmp_path: Path) -> None:
    """The memory stays; the read says the thing it is about is not there.

    A commit is dropped, and with it the only history ``extra.txt`` had. Both the
    subject and the citation stop resolving, and **neither is touched**: the
    stored columns are what the person's act wrote. A memory that quietly
    followed the repository would be the tool editing what somebody said.
    """
    repository = build_single_commit_repo(tmp_path / "rewritten")
    add_commit(repository, "adds a file")
    database = tmp_path / "rewritten.db"
    analyze(repository, database)

    created = _create(
        repository,
        database,
        "The extra file is where the flag lives.",
        "--about-path",
        "extra.txt",
        "--cite-file",
        "extra.txt",
    )
    assert created.exit_code == 0, created.stderr
    memory_id = _stored(database, repository)[0].memory_id

    git_output(repository, "reset", "--hard", "HEAD~1")
    analyze(repository, database)

    shown = _run(repository, database, "show", memory_id)
    assert "Subject     extra.txt  (no longer in this history)" in shown.stdout
    assert "file        extra.txt  no longer in this history" in shown.stdout

    listed = _run(repository, database, "list")
    assert "1. extra.txt  (no longer in this history)" in listed.stdout

    document = json.loads(_run(repository, database, "list", "--json").stdout)
    memory = document["memories"][0]
    assert memory["subject"] == {
        "kind": "path",
        "path": "extra.txt",
        "resolution": "unresolved",
    }
    assert memory["citations"] == [
        {"kind": "file", "ref": "extra.txt", "resolution": "unresolved"}
    ]

    # Nothing was repaired: what is stored is what the act accepted.
    stored = _stored(database, repository)[0]
    assert stored.subject == Subject("path", path="extra.txt")
    assert [citation.ref for citation in stored.citations] == ["extra.txt"]


def test_a_commit_subject_is_marked_and_the_project_subject_is_not(
    tmp_path: Path,
) -> None:
    """Two subjects, one rewrite: what pointed at the dropped commit is marked.

    The repository subject is the control, and it is not decoration — it is the
    one subject that cannot stop resolving, because a memory is only ever read
    under the repository it was written about. A check that marked both would be
    as wrong as one that marked neither.
    """
    repository = build_single_commit_repo(tmp_path / "dropped")
    add_commit(repository, "a commit that will be dropped")
    database = tmp_path / "dropped.db"
    analyze(repository, database)
    sha = git_output(repository, "rev-parse", "HEAD").strip()

    _create(repository, database, "About that commit.", "--about-commit", sha[:8])
    _create(repository, database, "About the project.", "--about-repository")

    git_output(repository, "reset", "--hard", "HEAD~1")
    analyze(repository, database)

    document = json.loads(_run(repository, database, "list", "--json").stdout)
    by_subject = {memory["subject"]["kind"]: memory for memory in document["memories"]}
    about_commit, about_project = by_subject["commit"], by_subject["repository"]
    assert about_commit["subject"] == {
        "kind": "commit",
        "commit": sha,
        "resolution": "unresolved",
    }
    assert about_project["subject"] == {"kind": "repository", "resolution": "resolved"}

    listed = _run(repository, database, "list")
    assert f"commit {sha[:8]}  (no longer in this history)" in listed.stdout
    assert "1. the project\n" in listed.stdout


def test_a_definition_subject_is_marked_while_its_file_still_resolves(
    tmp_path: Path,
) -> None:
    """The definition went with its version; the file did not go with it.

    The commit that added ``login`` is dropped, so the only stored version of
    ``app.py`` holding it goes too — while ``app.py`` itself is still in the
    history. One memory about the definition and one citation of the file, in
    the same memory, and the two answers differ, which is the whole point of
    checking them separately.
    """
    repository = tmp_path / "definition"
    repository.mkdir()
    git_output(repository, "init", "--initial-branch", "main")
    _configure(repository)
    _write_file(repository, "app.py", "def helper():\n    return 1\n")
    _commit(repository, INITIAL_DATE, "add the helper")
    _write_file(
        repository,
        "app.py",
        "def helper():\n    return 1\n\n\ndef login(user):\n    return user == 'admin'\n",
    )
    _commit(repository, LATER_DATE, "add login")

    database = tmp_path / "definition.db"
    analyze(repository, database)
    run_ast_pass(repository, database)

    created = _create(
        repository,
        database,
        "Login is the only door in.",
        "--about-path",
        "app.py",
        "--about-definition",
        "login",
        "--cite-file",
        "app.py",
    )
    assert created.exit_code == 0, created.stderr

    git_output(repository, "reset", "--hard", "HEAD~1")
    analyze(repository, database)

    document = json.loads(_run(repository, database, "list", "--json").stdout)
    memory = document["memories"][0]
    assert memory["subject"] == {
        "kind": "definition",
        "path": "app.py",
        "qualname": "login",
        "resolution": "unresolved",
    }
    assert memory["citations"] == [
        {"kind": "file", "ref": "app.py", "resolution": "resolved"}
    ]
    assert "Subject     app.py :: login  (no longer in this history)" in _run(
        repository, database, "show", memory["memory_id"]
    ).stdout


# --- what a rewrite may not take with it -------------------------------------


def test_a_rewrite_keeps_every_citation_row(tmp_path: Path) -> None:
    """A citation that stopped resolving is reported, never deleted.

    Every row is compared before and after the rewrite, position included, so a
    read that dropped the ones it could not resolve would fail here rather than
    in a reader's terminal. What changed is the resolution — a word computed at
    read time — and nothing that was stored.
    """
    repository = build_single_commit_repo(tmp_path / "citations")
    add_commit(repository, "the commit every citation names")
    database = tmp_path / "citations.db"
    analyze(repository, database)
    sha = git_output(repository, "rev-parse", "HEAD").strip()

    created = _create(
        repository,
        database,
        "Made from three things.",
        "--about-path",
        "only.py",
        "--cite-commit",
        sha[:8],
        "--cite-file",
        "extra.txt",
        "--cite-absence",
        "not_in_this_commit",
    )
    assert created.exit_code == 0, created.stderr
    before = _citation_rows(database)
    assert [row[2] for row in before] == ["commit", "file", "absence"]

    git_output(repository, "reset", "--hard", "HEAD~1")
    analyze(repository, database)

    assert _citation_rows(database) == before

    document = json.loads(_run(repository, database, "list", "--json").stdout)
    assert [citation["resolution"] for citation in document["memories"][0]["citations"]] == [
        "unresolved",
        "unresolved",
        "resolved",
    ]
