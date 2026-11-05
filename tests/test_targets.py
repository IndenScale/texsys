import json
from pathlib import Path
import shutil

import pytest
from typer.testing import CliRunner

from texsys.cli import app
from texsys.evidence import write_json
from texsys.execution import build_project
from texsys.packages import TexsysPackage
from texsys.project import ProjectError
from texsys.targets import Artifact, TargetDefinition, TargetResult

runner = CliRunner()
EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal"


class FakePackage(TexsysPackage):
  name = "fake"
  version = "1.2.3"

  def __init__(self, definitions):
    self._definitions = tuple(definitions)

  def targets(self):
    return self._definitions


def copied_project(tmp_path: Path) -> Path:
  root = tmp_path / "project"
  shutil.copytree(EXAMPLE, root)
  return root


def definition(identifier, calls, dependencies=(), profiles=("engineering",), status="pass"):
  def build(context):
    calls.append(identifier)
    name = identifier.replace(":", "-")
    artifact = context.build_path / f"{name}.json"
    write_json(
      artifact,
      {"target": context.target, "profile": context.profile, "scope": dict(context.scope)},
    )
    return TargetResult(status, (Artifact("data", artifact.relative_to(context.root).as_posix()),))

  return TargetDefinition(
    identifier,
    "fake",
    "1.2.3",
    build,
    profiles=profiles,
    default_profile=profiles[0] if profiles else None,
    dependencies=dependencies,
  )


def test_explicit_target_builds_only_selected_contract(tmp_path: Path, monkeypatch) -> None:
  root = copied_project(tmp_path)
  calls = []
  package = FakePackage((definition("demo:a", calls), definition("demo:b", calls)))
  monkeypatch.setattr("texsys.execution.installed_packages", lambda: (package,))

  result = build_project(root, ("demo:a",), scope={"zone": "plant"})

  assert result.status == "pass"
  assert calls == ["demo:a"]
  assert not (root / "build" / "demo-b.json").exists()
  report = json.loads(result.report.read_text())
  assert report["schema_version"] == 2
  assert report["requested_targets"] == ["demo:a"]
  assert report["resolved_targets"] == [
    {
      "id": "demo:a",
      "profile": "engineering",
      "scope": {"zone": "plant"},
      "resolution_source": "cli",
      "dependencies": [],
    }
  ]
  assert report["artifacts"][0]["id"] == "data"
  assert report["artifacts"][0]["partial"] is False
  assert report["packages"] == [{"name": "fake", "version": "1.2.3"}]


def test_default_targets_resolve_dependencies_once_in_order(tmp_path: Path, monkeypatch) -> None:
  root = copied_project(tmp_path)
  root.joinpath("texsys.toml").write_text(
    root.joinpath("texsys.toml").read_text() + '\n[build]\ndefault_targets = ["demo:a", "demo:b"]\n'
  )
  calls = []
  package = FakePackage(
    (
      definition("demo:a", calls, dependencies=("demo:b", "demo:c")),
      definition("demo:b", calls, dependencies=("demo:c",)),
      definition("demo:c", calls),
    )
  )
  monkeypatch.setattr("texsys.execution.installed_packages", lambda: (package,))

  result = build_project(root)

  assert result.status == "pass"
  assert calls == ["demo:c", "demo:b", "demo:a"]
  assert [item.definition.id for item in result.targets] == ["demo:c", "demo:b", "demo:a"]


def test_stage_binding_resolves_default_targets(tmp_path: Path, monkeypatch) -> None:
  root = copied_project(tmp_path)
  root.joinpath("texsys.toml").write_text(
    root.joinpath("texsys.toml").read_text()
    + '\n[disciplines.hvac]\ncurrent_stage = "hvac:concept"\n'
    + '\n[build.stage_targets]\n"hvac:concept" = ["demo:a"]\n'
  )
  calls = []
  monkeypatch.setattr(
    "texsys.execution.installed_packages",
    lambda: (FakePackage((definition("demo:a", calls),)),),
  )

  result = build_project(root)

  assert result.status == "pass"
  assert result.targets[0].resolution_source == "build.stage_targets"


def test_target_resolution_rejects_missing_unknown_cycle_and_profile(tmp_path: Path, monkeypatch) -> None:
  root = copied_project(tmp_path)
  calls = []
  package = FakePackage(
    (
      definition("demo:a", calls, dependencies=("demo:b",)),
      definition("demo:b", calls, dependencies=("demo:a",)),
      definition("demo:c", calls),
    )
  )
  monkeypatch.setattr("texsys.execution.installed_packages", lambda: (package,))

  with pytest.raises(ProjectError, match="No build target selected"):
    build_project(root)
  with pytest.raises(ProjectError, match="Unknown build target"):
    build_project(root, ("demo:missing",))
  with pytest.raises(ProjectError, match="dependency cycle"):
    build_project(root, ("demo:a",))
  with pytest.raises(ProjectError, match="does not support profile"):
    build_project(root, ("demo:c",), profile="feasibility")


def test_target_failure_is_structured_build_error(tmp_path: Path, monkeypatch) -> None:
  root = copied_project(tmp_path)

  def broken(context):
    raise ValueError("backend failed")

  package = FakePackage((TargetDefinition("demo:broken", "fake", "1.2.3", broken),))
  monkeypatch.setattr("texsys.execution.installed_packages", lambda: (package,))

  result = build_project(root, ("demo:broken",))

  assert result.status == "error"
  report = json.loads(result.report.read_text())
  assert report["targets"][0]["status"] == "error"
  assert report["targets"][0]["diagnostics"][0]["message"] == "backend failed"


def test_cli_accepts_repeatable_target_profile_and_scope(tmp_path: Path, monkeypatch) -> None:
  root = copied_project(tmp_path)
  calls = []
  package = FakePackage((definition("demo:a", calls), definition("demo:b", calls)))
  monkeypatch.setattr("texsys.execution.installed_packages", lambda: (package,))

  result = runner.invoke(
    app,
    [
      "build",
      "--target",
      "demo:a",
      "--target",
      "demo:b",
      "--profile",
      "engineering",
      "--scope",
      '{"zone":"plant"}',
      "-p",
      str(root),
    ],
  )

  assert result.exit_code == 0, result.output
  assert "Target demo:a (profile=engineering, source=cli)" in result.output
  assert calls == ["demo:a", "demo:b"]
