import json
import importlib.util
import sys

import pytest

from texsys import engineering
from texsys.dependencies import add_dependency, lock_project
from texsys.packages import TexsysPackage
from texsys.project import ProjectError, initialize_project, project_write_lock


class WaterAdapter:
  id = "water:route-check"
  name = "管路检查"
  version = "1"

  def supports(self, root):
    return (root / "src/route.study.json").is_file()

  def inputs(self, root):
    value = json.loads((root / "src/route.study.json").read_text())["route"]["length_m"]
    return [{"id": "length", "label": "管路长度", "file": "src/route.study.json",
             "pointer": "/route/length_m", "unit": "m", "object_ref": "water.Route-01",
             "value": value, "min": 1, "max": 100}]

  def evaluate(self, root):
    length = self.inputs(root)[0]["value"]
    actual = 2 * length
    status = "pass" if actual <= 40 else "fail"
    return {"assessment": status, "verification": "pass", "checks": [
      {"id": "pressure-budget", "label": "压损预算", "object_ref": "water.Pump-01", "status": status,
       "actual": actual, "limit": 40, "unit": "kPa", "scenario": "design"}],
      "extraction": [{"id": "length", "actual": length, "unit": "m", "source": "src/route.study.json"}],
      "limitations": ["Concept model with assumed roughness."], "targets": ["water:route-study"]}


class WaterPackage(TexsysPackage):
  name = "water"
  version = "1"

  def __init__(self, adapter):
    self.adapter = adapter

  def engineering_adapters(self):
    return (self.adapter,)


@pytest.fixture
def project(tmp_path, monkeypatch):
  root = tmp_path / "project"
  initialize_project(root)
  (root / "src/route.study.json").write_text(json.dumps({"route": {"length_m": 10, "name": "Route-01"}}))
  adapter = WaterAdapter()
  monkeypatch.setattr(engineering, "project_packages", lambda root: (WaterPackage(adapter),))
  monkeypatch.setattr(engineering, "_worker_call", engineering._dispatch)
  return root, adapter


def write_length(root, value):
  path = root / "src/route.study.json"
  document = json.loads(path.read_text())
  document["route"]["length_m"] = value
  path.write_text(json.dumps(document))


def test_source_edit_recompute_and_repair_preserve_run_evidence(project):
  root, adapter = project
  source = root / "src/route.study.json"
  before = source.read_bytes()
  initial = engineering.run_engineering(root, adapter.id)
  assert initial["execution"] == "completed" and initial["result"]["assessment"] == "pass"
  assert initial["current"] and initial["result"]["limitations"] == ["Concept model with assumed roughness."]
  assert source.read_bytes() == before
  saved_initial = (root / initial["path"]).read_bytes()
  write_length(root, 40)
  state = engineering.inspect_engineering(root)
  assert state["latest"]["freshness"] == "inputs-changed"
  failed = engineering.run_engineering(root, adapter.id)
  assert failed["current"] and failed["execution"] == "completed"
  assert failed["result"]["verification"] == "pass" and failed["result"]["assessment"] == "fail"
  comparison = engineering.inspect_engineering(root)["comparison"]
  assert comparison["base_run_id"] == initial["run_id"]
  assert comparison["checks"][0]["before"]["actual"] == 20
  assert comparison["checks"][0]["after"]["actual"] == 80
  write_length(root, 15)
  repaired = engineering.run_engineering(root, adapter.id)
  assert repaired["result"]["assessment"] == "pass"
  assert engineering.inspect_engineering(root)["revision"] == repaired["revision"]
  rerun = engineering.run_engineering(root, adapter.id)
  assert rerun["run_id"] != repaired["run_id"]
  assert engineering.inspect_engineering(root)["comparison"]["checks"] == []
  assert (root / initial["path"]).read_bytes() == saved_initial
  write_length(root, 16)
  assert all(not item["current"] for item in engineering.inspect_engineering(root)["runs"])


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), "20", None, 10 ** 1000])
def test_invalid_numeric_source_is_rejected_without_being_rewritten(project, value):
  root, adapter = project
  write_length(root, value)
  before = (root / "src/route.study.json").read_bytes()
  with pytest.raises(ProjectError):
    engineering.run_engineering(root, adapter.id)
  assert (root / "src/route.study.json").read_bytes() == before
  assert not list((root / "build/engineering/runs").glob("*.json"))


