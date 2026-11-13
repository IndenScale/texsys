from importlib.metadata import version as package_version
import re

from typer.testing import CliRunner

from texsys.cli import app

runner = CliRunner()


def test_help() -> None:
  result = runner.invoke(app, ["--help"])
  output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)

  assert result.exit_code == 0
  assert "Usage:" in output
  assert "--help" in output
  assert "--version" in output
  assert "init" in output
  assert "info" in output
  assert "check" in output
  assert "lint" in output
  assert "--install-completion" not in output


def test_version() -> None:
  result = runner.invoke(app, ["--version"])

  assert result.exit_code == 0
  assert result.output.strip() == f"texsys {package_version('texsys')}"


def test_unknown_option_fails() -> None:
  result = runner.invoke(app, ["--unknown"])

  assert result.exit_code != 0
  assert "No such option" in result.output
