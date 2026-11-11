import json
import importlib.util
import os
import sys

import pytest

from texsys import engineering as engineering
from texsys.packages import TexsysPackage
from texsys.project import ProjectError, initialize_project, project_write_lock


class WaterAdapter:
  id = "water:route-change"
  name = "管路变更"
  version = "1"

  def supports(self, root):
    return (root / "src/route.study.json").is_file()

  def inputs(self, root):
    value = json.loads((root / "src/route.study.json").read_text())["route"]["length_m"]
    return [{"id": "length", "label": "管路长度", "file": "src/route.study.json",
             "pointer": "/route/length_m", "unit": "m", "object_ref": "water.Route-01",
             "value": value, "min": 1, "max": 100}]

  def impact(self, root, changes):
    return [{"from": changes[0]["object_ref"], "to": "water.Pump-01", "reason": "管长影响水头损失",
             "checks": ["pressure-budget"]}]

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
  monkeypatch.setattr(engineering, "installed_packages", lambda: (WaterPackage(adapter),))
  monkeypatch.setattr(engineering, "_worker_call", engineering._dispatch)
  return root, adapter


def change(root, value):
  state = engineering.inspect_engineering(root)
  return {"adapter": state["selected"]["id"], "revision": state["revision"],
          "changes": [{"input_id": "length", "value": value}]}


def apply(root, value):
  request = change(root, value)
  plan = engineering.plan_engineering_change(root, request)
  return engineering.apply_engineering_change(root, request, plan["plan"])


def test_engineering_change_recompute_repair_and_accept_lifecycle(project):
  root, adapter = project
  initial = engineering.run_engineering(root, adapter.id)
  assert initial["execution"] == "completed" and initial["result"]["assessment"] == "pass"
  assert initial["current"] and initial["acceptance"] == "unreviewed"
  first_review = engineering.accept_engineering(root, adapter.id, initial["run_id"], "Engineer", "Budget checked.")
  assert first_review["state"] == "accepted"
  assert (root / first_review["path"]).is_file()
  assert first_review["evidence"]["result"]["limitations"] == ["Concept model with assumed roughness."]
  before = (root / "src/route.study.json").read_bytes()
  requested = change(root, 40)
  plan = engineering.plan_engineering_change(root, requested)
  assert (root / "src/route.study.json").read_bytes() == before
  assert plan["changes"][0]["before"] == 10 and plan["changes"][0]["after"] == 40
  assert plan["impacts"][0]["checks"] == ["pressure-budget"]
  assert "src/route.study.json" in plan["diff"]
  assert engineering.apply_engineering_change(root, requested, plan["plan"])["applied"]
  state = engineering.inspect_engineering(root)
  assert state["latest"]["freshness"] == "inputs-changed"
  assert state["reviews"][0]["state"] == "needs-review"
  failed = engineering.run_engineering(root, adapter.id)
  assert failed["current"] and failed["execution"] == "completed"
  assert failed["result"]["verification"] == "pass"
  assert failed["result"]["assessment"] == "fail"
  with pytest.raises(ProjectError, match="Only current"):
    engineering.accept_engineering(root, adapter.id, failed["run_id"], "Engineer", "Cannot accept this.")
  comparison = engineering.inspect_engineering(root)["comparison"]
  assert comparison["base_run_id"] == initial["run_id"]
  assert comparison["checks"][0]["before"]["actual"] == 20
  assert comparison["checks"][0]["after"]["actual"] == 80
  assert apply(root, 15)["applied"]
  repaired = engineering.run_engineering(root, adapter.id)
  review = engineering.accept_engineering(root, adapter.id, repaired["run_id"], "Engineer", "Changed route, verified pressure.")
  state = engineering.inspect_engineering(root)
  assert state["latest"]["acceptance"] == "accepted"
  assert state["revision"] == repaired["revision"]  # Review records do not invalidate the calculation.
  assert review["evidence_digest"] == repaired["evidence_digest"]
  rerun = engineering.run_engineering(root, adapter.id)
  assert rerun["run_id"] != repaired["run_id"] and rerun["acceptance"] == "unreviewed"
  assert engineering.inspect_engineering(root)["comparison"]["checks"] == []
  assert apply(root, 16)["applied"]
  assert all(item["state"] == "needs-review" for item in engineering.inspect_engineering(root)["reviews"])


