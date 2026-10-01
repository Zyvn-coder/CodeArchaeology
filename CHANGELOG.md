# Changelog

Every release of CodeArchaeology and what it changed. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A version here is a Git tag and a GitHub Release — the project is not on PyPI, so
there is no `pip install` to go with one. The date is the release commit's date.
v0.1.0 was never tagged; its section records the first state that was pushed.

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
