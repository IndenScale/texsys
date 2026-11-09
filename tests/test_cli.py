from importlib.metadata import version as package_version

from typer.testing import CliRunner

from texsys.cli import app

runner = CliRunner()


def test_help() -> None:
  result = runner.invoke(app, ["--help"])

  assert result.exit_code == 0
  assert "Usage:" in result.output
  assert "--help" in result.output
  assert "--version" in result.output
  assert "init" in result.output
  assert "info" in result.output
  assert "check" in result.output
  assert "lint" in result.output
  assert "--install-completion" not in result.output


def test_version() -> None:
  result = runner.invoke(app, ["--version"])

  assert result.exit_code == 0
  assert result.output.strip() == f"texsys {package_version('texsys')}"


def test_unknown_option_fails() -> None:
  result = runner.invoke(app, ["--unknown"])

  assert result.exit_code != 0
  assert "No such option" in result.output