@pytest.mark.parametrize("design", [{"definition": "water.Route"}, {"occurrence": ["plant", "route"]}])
def test_explicit_design_selection_survives_inspection_and_run(project, monkeypatch, design):
  root, adapter = project
  original_inputs, original_evaluate = adapter.inputs, adapter.evaluate
  monkeypatch.setattr(adapter, "inputs", lambda root: [{**original_inputs(root)[0], "design": design}])

  def evaluate(root):
    result = original_evaluate(root)
    result["checks"][0]["design"] = design
    result["extraction"][0]["design"] = design
    return result

  monkeypatch.setattr(adapter, "evaluate", evaluate)
  state = engineering.inspect_engineering(root)
  assert state["inputs"][0]["design"] == design
  assert state["inputs"][0]["object_ref"] == "water.Route-01"
  write_length(root, 12)
  run = engineering.run_engineering(root, adapter.id)
  assert run["input_values"][0]["design"] == design
  assert run["result"]["checks"][0]["design"] == design
  assert run["result"]["checks"][0]["object_ref"] == "water.Pump-01"
  assert run["result"]["extraction"][0]["design"] == design


@pytest.mark.parametrize("design", [
  None, True, {}, {"definition": ""}, {"definition": " "}, {"definition": True},
  {"occurrence": []}, {"occurrence": "route"}, {"occurrence": [""]},
  {"occurrence": ["plant", " "]}, {"occurrence": [True]},
  {"definition": "water.Route", "occurrence": ["route"]},
  {"definition": "water.Route", "extra": "unchecked"},
])
def test_invalid_input_design_is_rejected_before_evaluation(project, monkeypatch, design):
  root, adapter = project
  original = adapter.inputs
  before = (root / "src/route.study.json").read_bytes()
  monkeypatch.setattr(adapter, "inputs", lambda root: [{**original(root)[0], "design": design}])
  with pytest.raises(ProjectError, match="design selection"):
    engineering.run_engineering(root, adapter.id)
  assert (root / "src/route.study.json").read_bytes() == before
  assert not list((root / "build/engineering/runs").glob("*.json"))


@pytest.mark.parametrize("collection, design", [
  ("checks", {"definition": False}), ("checks", {"occurrence": []}),
  ("extraction", {"occurrence": ["plant", True]}),
  ("extraction", {"definition": "water.Route", "extra": "unchecked"}),
])
def test_invalid_result_design_is_not_saved_as_engineering_evidence(project, monkeypatch, collection, design):
  root, adapter = project
  original = adapter.evaluate

  def evaluate(root):
    result = original(root)
    result[collection][0]["design"] = design
    return result

  monkeypatch.setattr(adapter, "evaluate", evaluate)
  run = engineering.run_engineering(root, adapter.id)
  assert run["execution"] == "error" and run["result"]["assessment"] == "indeterminate"
  assert "design selection" in run["diagnostics"][0]["message"]
  saved = json.loads((root / run["path"]).read_text())
  assert saved["result"]["checks"] == [] and saved["result"]["extraction"] == []


def test_optional_adapter_description_and_design_are_not_inferred(project, monkeypatch):
  root, adapter = project
  state = engineering.inspect_engineering(root)
  assert "description" not in state["selected"] and "design" not in state["inputs"][0]
  run = engineering.run_engineering(root, adapter.id)
  assert "design" not in run["result"]["checks"][0]
  monkeypatch.setattr(adapter, "description", "管路长度对压损预算的影响。", raising=False)
  state = engineering.inspect_engineering(root)
  assert state["selected"]["description"] == "管路长度对压损预算的影响。"
  assert state["adapters"] == [state["selected"]]


