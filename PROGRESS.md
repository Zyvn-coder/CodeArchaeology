# PROGRESS.md

Working notes for whoever picks this up next, including a fresh session of the
agent that has lost its context. Read this before touching anything.

It holds the two things that cannot be recovered by reading the code or
`git log`: **why** each decision was made, and which traps already cost time.
It is not a changelog — `git log` covers what changed and when, so do not copy
that here.

Update it when a decision is made or a trap is found. Nothing else.

**Last updated: 2026-09-28, describing commit `c111281` plus the `--json` work
that is in the working tree but not committed yet.**

## Where the project stands

v0.1 is finished and pushed: `analyze`, `timeline` and `commit` work, and the
suite runs green on Linux and Windows, Python 3.11 and 3.13.

v0.2 is in progress, and everything built so far is **data layer with no way to
see it**. Four units in a row were spent on models that no command reads yet:

| Unit | What it added | User-visible? |
|---|---|---|
| 1 | `lifecycle.py` — one file's identity across renames, deletes and reuse | no |
| 2 | the rename similarity git reports, stored; schema version 1 to 2 | no |
| 3 | `statistics.py` — the numbers that summarise a file's life | one stderr warning |
| 4 | `hotspots.py`, and the `hotspots` command that prints the ranking | yes |
| 5 | `relationships.py` — commits and files, read from either end | no |
| 6 | the `file` and `files` commands, and `file.py` behind the first | yes |
| JSON | `--json` on `file`, `files` and `hotspots` | yes |

Every part of the v0.2 model now has a command in front of it. `analyze` fills
the database; `timeline`, `hotspots`, `files`, `file` and `commit` read it back.

The `--json` row has no unit number because the user asked for it as a standing
principle rather than as a numbered unit: "CLI → JSON → Web UI → AI". `timeline`
already had `--json` from v0.1; this added it to the three commands v0.2 brought,
so the whole read side of the CLI is now machine-readable.

## Decisions and why

| Decision | Why |
|---|---|
| Rename detection keeps git's default 50% similarity | A false rename invents an identity chain that never existed; a missed one only splits a life in two. Evidence First forbids the first. |
| The similarity score is stored | It is the difference between a certain rename and one that barely cleared the threshold. The planned v0.3 inference would otherwise have to rescan every repository to get it back. |
| Lifecycle identity is a chain, not a path | A name can be reused after a delete or after a rename, and both start a second life. Only a deletion closes one. |
| A schema mismatch drops and rebuilds the tables | `storage.py` already says the database is a cache and every row can be read out of git again, so there is nothing worth writing migration code to keep. |
| A modification is an event that changed content | `M` always counts; `R` counts only when it carried line changes. A pure rename leaves the content alone. |
| A file created and never touched has no last-modified time | Nothing modified it. Filling in its birth would be inventing a fact. |
| The rename threshold is a knob, not a git fact | Any `-M<n>` is still git's algorithm. Choosing `n` is not "inventing our own inference", so the principle does not decide it — it only rules out writing our own similarity scorer. |
| No CLI yet | The user chose data layer first, four units running. This is their call to keep making. |
| Hotspots are keyed on identity, not on names | Counting by name does two opposite things at once: it splits one renamed file across rows, and it merges two unrelated files that happened to share a name. A fixture demonstrates both in the same table. |
| Hotspots rank by commit count, not by churn | Commit counts are the steadier signal; churn is dominated by generated files and moves with the rename threshold. Both numbers are on the row, so a caller can sort by the other. |
| Deleted files stay out of the ranking by default | A hotspot is a place, and a file that is gone is not one. The history keeps them behind `include_deleted`. |
| Frequency must never be reported as importance | The same count comes from core code, from code that keeps breaking, from moving requirements, from a refactor in progress. The tool cannot tell those apart and must not imply it can. |
| "Which commits touched this name" and "which commits touched this file" stay two questions | They disagree wherever a name was reused, and the fixture shows both directions of the difference. Collapsing them into one would make the tool quietly wrong about either renames or reused names. |
| Every command that reads the history warns when the snapshot is behind | `timeline` had the warning and the three later commands did not, which is the same silence in a different place. It goes on stderr, so no command's output changes shape. |
| JSON carries the same facts, not a summary of them | The user's example listed six fields; the block prints more. Handing a program less than the terminal gets would make the JSON the place where facts go missing, so it carries everything the block shows, under the names the user wrote. |
| `file --json` is always an object holding a list | A name can belong to several files, so the answer cannot be the bare object without the shape depending on the repository. A reader that had to work out which of the two it got would be a reader that can be wrong. |
| `files --json` and `hotspots --json` emit identical bytes | The two commands differ only in how the terminal draws the ranking, and JSON is not a terminal drawing. Giving each its own key would make a consumer's code depend on which command it called. |
| The "frequency is not importance" sentence is not in the JSON | It is prose for a reader. stdout has to stay parseable, and the count means the same thing either way. |
| `deleted` is a flag rather than a null date to infer from | A reader that only wants to know whether the file is still there should not have to work out that `deleted_at: null` means "alive". |
| A timestamp is `isoformat()`, not `str()` | `str(datetime)` drops the UTC offset, and a commit's time without its offset is a different fact from the one git reported. |
| A time the file never had is `null` | Same rule as the block printing `-`: a file created and never edited has no last-modified time, and filling its birth in would be inventing a fact. |

