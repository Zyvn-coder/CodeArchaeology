# Appendix D2 — the six questions for `tqdm/tqdm`

**FACILITATOR ONLY. NEVER SHOWN TO A PARTICIPANT.**

Checkout: HEAD `9cf5a12b` (2026-09-11), or `5dcb022b` (2026-08-07) — the cited
files are byte-identical at the two commits, so every line number below holds for
either. Line numbers are as of that pair. Re-exported 2026-10-09 with the
review's corrections applied; the calibration commands were re-run then and their
output is what is recorded here (the source document is
`docs/v0.5-second-repository-experiment.md`, sha256 `d134bb5c…a15cc9`; the review
is `docs/v0.5-second-repository-review.md`).

Scoring is §7.2 of the design: **2** correct and evidenced, **1** correct but
unevidenced, **0** wrong, **U** correctly uncertain — and `U` scores 2 only where
the question's own "correctly uncertain" line says uncertainty is the truth. Score
from the written answer alone. Report per question and per level.

**The date convention, before anything else.** **The tool prints commit dates;
`git blame` and a plain `git log` print author dates.** Nine cited commits differ
(T-3's decision is the one most likely to be read aloud: authored 2019-06-05,
committed 2019-06-18 — the tool prints the latter). The anchors below accept
either date where it matters.

| # | Level | Site | What it measures |
|---|---|---|---|
| T-1 | L1 | `tqdm/std.py:687` | instrument control |
| T-2 | L1 | `tqdm/std.py:717` | instrument control |
| T-3 | L2 | `tqdm/std.py:664` | path-shortening |
| T-4 | L2 | `tqdm/std.py:211` | path-shortening — **the negative control** |
| T-5 | L3 | `tqdm/std.py:74` | discriminating |
| T-6 | L3 | `tqdm/std.py:1270` | discriminating |

**Disclose these two facts in the report, whatever the results** (review §4):
T-1 and T-2 are expected to saturate on both arms — that is the instrument control
working, not a null result; and T-3 and T-4 give the treatment arm no information
the control cannot reach in one command, so the discrimination rests on T-5 and
T-6.

---

## T-1 — which bars move when a nested bar finishes — L1, an instrument control

**Site.** `tqdm/std.py:687-715`, `_decr_instances` (decorator at 687, `def` at
688).

**Question as asked.** *"When a bar nested inside another bar finishes, which bars
move? The code moves exactly one bar and its own docstring admits that display
order is not maintained as a result. Why is that the trade, and has it always been
this way?"*

**Ground truth.**

| | |
|---|---|
| The rule | on completion, `close()` calls `self._decr_instances(self)`; that method finds the unfixed bars whose `pos` is at or past `last = (nrows or 20) - 1` and moves **the one with the smallest `pos`** into the vacated row: `inst.clear(nolock=True); inst.pos = abs(instance.pos)` (`std.py:705-715`) |
| Why one and not all | moving every subsequent bar up is what tqdm did up to 4.44.1; it repaints more of the screen, and the docstring states the goal as minimising "screen flicker/blank space" at the cost of ordering |
| When it changed | `bee52fa1 rework decr_instances()`, 2020-03-30 (author and commit dates agree), first release **v4.45.0**; body: "Reduces screen flicker/blank space at the cost of ordering. — don't move displaying (non-overflow) bars upwards — move the first unfixed (`pos >= 0`) overflow (`pos >= nrows`) bar to the current `pos`" |
| The `pos` arithmetic | `pos` is the bar's row; `_get_free_pos` (`std.py:680-685`) returns the lowest row not in use, and a `position=` passed by the user is stored negated (`std.py:1097`) — the `abs()` calls keep the two conventions apart |

**Correct (2).** One bar is moved — the unfixed *overflow* bar with the smallest
`pos`, into the vacated row — and the reason is flicker/blank-space reduction at
the cost of ordering; evidenced by the docstring, the code, or `bee52fa1`.
**Partially correct (1).** The flicker trade-off without the "one bar, and which
one" half, or the rule without any evidence.
**Correctly uncertain.** Not applicable — the repository states the answer.
**Wrong (0).** "All subsequent bars move up" (the pre-4.44.1 behaviour, and the
most likely answer from someone reading the loop without the docstring), or a
claim that the behaviour is accidental.

