# CodeArchaeology

**A time machine for understanding how code evolves.**

[English](README.md) | [简体中文](README.zh-CN.md)

> **Status: v0.3, complete.** The eight commands below work today: the three
> v0.1 brought, plus `hotspots`, `files` and `file` from v0.2, and `ast` and
> `structure` from v0.3. The project is not on PyPI yet, so there is no
> `pip install` for it.

## Why this project exists

Git already tells you **what** changed, **when**, and **who** changed it. What it
cannot tell you is **why** the code looks the way it does today.

A function that is 150 lines long is not the result of one decision. It is the
result of an original version, a refactor, a feature, a bug, a fix, and another
refactor. `git log` keeps every individual event but loses the story.

CodeArchaeology tries to put the story back.

It reads a local Git repository, extracts the facts (commits, diffs, file
changes, and the structure of every Python file version), stores them in SQLite,
and reconstructs how the code travelled from its first commit to its current
state.

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
archaeology 0.3.0
```

## Usage

Every command takes a directory inside the repository, defaulting to the current
directory. None of them ever writes to the repository: the analysis goes into a
database in your cache directory.

Every command that reads the history takes `--json`, which prints the same facts
in the same names for other programs to read. Only the JSON goes to stdout — the
note about a stale analysis goes to stderr — so the output stays parseable
whether or not the snapshot is current. `ast` is the exception: it writes rather
than reads, and prints a summary of what it did.

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

Running it again refreshes what was stored, so it is safe to repeat after new
commits, a rebase or an amend: a commit git no longer has takes its rows with it,
and everything still true stays. A database written by an older version of the
tool is the one case that is rebuilt from scratch — the structure layer included
— so run `ast` again afterwards.

If the repository is a shallow clone, `analyze` says so on stderr. The oldest
commit such a clone has is treated as the root, so every file in it looks like it
was born there and a merge commit reports changes it never made.

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
 ───────────────────────────────────────────────────────────────────────────────────────────────────────────
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
Note: this analysis stops at 78c0647a, but HEAD is now cd173ca5; run 'archaeology analyze /home/you/projects/sample-project' to refresh it
Repository  /home/you/projects/sample-project
Commits     6 (9 file changes)
Range       2024-03-01 to 2024-03-10
HEAD        78c0647a

 SHA        DATE         AUTHOR         FILES          +/-   MESSAGE
 ───────────────────────────────────────────────────────────────────────────────────────────────────────────
 78c0647a   2024-03-10   Ada Lovelace       3        +5/-5   Add logo and unicode module, drop legacy helper
 bbed4c45   2024-03-08   Ada Lovelace       0        +0/-0   Merge branch 'feature/caching'

4 more commits. Use --all to see them.
```

The exit code stays 0: the stored history is readable, it is only behind.

### Show the files that change most

`hotspots` ranks files by how many commits touched them. A file is counted by
identity rather than by name, so one that was renamed appears once carrying its
whole history instead of once per name it ever had.

```console
$ archaeology hotspots ~/projects/sample-project
Most Active Files

1. core/app.py
   3 commits
   +19 / -0

2. README.md
   1 commit
   +3 / -0

3. assets/logo.png
   1 commit
   +0 / -0

4. core/cache.py
   1 commit
   +12 / -0

5. 工具/文本.py
   1 commit
   +5 / -0

Frequent change is not importance: the reason each of these files is busy is not something this tool can see.
```

`--limit N` shows fewer files and says how many are hidden. `--all` shows every
one. Files that have been deleted are left out, because a hotspot is a place and
a file that is gone is no longer one.

`--json` prints the ranking for other programs to read; the shape is shown under
`files` below, and `files --json` prints exactly the same bytes.

**A hotspot is not a verdict.** The same count can come from core code, from code
that keeps breaking, from requirements that keep moving, from a refactor in
progress, or from a file that is simply edited often. CodeArchaeology cannot tell
those apart, so it does not try: it reports how often a file changed and stops
there.

### List the files

`files` prints the same ranking as a table, one line per file.

