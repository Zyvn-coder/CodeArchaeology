# PROGRESS.md

Working notes for whoever picks this up next, including a fresh session of the
agent that has lost its context. Read this before touching anything.

It holds the two things that cannot be recovered by reading the code or
`git log`: **why** each decision was made, and which traps already cost time.
It is not a changelog — `git log` covers what changed and when, so do not copy
that here.

Update it when a decision is made or a trap is found. Nothing else.

**Last updated: 2026-09-28, at commit `30d2bdc`.**

## Where the project stands

v0.1 is finished and pushed: `analyze`, `timeline` and `commit` work, and the
suite runs green on Linux and Windows, Python 3.11 and 3.13.

v0.2 is in progress, and everything built so far is **data layer with no way to
see it**. Three units in a row were spent on models that no command reads yet:

| Unit | What it added | User-visible? |
|---|---|---|
| 1 | `lifecycle.py` — one file's identity across renames, deletes and reuse | no |
| 2 | the rename similarity git reports, stored; schema version 1 to 2 | no |
| 3 | `statistics.py` — the numbers that summarise a file's life | one stderr warning |

The other half of v0.2, code hotspots, has not been started.

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
| No CLI yet | The user chose data layer first, three units running. This is their call to keep making. |

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
   was wrong because of it. Use `git ls-files --eol` for that question instead.
8. **A test that names a fixture must take it as a parameter.** Naming it only in
   the body silently refers to the fixture *function*, and the assertion then
   compares against a function object.
9. **A test must not verify a definition against itself.** The net-change
   invariant test asks git for the file's content at its last commit rather than
   re-deriving the same arithmetic, so the two are able to disagree.

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

## Next

1. **The first command that shows any of this**: `archaeology file <path>`, with
   the lifecycle's path history and its statistics.
2. **Code hotspots**, the other half of v0.2.
3. Optional and unasked: `.gitattributes` to pin LF; clearing the three junk
   databases in the local cache that point at deleted temp directories.

## How to verify

```console
$ uv run pytest                 # 121 tests
$ git push origin main          # over SSH, see above
$ gh run list --limit 1         # then gh run watch <id>
```

A database written by an older schema version rebuilds itself on the next
`analyze`; there is nothing to clean up by hand.
