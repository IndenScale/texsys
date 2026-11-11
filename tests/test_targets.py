import json
from pathlib import Path
import shutil

import pytest
from typer.testing import CliRunner

from texsys.cli import app
from texsys.dependencies import add_dependency, lock_project
from texsys.evidence import write_json
from texsys.execution import build_project
from texsys.packages import TexsysPackage
from texsys.project import ProjectError, initialize_project
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
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))

  result = build_project(root, ("demo:a",), scope={"zone": "plant"})

  assert result.status == "pass"
  assert calls == ["demo:a"]
  assert not (root / "build" / "demo-b.json").exists()
  report = json.loads(result.report.read_text())
  assert report["schema_version"] == 3
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


def test_build_reports_input_changes_and_marks_outputs_partial(tmp_path, monkeypatch):
  root = copied_project(tmp_path)

  def build(context):
    write_json(context.build_path / "data.json", {"value": 1})
    (context.root / "assets/changed.txt").write_text("external edit")
    return TargetResult("pass", (Artifact("data", "build/data.json"),))

  package = FakePackage((TargetDefinition("demo:a", "fake", "1.2.3", build),))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  result = build_project(root, ("demo:a",))
  report = json.loads(result.report.read_text())
  assert result.status == report["status"] == "error"
  assert report["diagnostics"][0]["rule"] == "E_INPUT_CHANGED"
  assert report["artifacts"][0]["partial"] is True
  assert {item["path"] for item in report["inputs"]} >= {"texsys.toml", "src/system.sdl.json"}


def test_build_rejects_old_lock_after_direct_removal_of_all_dependencies(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  package = tmp_path / "local-package"
  initialize_project(package)
  package_manifest = package / "texsys.toml"
  package_manifest.write_text(package_manifest.read_text().replace(
    'name = "local-package"', 'name = "local-package"\nversion = "1"',
  ))
  project_manifest = root / "texsys.toml"
  initial_manifest = project_manifest.read_bytes()
  add_dependency(root, package)
  project_manifest.write_bytes(initial_manifest)

  target = FakePackage((definition("demo:a", []),))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (target,))
  with pytest.raises(ProjectError, match="out of date"):
    build_project(root, ("demo:a",))

  lock_project(root)
  assert build_project(root, ("demo:a",)).status == "pass"


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
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))

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
    "texsys.execution.project_packages",
    lambda root: (FakePackage((definition("demo:a", calls),)),),
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
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))

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
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))

  result = build_project(root, ("demo:broken",))

  assert result.status == "error"
  report = json.loads(result.report.read_text())
  assert report["targets"][0]["status"] == "error"
  assert report["targets"][0]["diagnostics"][0]["message"] == "backend failed"


def test_cli_accepts_repeatable_target_profile_and_scope(tmp_path: Path, monkeypatch) -> None:
  root = copied_project(tmp_path)
  calls = []
  package = FakePackage((definition("demo:a", calls), definition("demo:b", calls)))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))

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


def read_build(result):
  return json.loads(result.report.read_text())


