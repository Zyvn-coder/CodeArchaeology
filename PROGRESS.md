# PROGRESS.md

Working notes for whoever picks this up next, including a fresh session of the
agent that has lost its context. Read this before touching anything.

It holds the two things that cannot be recovered by reading the code or
`git log`: **why** each decision was made, and which traps already cost time.
It is not a changelog — `git log` covers what changed and when, so do not copy
that here.

Update it when a decision is made or a trap is found. Nothing else.

**Last updated: 2026-10-01. v0.3.x is frozen — `docs/v0.3-final-state.md` is the
record. v0.3.0 is released and unchanged on the remote; everything since is in the
working tree and uncommitted, and the tree calls itself 0.3.1 (Unit 18: both
READMEs document all nine commands, their performance tables and known limitations
are the measurements rather than the ones they replaced, and one gap — `commit`
has no JSON form — is marked rather than filled). **`CHANGELOG.md` exists as of
the same unit**, the fifth place the version is written down; its 0.3.1 section is
dated 2026-10-01. The pass no longer writes a row it already stored; the AST reuse
path reads its stored definitions in one query and records which analyzer produced
them. **The commit is made; the tag and the release page are not**, and they wait
on a green CI run on that commit.**

## Where the project stands

**v0.3.0 is released.** Unit 9 was the release: both READMEs describe all eight
commands the CLI had then, the structure layer's five rules are stated in them,
the roadmap row says Done, and the version is 0.3.0 in the four places the
existing test compares. Every console block in both files was replayed against the
fixtures at release time and matched, line for line. `main` is pushed, `v0.3.0` is
tagged, a GitHub Release carries the notes, and CI is green on all four jobs.
**It describes eight commands because that is what existed then**; the ninth
(`cochange`) is in the tree and in both READMEs now, and v0.3.x — 0.3.1 — is the
release that carries it.

The tag points at `a2a4565` rather than at the release commit `ba62bc5`, and it
moved once to get there: the first push failed on the Ubuntu 3.13 runner (trap
24), and a release tag whose own CI is red is worse than a tag that moved five
minutes after it was created. The failed runs are still in the Actions log, so
the sequence — push, fail, fix, re-tag — is visible rather than erased.

**v0.2.0 is released.** The user ran the acceptance checklist over 22 items; all
of them pass, and the last three — the roadmap status, the README status line and
the version itself — were the release. `main` and the tag are pushed, and CI is
green on all four jobs.

v0.1 is finished and pushed: `analyze`, `timeline` and `commit` work, and the
suite runs green on Linux and Windows, Python 3.11 and 3.13.

v0.2 is feature-complete: every part of its model has a command in front of it.
It was built one unit at a time, and the first three units were data layer with
nothing to see them yet, which is why the table below has a "user-visible?"
column at all:

| Unit | What it added | User-visible? |
|---|---|---|
| 1 | `lifecycle.py` — one file's identity across renames, deletes and reuse | no |
| 2 | the rename similarity git reports, stored; schema version 1 to 2 | no |
| 3 | `statistics.py` — the numbers that summarise a file's life | one stderr warning |
| 4 | `hotspots.py`, and the `hotspots` command that prints the ranking | yes |
| 5 | `relationships.py` — commits and files, read from either end | no |
| 6 | the `file` and `files` commands, and `file.py` behind the first | yes |
| JSON | `--json` on `file`, `files` and `hotspots` | yes |

`analyze` fills the database; `timeline`, `hotspots`, `files`, `file` and
`commit` read it back.

The `--json` row has no unit number because the user asked for it as a standing
principle rather than as a numbered unit: "CLI → JSON → Web UI → AI". `timeline`
already had `--json` from v0.1; this added it to the three commands v0.2 brought.
**`commit` was left without one**, and the sentence that used to stand here — that
the whole read side is machine-readable — was false from the day it was written;
Unit 18's audit found it and the gap is now stated in both READMEs and in *Next*.

## The freeze, 2026-10-01

**`docs/v0.3-final-state.md` is the record**: version, schema, commands, tests, CI,
the performance baseline, the known limitations, and the v0.4 preconditions. Unit
18 was the audit that produced it, and what it found is worth keeping because every
item was a document that had stopped being true:

| Found | Fixed by |
|---|---|
| Both READMEs said "eight commands" while the CLI had nine, and co-change had no section at all | a co-change section in both, its blocks replayed, and a test that every registered command has a section in both READMEs |
| Both READMEs said a second `ast` costs nearly as much as the first — 374s against 405s at 100,000 commits — which was true before Unit 17 and false after (110s against 395s) | the measured three-run table in both |
| Both READMEs said "every command that reads the history takes `--json`"; `commit` never had one | the sentence names the six that do, and the gap is a known limitation and a *Next* item |
| Both READMEs' performance tables were measured on the pre-Unit-8 fixture, which no longer exists | replaced with the four-scale table from `benchmark.py` as it stands |
| The known limitations had no entry for large-repository cost, database size, or what a co-change score is not | three entries added, each with the measured numbers |
| The tree called itself 0.3.0 while containing a command the 0.3.0 tag does not have | version 0.3.1 in the four places `tests/test_cli.py` compared then (it compares five now, `CHANGELOG.md` included); `uv.lock` follows on the next `uv run` |

**What the audit confirmed rather than changed**: the five model rules, each named
with the test that holds it; the Evidence First scan over the whole of `src/` — no
AI, no network, no semantic inference, and `typer` and `rich` as the only runtime
dependencies; 473 tests green on 3.11 and 3.13; and four CI jobs green on the last
pushed commit.

**Nothing was built for v0.4** — no provider, no prompt, no page — and the freeze
is the reason: the deterministic layer stops moving before anything is built on
top of it.

## v0.3 scope — and the rule that it does not grow

The user started v0.3 on 2026-09-29 with one standing instruction, in their own
words: **"v0.3 不要中途增加功能"** — no feature gets added along the way. Anything
that looks like a good idea mid-flight gets written into the table below and
left alone until v0.3 is finished; it does not get built.

v0.3 is AST analysis: which functions and classes a Python file contained at each
commit, and how each of them changed over that file's life. **The frozen
decisions are in `docs/v0.3-design.md`** — the record shapes, the fingerprint, the
failure table and the measured traps. This section is the summary; that file is
the contract. The plan, one unit at a time, in the order the data has to be built:

| Unit | What it adds | User-visible? |
|---|---|---|
| 0 | `docs/v0.3-design.md` — the design freeze. No code. | no |
| 1 | `definitions.py` — one file version's definitions, read from its bytes | no |
| 2 | `objects.py` — one `git cat-file --batch` process, kept open for many reads | no |
| 3 | schema version 3, the two tables the AST pass fills, and a rescan that keeps them | no |
| 4 | `ast_pass.py` and `archaeology ast` — the pass that fills them | yes |
| 5 | `definition_history.py` — the histories derived from those snapshots | no |
| 6 | `structure.py` and `archaeology structure --history` — the evolution view | yes |
| 7 | `archaeology structure <path>` and the JSON of both faces — the read side in full | yes |
| 8 | the scale fixture, the phased benchmark, and the analyze regression tests | no |
| 9 | both READMEs, the roadmap row and the version — the release | yes |
| 10 | the `CHECK` on `commit_files.change_type`, and schema version 3 → 4 | no |

The user names each unit as it starts, so the table is what has been built. Every
row of the plan is built, Unit 9 included: the three commands of the freeze §2
exist, `structure` has all of its faces — `<path>`, `--commit`, `--history`, and
`--json` on both — and the documentation describes them with real output. v0.3 is
finished except for the push and the tag.

**Which unit depends on which, and which of them owns what**, is written down in
`docs/v0.3-design.md` §19 — the user's own division: 3 is "怎么存", 4 is "当前版本
里有什么，以及和上一可比较版本有什么差异", 5 is "snapshot 串起来以后的历史",
6 is "把历史转成 evolution model", 7 is "把结果展示给用户". Read that section
before moving a responsibility from one module to another.

Units 1–2 are the data layer, the same shape as v0.2's first three, and for the
same reason: the model has to be right before there is anything worth looking at.

**Done so far:** Unit 0 (the freeze), Unit 1 (`definitions.py`, 33 tests of its
own), Unit 2 (`objects.py`, 19 tests), Unit 3 (schema version 3 in
`storage.py`, 24 tests), Unit 4 (`ast_pass.py` and the `ast` command, 18 tests),
Unit 5 (`definition_history.py`, 20 tests), Unit 6 (`structure.py` and
`structure --history`, 17 tests), Unit 7 (the structure of one version, and the
JSON of both faces, 15 more), Unit 8 (the AST layer's arithmetic at a thousand
commits, the phased benchmark, and the analyze regression cases, 7 more) and
Unit 9 (the release: both READMEs, the version, the roadmap row) and Unit 10 (the
change-type constraint, which is the one place the storage could still have been
made to hold a conclusion). Unit 1's
promise — bytes in,
definitions or a recorded
failure out, with no Git, SQLite or CLI anywhere near it — is itself a test: it
reads the module's own source and refuses an import that is not in the allowed
set. Unit 2's promise — many objects, one process — is a test too, and it counts
the processes started, so fifty reads would show as fifty. Unit 3's promise — the
tables hold facts and not identities — is a test as well: it compares both column
lists against the agreed ones, so a `function_id` appearing later fails the build
instead of quietly becoming part of the model. The rule that a file which could
not be parsed is not an empty file is a test too, and it is the one that keeps a
syntax error from being read later as a deleted function. Unit 5's promise — no
false certainty about a definition whose file went dark — is a test three times
over: a deletion derived across a gap has to name the gap, a definition whose file
never parses again has no deletion event at all, and the derivation's every
created and modified event is checked against the pass's stored comparison. Unit
6's promise — that the *lines* do not round the uncertainty off — is a test as
well: the block is asserted line by line, and `unchanged` is asserted to be absent
from it.

The user froze the identity rule as **file version + qualified name, with no
rename inference**. So Unit 5 shows a function that was renamed as a death and a
birth, and says so, rather than inferring a link the evidence does not support.
The AST pass is deliberately not part of `analyze`: measured, `analyze` is 45.6s
at 200,000 commits while extraction runs at 664 files/s, so the AST pass on the
same repository is about 25 minutes. A user who wants only the git facts must not
pay for that.

| Not in v0.3 | Why |
|---|---|
| AI | v0.4. |
| A web UI | Same reason as v0.2: it is not the core. |
| Inferring *why* something changed | Not enough evidence, and inventing it is not the plan. |
| Languages other than Python | Python-only is the v0.x range. |
| Complexity scoring, call graphs, dependency analysis | These are not *evolution*; they are different features. |
| AST similarity used to re-link files git reported as delete + add | The user raised this direction in the Unit 2 spec, but it changes *file* identity, which v0.2 settled on git's evidence alone. Not v0.3 unless the user says otherwise. |

