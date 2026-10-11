# Appendix D1 — the six questions for `python-attrs/attrs`

**FACILITATOR ONLY. NEVER SHOWN TO A PARTICIPANT.**

Checkout: the derivation clone, HEAD `644b4e16` (2026-10-06), `src/attr/...`. Line
numbers and every measurement below are as of that commit. Re-exported
2026-10-09; the calibration commands were re-run then and their output is what is
recorded here (the source document is `docs/v0.5-understanding-experiment.md`
§5, sha256 `357b735b…c0187`).

Scoring is §7.2 of the design: **2** correct and evidenced, **1** correct but
unevidenced, **0** wrong, **U** correctly uncertain — and `U` scores 2 only where
the question's own "correctly uncertain" line says uncertainty is the truth. Score
from the written answer alone. Report per question and per level, never as an
average of six.

| # | Level | Site | What it measures |
|---|---|---|---|
| Q-A | L1 | `src/attr/_make.py:231` | instrument control |
| Q-B | L3 | `src/attr/_make.py:561` | discriminating |
| Q-C | L1 | `src/attr/_make.py:353` | instrument control |
| Q-D | L3 | `src/attr/_make.py:1730` | discriminating |
| Q-E | L2 | `src/attr/__init__.py:83` | path-shortening |
| Q-F | L2 | `src/attr/_make.py:869` | path-shortening |

---

## Q-A — the `linecache` loop — L1, an instrument control

**Site.** `src/attr/_make.py:231`, `_linecache_and_compile`; the loop is at
243–256.

**Question as asked.** *"This function loops, renaming `filename` until the cached
entry matches the one it just built. Why? What is the counter for, and what would
break without the loop?"*

**Ground truth.**

- The loop makes classes with **identical** generated source share one linecache
  entry, so a debugger shows one frame for them; classes whose source differs get
  `-1>`, `-2>`, … so their line numbers stay correct.
- Before this change the name was derived from `repr`-based uniqueness and always
  advanced, so identical classes each occupied their own entry.
- The decision: `3858063 Rework linecache handling (#828)`, 2021-06-30 — the diff
  rewrites the loop and splits out `_compile_and_eval`.
- The reason survives in the tree, which is why this is L1: `CHANGELOG.md:447` —
  "Generated source code is now cached more efficiently for identical classes."
  (the release consumed its fragment, `changelog.d/828.changes.rst`).
- The test that pins it: `tests/test_dunders.py` — a class aliased as `CopyC` and a
  second class of the same name with different source; it asserts `CopyC` gets the
  bare filename and `C` gets `-1>`.
- Also relevant: `eda9f2d Make unique_filename not depend on repr. (#560)`.

