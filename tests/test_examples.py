import json
from pathlib import Path
import shutil

import pytest
from typer.testing import CliRunner

from texsys.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).parents[1] / "examples"
PUBLIC_EXAMPLES = ("minimal", "hvac-cold-station", "hvac-system-schematic")


@pytest.mark.parametrize("name", PUBLIC_EXAMPLES)
def test_public_examples_match_reviewed_lint_baseline(name: str) -> None:
  root = EXAMPLES / name

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 0, result.output
  assert json.loads(result.output) == json.loads(
    (root / "expected" / "check.json").read_text()
  )


@pytest.mark.parametrize("name", PUBLIC_EXAMPLES)
def test_public_examples_are_formatted(name: str) -> None:
  root = EXAMPLES / name

  for command in ("format", "fmt"):
    result = runner.invoke(app, [command, "--check", "-p", str(root)])
    assert result.exit_code == 0, result.output


def test_invalid_example_matches_reviewed_diagnostics() -> None:
  root = EXAMPLES / "fixtures" / "invalid"

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 1
  assert json.loads(result.output) == json.loads(
    (root / "expected" / "check.json").read_text()
  )


@pytest.mark.slow
def test_large_example_is_a_deterministic_valid_project() -> None:
  root = EXAMPLES / "fixtures" / "large"

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 0, result.output
  assert json.loads(result.output) == json.loads(
    (root / "expected" / "check.json").read_text()
  )
  document = json.loads((root / "src" / "system.sdl.json").read_text())
  assert len(document["components"]["LargeLoop"]["instances"]) == 120
  assert runner.invoke(app, ["format", "--check", "-p", str(root)]).exit_code == 0


def test_writing_commands_run_on_a_copy(tmp_path: Path) -> None:
  source = EXAMPLES / "minimal"
  original = (source / "src" / "system.sdl.json").read_bytes()
  target = tmp_path / "minimal"
  shutil.copytree(source, target)
  document = json.loads((target / "src" / "system.sdl.json").read_text())
  (target / "src" / "system.sdl.json").write_text(json.dumps(document, indent=4))

  result = runner.invoke(app, ["format", "-p", str(target)])

  assert result.exit_code == 0, result.output
  assert "Formatted src/system.sdl.json" in result.output
  assert (target / "src" / "system.sdl.json").read_bytes() == original
  assert (source / "src" / "system.sdl.json").read_bytes() == original