## The v0.2 test matrix

The user set this matrix for v0.2 and asked for it to be complete rather than
numerous. Where each row is covered, so nobody has to search for it:

| Scenario | Where it is covered |
|---|---|
| Create | `test_lifecycle.py::test_a_file_that_is_only_edited`, `::test_the_first_commit_births_every_file_it_contains` |
| Modify | same, plus `test_statistics.py::test_a_file_that_is_created_and_then_edited` |
| Modify repeatedly | `build_edit_history_repo`; `test_statistics.py::test_a_file_edited_three_times_counts_three_modifications`, `::test_last_modified_names_the_last_edit_not_the_first` |
| Rename | `test_lifecycle.py::test_a_rename_does_not_split_the_life` |
| Rename chain | `test_lifecycle.py::test_path_history_lists_every_name_in_order` (two renames deep) |
| Delete | `test_lifecycle.py::test_a_deleted_file_is_closed` |
| Delete then recreate | `test_lifecycle.py::test_a_name_reused_after_a_deletion_is_a_second_life` |
| Rename with a heavy rewrite | `build_rename_boundary_repo`; `test_lifecycle.py::test_the_threshold_splits_three_files_differently`, `::test_a_rename_that_changed_too_much_breaks_the_chain` |
| Binary file | `assets/logo.png` in `build_sample_repo`; `test_statistics.py::test_a_binary_file_reports_no_lines_but_is_counted` |
| Unicode path | `工具/文本.py` in `build_sample_repo`; asserted in the hotspots and files tests |
| Empty file | `build_edit_history_repo`; `test_lifecycle.py::test_a_file_of_no_bytes_is_born_and_stays_one_life`, `test_statistics.py::test_an_empty_file_is_not_a_binary_file` |
| One name, two lives | `test_lifecycle.py::test_a_name_reused_after_a_deletion_is_a_second_life`, `::test_a_name_reused_after_a_rename_is_a_second_life` |
| Large repository | `test_scale.py`, over a thousand commits and five thousand file changes |

Beyond the matrix the suite also pins down the same-second ordering trap, a
rename that survives at 56% similarity, an edit whose path has no live owner,
and a cyclic history that git cannot produce.

## Performance

Measured on 2026-09-28 with `benchmarks/benchmark.py`, over generated histories,
fastest of three runs. **The generator changed in Unit 8** — it used to write files
that no parser accepts, and now writes real Python — so these are the git side of
a slightly different fixture than the table below them. The two are close but not
comparable line for line; the Unit 8 table is the one to plan v0.3 with.

| Commits | Changes | Repo | DB | `analyze` | `timeline` | `hotspots` | `file <path>` | by name only |
|---|---|---|---|---|---|---|---|---|
| 20,000 | 100,000 | 15 MB | 25 MB | 3.4s | 0.6s | 0.8s | 0.8s | 0.06s |
| 100,000 | 500,000 | 77 MB | 124 MB | 19.7s | 4.0s | 6.5s | 6.4s | 0.30s |
| 200,000 | 1,000,000 | 154 MB | 248 MB | 45.6s | 8.5s | 13.8s | 13.8s | 0.64s |

Three things to carry forward:

- **The v0.1 figure still holds.** The user recorded 20,000 commits at about
  0.65s for a timeline query; this measures 0.58s at the same size. Nothing in
  v0.2 slowed the timeline down, which was the thing worth checking.
- **`hotspots` and `file <path>` cost the same, by construction.** Both rebuild
  every life, and a life can only be rebuilt by walking the whole commit graph.
  The gap between them and the by-name query is what a stored lifecycle would
  buy, and at 200,000 commits it is 13.8s against 0.64s.
- **Nothing is quadratic.** Doubling the commits roughly doubles every figure.
  Between 20,000 and 100,000 the rise is steeper than fivefold, which is the
  cache falling out of the picture rather than the algorithm changing shape.
- **Reading content is not the bottleneck.** Measured on 2026-09-29: `objects.py`
  reads about 5,900 objects a second through one `git cat-file --batch`, 15 MB/s
  on 2 KB files, so a million file versions is under three minutes against roughly
  twenty-five minutes of parsing them. The audit's 12,271 files/s was measured
  under different conditions and does not reproduce today (git alone, with its
  streams redirected, measures 8,900/s); 6,000/s is the figure to plan with.

- **The pass, measured on 2026-09-29.** Over this repository: 75 file versions and
  869 definitions in 0.33s, and 0.12s the second time round with all 75 reused
  (the 869 is the same number the audit reached for a snapshot of this history).
  Over the 1,000-commit scale fixture: 5,000 file versions in 1.31s, 3,805
  versions a second, and 0.92s on the second run. Those files are trivial, so the
  figure that matters for planning is still the parse: 664 real files a second,
  against 5,900 objects a second for the reading.

- **The AST layer in phases, measured on 2026-09-29** with the fixture's files
  made real Python (`LARGE_DEFINITIONS` per version, one file in ten unparseable).
  Python 3.13.5, the pass run once on a fresh database:

  | Commits | Versions | Definitions | DB git | DB +AST | read | parse | extract | compare | insert | total |
  |---|---|---|---|---|---|---|---|---|---|---|
  | 1,000 | 5,000 | 27,000 | 1.3 MB | 10.1 MB | 0.86s | 0.31s | 1.12s | 0.02s | 0.52s | 3.02s |
  | 10,000 | 50,000 | 270,000 | 12.4 MB | 100.4 MB | 8.29s | 3.16s | 11.11s | 0.24s | 11.11s | 35.63s |
  | 20,000 | 100,000 | 540,000 | 24.9 MB | 202.4 MB | 13.93s | 5.24s | 19.13s | 0.38s | 27.42s | 69.09s |
  | 100,000 | 500,000 | 2,700,000 | 124.9 MB | 1008.0 MB | 67.91s | 25.52s | 100.40s | 2.07s | 192.18s | 405.27s |

  Four things to carry forward, and two open questions:

  - **Everything but the insert is linear.** Ten times the history costs ten times
    the reading, the parsing, the walking and the comparing.
  - **The walk costs more than the parse it does first.** `extract` — the canonical
    rendering and the fingerprint, per definition — is about three and a half times
    `parse` at every scale. If the pass is ever optimised, that is where the time is.
  - **The insert rate falls from about 52,000 rows/s to about 20,000** as the
    database grows from ten megabytes to two hundred. The rate flattens after the
    first drop, and the total stays linear. Why it falls is *not* established: the
    candidate is the page cache, since the row key begins with a random sha and the
    inserts are therefore scattered over a file that no longer fits in it.
  - **A snapshot stores 2.78–2.99 rows per row a change log would keep** on this
    fixture, and the audit's 2.15× was this repository's. The ratio is a property
    of the churn, so the benchmark prints both counts rather than a factor.
  - **The AST layer costs about eight times the git facts** to store, for a
    version holding six definitions. That is the number to plan with instead of
    v0.2's database size, and it moves with the definitions per file. At a hundred
    thousand commits: 125 MB of git facts become a one-gigabyte database, 2.7
    million definition rows, and six and three-quarter minutes of pass.

  The two open questions, both worth a unit of their own:

  - **A re-run costs almost as much as the first run** — 374s against 405s at a
    hundred thousand commits — even though it parses nothing. The saving is the
    parse and the walk (126s); what it pays instead is one query per version to
    read back the definitions it is reusing, five hundred thousand small queries
    where a single one would do. The docstring's claim ("a read and no parsing")
    is true; what was not known until it was measured is what the read costs.
    **Unit 16 answered this**: the queries became one query, and the profile then
    showed the larger cost was never the read at all — it is the rewrite, and
    the numbers below the co-change baseline carry it.
  - **The insert rate keeps falling** with the database size (52k rows/s at ten
    megabytes, 14k at a gigabyte) and the cause is still only a hypothesis: the
    row key begins with a random sha, so the inserts are scattered over a file
    that no longer fits in the page cache.

### Co-change baseline, 2026-09-30

Measured by `benchmarks/cochange_benchmark.py` on generated histories (200
files, the first commit creating all of them and each later one touching five,
so the commit sizes are uniform). This is the **baseline Unit 13 records before
any optimisation** — there has been none, and the numbers are here so a later
change can be judged against them.

| Commits | Files | Changes | Naive pairs | One command | Per query | Peak MB |
|---|---|---|---|---|---|---|
| 500 | 200 | 2,695 | 24,890 | 0.056s | 0.003s | 2 |
| 10,000 | 200 | 50,195 | 119,890 | 1.175s | 0.087s | 38 |
| 50,000 | 200 | 250,195 | 519,890 | 7.221s | 1.135s | 207 |

- **"One command" is what `archaeology cochange <file>` costs**: load the
  commits, build the identity model, answer for one file. It is the number a
  user feels, and it grows linearly with the history (0.056 → 1.175 → 7.221s
  against 100× the commits).
