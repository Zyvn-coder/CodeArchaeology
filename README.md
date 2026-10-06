# CodeArchaeology

**A time machine for understanding how code evolves.**

[English](README.md) | [简体中文](README.zh-CN.md)

> **Status: v0.4.0 is released.** Ten commands work today: the three v0.1 brought,
> plus `hotspots`, `files` and `file` from v0.2, `ast`, `structure` and `cochange`
> from v0.3, and `explain` from v0.4 — the only one that talks to a model. The
> evidence layer of v0.3.x is frozen, and what it is and is not is written down in
> [`docs/v0.3-final-state.md`](docs/v0.3-final-state.md); what v0.4 adds and what
> holds it in place is [`docs/v0.4-final-state.md`](docs/v0.4-final-state.md).
> **What that answer is, and what it is not** — an interpretation of the evidence,
> never a record of what happened — is fixed in
> [`docs/v0.4-problem-definition.md`](docs/v0.4-problem-definition.md) §12. The
> project is not on PyPI yet, so there is no `pip install` for it.

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
archaeology 0.4.0
```

## Usage

Every command takes a directory inside the repository, defaulting to the current
directory. None of them ever writes to the repository: the analysis goes into a
database in your cache directory.

The reading commands — `timeline`, `hotspots`, `files`, `file`, `structure`,
`cochange` and `explain` — take `--json`, which prints the same facts in the same
names for other programs to read. Only the JSON goes to stdout — the note about a
stale analysis goes to stderr — so the output stays parseable whether or not the
snapshot is current. `commit` has no JSON form yet, and the two writers,
`analyze` and `ast`, print a summary of what they did instead.

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
Note: this analysis stops at 78c0647a, but HEAD is now fbc2da34; run 'archaeology analyze /home/you/projects/sample-project' to refresh it
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

`--json` prints the ranking for other programs to read. The ranking's shape is
not the inventory's: a ranking cannot contain a deleted file, so it carries no
`state` field, and its rows are ordered by count.

```console
$ archaeology hotspots --json --limit 2 ~/projects/sample-project
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

**A hotspot is not a verdict.** The same count can come from core code, from code
that keeps breaking, from requirements that keep moving, from a refactor in
progress, or from a file that is simply edited often. CodeArchaeology cannot tell
those apart, so it does not try: it reports how often a file changed and stops
there.

### List the files

`files` answers a different question: not *which files are busiest* but *what
this repository contains*. It lists every file the history ever held — deleted
ones included — one line each, ordered by path the way a directory listing is.

```console
$ archaeology files --all ~/projects/sample-project
 FILE                   STATE     COMMITS   +LINES   -LINES
 ──────────────────────────────────────────────────────────
 README.md              alive           1        3        0
 assets/logo.png        alive           1        0        0
 core/app.py            alive           3       19        0
 core/cache.py          alive           1       12        0
 legacy.py              deleted         2        5        5
 工具/文本.py           alive           1        5        0

Deleted files are in the list on purpose: this is what the history contains, not what the working tree contains.
```

The `STATE` column is the fact that changes what a row means: `legacy.py` was
deleted, so its numbers describe a life that has ended, and the command says so
in as many words. A renamed file is still one row — `core/app.py` is the file
that was born as `app.py` — because "how many files are here" must have one
answer per file.

`--limit N` (default 20) and `--all` behave as they do for the timeline. The
`hotspots` command ranks living files by count; this one lists all of them by
path, and the two print the same numbers for any file they both contain.

`--json` prints the whole inventory, not the slice: `--limit` is a terminal
convenience, and a program that asked for the inventory asked for all of it.
Every row carries `state` and the names it was counted over — a row is counted
by identity, and without the chain a renamed file and two unrelated ones cannot
be told apart.