**Correct (2).** Identical generated source shares an entry; different source gets
a numbered name so the debugger maps lines to the right code, evidenced by the
changelog line, the commit, or the test.
**Partially correct (1).** "It is for debuggers" — that much is in the comment, so
it is almost free — with no account of the duplicate-name case or the counter.
**Correctly uncertain.** Not applicable — the evidence is sufficient.
**Wrong (0).** Not written as a form in the design document; score per the rubric
any mechanism the history does not support.
**Note for the scorer.** This is an instrument control: **if the tool arm loses
here, the tool is actively misleading and the rest of the data is beside the
point** (§10's Harmful row).

**Reachability.** Control: `grep -rn "identical classes" .` → `CHANGELOG.md:447`;
`git show 3858063`. Treatment: `archaeology structure src/attr/_make.py --history`
names `3858063` for the definition; `archaeology commit 3858063` prints it.

**Calibration (re-run 2026-10-09 on `644b4e16`).** L1 — `grep -rni "identical
classes" --include=*.py --include=*.md --include=*.rst --include=*.txt .` →
**exactly one hit, `CHANGELOG.md:447`** → the reason is in the tree.
L2 — `git log --oneline --grep=linecache` → `3858063` (the decision).
L3 — `git log -S "old_val == linecache_tuple"` → `3858063`; `git blame -L 231,256`
→ `38580632`×12, `74007f67`×8, `1499c77e`×5, `26d8dd79`×1 — blame's plurality is
the decision, so it can never be L3. **Level: L1.**

---

## Q-B — the frozen-exception carve-outs — L3, discriminating

**Site.** `src/attr/_make.py:561`; the five names are at 565–570.

**Question as asked.** *"A frozen class refuses every attribute write. This one
makes five exceptions for instances of `BaseException`. Why those five, and when
did each get added?"*

**Ground truth — three decisions from three eras, and that is the point.**

| Moment | What it is |
|---|---|
| `f2dabea` 2020-11-04 | **`Fix exception chaining on PyPy (#712)`** — the origin, and **it was PyPy-specific**: the carve-out was wrapped in `if PYPY:` and did not exist on CPython. On PyPy, setting `__cause__` / `__context__` / `__traceback__` during exception handling goes through `__setattr__`, so a frozen exception raised inside a `try` raised `FrozenInstanceError` instead of the real error. |
| `a71fbbad` 2023-01-08 | **`Fix @frozen exceptions to allow __traceback__ to be set. (#1081)`** — the `if PYPY:` wrapper was removed and the carve-out became unconditional on every interpreter. |
| `1921da6e` 2024-10-26 | **`allow __suppress_context__ and __notes__ to be mutated on frozen exceptions (#1365)`** — the two later names, documented at `CHANGELOG.md:146`. |
| `cfa6d2e9` 2016-08-20, `ae734096` 2024-08-16 | Creation and movement ("Cull Ruff ignores"). No decision. |

**Correct (2).** Three decisions — a **PyPy-only** exception-chaining fix in 2020,
its **generalization to every interpreter** in 2023 for `__traceback__`, and the
`__suppress_context__` / `__notes__` addition in 2024. **The PyPy origin is
required for a 2.**
**Partially correct (1).** Two commits — traceback and notes — with the earlier
PyPy-specific origin missing. **This is the answer blame produces**, and it is
plausible, well-evidenced and incomplete, which is what makes this a good question
rather than a trap.
**Correctly uncertain.** Not applicable; the evidence is sufficient.
**Wrong (0).** "Because `BaseException` is special-cased by Python" — plausible,
untrue, cites nothing.

**Reachability.** Control: `git blame -L 561,575` (full attribution below), then
`git show` on the commits it names; or `git log --oneline --grep=PyPy`. Treatment:
`archaeology structure src/attr/_make.py --history` prints the five rows with SHAs
and dates; `archaeology commit f2dabea` / `a71fbbad` / `1921da6e` print the
reasons.

**Calibration (re-run 2026-10-09).** L1 — the tree documents **only the 2024
addition** (`CHANGELOG.md:146`); neither the 2020 PyPy origin nor the 2023
generalization is stated anywhere in the tree → not L1. L2 —
`git log --oneline --grep=PyPy` → 8 hits, **one of them the origin `f2dabea`** →
the origin is message-findable. L3 — `git blame -L 561,575 --date=short` →
`a71fbbad`×11, `1921da6e`×2, **`f2dabeae`×1 (line 574, a blank line)**, `ae734096`×1
(line 575) — the dominant commit is the 2023 rewrite that generalized the
construct, and the origin's only visible line is blank → **L3**.

**Two notes this record must carry, because the source document's wording is
imprecise.**

1. §2.5 of the design prints this blame command **truncated with `...`** and
   concludes "the origin is missing". The full run shows the origin on **one blank
   line**. The level stands by §4.2's second half (blame's dominant attribution is
   a later rewrite, not the origin), but a control that reads *every* attribution
   can name all three commits.
2. Therefore the expected failure is not "cannot find 2020" but "stops at the
   dominant reading": a participant who names the 2023 and 2024 changes and misses
   2020 is the **expected partial answer (1)**, and a scorer should not treat the
   single `f2dabeae` line as making the question trivial.

---

## Q-C — the collector with `_broken` in its name — L1, an instrument control

**Site.** `src/attr/_make.py:353`, `_collect_base_attrs_broken`.

**Question as asked.** *"One of the two collectors for inherited attributes is
called `_broken` and its docstring says it 'adheres to the old incorrect
behavior' — and it is still what `attr.s` reaches by default, while
`attrs.define` reaches the other one. Which flag decides this, why was the buggy
version kept, and does the difference show up in behaviour?"*

**Ground truth.**

| | |
|---|---|
| The decision | `33b61316 Fix attribute collection (#635)`, 2020-04-06 — adds `@attr.s(collect_by_mro=False)`, the corrected collector, and the old one under its present name |
| What the two do differently | the broken one walks `cls.__mro__[1:-1]` front-to-back and keeps the first base's definition, and it includes attributes a base class merely *inherited*; the corrected one walks `reversed(cls.__mro__[1:-1])`, skips `a.inherited`, and keeps the freshest definition of each name (`_make.py:329-348`) |
| Why the bug survived | the changelog says it in as many words: "It's only necessary for certain cases of multiple-inheritance but is **kept off for now for backward-compatibility reasons**. It will be turned on by default in the future." (`CHANGELOG.md:680`) |
| The new API takes the fix | `b02335f5 Add attr.define/mutable/frozen (#666)`, 2020-08-17 — `collect_by_mro=True` from its first day |
| The promise kept? | No. At `644b4e16` the `attr.s` default is still `False`; the only mark of the flag's age is `.. versionadded:: 20.1.0 *collect_by_mro*` (`_make.py:1434`) |
| Side effect in the tree | the same commit gave `Attribute` its `inherited` field, which the corrected collector filters on |