```console
$ archaeology files ~/projects/sample-project
 FILE                   COMMITS   +LINES   -LINES
 ────────────────────────────────────────────────
 core/app.py                  3       19        0
 README.md                    1        3        0
 assets/logo.png              1        0        0
 core/cache.py                1       12        0
 工具/文本.py                 1        5        0
```

`--limit N` and `--all` behave as they do for the timeline.

`--json` prints the ranking for other programs to read. Every row carries the
names it was counted over, because a row is counted by identity: without the
chain, a renamed file and two unrelated ones cannot be told apart.

```console
$ archaeology files --json --limit 2 ~/projects/sample-project
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "files": [
    {
      "path": "core/app.py",
      "commits": 3,
      "additions": 19,
      "deletions": 0,
      "path_history": [
        "app.py",
        "core/app.py"
      ]
    },
    {
      "path": "README.md",
      "commits": 1,
      "additions": 3,
      "deletions": 0,
      "path_history": [
        "README.md"
      ]
    }
  ]
}
```

The caveat the `hotspots` command prints is a sentence for a reader, so it is not
in the JSON. The count is the same either way.

### Show one file

`file` shows one file's whole life: the names it carried, when it appeared, when
it was last changed, and what it cost in lines.

```console
$ archaeology file core/app.py ~/projects/sample-project
core/app.py
History:        app.py -> core/app.py

Created:        2024-03-01 09:00:00 +0000  392cc0db
Last modified:  2024-03-06 09:00:00 +0000  8eaff71d

Commits:        3
Modifications:  1
Renames:        1
Additions:      19
Deletions:      0

Net change:     +19
```

The file is found under any name it ever carried, so `file app.py` reaches the
same one as `file core/app.py`. A name that belonged to more than one file —
because it was deleted and created again, or renamed away and taken back later —
prints a block for each of them rather than picking one.

`Last modified` is `-` for a file that was created and never touched: nothing
modified it, so there is no such time to report.

`--json` prints the same facts. It is an object holding a list, even when the
name belongs to a single file, so the shape does not change with the history:

```console
$ archaeology file core/app.py --json ~/projects/sample-project
{
  "path": "core/app.py",
  "files": [
    {
      "path": "core/app.py",
      "commits": 3,
      "additions": 19,
      "deletions": 0,
      "renames": 1,
      "deleted": false,
      "created_at": "2024-03-01T09:00:00+00:00",
      "created_sha": "392cc0db21a7a299e0457555a38c1cfeef7578a3",
      "last_modified_at": "2024-03-06T09:00:00+00:00",
      "last_modified_sha": "8eaff71d34a6f7eb6e27366ca8f72ab22cbba204",
      "deleted_at": null,
      "deleted_sha": null,
      "modifications": 1,
      "binary_changes": 0,
      "path_history": [
        "app.py",
        "core/app.py"
      ],
      "net_change": 19
    }
  ]
}
```

`path` at the top is the name that was asked for; each entry's `path` is what
that file is called now. They differ exactly when the file was renamed. A time
the file never had is `null`, not a substitute — a file that was created and
never edited has no `last_modified_at`, and a file that is still there has no
`deleted_at`. Timestamps keep the UTC offset of the machine that made the commit,
because a commit's time means nothing without it.

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

### Read the Python structure

`ast` reads every Python file version in the stored history and records what each
one held: its classes, functions and methods, with their line ranges, decorators
and qualified names. It is the expensive half of the tool — measured, reading and
parsing a hundred thousand commits takes minutes where `analyze` takes seconds —
which is why it is a command of its own rather than part of `analyze`.

