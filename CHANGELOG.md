# Changelog

Every release of CodeArchaeology and what it changed. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A version here is a Git tag and a GitHub Release — the project is not on PyPI, so
there is no `pip install` to go with one. The date is the release commit's date.
v0.1.0 was never tagged; its section records the first state that was pushed.

## [0.5.0] - 2026-10-07

The v0.5 Developer Memory layer: what people know about the project, kept beside
the evidence and never treated as it. What it is and what holds it is
`docs/v0.5-final-state.md`; the definition is `docs/v0.5-design.md` §3, and the
five rules that keep memory out of the evidence layer are §13.2.

### Added

- **`archaeology memory`** — a group of six acts, and the last of the sixteen
  commands: `create` (admit a statement about one subject, with whatever evidence
  it was made from), `list`, `show`, `supersede` (replace it, linking both ways),
  `invalidate` (end it with a reason) and `adopt` (say that a path memories name
  is this project at a new one). None of them needs a model, which is Core First
  made visible: a memory is written, listed and read back with nothing
  configured.
- **The memory store** — two tables beside the evidence in the same SQLite file,
  with their own stamp (`memory_schema_version = "1"`; the evidence schema stays
  at 4). A memory is a UUID addressed by prefix, its subject is structured
  (repository, path, definition, commit) because it is looked up by, and a
  citation stays the v0.4 form string because it is compared for equality.
- **Provenance that is checked and never repaired** — a subject and up to six
  kinds of citation (commit, file, definition, range, co-change, absence) are
  held against the stored history when the memory is admitted and re-checked
  every time it is read, and the three states — resolves, no longer resolves,
  not checked — are rendered differently. There is no foreign key into the
  evidence tables on purpose: a cascade would delete memories and a plain key
  would make `analyze` fail.
- **The lifecycle** — active, superseded and invalidated; both endings are
  terminal, there is no delete and no in-place edit, and a rule that comes back
  is a new memory.
- **`explain` reads them** — the memories related to a commit are shown in their
  own labelled section, in the offline block and in the model's prompt, split
  into *in force at this commit* and *related, not provably in force*, capped at
  five with every dropped count stated. In `explain --json` the section is a
  sibling of `evidence` and absent when empty, so a commit with no related
  memories prints exactly what v0.4 printed. A model's answer may relate itself
  to a memory by id, checked against the section it was shown, and the stored
  statement is printed verbatim.
- **`--since-commit` and `--since-date`** on `create` and `supersede` — the
  author's claim about when a statement started holding, optional by design
  because absent means unknown and never "from the beginning".
- **`benchmarks/memory_benchmark.py`**, and `tests/test_memory*.py` — the store
  measured from ten to a hundred thousand memories, and the boundary suite that
  holds the phase's real risk: every evidence command is run with the memory
  store made unreadable, every reading command is compared byte for byte against
  a database with no memories in it, and a memory's id is refused as evidence of
  every kind.

### Changed

- **The database is no longer only a cache.** Every evidence row can be rebuilt
  by reading git again; a memory row cannot. `analyze`, `ast`, `clear_history`
  and a schema rebuild therefore preserve memory — the memory tables are
  deliberately not on the list of tables a rebuild drops — and the corruption
  sentence no longer says to delete the file, because that advice now destroys
  knowledge. Both READMEs state the exception wherever the database is
  described.
- **A memory is never evidence.** The validator's semantic pass refuses a
  memory's id as a citation of any kind without being told what a memory is, the
  provider imports nothing from the package so a model cannot reach the store,
  and only `cli.py` calls a writing act.

### Fixed

- **`memory list` was linear** — it read every memory in the store to print
  twenty of them. The read now stops at the slice, the count behind it is a
  query of its own, and the child rows are fetched by the ids that were read:
  78ms at a hundred thousand memories, where it was 1.36s.
- **A date that does not exist is refused** — `--since-date 2024-13-45` passed a
  shape check and would have been compared by the temporal rule as if it named a
  day.
- **A stamp with no tables read as an empty store** — a database whose memory
  tables were dropped by hand still claimed the current stamp and listed zero
  memories to somebody who had written a hundred. It is now its own refusal: the
  store is asked to account for what it claims rather than read as empty.
- **Three tests read a usage error's colour instead of its words.** Typer renders
  a usage error through Rich, which styles the option name inside the sentence,
  so with colour on `No such option: --json` is not a substring of the bytes the
  command wrote. All three passed on the machine they were written on and were
  red on all four CI jobs; `tests/cli_text.py` now reads the captured output with
  the escape sequences taken out.

