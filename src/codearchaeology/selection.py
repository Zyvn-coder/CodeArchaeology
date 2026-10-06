"""What of the evidence the model is shown, when the evidence is too large to send.

The context builder's rule is that the commit's own facts are not capped: a file
list or a definition list that stopped early would hide the answer to the
question being asked. That rule holds for the *reader*, who can scroll. It cannot
hold for a model, whose window is finite, and **measured**, a commit can exceed
it by an order of magnitude:

===================================  ==============  ===============
commit                               whole bundle    largest section
===================================  ==============  ===============
five files, two hunks each              13,610 B        ast_changes 22%
two hundred files                      251,829 B        lifecycle 37%
a thousand files                     1,188,629 B        lifecycle 38%
one file, five hundred hunks            35,202 B        file_changes 84%
one file, three hundred definitions    117,717 B        ast_changes 76%
two hundred files, twenty hunks each   851,275 B        ast_changes 62%
===================================  ==============  ===============

(``benchmarks/context_benchmark.py``; bytes are the bundle's own JSON, and the
thousand-file row is about 297,000 estimated tokens.)

So this module is the layer the unit that measured those numbers asked for: the
whole evidence, then a selection, then the budget. Three rules shape it.

* **The fact layer is not touched.** What the offline path prints and what
  ``explain --json`` carries stay exactly the bundle — the same bytes as before
  this module existed. What is reduced here is the model's view of it, and
  nothing else. A reader can still check every citation, because every citation
  the model could make names something the bundle holds.
* **A selection is stated, never silent.** Every list that lost rows says how
  many it lost, in a ``selection`` block inside the view, and a file whose spans
  were cut carries ``ranges_total`` so its own row says so. A model answering
  about a partial bundle while believing it holds the whole one is the failure
  the architecture freeze names, and it is the same failure as a silently
  truncated diff.
* **When nothing has to be dropped, nothing is.** The view is then the bundle
  itself, byte for byte, which is the rule Units 5 to 11 built the two paths on:
  a small commit is shown to the model exactly as it is printed to a reader.

**What is kept, when something must go.** Each list keeps its largest entries —
a file change by the lines it added and deleted, a definition by the lines it
spans — ties broken by path and name so the same commit selects the same rows on
every machine. Size is not importance and this module does not pretend it is:
the rule is stated, the counts are stated, and the prompt is told to read a
``selection`` as an incomplete list. A pure rename carries no lines and sorts
last among the files; that bias is the reason the counts are printed rather than
the selection being silent.

**The two lists that describe the files follow the files that were kept.** The
lifecycle and the earlier commits are context *about* a file, so a file that is
not shown does not need them; they are matched by the life's path history, which
is what keeps a renamed file's life attached to the change that renamed it.

**Co-change is left exactly as the bundle wrote it.** It is already bounded to
five files and five partners, so the view has nothing to add and ``bounds`` stays
true of it. The earlier-commit window is the one thing this module re-cuts — the
bundle holds five per file and the view holds two — and ``bounds`` is rewritten
to describe the view's window rather than left claiming one it does not have.
That is why the window is stated in ``bounds`` and the dropped rows in
``selection``: each mechanism says what it did, in the place that already means
that, instead of one block restating the other.
"""

import json
from dataclasses import dataclass, replace

from codearchaeology.context import CommitContext, build_object as context_object

# What each section of the model's view may hold. These are constants rather than
# configuration on purpose: a selection that moved with a setting would make the
# model's input depend on the machine it ran on, and the bundle's whole design is
# that the same commit and the same database produce the same bytes.
#
# The numbers come from the measured ceiling rather than from taste: at these
# values the worst shape the benchmark builds — two hundred files, twenty hunks
# and ten definitions each — comes to about 11,000 estimated tokens, against
# 357,866 for the bundle it was cut from. The earlier-commit window is the one
# that had to be re-cut rather than the one that had no limit: it is the fattest
# section per file, and two commits say the same thing about a file's churn that
# five do while costing a third as much.
FILE_CHANGES = 20
RANGES_PER_FILE = 5
DEFINITIONS = 20
ABSENCES = 20
HISTORY_WINDOW = 2
MESSAGE_CHARACTERS = 4000

SELECTION_NOTE = (
    "this commit's evidence was too large to send whole, so each list below holds"
    " its largest entries; every count in this block is about the list it sits"
    " under, and a file row carrying ranges_total holds only its first spans"
)

# The sentence a reader gets when the model did not see everything. It names what
# was shown rather than only that something was, and it says where the whole of it
# can be read — which is the same command with no model configured, so the reader
# has a way to check the answer against what it was not shown.
LARGE_COMMIT_NOTE = (
    "The commit is large, so the model was shown {shown}."
    " The evidence itself is not reduced: with no model configured, this command"
    " prints all of it."
)


