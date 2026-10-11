# Appendix B — the facilitator's script

**Facilitator's copy. Read the orientation paragraph below word for word. Improvise
nothing: a longer or improvised orientation measures the facilitator, not the tool.**

## Before the session

- **The checkout.** One repository per session, cloned in full (`--filter` and
  `--depth` are both disqualified), checked out at the pinned commit, `git status`
  clean. Then `uv run archaeology analyze <repo>` and `uv run archaeology ast <repo>`
  (≈1 s and ≈25–30 s), with `CODEARCHAEOLOGY_AI_*` unset — the tool must run
  offline. Pins and expected line-number bases:

  | Repository | Pin | Questions |
  |---|---|---|
  | `python-attrs/attrs` | `644b4e16` (2026-10-06) | Q-A…Q-F (`d1-questions-attrs.md`) |
  | `tqdm/tqdm` | `9cf5a12b` (2026-09-11), or `5dcb022b` (2026-08-07) — byte-identical for the cited files | T-1…T-6 (`d2-questions-tqdm.md`) |

- **The paper.** `a-participant-briefing.md` one per participant. `c-answer-sheet.md`
  six sheets per participant per session. This script and the repository's
  `d`-file on your side of the table, **never on theirs**.
- **The environment.** Network off. No recording. The participant works where you
  can see that they are not being helped, without hovering.
- **The arm assignment** (§6.2's counterbalance). One way to satisfy it, for up to
  eight participants:

  | Participant | Session: attrs | Session: tqdm |
  |---|---|---|
  | P1 | git alone | **tool** |
  | P2 | **tool** | git alone |
  | P3 | git alone | **tool** |
  | P4 | **tool** | git alone |
  | P5 | git alone | **tool** |
  | P6 | **tool** | git alone |
  | P7 | git alone | **tool** |
  | P8 | **tool** | git alone |

  With six participants use P1–P6 (three per arm on each repository). The design
  does not prescribe which repository comes first — **whichever you choose, use
  the same order for everyone and write it down.**

## Briefing (5 min)

Hand out `a-participant-briefing.md`. Read the last two bullets aloud. Ask "any
questions about the rules" and nothing else.

## Orientation (5 min, treatment arm only)

Word for word, every time:

> "The tool has ten commands. `analyze` and `ast` have already been run. These
> six read the stored history: `timeline` shows the commits, `hotspots` ranks the
> files that change most, `files` lists everything the history contains, `file`
> shows one file's life including its earlier names, `commit` shows one commit in
> full, `structure` shows a Python file's definitions and how each one changed
> over its life, `cochange` shows which files change together. `--json` is
> available on most of them. That is all of it."

## Questions (60 min)

One at a time, in the printed order. Announce the half-way point at five minutes.
At ten minutes, stop the participant and move on. Do not answer anything about the
content of a question; "I cannot help with that one" is the whole reply.

## Memory sub-experiment — DEFERRED, do not run

The block that §6.4 of the design once placed at minute 70 belongs to a separate
study (review decision, 2026-10-09). Do not improvise one. Record the deferral in
the report; the session ends with the debrief. The session is about 80 minutes.

## Debrief (10 min)

Ask: "which command did you use most", "was there a moment where the tool told you
something you could not have found otherwise", and "what would you have wanted it
to show you". Write the answers down verbatim.

## After every session

Transcribe the paper answer sheets into one file per participant, including the
times and the confidences, and score them in a separate pass with the ground truth
open. Strip the arm from the transcription if a second scorer will see it (the
specification is `docs/v0.5-second-repository-review.md` §6). Record, per
participant: which repository they had already read, and whether they had used
tqdm or attrs before as a *user* — prior familiarity is a real advantage and is
reported rather than controlled.
