"""Exercise the engineering lifecycle against the real FWS geometry/dispatch chain."""

import json
from pathlib import Path
import shutil

import pytest

from texsys.dependencies import lock_project
from texsys.engineering import (
  inspect_engineering,
  run_engineering,
)
from texsys_datacenter.engineering import DESIGN_SCENARIOS


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


def _write_fws(root, *, spine_x=None, row_diameter=None):
  path = root / "src/fws-branches.study.json"
  data = json.loads(path.read_text())
  if spine_x is not None:
    data["spine_x_m"] = spine_x
  if row_diameter is not None:
    data["pipe_types"]["row"]["diameter_m"] = row_diameter
  path.write_text(json.dumps(data, indent=2) + "\n")


def test_source_change_recalculates_real_geometry_and_delivery_then_recovers(fws_project):
  baseline = run_engineering(fws_project, ADAPTER)
  assert baseline["execution"] == "completed" and baseline["current"]
  assert baseline["result"]["assessment"] == baseline["result"]["verification"] == "pass"
  assert len(baseline["result"]["checks"]) == 254
  assert baseline["result"]["scope_verified"]
  assert set(baseline["result"]["scenarios"]) == set(DESIGN_SCENARIOS)
  assert baseline["result"]["fidelity"] == "concept"
  assert baseline["result"]["outside_scope_assessments"]["floor"]
  assert baseline["result"]["outside_scope_assessments"]["frame"]
  assert baseline["result"]["limitations"]

  _write_fws(fws_project, spine_x=46.5, row_diameter=.05)
  state = inspect_engineering(fws_project, ADAPTER)
  assert state["latest"]["freshness"] == "inputs-changed"
  failed = run_engineering(fws_project, ADAPTER)
  assert failed["execution"] == "completed" and failed["current"]
  assert failed["result"]["assessment"] == "fail"
  assert failed["result"]["upstream_diagnostics"]
  shortages = [check for check in failed["result"]["checks"] if check["status"] == "fail"]
  assert len(shortages) == 250
  assert all(check["actual"] < check["limit"] and check["unit"] == "kW" for check in shortages)
  assert any(check["object_ref"] == "L04/Unit-01-01" for check in shortages)
  old_geometry = {item["id"]: item for item in baseline["result"]["extraction"]}
  new_geometry = {item["id"]: item for item in failed["result"]["extraction"]}
  assert old_geometry.keys() == new_geometry.keys()
  assert any(item["value"] != new_geometry[key]["value"] for key, item in old_geometry.items() if key.startswith("length:"))
  assert all(item["state"] == "assumed" for item in new_geometry.values())
  assert any(item["id"].startswith("pressure:") and item["unit"] == "kPa" for item in new_geometry.values())

  _write_fws(fws_project, spine_x=46.4, row_diameter=.16)
  repaired = run_engineering(fws_project, ADAPTER)
  assert repaired["result"]["assessment"] == "pass"
  assert repaired["result"]["checks"] == baseline["result"]["checks"]
  state = inspect_engineering(fws_project, ADAPTER)
  assert state["comparison"]["before_assessment"] == "fail"
  assert state["comparison"]["after_assessment"] == "pass"
  assert state["latest"]["current"]
  assert state["latest"]["evidence_digest"] == repaired["evidence_digest"]


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
  result = run_engineering(fws_project, ADAPTER)["result"]
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
  result = run_engineering(fws_project, ADAPTER)["result"]
  geometry = next(check for check in result["checks"] if check["id"] == "fws:geometry")
  assert geometry["status"] == "fail" and geometry["actual"] > 0
  assert geometry["evidence"]
  assert result["assessment"] == "fail"


def test_source_with_nonphysical_inner_diameter_cannot_complete(fws_project):
  _write_fws(fws_project, row_diameter=.18)
  run = run_engineering(fws_project, ADAPTER)
  assert run["execution"] == "error"
  assert run["result"]["assessment"] == "indeterminate"
  assert run["diagnostics"]


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
  result = run_engineering(fws_project, ADAPTER)["result"]
  assert result["execution"] == "completed"
  assert result["assessment"] == "indeterminate"
  assert not result["scope_verified"] and "工况范围未成立" in result["scope"]
  definition = next(item for item in result["scenario_definitions"] if item["id"] == "full-supply-a-out")
  assert definition["errors"] and definition["sources"]
  assert definition["contract"]["unavailable_power"] == ["Incoming-A"]
  check = next(item for item in result["checks"] if item["id"] == "fws:scenario-scope:full-supply-a-out")
  assert check["status"] == "indeterminate"
