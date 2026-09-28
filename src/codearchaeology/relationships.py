"""The two-way link between commits and the files they touched.

Nothing here is new information. The database already holds which files each
commit touched, and the rest is the same facts read from the other end. What this
module adds is the shape of the question, so that "which commits touched this
file" is one call instead of a walk over the whole history.

Two questions that look alike are kept apart on purpose.

**By name.** :func:`commits_touching` answers for one path as it is written. If a
file was renamed away and a different file later took that name, the answer holds
commits from both, because both are what the name saw.

**By identity.** :func:`commits_of` answers for one file across every name it
ever had. That is the answer to "what is this file's history", and it is the one
that survives a rename.

They disagree exactly where a name was reused, which is why both exist. Picking
one and hiding the other would make the tool quietly wrong about half the
repositories it is pointed at.
"""

from codearchaeology.analysis import open_analysis
from codearchaeology.history import Commit
from codearchaeology.lifecycle import Lifecycle
from codearchaeology.storage import find_commits_touching


def files_of(commit: Commit) -> tuple[str, ...]:
    """The paths *commit* touched, in path order."""
    return tuple(change.path for change in commit.changes)


def commits_of(lifecycle: Lifecycle) -> tuple[str, ...]:
    """The shas of every commit that touched this file, oldest first.

    The file is followed rather than the name: a rename does not end the list, it
    only changes what the file is called from there on.
    """
    return tuple(event.commit_sha for event in lifecycle.events)


def commits_touching(connection, path: str) -> tuple[Commit, ...]:
    """Every stored commit that touched *path*, newest first."""
    return tuple(find_commits_touching(connection, path))


def load_commits_touching(repository_root, database, path: str) -> tuple[Commit, ...]:
    """Read the stored history and return the commits that touched *path*."""
    with open_analysis(repository_root, database) as connection:
        return commits_touching(connection, path)
