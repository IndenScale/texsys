from dataclasses import replace
import json
import math
from pathlib import Path
import shutil
from types import MappingProxyType

import numpy as np
import pytest

from texsys.evidence import canonical_digest
from texsys_hvac_dynamics.domain import Branch, Component, Parameter, PhysicalModel, PhysicsInputError, compile_model
from texsys_hvac_dynamics.hydraulics import HydraulicNetwork, pipe_loss
from texsys_hvac_dynamics.scenario import Scenario, Signal, load_scenario
from texsys_hvac_dynamics.thermal import ThermalNetwork

EXAMPLE = Path(__file__).parents[1] / "examples/hvac-dynamic-station"


def params(values):
  return MappingProxyType({name: Parameter(value, unit, "assumed", "Independent analytic test") for name, (value, unit) in values.items()})


def test_signal(value, unit="1"):
  return Signal(unit, "hold", ((0.0, value),), "assumed", "Independent analytic test")


test_signal.__test__ = False


def numerical_options():
  return {"method": "BDF", "rtol": 1e-8, "temperature_atol_k": 1e-9, "power_atol_w": 1e-4,
          "max_step_s": 10.0, "hydraulic_pressure_tolerance_pa": 1e-4,
          "hydraulic_mass_tolerance_kg_s": 1e-9, "initial_residual_tolerance_w": 0.01}


def pump_resistor_model(reverse=False, closed=False):
  pump = Component("S.P", "Pump", "pump", params({"shutoff_head_m": (20, "m"), "curve_m_per_m6_s2": (200000, "s2/m5"),
    "nominal_pressure_drop_pa": (1000, "Pa"), "nominal_flow_m3_s": (.01, "m3/s"), "elevation_change_m": (0, "m"), "check_valve": (0, "1")}),
    {"inlet": "chilled-water", "outlet": "chilled-water"}, "test", "test")
  passive = Component("S.R", "Resistor", "valve" if closed else "load", params({"nominal_pressure_drop_pa": (5000, "Pa"),
    "nominal_flow_m3_s": (.01, "m3/s"), "kv_m3_h": (40, "m3/h"), "elevation_change_m": (0, "m")}), pump.ports, "test", "test")
  ports = {"S.P.inlet": "a", "S.P.outlet": "b", "S.R.inlet": "a" if reverse else "b", "S.R.outlet": "b" if reverse else "a"}
  branches = tuple(Branch(c.identity, c.identity, "water", c.identity+".inlet", c.identity+".outlet", "chilled-water", .01, 0, 1) for c in (pump, passive))
  model = PhysicalModel("S", (pump, passive), branches, ("a", "b"), ports, (), 998.2, 4186, .001, 9.80665, "test", (), ())
  signals = {"S.P.speed_ratio": test_signal(1)}
  if closed:
    signals["S.R.opening"] = test_signal(0)
  scenario = Scenario("analytic", 100, 10, signals, {}, numerical_options(), (), "test")
  return model, scenario


@pytest.mark.parametrize("reverse", [False, True])
def test_coupled_hydraulics_matches_independent_pump_resistor_solution(reverse):
  model, scenario = pump_resistor_model(reverse)
  result = HydraulicNetwork(model).solve(scenario, 0)
  # H0 - a*q² balances two independently specified quadratic losses.
  rho_g = 998.2 * 9.80665
  expected = math.sqrt(rho_g * 20 / (rho_g * 200000 + 6000 / .01**2))
  assert result.volume_flows_m3_s[0] == pytest.approx(expected, rel=1e-8)
  assert result.volume_flows_m3_s[1] == pytest.approx(-expected if reverse else expected, rel=1e-8)
  assert result.mass_residual_kg_s < 1e-10
  assert result.pressure_residual_pa < 1e-5


def test_fully_closed_valve_enforces_zero_flow_without_pressure_failure():
  model, scenario = pump_resistor_model(closed=True)
  result = HydraulicNetwork(model).solve(scenario, 0)
  assert result.volume_flows_m3_s == pytest.approx((0, 0), abs=1e-12)
  assert result.blocked_branches == ("S.R",)
  assert result.pressures_pa["b"] == pytest.approx(model.density_kg_m3 * model.gravity_m_s2 * 20)


def test_pipe_laminar_limit_matches_poiseuille_and_reverses_sign():
  args = {"diameter": .02, "length": 10, "roughness": 0.00001, "minor_loss": 0, "density": 1000, "viscosity": .001}
  expected = 8 * .001 * 10 * 1e-6 / (math.pi * (.02/2)**4)
  assert pipe_loss(1e-6, **args) == pytest.approx(expected)
  assert pipe_loss(-1e-6, **args) == pytest.approx(-expected)


def tank_experiment():
  component = Component("S.T", "Tank", "tank", params({"heat_loss_ua_w_k": (100, "W/K"),
    "nominal_pressure_drop_pa": (1000, "Pa"), "nominal_flow_m3_s": (.001, "m3/s"), "elevation_change_m": (0, "m")}),
    {"inlet": "chilled-water", "outlet": "chilled-water"}, "test", "test")
  branch = Branch("S.T", "S.T", "water", "S.T.inlet", "S.T.outlet", "chilled-water", 1, 0, 1)
  model = PhysicalModel("S", (component,), (branch,), ("a",), {"S.T.inlet": "a", "S.T.outlet": "a"}, (), 1000, 4186, .001, 9.80665, "test", (), ())
  scenario = Scenario("cooldown", 1000, 100, {"S.T.ambient_c": test_signal(20, "degC")},
                      {"mode": "specified", "states": {"S.T.temperature_c": 60}}, numerical_options(), (), "test")
  return model, scenario


def test_lumped_cooldown_matches_closed_form_and_conserves_energy():
  model, scenario = tank_experiment()
  result = ThermalNetwork(model, scenario).run()
  expected = [20 + 40 * math.exp(-100 * time / (1000 * 4186)) for time in result["time_s"]]
  assert result["variables"]["S.T.temperature_c"]["values"] == pytest.approx(expected, abs=1e-5)
  assert result["checks"]["energy_residual_w"] < 1e-8
  assert result["checks"]["integrated_energy_error_j"] < .01
  assert result["status"] == "pass"


def test_conservation_gate_detects_unaccounted_heat():
  model, scenario = tank_experiment()
  network = ThermalNetwork(model, scenario)
  original = network.evaluate
  def leaking_rhs(time, state):
    derivative, output = original(time, state)
    derivative[0] += .001
    return derivative, output
  network.evaluate = leaking_rhs
  result = network.run()
  assert result["status"] == "fail"
  assert result["checks"]["integrated_energy_error_j"] > 1e6
  assert any(a.get("rule") == "energy-conservation" for a in result["assertions"])


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
