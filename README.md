# CodeArchaeology

**A time machine for understanding how code evolves.**

[English](README.md) | [简体中文](README.zh-CN.md)

> **Status: v0.1, complete.** The three commands below work today. The project
> is not on PyPI yet, so there is no `pip install` for it.

## Why this project exists

Git already tells you **what** changed, **when**, and **who** changed it. What it
cannot tell you is **why** the code looks the way it does today.

A function that is 150 lines long is not the result of one decision. It is the
result of an original version, a refactor, a feature, a bug, a fix, and another
refactor. `git log` keeps every individual event but loses the story.

CodeArchaeology tries to put the story back.

It reads a local Git repository, extracts the facts (commits, diffs, file
changes, and later AST structure), stores them in SQLite, and reconstructs how
the code travelled from its first commit to its current state.

## Requirements

- Git, available on your `PATH`
- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) to install from a checkout

## Install

CodeArchaeology is not on PyPI yet. To run it from a checkout:

```console
$ git clone https://github.com/Zyvn-coder/CodeArchaeology
$ cd CodeArchaeology
$ uv sync
$ uv run archaeology --version
archaeology 0.1.0
```

## Usage

Every command takes a directory inside the repository, defaulting to the current
directory. None of them ever writes to the repository: the analysis goes into a
database in your cache directory.

### Analyze a repository

`analyze` reads the whole history and stores it.

```console
$ archaeology analyze ~/projects/sample-project
Repository  /home/you/projects/sample-project
Commits     6 (9 file changes)
Range       2024-03-01 to 2024-03-10
HEAD        78c0647a
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

Running it again replaces what was stored, so it is safe to repeat after new
commits, a rebase or an amend. A database written by an older version of the
tool is rebuilt from scratch the same way.

### Show the timeline

`timeline` prints one line per commit, newest first. The message column uses
whatever width the terminal has left.

```console
$ archaeology timeline ~/projects/sample-project
Repository  /home/you/projects/sample-project
Commits     6 (9 file changes)
Range       2024-03-01 to 2024-03-10
HEAD        78c0647a

 SHA        DATE         AUTHOR         FILES          +/-   MESSAGE
 ──────────────────────────────────────────────────────────────────────────────────────────
 78c0647a   2024-03-10   Ada Lovelace       3        +5/-5   Add logo and unicode module, drop legacy helper
 bbed4c45   2024-03-08   Ada Lovelace       0        +0/-0   Merge branch 'feature/caching'
 8eaff71d   2024-03-06   Ada Lovelace       1        +6/-0   Fix login bug
 63d9a636   2024-03-05   Ada Lovelace       1       +12/-0   Add caching
 ed9c175d   2024-03-03   Ada Lovelace       1        +0/-0   Move app module into the core package
 392cc0db   2024-03-01   Ada Lovelace       3       +21/-0   Initial commit
```

`--limit N` shows fewer commits and says how many are hidden. `--all` shows every
commit.

```console
$ archaeology timeline --limit 2
...
4 more commits. Use --all to see them.
```

`--json` prints the same data for other programs to read. The `sha` fields are
full shas, unlike the abbreviated ones in the table.

```console
$ archaeology timeline --json --limit 1
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "commits": [
    {
      "sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
      "date": "2024-03-10",
      "author": "Ada Lovelace",
      "files_changed": 3,
      "insertions": 5,
      "deletions": 5,
      "message": "Add logo and unicode module, drop legacy helper"
    }
  ]
}
```

### When the analysis is behind

`analyze` takes a snapshot. If the repository gains commits afterwards, the
snapshot is still true but no longer complete, so `timeline` says so. The note
goes to stderr, which keeps stdout parseable for `--json`:

```console
$ archaeology timeline --limit 2
Note: this analysis stops at 78c0647a, but HEAD is now a80420c9; run 'archaeology analyze /home/you/projects/sample-project' to refresh it
Repository  /home/you/projects/sample-project
Commits     6 (9 file changes)
Range       2024-03-01 to 2024-03-10
HEAD        78c0647a

 SHA        DATE         AUTHOR         FILES          +/-   MESSAGE
 ──────────────────────────────────────────────────────────────────────────────────────────
 78c0647a   2024-03-10   Ada Lovelace       3        +5/-5   Add logo and unicode module, drop legacy helper
 bbed4c45   2024-03-08   Ada Lovelace       0        +0/-0   Merge branch 'feature/caching'