**Reachability.** Control: read the docstring, or
`git log --oneline --grep=flicker`. Treatment: `structure tqdm/std.py --history`
names the 2020 commit; `commit bee52fa1` prints the body. **What the tool uniquely
adds: nothing** — this is the point of an instrument control.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "flicker|blank space"` over
`*.py` and `*.rst` → **one hit, `tqdm/std.py:694`**, the docstring → the reason is
in the tree. L2 — `git log --oneline --grep=flicker` → `bee52fa` (the same
decision; the tie-break keeps it L1 because the tree states it). L3 —
`git log -S "moved ALL subsequent"` → `bee52fa`; `git blame -L 688,715` →
`bee52fa1c`×12, `5aa5924e2`×5, `6943da2e4`×4, `d7ccbbdab`×3, plus four smaller
attributions (`f9718d3a`×2, `f2246bb60`×1, `48b90f6d3`×1) — **blame lands on the
decision**, so it can never be L3. **Level: L1.**

**Second scorer.** `git show bee52fa1 --stat` and read the body;
`git tag --contains bee52fa` contains `v4.45.0`; confirm the docstring's claim
against `std.py:705-715`.

---

## T-2 — why the library owns a write path — L1, an instrument control

**Site.** `tqdm/std.py:717-759` (`write` at 718, `external_write_mode` below it).

**Question as asked.** *"A progress-bar library ships its own `write()`. Why does
that exist, and what exactly does it do to the bars on screen before and after the
message?"*

**Ground truth.**

| | |
|---|---|
| What it does | it enters `external_write_mode`, which acquires the write lock (unless `nolock`), **clears every live bar that writes to the same file** — where "same file" includes the case that both streams are among `sys.stdout`/`sys.stderr`, because terminals interleave the two — writes the message, and **refreshes the bars it cleared**, releasing the lock in a `finally` |
| Why | so a message appears on its own line instead of being overwritten by the next bar refresh; the method's docstring says "without overlap with bars" and the context manager's says "Disable tqdm within context and refresh tqdm when exits" |
| The odd condition | `inst.fp == fp or all(f in (sys.stdout, sys.stderr) for f in (fp, inst.fp))`, commented in the tree as "because both are mixed in terminal" (`std.py:746-750`) |
| Where it came from | `9c2a524 Added contextmanager to disable tqdm and allow smooth write to stdout` (authored 2017-08-25, committed 2017-09-09), first release **v4.17.0**; `write()` itself dates from `d3c3ad2` (committed 2016-04-25) and gained `nolock` in `da46cd43` (committed 2017-09-23) |
| The safety net nearby | `DisableOnWriteError` (`tqdm/utils.py:166-206`) wraps a stream so a write failing with `errno 5` or a "closed" `ValueError` quietly sets `miniters = inf` — the same problem one layer down |

**Correct (2).** Clears the bars on the same output, writes, refreshes them, all
under the lock, with the stdout/stderr mixing rule named; evidence from the
docstrings, the code, or `9c2a524`.
**Partially correct (1).** "It stops the bars from overwriting your message" with
no mechanism and no evidence.
**Correctly uncertain.** Not applicable.
**Wrong (0).** "It is for logging", "it disables the bar", or any answer that
ignores the refresh.

**Reachability.** Control: read the two docstrings, or
`git log --oneline --grep=overlap`. Treatment: the lifecycle view for
`tqdm.write` (13 events) and `tqdm.external_write_mode` (10). **What the tool
uniquely adds: nothing** — both arms read the same two docstrings.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "without overlap|overlap
with"` → **two hits, `tqdm/std.py:719` and `README.rst:613`** (the README embeds
the docstring) → in the tree. L2 — `git log --oneline --grep=overlap` → `74e6eba
Fix overlap of nested progressbar`, a different, older problem. L3 —
`git log -S external_write_mode -- tqdm/` → `9c2a524`, `da46cd4`, `4c41381`,
`f00036b`; `git blame -L 717,726` → six attributions, none dominant
(`9c2a524ab`×3, `da46cd437`×2, `74ff9929d`×2, `d3c3ad2af`×1, `4119d4aff`×1,
`0e1961cbb`×1); the answer is in the tree regardless. **Level: L1.**

**Second scorer.** Read `std.py:730-759` and check the three phases
(clear → yield → refresh) and the stream condition; `git tag --contains 9c2a524`
→ `v4.17.0`.