- **"Per query" rebuilds the model for every file**, because that is what the
  public `analyze_cochange` does. It is the price of asking about a whole
  repository in one process, not of one command. The obvious optimisation is an
  entry point that takes the lives once; it is deliberately not done here (the
  unit's charter forbids a refactor), and this column is the number that would
  justify it if anyone wants it.
- **"Naive pairs" is the count the per-file algorithm never materialises** —
  519,890 at 50,000 commits, against a distinct-pair count that stays flat
  because the fixture's files keep pairing with the same neighbours. The gap is
  the design's whole argument (§13 of the freeze), measured.
- **Peak memory is the loaded history**, not the pairs: 207 MB at 50,000
  commits, in the same range as the other commands that read the whole history.
- The 4,000-file single commit is covered by a test rather than this table: it
  is excluded by the limit, and its 7,997,998 potential pairs cost nothing.

### The AST pass's reuse path, 2026-09-30

Measured on a generated 50,000-commit history — 200 files, five touched per
commit, three functions per file — so 250,195 file versions and 750,585
definitions. **The generator is the reason this table exists**: the first
version of `benchmarks/cochange_benchmark.py` wrote files holding only a comment
and an assignment, so its "versions" had no definitions to store and every AST
timing taken from it measured an empty path. Fixed in Unit 16; the numbers
below are the first honest ones.

| Pass | Total | git read | definitions read | compare | insert |
|---|---|---|---|---|---|
| first (everything parsed) | 127.3s | 24.0s | — | 0.6s | 73.2s |
| second (everything reused) | 109.7s | 24.0s | 6.5s (one query) | 0.6s | 73.2s |

- **Reuse works and is now profitable**: the second pass is faster than the
  first, because it swaps 103s of parse-and-walk for 6.5s of reading.
- **The bulk read closed the item Unit 8 left open.** One query for all 750,585
  definitions costs 6.5s; the per-version reader costs 9.0s for the same rows —
  and that 9.0s is *mostly per-query overhead*, not row volume, which is why the
  gap is not larger. It is a real saving and it is not the big one.
- **The rewrite is the big one.** 73s of a 112s second pass is
  `write_ast_batch` deleting and re-inserting 750,585 rows that are already
  correct — 65% of the run, spent to store what is already stored. **Unit 17 did
  it** (the section below): a version whose stored rows are the ones it would
  write is not handed to the writer at all, and the second pass at this scale
  went from 131.6s to 46.3s having written nothing.
- **`parsed_at_version` is now `3.13.5+1`**: the parser's version and the
  analyzer's. The reuse rule compares it whole, so a change to how definitions
  are rendered or compared invalidates every stored row instead of reusing
  answers under a rule that no longer exists. Bumping `ANALYZER_VERSION` in
  `ast_pass.py` is what makes that happen, and a test pins it.

### Unit 17: the four scales, and the write that was not needed

Measured on 2026-09-30 with `benchmarks/benchmark.py`, one generated history per
scale and three runs over each: **cold** (a database with no AST rows), **warm**
(immediately again, every version reused) and **grown** (`--partial 100` commits
appended, `analyze` run, then the pass). The fixture is the v0.3 one — 200 files,
five touched per commit, three functions and a two-method class per file, one file
in ten unparseable — so 5.4 definitions per version. `written` is the number of
versions handed to `write_ast_batch`, counted by the wrapper rather than inferred.

| Commits | cold | warm | grown | cold wrote | warm wrote | grown wrote |
|---|---|---|---|---|---|---|
| 10,000 | 33.0s | 10.0s | 9.0s | 50,000 | **0** | **500** |
| 50,000 | 186.9s | 46.3s | 46.8s | 250,000 | **0** | **500** |
| 100,000 | 395.3s | 109.6s | 134.5s | 500,000 | **0** | **500** |
| 200,000 | 809.9s | 304.0s | 333.7s | 1,000,000 | **0** | **500** |

- **A warm pass writes nothing and a grown pass writes the hundred new commits
  and nothing else**, at every scale. The before/after, same harness and same
  fixture (the "before" run is the pre-change code, which is why it exists at only
  the two scales that were affordable to run twice):

  | Commits | warm before | warm after | grown before | grown after |
  |---|---|---|---|---|
  | 10,000 | 23.0s | 10.0s | 23.9s | 9.0s |
  | 50,000 | 131.6s | 46.3s | 138.3s | 46.8s |

- **The cold pass did not move** — 10,000 commits 32.5s → 33.0s, 50,000 191.2s →
  186.9s. That is the check that the change is a skip and not a saving found
  somewhere else: a cold pass has nothing to reuse, so nothing may change for it.
- **What a warm pass still pays, at 200,000 commits**: 304s, of which 112s is the
  git read (every blob fetched to learn it is the one already stored) and 134s is
  the stored definitions read back for the comparison. 81% of the run is reading
  to prove nothing changed. The definitions read is what makes the skip safe and
  it is the largest single item; the git read could be cut by asking
  `git cat-file --batch-check` for ids without contents, which is not built.
- **The definitions read is not linear**: 21.6s at 100,000 commits against 134.3s
  at 200,000, six times the time for twice the rows, with the statement unchanged
  (`SELECT * FROM definition_versions ORDER BY commit_sha, path, position`). The
  jump is the process, not the query: at 200,000 it builds 5.4 million
  `DefinitionVersion` objects and peaks at 4.35 GB.
- **The grown run reads more than the warm one, for the same rows** — at 200,000
  commits, 128.1s of git reads against 112.3s and 153.7s of definitions against
  134.3s. The hundred appended commits are not 30 seconds of work; the `analyze`
  between the two runs rewrote the whole history and pushed the pass's pages out
  of the operating system's cache, so the grown run reads them back from disk. It
  is the one number in the table that is about the machine rather than the work.
- **Memory is the limit nobody had measured.** Peak working set of the process:

  | Commits | versions | definitions | db git | db +ast | ratio | peak MB |
  |---|---|---|---|---|---|---|
  | 10,000 | 50,500 | 272,700 | 12.4M | 101.3M | 2.98x | 251 |
  | 50,000 | 250,500 | 1,352,700 | 62.6M | 505.5M | 3.00x | 1119 |
  | 100,000 | 500,500 | 2,702,700 | 124.9M | 1008.8M | 3.00x | 2202 |
  | 200,000 | 1,000,500 | 5,402,700 | 249.5M | 2016.1M | 3.00x | **4350** |

  The figure includes everything the benchmark itself loaded — it also builds the
  co-change model and the rankings — so it is an upper bound on `ast` alone. The
  pass's own two reads are the bulk of it: the stored versions and the stored
  definitions are both held whole, and **a 200,000-commit repository wants about
  four gigabytes to run the pass**. That is the number to plan with, not the
  database size.
- **The AST layer is 8.1× the git facts at 200,000 commits** (249.5 MB → 2016.1
  MB) and keeps 3.00 rows per row a change log would have kept. Both agree with
  the v0.3 table's 8× and 2.78–2.99× on a different fixture, which is the check
  that this harness measures the same thing it always did.

**Where the rescan's time goes.** `analyze` reported as two numbers, because the
split is the finding (the total is the fastest of three runs, the split the mean
over the same three, so the third column is approximate):

| Commits | analyze | of which `write_commits` | git extraction |
|---|---|---|---|
| 10,000 | 1.36s | 0.73s | 0.63s |
| 50,000 | 7.83s | 6.14s | 1.69s |
| 100,000 | 17.10s | 14.26s | 2.84s |
| 200,000 | 41.02s | 35.06s | 5.96s |

**The rescan is 85% writing rows git already gave us.** `write_commits` deletes
and re-inserts every commit, parent and file-change row on every `analyze`,
changed or not — 1,000,500 `commit_files` rows at 200,000 commits. It is the same
shape of waste this unit removed from the pass, and it is **not fixed here**: a
commit row is a fact about a sha and cannot change, so skipping the ones already
stored is provably safe in the same way, but unlike the pass there is no stored
marker saying which *extractor* wrote a row, so a change in how git's output is
parsed would not invalidate anything. That marker is the missing piece, and it is
in *Next*.

**The read-side commands**, fastest of three, same fixtures:

| Commits | timeline | hotspots | file (by name) | structure --history | cochange |
|---|---|---|---|---|---|
| 10,000 | 0.24s | 0.36s | 0.03s | 0.33s | 0.40s |
| 50,000 | 1.65s | 2.29s | 0.13s | 2.28s | 2.99s |
| 100,000 | 3.53s | 5.61s | 0.27s | 5.56s | 6.30s |
| 200,000 | 8.63s | 13.60s | 0.61s | 12.71s | 15.46s |

- **Everything is linear and nothing regressed.** The v0.2 table's 200,000-commit
  figures — timeline 8.5s, hotspots 13.8s, by name 0.64s — reproduce here within
  noise, on the same machine two days later.
- **The by-name query is the outlier by two orders of magnitude**, and that is the
  point of the row: it is the only read that is a query rather than a rebuild of
  the whole model, and the gap between it and `hotspots` is still what a stored
  lifecycle would buy.
- **Co-change is the most expensive single command** (15.46s at 200,000): it
  rebuilds the identity model and answers from the busiest file's sample. Asking
  about five files in one process costs 32.15s, because the public function
  rebuilds the model per call. Both are linear in the history.

### The index audit, 2026-09-30

`benchmarks/index_benchmark.py` over the 50,000-commit database — 505.5 MB, which
vacuuming brings to 475.6 MB; that 30 MB is free pages the pass's own delete and
insert leaves behind and belongs to no index. Every read the tool issues was timed
against a **vacuumed control** with one index changed, so a size and a time are
both read against the same shape of database.

| Index | Table | Size | In a plan? | Read without it | Rebuild over a full table |
|---|---|---|---|---|---|
| `commit_parents_parent` | commit_parents | 2.5 MB | **no** | — | 0.1s |
| `commit_files_path` | commit_files | 5.6 MB | **no** | — | 0.1s |
| `file_versions_path` | file_versions | 5.6 MB | yes, `structure` | 0.002s → 0.032s | 0.6s |
| `definition_versions_qualname` | definition_versions | 44.6 MB | yes, `structure` | 0.048s → 0.166s | 1.1s |
| `commits(committed_epoch)` *(candidate)* | commits | +0.6 MB | would be | 1.76s → 1.53s (inside the noise) | — |

- **Two of the four declared indexes are in no query plan at all.** The first is
  never queried by parent — the lifecycle walk goes the other way, from a commit
  to its parents — and the second is the index `find_commits_touching`'s own
  docstring says its `path = ? OR old_path = ?` cannot use. They cost 8.1 MB and
  are maintained on every `analyze`, whose entire write is 4.9 s.
- **The two that are used are load-bearing**, and by exactly one query each:
  `structure`'s versions read goes 16× slower without `file_versions_path` and its
  definitions read 3.5× slower without `definition_versions_qualname`.
- **The candidate was not added.** Its read effect is inside the noise, and the
  plan it produces replaces `timeline`'s scan-and-sort with a scan of the index —
  the same rows read through a second structure, for a `SELECT *` that needs the
  table either way.
- **The one index that looked expensive was a measurement artifact.** The first
  version of the table said maintaining `definition_versions_qualname` through a
  write cost 65.2 s against 1.1 s to build it afterwards, which argued for
  dropping it around the write; an A/B on the pass's own path — two interleaved
  runs each way — found the two level (12.4 s against 12.9 s at 10,000 commits,
  103.9 s against 103.2 s at 50,000, the sign of the difference changing between
  the scales). The replay wrote rows in storage order and the pass writes them in
  life order — trap 32. **The index set is therefore unchanged**, and the numbers
  above are why.

## Out of scope for v0.2

The user drew this boundary explicitly and the reasons are theirs. A session that
finds a feature request touching one of these should stop and ask rather than
build it — these are not "not yet scheduled", they are "do not start".

One row has since expired: AST analysis was forbidden here because it was v0.3,
and v0.3 has started, so that work is now the job. The other three carry over
unchanged, and the current boundary is the v0.3 table at the top of this file.

| Not now | Why |
|---|---|
| AI in any form — including "summarise this file" | Too early. Core First already forbids the *core* depending on a model; this extends it to features that merely use one. |
| AST analysis | That is v0.3. What has to be solid first is the chain Git history → file identity → file lifecycle. |
| A web UI | Tempting, and it is how a project like this quietly becomes "a nice page that shows Git data". That is not the core. |
| Inferring *why* something changed — "this file changes often, so it has bugs" | There is not enough evidence for that conclusion, and inventing some is not the plan. |

Verified on 2026-09-28: the only runtime dependencies are `typer` and `rich`, and
`src/` has no match for `urllib`, `requests`, `httpx`, `socket`, `http`, `openai`,
`anthropic`, `langchain`, `transformers`, `ast`, `flask`, `fastapi`, `django`,
`streamlit` or `gradio`. Re-run that check before claiming any of it still holds.

**The charter carries this too.** `AGENTS.md` §5.7 lists the same four, so a
session that reads the charter before anything else meets the boundary first.
§5.4 was reworded at the same time: it used to say AST analysis was Python-only
for v0.1–v0.3, which read as though AST were in scope now and merely
language-limited, and a session proposing AST work could point at that line. It
now says the limit is on language, not on when to start.

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
| The scale fixture is built by `git fast-import` | A thousand `git commit` calls take minutes and fast-import takes a quarter of a second, which is the difference between a test that is kept and one that gets deleted for being slow. One grammar note, learned the hard way: a `blob` command may not appear inside a commit, so file contents go inline on the `M` line. |
| The scale test asserts size, not elapsed time | A test that fails when CI is busy teaches people to re-run it. The fixture's size is what catches a walk that went quadratic; a wall-clock bound would only add flakiness. |
| `--limit` is not to be optimized now | The user's call, and the measurement backs it: `--limit` only affects what is printed, the whole history is read before it is sliced, and slicing is not where the time goes. Optimizing it would be work on a line that is already flat. |
| The benchmark measures every read-side question, not one | They do not scale alike, and the interesting fact — that two of them cost the same for a reason rather than an accident — is invisible from a single number. A benchmark that measured only the timeline would have shown nothing wrong and told nobody why. Unit 17 added co-change and the split inside `analyze` for the same reason: "where does the time go" has to include the operations a user actually waits in. |
| `analyze` is reported as two numbers, the extraction and the write | **Measured, and it changes what the rescan is.** At 50,000 commits `analyze` takes 8.07 s and 7.44 s of it is `write_commits` — every stored commit, its parents and its file changes written again, in one transaction, whether or not any of them changed. The git extraction is 0.63 s. A single total says the rescan is fast and hides that it is almost all write; the split is what makes the next unit arguable. |
| The benchmark runs by hand and is not in CI | At 200,000 commits it takes minutes, and a wall-clock assertion would fail whenever the machine is busy. It is a tool for a question, not a gate. |
| A test compares every copy of the version | It is written down in four places — `pyproject.toml`, `__version__`, and the two READMEs' install examples — and nothing compared them. A release that bumped one and not the others would ship a package whose own `--version` disagrees with its metadata, or a README showing a version nobody can install, and no test would have failed. Verified by breaking each copy in turn and watching the test fail. A fifth copy lives in `uv.lock`, generated from `pyproject.toml` and rewritten by the next `uv run`, so it is deliberately not compared: it cannot drift into a state that its own regeneration would not fix. |
| The failure record names the exception type | "invalid syntax" and "too many levels of indentation" are different problems, and a `RecursionError` is not a syntax problem at all. The stored reason is `TypeName: message`, so nothing downstream has to work out which one it was from the wording of the message. |
| The fingerprint is a token stream, not a rendered string | The structure has to be walked without recursion — the parser accepts trees that a second recursion of our own would not survive — and a stream can be hashed as it is produced, instead of building a string the size of the definition first. |
| `parsed_at_version` sits on the `Definition`, not only on the file version | The user's Unit 1 list has it there, and it makes every record self-describing: even the reason a failure carries is the interpreter's own wording, so the record has to say which interpreter said it. The storage layer can keep it once per file version, where it is the same for every definition of that version. |
| The definition's position is the tuple's order, not a field | `definitions_of` returns them in the order they appear in the file, so the index already is the position. A field would be a second copy of the same fact, and the two could disagree. |
| One request, one answer, over one process | Writing requests ahead of the answers is not an optimisation here but a deadlock: measured, three thousand requests written before any answer fills git's stdout pipe, git stops reading, and the write fails with `BrokenPipeError`. There is nothing to win either — the reader runs at 5,900 objects/s against 664 file versions/s of parsing. |
| The reader hands back the identity with the bytes | Every answer's header carries the object id git resolved the request to, so asking for a file version (`<commit>:<path>`) returns that blob's own id beside its content — never a hash computed here. The first draft returned bytes only and left Unit 4 to work the id out again; the user's Unit 2 revision asked for exactly this, and the fix was to stop discarding what git had already said. |
| `definition_versions` is a snapshot with a change marker, not a change log | **The user settled this**, after the first draft stored only what changed. A change log makes "what did this file look like at this commit" a replay of the file's whole history, and one missing row then corrupts every later answer; a snapshot costs about twice the rows (measured 869 against 404) and keeps every row a fact about one blob. |
| `position` stays in the key | The user asked for uniqueness on `(commit, path, kind, qualname)`, and that cannot be had: two definitions in one file can share a qualified name — measured, and Unit 1 has a test for it — so that key would keep one and silently drop the other. The position is the index `definitions_of` returns, and it is what tells them apart. |
| A rescan keeps the AST rows that are still true | **The user settled this too.** `write_commits` now means "the stored history is exactly these commits": it writes them, and removes the ones git no longer has together with their file versions and definitions. The first draft cleared the AST layer on every `analyze`, which threw away minutes of parsing and said nothing about it. |
| The commit row is updated, not replaced | A file version names its commit, so the foreign key refuses to let that row go while those versions exist — and deleting the versions first is exactly the data loss this unit is about. `INSERT ... ON CONFLICT(sha) DO UPDATE` keeps the row in place. |
| `change_type` compares against the last version that could be **read** | A version that fails to parse is recorded as a failure and gets no definition rows; the next version that parses is compared against the last one that parsed, so an `unchanged` can span a failure. The user's rule: this is a cached comparison, not a claim that nothing happened in between, and the history layer must not read it as an unbroken chain. |
| The pass walks file lifecycles, not paths | That is what makes a rename continue rather than restart: after `app.py` moves to `core/app.py`, the definitions in it are `unchanged`, which is the whole point of resolving the predecessor through v0.2's lifecycle walk. |
| Reuse is keyed on the version, not on the blob | A stored version is reusable exactly when its `content_sha` and `parsed_at_version` are the ones about to be worked out, so a re-run reads and compares but parses nothing. A pure rename carries the same bytes under a new name and is parsed again — the cache is per version because the row that has to be written is per version. |
| `write_ast_batch` writes a version and its definitions in one transaction | A version whose own row has been replaced while its definitions are still the previous run's says two things at once, and that is the state an interrupted pass must never leave behind. The pass writes in batches of 500 so that committing is not the cost of the run and a crash keeps what it finished. |
| `interpreter_version` is public | The pass has to record it for a version that contained no definitions at all, where there is no `Definition` to read it from — and two copies of that string would be two things to keep in step. |
| `clear_history` stays, though nothing calls it now | It is the whole-database wipe, and a rescan is no longer that. Its docstring says so plainly, so the next session does not reach for it by name; keeping it costs nothing and a caller that wants a clean database has it. |
| Replacing a file version cascades to its definitions | Without `ON DELETE CASCADE`, a second run of the pass would leave the first run's definitions behind, and every caller would have to delete them in the right order. The database can enforce it once instead. |
| `change_type` may only be `created`, `modified` or `unchanged` | The user's rule: a definition that is gone gets no row, so `deleted` is not a word this column may hold. A `CHECK` in the schema says the same three, which turns a wrong word into an error at the write instead of a row nobody can interpret. `kind` is constrained the same way, against Unit 1's three words. |
| A history is derived on the spot, never stored | The tables hold facts about blobs; a history is what a rule makes of them. Storing it would freeze a rule the user may still change, and the first rule that changed would leave rows that cannot be corrected. Derived, a rule change takes effect on the next read. |
| The derivation repeats the comparison instead of reading `change_type` | The column is a cache keyed on the pass's identity rule, and this layer has to be able to say what *it* derived. A test walks every stored row and holds the two to each other in both directions, so a rule that changes in one place and not the other fails the build. |
| Co-change is directional, `P(B \| A)`, with the queried file's commits as the denominator | The question is "when I change A, what else moves" — conditional on A, and answered from A's sample. *Measured*: the strongest pair on the generated history shares 51 commits, which is 0.1% of the repository, so the global-share denominator would report "nothing co-changes" on a repository full of co-change. The numerator must be counted inside the same filtered commit set as the denominator, or the ratio stops being a ratio of one sample. |
| A co-change pair is a pair of lifecycles, not of paths | The user's recommendation, and the fixtures show why: a file renamed twice keeps one identity and its pre-rename commits in the denominator, and a name reused after a delete starts a second life instead of merging with the first. Querying a name answers for every life that ever carried it, as `file` already does. |
| Co-change never re-implements the identity walk | The design unit's measurement script did, and its ordinal counter (`len(seen)`, which counts paths) merged two lives on the lifecycle fixture — the table was wrong and looked plausible. `build_lifecycles` numbers lives with a separate counter; the co-change module must reuse it (or map `(commit_sha, path)` from its events) so there is one identity implementation. Recorded as trap 25. |
| Merges contribute nothing, and no code says so | `git log --raw --numstat` prints no diff for a merge, so a merge has no `commit_files` rows at all — *measured*, the sample fixture's merge holds zero. Excluding merges is therefore a property of the storage model, not a query-layer rule; counting them would double every pair that crossed the merge, the over-counting `hotspots.py` already documents. Unit 11 must not add a merge branch. |
| A commit over `--large-commit-limit` (default 100) files is excluded from the analysis | A 500-file commit is one bulk change, not 124,750 pairwise facts — *measured*, that is the pair count of one such commit. *Measured* at 50,000 commits: the cap keeps 99.5% of commits and cuts generated pairs from 7,609,547 to 2,117,167. It is a definition, not an optimisation — the per-file algorithm never materialises the pairs either way. The limit is a parameter and the excluded count is reported, so the exclusion is visible. |
| Co-change is derived at query time; no table, no cache | *Measured*: generating all pairs costs 7.6M pairs / 31 s / 456 MB at 50,000 commits, but a per-file query answers in 4–18 ms at a 0.2 MB peak, because it counts occurrences inside the file's commits instead of materialising the rest. A table would also freeze a derivation whose rules (`large_commit_limit`, `min_shared`, the identity model) may still change. The design unit's rule: the database holds observations, the query layer holds derivations. |
| Minimum support is 2 shared commits, as a display filter | A pair with one shared commit and `score 1.0` is the strongest reading of the weakest evidence — *measured*, `pkg29/mod2900.py` has 48 commits and 712 partners, almost all at one shared commit. The threshold hides rows but does not touch the denominator or any shown score, and the report says how many rows it hid. `--min-shared 1` shows everything. |
| Co-change ordering is score, then shared count, then path | Same rule as the other rankings: the same database and parameters must produce the same bytes. Step 3 is the other file's `current_path`; a fourth step on the identity ordinal makes even two lives with the same current path deterministic. |
| Unit 11's knobs are exactly two: `--min-shared` and `--large-commit-limit` | Everything else the design considered is rejected in the freeze with a reason: Jaccard and global share (wrong question), `--reverse` (asking the other direction means querying the other file), `--alive-only` (asking about a deleted file is a legitimate historical question), a cache table (freezes a derivation to save milliseconds). A future unit may revisit any of them by arguing with the reason, not by adding one quietly. |
| Co-change derives identities from `build_lifecycles`'s events, not from a second walk | The freeze's rule, and trap 25 is the reason it is a rule rather than a preference. `cochange.py` maps `(commit_sha, path)` to the life ordinal from the events, so there is one identity implementation; the comment on the mapping records which life wins a repeated key, and a test holds the map to the lives in both directions. |
| A commit is reduced to a set of identities before anything is counted | The set is what makes `A ↔ A` impossible rather than filtered, and it is what `large_commit_limit` is compared against — a rename names two paths and is one identity, so a 100-file commit is 100 identities, not 101 rows. A hand-made commit with two rows for one path pins it (no repository produces that input, which is exactly why the test builds it by hand). |
| `min_shared` hides rows and nothing else | The score of a shown pair is computed the same way at any threshold, and `hidden_pairs` carries the count of what the filter removed, so a filter never reads as "there was nothing". The fixture makes the two sides visible: at the default `a.py` shows one pair and hides one; at `min_shared=1` it shows both. |
| `cochange --json` holds a `files` list, even for one file | The design's §14 shows a single object, but a name can belong to two lives and the JSON shape must not depend on which repository it was pointed at — the same reason `file --json` wraps its object. Each entry's `path` is the life's *current* name (the `file` command's rule), and the queried name sits at the top as `path`; `analyzed_commits`, `large_commits_excluded` and `hidden_pairs` travel beside the rows so a program can verify a score by division and can tell "none found" from "none shown". |
| The co-change table has no prose column | The frozen ordering already puts the strongest pair first, so a word like "often" would be the tool's reading of a number the reader can see. The block prints the denominator above the rows and one closing sentence about what the score is not; the rest is arithmetic. `--limit` and `--all` behave as they do everywhere else, and the hidden-row count is printed rather than implied. |
| The command's two knobs are the definition's two parameters, nothing more | `--min-shared` and `--large-commit-limit` are the only options beyond the shared `--limit`/`--all`/`--json`. Both are `min=1`, so a value that would silently mean something else is refused by the command line before the analysis runs. No `--reverse`, no `--alive-only`: the freeze's §7 and §14 say why. |
| `files` is an inventory; `hotspots` is a ranking | **The user's decision**, after reading what the two commands actually were — the same call, the same order, the same bytes, differing only in how the terminal drew them. `files` now lists every file the history contained, deleted ones included, ordered by path; `hotspots` keeps ranking living files by commits. The reason the split is worth having: a hotspot is a *place*, so a gone file is not one, while an inventory is *what exists and existed*, so hiding the dead makes it incomplete. The fixture makes the difference visible — the lifecycle repository's busiest file (4 commits) was absent from both commands and is now in the inventory. |
| The deleted files appear in `files` by default, and `hotspots` gets no `--deleted` | Also the user's call. A list that silently drops part of what it lists is not an inventory, and if both commands could bring the dead back they would drift into being the same thing again — which is what this decision exists to end. |
| The two JSON shapes differ, and only `files` moved | `hotspots --json` keeps the bytes it had, because its consumers are the ones that must not have to change and its `state` would always read `alive`. `files --json` carries `state` and every row — `--limit` is a terminal convenience, and a program that asked for the inventory asked for all of it. The byte-identical contract was a consequence of the two commands being one command; once they differ, identical bytes would mean the schema was hiding a difference the command line shows. A test now pins the difference and another pins that `hotspots` did not move. |
| `files` ends by saying what a deleted row means | The sentence (`DELETED_FILES_NOTE`) is the whole reason the command is allowed to show dead files: a list of them invites exactly one misreading — that they are gone from the working tree — and the command answers it in as many words. It is prose for a reader, so it is not in the JSON; `state` says the same thing to a program. |
| The index set is unchanged, and each of the three axes was measured | **The user's Unit 17 question — read performance vs write amplification vs database size — answered for all four declared indexes plus one candidate** (`benchmarks/index_benchmark.py`: every read the tool issues, timed against a vacuumed control with one index changed; the index's own size on disk; what the write pays to maintain it; and what it costs to build again over a finished table). *Measured at 50,000 commits*: `commit_parents_parent` and `commit_files_path` are in **no plan at all** — the first is never queried by parent, the second is the index `find_commits_touching`'s own docstring says it cannot use — and they cost 8.1 MB and are maintained on every `analyze`, whose whole write is 4.9 s. Dropping them would save that and cost a schema version bump, which rebuilds every existing database; they stay, with the numbers in the audit table rather than a decision nobody can re-derive. |
| No index was added, and the one that looked expensive was the replay's row order | `commits(committed_epoch)` was tried and not added: its read effect is inside the harness's noise, and the plan it produces replaces `timeline`'s scan-and-sort with a scan of the index, which for a `SELECT *` is the same rows read through a second structure. The other half is a correction: the audit first said maintaining `definition_versions_qualname` through a write cost 65.2 s against 1.1 s to build it afterwards — a 59× gap that argued for dropping it around the write — and an A/B on the pass's own path put the two level (12.4 s against 12.9 s at 10,000 commits, 103.9 s against 103.2 s at 50,000, two interleaved runs each way and the sign flipping between scales). The replay wrote by commit and the pass writes by file; a `(path, ...)` index is an append in one order and a scatter in the other. Trap 32 has the shape, and the change was reverted rather than kept on a number that did not survive. |
| The two indexes on the AST tables are left alone during the write | Follows from the row above: an index is maintained row by row, so it is worth asking whether taking it out for a bulk write pays — and here it does not, because the walk writes a file's versions one after another, which is the order those b-trees keep their keys in. `ast_pass.py` and `storage.py` both say so where a later session would otherwise try it again. |
| The version is 0.3.1, not 0.4.0 | The tree contains a command the v0.3.0 tag does not have, so it cannot call itself 0.3.0 — and it cannot be 0.4.0 either, because the user's own vocabulary reserves v0.4 for the AI layer, which starts from a new design freeze. v0.3.x is the deterministic layer, and this is its last release. |
| A release tag names a commit that passed CI on every job | The v0.3.0 tag had to be moved once, because it was made before CI finished and the first push failed on the Ubuntu 3.13 runner. The order is therefore fixed and written down in `docs/v0.3-final-state.md`: commit, push, wait for all four jobs **on that commit**, then tag, then the release page. A tag that moves is a tag nobody can trust to name a tested tree. |
| `commit --json` is marked, not built | The audit found both READMEs promising JSON for every reading command while `commit` has none. Adding one during a freeze would invent a JSON shape at the moment the surface is supposed to stop changing; the honest fix is to say what is true and record the gap, which is what the READMEs, the final-state document and *Next* now do. |
| The READMEs' performance tables are the current measurements | They had been measured on the pre-Unit-8 fixture, which no longer exists, so a reader running the documented command would get different numbers with no way to tell which was wrong. The tables now come from `benchmarks/benchmark.py` as it stands, at four scales; PROGRESS keeps the older tables with the note that explains why they moved. |
| The READMEs' console blocks are a test, not a release step | **The user's call**, after the v0.3 release showed that a block can drift silently: the release-time checker was a throwaway script, and it let three stale SHAs through because it compared against values no fixture produced. `tests/test_readme.py` now replays every block in both READMEs — 44 of them — against five fixture states. It stays deliberately small: it checks the bytes a reader sees and parses every `--json` block, and it does **not** test the behaviour behind the examples, which have their own tests. The line it holds is "catch what drifts", not "cover the tool twice". |
| A README block is replayed against the state its own text names | The blocks are written against five states of the fixtures — analyzed, analyzed-and-read, stale, the separation demo's seven commits, and the broken file — and the routing reads which one from the block (`broken.py`, "stops at", "no version was stored", and whether it reads the AST layer). The database is put back to that state **once per block**, because commands write: without the restore, the `ast` block left the database read and a later block saw `0 parsed, 6 reused` where its text says `6 parsed, 0 reused`. |
| A block's run is made to look like the documentation's | Four things differ between a run and a block and all four are handled rather than tolerated: the fixture's path and the database's name (a digest of that path) are substituted back to the documented ones, in both plain and JSON-escaped spellings; `COLUMNS=110` is set, because Rich has no terminal under pytest and would otherwise truncate every message column at 80; the comparison collapses runs of spaces and treats a horizontal rule as one character, because the tables pad to whatever width they are given and the blocks were written across more than one; and a block may elide its middle with `...`, which matches any run of lines between the parts it does show. Everything else is compared as written, so a changed word, number or key still fails. |
| `parsed_at_version` names the producer, not just the interpreter | **The user's Unit 16 condition**, and the one part of it the code did not already meet: reuse checked the content and the Python, but nothing recorded the *analyzer*, so a change to how a definition is rendered, fingerprinted or compared would have reused rows the current code would not have written. It is now `3.13.5+1` — interpreter plus `ANALYZER_VERSION` — and the reuse rule compares it whole. The column keeps its name because it is still one value saying who produced a row; a database written by an older analyzer re-reads instead of reusing, which is the intended cost of changing the rule. |
| The stored definitions are read with one query, not one per version | The item Unit 8 left open, and Unit 16 measured it: 250,195 per-version queries cost 9.0 s against 6.5 s for one query over the whole table. `read_all_definition_versions` returns them grouped by version and ordered by position, so the *n*-th-occurrence rule still counts in the same order, and a test holds the bulk reader and the per-version one to each other so the two cannot drift. |
| The pass writes a version only when what it would write differs from what is stored | **Unit 17's fix**, and the cost Unit 16's profile pointed at: 73 s of a 112 s second pass at 50,000 commits was `write_ast_batch` deleting and re-inserting 750,585 rows that were already correct. A reused version is now compared — its own row and every definition row — and skipped when the two agree, so a warm pass writes nothing and a grown pass writes only what the new commits brought. *Measured at 10,000 commits*: warm 23.0 s → 10.0 s, having written 0 versions instead of 50,000; grown 23.9 s → 9.0 s, having written 500 instead of 50,500. *At 50,000*: warm 131.6 s → 46.3 s, grown 138.3 s → 46.8 s. The cold pass is unchanged, which is the check that nothing else moved: it has nothing to reuse. |
| The skip is a comparison, not an assumption | `change_type` is derived against the version *before* this one, so a history that moved under a stored row — a rename that no longer follows the same chain, a threshold that changed — moves the comparison without moving this version's own bytes. Comparing what would be written against what is stored is what makes skipping equivalent to writing, and a version whose rows differ is still written. `tests/test_ast_pass.py` corrupts a stored `change_type` and asserts the pass puts it back; a separate check runs the whole 50,000-commit database twice, once with the skip forced off, and compares both tables with `EXCEPT` in both directions. |
| A blind spot is a version of the file like any other | `FileHistory.versions` holds the unreadable versions in the order they happened, beside the readable ones, so a reader can see where the file went dark and for how long. The first draft kept them only in the pending gap, which left them out of the file's own history. |
| The gap is carried by the event that spans it, and by the life that has no ending | The user's §5.7: a deletion derived across a parse failure must name the versions it was derived across, and a definition whose file's latest versions are unreadable must not be given a deletion at all. The event carries the first; `DefinitionLife.unresolved` carries the second. |
| The *n*-th definition of a name continues the *n*-th life | The same rule the pass compares by, and the only one available when one file defines one name twice. `occurrence` on the life is that index, 0 for nearly every name; without it, two lives with one name would be two records nothing could tell apart. |
| One shared tuple of blind spots per step | Every event of a step gets the same tuple object. A file with a long gap and many definitions would otherwise hold one copy of the gap per definition, and a gap is one fact about the file. |
| The command's shape is `structure <path> --history` | **The user chose it** over a fourth command, which is the answer to the question the freeze §8 left open. The other half of `structure` is a later unit, so until it exists the command without `--history` says that and exits 1 — printing a plausible-looking answer for a question it cannot answer is the one thing worse than refusing. |
| The unreadable versions are listed in the header | A gap that no event happened to span — every definition unchanged across it — would otherwise leave no trace in the output at all, and the reader would take a complete-looking history for a complete one. The reason is printed with the commit, so a parse failure and a file that was not in the tree are told apart on sight. |
| The gap is said on the line *under* the event | The event line keeps one shape (change, moment, commit, line) and the note carries the part that cannot be dated: "at some point after X; Y could not be read, so when it went is not known". Putting it on the same line made every gapped event a different width, and the note reads as a footnote to the line above it. |
| A creation with nothing readable before it says "first seen here" | `created` would claim it was born at the first version that showed it, and with the file's earlier versions unread that is exactly the certainty the unit exists to avoid. It is the same rule as §5.7, applied to the other end of a life. |
| The rendering is its own module, named after the command | `definition_history` is a derivation over facts and imports no terminal; `structure` is what a reader sees and holds both halves of the command. `file.py` and `timeline.py` already split the two jobs this way. |
| The query reads `definition_versions`, and there is no change-log table | **The user's rule.** `change_type` is a cached comparison on a snapshot row, and a deletion is derived from two snapshots by the query layer — the history JSON carries the derived `change` beside the `previous_commit_sha` and `blind_spots` it came from, so nothing has to be taken on trust. |
| The default version is the file's latest stored one | It is what the stored data can answer, it does not refuse a file that has since been deleted, and `--commit <prefix>` picks any other. A commit that did not change the file has no version to show, and the error says what a file version is and points at `--history`. |
| An unreadable version prints a reason, never an empty listing | The user's §7.2: zero definitions would read as a version that held none. The three reasons are told apart — `parse failed` (with the parser's own words), `no version was stored for this commit`, `the file was not in this commit` — and the JSON carries the same distinction as `state` + `reason` + `parse_error`, so no reader infers it from an empty list. |
| `compared_with` and `blind_spots` are shown beside the change types | `change_type` is the pass's cached comparison against the nearest readable version. A version compared across unreadable ones says which version that was; without it, `unchanged` on a version whose file went dark in between reads as "nothing happened". |
| The commit that ends a file's life is one of its versions | A correction, not a feature: until Unit 7 the deletion commit was reachable from no query, so `structure app.py` on a deleted file answered about the last version before it and never said the file was gone. It is a blind spot with the reason `the file was not in this commit`, which is the same fact a deletion in the middle of a life already recorded. |
| The scale fixture's files are real Python | A fixture whose files no parser accepts gives the AST layer nothing to walk, compare or insert, so a benchmark over it would report a fast pass and prove nothing. The v0.2 assertions — 22 lines, one net change, no renames — are unchanged by the content. |
| The pass's phases are measured by wrapping its calls, and every wrapper counts | The pass imports what it uses by name, so replacing the name times the call without touching the pass. The count is the guard: a wrapper that stopped being called would otherwise report a phase that got infinitely fast, which is the one failure a benchmark must not have. |
| The pass is timed three times: cold, warm, and after the history grew | Each is a question a user has — the first run on a repository, the run after nothing changed, and the run after a pull — and one number cannot answer all three. Unit 17 added the third: it appends `--partial` commits, rescans and runs the pass again, so "a hundred new commits at the end of a hundred thousand" is measured instead of extrapolated, and the rows written are counted per run so the three can be compared on what they stored and not only on what they cost. |
| The benchmark prints both row counts, not a growth factor | A change log stores what changed; a snapshot stores every definition of every version, so the ratio is a property of the churn. The audit's 2.15× was this repository; the fixture measures 2.78–2.99×. Printing both lets anyone recompute it for their own history. |
| The method count is prose, the kind column is a fact | A method is a function whose enclosing scope is a class in the same file — derivable from the qualname, and said in the counts line. The table keeps the stored kind word, and the JSON carries `kind` and `qualname` as stored, so no consumer has to know a second vocabulary. |
| The five rules of the structure layer are stated in the README, before the commands | **The user's Unit 9 asked for the release documentation to say five things explicitly**: the AST layer is snapshot-based, a deletion is derived rather than stored, a parse failure is uncertainty rather than a deletion, `change_type` is a cached comparison rather than a semantic change log, and `analyze` and `ast` are separate passes. They are written as five numbered rules ahead of the two commands that demonstrate them, so a reader meets the contract before the output rather than having to infer it from a table. §17 of the design doc records where each one landed. |
| The `analyze`/`ast` separation is shown with a run, not asserted | The README claims `analyze` does not throw the structure away and that a new commit needs `ast` again. Both are shown by the same three-command sequence — analyze, `structure` (which says no version was stored), `ast` (which parses one and reuses six) — over a repository that gains a commit after the pass has run. An assertion nobody can reproduce is worth less than a block a reader can. |
| The READMEs' blocks are replayed against the fixtures, by a script that is not kept | Every console block in both files — 46 of them — was replayed at release time and compared with the command's real output, line for line, which is how the two stale lines below were found. The script lives outside the repository on purpose: it is a release step, not a gate, and it depends on the fixture module and on Windows path handling. Two things bit while writing it, and are worth knowing before writing it again: stderr has to be merged *before* stdout (a note is printed above the block it warns about), and a database path must be replaced before the repository path, because the first begins with the second. |
| `commit_files.change_type` carries a `CHECK`, and the schema version moved with it | The column held git's letter with nothing stopping a word being written into it — the last place where a conclusion could have been stored beside the observation it came from. The constraint allows git's nine documented letters rather than the subset this command line can produce, because a constraint narrower than git's alphabet turns a real observation into a failed analysis, while a wider one only means a letter with no word of its own, which the view already prints as itself. Version 3 → 4 because the DDL is part of the shape the version names and `open_analysis` refuses a database it cannot vouch for; the cost is one re-parse of the structure layer, and no database of version 3 exists outside this machine, since v0.3.0 is not pushed. |
| The two stale lines in the READMEs were fixed with the release | Both timeline blocks drew a 91-character rule over rows from a 110-column run, which draws 108; and the stale-analysis example named a HEAD sha (`a80420c9`) that no documented sequence produces. The rule now matches its rows, and the sha is `cd173ca5`, the one the release's own "add a health check" commit makes — the same commit the separation demo uses, so the two blocks agree. |
| The two design freezes keep their `v0.4-` names | **The user's call**, after the mismatch was pointed out: `docs/v0.4-cochange-design.md` and `docs/v0.4-files-semantics-design.md` describe work that shipped in v0.3.1, and their names say v0.4. Seven references point at them (one of them a test that opens the file by path), so renaming is cheap but not free; the user chose to leave them. The names are a known inaccuracy, not a drift nobody noticed. |
| A CHANGELOG is kept, and it is the fifth place the version is written down | **The user asked for one**, after the freeze audit left the choice open (a file, or a note that the project has none by design). It opens with the newest release, and `tests/test_cli.py` now holds that heading to `__version__`, so a version bump without a section — or a section for a version the tree is not — fails the suite. Its 0.3.1 section is dated 2026-10-01, the release commit's date; the tag follows a green CI run on that commit, which is the rule every release here follows. v0.1.0 gets a section even though it was never tagged, because the first pushed state is part of the history a reader is catching up on. The two READMEs link it from the roadmap, which is where releases are discussed. |

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
12. **A fixture set can be uniformly blind.** Every repository in the suite
    modified a file at most once, so a last-modified time taken from the *first*
    modification rather than the last would have passed on all of them — the
    existing test for it compared against `changes[-1]`, which is the same event
    as `changes[0]` when there is only one. The gap was not found by running the
    suite; it was found by asking the user's matrix "which of these is covered"
    and measuring instead of assuming. The question to ask of a fixture is not
    what it covers but **what could be wrong and still pass**.
