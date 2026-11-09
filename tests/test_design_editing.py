import json
from pathlib import Path
import shutil

import pytest
from typer.testing import CliRunner

from texsys import api
from texsys.cli import app
from texsys.project import initialize_project, project_write_lock

ROOT = Path(__file__).parents[1]
runner = CliRunner()


def request(project, operations):
  state = api.read_design(project)
  return {"version": 1, "source": state["source"], "revision": state["revision"], "operations": operations}


def new_loop(kind="water"):
  return [
    {"op": "create-design", "id": "Loop"},
    {"op": "declare-component", "id": "Pump", "definition": {"ports": {
      "inlet": {"kind": "water", "direction": "in"}, "outlet": {"kind": "water", "direction": "out"},
    }}},
    {"op": "add-instance", "id": "first", "component": "Pump"},
    {"op": "add-instance", "id": "second", "component": "Pump"},
    {"op": "add-connection", "id": "feed", "from": "first.outlet", "to": "second.inlet"},
    {"op": "declare-net", "id": "Water", "kind": kind, "connections": ["feed"]},
  ]


def test_cli_batch_previews_and_atomically_creates_design(tmp_path):
  initialize_project(tmp_path)
  cli = ["design", "inspect", "-p", str(tmp_path)]
  state = json.loads(runner.invoke(app, cli).output)
  assert state["document"] is None
  batch = {"version": 1, "source": state["source"], "revision": state["revision"], "operations": new_loop()}
  preview = runner.invoke(app, ["design", "plan", "-", "-p", str(tmp_path)], input=json.dumps(batch))
  assert preview.exit_code == 0, preview.output
  plan = json.loads(preview.output)
  assert "+++ b/src/system.sdl.json" in plan["diff"]
  assert not (tmp_path / state["file"]).exists()

  apply_args = ["design", "apply", "-", "--plan", plan["plan"], "-p", str(tmp_path)]
  changed = {**batch, "operations": [*batch["operations"], {"op": "add-instance", "id": "extra", "component": "Pump"}]}
  mismatch = runner.invoke(app, apply_args, input=json.dumps(changed))
  assert mismatch.exit_code == 2
  assert not (tmp_path / state["file"]).exists()

  applied = runner.invoke(app, apply_args, input=json.dumps(batch))
  assert applied.exit_code == 0, applied.output
  result = json.loads(applied.output)
  assert result["applied"] is True
  current = api.read_design(tmp_path)
  assert current["revision"] == result["revision"] != state["revision"]
  assert current["document"]["components"]["Loop"]["nets"]["Water"]["connections"] == ["feed"]
  assert api.check(tmp_path).status == "pass"
  assert runner.invoke(app, apply_args, input=json.dumps(batch)).exit_code == 3

  update = request(tmp_path, [{"op": "set-net-members", "id": "Water", "connections": []}])
  update_plan = api.plan_edit(tmp_path, update)
  assert api.apply_edit(tmp_path, update, update_plan["plan"])["applied"] is True
  updated = api.read_design(tmp_path)["document"]["components"]["Loop"]
  assert updated["nets"]["Water"]["connections"] == []
  assert updated["connections"][0]["id"] == "feed"


def test_rejected_batch_does_not_write_and_changed_inputs_conflict(tmp_path):
  initialize_project(tmp_path)
  bad = request(tmp_path, new_loop("air"))
  rejected = api.plan_edit(tmp_path, bad)
  assert rejected["status"] == "fail"
  assert rejected["diagnostics"][0]["rule"] == "L013"
  assert api.apply_edit(tmp_path, bad, "invalid")["status"] == "fail"
  assert api.read_design(tmp_path)["document"] is None

  batch = request(tmp_path, new_loop())
  plan = api.plan_edit(tmp_path, batch)
  (tmp_path / "assets/new.txt").write_text("external edit", encoding="utf-8")
  assert api.apply_edit(tmp_path, batch, plan["plan"])["status"] == "conflict"
  assert api.read_design(tmp_path)["document"] is None


def test_plan_rejects_path_escape_and_symlink(tmp_path):
  project = tmp_path / "project"
  initialize_project(project)
  outside = tmp_path / "other.sdl.json"
  outside.write_text("{}", encoding="utf-8")
  (project / "src/link.sdl.json").symlink_to(outside)
  for source in ("../../other.sdl.json", "link.sdl.json", str(outside)):
    with pytest.raises(api.ProjectError):
      api.read_design(project, source)
  assert outside.read_text(encoding="utf-8") == "{}"


def test_api_and_direct_edit_produce_same_cold_station_artifacts(tmp_path):
  project = tmp_path / "station"
  shutil.copytree(ROOT / "examples/hvac-system-schematic", project, ignore=shutil.ignore_patterns("build"))
  manifest = project / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))
  api.lock_dependencies(project)
  source = project / "src/system.sdl.json"
  direct = json.loads(source.read_text())
  direct["schema_version"] = 2
  root = direct["components"][direct["root"]]
  operations = []
  for index, connection in enumerate(root["connections"]):
    name = f"link-{index}"
    operations.append({"op": "name-connection", "id": name, **connection})
    connection["id"] = name
  root["nets"] = {
    "CHW": {"kind": "chilled-water", "connections": ["link-0", "link-1", "link-2"]},
    "CW": {"kind": "cooling-water", "connections": ["link-3", "link-4", "link-5"]},
  }
  operations.extend({"op": "declare-net", "id": name, **net} for name, net in root["nets"].items())
  batch = request(project, operations)
  preview = api.plan_edit(project, batch)
  assert preview["status"] == "pass", preview
  assert api.apply_edit(project, batch, preview["plan"])["applied"] is True
  assert api.read_design(project)["document"] == direct
  assert api.build(project, scope={"view": "system"}).status == "pass"
  diagram = project / "build/hvac/system/system.json"
  first = diagram.read_bytes()

  shutil.rmtree(project / "build")
  source.write_text(json.dumps(direct), encoding="utf-8")
  assert api.build(project, scope={"view": "system"}).status == "pass"
  assert diagram.read_bytes() == first


def test_dependency_api_shares_writer_lock_and_restores_inputs_on_failure(tmp_path, monkeypatch):
  project = tmp_path / "project"
  package = tmp_path / "lib"
  initialize_project(project)
  initialize_project(package)
  manifest = package / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('name = "lib"', 'name = "lib"\nversion = "1"'))
  batch = request(project, new_loop())
  planned = api.plan_edit(project, batch)
  with project_write_lock(project):
    with pytest.raises(api.ProjectError, match="locked"):
      api.apply_edit(project, batch, planned["plan"])
    with pytest.raises(api.ProjectError, match="locked"):
      api.add_dependency(project, package)

  assert api.add_dependency(project, package, "1").packages[0].name == "lib"
  assert api.lock_dependencies(project, check=True).packages[0].version == "1"
  assert api.sync_dependencies(project)[0].name == "lib"
  assert api.apply_edit(project, batch, planned["plan"])["status"] == "conflict"
  files = [project / "texsys.toml", project / "texsys.lock"]
  before = [file.read_bytes() for file in files]

  def fail_sync(_root):
    raise api.ProjectError("sync failed")

  with monkeypatch.context() as patch:
    patch.setattr("texsys.dependencies._sync_project", fail_sync)
    with pytest.raises(api.ProjectError, match="sync failed"):
      api.remove_dependency(project, "lib")
  assert [file.read_bytes() for file in files] == before
  assert api.remove_dependency(project, "lib").packages == ()