def test_cached_build_restores_outputs_and_keeps_immutable_history(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  calls = []
  package = FakePackage((definition("demo:a", calls),))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  first = read_build(build_project(root, ("demo:a",)))
  artifact = first["artifacts"][0]
  saved = (root / artifact["stored_path"]).read_bytes()
  (root / artifact["path"]).unlink()
  second = read_build(build_project(root, ("demo:a",)))
  assert calls == ["demo:a"]
  assert first["run_id"] != second["run_id"]
  assert first["previous_run_id"] is None
  assert second["previous_run_id"] == first["run_id"]
  assert first["targets"][0]["execution"] == "executed"
  assert second["targets"][0]["execution"] == "cached"
  assert second["targets"][0]["reused_from"] == first["run_id"]
  assert (root / artifact["path"]).read_bytes() == saved
  (root / artifact["path"]).write_text("later unrelated overwrite")
  assert (root / artifact["stored_path"]).read_bytes() == saved
  assert json.loads((root / "build/runs" / first["run_id"] / "build.json").read_text()) == first


def test_corrupt_cached_bytes_rebuild_instead_of_reusing(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  calls = []
  package = FakePackage((definition("demo:a", calls),))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  first = read_build(build_project(root, ("demo:a",)))
  key = first["targets"][0]["cache_key"].removeprefix("sha256:")
  (root / "build/.cache/targets" / key / "files/build/demo-a.json").write_text("corrupt")
  second = read_build(build_project(root, ("demo:a",)))
  assert calls == ["demo:a", "demo:a"]
  assert second["targets"][0]["execution"] == "executed"
  assert json.loads((root / "build/demo-a.json").read_text())["target"] == "demo:a"


def test_only_declared_inputs_and_upstream_changes_invalidate_target_cache(tmp_path, monkeypatch):
  from dataclasses import replace

  root = copied_project(tmp_path)
  (root / "assets/a.txt").write_text("a1")
  (root / "assets/b.txt").write_text("b1")
  calls = []
  package = FakePackage((
    replace(definition("demo:a", calls), input_patterns=("assets/a.txt",)),
    replace(definition("demo:b", calls), input_patterns=("assets/b.txt",)),
    replace(definition("demo:c", calls, dependencies=("demo:a",)), input_patterns=()),
  ))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  build_project(root, ("demo:b", "demo:c"))
  (root / "assets/a.txt").write_text("a2")
  changed = read_build(build_project(root, ("demo:b", "demo:c")))
  assert calls == ["demo:b", "demo:a", "demo:c", "demo:a", "demo:c"]
  assert {t["id"]: t["execution"] for t in changed["targets"]} == {
    "demo:a": "executed", "demo:b": "cached", "demo:c": "executed",
  }


def test_profile_and_builder_identity_invalidate_cache(tmp_path, monkeypatch):
  from dataclasses import replace

  root = copied_project(tmp_path)
  calls = []
  target = definition("demo:a", calls, profiles=("engineering", "concept"))
  package = FakePackage((target,))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  first = read_build(build_project(root, ("demo:a",)))
  second = read_build(build_project(root, ("demo:a",), profile="concept"))
  package._definitions = (replace(target, package_version="1.2.4"),)
  third = read_build(build_project(root, ("demo:a",), profile="concept"))
  assert calls == ["demo:a"] * 3
  assert len({r["targets"][0]["cache_key"] for r in (first, second, third)}) == 3


def test_failed_branch_keeps_complete_outputs_and_blocks_only_its_dependents(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  calls = []
  package = FakePackage((definition("demo:bad", calls, status="fail"),
                         definition("demo:blocked", calls, dependencies=("demo:bad",)),
                         definition("demo:independent", calls)))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  result = build_project(root, ("demo:blocked", "demo:independent"))
  first = read_build(result)
  assert result.status == "fail"
  assert calls == ["demo:bad", "demo:independent"]
  rows = {r["id"]: r for r in first["targets"]}
  assert rows["demo:blocked"]["execution"] == "blocked"
  assert rows["demo:blocked"]["blocked_by"] == ["demo:bad"]
  assert rows["demo:independent"]["status"] == "pass"
  assert all(item["complete"] and not item["partial"] for item in first["artifacts"])
  second = read_build(build_project(root, ("demo:blocked", "demo:independent")))
  assert calls == ["demo:bad", "demo:independent"]
  assert {r["execution"] for r in second["targets"]} == {"cached", "blocked"}
  assert second["status"] == "fail"


def test_execution_errors_are_never_cached_and_independent_target_still_runs(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  calls = []

  def crash(context):
    calls.append("crash")
    raise ValueError("no solver available")

  package = FakePackage((TargetDefinition("demo:crash", "fake", "1", crash),
                         definition("demo:ok", calls)))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  for _ in range(2):
    assert build_project(root, ("demo:crash", "demo:ok")).status == "error"
  assert calls == ["crash", "demo:ok", "crash"]


def test_incomplete_artifact_is_not_cached_even_when_target_passes(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  calls = []

  def partial(context):
    calls.append("partial")
    write_json(context.build_path / "partial.json", {"unresolved": ["pump"]})
    return TargetResult("pass", (Artifact("partial", "build/partial.json", complete=False,
                                          preview={"kind": "bom", "root": "Station"}),))

  package = FakePackage((TargetDefinition("demo:partial", "fake", "1", partial),))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  for _ in range(2):
    report = read_build(build_project(root, ("demo:partial",)))
    assert report["artifacts"][0]["partial"] is True
    assert report["artifacts"][0]["preview"] == {"kind": "bom", "root": "Station"}
  assert calls == ["partial", "partial"]


def test_input_race_does_not_publish_cache_or_current_evidence(tmp_path, monkeypatch):
  from texsys.build_records import build_freshness

  root = copied_project(tmp_path)

  def mutate(context):
    write_json(context.build_path / "data.json", {"value": 1})
    (context.root / "assets/raced.txt").write_text("changed")
    return TargetResult("pass", (Artifact("data", "build/data.json"),))

  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (
    FakePackage((TargetDefinition("demo:a", "fake", "1", mutate),)),
  ))
  report = read_build(build_project(root, ("demo:a",)))
  assert report["inputs_consistent"] is False
  assert not (root / "build/.cache/targets").exists()
  assert build_freshness(root, report) == {"current": False, "freshness": "input-inconsistent"}


def test_build_freshness_detects_input_change_and_damaged_record(tmp_path, monkeypatch):
  from texsys.build_records import build_freshness

  root = copied_project(tmp_path)
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (
    FakePackage((definition("demo:a", []),)),
  ))
  report = read_build(build_project(root, ("demo:a",)))
  assert build_freshness(root, report) == {"current": True, "freshness": "current"}
  (root / "assets/new.txt").write_text("new input")
  assert build_freshness(root, report)["freshness"] == "inputs-changed"
  report["status"] = "fail"
  assert build_freshness(root, report)["freshness"] == "invalid-record"


def test_preflight_failure_does_not_suppress_independent_diagnostics(tmp_path, monkeypatch):
  from texsys.formal import CheckResult, Diagnostic

  root = copied_project(tmp_path)
  calls = []
  monkeypatch.setattr("texsys.execution.check_project", lambda root: CheckResult(
    "fail", 1, (Diagnostic("F001", "fail", "src/system.sdl.json", "unresolved connection"),),
  ))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (
    FakePackage((definition("demo:a", calls),)),
  ))
  report = read_build(build_project(root, ("demo:a",)))
  assert report["status"] == "fail"
  assert report["preflight"]["diagnostics"][0]["rule"] == "F001"
  assert calls == ["demo:a"]
  assert report["artifacts"][0]["complete"] is True


def test_concurrent_build_lock_fails_without_touching_owner(tmp_path, monkeypatch):
  from texsys.build_records import build_lock

  root = copied_project(tmp_path)
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (
    FakePackage((definition("demo:a", []),)),
  ))
  with build_lock(root, "build"):
    with pytest.raises(ProjectError, match="Another build"):
      build_project(root, ("demo:a",))
    assert (root / "build/.build.lock/owner.json").exists()
  assert not (root / "build/.build.lock").exists()


def test_callable_inputs_cache_policy_and_declared_environment(tmp_path, monkeypatch):
  from dataclasses import replace

  root = copied_project(tmp_path)
  calls = []
  (root / "assets/model.txt").write_text("input")
  target = replace(definition("demo:a", calls), input_patterns=lambda context: ("assets/model.txt",),
                   cache=lambda context: not context.scope.get("external"),
                   cache_environment=("TEXSYS_TEST_TOOL_VERSION",))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (FakePackage((target,)),))
  monkeypatch.setenv("TEXSYS_TEST_TOOL_VERSION", "1")
  build_project(root, ("demo:a",))
  monkeypatch.setenv("SHLVL", "987")
  monkeypatch.setenv("_", "different-launcher")
  (root / "assets/unrelated.txt").write_text("unrelated")
  assert read_build(build_project(root, ("demo:a",)))["targets"][0]["execution"] == "cached"
  monkeypatch.setenv("TEXSYS_TEST_TOOL_VERSION", "2")
  assert read_build(build_project(root, ("demo:a",)))["targets"][0]["execution"] == "executed"
  for _ in range(2):
    result = read_build(build_project(root, ("demo:a",), scope={"external": True}))
    assert result["targets"][0]["cache_enabled"] is False
  assert calls == ["demo:a"] * 4


def test_input_selector_error_keeps_independent_branch_running(tmp_path, monkeypatch):
  from dataclasses import replace

  root = copied_project(tmp_path)
  calls = []

  def broken_selector(context):
    raise RuntimeError("invalid reference")

  package = FakePackage((replace(definition("demo:bad", calls), input_patterns=broken_selector),
                         definition("demo:ok", calls)))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  result = read_build(build_project(root, ("demo:bad", "demo:ok")))
  assert calls == ["demo:ok"]
  assert result["status"] == "error"
  assert [target["status"] for target in result["targets"]] == ["error", "pass"]


def test_changed_runtime_versions_invalidate_freshness_and_target_cache(tmp_path, monkeypatch):
  from texsys.build_records import build_freshness

  root = copied_project(tmp_path)
  calls = []
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (
    FakePackage((definition("demo:a", calls),)),
  ))
  report = read_build(build_project(root, ("demo:a",)))
  monkeypatch.setattr("texsys.build_records.distribution_versions", lambda: [["solver", "different"]])
  assert build_freshness(root, report)["freshness"] == "tools-changed"
  assert read_build(build_project(root, ("demo:a",)))["targets"][0]["execution"] == "executed"
  assert calls == ["demo:a", "demo:a"]
