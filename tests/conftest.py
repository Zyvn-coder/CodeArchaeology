"""Shared pytest fixtures."""

import os
import sys
from pathlib import Path

import pytest

from sample_repo import build_sample_repo


def pytest_configure(config) -> None:
    """Refuse to run on an interpreter other than the one that was requested.

    The CI matrix sets ``EXPECTED_PYTHON``. Without this check a job could pass
    while quietly testing a different Python than the one it claims to test,
    which is the kind of unverified claim this project tries to avoid.
    """
    expected = os.environ.get("EXPECTED_PYTHON")
    if not expected:
        return

    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running != expected:
        raise pytest.UsageError(
            f"this run asked for Python {expected} but is running on {running}"
        )


@pytest.fixture(scope="session")
def sample_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture repository: six commits, a rename, a deletion and a merge."""
    return build_sample_repo(tmp_path_factory.mktemp("sample-repo"))