def test_plan_rejects_changed_inputs_and_tampered_request(project):
  root, _ = project
  request = change(root, 15)
  plan = engineering.plan_engineering_change(root, request)
  before = (root / "src/route.study.json").read_bytes()
  tampered = {**request, "changes": [{"input_id": "length", "value": 16}]}
  with pytest.raises(ProjectError, match="digest differs"):
    engineering.apply_engineering_change(root, tampered, plan["plan"])
  assert (root / "src/route.study.json").read_bytes() == before
  (root / "assets/environment.txt").write_text("New environment.")
  assert engineering.apply_engineering_change(root, request, plan["plan"])["status"] == "conflict"
  assert (root / "src/route.study.json").read_bytes() == before


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), "20", 0, 101, None, 10 ** 1000])
def test_invalid_numeric_changes_never_touch_sources(project, value):
  root, _ = project
  before = (root / "src/route.study.json").read_bytes()
  with pytest.raises(ProjectError, match="finite"):
    engineering.plan_engineering_change(root, change(root, value))
  assert (root / "src/route.study.json").read_bytes() == before


def test_noop_preserves_original_file_and_invalid_changes_are_rejected(project):
  root, _ = project
  before = (root / "src/route.study.json").read_bytes()
  assert apply(root, 10)["applied"] is False
  assert (root / "src/route.study.json").read_bytes() == before
  request = change(root, 15)
  for changes in ([{"input_id": "missing", "value": 15}], request["changes"] * 2,
                  [{"input_id": "length", "value": 15, "file": "src/other.json"}]):
    with pytest.raises(ProjectError, match="unique declared"):
      engineering.plan_engineering_change(root, {**request, "changes": changes})


@pytest.mark.parametrize("design", [{"definition": "water.Route"}, {"occurrence": ["plant", "route"]}])
def test_explicit_design_selection_survives_plan_run_and_review(project, monkeypatch, design):
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
  request = change(root, 12)
  plan = engineering.plan_engineering_change(root, request)
  assert plan["changes"][0]["design"] == design
  assert engineering.apply_engineering_change(root, request, plan["plan"])["applied"]
  run = engineering.run_engineering(root, adapter.id)
  assert run["input_values"][0]["design"] == design
  assert run["result"]["checks"][0]["design"] == design
  assert run["result"]["checks"][0]["object_ref"] == "water.Pump-01"
  assert run["result"]["extraction"][0]["design"] == design
  review = engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Explicit model selection checked.")
  assert review["evidence"]["result"]["checks"][0]["design"] == design


@pytest.mark.parametrize("design", [
  None, True, {}, {"definition": ""}, {"definition": " "}, {"definition": True},
  {"occurrence": []}, {"occurrence": "route"}, {"occurrence": [""]},
  {"occurrence": ["plant", " "]}, {"occurrence": [True]},
  {"definition": "water.Route", "occurrence": ["route"]},
  {"definition": "water.Route", "extra": "unchecked"},
])
def test_invalid_input_design_cannot_be_applied_even_with_an_earlier_valid_plan(project, monkeypatch, design):
  root, adapter = project
  request = change(root, 12)
  plan = engineering.plan_engineering_change(root, request)
  original = adapter.inputs
  before = (root / "src/route.study.json").read_bytes()
  monkeypatch.setattr(adapter, "inputs", lambda root: [{**original(root)[0], "design": design}])
  with pytest.raises(ProjectError, match="design selection"):
    engineering.apply_engineering_change(root, request, plan["plan"])
  assert (root / "src/route.study.json").read_bytes() == before
  with pytest.raises(ProjectError, match="design selection"):
    engineering.run_engineering(root, adapter.id)
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
  with pytest.raises(ProjectError, match="Only current"):
    engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Invalid selection.")


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