13. **`ast.dump` is not portable between interpreters.** 3.11 prints every field
    (`decorator_list=[]`, `posonlyargs=[]`, `type_ignores=[]`); 3.13 omits the
    empty ones. The same source therefore has two different dumps, so a
    fingerprint built on `ast.dump` would mark every definition in a repository
    as modified the first time it was analyzed by another Python. The tool
    renders the structure itself, omitting empty fields, which was measured
    byte-identical on 3.11.16 and 3.13.5 across a 42-sample corpus. Two smaller
    traps came out of the same probe: `ast.parse` raises **`RecursionError`**,
    not `SyntaxError`, for a deeply nested attribute chain (so the failure set is
    not just `SyntaxError`; how deep is another matter, see trap 24), and an
    invalid escape sequence is a
    `DeprecationWarning` on 3.11 but a **`SyntaxWarning`** on 3.13 — which prints
    by default, so a scan over a large repository would flood stderr unless
    parsing suppresses warnings.
14. **Running a probe from the shared temp directory puts that directory on
    `sys.path`.** A throwaway script in `%TEMP%` died with
    `AttributeError: module 'inspect' has no attribute 'get_annotations'`,
    because another tool had left an `inspect.py` there that shadowed the
    standard library — and the stray file printed its own output on import, so
    the failure came with a page of somebody else's diagnostics above it. Probe
    scripts go in a directory of their own, never in a directory other tools
    also write to.
