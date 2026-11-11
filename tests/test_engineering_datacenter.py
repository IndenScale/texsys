"""Exercise the engineering lifecycle against the real FWS geometry/dispatch chain."""

import json
from pathlib import Path
import shutil

import pytest

from texsys.dependencies import lock_project
from texsys.engineering import (
  accept_engineering,
  apply_engineering_change,
  inspect_engineering,
  plan_engineering_change,
  run_engineering,
)
from texsys.project import ProjectError
from texsys_datacenter.engineering import DESIGN_SCENARIOS, FwsChangeAdapter


REPO = Path(__file__).parents[1]
ADAPTER = "datacenter:fws-change"


@pytest.fixture
def fws_project(tmp_path):
  root = tmp_path / "fws-change"
  shutil.copytree(REPO / "examples/datacenter-hybrid-unit", root,
                  ignore=shutil.ignore_patterns("build", "reviews", ".texsys-write.lock"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(root)
  return root


def _change(root, **values):
  state = inspect_engineering(root, ADAPTER)
  request = {"adapter": ADAPTER, "revision": state["revision"],
             "changes": [{"input_id": key, "value": value} for key, value in values.items()]}
  plan = plan_engineering_change(root, request)
  assert plan["status"] == "pass"
  assert {item["id"] for item in plan["impacts"]} >= {"fws-route-extraction", "fws-delivery", "unregistered-disciplines"}
  assert apply_engineering_change(root, request, plan["plan"])["applied"]
  return plan


def test_fws_change_recalculates_real_geometry_and_delivery_then_records_scoped_acceptance(fws_project):
  baseline = run_engineering(fws_project, ADAPTER)
  assert baseline["execution"] == "completed" and baseline["current"]
  assert baseline["result"]["assessment"] == baseline["result"]["verification"] == "pass"
  assert len(baseline["result"]["checks"]) == 254
  assert baseline["result"]["scope_verified"]
  assert set(baseline["result"]["scenarios"]) == set(DESIGN_SCENARIOS)
  assert baseline["result"]["fidelity"] == "concept"
  assert baseline["result"]["outside_scope_assessments"]["floor"]
  assert baseline["result"]["outside_scope_assessments"]["frame"]
  accepted = accept_engineering(fws_project, ADAPTER, baseline["run_id"], "POC reviewer", "仅接受列明工况的概念设施水结论。")
  assert accepted["limitations"]

  plan = _change(fws_project, **{"fws.spine-x": 46.5, "fws.row-diameter": .05})
  assert all(item["file"] == "src/fws-branches.study.json" for item in plan["changes"])
  state = inspect_engineering(fws_project, ADAPTER)
  assert state["latest"]["freshness"] == "inputs-changed"
  assert state["reviews"][0]["state"] == "needs-review"
  failed = run_engineering(fws_project, ADAPTER)
  assert failed["execution"] == "completed" and failed["current"]
  assert failed["result"]["assessment"] == "fail"
  assert failed["result"]["upstream_diagnostics"]
  shortages = [check for check in failed["result"]["checks"] if check["status"] == "fail"]
  assert len(shortages) == 250
  assert all(check["actual"] < check["limit"] and check["unit"] == "kW" for check in shortages)
  assert any(check["object_ref"] == "L04/Unit-01-01" for check in shortages)
  with pytest.raises(ProjectError, match="passing"):
    accept_engineering(fws_project, ADAPTER, failed["run_id"], "POC reviewer", "不能接受缺冷方案。")
  old_geometry = {item["id"]: item for item in baseline["result"]["extraction"]}
  new_geometry = {item["id"]: item for item in failed["result"]["extraction"]}
  assert old_geometry.keys() == new_geometry.keys()
  assert any(item["value"] != new_geometry[key]["value"] for key, item in old_geometry.items() if key.startswith("length:"))
  assert all(item["state"] == "assumed" for item in new_geometry.values())
  assert any(item["id"].startswith("pressure:") and item["unit"] == "kPa" for item in new_geometry.values())

  _change(fws_project, **{"fws.spine-x": 46.4, "fws.row-diameter": .16})
  repaired = run_engineering(fws_project, ADAPTER)
  assert repaired["result"]["assessment"] == "pass"
  assert repaired["result"]["checks"] == baseline["result"]["checks"]
  state = inspect_engineering(fws_project, ADAPTER)
  assert state["comparison"]["before_assessment"] == "fail"
  assert state["comparison"]["after_assessment"] == "pass"
  assert state["latest"]["acceptance"] == "unreviewed"
  review = accept_engineering(fws_project, ADAPTER, repaired["run_id"], "POC reviewer", "恢复管径和路由，复算所有声明工况后接受。")
  assert review["evidence_digest"] == repaired["evidence_digest"]
  assert (fws_project / review["path"]).is_file()


def test_expected_regression_failures_do_not_turn_a_shortage_into_an_engineering_pass(fws_project):
  path = fws_project / "src/fws-branches.study.json"
  data = json.loads(path.read_text())
  data["pipe_types"]["row"]["diameter_m"] = .05
  path.write_text(json.dumps(data))
  path = fws_project / "src/building.study.json"
  data = json.loads(path.read_text())
  for scenario in data["scenarios"].values():
    scenario["expected"] = "fail"
  path.write_text(json.dumps(data))
  result = FwsChangeAdapter().evaluate(fws_project)
  assert result["execution"] == "completed"
  assert result["verification"] == "pass"
  assert result["assessment"] == "fail"
  shortage = next(check for check in result["checks"] if check["id"] == "fws:initial-normal:L01/Unit-01-01")
  assert shortage["status"] == "fail" and shortage["actual"] == pytest.approx(81.8278857508)
  assert "流速上限 2.5 m/s" in shortage["message"]
  assert "扬程余量" in shortage["message"] and "达到可用泵" not in shortage["message"]
  constraints = shortage["evidence"]["hydraulic_constraints"]
  assert constraints and all(item["kind"] == "velocity" for item in constraints)
  assert all(item["actual"] == pytest.approx(item["limit"]) for item in constraints)
  assert all(item["inside_m"] == .05 and item["sources"] for item in constraints)
  assert all(item["object_ref"].startswith("L01/") for item in constraints)
  assert all(item["head_margin_kpa"] > 400 for item in shortage["evidence"]["pump_head"])


def test_evaluation_profile_keeps_geometry_conflicts_visible(fws_project):
  path = fws_project / "src/fws-branches.study.json"
  data = json.loads(path.read_text())
  data["trains"]["HeatRejection-B"]["legs"]["supply"]["overhead_m"] = 3.35
  path.write_text(json.dumps(data))
  result = FwsChangeAdapter().evaluate(fws_project)
  geometry = next(check for check in result["checks"] if check["id"] == "fws:geometry")
  assert geometry["status"] == "fail" and geometry["actual"] > 0
  assert geometry["evidence"]
  assert result["assessment"] == "fail"


def test_fws_change_bounds_prevent_nonphysical_inner_diameter(fws_project):
  state = inspect_engineering(fws_project, ADAPTER)
  request = {"adapter": ADAPTER, "revision": state["revision"],
             "changes": [{"input_id": "fws.row-diameter", "value": .18}]}
  with pytest.raises(ProjectError, match="bounds"):
    plan_engineering_change(fws_project, request)


@pytest.mark.parametrize("change", ["missing-outage", "wrong-phase", "unit-fault-removed"])
def test_named_scenario_cannot_claim_an_unmodeled_outage_or_phase(fws_project, change):
  path = fws_project / "src/building.study.json"
  data = json.loads(path.read_text())
  if change == "missing-outage":
    data["scenarios"]["full-supply-a-out"]["unavailable_power"] = []
    data["scenarios"]["full-supply-a-out"]["unit_scenario"] = "normal"
  elif change == "wrong-phase":
    data["scenarios"]["full-supply-a-out"]["phase"] = "initial"
  else:
    unit_path = fws_project / "src/unit.study.json"
    unit = json.loads(unit_path.read_text())
    unit["scenarios"]["supply-a-out"]["unavailable"] = []
    unit_path.write_text(json.dumps(unit))
  path.write_text(json.dumps(data))
  result = FwsChangeAdapter().evaluate(fws_project)
  assert result["execution"] == "completed"
  assert result["assessment"] == "indeterminate"
  assert not result["scope_verified"] and "工况范围未成立" in result["scope"]
  definition = next(item for item in result["scenario_definitions"] if item["id"] == "full-supply-a-out")
  assert definition["errors"] and definition["sources"]
  assert definition["contract"]["unavailable_power"] == ["Incoming-A"]
  check = next(item for item in result["checks"] if item["id"] == "fws:scenario-scope:full-supply-a-out")
  assert check["status"] == "indeterminate"