@dataclass(frozen=True, slots=True)
class View:
    """What the model is shown, and what it was made from.

    ``bundle`` is the whole evidence — what a reader is printed offline and what
    ``--json`` carries. ``context`` is the reduced one the model is sent and the
    one its answer is checked against, so a citation of a row the model never saw
    is refused rather than accepted for being true of something else.

    ``selection`` is ``None`` when nothing was dropped, and that is not the same
    as an empty block: it is the statement that the view and the bundle are the
    same object, which is the case the two paths were built to agree on.
    """

    bundle: CommitContext
    context: CommitContext
    selection: dict | None

    @property
    def object(self) -> dict:
        """The view as the object the model is shown.

        The ``ranges_total`` fields are worked out by comparing the view with the
        bundle rather than carried along beside it, so a row cannot say it was cut
        when it was not — the same reason ``context._bounds`` counts what the caps
        actually trimmed instead of taking a second walk.
        """
        found = context_object(self.context)
        if self.selection is None:
            return found

        totals = {change.path: len(change.ranges) for change in self.bundle.changes}
        for row in found["file_changes"]:
            total = totals.get(row["path"], 0)
            if total > len(row["ranges"]):
                row["ranges_total"] = total
        found["selection"] = self.selection
        return found

    @property
    def json(self) -> str:
        """The view as the bytes the model is sent.

        The same indentation and the same ``ensure_ascii`` as the bundle's own
        JSON, so that when nothing was dropped this string is the bundle's string
        and a test can hold the two to each other.
        """
        return json.dumps(self.object, indent=2, ensure_ascii=False)

    @property
    def note(self) -> str | None:
        """What the model was shown, said to a reader, or ``None`` if it saw it all.

        Built from the counts rather than from the constants, so the sentence
        cannot name a limit that was not the one applied. The files are one item
        rather than three because the life and the earlier commits follow the same
        files the change list kept — saying them separately would read as three
        cuts where there was one.
        """
        if self.selection is None:
            return None

        shown: list[str] = []
        files = self.selection["file_changes"]
        if files["omitted"]:
            shown.append(
                f"the largest {files['shown']} of {files['shown'] + files['omitted']}"
                f" file changes, with each file's life and recent history"
            )
        for key, label in (("ast_changes", "AST changes"), ("absences", "absences")):
            entry = self.selection[key]
            if entry["omitted"]:
                shown.append(
                    f"{entry['shown']} of {entry['shown'] + entry['omitted']} {label}"
                )
        if self.selection["ranges"]["omitted"]:
            shown.append(
                f"{self.selection['ranges']['per_file']} of each file's changed spans"
            )
        if self.selection["message"]["characters_omitted"]:
            shown.append("the first part of the commit message")
        if not shown:
            # Reachable in principle — a life that no kept path names would be
            # dropped with every file change kept — and the sentence has to stay a
            # sentence when it happens rather than read as a blank.
            shown.append("the largest entries of the lists that did not fit")
        return LARGE_COMMIT_NOTE.format(shown=", ".join(shown))


def build_view(context: CommitContext) -> View:
    """Reduce the bundle to what the model may see, and say what was left out."""
    changes, omitted_changes = _largest(
        context.changes, _change_size, _change_order, FILE_CHANGES
    )
    changes, omitted_ranges = _fewer_spans(changes)
    definitions, omitted_definitions = _largest(
        context.definitions, _definition_size, _definition_order, DEFINITIONS
    )
    absences, omitted_absences = _head(context.absences, ABSENCES)
    message, omitted_characters = _head_text(context.message)

    paths = {change.path for change in changes}
    lives, omitted_lives = _lives(context.lifecycle, paths)
    names = {life.path for life in lives}
    histories, omitted_histories = _histories(context.history, names)

    omitted = (
        omitted_changes,
        omitted_definitions,
        omitted_absences,
        omitted_characters,
        omitted_ranges,
        omitted_lives,
        omitted_histories,
    )
    if not any(omitted):
        # A commit that fits is sent whole, window included: the view is the
        # bundle object itself, so ``view.json`` is the bytes the offline path
        # prints and the two paths cannot drift on anything small.
        return View(bundle=context, context=context, selection=None)

    histories = _narrowed(histories)

    return View(
        bundle=context,
        context=replace(
            context,
            message=message,
            changes=changes,
            lifecycle=lives,
            definitions=definitions,
            history=histories,
            absences=absences,
            bounds=_windowed(context.bounds, histories),
        ),
        selection={
            "note": SELECTION_NOTE,
            "file_changes": {
                "shown": len(changes),
                "omitted": len(context.changes) - len(changes),
            },
            "ranges": {
                "shown": sum(len(change.ranges) for change in changes),
                "omitted": omitted_ranges,
                "per_file": RANGES_PER_FILE,
            },
            "ast_changes": {
                "shown": len(definitions),
                "omitted": len(context.definitions) - len(definitions),
            },
            "lifecycle": {
                "shown": len(lives),
                "omitted": len(context.lifecycle) - len(lives),
            },
            "history": {
                "shown": len(histories),
                "omitted": len(context.history) - len(histories),
            },
            "absences": {
                "shown": len(absences),
                "omitted": len(context.absences) - len(absences),
            },
            "message": {
                "characters_shown": len(message),
                "characters_omitted": len(context.message) - len(message),
            },
        },
    )


