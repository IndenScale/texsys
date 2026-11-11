from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil

import numpy as np
import pytest

from texsys.evidence import canonical_digest
from texsys_hvac_dynamics.domain import PhysicsInputError, compile_model
from texsys_hvac_dynamics.scenario import load_scenario
from texsys_hvac_dynamics.thermal import ThermalNetwork

EXAMPLE = Path(__file__).parents[1] / "examples/hvac-dynamic-station"


# The capability package owns these portable reference tests; the repository
# collects the same definitions instead of maintaining a second copy.
_reference_path = Path(__file__).parents[1] / "packages/hvac-dynamics/tests/test_reference_physics.py"
_reference_spec = importlib.util.spec_from_file_location("hvac_reference_physics", _reference_path)
_reference_module = importlib.util.module_from_spec(_reference_spec)
_reference_spec.loader.exec_module(_reference_module)
test_signal = _reference_module.test_signal
for _name in dir(_reference_module):
  if _name.startswith("test_") or _name in {"params", "numerical_options", "pump_resistor_model", "tank_experiment"}:
    globals()[_name] = getattr(_reference_module, _name)


@pytest.mark.parametrize("name,expected", [("load-step", "pass"), ("pump-speed", "pass"), ("pump-trip", "fail")])
def test_cold_station_transient_events_and_snapshot_integrity(name, expected):
  model = compile_model(EXAMPLE)
  scenario = load_scenario(EXAMPLE / f"assets/scenarios/{name}.json", model)
  before = canonical_digest(model.as_dict())
  result = ThermalNetwork(model, scenario).run()
  assert result["status"] == expected
  assert result["events"][0]["time_s"] == 300
  assert result["initialization"]["maximum_residual_w"] < .1
  assert result["checks"]["energy_residual_w"] < .1
  assert result["checks"]["integrated_energy_error_j"] < 1
  assert canonical_digest(model.as_dict()) == before
  assert len(result["time_s"]) == 181
  if name == "pump-trip":
    values = result["variables"]["ColdStation.P-CHW-01.mass_flow_kg_s"]["values"]
    assert max(abs(v) for t, v in zip(result["time_s"], values) if t >= 300) < 1e-9
    assert any(a["status"] == "fail" for a in result["assertions"])


def test_time_integration_converges_against_tighter_tolerances():
  model = compile_model(EXAMPLE)
  scenario = load_scenario(EXAMPLE / "assets/scenarios/load-step.json", model)
  baseline = ThermalNetwork(model, scenario).run()
  tighter = replace(scenario, solver={**scenario.solver, "rtol": 1e-8, "max_step_s": 2.5})
  reference = ThermalNetwork(model, tighter).run()
  name = "ColdStation.CH-01#chilled.outlet_temperature_c"
  assert np.max(np.abs(np.array(baseline["variables"][name]["values"]) - reference["variables"][name]["values"])) < .002


def test_unresolved_parameter_does_not_receive_a_default(tmp_path):
  root = tmp_path / "model"
  shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("build"))
  (root / "build").mkdir()
  path = root / "src/system.sdl.json"
  source = json.loads(path.read_text())
  binding = source["components"]["Chiller"]["behavior"]["parameters"]["capacity_kw"]
  binding["state"] = "unknown"
  binding.pop("value")
  path.write_text(json.dumps(source))
  with pytest.raises(PhysicsInputError, match="known or assumed"):
    compile_model(root)


def test_missing_hydraulic_parameter_blocks_snapshot_before_bom(tmp_path):
  root = tmp_path / 'model'
  shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('build'))
  (root / 'build').mkdir()
  path = root / 'src/system.sdl.json'
  source = json.loads(path.read_text())
  del source['components']['ChilledWaterPump']['behavior']['parameters']['efficiency']
  path.write_text(json.dumps(source))
  with pytest.raises(PhysicsInputError, match='missing parameter efficiency'):
    compile_model(root)


def test_invalid_scenario_assertion_and_signal_container_are_reported(tmp_path):
  model = compile_model(EXAMPLE)
  source = json.loads((EXAMPLE / 'assets/scenarios/load-step.json').read_text())
  path = tmp_path / 'invalid.json'
  source['signals'] = None
  path.write_text(json.dumps(source))
  with pytest.raises(PhysicsInputError, match='signals must be an object'):
    load_scenario(path, model)
  source = json.loads((EXAMPLE / 'assets/scenarios/load-step.json').read_text())
  source['assertions'][0]['value'] = '12'
  path.write_text(json.dumps(source))
  with pytest.raises(PhysicsInputError, match='assertion value'):
    load_scenario(path, model)


def test_inconsistent_steady_initialization_is_not_a_solved_run():
  from texsys_hvac_dynamics.thermal import TransientFailure
  model = compile_model(EXAMPLE)
  scenario = load_scenario(EXAMPLE / 'assets/scenarios/load-step.json', model)
  signals = {**scenario.signals, 'ColdStation.P-CHW-01.speed_ratio': test_signal(0)}
  network = ThermalNetwork(model, replace(scenario, signals=signals))
  with pytest.raises(TransientFailure, match='initialization failed'):
    network.run()


def test_unavailable_modelica_exports_equations_but_does_not_claim_execution(tmp_path, monkeypatch):
  from texsys_hvac_dynamics.domain import PhysicsUnresolved
  from texsys_hvac_dynamics.modelica_backend import run_modelica
  model = compile_model(EXAMPLE)
  scenario = load_scenario(EXAMPLE / 'assets/scenarios/load-step.json', model)
  monkeypatch.setenv('TEXSYS_OMC', str(tmp_path / 'no-compiler'))
  with pytest.raises(PhysicsUnresolved, match='unavailable'):
    run_modelica(model, scenario, tmp_path)
  text = (tmp_path / 'TexsysWaterNetwork.mo').read_text()
  assert 'der(x1)' in text and 'pipeLoss(q' in text and 'time < 300.0' in text
  assert not (tmp_path / 'TexsysWaterNetwork_res.csv').exists()


def test_failed_behavior_is_invalid_and_not_an_unresolved_draft(tmp_path):
  from texsys_hvac_dynamics.domain import PhysicsUnresolved
  root = tmp_path / 'model'
  shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('build'))
  (root / 'build').mkdir()
  path = root / 'src/system.sdl.json'
  source = json.loads(path.read_text())
  source['components']['Chiller']['behavior']['state'] = 'failed'
  path.write_text(json.dumps(source))
  with pytest.raises(PhysicsInputError, match='explicit failed state') as error:
    compile_model(root)
  assert not isinstance(error.value, PhysicsUnresolved)