4 more commits. Use --all to see them.
```

The exit code stays 0: the stored history is readable, it is only behind.

### Inspect one commit

`commit` shows one commit in full. A prefix of the sha is enough, as in git.

```console
$ archaeology commit ed9c175d ~/projects/sample-project
Commit      ed9c175d27ed94fcfc21cebb09b09a5e093e0aa6
Author      Ada Lovelace <ada@example.com>
Authored    2024-03-03 09:00:00 +0000
Committed   2024-03-03 09:00:00 +0000
Parents     392cc0db

Move app module into the core package

 FILE                                                    +/-   CHANGE
 ──────────────────────────────────────────────────────────────────────────
 app.py → core/app.py                                  +0/-0   renamed
```

The whole message is printed, however long it is. `Authored` and `Committed`
differ when a commit was rebased, and both keep the UTC offset of the machine
that made the commit.

A merge commit shows its two parents and no files, because `git log` prints no
diff for a merge:

```console
$ archaeology commit bbed4c45 ~/projects/sample-project
Commit      bbed4c456457f115e0687de6d04580cc8276f20e
Author      Ada Lovelace <ada@example.com>
Authored    2024-03-08 09:00:00 +0000
Committed   2024-03-08 09:00:00 +0000
Parents     8eaff71d 63d9a636

Merge branch 'feature/caching'

No file changes recorded (git prints no diff for a merge commit).
```

### Where the database lives

Databases are named after a digest of the repository's absolute path, so two
checkouts of the same project keep separate ones.

| Platform | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\codearchaeology\` |
| macOS | `~/Library/Caches/codearchaeology/` |
| Linux and others | `$XDG_CACHE_HOME/codearchaeology/`, or `~/.cache/codearchaeology/` |

Set `CODEARCHAEOLOGY_CACHE_DIR` to put them somewhere else, or pass `--db` to any
command to name the file directly.

## Project structure

```
src/codearchaeology/
    cli.py          the Typer application and its three commands
    analysis.py     running an analysis: read the repository, write the database
    cache.py        where analysis databases live
    history.py      running git and parsing its output into Commit objects
    storage.py      the SQLite schema and the queries over it
    timeline.py     the timeline view: rows, table, JSON
    commit.py       the single-commit view
    lifecycle.py    rebuilding each file's life from the stored history
    formatting.py   small helpers shared by the two views
tests/
    sample_repo.py  builds a small deterministic repository for the tests
    conftest.py     the fixture that hands that repository to every test
```

## Principles

These are not aspirations. They constrain what the code is allowed to do.

1. **Core First** — The core cannot depend on an LLM. Every feature must run
   with no AI at all. AI is a layer on top, never the foundation.
2. **Local First** — Any Git repository must be analyzable locally, with no
   remote service involved.
3. **Evidence First** — Every conclusion must be traceable to a specific
   commit, diff, or AST node. No guessing.
4. **Python-only in v0.x** — AST analysis supports Python only until v0.3.
   Multi-language support is on the roadmap, not in scope.
5. **CLI before web** — No web UI before v0.3.
6. **SQLite is the only store** — No PostgreSQL, Redis, or vector databases
   in v0.x.

## Roadmap

| Stage | What it adds | Status |
|---|---|---|
| v0.1 | Git scan, commit history, file changes, SQLite storage, CLI timeline | Done |
| v0.2 | File lifecycle, code hotspots | In development |
| v0.3 | AST analysis, function and class evolution | Planned |
| v0.4 | AI explanations over the evidence layer (pluggable providers) | Planned |
| v0.5 | Developer memory — your own technical usage over time | Planned |
| v0.6 | AI-assisted change analysis and replay | Planned |

One stage at a time. Nothing in a later stage gets designed before the earlier
stage is stable.

## Known limitations

- `analyze` always rescans the whole history. There is no incremental update.
- `--limit` only affects what is printed: the whole history is read from the
  database before it is sliced.
- Merge commits are stored with their parents but with no file changes, because
  `git log` prints no diff for a merge.
- Binary files are stored without line counts and shown as `-`.
- No patch content is read or stored, so the tool never shows the body of a
  diff. Only the changed files and their line counts.
- Rename detection is git's, at its default 50% similarity. Renaming a file
  while most of its content changes makes git report a deletion plus a separate
  addition, which splits that file's life in two. The score git did report is
  stored alongside the rename, so how close a rename came to that threshold
  stays visible after the fact.
- `commit` accepts shas and sha prefixes only, not refs such as `HEAD` or a
  branch name.
- The database is keyed on the repository's absolute path, so moving or renaming
  a repository means analyzing it again.
- Tested on Linux and Windows, on Python 3.11 and 3.13, by CI. macOS is untested.

## License

MIT — see [LICENSE](LICENSE).