---

## T-3 — registration and the monitor inside the class lock — L2

**Site.** `tqdm/std.py:664-678`, `__new__`.

**Question as asked.** *"`tqdm` registers itself in a class-level `WeakSet` and
starts a monitoring thread — but it does all of that in `__new__`, under the
class's lock, before `__init__` has validated anything. Why `__new__`, and why the
lock?"*

**Ground truth.**

| | |
|---|---|
| The decision | `5157c9bf Assign to _instances and monitor under the class lock`, **authored 2019-06-05, committed 2019-06-18** (the tool prints the commit date), first release **v4.32.2** |
| Why (the message states two races) | "This ensures that when two instances of tqdm are created simultaneously, there will be no two WeakSet or two TMonitor objects. This ensures that when the last instance is closed and a new one is created simultaneously, the last instance will not delete the monitor that serves the new instance." |
| Why the *class* lock | the raced objects are both class attributes, set at `std.py:363-365`: `monitor_interval = 10`, `monitor = None`, `_instances = WeakSet()`; the lock itself is created on first use by `get_lock()` (`std.py:766-771`) |
| Why `__new__` rather than `__init__` | **INFERRED, not stated anywhere**: registration has to happen before anything can call `close()` on the instance, and `close()` is what removes it from `_instances` (T-6). The 2019 commit moved the two statements into `__new__`; the method predates the lock by three years. Whether a *specific* failure motivated `__new__` is **UNKNOWN** |
| What the monitor does with the set | `TMonitor.run()` walks started instances every `monitor_interval` (default 10 s) and, for any whose `miniters > 1` and whose last print is older than `maxinterval`, sets `miniters = 1` and refreshes (`tqdm/_monitor.py:62-101`) |

