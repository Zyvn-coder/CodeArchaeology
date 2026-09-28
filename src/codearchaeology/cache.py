"""Work out where a repository's analysis database lives."""

import hashlib
import os
import sys
from pathlib import Path

CACHE_DIRECTORY_VARIABLE = "CODEARCHAEOLOGY_CACHE_DIR"
APPLICATION_DIRECTORY = "codearchaeology"


def cache_directory() -> Path:
    """Return the directory that holds analysis databases.

    Set ``CODEARCHAEOLOGY_CACHE_DIR`` to put them somewhere else.
    """
    override = os.environ.get(CACHE_DIRECTORY_VARIABLE)
    if override:
        return Path(override)

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"

    return Path(base) / APPLICATION_DIRECTORY


def database_path(repository_root) -> Path:
    """Return the database file that belongs to *repository_root*.

    The file name is a digest of the repository path, so two checkouts of the
    same project keep separate databases, and a long or non-ASCII path can never
    turn into an invalid file name.
    """
    key = str(Path(repository_root).resolve())
    if sys.platform == "win32":
        # Windows compares paths without regard to case, so D:\Repo and d:\repo
        # are the same directory and have to map to the same database.
        key = key.casefold()

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return cache_directory() / f"{digest}.db"