## [0.4.0] - 2026-10-06

The v0.4 AI layer: an interpretation of the evidence, and never a record of what
happened. What it is and what holds it is `docs/v0.4-final-state.md`; the contract
itself is `docs/v0.4-problem-definition.md` §12.

### Added

- **`archaeology explain <commit>`** — the tenth command, and the only one that
  talks to a model. It gathers the evidence for one commit into a bundle and has
  a model write an answer from it. With no model configured it prints the
  evidence itself and exits 0, which is Core First rather than a fallback: the
  analysis is complete without a model, and the model is a layer on top.
- **The evidence bundle** — the commit, the files it touched *and where in each
  the change landed* (read out of git, the one thing the database does not hold),
  each file's life, the definitions this commit created, modified or ended, what
  usually changes alongside those files, the earlier commits that touched them,
  and what could not be read. Deterministic, with no model anywhere near it.
- **The explanation structure** — `summary`, `observed_changes`, `evidence`,
  `possible_reasons` and `uncertainty`, with Observed, Possible and Unknown kept
  apart by the structure rather than by the wording, so a model that blurs them
  has nowhere to put the result.
- **Three validation passes** — the text is a JSON object, it has the shape that
  was asked for and only that shape, and every citation names something the
  bundle holds. A broken answer is asked for once more and then refused whole:
  nothing of it is shown, because half an explanation with a note is worse than
  none. `confidence` is counted by the tool from what a candidate rests on and
  never reported by the model.
- **`explain --json`** — the same answer for a program. `state` says which kind
  of answer it is (`explained` or `evidence_only`), the evidence travels beside
  the explanation so an answer can be checked against its input, and `selection`
  is added when the model's view was reduced.
- **An OpenAI-compatible provider** — one adapter for OpenAI, DeepSeek, Ollama,
  vLLM and LM Studio, over the standard library only. The key is read from the
  environment and never from a flag, the deadline is wall-clock around the whole
  call rather than a socket timeout, retries cover the failures repeating can fix,
  and `provider.py` is the only module in the tool that reaches a network.
- **`benchmarks/context_benchmark.py`** — the bundle's size, section by section,
  at six shapes a commit can be large along.
- **`tests/test_boundaries.py`, `tests/test_offline.py`, `tests/test_selection.py`**
  — the seven ways a model can be wrong (each with the honest verdict: refused,
  marked, or not catchable), the core's independence from the network held both
  structurally and by running every reading command with no socket, and the
  context budget.

### Changed

- **A commit too large to send whole is reduced for the model, and only for the
  model.** The largest file changes and definitions are kept, with each file's
  life and its two most recent earlier commits, and the first few spans of each
  change; every row that was dropped is counted in a `selection` block inside the
  prompt, and a file whose spans were cut says so on its own row. The evidence
  itself — what the command prints with no model, and what `--json` carries — is
  unchanged, and the answer is checked against what the model was actually shown.
  Measured: a thousand-file commit is about 373,000 estimated tokens of evidence
  and about 7,300 of view.
- **The output says what it is.** The block opens by stating that it is an
  interpretation of the evidence and not a record of what happened, with the
  citations checked and the sentences around them not; the same statement is the
  seventh README principle and part of `explain --help`. The contract is
  `docs/v0.4-problem-definition.md` §12.
- The refusal past `CODEARCHAEOLOGY_AI_MAX_CONTEXT_TOKENS` now says the evidence
  was already reduced, because by then it has been: what is left over the limit
  is a limit set too low for the model rather than a commit that is too large.

### Fixed

- A mistyped `CODEARCHAEOLOGY_AI_BASE_URL` — a host written the way hosts are
  written everywhere else, without a scheme — reached the user as a Python
  traceback from inside `urllib`. It is now a sentence naming the variable and
  showing the shape it needs.
- A deadline of `0` is a typo rather than a setting, and it made every call fail
  with "did not finish within 0 seconds"; it falls back to the default now, the
  way a non-numeric value already did. Retries keep their zero, because no
  retries is a real choice.
- A response was read to the end however large it was; past a megabyte it is
  refused instead, because an endpoint that sends that much is not answering with
  a completion.
- Both READMEs' module listings had never been updated for the v0.4 modules and
  still said the command line had nine commands.