15. **A dead `git cat-file` is met at the write, not at the read.** A test that
    killed the process expected the *read* to fail; what fails first is the
    `stdin.write` of the next request, with `BrokenPipeError` — and the `close()`
    that follows raises `OSError: [Errno 22]` on Windows, because there is no
    longer a pipe to close. Both are handled and both are pinned by a test, but a
    reader written without them reports a broken pipe instead of "git stopped
    answering". Two more protocol facts that cost time to learn: the answer to a
    missing object is the request echoed back with `missing`, and the process
    carries on afterwards; and an empty blob still ends with the separator
    newline, so a reader that reads `size` bytes and stops loses its place on the
    very next request.
16. **`write_commits` no longer means "append these".** It means "the stored
    history is exactly these commits": the ones git no longer has are removed,
    with their file versions and definitions. A test written the old way — write
    one commit, then write another — deletes the first and everything hanging off
    it, and the mistake surfaces as a foreign-key error on the *next* write
    rather than at the line that did it. That is how the first version of the new
    tests failed, and it is worth knowing before writing the pass.
17. **An anchor can appear inside the data you are inserting next to.** Inserting
    a fixture at the first `if __name__ == "__main__":` in `sample_repo.py` put it
    inside `APP_BEFORE`, which is itself a Python module and contains that line —
    the result parsed cleanly and was nonsense, and the import that should have
    failed with a syntax error failed with "cannot import name" instead. Anchor on
    something unique to the place, and when the file is tracked and otherwise
    unchanged, `git checkout -- <file>` is the cheap way back.
