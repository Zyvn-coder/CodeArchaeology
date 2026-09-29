"""Command line entry point for CodeArchaeology.

Eight commands: ``analyze`` fills the database with the git history and ``ast``
fills it with the structure of every Python file version in that history;
``timeline``, ``hotspots``, ``files``, ``file``, ``commit`` and ``structure``
read it back.
"""

from pathlib import Path

import typer
from rich.console import Console

from codearchaeology import __version__
from codearchaeology.analysis import (
    AnalysisError,
    shallow_clone_note,
    stale_analysis_note,
    stored_head_sha,
)
from codearchaeology.analysis import analyze as run_analysis
from codearchaeology.ast_pass import PYTHON_SUFFIX, run_ast_pass
from codearchaeology.cache import database_path
from codearchaeology.commit import CommitNotFound, build_file_table, load_commit
from codearchaeology.definition_history import load_histories
from codearchaeology.file import build_block, build_json as build_file_json
from codearchaeology.file import find_files, normalise
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.history import GitError, find_repository_root
from codearchaeology.hotspots import build_json as build_ranking_json
from codearchaeology.hotspots import build_table as build_files_table
from codearchaeology.hotspots import rank_hotspots
from codearchaeology.lifecycle import load_lifecycles
from codearchaeology.structure import (
    build_history,
    build_history_json,
    build_version,
    build_version_json,
    build_version_table,
    definitions_in,
    nothing_read_note,
    select_version,
)
from codearchaeology.timeline import build_table, load_timeline

app = typer.Typer(
    name="archaeology",
    help="A time machine for understanding how code evolves.",
    no_args_is_help=True,
)

REPOSITORY_ARGUMENT = typer.Argument(
    Path("."),
    help="Directory inside the repository to work on.",
    exists=True,
    file_okay=False,
    resolve_path=True,
)

DATABASE_OPTION = typer.Option(
    None,
    "--db",
    help="Database file to use. Defaults to the user cache directory.",
)


def _print_version_and_exit(value: bool) -> None:
    if value:
        typer.echo(f"archaeology {__version__}")
        raise typer.Exit()


def _repository_and_database(path: Path, database: Path | None) -> tuple[Path, Path]:
    repository_root = find_repository_root(path)
    if repository_root is None:
        raise GitError(f"not a git repository: {path}")
    return repository_root, database or database_path(repository_root)


def _warn_if_behind(repository_root: Path, database: Path) -> str:
    """Say on stderr when the stored analysis stops short of the repository.

    Every command that reads the history makes a claim about completeness, so
    every one of them has to admit when the snapshot it read is behind. stderr
    keeps the output itself unchanged for anything reading it.

    Returns the commit the snapshot stops at, so a caller that also needs it for
    its own output does not open the database a second time for the same value.
    """
    head_sha = stored_head_sha(repository_root, database)
    note = stale_analysis_note(repository_root, head_sha)
    if note:
        typer.echo(note, err=True)
    return head_sha


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the version and exit.",
        callback=_print_version_and_exit,
        is_eager=True,
    ),
) -> None:
    """CodeArchaeology — reconstruct how a Git repository evolved."""


@app.command()
def analyze(
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
) -> None:
    """Read a repository's history and store it in SQLite."""
    try:
        result = run_analysis(path, database)
    except GitError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    # On stderr, so the summary below stays exactly what the README documents.
    note = shallow_clone_note(result.repository_root)
    if note:
        typer.echo(note, err=True)

    typer.echo(f"Repository  {result.repository_root}")
    typer.echo(f"Commits     {result.commits} ({result.file_changes} file changes)")
    typer.echo(
        f"Range       {result.earliest_commit:%Y-%m-%d}"
        f" to {result.latest_commit:%Y-%m-%d}"
    )
    typer.echo(f"HEAD        {result.head_sha[:SHORT_SHA_LENGTH]}")
    typer.echo(f"Database    {result.database}")