def test_known_numeric_change_cannot_retain_source_endorsement(project, monkeypatch):
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
  assert apply(root, 10)["applied"] is False
  assert source.read_bytes() == before
  run = engineering.run_engineering(root, adapter.id)
  assert run["execution"] == "completed" and run["input_values"][0]["state"] == "known"
  request = change(root, 12)
  with pytest.raises(ProjectError, match="known.*source endorsement.*source and state"):
    engineering.plan_engineering_change(root, request)
  with pytest.raises(ProjectError, match="known.*source endorsement"):
    engineering.apply_engineering_change(root, request, "cannot-bypass-source-protection")
  assert source.read_bytes() == before
  # A separate source-aware workflow explicitly replaces the measured basis.
  document["route"].update(state="assumed", source="Design alternative B, assumed route")
  source.write_text(json.dumps(document))
  assert apply(root, 12)["applied"]
  changed = json.loads(source.read_text())["route"]
  assert changed["length_m"] == 12 and changed["state"] == "assumed"
  assert changed["source"] == "Design alternative B, assumed route"


@pytest.mark.parametrize("state", ["unknown", "missing", "not-applicable", "failed", None, True, "estimated", {}])
def test_non_numeric_or_invalid_input_state_cannot_be_applied(project, monkeypatch, state):
  root, adapter = project
  request = change(root, 12)
  plan = engineering.plan_engineering_change(root, request)
  original = adapter.inputs
  before = (root / "src/route.study.json").read_bytes()
  monkeypatch.setattr(adapter, "inputs", lambda root: [{**original(root)[0], "state": state}])
  with pytest.raises(ProjectError, match="input.*state"):
    engineering.apply_engineering_change(root, request, plan["plan"])
  assert (root / "src/route.study.json").read_bytes() == before


def test_existing_project_lock_serializes_writes_and_runs(project):
  root, adapter = project
  request = change(root, 12)
  plan = engineering.plan_engineering_change(root, request)
  with project_write_lock(root):
    with pytest.raises(ProjectError, match="locked"):
      engineering.apply_engineering_change(root, request, plan["plan"])
    with pytest.raises(ProjectError, match="locked"):
      engineering.run_engineering(root, adapter.id)


def test_external_input_mutation_during_run_is_preserved_but_cannot_be_accepted(project, monkeypatch):
  root, adapter = project
  original = adapter.evaluate

  def mutate(path):
    result = original(path)
    (path / "assets/concurrent.txt").write_text("Changed by another editor.")
    return result

  monkeypatch.setattr(adapter, "evaluate", mutate)
  run = engineering.run_engineering(root, adapter.id)
  assert run["execution"] == "error" and not run["inputs_consistent"]
  assert run["result"]["assessment"] == "pass"  # Distinct from execution/input validity.
  assert not run["current"]
  assert run["diagnostics"][0]["code"] == "ENGINEERING_INPUT_CHANGED"
  assert (root / run["path"]).is_file()
  with pytest.raises(ProjectError, match="Only current"):
    engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Stale inputs.")


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


def test_input_change_during_impact_analysis_rejects_plan(project, monkeypatch):
  root, adapter = project
  request = change(root, 12)

  def mutate(path, changes):
    (path / "assets/concurrent.txt").write_text("Changed while impact is being calculated.")
    return []

  monkeypatch.setattr(adapter, "impact", mutate)
  plan = engineering.plan_engineering_change(root, request)
  assert plan["status"] == "conflict" and plan["plan"] is None
  assert json.loads((root / "src/route.study.json").read_text())["route"]["length_m"] == 10


def test_change_during_final_acceptance_tool_scan_is_rejected_before_recording(project, monkeypatch):
  root, adapter = project
  run = engineering.run_engineering(root, adapter.id)
  original = engineering._toolchain
  calls = 0

  def change_after_snapshot(packages, adapter):
    nonlocal calls
    result = original(packages, adapter)
    calls += 1
    if calls == 2:
      (root / "src/route.study.json").write_text('{"route":{"length_m":80}}')
    return result

  monkeypatch.setattr(engineering, "_toolchain", change_after_snapshot)
  with pytest.raises(ProjectError, match="changed while capturing"):
    engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Source changed during acceptance.")
  assert not list((root / "reviews/engineering").glob("*.json"))
  assert not engineering.inspect_engineering(root)["latest"]["current"]