18. **An edit anchored on a `def` line eats the docstring under it.** Inserting a
    function *above* another one by matching `def name(...):` plus the docstring's
    first line, and replacing with only the new code plus `def name(...):`,
    deletes that first line and leaves the docstring's body dangling — a syntax
    error, and the third time this exact slip has happened in this project. When
    inserting before a function, anchor on the blank lines *above* it, or include
    the whole docstring in both the old and the new text. `ast.parse` the file
    straight afterwards; it takes a second and catches it every time.
19. **`git merge -X ours` does not resolve a modify/delete conflict.** A fixture
    that deletes a file on one branch and edits it on another needs the file to
    survive the merge, and `-X ours` — the *option* of the default strategy —
    exits 1 with the conflict still there, because it only settles conflicts
    inside a file both sides kept. `-s ours` — the *strategy* — is what keeps our
    whole tree, and it is what the fixture uses. The shape is worth having: it is
    the only way a repository produces a file that is absent from one commit and
    present again in the same life, which is the case `NOT_IN_TREE` exists for.
20. **A derivation is worth printing before it is worth asserting.** Two bugs
    survived a careful reading of `definition_history.py` and died in one run of
    a throwaway script that printed the histories of the new fixtures: blind
    spots were being collected in the pending gap but never added to the file's
    `versions`, on both the parse-failure path and the mid-life-deletion path.
    Both would have shown up as failing assertions eventually — as wrong
    *expectations* in the test file, which is the expensive way to find them.
    Write the fixtures first, print what comes out, then write the assertions.