```console
$ archaeology files --json ~/projects/sample-project
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "files": [
    {
      "path": "README.md",
      "state": "alive",
      "commits": 1,
      "additions": 3,
      "deletions": 0,
      "path_history": [
        "README.md"
      ]
    },
    {
      "path": "assets/logo.png",
      "state": "alive",
      "commits": 1,
      "additions": 0,
      "deletions": 0,
      "path_history": [
        "assets/logo.png"
      ]
    },
    {
      "path": "core/app.py",
      "state": "alive",
      "commits": 3,
      "additions": 19,
      "deletions": 0,
      "path_history": [
        "app.py",
        "core/app.py"
      ]
    },
    {
      "path": "core/cache.py",
      "state": "alive",
      "commits": 1,
      "additions": 12,
      "deletions": 0,
      "path_history": [
        "core/cache.py"
      ]
    },
    {
      "path": "legacy.py",
      "state": "deleted",
      "commits": 2,
      "additions": 5,
      "deletions": 5,
      "path_history": [
        "legacy.py"
      ]
    },
    {
      "path": "工具/文本.py",
      "state": "alive",
      "commits": 1,
      "additions": 5,
      "deletions": 0,
      "path_history": [
        "工具/文本.py"
      ]
    }
  ]
}
```

The sentence about deleted files is for a reader; `state` says the same thing to
a program, so the JSON does not repeat it.

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
Version:     2024-05-02 09:00:00 +0000  f1f2a3f8

AST analysis unavailable: parse failed
  SyntaxError: invalid syntax (line 5, column 12)
```

```console
$ archaeology structure broken.py --history ~/projects/broken-project
broken.py
Versions:    1 read, 1 could not be read
  f1f2a3f8  SyntaxError: invalid syntax

ok  function
  created  2024-05-01 09:00:00 +0000  fec36049  line 1
      no ending: f1f2a3f8 could not be read, so whether it is still there is not known
```

The parse failed, so nothing is known about what the file held — not even whether
`ok` was still in it. The tool says that, rather than reporting a deletion it
cannot see or printing "0 definitions", which would read as a file that held
none. The words after `SyntaxError:` are the parser's own, recorded together with
the version of Python that said them.

`structure` reads Python only. A path that does not end in `.py`, a path the
stored history never touched, and `--history` given together with `--commit` are
each refused with a message rather than answered with something plausible.

### Show the files that change together

Some files move as a group: a module and its test, a schema and the code that
reads it. `cochange` answers that question out of the commits the history already
has, and it infers nothing — not from the message, not from the content, not from
what the files are for.

```console
$ archaeology cochange core/app.py
core/app.py
History:  app.py -> core/app.py

Analyzed: 3 commits of this file

No file changed alongside it.
2 pairs hidden: fewer than 2 shared commits.

The score is the share of this file's analyzed commits that touched the other file. Moving together is not a dependency, and a shared commit is not evidence of one.
```

The score is a conditional rate — how many of *this* file's commits touched the
other one — so it is not symmetric: asking about `legacy.py` after asking about
`core/app.py` is a different question with a different denominator. A pair needs
two shared commits before it is shown, because one shared commit is the weakest
evidence there is. `--min-shared 1` shows everything, and the hidden count is
printed rather than implied, so "none found" and "none shown" stay two different
answers:

```console
$ archaeology cochange core/app.py --min-shared 1
core/app.py
History:  app.py -> core/app.py

Analyzed: 3 commits of this file

 FILE        SHARED   SCORE
 ──────────────────────────
 README.md        1   0.333
 legacy.py        1   0.333