```console
$ archaeology ast ~/projects/sample-project
Repository   /home/you/projects/sample-project
Versions     6 file versions, 13 definitions
Parsed       6 parsed, 0 reused, 0 could not be parsed
Database     /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

`Parsed` counts this run: a version that was already read is reused rather than
parsed again, and a version nobody could parse is counted in the third number
rather than dropped quietly. Only Python files are read; every other file in the
history is left out.

### What the structure layer is

Five rules decide everything the commands below print, so they are worth reading
before the output — the output is shaped by them.

1. **The snapshot is the fact.** `definition_versions` holds the definitions that
   actually existed in each file version that could be read. It is a snapshot,
   not a change log: a definition that did not change is stored again in the next
   version, and `unchanged` is a fact about that version rather than an absence
   of news.
2. **A deletion is derived, never stored.** `change_type` may only be `created`,
   `modified` or `unchanged`. A definition that is gone has no row at all, and
   the deletion is derived by the query layer, from the snapshot the definition
   was in and the next snapshot that was read.
3. **A parse failure is uncertainty, not a deletion.** A version nobody could
   read is recorded as unreadable, with the parser's own words, and gets no
   definitions. Nothing is concluded from that absence: a definition whose file
   went dark is not reported as deleted, and the command says the file could not
   be read instead of printing a listing of zero definitions.
4. **`change_type` is a cached comparison, not a semantic change log.** The three
   words say how a definition relates to the previous snapshot that could be
   read. `modified` means its structure differs, not that its meaning changed,
   and `unchanged` can span a version that could not be read — which is why the
   version each row was compared with is named beside it.
5. **`analyze` and `ast` are separate passes.** `analyze` writes the Git facts
   and stays cheap; `ast` writes the Python structure and is the expensive one.
   Neither touches the other's tables.

### Show a file's structure

```console
$ archaeology structure core/app.py ~/projects/sample-project
core/app.py
History:     app.py -> core/app.py
Version:     2024-03-06 09:00:00 +0000  8eaff71d
Definitions: 3 (3 functions)

 KIND             DEFINITION             LINES   DECORATORS           CHANGE
 ──────────────────────────────────────────────────────────────────────────────
 function         login                    4-7                        modified
 function         logout                 10-11                        created
 function         main                   14-15                        unchanged
```

The version shown is the file's latest stored one; `--commit <prefix>` shows the
file as it was at any commit that changed it, and a file that was renamed is
shown under the name it has now, with the names it carried above it.

`LINES` is where the definition sat in that version. `CHANGE` is the comparison
from rule 4 above: `main` moved from line 8 to line 14 and is `unchanged`, because
what it is did not change, only where it sits. A method is a function whose
enclosing scope is a class in the same file, which the qualified name already
says:

```console
$ archaeology structure core/cache.py ~/projects/sample-project
core/cache.py
Version:     2024-03-05 09:00:00 +0000  63d9a636
Definitions: 4 (1 class, 3 functions, 3 of them methods)

 KIND             DEFINITION             LINES   DECORATORS           CHANGE
 ──────────────────────────────────────────────────────────────────────────────
 class            Cache                   4-12                        created
 function         Cache.__init__           5-6                        created
 function         Cache.get                8-9                        created
 function         Cache.set              11-12                        created
```

`--json` prints the same version for other programs to read. The state of the
version is spelled out rather than left to be inferred from an empty list, and
the version the change types were compared with is named:

```console
$ archaeology structure core/app.py --json ~/projects/sample-project
{
  "path": "core/app.py",
  "path_history": [
    "app.py",
    "core/app.py"
  ],
  "commit_sha": "8eaff71d34a6f7eb6e27366ca8f72ab22cbba204",
  "committed_at": "2024-03-06T09:00:00+00:00",
  "state": "read",
  "reason": null,
  "parse_error": null,
  "error_lineno": null,
  "error_offset": null,
  "compared_with": "ed9c175d27ed94fcfc21cebb09b09a5e093e0aa6",
  "blind_spots": [],
  "definitions": [
    {
      "qualname": "login",
      "kind": "function",
      "lineno": 4,
      "end_lineno": 7,
      "decorators": [],
      "change_type": "modified"
    },
    {
      "qualname": "logout",
      "kind": "function",
      "lineno": 10,
      "end_lineno": 11,
      "decorators": [],
      "change_type": "created"
    },
    {
      "qualname": "main",
      "kind": "function",
      "lineno": 14,
      "end_lineno": 15,
      "decorators": [],
      "change_type": "unchanged"
    }
  ]
}
```

### Show how the definitions changed

```console
$ archaeology structure core/app.py --history ~/projects/sample-project
core/app.py
History:     app.py -> core/app.py
Versions:    3 read, 0 could not be read