**Correct (2).** Names the flag (`collect_by_mro`), the two APIs that set it
differently, and the reason — an opt-in fix so existing `attr.s` users' field order
cannot change silently — with `33b61316` or `CHANGELOG.md:680` as evidence. It may
also note the changelog's promise was never kept.
**Partially correct (1).** "`_broken` is the old algorithm, kept for backward
compatibility" with no flag, no API split, or no statement of *what* differs.
**Correctly uncertain.** Not applicable — everything needed is in the tree.
**Wrong but plausible (0).** "It is dead code left behind by a refactor" (the name
invites it), or "it exists so old pickles keep loading" (attribute collection and
pickling are unrelated).
**Note for the scorer.** Instrument control: the tool arm must not lose here.

**Reachability.** Control: `grep -rn "collect_by_mro" .` → 19 hits, including the
changelog line that states the reason. Treatment: `structure … --history` lists
`_collect_base_attrs_broken` as created 2020-04-06 by `33b61316`, and
`commit 33b61316` shows the fix.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "collect_by_mro"` over the
four file types → **19 hits, one of which states the reason** (`CHANGELOG.md:680`)
→ in the tree. L2 — `git log --oneline --grep=collect_by_mro --all` → **three
hits, none of them the decision** (`45de9be` docs, `9f7d11e` and `56c7308` typing
stubs). L3 — `git log -S "_collect_base_attrs_broken"` → `33b6131`;
`git blame -L 353,359` → every line `33b61316`. **Blame lands on the decision, and
the reason is in the tree → L1.**

---

## Q-D — one `__ne__` shared by every class — L3, the second discriminating question

**Site.** `src/attr/_make.py:1730` (the module-level `__ne__`), read with
`_add_method_dunders`'s stamping and the two installers (`add_eq` at `:1150`,
`_add_eq` at `:1847`).

**Question as asked.** *"`C.__eq__` belongs to `C` — its `__qualname__` says so.
`__ne__` is a single module-level function shared by every class with equality,
and it keeps its own name. Has attrs always shared it this way, and is sharing it
safe?"*

**Ground truth — three moments, and the third one changed the answer.**

| Moment | What it is |
|---|---|
| `9e1f1360` 2017-12-27 | **`Add __module__ and __qualname__ to methods, fix __name__ (#316)`** — the hazard is created: `_add_method_dunders` starts stamping `__module__` and `__qualname__` **onto the method object**, and the module-level `_ne` is renamed `__ne__` in the same commit. |
| `4bd68270` 2020-03-09 | **`Wrapped __ne__ in a _make_ne function #626 (#632)`** — every class gets a **freshly created** `__ne__`, "to guarantee that each attrs class gets a new instance of the `__ne__` function". The reason is the stamping: it mutates, so one shared object cannot carry two classes' identities. Its test and comment are still in the tree (`tests/test_make.py:2181`). |
| `74007f67` 2025-02-15 | **`ClassBuilder: eval only once (#1407)`** — `_make_ne` is deleted and both installers assign the module-level object **directly**, without stamping; only `__eq__` is stamped now. In the same commit `"__ne__"` is **removed from `test_attaches_meta_dunders`'s parametrisation** (`tests/test_make.py:2147-2159`), so the suite stays green while the guarantee is gone — and the comment explaining why the assertion existed stays in place. |
| Measured at `644b4e16` | `C.__ne__ is D.__ne__` → `True`; `C.__ne__.__qualname__` → `'__ne__'`; `C.__ne__.__module__` → `'attr._make'`. The sharing question is live, not historical. |

**Correct (2).** All three parts — the per-class copy was needed because attrs
stamps generated methods in place; the 2020 fix and the 2025 removal both
happened; and sharing is safe today because `__ne__` is no longer stamped at all,
**not** because a copy is made. Evidence: the three commits, the current `add_eq`,
or the test diff.
**Partially correct (1).** "Each class needed its own so `__qualname__` would be
right, and the 2025 speed refactor removed it" — right about the middle era,
silent about the mechanism and about what 2025 gave up. **This is what blame plus
the surviving comment produce.**
**Correctly uncertain.** Not applicable to the three parts. **A note for the
scorer:** whether the 2025 change *meant* to give up the `__qualname__` guarantee
is not recorded anywhere; an answer that says so is correct about everything the
repository supports and must not be scored down.
**Wrong but plausible (0).** "The 2025 'eval only once' refactor added `__ne__`"
(that is literally what blame says), or "it is a Python 2 leftover" as an answer
to *this* question.

**Reachability.** Control: `git blame -L 1730,1739 --date=short src/attr/_make.py`
→ `74007f67` and `cd8890c7` (2017-12-04), **neither of which is the origin** — see
the calibration record for why the second is an artifact. Treatment:
`structure src/attr/_make.py --history` prints, under `__ne__`, `created
2017-12-27 9e1f1360`, `deleted 2020-03-09 4bd68270`, `created 2025-02-15
74007f67`; then `commit 4bd68270` and `commit 74007f67` print the two reasons.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "reused across multiple" .`
→ **exactly one hit, `tests/test_make.py:2182`**, which states the 2020 constraint
and nothing about the other two eras: a partial statement, not the answer. L2 —
`git log --oneline --grep="instance of the __ne__"` → `4bd6827`, so the middle
era's reason is message-findable. L3 — `git log -S "def __ne__"` → five hits,
including `9e1f1360`; `git blame -L 1730,1739` → `74007f67`×7 and `cd8890c7`×3,
**neither of which is the origin**, and the 2020 commit the message grep finds is
invisible to blame. **L3.**

**One evidence correction.** The design's §5 says blame reports "`74007f67` on six
lines and `cd8890c7` on three"; the range 1730–1739 has ten lines and the
measured split is **seven and three**. Nothing about the level changes.

---

## Q-E — the three lazy submodules — L2

**Site.** `src/attr/__init__.py:83` (`_LAZY_SUBMODULES`) and `:119`
(`__dir__`).

**Question as asked.** *"`import attr` does not import `attr.converters`,
`attr.validators` or `attr.filters`, yet all three are in `__all__`, reachable as
attributes, and documented. What makes them appear, why is it done that way, and
what is the hand-written `__dir__` for?"*

**Ground truth.**

| | |
|---|---|
| The mechanism | PEP 562 module-level `__getattr__`: the first touch of `attr.validators` imports the submodule, **writes it into the module's own dict** (`sys.modules[mod_name].__dict__[name] = mod`) so the hook runs once, and returns it |
| The reason | `6851ab59 Defer imports on the cold import path (#1600)`, 2026-07-31 — the body says "Defer imports and preserve module introspection" and "Reduce cold import overhead". `import attr` is paid by every program that uses attrs. |
| What `__dir__` is for | the three names are in `__all__` but not in `globals()` until first access, so `dir(attr)` would lose them; `globals() | __all__` is the whole fix |
| The hook's earlier life | `d4934b14 Drop 3.6 & switch to 100% static packaging configuration (#993)`, 2023-01-11 — `_make_getattr` first appears for `__version__`/`__version_info__`; the submodule branch and `__dir__` are the 2026 addition |
| Same commit, other files | `_make.py`'s function-local `import copy` and `__init__.py`'s local `from importlib.metadata import metadata` are the same decision |

**Correct (2).** The reason is the cost of `import attr` and the shape is lazy
submodules plus a `__dir__` so introspection does not lose names — with `6851ab59`
or the code's own coupling of `__all__` to `__dir__` as evidence.
**Partially correct (1).** "It is a lazy import so that `import attr` is faster",
with no part for `__dir__`, `__all__`, or the write-back that makes the hook run
once.
**Correctly uncertain.** Not applicable.
**Wrong but plausible (0).** "To break a circular import" — the classic reason for
a lazy import, and not this one — or "the submodules are deprecated" (they are in
`__all__` and in the docs).

**Reachability.** Control: `git blame -L 83,120 --date=short src/attr/__init__.py`
→ 13 lines `6851ab59` (plus the 2023 lines of the version proxy), then
`git show 6851ab59`; or `git log --oneline --grep="cold import"`. Treatment:
`structure src/attr/__init__.py --history` → `_make_getattr` modified 2026-07-31
by `6851ab59` and `__dir__` **created** 2026-07-31 by `6851ab59`.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "lazy|defer|cold import"
src/attr/*.py` → **five hits, every one of them the code's own identifier**
(`_LAZY_SUBMODULES` twice; `_lazy_is_generator` three times — its definition, its
import and its call site). Nothing in the tree says why. L2 —
`git log --oneline --grep="cold import"` → `6851ab5`. L3 — `git log -S
"_LAZY_SUBMODULES" -- src/attr/__init__.py` → `6851ab5`; blame lands on
`6851ab59` for thirteen of the range's lines. **Blame lands on the decision, so it
cannot be L3, and the reason is message-only → L2** — the case §4.2's tie-break
was written for: a grep "hit" that is the identifier the reader is staring at is
not the tree stating a reason.

---

## Q-F — a private CPython call on a class about to be replaced — L2, composite

**Site.** `src/attr/_make.py:869-871`, opening `_ClassBuilder._create_slots_class`.

**Question as asked.** *"attrs rebuilds a class when it makes it slotted. Before it
does, it calls a private CPython function if that function exists, and the comment
above it says only `3.14.0rc2+`. What is being cleared, what would leak without
it, and what was there before this line?"*

**Ground truth — two moments, eighteen days apart.**

| Moment | What it is |
|---|---|
| `000c5634` 2025-07-28 | **`Fix slotted reference cycles on 3.14 (#1446)`** — the first fix, entirely in Python: a `_Deproxier` class whose `__ror__` unwraps a `mappingproxy`, used to pull the old class's raw `__dict__` out, delete `__dict__`/`__weakref__`, and bump the type's internal version tag. Its message names the cause: a *reference cycle*. |
| `19175d91` 2025-08-15 | **`Use sys._clear_type_descriptors() on 3.14 to break slot reference cycle (#1459)`** — replaces all of that with the private API. The comment above the call records the version the API appeared in, which is why the call is feature-detected rather than version-checked. |
| Why it is needed | on 3.14 a type object caches descriptors that point back at the class; attrs clones the class and abandons the original, so the cache keeps the original alive. Clearing the caches breaks the cycle. |
| Reachable without the issue tracker | Yes. Both messages are in the repository and both say *reference cycle*; the deleted Python workaround is visible in `19175d91`'s diff. The linked CPython issues are extra detail and are **not needed to score**. |

**Correct (2).** The reason (a 3.14 descriptor-cache reference cycle that kept the
original class from being collected) **and** the fact that this line replaced an
earlier pure-Python workaround — evidence `19175d91`, `000c5634`, or the deleted
`_Deproxier` block in the first one's diff.
**Partially correct (1).** "It is a Python 3.14 workaround so the old class can be
collected", with the earlier commit missing. **This is the answer blame produces.**
**Correctly uncertain.** Not applicable.
**Wrong but plausible (0).** "It clears method caches after the rebuild so
attribute lookups find the new methods" — plausible, wrong twice (the class being
cleared is the old one, and the message says reference cycle) — or "it is for
weakref support", a different mechanism in the same function.

**Reachability.** Control: `git blame -L 869,871 --date=short` → `19175d91`, then
`git log --oneline --grep="reference cycle"` finds both moments by their messages.
Treatment: `structure src/attr/_make.py --history` lists, for
`_ClassBuilder._create_slots_class`, `modified 2025-07-28 000c5634` and `modified
2025-08-15 19175d91`; `commit 000c5634` prints the first fix. **The tool's
advantage here is real but small** — it hands over both commits in one view where
the control must read the newer commit's diff to learn the older one exists.

**Calibration (re-run 2026-10-09).** L1 — `grep -rni "clear_type_descriptors"`
over the four file types → **two hits, both the code on this site**;
`grep -rni "reference cycle|garbage collect" CHANGELOG.md` → one hit
(`CHANGELOG.md:205`, about `cached_property`, not this). Nothing in the tree states
why. L2 — `git log --oneline --grep="reference cycle" --all` → `19175d9` and
`000c563` (a third hit, `554d6f2`, is the unrelated cloudpickle move). L3 —
`git log -S 'hasattr(sys, "_clear_type_descriptors")'` → `19175d9`;
`git blame -L 869,871` → `19175d91` on all three lines. **Blame lands on the
newest decision and its subject states the reason, so the second half of the L3
test fails → L2**; the earlier era is what the lifecycle view adds.

---

## The composition, and the one rule for reading the results

Two L1 (Q-A, Q-C) as instrument controls, two L2 (Q-E, Q-F), two L3 (Q-B, Q-D) as
the discriminating pair. **Report per question and per level.** If the two arms tie
on both L3s, the tool's central claim is unsupported — and a tie on Q-B in
particular should be read against the note above (the origin's single blank line),
not as a defect in the question.