The score is the share of this file's analyzed commits that touched the other file. Moving together is not a dependency, and a shared commit is not evidence of one.
```

`--large-commit-limit` is the other knob. A commit that touches more files than
the limit — 100 by default — is left out of every sample, because a thousand-file
commit is one bulk change rather than half a million pairwise facts. The count of
commits left out travels with the answer:

```console
$ archaeology cochange core/app.py --min-shared 1 --json
{
  "repository": "/home/you/projects/sample-project",
  "head_sha": "78c0647a29c58018213455e4b99fb8f5869b2d3f",
  "path": "core/app.py",
  "files": [
    {
      "path": "core/app.py",
      "path_history": [
        "app.py",
        "core/app.py"
      ],
      "analyzed_commits": 3,
      "large_commits_excluded": 0,
      "hidden_pairs": 0,
      "co_changes": [
        {
          "path": "README.md",
          "shared_commits": 1,
          "score": 0.3333333333333333
        },
        {
          "path": "legacy.py",
          "shared_commits": 1,
          "score": 0.3333333333333333
        }
      ]
    }
  ]
}
```

**A name answers for every file that ever carried it.** The `History` line above
is that file's names in order, and the commits under the old name are part of the
same sample. A name reused after a deletion answers for both files, because the
history contains both — the JSON then carries one entry per file. This is the
same identity the rest of the tool uses: `hotspots` counts commits per file
rather than per name, and `file` answers for every file a name ever named.

Co-change is a *statistic about commits*, and the block says what it is not: two
files moving together is not a dependency, and a shared commit is not evidence of
one. It is a place to look, not a conclusion to draw.

### Explain one commit

`explain` is the only command that talks to a model. It builds the evidence for
one commit — everything the other commands read, gathered into one bundle — and,
when a model is configured, has it written out.

**Without a model it prints the evidence itself.** That is a working answer rather
than an error: this is a local analyser first, and the model is an addition to it.

```console
$ archaeology explain 8eaff71d
No model is configured, so this is the evidence itself. Set CODEARCHAEOLOGY_AI_BASE_URL and CODEARCHAEOLOGY_AI_MODEL to have one explained.
{
  "commit": {
    "sha": "8eaff71d34a6f7eb6e27366ca8f72ab22cbba204",
...
  "absences": [],
  "bounds": {
    "co_change_partners": 5,
    "co_change_files": 5,
    "co_change_files_omitted": 0,
    "history_commits": 5,
    "history_commits_omitted": 0
  }
}
```

The bundle carries the commit, the files it touched **and where in them the change
landed** (`ranges`, read out of git — the one thing the database does not hold),
each file's life, the definitions this commit created, modified or ended, what
usually changes alongside those files, the earlier commits that touched them, and
a list of what could not be read. `bounds` says what was capped, so a context that
left rows out never reads like a complete one.

**A commit too large to send whole is reduced for the model, and only for the
model.** The bundle is deliberately not capped — a file list that stopped early
would hide the answer to the question being asked, and a reader can scroll — so a
commit touching a thousand files runs to about 297,000 estimated tokens. What the
model is sent is a selection of it: the largest file changes and definitions, the
life of each of those files, the two most recent earlier commits of each, and the
first few spans of each change. Every list that lost rows says how many it lost,
in a `selection` block inside the prompt, and a file whose spans were cut carries
`ranges_total` on its own row, so a partial bundle never reads like a whole one.
The evidence this command prints stays whole, `explain --json` still carries all
of it, and the answer is checked against what the model was shown — a citation of
a row that was left out is refused even though the tool holds it. Measured, on the
widest shape in `benchmarks/context_benchmark.py` — two hundred files, twenty
hunks and ten definitions each — that is about 11,000 tokens sent against 358,000
in the bundle.

To have a model write it out, name an endpoint and a model:

```bash
export CODEARCHAEOLOGY_AI_BASE_URL=https://api.openai.com/v1
export CODEARCHAEOLOGY_AI_MODEL=gpt-4o-mini
export CODEARCHAEOLOGY_AI_API_KEY=...        # or OPENAI_API_KEY
```

The endpoint has to speak the OpenAI chat shape, which is what OpenAI, DeepSeek,
Ollama, vLLM and LM Studio all speak — pointing `CODEARCHAEOLOGY_AI_BASE_URL` at a
local Ollama is the fully local case and needs no code of its own. The key is read
from the environment only, never from a flag, and it never reaches an error
message.

Everything else is an environment variable too, and there is no config file: a
second place for settings is a second place for them to disagree.

| Variable | Meaning | Default |
|---|---|---|
| `CODEARCHAEOLOGY_AI_BASE_URL` | The endpoint | none — required to use a model |
| `CODEARCHAEOLOGY_AI_MODEL` | The model to ask | none — required to use a model |
| `CODEARCHAEOLOGY_AI_API_KEY` | The key, sent as a bearer token | falls back to `OPENAI_API_KEY` |
| `CODEARCHAEOLOGY_AI_TIMEOUT` | The deadline around one whole call, in seconds | 60 |
| `CODEARCHAEOLOGY_AI_MAX_RETRIES` | Attempts after the first, on the failures repeating can fix | 2 |
| `CODEARCHAEOLOGY_AI_MAX_CONTEXT_TOKENS` | The ceiling on what is sent | none — past it the command refuses rather than truncating |
| `CODEARCHAEOLOGY_AI_MAX_OUTPUT_TOKENS` | The ceiling on the answer | none — an answer cut off at it is reported as cut off, not as malformed |

The deadline is wall-clock around the whole call rather than a socket timeout, so
an endpoint that dribbles one byte at a time still ends. Retries cover connection
and DNS failures, a read that timed out, `429` and `5xx`; every other `4xx` is the
request or the key being wrong, and repeating it would spend your money to fail
identically.

The answer is shown under three headings, and they are the whole of what tells a
reader which part is which:

- **Observed** — what the evidence holds. Every line cites the bundle, and a
  citation that names nothing the bundle holds is refused rather than shown.
- **Possible** — candidate reasons, each naming the observed changes it rests on.
  The count under each one is how much evidence there is, not how good the reason
  is; it is counted by the tool, never reported by the model.
- **Unknown** — what could not be read, or is not in the repository at all.

**The block opens by saying what the whole of it is: an interpretation of the
evidence, not a record of what happened.** That is the contract this layer is
held to, and it is the one thing a reader needs before the first claim rather than
after it — an answer about a commit reads like a record of that commit, and it is
not one. The distinction is not modesty about model quality; it is what the tool
can actually check. The citations are the facts, the sentences around them are the
reading, and the opening line says which half is verified. A sentence that stays
inside a citation and still overstates it is not something a program catches —
which is why the sentence is printed rather than left to the reader's judgement.
The same commit explained twice may be explained differently, and that is what an
interpretation is; what is identical between two runs is the evidence.

**Every claim prints what it rests on**, expanded: the commit, the files, the
spans the change landed in, the definitions it touched, what moves alongside
them, the absences. The citations appear under the claim that uses them and again
under the reason built on them, so a reader never has to follow ids back through
the JSON to find out why something was said. **A candidate reason may be wrong;
what it cannot do is leave a reader unable to see what it was standing on.**

A citation names one of six things, and each is held against the bundle rather
than against its shape: a `commit`, a `file`, a `definition`, a `range` (a span a
diff actually put there), a `cochange` (one direction of a pair, because the
statistic is not symmetric), or an `absence`. Citing the narrowest thing that
carries the claim — the span rather than the file, the pair rather than "these
files" — is what makes an answer checkable at all.

**And a candidate is marked for what it is.** Under the candidates there is a
sentence saying that a reading is not a finding — moving together is not a
dependency, and a definition appearing or changing is not a statement about what
the author meant — and a candidate whose entire support is a co-change statistic
says so on its own line. A sentence is not a defence against a model that means to
mislead; it is a defence against a reader taking a candidate for a finding, which
is the failure this layer can actually prevent. What the tool refuses outright,
what it only marks, and what it cannot catch at all is written down in
`tests/test_boundaries.py`, one section per way a model can be wrong.

**The model is never asked why the author made a change**, because the evidence
does not hold it and no wording makes it available. A candidate reason is a
candidate, and it is never printed in the summary, where a guess would read as a
finding. An answer that breaks these rules is asked for once more and then refused
whole — nothing of it is shown, because half an explanation with a note is worse
than none.

`--json` prints the same answer for a program. stdout is the document and nothing
else; every note and every error goes to stderr, so a caller can parse stdout
without filtering it first. When the commit was too large to send whole the
document also carries `selection` beside the evidence: the evidence is still all
of it, and that key is how much of it the model saw.

```console
$ archaeology explain 8eaff71d --json
No model is configured, so this is the evidence itself. Set CODEARCHAEOLOGY_AI_BASE_URL and CODEARCHAEOLOGY_AI_MODEL to have one explained.
{
  "commit": "8eaff71d34a6f7eb6e27366ca8f72ab22cbba204",
  "state": "evidence_only",
  "explanation": null,
  "evidence": {
...
      "co_change_partners": 5,
      "co_change_files": 5,
      "co_change_files_omitted": 0,
      "history_commits": 5,
      "history_commits_omitted": 0
    }
  }
}
```

**One shape in both states, with `state` saying which one it is.** Without a model
there is no explanation, and the evidence is the answer — so `state` is
`evidence_only` and `explanation` is null. A reader that had to work out which of
the two it got would be a reader that can be wrong, the same reason `file --json`
is always an object holding a list. The evidence travels beside the answer so that
one can be checked against the other without a second call, and the `confidence`
in the JSON is the count the tool made, not a number the model chose.

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
HEAD        07968666
Database    /home/you/.cache/codearchaeology/82d48b376e387fde.db
$ archaeology structure core/app.py ~/projects/sample-project
core/app.py
History:     app.py -> core/app.py
Version:     2024-03-12 09:00:00 +0000  07968666

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

`benchmarks/benchmark.py` measures the read-side commands and the AST pass at
whatever scale you ask for, and it runs the pass three times: on a database with
no structure in it, on the same one immediately afterwards, and on one that just
gained a hundred commits. It is run by hand, not by CI, because a test that
asserts a wall-clock time fails whenever the machine is busy and teaches people
to re-run it.

```console
$ uv run python benchmarks/benchmark.py --commits 10000 50000 100000 200000
```

On one machine, over a generated history, taking the fastest of three runs:

| Commits | File changes | Repository | Database | `analyze` | `timeline` | `hotspots` | `file <path>` | by name only |
|---|---|---|---|---|---|---|---|---|
| 10,000 | 50,500 | 8.5 MB | 12.4 MB | 1.4s | 0.24s | 0.36s | 0.39s | 0.03s |
| 50,000 | 250,500 | 42 MB | 62.6 MB | 7.8s | 1.7s | 2.3s | 2.3s | 0.13s |
| 100,000 | 500,500 | 84 MB | 125 MB | 17.1s | 3.5s | 5.6s | 5.6s | 0.27s |
| 200,000 | 1,000,500 | 168 MB | 250 MB | 41.0s | 8.6s | 13.6s | 13.3s | 0.61s |

**The history is generated, so read the curve and not the numbers.** 200 files,
five of them edited per commit, no renames and no deletions, so the commit sizes
are uniform. A real repository has larger diffs, more renames, and a longer tail
of file sizes, and all three move these figures.

What the curve says:

- **Nothing is quadratic.** Every operation roughly doubles when the history
  doubles, from ten thousand commits to two hundred thousand.
- **`analyze` dominates, and it is paid once.** Reading the history out of git
  and writing it to SQLite costs more than every query put together — and at two
  hundred thousand commits 85% of it is the writing, because the rescan rewrites
  every commit, parent and file-change row whether or not anything changed.
  Everything after it reads the database instead.
- **`hotspots` and `file <path>` cost the same**, because a file's life can only
  be rebuilt by walking the whole commit graph, and both of them need one. This
  is not a coincidence to be tuned away; it is the shape of the model.
- **Asking by name is one to two orders of magnitude cheaper** than asking by
  identity, because it is a query rather than a walk. It is also a different
  question: it answers for a path as written, not for the file across its
  renames.

### The structure layer, measured separately

The `ast` pass is the expensive half of the tool, so the benchmark times it in
phases and runs it three times: **cold** (a database with no structure in it),
**warm** (the same database again, where every version is already stored) and
**grown** (a hundred commits appended, `analyze` run, then the pass). Same
fixture, Python 3.13:

| Commits | File versions | Definitions | Database (Git) | Database (+ AST) | cold | warm | grown |
|---|---|---|---|---|---|---|---|
| 10,000 | 50,500 | 272,700 | 12.4 MB | 101.3 MB | 33.0s | 10.0s | 9.0s |
| 50,000 | 250,500 | 1,352,700 | 62.6 MB | 505.5 MB | 186.9s | 46.3s | 46.8s |
| 100,000 | 500,500 | 2,702,700 | 124.9 MB | 1008.8 MB | 395.3s | 109.6s | 134.5s |
| 200,000 | 1,000,500 | 5,402,700 | 249.5 MB | 2016.1 MB | 809.9s | 304.0s | 333.7s |

- **A re-run parses nothing, writes nothing, and still reads everything.** The
  warm pass reuses every version it already stored and hands no row to the
  writer, and it still costs a third of the first pass: it reads every blob out
  of git to learn it is the one that was stored, and reads every stored
  definition back to compare against. What it saves is the parsing and the
  writing.
- **A pass after a hundred new commits writes those hundred** — 500 versions out
  of a million at two hundred thousand commits — and costs about what the warm
  pass costs, most of the difference being the rescan in between pushing the
  pass's pages out of the operating system's cache.
- **Every phase is linear in the number of file versions except the insert**,
  which slows as the database grows past the page cache (about 52,000 rows a
  second at ten megabytes, about 20,000 at a gigabyte) and stays linear anyway.
- **The walk over the tree costs more than the parse that comes before it.**
  Rendering each definition and hashing it costs about three and a half times the
  parsing of the file it came from, at every scale.
- **A snapshot stores about three rows for every row a change log would keep**
  over this fixture (2.98 to 3.00), and the structure layer costs about eight
  times the Git facts to store: at two hundred thousand commits, 250 MB of Git
  facts become a two-gigabyte database.
- **A two-hundred-thousand-commit history wants about four gigabytes of memory**
  to run the pass, because the stored versions and the stored definitions are
  both held whole while it works. That is the figure to plan with, not the
  database size.

The pass reports its own success rate — `parsed 45000, failed 5000` at ten
thousand commits, 10.0%, which is the fixture's one-file-in-ten — and every row
carries the interpreter and the analyzer that produced it.

## Project structure

```
src/codearchaeology/
    cli.py          the Typer application and its ten commands
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
    cochange.py     the co-change analysis: the statistic, the block and the JSON
    lifecycle.py    rebuilding each file's life from the stored history
    statistics.py   the numbers that summarise one file's life
    hotspots.py     ranking living files, and the inventory of every file
    relationships.py  commits and files, read from either end
    context.py      the evidence for one commit, built with no model anywhere near it
    selection.py    what of that evidence a model is shown, when it is too large
    explanation.py  the answer's shape, the instructions, and the block
    validation.py   the three passes that decide whether an answer may be shown
    provider.py     the one module in the tool allowed to reach a network
    formatting.py   small helpers shared by the two views