21. **`min_width` does not stop Rich squeezing a column below it.** The structure
    table's `CHANGE` column came out as `creat` and the decorator column as
    `@staticme…` at 80 columns: the columns' minimums added up to more than the
    console, and Rich takes the shortfall out of the last column rather than
    refusing to render. The fix is arithmetic, not a Rich option — every width
    plus Rich's own padding must add up to *less* than the console, with slack to
    spare. Measured at 80, 100 and 120 columns before it was believed. The other
    half of the same trap: `shorten()` on a column whose real width is only known
    at render time truncates values that would have fitted, so it is applied to
    the one column whose width is computed here and to no other.
22. **A probe written to the shared temp directory breaks the same way every
    time.** Trap 14, hit again in Unit 8: a throwaway script at
    ``%TEMP%/caprobe8.py`` died with ``module 'inspect' has no attribute
    'get_annotations'`` because another tool's ``inspect.py`` is still sitting in
    that directory, and the stray file printed a page of somebody else's
    diagnostics above the traceback. The rule is not "remember trap 14", it is
    "probe scripts go in a directory of their own" — ``mkdir`` first, then write.
23. **A benchmark's own name can collide with the tool's.** ``build_history`` was
    the benchmark's fast-import generator and also the structure view's block
    builder; the import silently won and the failure came out as a missing
    argument three call sites away. Import the tool's names with an alias when
    the benchmark has a word of its own for the same thing.
24. **How deep is too deep belongs to the machine, not to the source.** A test
    asserting that a 5,000-long attribute chain exhausts the parser passed on
    Windows and on Linux 3.11, and failed the first time CI ran it on Linux
    3.13: the runner read the whole thing without complaint. The limit is the C
    stack — measured on 3.13, Windows gives up at the same 2,995 nested
    attributes at a recursion limit of 100 and of 1,000, so
    ``sys.setrecursionlimit`` does not move it — which makes the depth a
    property of the interpreter build and its stack, not of the source. The test
    now deepens the source until the parser gives up (5,000, doubling to
    320,000) and skips rather than fails on a machine that reads every depth.
    The general shape is worth remembering: **a test whose input has to *exceed*
    an implementation limit is testing the platform**, and a second platform is
    where that shows.
25. **A counter over paths is not a counter over identities.** The design unit's
    throwaway measurement script numbered each new file identity as
    `("life", len(seen))`, where `seen` maps *paths* to identities. The
    lifecycle fixture re-creates a path after a delete, and the reused name gave
    its new life the same ordinal as an unrelated one — so the script merged two
    lives, printed a co-change table where `kept.py` appeared to pair with
    `renamed.py`, and every number in it looked plausible. `build_lifecycles`
    avoids this by numbering lives with a separate counter (`len(lives)`), which
    is why the real model was right while the copy of its rule was wrong. The
    general shape: **a derived identifier must come from a counter of the thing
    being identified**, and when a second implementation copies a rule, the rule
    is what has to be copied exactly — or the first implementation is what
    should be called.
26. **A block can be internally inconsistent, and only a run shows it.** The
    timeline block's rule line was 108 characters while its own message row was
    also 108 and *not* truncated — and no terminal width produces both, because
    the rule is one character shorter than the row it underlines at every width
    measured (106→105/105, 110→108/108, 114→108/108). The block had been written
    across more than one terminal and looked right. **A block is a claim about
    one run**, and the only way to check it is to make the run: that is the
    whole argument for the checker, and it is also why the checker must set a
    width rather than inherit one.
27. **The tool resolves the path it prints; a substitution has to use the
    resolved form.** `Path("D:/tmp/x").resolve()` on Windows is
    `D:\tmp\x`, and both `hotspots --json` and `analyze` print the resolved
    form, so a checker substituting the literal argument it passed in finds
    nothing. The same substitution has a second spelling to get right:
    `json.dumps(..., ensure_ascii=False)` is what every JSON output in this tool
    uses, so a path holding non-ASCII characters appears literally rather than
    as `\uXXXX` escapes, and a replacement built with the default
    `ensure_ascii=True` silently misses it.
28. **A fixture that commands write to has to be restorable.** The README
    checker's scenarios share one database per fixture, and `analyze` and `ast`
    both change it — so the `ast` block, which shows the pass doing its work
    (`6 parsed, 0 reused`), left the database read, and a later block that also
    reads the AST layer saw `0 parsed, 6 reused` against its own text. The fix
    is a copy of the database taken once and restored **before each block**,
    not before each command: a block with several commands — the separation demo
    has three — means them to build on one another, which is the only reason the
    demo shows anything.
29. **A benchmark's fixture can make its own measurements meaningless, and the
    numbers still look plausible.** `benchmarks/cochange_benchmark.py` wrote
    files holding `# file N` and `value = M` — valid Python with no definitions
    in it — and its histories are also what the AST pass is measured against. A
    50,000-commit run of it therefore produced 250,195 file versions and **zero
    definition rows**, and every AST timing taken from it timed the walk over
    empty answers: a "second pass" of 53 s that looked like a fine reuse result
    was measuring nothing. The pass reported `parsed=250195, failed=0`, which is
    true and says nothing about whether there was anything to find. **The tell
    is a row count nobody checked**: `definitions=0` was printed by the run and
    read past, and the same generator is shared by two benchmarks, so the
    mistake was in both. When a measurement is the reason for a change, count
    what the fixture actually contains before trusting the seconds.
30. **git leaves the packs it writes read-only, so a fixture cannot be rebuilt.**
    `git fast-import` writes `.pack` and `.idx` files with the read-only
    attribute, and `shutil.rmtree` stops at the first of them on Windows with
    `PermissionError: [WinError 5]` on a file nobody has open — which reads as a
    permission problem rather than as the attribute it is. The benchmark's
    rebuild path had never run before Unit 17: earlier runs either built a
    repository from nothing or reused the one they found, and the grown run's
    extra commits were the first thing to make a rebuild necessary. The fix
    clears the attribute on every entry before deleting; the general shape is
    **a fixture that has to be deleted needs the same care as one that has to be
    built**.
31. **The busiest path was a file that held nothing.** `index_benchmark.py`
    picked the path to query with `SELECT path FROM file_versions GROUP BY path
    ORDER BY COUNT(*) DESC LIMIT 1`, and the fixture writes one file in ten so
    that it cannot be parsed — a file with as many versions as any other and no
    definitions at all. The first path with the highest version count was one of
    those, so the definitions query was timed on an **empty answer** and reported
    0.000 s while the index it was supposed to be testing looked unnecessary. The
    number that exposed it was the *other* column: without the index the same
    query took 0.114 s, and a 200× gap between "finds nothing" and "scans the
    table" is what a real lookup does not look like. Trap 29's rule, one layer
    down: ask what the query actually returned, not just how long it took.