# Choosing what to keep.


def _largest(rows, size, order, limit: int):
    """The *limit* largest rows, and how many were left out.

    The order is the size descending and then the row's own name, so the same
    commit selects the same rows however the database happened to return them.
    A list that already fits is handed back untouched — not re-sorted, because
    the bundle's own order is the commit's and a view that reordered it would
    differ from the bundle in a way no count explains.
    """
    if len(rows) <= limit:
        return rows, 0
    ranked = sorted(rows, key=lambda row: (-size(row), order(row)))
    return tuple(ranked[:limit]), len(rows) - limit


def _head(rows, limit: int):
    if len(rows) <= limit:
        return rows, 0
    return rows[:limit], len(rows) - limit


def _head_text(text: str):
    """The message, cut at the character ceiling.

    Cut by characters rather than bytes: the ceiling is about how much of the
    text a reader has to read, and a message written in Chinese would otherwise
    lose two thirds of what a message in English keeps.
    """
    if len(text) <= MESSAGE_CHARACTERS:
        return text, 0
    return text[:MESSAGE_CHARACTERS], len(text) - MESSAGE_CHARACTERS


def _fewer_spans(changes):
    """Each file's changed spans, cut to the first few.

    The first rather than the largest: every span is a change of the same kind,
    so the useful thing to keep is where the file starts changing, and the count
    on the row says how many more there were.
    """
    kept, omitted = [], 0
    for change in changes:
        if len(change.ranges) <= RANGES_PER_FILE:
            kept.append(change)
            continue
        omitted += len(change.ranges) - RANGES_PER_FILE
        kept.append(replace(change, ranges=change.ranges[:RANGES_PER_FILE]))
    return tuple(kept), omitted


def _lives(lifecycle, paths: set[str]):
    """The lives of the files whose changes are shown.

    Matched on the life's whole path history rather than on its current name: a
    file renamed in this commit is named one way in the change and another way in
    its life, and matching the current name would drop exactly the lives a rename
    makes interesting.
    """
    kept = tuple(
        life for life in lifecycle if paths & set(life.path_history)
    )[:FILE_CHANGES]
    return kept, len(lifecycle) - len(kept)


def _histories(history, names: set[str]):
    """The earlier commits of the files whose changes are shown.

    By the life's current name, which is the name both the lifecycle and the
    history carry — the two are built from the same walk in the same order, so a
    name that is in one is the same file in the other.
    """
    kept = tuple(entry for entry in history if entry.path in names)[:FILE_CHANGES]
    return kept, len(history) - len(kept)


def _narrowed(histories):
    """The same rows, each holding the most recent few of its earlier commits.

    The bundle holds five per file and this keeps two, which is the single
    largest saving the view makes — the earlier commits are the fattest section
    per file, and two of them say what five do about whether a file is hot.
    """
    return tuple(
        replace(entry, commits=entry.commits[-HISTORY_WINDOW:]) for entry in histories
    )


def _windowed(bounds, histories):
    """``bounds`` rewritten as the view's own, so it cannot claim a window it lost.

    The same rule ``context._bounds`` follows: the counts come from the rows the
    cap was applied to, not from a second walk that could describe a different
    set. A narrow window is stated here rather than in the selection block,
    because ``bounds`` is the bundle's own place for "what this capped" and a
    reader who is told about it twice is told it in two places that can disagree.
    """
    if bounds.history_commits <= HISTORY_WINDOW:
        return bounds
    return replace(
        bounds,
        history_commits=HISTORY_WINDOW,
        history_commits_omitted=sum(
            max(0, entry.total - HISTORY_WINDOW) for entry in histories
        ),
    )


def _change_size(change) -> int:
    """How many lines a file's change moved.

    A binary file reports no lines, and a pure rename reports none either: both
    are 0 here, which is what puts them last rather than dropping them — the
    counts in the selection block say how many rows were left out.
    """
    return (change.added_lines or 0) + (change.deleted_lines or 0)


def _change_order(change) -> str:
    return change.path


def _definition_size(definition) -> int:
    """How many lines a definition spans.

    For a definition that was created this is what was added; for one that was
    modified it is the whole definition, because the evidence holds the span of
    the definition and not of the change inside it. It is the same measure either
    way — the lines the definition occupies — and the prompt is told the rule.
    """
    if definition.lineno is None:
        return 0
    end = definition.end_lineno if definition.end_lineno is not None else definition.lineno
    return max(0, end - definition.lineno + 1)


def _definition_order(definition):
    return (definition.path, definition.qualname, definition.occurrence)
