"""Which files change alongside the file you ask about.

The statistic is frozen in ``docs/v0.4-cochange-design.md`` and this module
implements it, nothing more. For one file A and one other file B::

    analyzed(A)            the commits that touched A and are not large
    shared(A, B)           |analyzed(A) ∩ commits(B)|
    score(A → B)           shared(A, B) / |analyzed(A)|

Three properties of that definition shape everything below.

**A pair is a pair of file identities, not of paths.** The identity is the
lifecycle one, so a rename does not end the file: querying ``core/app.py``
answers for the file that was born as ``app.py``, pre-rename commits included.
The identities come from :func:`codearchaeology.lifecycle.build_lifecycles`
rather than from a second walk written here. A copy of the rule is a copy that
can drift — the design freeze's own measurement script numbered identities by
the size of a path-keyed mapping, merged two lives, and printed a table that
looked plausible.

**A commit contributes at most one occurrence per identity.** A rename names
two paths and is still one file changing once, so a commit is reduced to the
*set* of identities it touched before anything is counted. That is what makes
``A ↔ A`` impossible rather than merely filtered: a set has no pair with
itself. It is also what the denominator's ``large_commit_limit`` is compared
against.

**The denominator is the queried file, not the repository.** The score is
conditional on A changing — ``0.5`` means half of A's analyzed commits also
touched B — so the sample is A's commits and the numerator is counted inside
the same filtered set. Counting it over a different set would make the ratio
stop being a ratio of one sample.

What this module deliberately does not do: it stores nothing, caches nothing,
reads no git and no AST, and infers nothing from content or messages. Every
number is a count of stored ``commit_files`` facts, and a pair that shares one
commit is hidden by default because ``score 1.0`` from one observation is the
strongest possible reading of the weakest possible evidence.
"""

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path

from rich import box
from rich.table import Table

from codearchaeology.analysis import open_analysis
from codearchaeology.history import Commit
from codearchaeology.lifecycle import build_lifecycles
from codearchaeology.storage import read_stored_commits

# The design freeze's default: a commit over this many files is a bulk change,
# not a set of pairwise observations, and it leaves the analysis entirely. The
# number is a parameter of the definition rather than a performance guard — the
# per-file algorithm never materialises the pairs a big commit would generate.
DEFAULT_LARGE_COMMIT_LIMIT = 100

# A pair needs at least this many shared commits to be shown. One shared commit
# is the weakest evidence the statistic can rest on, and the default keeps it
# out of the report; the count of what it hid is reported instead.
DEFAULT_MIN_SHARED = 2


@dataclass(frozen=True, slots=True)
class CoChange:
    """One file that changed alongside the queried one."""

    path: str
    shared_commits: int
    score: float


@dataclass(frozen=True, slots=True)
class CoChangeReport:
    """One file's co-change answer, and the sample the ratio was taken over.

    ``analyzed_commits`` is the denominator: every score below is
    ``shared_commits`` divided by it. ``excluded_large_commits`` is the part of
    the file's history that left the analysis, reported so the sample is visible
    rather than implied. ``hidden_pairs`` counts the files ``min_shared`` kept
    out of ``co_changes``, for the same reason: a filter that says nothing is a
    filter the reader cannot see.
    """

    path: str
    path_history: tuple[str, ...]
    analyzed_commits: int
    excluded_large_commits: int
    hidden_pairs: int
    co_changes: tuple[CoChange, ...]