@pytest.mark.parametrize("description", [None, True, ["invalid"]])
def test_invalid_adapter_description_is_rejected(project, monkeypatch, description):
  root, adapter = project
  monkeypatch.setattr(adapter, "description", description, raising=False)
  with pytest.raises(ProjectError, match="description must be a string"):
    engineering.inspect_engineering(root)


def test_input_provenance_is_recorded_from_each_source_revision(project, monkeypatch):
  root, adapter = project
  source = root / "src/route.study.json"
  document = json.loads(source.read_text())
  document["route"].update(state="known", source="Measured route, survey A")
  source.write_text(json.dumps(document))
  original = adapter.inputs

  def inputs(root):
    route = json.loads(source.read_text())["route"]
    return [{**original(root)[0], "state": route["state"], "source": route["source"]}]

  monkeypatch.setattr(adapter, "inputs", inputs)
  before = source.read_bytes()
  measured = engineering.run_engineering(root, adapter.id)
  assert measured["execution"] == "completed" and measured["input_values"][0]["state"] == "known"
  assert source.read_bytes() == before
  document["route"].update(length_m=12, state="assumed", source="Design alternative B, assumed route")
  source.write_text(json.dumps(document))
  alternative = engineering.run_engineering(root, adapter.id)
  assert alternative["input_values"][0]["state"] == "assumed"
  assert alternative["input_values"][0]["source"] == "Design alternative B, assumed route"
  assert measured["input_values"][0]["source"] == "Measured route, survey A"
  assert engineering.inspect_engineering(root)["runs"][1]["freshness"] == "inputs-changed"


@pytest.mark.parametrize("state", ["unknown", "missing", "not-applicable", "failed", None, True, "estimated", {}])
def test_numeric_evidence_rejects_non_numeric_or_invalid_input_state(project, monkeypatch, state):
  root, adapter = project
  original = adapter.inputs
  before = (root / "src/route.study.json").read_bytes()
  monkeypatch.setattr(adapter, "inputs", lambda root: [{**original(root)[0], "state": state}])
  with pytest.raises(ProjectError, match="input.*state"):
    engineering.run_engineering(root, adapter.id)
  assert (root / "src/route.study.json").read_bytes() == before


def test_existing_project_lock_serializes_runs(project):
  root, adapter = project
  with project_write_lock(root):
    with pytest.raises(ProjectError, match="locked"):
      engineering.run_engineering(root, adapter.id)


def test_external_input_mutation_during_run_invalidates_evidence(project, monkeypatch):
  root, adapter = project
  original = adapter.evaluate

  def mutate(path):
    result = original(path)
    (path / "assets/concurrent.txt").write_text("Changed by another editor.")
    return result

  monkeypatch.setattr(adapter, "evaluate", mutate)
  run = engineering.run_engineering(root, adapter.id)
  assert run["execution"] == "error" and not run["inputs_consistent"]
  assert run["result"]["assessment"] == "pass"  # Separate from execution/input validity.
  assert not run["current"]
  assert run["diagnostics"][0]["code"] == "ENGINEERING_INPUT_CHANGED"
  assert (root / run["path"]).is_file()


def test_deleted_adapter_input_during_run_still_records_execution_failure(project, monkeypatch):
  root, adapter = project
  original = adapter.evaluate

  def remove_source(path):
    result = original(path)
    (path / "src/route.study.json").unlink()
    return result

  monkeypatch.setattr(adapter, "evaluate", remove_source)
  run = engineering.run_engineering(root, adapter.id)
  assert run["execution"] == "error" and not run["current"]
  assert (root / run["path"]).is_file()


def test_input_change_while_capturing_tools_is_rejected(project, monkeypatch):
  root, adapter = project
  original = engineering._toolchain

  def mutate(packages, adapter):
    result = original(packages, adapter)
    write_length(root, 80)
    return result

  monkeypatch.setattr(engineering, "_toolchain", mutate)
  with pytest.raises(ProjectError, match="changed while capturing"):
    engineering.run_engineering(root, adapter.id)
  assert not list((root / "build/engineering/runs").glob("*.json"))