tests/
    sample_repo.py  builds a small deterministic repository for the tests
    conftest.py     the fixture that hands that repository to every test
benchmarks/
    benchmark.py    the Git operations and the AST pass, at whatever scale you ask
    cochange_benchmark.py  the co-change analysis as the history grows
    index_benchmark.py     what each database index buys and what it costs
    context_benchmark.py   the explanation bundle's size, section by section
```

## Principles

These are not aspirations. They constrain what the code is allowed to do.

1. **Core First** — The core cannot depend on an LLM. Every feature must run
   with no AI at all. AI is a layer on top, never the foundation. Held by
   `tests/test_offline.py`, two ways: every reading command is run with every way
   of opening a socket taken away, and `provider.py` is the only module in the
   package allowed to import a networking module — so a later unit that reaches
   for one one layer too high fails the build.
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
7. **Interpretation, not history** — What the tool derives from a repository is a
   fact: a commit, a diff, a definition, each with a test behind it. What a model
   writes from those facts is a **reading of them, never a record of what
   happened**, and the two are never presented as the same kind of thing. The
   evidence is reproducible and the prose is not; the structure keeps Observed,
   Possible and Unknown apart; every claim prints the citations it rests on; the
   block opens by saying what it is; and nothing a model writes is ever stored as
   evidence. Held by `tests/test_explain.py` (the block's opening line, and its
   absence from the JSON) and by the structure itself, which is what
   `tests/test_validation.py` and `tests/test_boundaries.py` keep honest: a
   reading cannot be presented as an observation, and a citation that names
   nothing is refused whole. The full statement is
   `docs/v0.4-problem-definition.md` §12.

## Roadmap

| Stage | What it adds | Status |
|---|---|---|
| v0.1 | Git scan, commit history, file changes, SQLite storage, CLI timeline | Done |
| v0.2 | File lifecycle, code hotspots, and a JSON output for other programs | Done |
| v0.3 | AST analysis, function and class evolution, and co-change | Done |
| v0.4 | AI explanations over the evidence layer (pluggable providers) | Done — `explain`, with the OpenAI-compatible provider |
| v0.5 | Developer memory — your own technical usage over time | Planned |
| v0.6 | AI-assisted change analysis and replay | Planned |

One stage at a time. Nothing in a later stage gets designed before the earlier
stage is stable.

Each release, and what it changed, is recorded in the
[CHANGELOG](CHANGELOG.md).

## Known limitations

- **Nothing is incremental yet, in either direction.** `analyze` rescans the whole
  history and rewrites every commit, parent and file-change row — at two hundred
  thousand commits, 85% of its time is that rewrite. The AST pass walks every
  Python file version every time it runs: it parses nothing it has already parsed
  and writes nothing it already stored, but it still reads every blob and every
  stored definition back to find that out.
- **A large repository costs real time and real memory.** The first `ast` over a
  two-hundred-thousand-commit history takes about thirteen minutes, holds about
  four gigabytes, and produces a two-gigabyte database. Smaller histories are
  proportionally smaller on every axis: a hundred thousand commits is about seven
  minutes, two gigabytes and one gigabyte of database.
- **The structure layer is about eight times the size of the Git facts it comes
  from**, because it is a snapshot rather than a change log: every definition of
  every version is stored, not only the ones that changed.
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
- `commit` has no JSON form, so a program that wants one commit's facts has to
  parse the block. The other six reading commands take `--json`.
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
- **A co-change score is a statistic about commits, not a statement about the
  code.** It is the share of one file's commits that touched another, it is not
  symmetric, and two files moving together is not evidence that either one
  depends on the other. A commit touching more files than `--large-commit-limit`
  (100 by default) is left out of every sample, and pairs sharing fewer than
  `--min-shared` commits (2 by default) are hidden; both counts are reported, so
  an excluded commit and an unshown pair never read as "nothing found".
- A second `ast` run parses nothing, writes nothing, and still reads every blob
  and every stored definition back to compare them, so it costs about a third of
  the first run: 110s against 395s at a hundred thousand commits.
- Every stored row carries the version of Python that read it *and* the version
  of the analyzer that compared it, so a database written by an older interpreter
  or an older analyzer has those versions read again rather than two producers'
  results being mixed. Changing how a definition is rendered or compared
  therefore invalidates every stored row, and the next `ast` re-parses it.
- A definition is compared by its own structure, not by where it sits, so a
  function that only moved down its file is `unchanged`.
- **`explain` is the only command that needs anything outside the machine.** With
  no model configured it prints the evidence and exits 0; with one, it sends the
  evidence for that commit to the endpoint and shows what comes back. Nothing else
  in the tool depends on it, and the analysis itself never leaves the repository.
- **An explanation is an interpretation, not a historical fact, and it is checked
  for its citations rather than for its truth.** A citation that names nothing the
  bundle holds is refused whole, and a candidate reason is kept out of the summary
  — but a sentence that stays inside a citation it does name and still overstates
  it is not something a program can catch. The block says so in its first line,
  and the check is written down with its limit in
  `docs/v0.4-explanation-schema.md` §6 and `docs/v0.4-problem-definition.md` §12.
- **Nothing a model writes is stored.** An explanation is printed and read, never
  written back to the database, so no interpretation can later be read as evidence
  by a command that treats rows as facts. Whether one is ever stored is an open
  question, and this is why it is not a small one.
- `explain --json` always carries the evidence beside the answer, but there is no
  flag for seeing the evidence *alone* in the block form once a model is
  configured — the offline path is the only way to that.
- What is sent to a model is bounded twice. The selection cuts a large commit
  down to its largest entries — about 11,000 estimated tokens at the widest shape
  the benchmark builds, against 358,000 in the bundle — and past
  `CODEARCHAEOLOGY_AI_MAX_CONTEXT_TOKENS` the command refuses rather than sending
  evidence the endpoint would truncate.
- **The selection keeps the largest changes, which is a rule about size and not
  about importance.** A pure rename moves no lines, so on a commit large enough to
  be reduced it sorts last among the files and is the first thing left out; the
  counts in the `selection` block say how many rows went with it. Nothing here
  decides what a change *meant*, and the block is written so a reader is not left
  to guess what is missing.
- Tested on Linux and Windows, on Python 3.11 and 3.13, by CI. macOS is untested.

## License

MIT — see [LICENSE](LICENSE).