def analyze_cochange(
    commits: Iterable[Commit],
    path: str,
    large_commit_limit: int = DEFAULT_LARGE_COMMIT_LIMIT,
    min_shared: int = DEFAULT_MIN_SHARED,
) -> tuple[CoChangeReport, ...]:
    """Answer the co-change question for every life that ever carried *path*.

    A name can belong to more than one file — one deleted and later created
    again, one renamed away whose name was taken back — so every life that ever
    carried it is answered for, oldest first, the way the single-file view
    already answers. Returning only the newest would hide the others.

    An empty result means the stored history never carried the name.
    """
    lives = build_lifecycles(commits)

    # Which life each (commit, path) belongs to, and which lives each commit
    # touched. The first life wins a repeated key, which is the order the lives
    # were created in: the life holding a path in that commit is the one the
    # event belongs to. A life's own events carry the path it had at the time,
    # so a rename resolves through the new name and a deletion through the one
    # it died under.
    identity_of: dict[tuple[str, str], int] = {}
    touched: dict[int, set[str]] = {}
    for ordinal, life in enumerate(lives):
        for event in life.events:
            identity_of.setdefault((event.commit_sha, event.path), ordinal)
            touched.setdefault(ordinal, set()).add(event.commit_sha)

    # A commit's occurrence set is the set of identities it touched, so the
    # limit is compared against identities and a rename counts once.
    per_commit: dict[str, set[int]] = {}
    for (sha, _), ordinal in identity_of.items():
        per_commit.setdefault(sha, set()).add(ordinal)

    analyzed = {
        sha
        for sha, identities in per_commit.items()
        if len(identities) <= large_commit_limit
    }

    reports = []
    for ordinal, life in enumerate(lives):
        if path not in life.path_history:
            continue

        # The file's commits, and the part of them that is inside the analysis.
        # Both are filtered the same way, which is what keeps the score a ratio
        # over one sample.
        commits_of_file = touched.get(ordinal, set())
        sample = commits_of_file & analyzed

        shared: dict[int, int] = {}
        for sha in sample:
            for other in per_commit[sha]:
                if other != ordinal:
                    shared[other] = shared.get(other, 0) + 1

        partners = [
            (
                ordinal_of_other,
                CoChange(
                    path=lives[ordinal_of_other].current_path,
                    shared_commits=count,
                    score=count / len(sample),
                ),
            )
            for ordinal_of_other, count in shared.items()
            if count >= min_shared
        ]
        # Frozen ordering: score, then the count that backs it, then the name.
        # The ordinal is the last step and cannot be reached unless two lives
        # share a current path, but it keeps even that case deterministic.
        partners.sort(
            key=lambda row: (-row[1].score, -row[1].shared_commits, row[1].path, row[0])
        )

        reports.append(
            CoChangeReport(
                path=life.current_path,
                path_history=life.path_history,
                analyzed_commits=len(sample),
                excluded_large_commits=len(commits_of_file) - len(sample),
                hidden_pairs=len(shared) - len(partners),
                co_changes=tuple(partner for _, partner in partners),
            )
        )

    return tuple(reports)


def load_cochange_commits(repository_root, database) -> list[Commit]:
    """Read the history stored in *database*, for the co-change analysis.

    The analysis walks the parent links, so the commits come back the way the
    other readers hand them over and the walk settles the order itself.
    """
    with open_analysis(repository_root, database) as connection:
        return read_stored_commits(connection)


def build_table(reports: Iterable[CoChangeReport], width: int) -> Table:
    """Render one life's co-change rows as a Rich table.

    The other file's name is the only column that can be long, so it is the only
    one that gives when the terminal is narrow. The counts and the score are
    fixed shapes, and a squeezed number is a wrong number.
    """
    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    table.add_column("FILE", no_wrap=True)
    table.add_column("SHARED", justify="right", no_wrap=True)
    table.add_column("SCORE", justify="right", no_wrap=True)

    for row in reports:
        table.add_row(row.path, str(row.shared_commits), f"{row.score:.3f}")

    return table


def build_block(report: CoChangeReport) -> str:
    """Render one file's answer as the lines the command prints."""
    lines = [report.path]
    if len(report.path_history) > 1:
        lines.append("History:  " + " -> ".join(report.path_history))
    lines.append("")
    lines.append(f"Analyzed: {report.analyzed_commits} commits of this file")
    return "\n".join(lines)


def build_object(report: CoChangeReport) -> dict:
    """One file's answer as the fields other programs read.

    ``analyzed_commits`` is the denominator of every score beside it, so a
    reader can verify any row by division. The two counts that are not shown in
    the table travel too: ``large_commits_excluded`` is the sample that left the
    analysis, and ``hidden_pairs`` is what ``min_shared`` kept out, so a program
    never has to guess whether an empty list means "none found" or "none shown".
    """
    return {
        "path": report.path,
        "path_history": list(report.path_history),
        "analyzed_commits": report.analyzed_commits,
        "large_commits_excluded": report.excluded_large_commits,
        "hidden_pairs": report.hidden_pairs,
        "co_changes": [
            {
                "path": row.path,
                "shared_commits": row.shared_commits,
                "score": row.score,
            }
            for row in report.co_changes
        ],
    }


def build_json(reports: Iterable[CoChangeReport], path: str, repository_root, head_sha: str) -> str:
    """The whole answer for *path*, as JSON.

    The object always holds a list, even when the name belongs to a single
    file, for the same reason the single-file view does: a name can belong to
    several lives and the shape must not depend on which repository was asked.
    ``path`` at the top is what was queried, and each entry's ``path`` is what
    that life is called now.
    """
    return json.dumps(
        {
            "repository": str(Path(repository_root).resolve()),
            "head_sha": head_sha,
            "path": path,
            "files": [build_object(report) for report in reports],
        },
        indent=2,
        ensure_ascii=False,
    )