def test_change_while_publishing_review_never_returns_current_acceptance(project, monkeypatch):
  root, adapter = project
  run = engineering.run_engineering(root, adapter.id)
  original = engineering._write_record

  def change_after_publication(path, record):
    original(path, record)
    (root / "src/route.study.json").write_text('{"route":{"length_m":80}}')

  monkeypatch.setattr(engineering, "_write_record", change_after_publication)
  with pytest.raises(ProjectError, match="changed while publishing"):
    engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Source changed during publication.")
  assert engineering.inspect_engineering(root)["reviews"][0]["state"] == "needs-review"


def test_missing_dependency_invalidates_existing_evidence(project):
  root, adapter = project
  run = engineering.run_engineering(root, adapter.id)
  engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Checked.")
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text() + '\n[dependencies]\nmissing = {path = "../missing", version = "1"}\n')
  state = engineering.inspect_engineering(root)
  assert state["dependency_error"]
  assert state["latest"]["freshness"] == "dependency-invalid"
  assert state["reviews"][0]["state"] == "needs-review"
  with pytest.raises(ProjectError):
    engineering.run_engineering(root, adapter.id)
  with pytest.raises(ProjectError):
    engineering.plan_engineering_change(root, change(root, 12))


def test_adapter_version_change_invalidates_evidence_and_plans(project):
  root, adapter = project
  run = engineering.run_engineering(root, adapter.id)
  engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Checked.")
  requested = change(root, 12)
  plan = engineering.plan_engineering_change(root, requested)
  adapter.version = "2"
  state = engineering.inspect_engineering(root)
  assert state["latest"]["freshness"] == "tools-changed"
  assert state["reviews"][0]["state"] == "needs-review"
  with pytest.raises(ProjectError, match="digest differs"):
    engineering.apply_engineering_change(root, requested, plan["plan"])
  with pytest.raises(ProjectError, match="Only current"):
    engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Old tool.")


def test_tampered_evidence_is_visible_as_diagnostic_and_rejected(project):
  root, adapter = project
  run = engineering.run_engineering(root, adapter.id)
  path = root / run["path"]
  record = json.loads(path.read_text())
  record["result"]["checks"][0]["actual"] = 0
  path.write_text(json.dumps(record))
  state = engineering.inspect_engineering(root)
  assert state["latest"] is None and "integrity" in state["diagnostics"][0]["message"]
  with pytest.raises(ProjectError, match="integrity"):
    engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Tampered.")


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
  with pytest.raises(ProjectError, match="Only current"):
    engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Invalid result.")


def test_adapter_cannot_escape_sources_or_rewrite_two_files(project, monkeypatch, tmp_path):
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
  (root / "src/other.json").write_text(outside.read_text())
  monkeypatch.setattr(adapter, "inputs", lambda root: [original(root)[0], {**original(root)[0], "id": "other", "file": "src/other.json"}])
  state = engineering.inspect_engineering(root)
  request = {"adapter": adapter.id, "revision": state["revision"],
             "changes": [{"input_id": "length", "value": 11}, {"input_id": "other", "value": 12}]}
  with pytest.raises(ProjectError, match="one source file"):
    engineering.plan_engineering_change(root, request)
  assert json.loads((root / "src/other.json").read_text())["route"]["length_m"] == 10


def test_review_needs_named_actor_and_reason_and_stays_portable(project):
  root, adapter = project
  run = engineering.run_engineering(root, adapter.id)
  for actor, reason in (("", "reason"), ("Engineer", " ")):
    with pytest.raises(ProjectError, match="named reviewer"):
      engineering.accept_engineering(root, adapter.id, run["run_id"], actor, reason)
  review = engineering.accept_engineering(root, adapter.id, run["run_id"], "Engineer", "Within concept scope.")
  (root / run["path"]).unlink()
  state = engineering.inspect_engineering(root)
  assert state["reviews"][0]["state"] == "accepted"
  assert state["reviews"][0]["evidence"]["evidence_digest"] == review["evidence_digest"]