32. **An experiment needs a control, and the control has to be the same shape as
    the probe.** The index experiment made four mistakes of this one kind, and
    each produced a plausible number:
    - Every probe was vacuumed and the baseline was not, so each index was
      charged for the free pages the vacuum reclaimed (a 30 MB error on a 500 MB
      file) and its reads were measured on a different physical layout.
    - The write cost of `commit_files_path` and `commit_parents_parent` was
      measured by replaying the AST tables, which those indexes are not on: their
      ±5% "saving" was the noise floor of a measurement that could not have
      detected them at all.
    - The two replays were run as one, so an index on the git facts was compared
      against a write that never touches its table.
    - The replay wrote the rows in **storage** order — by commit, then path —
      while the pass writes them in **life** order, one file's versions one after
      another. The same rows arrive at an index's keys in a different order in
      each, and a b-tree fed in its own order is appended to while one fed in
      another order is scattered over. The first version of the table said
      maintaining `definition_versions_qualname` through a write cost 65.2 s
      against 1.1 s to build it afterwards — a 59× difference that argued for
      dropping the index around the write — and an A/B on the pass's own path
      found the two within 1% of each other (12.4 s against 12.5 s at 10,000
      commits). **The measurement was of the order, not of the index.**
    The fix is a control — the same copy, the same vacuum, no index changed — one
    replay per set of tables, and the replay in the order the real writer uses.
    The tell is the same each time: a column that moved when the thing it measures
    was not in play.
33. **A ctypes call without `argtypes` fails silently, and the failure reads as a
    machine fact.** `GetProcessMemoryInfo` was bound with
    `ctypes.windll.psapi.GetProcessMemoryInfo` and no prototype: ctypes passed
    the process handle as a C int, the call returned 0, and the benchmark
    reported `-` for memory — "this machine has no memory information" rather
    than "this binding is wrong". Setting `argtypes` and `restype` made the same
    call return 14 MB. The shape is the same as trap 3: a function that reports
    failure through its return value, called from a language that does not check
    it.
34. **A checker that replays the blocks does not check the prose, and the prose is
    where the drift was.** Every console block in both READMEs is replayed against
    the fixtures and every one of them passed while the sentences around them were
    wrong in three places: "eight commands" when the CLI had nine, "a second `ast`
    costs 374s against 405s" when Unit 17 had made it 110s against 395s, and "every
    command that reads the history takes `--json`" when `commit` has none. A block
    is a claim about one run and a machine can hold it; a sentence is a claim about
    the tool, and a machine can only hold the parts of it that are comparable to
    something else in the tree. Unit 18 added one such comparison — the set of
    registered commands against the set of documented ones, which is what the
    eight-versus-nine drift needed — and found the other two by reading. The
    general shape: **when a document is checked mechanically, ask what the check
    cannot see**, and put the answer in the same document so the next reader knows
    which half is held.

## Environment notes

- **Pushing works over SSH on port 443**, through a repository deploy key and the
  host alias `codearchaeology`. `github.com:443` is blocked and the `ghfast.top`
  proxy that serves fetches does not support pushes.
- **`uv run --python 3.11` rebuilds `.venv` for that interpreter.** Running a
  cross-version probe therefore *removes* the project's 3.13 environment and
  leaves a 3.11 one behind, until the next plain `uv run` puts it back. Nothing
  is lost and the reinstall takes under a second, but a session that checks
  `sys.version` inside `.venv` afterwards will be surprised by it.
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
- **The design freeze's measurements live in `D:/tmp/coh/`**, and nothing
  depends on them being there. `gen_tail.py` regenerates the heavy-tailed
  fifty-thousand-commit history; the databases beside it are the ones the
  numbers in `docs/v0.4-cochange-design.md` were read from; the fixture
  repositories are rebuilt by `tests/sample_repo.py`. The quoted numbers are
  from CPython 3.13.5 on 2026-09-30, and Unit 11's implementation is wrong until
  it agrees with them or explains the difference.

## Next

1. **Co-change is complete and hardened.** `docs/v0.4-cochange-design.md` is the
   frozen definition (with §18 recording what the building changed);
   `src/codearchaeology/cochange.py` implements it; `archaeology cochange <file>`
   exposes it. Tests: 30 in `tests/test_cochange.py` (the analysis and the
   boundaries) and 29 in `tests/test_cochange_cli.py` (the command, both output
   shapes, and the hand-computed contract), 462 total, green on 3.11 and 3.13.
   Unit 13 added the hardening cases — a 4,000-file commit, the merge's absence
   from every commit set, a repeated history, a thousand unrelated files, and the
   end-to-end contract against the hand-computed fixture — plus
   `benchmarks/cochange_benchmark.py` and the baseline table in the Performance
   section. The implementation agrees with the freeze's own measurements on the
   50,000-commit history exactly (`pkg0/mod0.py`: 228 analyzed, 20 excluded, top
   pair `pkg0/mod1.py` shared 45 at 0.197); one figure moved and §18 says why
   (the pass costs ~1.4 s, not ~0.6 s, because `build_lifecycles` builds every
   event object).
   **What is deliberately not here**: no storage table, no `--reverse`, no
   `--alive-only`, no Jaccard — each rejected in the freeze with a reason. The
   two READMEs document the command now (Unit 18), and its blocks are replayed by
   `tests/test_readme.py`.
2. **`files` and `hotspots` are two different questions now.** The user chose the
   split and answered the four design questions: `files` is the inventory (every
   file the history contained, deleted ones included, ordered by path), and
   `hotspots` stays the ranking of living files. The freeze is
   `docs/v0.4-files-semantics-design.md`, §10 records what the building changed,
   and the decision rows below carry the reasoning. **Closed** — this item used
   to ask whether one of the two should become something else, and it did.
3. **The README checker is built; the benchmark is still hand-run.** Every
   console block in both READMEs is now replayed by `tests/test_readme.py` —
   44 blocks, five fixture states, green on 3.11 and 3.13 — so the half of this
   item that mattered is closed. The decision table's "the blocks are a
   documented contract" row is what it was built from, and the traps it cost are
   recorded below. **The benchmark half stays open on purpose**: nothing runs
   `benchmarks/benchmark.py` or `benchmarks/cochange_benchmark.py`, and a smoke
   run at a few hundred commits would close it cheaply if the user wants it.
   The checker is also the answer to "does a README block drift" — it caught
   three stale SHAs the moment it first ran, which the release-time script had
   let through by comparing against values no fixture produced.
4. Optional and unasked: a GitHub Release for v0.2.0, whose tag carries notes but
   has no release page (v0.3.0 has one); `.gitattributes` to pin LF; clearing the
   three junk databases in the local cache that point at deleted temp
   directories.
5. **The pass no longer writes what it already stored.** Unit 17's fix is in and
   measured: a warm pass writes nothing and a grown pass writes only the new
   commits, at every scale from 10,000 to 200,000 commits (the tables are in the
   Performance section). **Closed** — this item used to be the rewrite.
   What makes it safe rather than hopeful is the comparison in `_already_stored`:
   a version whose rows differ from what would be written is still written, and
   `D:/tmp/u17/verify_equivalence.py` runs a whole 50,000-commit database twice,
   once with the skip forced off, and compares both tables with `EXCEPT` in both
   directions.
6. **What the pass still pays, and the two ways to cut it.** At 200,000 commits a
   warm pass is 304s: 112s of git reads, 134s of stored definitions read back,
   and the rest the walk and the comparison. Neither candidate is built:
   - **`git cat-file --batch-check`** returns a blob's id without its contents,
     which is all the reuse test needs. It is a second process and a second
     protocol, and the round trips stay either way, so what it would save is the
     transfer — worth measuring before building, not after.
   - **Reading the stored rows a file at a time rather than all at once.** The
     bulk read holds 5.4 million definition records in one dict because the walk
     may ask for any version; a walk that went life by life would hold one file's
     rows and let the rest go. That is the memory fix (4.35 GB at 200,000 commits)
     and probably the time fix too — the definitions read takes six times as long
     at 200,000 as at 100,000 for twice the rows, and the candidate explanation is
     allocation and paging rather than the query, which is **not established**.
     It is a restructuring of the walk, not a parameter, so it is a unit of its
     own.
7. **The rescan writes the whole history on every `analyze`.** 85% of it at
   200,000 commits — 35.06s of 41.02s — is `write_commits` deleting and
   re-inserting 1,000,500 `commit_files` rows git already reported, whether or not
   anything changed. Skipping the commits already stored is the same argument as
   the pass's (a sha fixes a commit, so a stored row cannot go stale), with one
   piece missing: the pass's argument works because `parsed_at_version` records
   *who produced* a row, and the git facts have no such marker. An extractor
   version in `meta` is that piece; without it, skipping would freeze rows written
   by an older reading of git's output.
8. **The two READMEs document all nine commands now**, and a test holds the two
   sets to each other (`tests/test_readme.py::test_every_registered_command_is_documented_in_both_readmes`).
   **Closed** — this item used to be the eight-versus-nine drift, and the test is
   what keeps it closed.
9. **The index audit is in the Performance section, with the decision it led to.**
   Four declared indexes were measured for what they buy and what they cost; two
   of them appear in no query plan at all. The numbers and the reasoning are in
   the row that records the decision.
10. **`commit` has no JSON form**, and both READMEs now say so. The shape is
    implied by the v0.2 JSON decisions — the same facts as the block, under the
    names the other outputs use — and the work is small: a `--json` option and an
    object builder beside `commit.py`'s block, plus a replayed README block. It is
    marked rather than built because v0.3.x is frozen and the JSON surface is part
    of what froze.
11. **The release is under way**: the date is in `CHANGELOG.md` and the tree is
    committed. What is left is the tag `v0.3.1` and the GitHub Release, after the
    push and a green CI run on that commit — the order is the point and
    `docs/v0.3-final-state.md` records it.

## How to verify

```console
$ uv run pytest                 # 473 tests, on 3.13
$ uv run --python 3.11 pytest   # the same suite, on the declared floor
$ uv run python benchmarks/benchmark.py --commits 10000 50000
$ git push origin main          # over SSH, see above
$ gh run list --limit 1         # then gh run watch <id>
```

A database written by an older schema version rebuilds itself on the next
`analyze`; there is nothing to clean up by hand.

A release is three things beyond the code: the version in the four places the
suite compares, the roadmap row, and both READMEs. The READMEs' console blocks
are replayed against the fixtures rather than trusted — 50 of them now, and every
registered command is required to have a section — and the way to do that is in
the decisions table above.

**The order of a release is part of the release**: commit, push, wait for all four
CI jobs on that commit, then tag, then the release page. `v0.3.0`'s tag had to be
moved once because it was made first, and a tag that moves is a tag nobody can
trust to name a tested tree.