**Correct (2).** Names the class-level `WeakSet`/monitor and **the two races** the
message describes, with `5157c9bf` as evidence.
**Partially correct (1).** "So that two threads cannot create two monitors" — one
race, correct as far as it goes, and this is what blame plus a careful read
produce.
**Correctly uncertain.** Not applicable to the two races; **applies** to *why
`__new__` instead of `__init__`* — an answer that gives the races and then says the
repository does not say why the location is `__new__` is right about everything
the evidence supports (score 2 if the races are evidenced).
**Wrong (0).** The factory reading ("so `tqdm(...)` can return a different
class"); "for garbage collection" with no mechanism.

**Reachability.** Control: `git blame -L 664,678` lands on the decision;
`git log --oneline --grep=simultaneous`. Treatment: the 23-event lifecycle list;
`commit 5157c9bf`. **What the tool uniquely adds: nothing** (blame reaches the
same commit in one command) — this is a *middle* question by construction.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "simultaneous|WeakSet|race"`
→ `WeakSet` at `std.py:17` and `:365`, the word "traces" in the README and at
`std.py:264`, and **two unrelated race comments** (`tqdm/_monitor.py:59`,
`std.py:1107`) — neither answers this question → the reason is message-only.
L2 — `git log --oneline --grep=simultaneous` → `5157c9b`, body names the two
races ✔. L3 — `git log -S "def __new__" -- tqdm/` → `9224b7b` (2020-11-27),
`ee5f8d2` (2020-11-20), `8b5b4bb` (2020-05-23), `8f56179` (2020-04-29),
`f9718d3` (2016-01-22); `git blame -L 664,678` → `5157c9bf7`×6, `9224b7b7c`×2,
`3c581208d`×2, and five singletons (`a8317c926`, `d11ffcac9`, `48b90f6d3`,
`6e50fa271`, `2ddae6b23`) — **blame lands on the decision** (six of fifteen lines,
the plurality), so it cannot be L3. **Level: L2.**

**Notes for the scorer.** (1) `3c581208d` and `2ddae6b23` are lint/duplicate-line
blame artifacts — the tool's event list correctly omits both, so a scorer verifying
against `structure --history` must not expect them. (2) The earliest event
(2016-01-22 `f9718d3a`) is absent from blame and is not part of the answer.
(3) **Accept either date** (2019-06-05 or 2019-06-18); score the two races
separately and require at least one with evidence for a 2.

---

## T-4 — the divisor in the EMA — L2, the negative control

**Site.** `tqdm/std.py:211-239`, `EMA.__call__`.

**Question as asked.** *"The EMA keeps a running average and then divides it by
`1 - beta ** calls`. What is that divisor for, and why is the smoothing state an
object with a call counter rather than a couple of variables?"*

**Ground truth.**

| | |
|---|---|
| What the divisor is | the standard debiasing term for a moving average initialised at zero: after `calls` updates the estimate is biased towards the initial `0`, and dividing by `1 - beta**calls` removes that bias. The tree **never says this** |
| Where the divisor came from | `e9bf3cd better EMA accuracy for early iterations`, 2020-12-24, "fixes #1101" — added to the then-*function* form: `return res / (1 - beta ** self.n) if self.n else res` |
| Where the object came from | `8ae4fc8 fix EMA estimates`, 2020-12-24 **four and a half hours later**, release **v4.55.0** — rewrote the function into the `EMA` class, replacing `mu`/`n` with `self.last`/`self.calls`, and rewired `tqdm.__init__`/`reset()` to hold `_ema_dn`, `_ema_dt`, `_ema_miniters` |
| Why the counter must live in the object | the correction needs the number of updates *per bar*; a bar keeps three independent EMAs and `reset()` replaces them wholesale (T-6) |
| The earlier life | `de4ae9f Add EMA function into _tdqm modules`, authored 2018-09-18, committed 2018-10-14, first release v4.27.0 — the smoothing existed as a bare function with explicit state for two years |

**Correct (2).** The divisor corrects the zero-initialised average's bias in early
iterations (or equivalently, makes the early estimates match the best constant
estimate), **and** the answer names the provenance — that the formula and the
object arrived on the same day, 2020-12-24, from `e9bf3cd` and `8ae4fc8` (or at
minimum cites one of them).
**Partially correct (1).** "It corrects the estimate in early iterations" with no
evidence, or the class refactor with no account of the divisor.
**Correctly uncertain.** Not applicable; one *sub*-claim must be allowed to be
uncertain: **why** the correction was done as a post-division rather than by
initialising at the first value is **UNKNOWN** — no message says.
**Wrong (0).** "It is a normalisation constant"; "it prevents division by zero"
(the `if self.calls else` guard does that); "the formula is standard and needs no
reason" with no evidence.

**Reachability.** Control: `git blame -L 228,239` (12/12 lines one commit);
`git log -S "beta ** self.calls"`; `git log --oneline --grep=EMA`. Treatment: the
one-event lifecycle for `EMA.__call__` — the same commit. **What the tool uniquely
adds: nothing, and the tool's view is in fact narrower than a message grep**
(review §4.2): it does not show the 2018 function era at all, and it cannot reach
`e9bf3cd`. **This is the negative control: a tool-arm advantage here would be
evidence of something other than the tool's information.** Report it as such and
do not drop the question — that would be selecting the instrument on expected
results.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni bias` → **no hits at all**.
L2 — `git log --oneline --grep=EMA` → `638e889`, `8ae4fc8`, `e9bf3cd`, `de4ae9f` ✔.
L3 — `git log -S "beta ** self.calls"` → `8ae4fc8` only, so a scorer must also
search the function-era form `1 - beta ** self.n`, which reaches `e9bf3cd` as well;
`git blame -L 228,239` → **12 of 12 lines `8ae4fc86`**, one commit, the same one
the message grep finds → the second half of the L3 test fails. **Level: L2.**

**Second scorer.** `git show e9bf3cd` (the divisor, `fixes #1101`);
`git show 8ae4fc8` (the class); **run both pickaxe searches** — a scorer who runs
only the first thinks the story starts in `8ae4fc8`.

---

## T-5 — the write lock's three lives — L3, discriminating

**Site.** `tqdm/std.py:74-98`, `TqdmDefaultWriteLock.__init__` (the class's comment
above `th_lock` is at 81-86; the constructor's body at 87-98).

**Question as asked.** *"This constructor acquires the class's thread lock,
creates the multiprocessing lock inside it, then releases it — and the class
deliberately does not create the multiprocessing lock at import time. Reconstruct
why each of those three things is the way it is."*

**Ground truth — three eras.**

| Moment | What it is |
|---|---|
| `48b90f6 Add set_lock() for parallelism safety` — authored 2016-12-28, **committed 2017-09-23**, first release **v4.18.0** | the class's origin: a global write lock so bars from parallel workers do not overwrite each other's lines. The tool reports the class created 2017-09-23 (the commit date) |
| `b566af8 Avoid global multiprocessing locks` — authored 2018-09-21, **committed 2019-01-06**, release **v4.29.0** | moves the locks to class attributes and makes the multiprocessing lock **lazy**. Why it matters: creating a `multiprocessing` lock at import time *sets the multiprocessing start context*, disallowing `spawn()`/`forkserver()` — the one reason the tree states, in the comment above `th_lock` |
| `4bdb4f76 fix multiprocessing lock creation leak` — 2020-10-06, release **v4.50.1**, body: "fixes #982 / (probably) fixes #936 / fixes #759" | the current shape: lazy creation wrapped in the class thread lock, so two processes forking at once cannot both create a multiprocessing lock; `TRLock()` (`std.py:65-71`) tolerates a platform where `threading.RLock()` cannot be constructed |
| Why the class is not constructible on Windows | the docstring: "Works only on platforms supporting `fork` (so Windows is excluded)" |

**Correct (2).** All three eras — introduced 2017-09 for parallelism safety, made
lazy 2019-01 to avoid pinning the multiprocessing start method, wrapped in the class
thread lock 2020-10 because the lazy creation itself raced and leaked locks — with
at least two of the three SHAs, or `48b90f6` (the origin) plus one of the others.
**Partially correct (1).** The 2020 leak fix alone (the blame answer), or the
in-tree `spawn()`/`forkserver()` reason alone with no account of the leak.
**Correctly uncertain.** Applies to *what the leak physically was* — the repository
states the symptom, not the mechanism; an answer that says so is correct about the
evidence.
**Wrong (0).** "The lock exists because of Windows"; "it is created lazily for
import speed" as the whole answer.

**Reachability.** Control: `git blame -L 88,98` (does **not** reach the origin),
`git log -S "class TqdmDefaultWriteLock" -- tqdm/` for the origin,
`git log --oneline --grep="multiprocessing lock"` for the 2019/2020 reasons.
Treatment: the 8-event lifecycle list names the origin and both 2019-01-06 rows.
**What the tool uniquely adds: the eight-event list**, which turns "this is the
lock" into "this is three incidents". It cannot say why any of them happened —
that is the messages' job.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "forkserver|spawn()|lock
creation leak"` → **one hit, `tqdm/std.py:85`** — one of the three reasons is in
the tree, the leak is not (a partial hit; the question asks for the sequence).
L2 — `git log --oneline --grep="multiprocessing lock"` → `4bdb4f7` and `b566af8`
✔. L3 — `git log -S "class TqdmDefaultWriteLock" -- tqdm/` → `48b90f6`;
`git blame -L 88,98` → `4bdb4f76a`×6, `b2c6e9e04`×2, `b566af8bf`×1,
`61acdf699`×1, `42ec75915`×1 — **the origin `48b90f6d` is absent** and the
dominant commit is a later fix → **L3.**

**Notes for the scorer.** (1) `b2c6e9e04` ("flake8, .gitignore ipy") only rewrapped
two comment lines and is correctly absent from the tool's event list. (2) Confirm
the three release tags `v4.18.0`, `v4.29.0`, `v4.50.1`. (3) **Score the three eras
independently and require the origin for a 2.**

---

## T-6 — finishing a bar: which line stays, and when nothing prints — L3, discriminating

**Site.** `tqdm/std.py:1270-1314`, `close` (the method runs to 1315).

**Question as asked.** *"A finished bar decides whether to erase its line or keep
it. The outer bar of a nested pair keeps its line and the inner one does not, and a
bar that finished faster than `delay` prints nothing at all. Where do those rules
come from, and when did each appear?"*

**Ground truth.**

| Moment | What it is |
|---|---|
| The `leave` rule — `08261f1c add leave=None, make close() display at pos=0 if leave`, 2019-08-18, release **v4.34.0** | `leave=None` (the default) means "leave only if this is the outermost bar": `pos == 0` is true only for the bar at row zero, so nested bars erase themselves and the outer one stays. `leave` is at `std.py:1303` |
| The `delay` rule — `90774d3 Allow hiding progressbar until some time interval has elapsed.`, authored **2019-11-07**, committed 2021-02-25, release **v4.58.0** | a bar whose whole life was shorter than `delay` never displayed, so `close()` returns early: `if self.last_print_t < self.start_t + self.delay: return` (`std.py:1284`). The feature's name was `waituntil` until `90cb341 rename waituntil => delay` (same day) |
| The partially-constructed guard — `494372e2 Fix AttributeError in close() on failed __init__`, authored 2026-07-18, committed 2026-07-24, release **v4.69.1** | `if getattr(self, 'disable', True): return` (`std.py:1272`) — `close()` can be reached on an instance whose `__init__` raised, and the default `True` makes that a no-op instead of an `AttributeError` |
| What `close()` does in between | marks itself disabled; removes itself from `_instances` and repositions an overflow bar (T-1); then either prints a final `display(pos=0)` plus a newline (`leave`) or clears its own row — and when `leave` is taken, replaces `_ema_dt` (`std.py:1308`) so the closing line's rate is not smoothed |
| The method's own age | created 2015-07-12 `cb668bea`; **68 events** to 2026-07-24, i.e. touched in 3% of the repository's commits |

**Correct (2).** The `leave=None` rule with `pos == 0` (outermost keeps its line)
**and** the `delay` guard (a bar that never displayed clears nothing) **and** at
least one of their commits (`08261f1c`, `90774d3`), or both rules plus the 2026
failed-`__init__` guard with its commit.
**Partially correct (1).** "Nested bars are erased, the outer one stays" with no
rule and no evidence; or the `delay` guard alone. **This is what blame plus a
careful read produce.**
**Correctly uncertain.** Applies to when exactly the `delay` behaviour entered a
release relative to when it was written — authored 2019-11-07, committed
2021-02-25, so any answer that states one date and notes the other is right, and an
answer that says the repository is ambiguous here is also right.
**Wrong (0).** "`leave` defaults to `True`"; "the `getattr` default is `False`"; a
claim that the rules are undocumented *and therefore arbitrary*.

**Reachability.** Control: `git blame -L 1282,1314` (dominated by two unrelated
refactors), `git log --oneline --grep=waituntil`,
`--grep="failed .__init__"`. Treatment: the 68-event lifecycle for `tqdm.close`
names every relevant moment, including `90774d36` — the `delay` feature — which a
blame-led search has no reason to look for. **What the tool uniquely adds: the
event list**, which separates "last touched by a perf cleanup" from "the rules
arrived in 2019, 2021 and 2026". It also exposes the author-versus-commit gap on
`90774d3`, because it prints the commit date.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "outermost|only leave"` →
`README.rst:831` and `.meta/.readme.rst:614`, both about `position=n`
("`n=0` for the outermost bar") — **neither states the `leave` rule**; the `delay`
rule and the 2026 guard are likewise absent from the tree. L2 —
`git log --oneline --grep=waituntil` → `90cb341` (and the merge `c76af4d`);
`--grep="failed .__init__"` → `494372e2` ✔. L3 —
`git log -S "pos == 0 if self.leave is None"` → `08261f1` and
`-S "_ema_dt = lambda"` → `65ff8be` — **both without a pathspec** (with
`-- tqdm/std.py` the first returns the 2019-08-26 submodule migration instead,
because the line was added under the old path); `git blame -L 1282,1314` →
`09a226004`×11, `caf6dbbaa`×9, `47e303cde`×4, `494372e2b`×2, `08261f1c6`×2, and
five one-line attributions (`068c5d4e9`, `2f56a1f8c`, `a587f3037`, `a34a7699f`,
`48b90f6d3`) — **two unrelated refactors dominate and the origin is absent** →
**L3.**

**Second scorer.** `git show 08261f1c`; `git show 90774d3` and `git show 90cb341`;
`git show 494372e2`; then read `std.py:1270-1314` and confirm the three guards
appear in the order the answer claims.

---

## The composition, and the two facts the report must carry

Two L1 (T-1, T-2) as instrument controls, two L2 (T-3, T-4), two L3 (T-5, T-6) —
the frozen rule met, and the set spans three subsystems inside one file.

**Report per question and per level, and disclose:** (1) T-1 and T-2 were expected
to saturate in both arms — an L1 pair at ceiling is the control working; (2) T-3
and T-4 give the treatment arm no informational advantage (T-4 none at all), so the
set's discrimination rests on **T-5 and T-6**; and (3) the date convention above,
with the nine commits where the two dates differ. A tool-arm improvement on T-4
would be a finding about participants, not about the instrument.
