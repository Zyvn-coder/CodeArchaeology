# PROGRESS.md

Working notes for whoever picks this up next, including a fresh session of the
agent that has lost its context. Read this before touching anything.

It holds the two things that cannot be recovered by reading the code or
`git log`: **why** each decision was made, and which traps already cost time.
It is not a changelog — `git log` covers what changed and when, so do not copy
that here.

Update it when a decision is made or a trap is found. Nothing else.

**Last updated: 2026-09-29, the day v0.3 was released. The version described
below is v0.3.0.**

## Where the project stands

**v0.3.0 is written and documented.** Unit 9 was the release: both READMEs now
describe all eight commands, the structure layer's five rules are stated in them,
the roadmap row says Done, and the version is 0.3.0 in the four places the
existing test compares. Every console block in both files was replayed against
the fixtures at release time and matched, line for line. What has not happened is
the push and the tag — those are the user's to run, as they were for v0.2.

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
already had `--json` from v0.1; this added it to the three commands v0.2 brought,
so the whole read side of the CLI is now machine-readable.

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
  - **The insert rate keeps falling** with the database size (52k rows/s at ten
    megabytes, 14k at a gigabyte) and the cause is still only a hypothesis: the
    row key begins with a random sha, so the inserts are scattered over a file
    that no longer fits in the page cache.

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
| The benchmark measures four operations, not one | They do not scale alike, and the interesting fact — that two of them cost the same for a reason rather than an accident — is invisible from a single number. A benchmark that measured only the timeline would have shown nothing wrong and told nobody why. |
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
| The pass is timed once, on a database with no AST rows | A second run reuses everything and measures the cache. The re-run is timed separately and reported as its own column, because "what does it cost when nothing changed" is a question of its own. |
| The benchmark prints both row counts, not a growth factor | A change log stores what changed; a snapshot stores every definition of every version, so the ratio is a property of the churn. The audit's 2.15× was this repository; the fixture measures 2.78–2.99×. Printing both lets anyone recompute it for their own history. |
| The method count is prose, the kind column is a fact | A method is a function whose enclosing scope is a class in the same file — derivable from the qualname, and said in the counts line. The table keeps the stored kind word, and the JSON carries `kind` and `qualname` as stored, so no consumer has to know a second vocabulary. |
| The five rules of the structure layer are stated in the README, before the commands | **The user's Unit 9 asked for the release documentation to say five things explicitly**: the AST layer is snapshot-based, a deletion is derived rather than stored, a parse failure is uncertainty rather than a deletion, `change_type` is a cached comparison rather than a semantic change log, and `analyze` and `ast` are separate passes. They are written as five numbered rules ahead of the two commands that demonstrate them, so a reader meets the contract before the output rather than having to infer it from a table. §17 of the design doc records where each one landed. |
| The `analyze`/`ast` separation is shown with a run, not asserted | The README claims `analyze` does not throw the structure away and that a new commit needs `ast` again. Both are shown by the same three-command sequence — analyze, `structure` (which says no version was stored), `ast` (which parses one and reuses six) — over a repository that gains a commit after the pass has run. An assertion nobody can reproduce is worth less than a block a reader can. |
| The READMEs' blocks are replayed against the fixtures, by a script that is not kept | Every console block in both files — 46 of them — was replayed at release time and compared with the command's real output, line for line, which is how the two stale lines below were found. The script lives outside the repository on purpose: it is a release step, not a gate, and it depends on the fixture module and on Windows path handling. Two things bit while writing it, and are worth knowing before writing it again: stderr has to be merged *before* stdout (a note is printed above the block it warns about), and a database path must be replaced before the repository path, because the first begins with the second. |
| `commit_files.change_type` carries a `CHECK`, and the schema version moved with it | The column held git's letter with nothing stopping a word being written into it — the last place where a conclusion could have been stored beside the observation it came from. The constraint allows git's nine documented letters rather than the subset this command line can produce, because a constraint narrower than git's alphabet turns a real observation into a failed analysis, while a wider one only means a letter with no word of its own, which the view already prints as itself. Version 3 → 4 because the DDL is part of the shape the version names and `open_analysis` refuses a database it cannot vouch for; the cost is one re-parse of the structure layer, and no database of version 3 exists outside this machine, since v0.3.0 is not pushed. |
| The two stale lines in the READMEs were fixed with the release | Both timeline blocks drew a 91-character rule over rows from a 110-column run, which draws 108; and the stale-analysis example named a HEAD sha (`a80420c9`) that no documented sequence produces. The rule now matches its rows, and the sha is `cd173ca5`, the one the release's own "add a health check" commit makes — the same commit the separation demo uses, so the two blocks agree. |

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
    not `SyntaxError`, for a 5,000-long attribute chain (so the failure set is
    not just `SyntaxError`), and an invalid escape sequence is a
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

## Next

1. **Co-change**, which the user described as the next thing the commit-file link
   makes possible: which files tend to change together. It has not been designed
   yet, and the percentage has to be pinned down first — of a file's commits, of
   the pair's, or of everything — because the three give different numbers.
2. **v0.3.0 is written but not pushed.** The version, both READMEs, the roadmap
   row and the design doc are in place, and the suite is green on both
   interpreters. The push and the tag are the user's, exactly as they were for
   v0.2.0 — see "How to verify" below for the commands.
3. **`files` and `hotspots` show the same ranking in two shapes**, and now emit
   byte-identical JSON as well. The user asked for both, so both exist. If one of
   them should become something else — an inventory including deleted files, say
   — that is their call.
4. **Two hand-run tools are covered by nothing.** Nothing runs
   `benchmarks/benchmark.py`, and nothing replays the READMEs' console blocks, so
   a rename in the modules either one imports breaks it silently until someone
   uses it. That is a deliberate trade — a test that runs a benchmark or a
   README checker muddies what the suite is for — but it is a trade, not an
   oversight, and a smoke run at a few hundred commits would close the benchmark
   half of it cheaply. The README half is the more valuable of the two now that
   the blocks are a documented contract: the checker described in the decisions
   table is the thing to rebuild, and it is a unit's worth of work if the user
   wants it kept.
5. Optional and unasked: `.gitattributes` to pin LF; clearing the three junk
   databases in the local cache that point at deleted temp directories.
6. **The reuse path re-reads the stored definitions one version at a time.**
   Measured in Unit 8: a second pass over a hundred thousand commits costs 374s
   against 405s for the first, because the five hundred thousand small queries
   replace the parse and the walk almost exactly. Reading them in one query is
   the obvious next measurement, and it is a change to the pass rather than to
   the model, so it was left out of the unit that measured it.

## How to verify

```console
$ uv run pytest                 # 348 tests, on 3.13
$ uv run --python 3.11 pytest   # the same suite, on the declared floor
$ git push origin main          # over SSH, see above
$ gh run list --limit 1         # then gh run watch <id>
```

A database written by an older schema version rebuilds itself on the next
`analyze`; there is nothing to clean up by hand.

A release is three things beyond the code: the version in the four places the
suite compares, the roadmap row, and both READMEs. The READMEs' console blocks
are replayed against the fixtures rather than trusted — 46 of them at v0.3.0 —
and the way to do that is in the decisions table above.
