"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from sample_repo import build_sample_repo


@pytest.fixture(scope="session")
def sample_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture repository: five commits, one rename, one merge."""
    return build_sample_repo(tmp_path_factory.mktemp("sample-repo"))