login  function
  created  2024-03-01 09:00:00 +0000  392cc0db  line 4
  modified 2024-03-06 09:00:00 +0000  8eaff71d  line 4

main  function
  created  2024-03-01 09:00:00 +0000  392cc0db  line 8

logout  function
  created  2024-03-06 09:00:00 +0000  8eaff71d  line 10
```

`unchanged` does not appear here. A history is the moments a definition changed,
and a version where nothing happened to it is not one of them — the snapshot
still holds it, which is why the listing above shows `main` as `unchanged`.

A deletion is derived, not read out of a row. `legacy.py` was dropped in the last
commit:

```console
$ archaeology structure legacy.py --history ~/projects/sample-project
legacy.py
Versions:    1 read, 1 could not be read
  78c0647a  the file was not in this commit

parse  function
  created  2024-03-01 09:00:00 +0000  392cc0db  line 4
  deleted  2024-03-10 09:00:00 +0000  78c0647a
```

The database has no row saying `deleted`, and could not have: the schema refuses
the word. The commit that dropped the file is a version of it like any other —
one in which the file was not in the tree — and the deletion is derived from the
snapshots either side of it. `--history --json` carries the same facts, with each
event's `previous_commit_sha` and the `blind_spots` it was derived across.

A file nobody could read is uncertainty, not an ending. This second repository
has `broken.py` fine in one commit and a syntax error in the next:

```console
$ archaeology structure broken.py ~/projects/broken-project
broken.py
Version:     2024-03-02 09:00:00 +0000  88003169

AST analysis unavailable: parse failed
  SyntaxError: invalid syntax (line 5, column 12)
```

```console
$ archaeology structure broken.py --history ~/projects/broken-project
broken.py
Versions:    1 read, 1 could not be read
  88003169  SyntaxError: invalid syntax

ok  function
  created  2024-03-01 09:00:00 +0000  a0e51834  line 1
      no ending: 88003169 could not be read, so whether it is still there is not known
```

The parse failed, so nothing is known about what the file held — not even whether
`ok` was still in it. The tool says that, rather than reporting a deletion it
cannot see or printing "0 definitions", which would read as a file that held
none. The words after `SyntaxError:` are the parser's own, recorded together with
the version of Python that said them.

`structure` reads Python only. A path that does not end in `.py`, a path the
stored history never touched, and `--history` given together with `--commit` are
each refused with a message rather than answered with something plausible.

### Re-running `analyze` does not throw the structure away

`analyze` and `ast` write different tables, and `analyze` removes only what git no
longer has: a rebase or an amend takes its commits, and the file versions and
definitions that belonged to them, out of the database. Everything still true
stays. A commit that is new to the database is the exception — it has no
structure until `ast` runs again, and `structure` says which command to run:

```console
$ archaeology analyze ~/projects/sample-project
Repository  /home/you/projects/sample-project
Commits     7 (10 file changes)
Range       2024-03-01 to 2024-03-12
HEAD        cd173ca5
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
$ archaeology structure core/app.py ~/projects/sample-project
core/app.py
History:     app.py -> core/app.py
Version:     2024-03-12 09:00:00 +0000  cd173ca5

AST analysis unavailable: no version was stored for this commit
  run 'archaeology ast' to read the file versions git holds