def test_missing_dependency_invalidates_existing_evidence(project):
  root, adapter = project
  engineering.run_engineering(root, adapter.id)
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text() + '\n[dependencies]\nmissing = {path = "../missing", version = "1"}\n')
  state = engineering.inspect_engineering(root)
  assert state["dependency_error"]
  assert state["latest"]["freshness"] == "dependency-invalid"
  with pytest.raises(ProjectError):
    engineering.run_engineering(root, adapter.id)


def test_adapter_version_change_invalidates_existing_evidence(project):
  root, adapter = project
  engineering.run_engineering(root, adapter.id)
  adapter.version = "2"
  state = engineering.inspect_engineering(root)
  assert state["latest"]["freshness"] == "tools-changed" and not state["latest"]["current"]
  assert engineering.run_engineering(root, adapter.id)["current"]


def test_tampered_evidence_is_visible_as_diagnostic(project):
  root, adapter = project
  run = engineering.run_engineering(root, adapter.id)
  path = root / run["path"]
  record = json.loads(path.read_text())
  record["result"]["checks"][0]["actual"] = 0
  path.write_text(json.dumps(record))
  state = engineering.inspect_engineering(root)
  assert state["latest"] is None and "integrity" in state["diagnostics"][0]["message"]


def test_run_errors_and_invalid_aggregate_do_not_claim_success(project, monkeypatch):
  root, adapter = project
  original = adapter.evaluate

  def malformed(path):
    result = original(path)
    result["checks"][0]["status"] = "fail"
    return result

  monkeypatch.setattr(adapter, "evaluate", malformed)
  run = engineering.run_engineering(root, adapter.id)
  assert run["execution"] == "error" and run["result"]["assessment"] == "indeterminate"
  assert "cannot hide" in run["diagnostics"][0]["message"]


def test_adapter_inputs_stay_in_source_and_can_span_files(project, monkeypatch, tmp_path):
  root, adapter = project
  original = adapter.inputs
  outside = tmp_path / "outside.json"
  outside.write_text('{"route":{"length_m":10}}')
  (root / "src/link.json").symlink_to(outside)
  for path in ("../outside.json", "assets/other.json", "src/link.json"):
    monkeypatch.setattr(adapter, "inputs", lambda root, path=path: [{**original(root)[0], "file": path}])
    with pytest.raises(ProjectError):
      engineering.inspect_engineering(root)
  (root / "src/link.json").unlink()
  other = root / "src/other.json"
  other.write_text(outside.read_text())
  monkeypatch.setattr(adapter, "inputs", lambda root: [original(root)[0], {**original(root)[0], "id": "other", "file": "src/other.json"}])
  state = engineering.inspect_engineering(root)
  assert {item["file"] for item in state["inputs"]} == {"src/route.study.json", "src/other.json"}
  assert json.loads(other.read_text())["route"]["length_m"] == 10


def test_unsupported_project_has_no_engineering_adapter(tmp_path, monkeypatch):
  initialize_project(tmp_path)
  monkeypatch.setattr(engineering, "project_packages", lambda root: (WaterPackage(WaterAdapter()),))
  monkeypatch.setattr(engineering, "_worker_call", engineering._dispatch)
  state = engineering.inspect_engineering(tmp_path)
  assert not state["available"] and state["selected"] is None and state["runs"] == []


