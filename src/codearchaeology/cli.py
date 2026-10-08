"""Command line entry point for CodeArchaeology.

Ten commands: ``analyze`` fills the database with the git history and ``ast``
fills it with the structure of every Python file version in that history;
``timeline``, ``hotspots``, ``files``, ``file``, ``commit``, ``structure`` and
``cochange`` read it back; and ``explain`` reads it back through a model, when
one is configured.

Seven more hang off ``memory`` — ``create``, ``list``, ``show``, ``supersede``,
``invalidate``, ``adopt`` and ``export`` — and they are the one part of the tool
that is not derived from the repository. ``export`` is the only command that
writes a file a person named, and the only one that could replace something the
tool did not write, which is why it refuses an existing path unless told to.
"""

import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from codearchaeology import __version__
from codearchaeology.analysis import (
    AnalysisError,
    open_analysis,
    shallow_clone_note,
    stale_analysis_note,
    stored_head_sha,
)
from codearchaeology.analysis import analyze as run_analysis
from codearchaeology.ast_pass import PYTHON_SUFFIX, run_ast_pass
from codearchaeology.cache import database_path
from codearchaeology.cochange import (
    DEFAULT_LARGE_COMMIT_LIMIT,
    DEFAULT_MIN_SHARED,
    analyze_cochange,
)
from codearchaeology.cochange import build_block as build_cochange_block
from codearchaeology.cochange import build_json as build_cochange_json
from codearchaeology.cochange import build_table as build_cochange_table
from codearchaeology.cochange import load_cochange_commits
from codearchaeology.commit import CommitNotFound, build_file_table, load_commit
from codearchaeology.context import build_context
from codearchaeology.context import build_json as build_context_json
from codearchaeology.definition_history import load_histories
from codearchaeology.explanation import build_json as build_explanation_json
from codearchaeology.explanation import build_prompt, render
from codearchaeology.file import build_block, build_json as build_file_json
from codearchaeology.file import find_files, normalise
from codearchaeology.formatting import SHORT_SHA_LENGTH
from codearchaeology.history import GitError, find_repository_root, read_identity
from codearchaeology.memory import (
    Citation,
    MemoryNotFound,
    MemoryStoreError,
    Subject,
    admit,
    adopt as adopt_memory,
    count_memories,
    find_memory,
    foreign_memories,
    invalidate as invalidate_memory,
    read_memories,
    repository_identity,
    supersede as supersede_memory,
)
from codearchaeology.memory_export import (
    MemoryExportError,
    build_document as build_export_document,
    write_document as write_export_document,
)
from codearchaeology.memory_checks import (
    Evidence,
    resolutions,
    resolve_citations,
    resolve_since_commit,
    resolve_subject,
    since_date_of,
    subject_resolution,
)
from codearchaeology.memory_section import (
    build_section,
    context_json,
    prompt_section,
    related_ids,
    related_notes,
    render_section,
    section_object,
)
from codearchaeology.memory_view import (
    Shown,
    build_list,
    build_list_json,
    build_show,
    build_show_json,
    shown,
    subject_line,
)
from codearchaeology.hotspots import DELETED_FILES_NOTE, build_inventory
from codearchaeology.hotspots import build_inventory_json as build_files_json
from codearchaeology.hotspots import build_inventory_table as build_files_table
from codearchaeology.hotspots import build_json as build_hotspots_json
from codearchaeology.hotspots import rank_hotspots
from codearchaeology.lifecycle import load_lifecycles
from codearchaeology.provider import (
    BASE_URL_VARIABLE,
    MODEL_VARIABLE,
    OpenAICompatibleProvider,
    ProviderError,
    Settings,
)
from codearchaeology.selection import build_view
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
from codearchaeology.validation import ExplanationError, validate

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