def test_unsupported_project_has_no_engineering_actions(tmp_path, monkeypatch):
  initialize_project(tmp_path)
  monkeypatch.setattr(engineering, "installed_packages", lambda: (WaterPackage(WaterAdapter()),))
  monkeypatch.setattr(engineering, "_worker_call", engineering._dispatch)
  state = engineering.inspect_engineering(tmp_path)
  assert not state["available"] and state["selected"] is None and state["runs"] == []


def test_fresh_worker_uses_current_code_for_inputs_plans_and_runs(tmp_path, monkeypatch):
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
  def impact(self, root, changes): return [{"factor":FACTOR}]
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
  source = tmp_path / "engineering_worker_audit.py"
  source.write_text(code)
  distribution = tmp_path / "engineering_worker_audit-1.0.dist-info"
  distribution.mkdir()
  (distribution / "METADATA").write_text("Metadata-Version: 2.1\nName: engineering-worker-audit\nVersion: 1.0\n")
  (distribution / "entry_points.txt").write_text("[texsys.packages]\nworker_audit = engineering_worker_audit:package\n")
  monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(tmp_path), os.environ.get("PYTHONPATH")])))
  root = tmp_path / "project"
  initialize_project(root)
  (root / "src/input.json").write_text('{"length":10}')
  baseline = engineering.run_engineering(root, "audit:fresh-worker")
  assert baseline["result"]["assessment"] == "pass"
  assert baseline["toolchain"]["runner"] == "fresh-python-process"
  engineering.accept_engineering(root, "audit:fresh-worker", baseline["run_id"], "Audit", "Initial code.")
  spec = importlib.util.spec_from_file_location("engineering_worker_audit_stale", source)
  stale = importlib.util.module_from_spec(spec)
  monkeypatch.setitem(sys.modules, spec.name, stale)
  spec.loader.exec_module(stale)
  source.write_text(code.replace("FACTOR = 1", "FACTOR = 1000"))
  assert stale.FACTOR == 1
  # The caller can still hold the old instance; public operations must ignore it.
  monkeypatch.setattr(engineering, "installed_packages", lambda: (stale.Package(),))
  state = engineering.inspect_engineering(root, "audit:fresh-worker")
  assert state["inputs"][0]["label"] == "Length factor 1000"
  assert not state["latest"]["current"] and state["reviews"][0]["state"] == "needs-review"
  request = {"adapter": "audit:fresh-worker", "revision": state["revision"], "changes": [{"input_id": "length", "value": 12}]}
  plan = engineering.plan_engineering_change(root, request)
  assert plan["impacts"] == [{"factor": 1000}]
  run = engineering.run_engineering(root, "audit:fresh-worker")
  assert run["execution"] == "completed" and run["result"]["checks"][0]["actual"] == 10000
  assert run["result"]["assessment"] == "fail"
  with pytest.raises(ProjectError, match="Only current"):
    engineering.accept_engineering(root, "audit:fresh-worker", run["run_id"], "Audit", "Must fail.")
  monkeypatch.setenv("TEXSYS_AUDIT_MUTATE_SOURCE", "1")
  changed = engineering.run_engineering(root, "audit:fresh-worker")
  assert changed["execution"] == "error" and not changed["current"]
  assert "changed" in changed["diagnostics"][-1]["message"]
  monkeypatch.delenv("TEXSYS_AUDIT_MUTATE_SOURCE")
  monkeypatch.setenv("TEXSYS_AUDIT_PAUSE", "1")
  monkeypatch.setitem(engineering.WORKER_TIMEOUT_SECONDS, "run", 3)
  with pytest.raises(ProjectError, match="exceeded 3 seconds"):
    engineering.run_engineering(root, "audit:fresh-worker")
  assert (root / "build/worker-sleep-started").is_file()
  assert not (root / ".texsys-write.lock").exists()