@app.command()
def ast(
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
) -> None:
    """Read the structure of every Python file version the history holds."""
    try:
        repository_root, database = _repository_and_database(path, database)
        # ast reads the history the database has, so it has to admit when that
        # history stops short of the repository, exactly as the other reading
        # commands do.
        _warn_if_behind(repository_root, database)
        result = run_ast_pass(repository_root, database)
    except (GitError, AnalysisError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    if result.skipped:
        typer.echo(
            f"Note: {result.skipped} file versions could not be read from git and"
            f" have no structure recorded",
            err=True,
        )

    typer.echo(f"Repository   {result.repository_root}")
    typer.echo(
        f"Versions     {result.file_versions} file versions,"
        f" {result.definitions} definitions"
    )
    typer.echo(
        f"Parsed       {result.parsed} parsed, {result.reused} reused,"
        f" {result.failed} could not be parsed"
    )
    typer.echo(f"Database     {result.database}")


@app.command()
def timeline(
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    limit: int = typer.Option(
        20, "--limit", "-n", min=1, help="How many commits to show."
    ),
    show_all: bool = typer.Option(False, "--all", help="Show every commit."),
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of a table."
    ),
) -> None:
    """Show how the repository evolved, newest commit first."""
    try:
        repository_root, database = _repository_and_database(path, database)
        stored = load_timeline(repository_root, database)
    except (GitError, AnalysisError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    # On stderr, and before the branch below, so that --json keeps stdout
    # parseable while still warning anyone reading the command by eye.
    note = stale_analysis_note(repository_root, stored.head_sha)
    if note:
        typer.echo(note, err=True)

    selected = None if show_all else limit

    if as_json:
        # Nothing else may go to stdout: the output has to stay parseable.
        typer.echo(stored.as_json(selected))
        return

    commits = stored.commits
    typer.echo(f"Repository  {stored.repository_root}")
    typer.echo(
        f"Commits     {len(commits)}"
        f" ({sum(len(commit.changes) for commit in commits)} file changes)"
    )
    typer.echo(
        f"Range       {min(commit.committed_at for commit in commits):%Y-%m-%d}"
        f" to {max(commit.committed_at for commit in commits):%Y-%m-%d}"
    )
    typer.echo(f"HEAD        {stored.head_sha[:SHORT_SHA_LENGTH]}")
    typer.echo()

    rows = stored.rows(selected)
    console = Console()
    console.print(build_table(rows, console.width))

    hidden = len(commits) - len(rows)
    if hidden:
        typer.echo(f"\n{hidden} more commits. Use --all to see them.")


@app.command()
def hotspots(
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    limit: int = typer.Option(
        20, "--limit", "-n", min=1, help="How many files to show."
    ),
    show_all: bool = typer.Option(False, "--all", help="Show every file."),
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of the ranking."
    ),
) -> None:
    """Show the files that change most often."""
    try:
        repository_root, database = _repository_and_database(path, database)
        lives = load_lifecycles(repository_root, database)
    except (GitError, AnalysisError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    head_sha = _warn_if_behind(repository_root, database)

    rows = rank_hotspots(lives)
    selected = rows if show_all else rows[:limit]

    if as_json:
        # Nothing else may go to stdout, including the note below: the output has
        # to stay parseable.
        typer.echo(build_ranking_json(selected, repository_root, head_sha))
        return

    typer.echo("Most Active Files")
    typer.echo()
    for position, row in enumerate(selected, start=1):
        commits = "commit" if row.commits == 1 else "commits"
        typer.echo(f"{position}. {row.current_path}")
        typer.echo(f"   {row.commits} {commits}")
        typer.echo(f"   +{row.additions} / -{row.deletions}")
        typer.echo()

    hidden = len(rows) - len(selected)
    if hidden:
        typer.echo(f"{hidden} more files. Use --all to see them.")
        typer.echo()

    # The count is the whole point of the command, so the command has to say
    # what the count is not.
    typer.echo(
        "Frequent change is not importance: the reason each of these files is"
        " busy is not something this tool can see."
    )


@app.command()
def files(
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    limit: int = typer.Option(
        20, "--limit", "-n", min=1, help="How many files to show."
    ),
    show_all: bool = typer.Option(False, "--all", help="Show every file."),
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of the table."
    ),
) -> None:
    """List the files, most changed first."""
    try:
        repository_root, database = _repository_and_database(path, database)
        lives = load_lifecycles(repository_root, database)
    except (GitError, AnalysisError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    head_sha = _warn_if_behind(repository_root, database)

    rows = rank_hotspots(lives)
    selected = rows if show_all else rows[:limit]

    if as_json:
        typer.echo(build_ranking_json(selected, repository_root, head_sha))
        return

    console = Console()
    console.print(build_files_table(selected, console.width))

    hidden = len(rows) - len(selected)
    if hidden:
        typer.echo(f"\n{hidden} more files. Use --all to see them.")


@app.command()
def file(
    file_path: str = typer.Argument(..., help="File to show, as git writes it."),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of the block."
    ),
) -> None:
    """Show one file's life: its names, its dates and its numbers."""
    try:
        repository_root, database = _repository_and_database(path, database)
        lives = load_lifecycles(repository_root, database)
    except (GitError, AnalysisError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    _warn_if_behind(repository_root, database)

    wanted = normalise(file_path)
    found = find_files(lives, wanted)
    if not found:
        typer.echo(
            f"Error: nothing in the stored history touched {wanted};"
            f" use 'archaeology files' to see what is there",
            err=True,
        )
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(build_file_json(found, wanted))
        return

    # A name can belong to more than one file. All of them are shown, oldest
    # first, because picking one would hide the others.
    for position, lifecycle in enumerate(found):
        if position:
            typer.echo()
        typer.echo(build_block(lifecycle))


@app.command()
def commit(
    sha: str = typer.Argument(..., help="Commit to show. A prefix is enough."),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
) -> None:
    """Show one commit in full, its message and files included."""
    try:
        repository_root, database = _repository_and_database(path, database)
        found = load_commit(repository_root, database, sha)
    except (GitError, AnalysisError, CommitNotFound) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    parents = " ".join(
        parent[:SHORT_SHA_LENGTH] for parent in found.parents
    ) or "(none)"

    typer.echo(f"Commit      {found.sha}")
    typer.echo(f"Author      {found.author_name} <{found.author_email}>")
    typer.echo(f"Authored    {found.authored_at:%Y-%m-%d %H:%M:%S %z}")
    typer.echo(f"Committed   {found.committed_at:%Y-%m-%d %H:%M:%S %z}")
    typer.echo(f"Parents     {parents}")
    typer.echo()
    typer.echo(found.message)
    typer.echo()

    if not found.changes:
        note = "No file changes recorded"
        if found.is_merge:
            note += " (git prints no diff for a merge commit)"
        typer.echo(f"{note}.")
        return

    console = Console()
    console.print(build_file_table(found.changes, console.width))


@app.command()
def structure(
    file_path: str = typer.Argument(..., help="File to show, as git writes it."),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    history: bool = typer.Option(
        False,
        "--history",
        help="Show how each definition in the file changed over its life.",
    ),
    commit: str | None = typer.Option(
        None,
        "--commit",
        help="Show the file as it was at this commit. A prefix is enough."
        " Defaults to the file's latest stored version.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of the block."
    ),
) -> None:
    """Show a Python file's structure, and how its definitions changed."""
    if history and commit is not None:
        typer.echo(
            "Error: --commit picks one version and --history shows all of them;"
            " use one or the other",
            err=True,
        )
        raise typer.Exit(code=1)

    wanted = normalise(file_path)
    if not wanted.endswith(PYTHON_SUFFIX):
        typer.echo(
            f"Error: {wanted} is not a Python file; this version reads Python only",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        repository_root, database = _repository_and_database(path, database)
        histories = load_histories(repository_root, database, wanted)
        if commit is not None:
            chosen = load_commit(repository_root, database, commit)
    except (GitError, AnalysisError, CommitNotFound) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    _warn_if_behind(repository_root, database)

    if not histories:
        typer.echo(
            f"Error: nothing in the stored history touched {wanted};"
            f" use 'archaeology files' to see what is there",
            err=True,
        )
        raise typer.Exit(code=1)

    if history:
        note = nothing_read_note(histories, wanted)
        if note:
            typer.echo(f"Error: {note}", err=True)
            raise typer.Exit(code=1)

        if as_json:
            # Nothing else may go to stdout: the output has to stay parseable.
            typer.echo(build_history_json(histories, wanted))
            return

        # A name can belong to more than one file. All of them are shown, oldest
        # first, because picking one would hide the others.
        for position, found in enumerate(histories):
            if position:
                typer.echo()
            typer.echo(build_history(found))
        return

    selected = select_version(histories, None if commit is None else chosen.sha)
    if selected is None:
        if commit is None:
            typer.echo(f"Error: the stored history has no version of {wanted}", err=True)
        else:
            typer.echo(
                f"Error: the stored history has no version of {wanted} at"
                f" {chosen.sha[:SHORT_SHA_LENGTH]}; a file version is a commit that"
                f" changed the file — 'archaeology structure {wanted} --history'"
                f" lists them",
                err=True,
            )
        raise typer.Exit(code=1)

    selected_history, version = selected
    if as_json:
        typer.echo(build_version_json(selected_history, version))
        return

    typer.echo(build_version(selected_history, version))
    definitions = definitions_in(version)
    if definitions:
        console = Console()
        typer.echo()
        console.print(build_version_table(definitions, console.width))