$ archaeology ast ~/projects/sample-project
Repository   /home/you/projects/sample-project
Versions     7 file versions, 17 definitions
Parsed       1 parsed, 6 reused, 0 could not be parsed
Database     /home/you/.cache/codearchaeology/82d48b376e387fde.db
```

The second `ast` parsed the one new version and reused the six that were already
read — the six `analyze` left alone. Had `analyze` thrown the structure away, that
line would read `7 parsed, 0 reused`. A schema rebuild is the one thing that does
clear it, and that is a different database being made rather than a rescan of the
same one.

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

## Performance

`benchmarks/benchmark.py` measures the four operations the tool is built around,
at whatever scale you ask for. It is run by hand, not by CI, because a test that
asserts a wall-clock time fails whenever the machine is busy and teaches people
to re-run it.

```console
$ uv run python benchmarks/benchmark.py --commits 20000 100000 200000
```

On one machine, over a generated history, taking the fastest of three runs:

| Commits | File changes | Repository | Database | `analyze` | `timeline` | `hotspots` | `file <path>` | `file <path>` by name only |
|---|---|---|---|---|---|---|---|---|
| 20,000 | 100,000 | 15 MB | 25 MB | 3.4s | 0.6s | 0.8s | 0.8s | 0.06s |
| 100,000 | 500,000 | 77 MB | 124 MB | 19.7s | 4.0s | 6.5s | 6.4s | 0.30s |
| 200,000 | 1,000,000 | 154 MB | 248 MB | 45.6s | 8.5s | 13.8s | 13.8s | 0.64s |

**The history is generated, so read the curve and not the numbers.** Files are
created and then edited in place, one line at a time. A real repository has
larger diffs, more renames, and a longer tail of file sizes, and all three move
these figures.

What the curve says:

- **Nothing is quadratic.** From 100,000 commits to 200,000 the cost of every
  operation roughly doubles. Between 20,000 and 100,000 it rises faster than
  fivefold, which is where the working set stops fitting in cache rather than
  where the algorithm changes shape.
- **`analyze` dominates, and it is paid once.** Reading the history out of git
  and writing it to SQLite costs more than every query put together. Everything
  after it reads the database instead.
- **`hotspots` and `file <path>` cost the same**, because a file's life can only
  be rebuilt by walking the whole commit graph, and both of them need one. This
  is not a coincidence to be tuned away; it is the shape of the model.
- **Asking by name is one to two orders of magnitude cheaper** than asking by
  identity, because it is a query rather than a walk. It is also a different
  question: it answers for a path as written, not for the file across its
  renames.

### The structure layer, measured separately

The `ast` pass is the expensive half of the tool, so the benchmark times it in
phases rather than as one total: reading the contents out of git, parsing them,
walking each definition into a fingerprint, comparing versions, and inserting.
Same fixture, Python 3.13, the pass run once on a fresh database:

| Commits | File versions | Definitions | Database (Git) | Database (+ AST) | `ast` pass |
|---|---|---|---|---|---|
| 1,000 | 5,000 | 27,000 | 1.3 MB | 10.1 MB | 3.0s |
| 10,000 | 50,000 | 270,000 | 12.4 MB | 100.4 MB | 35.6s |
| 100,000 | 500,000 | 2,700,000 | 124.9 MB | 1008.0 MB | 405.3s |

- **Every phase is linear in the number of file versions except the insert.** Ten
  times the history costs ten times the reading, the parsing, the walking and the
  comparing. The insert slows from about 52,000 rows a second to about 20,000 as
  the database grows past the page cache; the total stays linear anyway.
- **The walk over the tree costs more than the parse that comes before it.**
  Rendering each definition and hashing it costs about three and a half times the
  parsing of the file it came from, at every scale.
- **A snapshot stores 2.78 to 2.99 rows for every row a change log would keep**
  over this fixture. The ratio is a property of how much churn a history has —
  this repository's was 2.15 — so the benchmark prints both counts rather than a
  growth factor.
- **The structure layer costs about eight times the Git facts to store**, for a
  version holding six definitions. At a hundred thousand commits, 125 MB of Git
  facts become a one-gigabyte database. That is the figure to plan with instead
  of v0.2's database size, and it moves with the number of definitions per file.
- **A second pass costs almost as much as the first** — 374s against 405s at a
  hundred thousand commits. It parses nothing and reuses every version, but it
  still reads each one back and compares it: the saving is the parse and the
  walk, and what it pays instead is one query per version.

The pass reports its own success rate, `parsed 90000, failed 10000` at twenty
thousand commits — 10.0%, which is the fixture's one-file-in-ten — and every row
carries the interpreter that read it.

## Project structure

```
src/codearchaeology/
    cli.py          the Typer application and its eight commands
    analysis.py     running an analysis: read the repository, write the database
    ast_pass.py     the AST pass: read every Python file version, store its structure
    cache.py        where analysis databases live
    history.py      running git and parsing its output into Commit objects
    objects.py      reading file contents out of git, many at a time
    definitions.py  one file version's definitions, read from its bytes
    definition_history.py  deriving each definition's life from the snapshots
    structure.py    the structure view: one version, one history, and their JSON
    storage.py      the SQLite schema and the queries over it
    timeline.py     the timeline view: rows, table, JSON
    commit.py       the single-commit view
    file.py         the single-file view: block and JSON
    lifecycle.py    rebuilding each file's life from the stored history
    statistics.py   the numbers that summarise one file's life
    hotspots.py     ranking files by how often they change, and its two views
    relationships.py  commits and files, read from either end
    formatting.py   small helpers shared by the two views