def _speak_utf8_outside_a_terminal() -> None:
    """Write UTF-8 to an output stream that is not a terminal.

    Python already speaks UTF-8 to a Windows console, but a pipe or a file
    carries the machine's ANSI code page instead — cp1252 on a default Windows
    install — and this tool's output is not written in cp1252: a renamed file
    is printed as ``old → new``, a repository may hold a path like
    ``工具/文本.py``, and rich draws its tables with box characters. Rich
    degrades the boxes to ASCII when the stream cannot carry them, which is
    honest, but a path cannot be degraded, and the command dies with
    UnicodeEncodeError instead — which is what the first Windows CI run of the
    README checker found, in nine blocks and one exit code.

    A console is left alone: Python's console layer is already UTF-8, and rich
    knows how to draw on the consoles that are not.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream.isatty():
                continue
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            # A stream that cannot be reconfigured is no reason to refuse to
            # run the command; it keeps the encoding it has.
            pass


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
    _speak_utf8_outside_a_terminal()


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

    mixed = _foreign_memory_note(result.repository_root, result.database)
    if mixed:
        typer.echo(mixed, err=True)

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
        typer.echo(build_hotspots_json(selected, repository_root, head_sha))
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
    """List every file the history contains, deleted ones included."""
    try:
        repository_root, database = _repository_and_database(path, database)
        lives = load_lifecycles(repository_root, database)
    except (GitError, AnalysisError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    head_sha = _warn_if_behind(repository_root, database)

    rows = build_inventory(lives)
    selected = rows if show_all else rows[:limit]

    if as_json:
        # The whole inventory, not the slice: --limit is a terminal convenience,
        # and a program that asked for the inventory asked for all of it.
        typer.echo(build_files_json(rows, repository_root, head_sha))
        return

    console = Console()
    console.print(build_files_table(selected, console.width))

    hidden = len(rows) - len(selected)
    if hidden:
        typer.echo(f"\n{hidden} more files. Use --all to see them.")

    # A list that shows dead files invites one misreading — that they are gone
    # from the working tree — so the command says which one it is.
    typer.echo()
    typer.echo(DELETED_FILES_NOTE)


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


@app.command()
def cochange(
    file_path: str = typer.Argument(..., help="File to ask about, as git writes it."),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    limit: int = typer.Option(
        20, "--limit", "-n", min=1, help="How many files to show."
    ),
    show_all: bool = typer.Option(False, "--all", help="Show every file."),
    min_shared: int = typer.Option(
        DEFAULT_MIN_SHARED,
        "--min-shared",
        min=1,
        help="Hide pairs that share fewer commits than this.",
    ),
    large_commit_limit: int = typer.Option(
        DEFAULT_LARGE_COMMIT_LIMIT,
        "--large-commit-limit",
        min=1,
        help="Leave commits touching more files than this out of the analysis.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of the block."
    ),
) -> None:
    """Show the files that change in the same commits as this one."""
    try:
        repository_root, database = _repository_and_database(path, database)
        stored = load_cochange_commits(repository_root, database)
    except (GitError, AnalysisError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    head_sha = _warn_if_behind(repository_root, database)

    wanted = normalise(file_path)
    reports = analyze_cochange(
        stored,
        wanted,
        large_commit_limit=large_commit_limit,
        min_shared=min_shared,
    )
    if not reports:
        typer.echo(
            f"Error: nothing in the stored history touched {wanted};"
            f" use 'archaeology files' to see what is there",
            err=True,
        )
        raise typer.Exit(code=1)

    if as_json:
        # Nothing else may go to stdout: the output has to stay parseable.
        typer.echo(build_cochange_json(reports, wanted, repository_root, head_sha))
        return

    console = Console()
    for position, report in enumerate(reports):
        if position:
            typer.echo()

        typer.echo(build_cochange_block(report))

        if not report.co_changes:
            typer.echo()
            typer.echo("No file changed alongside it.")
        else:
            selected = report.co_changes if show_all else report.co_changes[:limit]
            typer.echo()
            console.print(build_cochange_table(selected, console.width))

            hidden = len(report.co_changes) - len(selected)
            if hidden:
                typer.echo(f"{hidden} more files. Use --all to see them.")

        if report.hidden_pairs:
            typer.echo(
                f"{report.hidden_pairs} pairs hidden: fewer than"
                f" {min_shared} shared commits."
            )
        if report.excluded_large_commits:
            typer.echo(
                f"{report.excluded_large_commits} commits of this file left out:"
                f" more than {large_commit_limit} files changed in them."
            )

    typer.echo()
    # The score is the whole point of the command, so the command has to say
    # what it is a share of and what it is not.
    typer.echo(
        "The score is the share of this file's analyzed commits that touched"
        " the other file. Moving together is not a dependency, and a shared"
        " commit is not evidence of one."
    )


NO_PROVIDER_NOTE = (
    "No model is configured, so this is the evidence itself."
    f" Set {BASE_URL_VARIABLE} and {MODEL_VARIABLE} to have one explained."
)


@app.command()
def explain(
    sha: str = typer.Argument(..., help="Commit to explain. A prefix is enough."),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of the block."
    ),
) -> None:
    """Explain what one commit did, from the evidence the tool holds.

    The answer is an interpretation of that evidence and never a record of what
    happened: every claim carries the citations it rests on, the citations are
    checked against the bundle, and the block opens by saying so. With no model
    configured the evidence itself is printed instead, which is the same bundle
    the model would have been shown.
    """
    try:
        repository_root, database = _repository_and_database(path, database)
        context = build_context(repository_root, database, sha)
    except (GitError, AnalysisError, CommitNotFound) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    _warn_if_behind(repository_root, database)
    section = _memory_section(repository_root, database, context)

    settings = Settings.from_environment()

    if settings is None:
        # Core First: with no model the evidence is still the answer, and the
        # command still succeeds. The note goes to stderr so that what a program
        # reads stays the evidence alone.
        typer.echo(NO_PROVIDER_NOTE, err=True)
        typer.echo(
            build_explanation_json(None, context, memory=section_object(section))
            if as_json
            else context_json(context, section)
        )
        return

    # What the model is shown: the bundle itself when it fits, and a stated
    # selection of its largest entries when the commit is too large to send
    # whole. The evidence stays the bundle either way — printed with no model,
    # carried whole by --json, and the thing a citation is read beside.
    view = build_view(context)
    if view.note is not None:
        typer.echo(view.note, err=True)

    provider = OpenAICompatibleProvider(settings)
    prompt = build_prompt(view.json, prompt_section(section))

    try:
        # Checked against the view rather than the bundle: a citation of a row
        # the model never saw is refused, because an answer has to rest on what
        # it was given and not on something that happens to be true elsewhere.
        # The memory section is checked the same way, by id.
        explanation = validate(
            provider.explain(prompt), view.context, related_ids(section)
        )
    except ProviderError as error:
        # The call never produced an answer. Repeating is the provider's own
        # business, and it has already done as much of it as its settings allow.
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)
    except ExplanationError as error:
        # The model spoke and what it said was unusable. That is worth asking
        # once more and no further: a second failure of the same kind is not a
        # transient condition, and every attempt is paid for.
        try:
            explanation = validate(
                provider.explain(prompt), view.context, related_ids(section)
            )
        except (ProviderError, ExplanationError) as second:
            typer.echo(f"Error: {second}", err=True)
            typer.echo(
                "The answer was not shown: a part of it did not check out, and"
                " printing the rest would present it as a whole one.",
                err=True,
            )
            raise typer.Exit(code=1) from None

    if as_json:
        typer.echo(
            build_explanation_json(
                explanation, context, view.selection, section_object(section)
            )
        )
        return

    block = render(explanation)
    if section is not None:
        # After the answer, as its own section, because the two are different
        # things: the answer is a reading of the evidence and this is what people
        # stated, printed from the store and never from the model.
        block = f"{block}\n\n{render_section(section, related_notes(explanation))}"
    typer.echo(block)


# Memory. The acts are Unit 1's five plus Unit 2's adopt; this unit ships the
# three that make a person's loop complete without a model — write one, list
# them, read one back — and the rest are their own units.

memory_app = typer.Typer(
    name="memory",
    help="Statements people made about this project, kept by the tool and never"
    " treated as evidence.",
    no_args_is_help=True,
)

MEMORY_ABOUT_REPOSITORY = typer.Option(
    False, "--about-repository", help="The memory is about the project as a whole."
)
MEMORY_ABOUT_PATH = typer.Option(
    None, "--about-path", help="The file it is about, as git writes it."
)
MEMORY_ABOUT_DEFINITION = typer.Option(
    None,
    "--about-definition",
    help="A definition's qualified name, inside --about-path.",
)
MEMORY_ABOUT_COMMIT = typer.Option(
    None, "--about-commit", help="The commit it is about. A prefix is enough."
)
MEMORY_CITE_COMMIT = typer.Option(
    None, "--cite-commit", help="A commit the memory was made from. Repeatable."
)
MEMORY_CITE_FILE = typer.Option(
    None, "--cite-file", help="A file the memory was made from. Repeatable."
)
MEMORY_CITE_DEFINITION = typer.Option(
    None, "--cite-definition", help="A definition it was made from. Repeatable."
)
MEMORY_CITE_RANGE = typer.Option(
    None,
    "--cite-range",
    help="A changed span, as <path>:<start>-<end>. Needs --cite-commit.",
)
MEMORY_CITE_COCHANGE = typer.Option(
    None,
    "--cite-cochange",
    help='One direction of a pair, as "<path> -> <other path>". Repeatable.',
)
MEMORY_CITE_ABSENCE = typer.Option(
    None, "--cite-absence", help="One of the absence kinds. Repeatable."
)
MEMORY_SINCE_COMMIT = typer.Option(
    None,
    "--since-commit",
    help="The commit the statement has held since. A prefix is enough.",
)
MEMORY_SINCE_DATE = typer.Option(
    None, "--since-date", help="The date the statement has held since, YYYY-MM-DD."
)


@memory_app.command()
def create(
    statement: str = typer.Argument(
        ...,
        help="What a person knows, in their own words. '-' reads it from stdin.",
    ),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    about_repository: bool = MEMORY_ABOUT_REPOSITORY,
    about_path: str | None = MEMORY_ABOUT_PATH,
    about_definition: str | None = MEMORY_ABOUT_DEFINITION,
    about_commit: str | None = MEMORY_ABOUT_COMMIT,
    cite_commit: list[str] | None = MEMORY_CITE_COMMIT,
    cite_file: list[str] | None = MEMORY_CITE_FILE,
    cite_definition: list[str] | None = MEMORY_CITE_DEFINITION,
    cite_range: list[str] | None = MEMORY_CITE_RANGE,
    cite_cochange: list[str] | None = MEMORY_CITE_COCHANGE,
    cite_absence: list[str] | None = MEMORY_CITE_ABSENCE,
    since_commit: str | None = MEMORY_SINCE_COMMIT,
    since_date: str | None = MEMORY_SINCE_DATE,
) -> None:
    """Record something a person knows about this project.

    What is written is a memory: a statement kept beside the evidence and never
    treated as evidence itself. Every citation is held against the stored
    history before anything is written, so a memory cannot be made to point at
    something that is not there — and a memory with no citations is a memory
    that says so.
    """
    statement = _memory_statement(statement)

    try:
        repository_root, database = _repository_and_database(path, database)
        _warn_if_behind(repository_root, database)
        with open_analysis(repository_root, database) as connection:
            evidence = Evidence(connection, repository_root, database)
            subject = resolve_subject(
                evidence,
                _memory_subject(
                    about_repository, about_path, about_definition, about_commit
                ),
            )
            citations = resolve_citations(
                evidence,
                _memory_citations(
                    cite_commit,
                    cite_file,
                    cite_definition,
                    cite_range,
                    cite_cochange,
                    cite_absence,
                ),
            )
            start, day = _memory_since(evidence, since_commit, since_date)
            author_name, author_email = read_identity(repository_root)
            written = admit(
                connection,
                repository_path=repository_root,
                statement=statement,
                subject=subject,
                author_name=author_name,
                author_email=author_email,
                since_commit_sha=start,
                since_date=day,
                citations=citations,
            )
    except (
        GitError,
        AnalysisError,
        MemoryStoreError,
        MemoryNotFound,
        CommitNotFound,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Memory      {written.memory_id}")
    typer.echo(f"Subject     {subject_line(written.subject)}")
    typer.echo(f"Evidence    {_citation_count(len(written.citations))}")
    typer.echo(f"Database    {database}")


@memory_app.command("list")
def list_memories(
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    limit: int = typer.Option(
        20, "--limit", "-n", min=1, help="How many memories to show."
    ),
    show_all: bool = typer.Option(False, "--all", help="Show every memory."),
    include_ended: bool = typer.Option(
        False,
        "--include-ended",
        help="Also show the memories that have been superseded or invalidated.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of the block."
    ),
) -> None:
    """Show the memories kept for this project, newest first."""
    try:
        repository_root, database = _repository_and_database(path, database)
        head_sha = _warn_if_behind(repository_root, database)
        with open_analysis(repository_root, database) as connection:
            evidence = Evidence(connection, repository_root, database)
            foreign = foreign_memories(connection, repository_root)
            # **Only the rows that will be printed are read at all.** A list
            # re-checks the cheap citations and the subject of every memory it
            # shows — a diff and a walk of the history are what `memory show`
            # pays for, and a list that stayed silent about them would read as
            # though everything had been verified — but that is one query per
            # memory, and a block prints twenty of a store that may hold a
            # hundred thousand. So the read stops at the slice and the count
            # says what is behind it. `--json` carries all of them, so it reads
            # all of them.
            every = as_json or show_all
            memories = read_memories(
                connection,
                repository_root,
                include_ended=include_ended,
                limit=None if every else limit,
            )
            total = (
                len(memories)
                if every
                else count_memories(
                    connection, repository_root, include_ended=include_ended
                )
            )
            entries = [_shown(evidence, memory, deep=False) for memory in memories]
    except (GitError, AnalysisError, MemoryStoreError, MemoryNotFound) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    count, paths = foreign
    if count:
        # On stderr, so the block and the JSON stay what they are: a note about
        # the database is not part of the memories it holds. The count is in
        # brackets rather than in the noun, so the sentence is the same sentence
        # whether one memory is hidden or a hundred.
        typer.echo(
            f"Note: this database also holds memories made about"
            f" {', '.join(paths)} ({_memory_count(count)}), and they are not"
            f" shown here",
            err=True,
        )

    if as_json:
        typer.echo(
            build_list_json(
                entries, repository=repository_root, head_sha=head_sha, foreign=foreign
            )
        )
        return

    typer.echo(build_list(entries, hidden=total - len(entries)))


@memory_app.command()
def show(
    memory: str = typer.Argument(
        ..., help="Memory to show. A prefix of its id is enough."
    ),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of the block."
    ),
) -> None:
    """Show one memory in full, its evidence and its lifecycle included."""
    try:
        repository_root, database = _repository_and_database(path, database)
        _warn_if_behind(repository_root, database)
        with open_analysis(repository_root, database) as connection:
            evidence = Evidence(connection, repository_root, database)
            found = find_memory(connection, memory)
            shown = _shown(evidence, found, deep=True)
            successor = _successor(connection, found)
    except (GitError, AnalysisError, MemoryStoreError, MemoryNotFound) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(build_show_json(shown) if as_json else build_show(shown, successor))


@memory_app.command()
def supersede(
    memory: str = typer.Argument(
        ..., help="Memory to replace. A prefix of its id is enough."
    ),
    statement: str = typer.Argument(
        ..., help="What replaces it. '-' reads it from stdin."
    ),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    about_repository: bool = MEMORY_ABOUT_REPOSITORY,
    about_path: str | None = MEMORY_ABOUT_PATH,
    about_definition: str | None = MEMORY_ABOUT_DEFINITION,
    about_commit: str | None = MEMORY_ABOUT_COMMIT,
    cite_commit: list[str] | None = MEMORY_CITE_COMMIT,
    cite_file: list[str] | None = MEMORY_CITE_FILE,
    cite_definition: list[str] | None = MEMORY_CITE_DEFINITION,
    cite_range: list[str] | None = MEMORY_CITE_RANGE,
    cite_cochange: list[str] | None = MEMORY_CITE_COCHANGE,
    cite_absence: list[str] | None = MEMORY_CITE_ABSENCE,
    since_commit: str | None = MEMORY_SINCE_COMMIT,
    since_date: str | None = MEMORY_SINCE_DATE,
) -> None:
    """Replace a memory with a new statement, keeping the one it replaced.

    One act, two rows: the successor is admitted — the same subject rules, the
    same citation rules — and the memory it replaces is closed with the time and
    a link to what replaced it. **Nothing is deleted and nothing is edited.** The
    old statement stays exactly as it was written, because the end of a rule's
    life is what a reader of an old commit wants to find.
    """
    statement = _memory_statement(statement)

    try:
        repository_root, database = _repository_and_database(path, database)
        _warn_if_behind(repository_root, database)
        with open_analysis(repository_root, database) as connection:
            evidence = Evidence(connection, repository_root, database)
            # Found before anything is written, so an id that names nothing or
            # several refuses the act rather than half of it.
            replaced = find_memory(connection, memory)
            subject = resolve_subject(
                evidence,
                _memory_subject(
                    about_repository, about_path, about_definition, about_commit
                ),
            )
            citations = resolve_citations(
                evidence,
                _memory_citations(
                    cite_commit,
                    cite_file,
                    cite_definition,
                    cite_range,
                    cite_cochange,
                    cite_absence,
                ),
            )
            start, day = _memory_since(evidence, since_commit, since_date)
            author_name, author_email = read_identity(repository_root)
            successor = supersede_memory(
                connection,
                replaced.memory_id,
                repository_path=repository_root,
                statement=statement,
                subject=subject,
                author_name=author_name,
                author_email=author_email,
                since_commit_sha=start,
                since_date=day,
                citations=citations,
            )
    except (
        GitError,
        AnalysisError,
        MemoryStoreError,
        MemoryNotFound,
        CommitNotFound,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Memory      {successor.memory_id}")
    typer.echo(f"Supersedes  {replaced.memory_id}")
    typer.echo(f"Subject     {subject_line(successor.subject)}")
    typer.echo(f"Database    {database}")


@memory_app.command()
def invalidate(
    memory: str = typer.Argument(
        ..., help="Memory to end. A prefix of its id is enough."
    ),
    reason: str = typer.Option(
        ..., "--reason", help="Why it ended. Required, and kept with the memory."
    ),
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
) -> None:
    """End a memory, with the reason it ended.

    Not a delete: the row keeps its statement, its citations and the reason, and
    it is what ``memory list --include-ended`` and ``memory show`` print. A rule
    that comes back is a *new* memory, so the record reads "in force, then not,
    then in force again" rather than losing the middle of it.
    """
    try:
        repository_root, database = _repository_and_database(path, database)
        _warn_if_behind(repository_root, database)
        with open_analysis(repository_root, database) as connection:
            ended = invalidate_memory(
                connection,
                memory,
                repository_path=repository_root,
                reason=reason,
            )
    except (
        GitError,
        AnalysisError,
        MemoryStoreError,
        MemoryNotFound,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Memory      {ended.memory_id}")
    typer.echo(f"State       {ended.state}")
    typer.echo(f"Reason      {ended.end_reason}")
    typer.echo(f"Database    {database}")


@memory_app.command()
def adopt(
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    from_path: str = typer.Option(
        ...,
        "--from",
        help="The path the memories were made about, in full. Typing it is the"
        " confirmation.",
    ),
) -> None:
    """Take over the memories made about another path, as this repository's.

    The act for a repository that moved, and the only way out of a database
    whose rows name a path this checkout no longer has. Nothing about a memory
    changes but its repository: the statements, the authors, the times, the
    citations and the states are all kept exactly as they were written.

    **The path is given in full and there is no prompt.** Typing it is the
    confirmation, which is why a typo cannot match and why the tool stays
    scriptable — the same reason nothing else here asks a question.
    """
    try:
        repository_root, database = _repository_and_database(path, database)
        _warn_if_behind(repository_root, database)
        with open_analysis(repository_root, database) as connection:
            moved = adopt_memory(
                connection, from_path=from_path, to_path=repository_root
            )
    except (
        GitError,
        AnalysisError,
        MemoryStoreError,
        MemoryNotFound,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Adopted     {_memory_count(moved)}")
    typer.echo(f"From        {Path(from_path).resolve()}")
    typer.echo(f"To          {repository_root}")
    typer.echo(f"Database    {database}")


@memory_app.command()
def export(
    path: Path = REPOSITORY_ARGUMENT,
    database: Path | None = DATABASE_OPTION,
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the file here instead of to standard output.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Replace the file when it already exists."
    ),
) -> None:
    """Write this project's memories to a file a person can carry.

    **The file is a copy, not a second copy of record.** Editing it changes
    nothing until something imports it, and every other command reads the
    database. It carries what the store holds and none of what the store works
    out — no resolution, no commit date, no reverse lifecycle link — so writing
    it reads no evidence at all: it cannot be misled by a stale analysis, and it
    is what a person can still reach for when the analysis is what is broken.

    With no ``--output`` the document goes to standard output, so it composes
    with a redirect of your own. With ``--output`` it is written and the block
    says what was written. **An existing file is refused** unless ``--force`` is
    given: this tool does not overwrite a file it did not write, and it never
    prompts.
    """
    try:
        repository_root, database = _repository_and_database(path, database)
        with open_analysis(repository_root, database) as connection:
            memories = read_memories(connection, repository_root, include_ended=True)
            foreign = foreign_memories(connection, repository_root)
    except (GitError, AnalysisError, MemoryStoreError, MemoryNotFound) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    # The identity, not the argument: the header has to name the repository the
    # rows name, and a file that disagreed with itself about that would be the
    # mixing the memory layer exists to prevent.
    repository = repository_identity(repository_root)
    document = build_export_document(
        memories, repository=repository, exported_at=datetime.now().astimezone()
    )

    count, paths = foreign
    if count:
        # The same note `memory list` gives, and for the same reason: a file is
        # about one repository, so the others are reported rather than included
        # in silence. On stderr, so that a redirected export stays the document.
        typer.echo(
            f"Note: this database also holds memories made about"
            f" {', '.join(paths)} ({_memory_count(count)}), and they are not"
            f" in this file",
            err=True,
        )

    if output is None:
        _write_export_to_stdout(document)
        return

    target = output.resolve()
    try:
        write_export_document(document, target, replace=force)
    except MemoryExportError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Wrote       {_memory_count(len(memories))}")
    typer.echo(f"To          {target}")
    typer.echo(f"Repository  {repository}")
    typer.echo(f"Database    {database}")


def _write_export_to_stdout(document: str) -> None:
    """The document and nothing else, with the line endings the format froze.

    Written as bytes when standard output is not a terminal, because a Windows
    pipe is exactly where ``\\n`` becomes ``\\r\\n``, and this file's bytes are
    part of its contract: two exports of one store have to be comparable with a
    diff, and a diff of a file that changed endings between machines is noise.

    A terminal is left to :func:`typer.echo`, for the reason
    ``_speak_utf8_outside_a_terminal`` gives — Python's console layer is already
    UTF-8, and how a console draws a line ending is the console's business and
    not the format's. A stream with no buffer, which is what a test double is,
    takes the same path.
    """
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None or sys.stdout.isatty():
        typer.echo(document, nl=False)
        return
    buffer.write(document.encode("utf-8"))
    buffer.flush()


def _foreign_memory_note(repository_root, database) -> str | None:
    """What to say when a database already holds memories about another path.

    The one moment the tool itself can leave knowledge behind: `analyze` pointed
    at a file another repository's memories live in. Nothing is lost — the rows
    stay, and the memory commands report them — but this is the moment a person
    should hear it, rather than discovering it later in `memory list`. It is
    also the middle step of the recipe a moved repository follows, so the note
    names the act that finishes it.

    Read from the command line rather than from ``analysis.py``: the evidence
    layer must not know that memory exists, and this is a note for the person.
    """
    try:
        with open_analysis(repository_root, database) as connection:
            count, paths = foreign_memories(connection, repository_root)
    except (AnalysisError, MemoryStoreError):
        # An unreadable memory store is not this command's business: the memory
        # commands say so themselves, and the analysis has already happened.
        return None

    if not count:
        return None
    return (
        f"Note: this database also holds memories made about"
        f" {', '.join(paths)} ({_memory_count(count)}); they are kept, and"
        f" 'archaeology memory list' reports what is not shown. If the repository"
        f" moved, run 'archaeology memory adopt --from {paths[0]}' to make them"
        f" this one's"
    )


def _memory_count(count: int) -> str:
    return f"{count} memory" if count == 1 else f"{count} memories"


def _memory_statement(typed: str) -> str:
    """The statement as typed, or the whole of stdin when it is ``-``."""
    if typed == "-":
        return sys.stdin.read().rstrip("\n")
    return typed


def _memory_subject(
    about_repository: bool,
    about_path: str | None,
    about_definition: str | None,
    about_commit: str | None,
) -> Subject:
    """The subject the flags name, refused unless exactly one form is given.

    The four forms are the schema's four subject kinds, and the flags are their
    own words: a reader who typed two of them meant one, and a memory that
    guessed which would be a memory filed under the wrong thing.
    """
    if about_definition and not about_path:
        raise MemoryStoreError(
            "a definition is a path and a qualified name; give --about-path too"
        )
    if sum([bool(about_repository), bool(about_path), bool(about_commit)]) != 1:
        raise MemoryStoreError(
            "a memory is about one thing: give exactly one of --about-repository,"
            " --about-path (with --about-definition for a definition in it), or"
            " --about-commit"
        )

    if about_repository:
        return Subject("repository")
    if about_path:
        if about_definition:
            return Subject("definition", path=about_path, qualname=about_definition)
        return Subject("path", path=about_path)
    return Subject("commit", commit_sha=about_commit)


def _memory_since(
    evidence: Evidence, commit_sha: str | None, day: str | None
) -> tuple[str | None, str | None]:
    """The two time flags: the commit resolved, the date passed through.

    A start is the author's claim about the project and it is optional — absent
    means unknown, never "from the beginning" (Unit 2 §6). The commit is
    resolved the way a commit subject is, so what is stored is an address the
    store held and a prefix that names nothing is refused before the act; the
    date's shape, and the rule that a memory has one start and not two, are the
    store's, where the schema's own constraints live.
    """
    if not commit_sha:
        return None, day
    return resolve_since_commit(evidence, commit_sha), day


def _memory_citations(
    cite_commit, cite_file, cite_definition, cite_range, cite_cochange, cite_absence
) -> tuple[Citation, ...]:
    """The citations the flags name. The order is settled when they are stored."""
    found = []
    for kind, values in (
        ("commit", cite_commit),
        ("file", cite_file),
        ("definition", cite_definition),
        ("range", cite_range),
        ("cochange", cite_cochange),
        ("absence", cite_absence),
    ):
        for value in values or ():
            found.append(Citation(kind=kind, ref=value))
    return tuple(found)


def _shown(evidence: Evidence, memory, *, deep: bool) -> Shown:
    """A memory re-checked: its subject, its citations, and its since-commit.

    ``deep`` is the citations' business alone. A range costs a diff and a
    co-change pair costs a walk of the history, so a list leaves those two to
    ``memory show``; the subject is a single indexed query either way, so both
    views check it and neither can print it as though it were still there.
    """
    return shown(evidence, memory, deep=deep)


def _memory_section(repository_root, database, context):
    """The memories related to this commit, or ``None`` when there are none.

    **An unreadable memory store does not stop the evidence being read.** The
    refusal is printed as a note and the command carries on without a section:
    the store's version stamp being from a newer tool is a reason not to read
    memories, and never a reason to refuse an explanation of a commit. Silence
    would be worse than the note — a reader would not know a section was meant
    to be there.
    """
    try:
        with open_analysis(repository_root, database) as connection:
            return build_section(
                Evidence(connection, repository_root, database), context, repository_root
            )
    except MemoryStoreError as error:
        typer.echo(f"Note: {error}", err=True)
        return None


def _successor(connection, memory):
    """The memory that replaced this one, when there is one to read."""
    if memory.superseded_by is None:
        return None
    try:
        return find_memory(connection, memory.superseded_by)
    except MemoryNotFound:
        return None


def _citation_count(count: int) -> str:
    if not count:
        return "none attached"
    return f"{count} citation" if count == 1 else f"{count} citations"


app.add_typer(memory_app, name="memory")