def test_fresh_worker_uses_current_code_for_inputs_and_runs(tmp_path, monkeypatch):
  """A long-lived caller must never label old loaded code with new disk hashes."""
  code = '''import json
import os
from pathlib import Path
from texsys.packages import TexsysPackage
FACTOR = 1
class Adapter:
  id = "audit:fresh-worker"
  name = "Fresh worker audit"
  version = "1"
  def supports(self, root): return (root / "src/input.json").is_file()
  def inputs(self, root):
    value=json.loads((root / "src/input.json").read_text())["length"]
    return [{"id":"length","label":f"Length factor {FACTOR}","file":"src/input.json","pointer":"/length","unit":"m","object_ref":"Route","value":value,"min":1,"max":100}]
  def evaluate(self, root):
    print("adapter log must not corrupt worker JSON")
    if os.environ.get("TEXSYS_AUDIT_PAUSE"):
      import time
      (root / "build/worker-sleep-started").write_text("started")
      time.sleep(60)
    value=self.inputs(root)[0]["value"] * FACTOR
    status="pass" if value <= 40 else "fail"
    if os.environ.get("TEXSYS_AUDIT_MUTATE_SOURCE"):
      source=Path(__file__)
      source.write_text(source.read_text().replace("FACTOR = 1000", "FACTOR = 2000"))
    return {"assessment":status,"checks":[{"id":"capacity","label":"Capacity","object_ref":"Route","status":status,"actual":value,"limit":40}],"limitations":["audit"]}
class Package(TexsysPackage):
  name="worker-audit"
  version="1"
  def engineering_adapters(self): return (Adapter(),)
package=Package()
'''
  capability = tmp_path / "worker-audit"
  initialize_project(capability)
  manifest = capability / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('name = "worker-audit"', 'name = "worker-audit"\nversion = "1"')
                      + '\n[package]\nkind = "capability"\n[package.runtime]\napi_version = 1\n'
                      + 'modules = ["engineering_worker_audit"]\nentry_point = "engineering_worker_audit:package"\n')
  source = capability / "src/engineering_worker_audit.py"
  source.write_text(code)
  root = tmp_path / "project"
  initialize_project(root)
  add_dependency(root, capability)
  (root / "src/input.json").write_text('{"length":10}')
  baseline = engineering.run_engineering(root, "audit:fresh-worker")
  assert baseline["result"]["assessment"] == "pass"
  assert baseline["toolchain"]["runner"] == "fresh-python-process"
  spec = importlib.util.spec_from_file_location("engineering_worker_audit_stale", source)
  stale = importlib.util.module_from_spec(spec)
  monkeypatch.setitem(sys.modules, spec.name, stale)
  spec.loader.exec_module(stale)
  source.write_text(code.replace("FACTOR = 1", "FACTOR = 1000"))
  lock_project(root)  # Explicitly adopt the developer package revision.
  assert stale.FACTOR == 1
  # The caller can still hold the old instance; public operations must ignore it.
  monkeypatch.setattr(engineering, "project_packages", lambda root: (stale.Package(),))
  state = engineering.inspect_engineering(root, "audit:fresh-worker")
  assert state["inputs"][0]["label"] == "Length factor 1000"
  assert not state["latest"]["current"]
  (root / "src/input.json").write_text('{"length":12}')
  run = engineering.run_engineering(root, "audit:fresh-worker")
  assert run["execution"] == "completed" and run["result"]["checks"][0]["actual"] == 12000
  assert run["result"]["assessment"] == "fail"
  monkeypatch.setenv("TEXSYS_AUDIT_MUTATE_SOURCE", "1")
  changed = engineering.run_engineering(root, "audit:fresh-worker")
  assert changed["execution"] == "error" and not changed["current"]
  assert "changed" in changed["diagnostics"][-1]["message"]
  monkeypatch.delenv("TEXSYS_AUDIT_MUTATE_SOURCE")
  lock_project(root)
  monkeypatch.setenv("TEXSYS_AUDIT_PAUSE", "1")
  monkeypatch.setitem(engineering.WORKER_TIMEOUT_SECONDS, "run", 3)
  with pytest.raises(ProjectError, match="exceeded 3 seconds"):
    engineering.run_engineering(root, "audit:fresh-worker")
  assert (root / "build/worker-sleep-started").is_file()
  assert not (root / ".texsys-write.lock").exists()