tests/
    sample_repo.py  builds a small deterministic repository for the tests
    conftest.py     the fixture that hands that repository to every test
benchmarks/
    benchmark.py    the Git operations and the AST pass, at whatever scale you ask
```

## Principles

These are not aspirations. They constrain what the code is allowed to do.

1. **Core First** — The core cannot depend on an LLM. Every feature must run
   with no AI at all. AI is a layer on top, never the foundation.
2. **Local First** — Any Git repository must be analyzable locally, with no
   remote service involved.
3. **Evidence First** — Every conclusion must be traceable to a specific
   commit, diff, or AST node. No guessing.
4. **Python-only in v0.x** — AST analysis reads Python. Multi-language support is
   on the roadmap, not in scope.
5. **CLI before web** — The command line is the interface in v0.x. There is no
   web UI.
6. **SQLite is the only store** — No PostgreSQL, Redis, or vector databases
   in v0.x.

## Roadmap

| Stage | What it adds | Status |
|---|---|---|
| v0.1 | Git scan, commit history, file changes, SQLite storage, CLI timeline | Done |
| v0.2 | File lifecycle, code hotspots, and a JSON output for other programs | Done |
| v0.3 | AST analysis, function and class evolution | Done |
| v0.4 | AI explanations over the evidence layer (pluggable providers) | Planned |
| v0.5 | Developer memory — your own technical usage over time | Planned |
| v0.6 | AI-assisted change analysis and replay | Planned |

One stage at a time. Nothing in a later stage gets designed before the earlier
stage is stable.

## Known limitations

- `analyze` always rescans the whole history, and neither command is incremental:
  the AST pass walks every Python file version every time it runs.
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
  stays visible after the fact. One consequence reaches the numbers: the
  additions and deletions a file is credited with depend on that decision, while
  the net change does not. A file whose renames were missed is also scattered
  across several rows, so it can fall off a ranking it belongs on.
- A shallow clone is not a shorter history, it is a differently shaped one. Git
  treats the oldest commit it has as the root, so every file in it looks like it
  was born there and a merge commit reports changes it never made. `analyze`
  warns about this on stderr.
- `commit` accepts shas and sha prefixes only, not refs such as `HEAD` or a
  branch name.
- The database is keyed on the repository's absolute path, so moving or renaming
  a repository means analyzing it again.
- The structure layer reads Python only, and a definition is identified by its
  qualified name inside one file. A function that was renamed is a death and a
  birth rather than one definition that changed, and a definition that moved to
  another file is not linked to the one it left. Both are deliberate: linking
  them would be an inference the evidence does not support.
- A file version that could not be parsed is stored as unreadable, with the
  parser's words, and never as a file that held nothing. Its definitions are
  unknown until a version that parses, and none of them is reported as deleted on
  the strength of the failure.
- A second `ast` run parses nothing and reuses every version it already read, but
  still reads each version back and compares it, so it costs nearly as much as
  the first: 374s against 405s at a hundred thousand commits.
- Every stored row carries the version of Python that read it, so a database
  written by 3.11 and read by 3.13 has those versions read again rather than the
  two interpreters' results being mixed.
- A definition is compared by its own structure, not by where it sits, so a
  function that only moved down its file is `unchanged`.
- Tested on Linux and Windows, on Python 3.11 and 3.13, by CI. macOS is untested.

## License

MIT — see [LICENSE](LICENSE).
