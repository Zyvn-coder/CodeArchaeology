"""Smoke tests for the command line entry point."""

import re
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from codearchaeology import __version__
from codearchaeology.cli import app

runner = CliRunner()

ROOT = Path(__file__).parent.parent


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])

    assert "Usage" in result.stdout


def test_every_copy_of_the_version_agrees() -> None:
    """The version is written down in five places and nothing else compares them.

    ``pyproject.toml`` is what a build would publish, ``__version__`` is what the
    command prints, the two READMEs show that output to whoever is installing,
    and ``CHANGELOG.md`` opens with the release it is describing. A release that
    bumped one and not the others would ship a package whose own ``--version``
    disagrees with its metadata, a README showing a version nobody can install,
    or a changelog whose newest entry is the previous release — and none of them
    would fail anything.

    ``uv.lock`` carries the version as well, but it is generated from
    ``pyproject.toml``, so it cannot drift on its own and is not checked here.
    """
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert declared["project"]["version"] == __version__

    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")

        assert f"archaeology {__version__}" in readme, name

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)

    assert newest is not None, "CHANGELOG.md has no version section"
    assert newest.group(1) == __version__, "CHANGELOG.md's newest release"