## Traps already found

1. **The similarity score was silently discarded.** `change_type = fields[4][0]`
   took the first letter of `R056` and dropped the rest. Fixed, but check for the
   same shape elsewhere: git packs two facts into one field.
2. **`create_schema` cannot add a column.** It uses `CREATE TABLE IF NOT EXISTS`,
   so an existing table keeps its old shape. Raising the schema version would
   have crashed the *writer*, which had no version guard while the reader did.
3. **`prepare_database` must read the version before `create_schema` runs.**
   `create_schema` stamps the version unconditionally, so checking afterwards
   finds a fresh version on an old set of tables and concludes all is well.
4. **`read_commits` returns newest first.** Tuple unpacking has been wrong twice
   because of it. `(renaming, _) = read_commits(...)`, not the other way round.
5. **A shallow clone is not a short history, it is a differently shaped one.**
   Git treats the oldest commit it has as the root: a merge there loses its
   parents entirely and reports four files as brand new, where the full
   repository reports none. `analyze` warns on stderr.
6. **A path can belong to several lifecycles.** Any lookup by path has to say
   which one it means; tests use a `_lives_of(...)[index]` helper for this.
7. **`grep` in this shell is `ugrep`, not GNU grep.** A line-ending measurement
   was wrong because of it. It then went wrong a second time, in a way that reads
   as a repository-wide defect: `git show <rev>:<path> | grep -c $'\r'` reported
   every line of files that are stored as LF as CRLF. For this question use
   `git ls-files --eol`, or compare blob hashes — `git rev-parse <rev>:<path>`
   against the sha1 of `blob <length>\0<bytes>`. Both are tool-independent.
8. **A test that names a fixture must take it as a parameter.** Naming it only in
   the body silently refers to the fixture *function*, and the assertion then
   compares against a function object.
9. **A test must not verify a definition against itself.** The net-change
   invariant test asks git for the file's content at its last commit rather than
   re-deriving the same arithmetic, so the two are able to disagree.
10. **Commits can share a timestamp, and the walk depended on the order it was
    handed.** `build_lifecycles` sorted by time alone, and Python's sort is
    stable, so ties kept the caller's order — which from the store is newest
    first. A rename was replayed before the creation it belongs to and the file
    split in two. Rebase, scripted imports and converted histories all produce
    same-second commits. It now walks the parent links. Every earlier fixture
    used distinct timestamps, which is why three rounds of tests missed it.
11. **A script that checks the README can read the wrong code block.** Searching
    forward from the `$ command` line starts *inside* the fenced block, so the
    regex finds the next block's opening fence and compares against the wrong
    thing. Search backwards from the command line for the fence instead. This
    produced two false alarms in one session — once on the new JSON blocks, once
    on the Chinese README's `commit` block, which was correct all along.

## Environment notes

- **Pushing works over SSH on port 443**, through a repository deploy key and the
  host alias `codearchaeology`. `github.com:443` is blocked and the `ghfast.top`
  proxy that serves fetches does not support pushes.
- **Git Bash's own `ssh` does not read `~/.ssh/config`** on this machine: it
  resolves the home directory through `getpwuid`, there is no `/etc/passwd`, and
  it ignores `HOME`. Git is therefore pointed at the native OpenSSH binary with
  `core.sshCommand`, which reads the config correctly.
- **The system git config sets `core.autocrlf=true`.** The repository is LF
  everywhere and git warns about it on every diff. Harmless, but a
  `.gitattributes` with `* text=auto eol=lf` would settle it.
- **The two READMEs are CRLF in the working tree, uniformly.** Every other file
  is LF, and every blob in the index is LF, so the committed content is correct
  and `git diff` shows only content changes. It is a working-tree state, not
  something an edit introduced — the file is 100% CRLF rather than mixed. Do not
  "fix" it: normalising it would rewrite every line of both files.

## Next

1. **Co-change**, which the user described as the next thing the commit-file link
   makes possible: which files tend to change together. It has not been designed
   yet, and the percentage has to be pinned down first — of a file's commits, of
   the pair's, or of everything — because the three give different numbers.
2. **The English README's `timeline` block has a stale rule line.** Its data rows
   are a faithful width-110 run, but the rule above them is 91 characters where
   a run at that width draws 108. Every other line of every other block in both
   READMEs reproduces from a real run, so this is one line, not a habit. Left
   alone pending the user's word, because it is a file change.
3. **`files` and `hotspots` show the same ranking in two shapes**, and now emit
   byte-identical JSON as well. The user asked for both, so both exist. If one of
   them should become something else — an inventory including deleted files, say
   — that is their call.
4. **The `--json` work is not committed.** It is in the working tree, green, and
   waiting on the user's word.
5. Optional and unasked: `.gitattributes` to pin LF; clearing the three junk
   databases in the local cache that point at deleted temp directories.

## How to verify

```console
$ uv run pytest                 # 176 tests
$ git push origin main          # over SSH, see above
$ gh run list --limit 1         # then gh run watch <id>
```

A database written by an older schema version rebuilds itself on the next
`analyze`; there is nothing to clean up by hand.
