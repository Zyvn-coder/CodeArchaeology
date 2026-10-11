# The pilot kit — what to print, and what never to show anyone

**This directory is the printable form of the experiment's materials.** The source
of truth stays `docs/v0.5-understanding-experiment.md` (the design, the rubric,
the decision rule) and `docs/v0.5-second-repository-experiment.md` (tqdm's six
questions); the kit is those materials rearranged into pages a facilitator can
hand out and score from. If a source document changes, re-export from it — this
kit was cut from `docs/v0.5-understanding-experiment.md` sha256 `357b735b…c0187`
and `docs/v0.5-second-repository-experiment.md` sha256 `d134bb5c…a15cc9`
(commit `e41b3de`).

## The files, and who may see each one

| File | Whose | Print |
|---|---|---|
| `a-participant-briefing.md` | **the participant's** | one per participant, every session |
| `b-facilitator-script.md` | the facilitator's | one, and read it verbatim |
| `c-answer-sheet.md` | **the participant's** (they write on it) | **six per participant per session** |
| `d1-questions-attrs.md` | **facilitator only — never shown to a participant** | one |
| `d2-questions-tqdm.md` | **facilitator only — never shown to a participant** | one |

**The rule that costs the most to get wrong:** D carries the ground truth and the
scoring anchors. A participant who reads one line of D is no longer a participant.
Print D for yourself, keep it out of the room, and collect every answer sheet
before the debrief.

## The two repositories, and the commits the materials are pinned to

| | attrs (repository 1) | tqdm (repository 2) |
|---|---|---|
| Upstream | `python-attrs/attrs` | `tqdm/tqdm` |
| The checkout the questions were derived and verified on | `644b4e16` (2026-10-06) — this is the derivation clone's HEAD | HEAD `9cf5a12b` (2026-09-11); `5dcb022b` (2026-08-07) is the last commit touching `tqdm/std.py` — the cited files are byte-identical at both (review §1) |
| Questions | Q-A…Q-F (`d1`) | T-1…T-6 (`d2`) |
| Level composition | 2 L1 (Q-A, Q-C), 2 L2 (Q-E, Q-F), 2 L3 (Q-B, Q-D) | 2 L1 (T-1, T-2), 2 L2 (T-3, T-4), 2 L3 (T-5, T-6) |
| Prepare | clone in full, then `analyze` then `ast` (≈1 s / 25 s) | clone in full, check out a pinned commit, then `analyze` then `ast` (≈1 s / 26–30 s) |

**Pin both checkouts and write the SHAs into the session record.** Line numbers in
`d1`/`d2` are as of the checkouts above; a fresh clone at a newer HEAD moves them.

## The session, in one list

1. **Before anyone arrives.** Checkout pinned, `analyze` + `ast` run, `git status`
   clean, `CODEARCHAEOLOGY_AI_*` unset, the browser closed and the network off,
   A and C printed, D on your side of the table.
2. **Briefing (5 min).** Hand out A; read the last two bullets aloud; take
   questions about the rules only.
3. **Orientation (5 min, treatment arm only).** Read the script's quoted paragraph
   word for word. Nothing else.
4. **Six questions (60 min).** One at a time, in the printed order; announce the
   five-minute point; stop at ten minutes and move on.
5. **Debrief (10 min).** The three questions in the script, written down verbatim.
6. **After.** Transcribe the paper sheets; score in a separate pass with D open.
   The Memory block that §6.4 of the design once placed at minute 70 is
   **deferred to a separate study** (decision of 2026-10-09) — do not improvise
   one, and record the deferral in the report.

**A participant does two sessions** — one repository with git alone, the other
with the tool — assigned so that half start with the tool on attrs and half on
tqdm. `b-facilitator-script.md` carries the assignment table.

## What this kit is not

It is not the scoring rubric's substitute: the rubric (2 / 1 / 0 / U, and U
counted as 2 only where the question's own "correctly uncertain" line says
uncertainty is the truth) is in `docs/v0.5-understanding-experiment.md` §7.2, and
the four pre-registered outcomes are §10. It is not the second scorer's pack
either — that is specified in `docs/v0.5-second-repository-review.md` §6 and can
be cut from these files once a scorer exists. **No volunteers have been recruited
and no session has been run: this kit is preparation, and nothing in it is a
result.**