## [0.3.1] - 2026-10-01

The v0.3.x release: the deterministic evidence layer, frozen.

### Added

- **`archaeology cochange <file>`** — the files that change in the same commits
  as this one. The score is the share of *this* file's analyzed commits that
  touched the other file, so it is directional and not symmetric. The sample is
  the file's whole life, so commits under an earlier name count, and a name that
  belonged to two files answers for both. `--min-shared` (2 by default) hides
  pairs with too little evidence, `--large-commit-limit` (100 by default) leaves
  bulk commits out of every sample, and both counts are printed, so "none found"
  and "none shown" stay two different answers. `--json` included.
- `benchmarks/cochange_benchmark.py` and `benchmarks/index_benchmark.py` — the
  co-change analysis as the history grows, and an audit of what each database
  index buys on the read side and costs on the write side.

### Changed

- **`hotspots` and `files` are two different questions now.** They used to be the
  same ranking with the same JSON. `hotspots` ranks living files by how many
  commits touched them, because a hotspot is a place and a deleted file is not
  one. `files` is an inventory of everything the history contains, deleted files
  included and marked, ordered by path the way a directory listing is.
- **A second `ast` run no longer rewrites what it already stored.** A version
  whose stored bytes and producer are the ones it is about to work out is
  skipped, and it is skipped only if the rows that are stored are the rows that
  would be written. Measured at a hundred thousand commits: 110s against 395s for
  a cold pass, with nothing written.
- **Every stored row now names the analyzer as well as the interpreter** —
  `3.13.5+1` rather than `3.13.5`. A database written by 0.3.0 therefore has every
  version read again by the next `ast`, once. The point of the marker is that a
  change to how the tool reads code invalidates its own rows instead of mixing
  two producers' answers.
- The READMEs' console blocks are replayed against the fixtures by the test
  suite, and every registered command is required to have a section in both, so
  the examples are a contract rather than a description.

### Fixed

- Both READMEs described eight commands while the command line had nine, and
  co-change had no section at all.
- Both READMEs said a second `ast` costs nearly as much as the first — 374s
  against 405s at a hundred thousand commits. That was true before this release
  and false in it; the tables now carry the measured numbers.
- Both READMEs said every command that reads the history takes `--json`; `commit`
  has none. The sentence names the six that do, and the gap is a known
  limitation.

## [0.3.0] - 2026-09-29

### Added

- **`ast`** — reads every Python file version the stored history holds and stores
  the definitions it contained: classes, functions and methods, with their line
  ranges, decorators and qualified names.
- **`structure <path>`** — one file version's structure, and how each definition
  relates to the version it was compared with. `--commit` picks another version;
  `--history` shows the evolution, the moments each definition was created,
  modified or deleted; `--json` on both faces.
- The five rules the layer is held to: a snapshot is a fact; a deletion is
  derived and never stored; a parse failure is uncertainty and not an empty file;
  `change_type` is a cached comparison against the last readable version; and no
  identity is stored anywhere. Each has a test that fails if it stops being true.
- Schema version 4, which puts a `CHECK` on `commit_files.change_type` so the
  column can hold git's letters and nothing else.

### Changed

- The AST pass is deliberately not part of `analyze`. Measured, it costs far more
  than the git scan, and a user who wants only the git facts must not pay for it.

## [0.2.0] - 2026-09-28

### Added

- **`hotspots`** — the files ranked by how many commits touched them, with the
  warning that frequency is not importance.
- **`files`** and **`file`** — the inventory of what the history contains, and one
  file's life: its names, its dates and its numbers.
- **`--json`** on `hotspots`, `files` and `file`. (`timeline` had it from 0.1.0.)
- A file is an identity across renames, deletes and reuse — its life — and the
  similarity git reported for a rename is stored beside it (schema version 1
  to 2).

### Changed

- The timeline is ordered by parent links rather than by timestamps.
- `analyze` warns on stderr when the clone is shallow, because that makes the
  history a different shape rather than a shorter one.

## [0.1.0] - 2026-09-28

The first state of the project. It was never tagged or released; the three
commands below are what it had.

### Added

- **`analyze`** — reads a repository's history (commits, parents, changed files
  and their line counts) into one SQLite database per repository.
- **`timeline`** — the history, newest commit first, with `--json`.
- **`commit`** — one commit in full: its message and its files.
- The database carries a schema version, and one the tool cannot read is refused
  rather than read.
