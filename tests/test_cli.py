"""Smoke tests for the command line entry point."""

from typer.testing import CliRunner

from codearchaeology import __version__
from codearchaeology.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])

    assert "Usage" in result.stdout
