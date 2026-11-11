"""The same engineering lifecycle applied to a second, transient physical case."""

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
from texsys_hvac_dynamics.engineering import HvacCapacityChangeAdapter


REPO = Path(__file__).parents[1]
ADAPTER = "hvac:capacity-change"
CAPACITY = "hvac.chiller-capacity"
LOAD_TEMPERATURE = "hvac:assertion:ColdStation.LOAD-01.outlet_temperature_c:0"


@pytest.fixture
def station(tmp_path):
  root = tmp_path / "station"
  shutil.copytree(REPO / "examples/hvac-dynamic-station", root,
                  ignore=shutil.ignore_patterns("build", "reviews", ".texsys-write.lock"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(root)
  return root


def _binding(root):
  return json.loads((root / "src/system.sdl.json").read_text())["components"]["Chiller"]["behavior"]["parameters"]["capacity_kw"]


def _change(root, capacity):
  state = inspect_engineering(root, ADAPTER)
  request = {"adapter": ADAPTER, "revision": state["revision"], "changes": [{"input_id": CAPACITY, "value": capacity}]}
  preview = plan_engineering_change(root, request)
  assert preview["status"] == "pass"
  assert {item["id"] for item in preview["impacts"]} == {"hvac:capacity-model", "hvac:load-step", "unregistered-disciplines"}
  assert preview["impacts"][0]["objects"] == ["ColdStation.CH-01"]
  assert apply_engineering_change(root, request, preview["plan"])["applied"]


def _check(result, identity):
  return next(item for item in result["checks"] if item["id"] == identity)


def test_second_adapter_capacity_change_preserves_sources_and_closes_review_loop(station):
  state = inspect_engineering(station, ADAPTER)
  assert state["selected"]["id"] == ADAPTER
  item = state["inputs"][0]
  assert item["id"] == CAPACITY and item["value"] == 120
  assert item["design"] == {"definition": "src/system.sdl.json#Chiller"}
  original_binding = _binding(station)
  baseline = run_engineering(station, ADAPTER)
  assert baseline["execution"] == "completed" and baseline["current"]
  result = baseline["result"]
  assert result["assessment"] == "pass" and result["solver_execution"] == "completed"
  assert result["scope_verified"] and result["fidelity"] == "concept"
  temperature = _check(result, LOAD_TEMPERATURE)
  assert temperature["actual"] == pytest.approx(10.397438, abs=1e-5)
  assert temperature["design"] == {"occurrence": ["LOAD-01"]}
  assert temperature["object_ref"] == "ColdStation.LOAD-01"
  assert _check(result, "hvac:assertion:ColdStation.CH-01#chilled.outlet_temperature_c:1")["design"] == {"occurrence": ["CH-01"]}
  assert {item["id"] for item in result["other_scenarios"]} == {"pump-speed", "pump-trip"}
  assert all(item["assessment"] == "not-assessed" for item in result["other_scenarios"])
  review = accept_engineering(station, ADAPTER, baseline["run_id"], "概念模型审查员", "接受声明工况的概念结论；不涉及设备选型签核。")
  assert review["limitations"]

  _change(station, 60)
  binding = _binding(station)
  assert binding == {**original_binding, "value": 60}
  assert inspect_engineering(station, ADAPTER)["reviews"][0]["state"] == "needs-review"
  failed = run_engineering(station, ADAPTER)
  assert failed["execution"] == "completed" and failed["current"]
  assert failed["result"]["assessment"] == "fail"
  temperature = _check(failed["result"], LOAD_TEMPERATURE)
  assert temperature["status"] == "fail"
  assert temperature["actual"] == pytest.approx(13.175647, abs=1e-5)
  assert temperature["limit"] == 12
  assert temperature["evidence"]["first_violation_time_s"] == 710
  assert "710 s" in temperature["message"]
  assert len(temperature["evidence"]["time_s"]) == len(temperature["evidence"]["values"]) == 181
  assert _check(failed["result"], "hvac:energy_residual_w")["status"] == "pass"
  assert _check(failed["result"], "hvac:integrated_energy_error_j")["actual"] < 1e-6
  assert all(item["state"] == "assumed" for item in failed["result"]["extraction"])
  effective = failed["result"]["extraction"][0]
  assert effective["value"] == 60 and effective["design"] == {"occurrence": ["CH-01"]}
  assert effective["source"]["description"] == original_binding["source"]
  assert effective["source"]["path"] == "src/system.sdl.json"
  with pytest.raises(ProjectError, match="passing"):
    accept_engineering(station, ADAPTER, failed["run_id"], "概念模型审查员", "温度超限不能接受。")

  _change(station, 120)
  repaired = run_engineering(station, ADAPTER)
  assert repaired["result"]["assessment"] == "pass" and repaired["current"]
  assert _binding(station) == original_binding
  assert _check(repaired["result"], LOAD_TEMPERATURE)["actual"] == _check(result, LOAD_TEMPERATURE)["actual"]
  final_review = accept_engineering(station, ADAPTER, repaired["run_id"], "概念模型审查员", "容量恢复后重算，接受本次明确范围。")
  assert final_review["evidence_digest"] == repaired["evidence_digest"]
  assert (station / final_review["path"]).is_file()


@pytest.mark.parametrize("change", ["smaller-load", "weaker-limit", "different-control"])
def test_actual_scenario_cannot_silently_change_declared_review_scope(station, change):
  path = station / "assets/scenarios/load-step.json"
  scenario = json.loads(path.read_text())
  if change == "smaller-load":
    scenario["signals"]["ColdStation.LOAD-01.load_w"]["points"][1][1] = 60000
  elif change == "weaker-limit":
    scenario["assertions"][0]["value"] = 20
  else:
    scenario["signals"]["ColdStation.CH-01.setpoint_c"]["points"][0][1] = 6
  path.write_text(json.dumps(scenario))
  result = HvacCapacityChangeAdapter().evaluate(station)
  assert result["assessment"] == "indeterminate" and not result["scope_verified"]
  assert result["solver_execution"] == "completed"
  scope = _check(result, "hvac:scenario-scope")
  assert scope["status"] == "indeterminate" and scope["evidence"]["errors"]
  assert scope["evidence"]["declaration"] == scenario


def test_additional_assertion_failure_is_not_hidden_by_the_two_selected_temperatures(station):
  path = station / "assets/scenarios/load-step.json"
  scenario = json.loads(path.read_text())
  scenario["assertions"].append({"variable": "ColdStation.CT-01.outlet_temperature_c", "operator": "<=", "unit": "degC", "value": 20})
  path.write_text(json.dumps(scenario))
  result = HvacCapacityChangeAdapter().evaluate(station)
  assert result["scope_verified"] and result["assessment"] == "fail"
  extra = _check(result, "hvac:assertion:ColdStation.CT-01.outlet_temperature_c:2")
  assert extra["status"] == "fail" and extra["design"] == {"occurrence": ["CT-01"]}


def test_unresolved_capacity_is_not_defaulted_or_presented_as_a_solver_run(station):
  path = station / "src/system.sdl.json"
  source = json.loads(path.read_text())
  capacity = source["components"]["Chiller"]["behavior"]["parameters"]["capacity_kw"]
  capacity["state"] = "unknown"
  del capacity["value"]
  path.write_text(json.dumps(source))
  state = inspect_engineering(station, ADAPTER)
  assert state["inputs"] == []
  run = run_engineering(station, ADAPTER)
  assert run["result"]["assessment"] == "indeterminate"
  assert run["result"]["solver_execution"] == "not-run"
  assert run["result"]["diagnostics"][0]["code"] == "H_UNRESOLVED"
  assert "value" not in _binding(station)
  with pytest.raises(ProjectError, match="passing"):
    accept_engineering(station, ADAPTER, run["run_id"], "审查员", "未知输入不可接受。")


def test_unrealizable_initial_state_is_execution_error_not_a_temperature_verdict(station):
  _change(station, 20)
  run = run_engineering(station, ADAPTER)
  assert run["execution"] == "error" and run["result"]["assessment"] == "indeterminate"
  assert any("initialization failed" in item["message"] for item in run["diagnostics"])
