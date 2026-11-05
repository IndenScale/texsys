"""Coupled lumped thermal storage with quasi-steady nonlinear hydraulics.

All temperatures are solved in one state vector. Flow directions select upstream
states; zero-storage junction temperatures are mass-flow-weighted mixtures.
Hydraulic dissipation heats the water, and chiller heat is conserved between its
two circuits. The numerical integrator never mutates the engineering snapshot.
"""

from dataclasses import dataclass
import math

import numpy as np
import scipy
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

from .domain import PhysicalModel, PhysicsInputError
from .hydraulics import HydraulicNetwork
from .scenario import Scenario


class TransientFailure(ValueError):
  pass


@dataclass(frozen=True)
class State:
  identity: str
  component: str
  unit: str
  capacity_j_k: float
  branch_index: int | None


class ThermalNetwork:
  version = "lumped-water-network/1"

  def __init__(self, model: PhysicalModel, scenario: Scenario):
    self.model, self.scenario = model, scenario
    self.components = model.by_id
    self.hydraulics = HydraulicNetwork(model)
    self.hydraulic_cache = {}
    self.states = []
    self.branch_states = []
    for index, branch in enumerate(model.branches):
      positions = []
      capacity = (model.density_kg_m3 * model.cp_j_kg_k * branch.volume_m3 + branch.wall_capacity_j_k) / branch.segments
      for segment in range(branch.segments):
        positions.append(len(self.states))
        suffix = f".cell-{segment}" if branch.segments > 1 else ""
        self.states.append(State(f"{branch.identity}{suffix}.temperature_c", branch.component, "degC", capacity, index))
      self.branch_states.append(positions)
    self.chiller_states = {}
    for component in model.components:
      if component.kind == "chiller":
        self.chiller_states[component.identity] = len(self.states)
        self.states.append(State(f"{component.identity}.cooling_power_w", component.identity, "W", 0, None))
        component.number("capacity_kw", "kW", positive=True)
        component.number("nominal_cop", "1", positive=True)
        component.number("response_tau_s", "s", positive=True)
        component.number("minimum_flow_m3_s", "m3/s", positive=True)
      elif component.kind == "pump":
        efficiency = component.number("efficiency", "1", positive=True)
        fraction = component.number("water_heat_fraction", "1", nonnegative=True)
        if efficiency > 1 or fraction > 1:
          raise PhysicsInputError(f"{component.identity}: efficiency and heat fraction cannot exceed 1.")
      elif component.kind in {"pipe", "tank"}:
        component.number("heat_loss_ua_w_k", "W/K", nonnegative=True)
      elif component.kind == "tower":
        component.number("ua_w_k", "W/K", positive=True)
    self.capacities = np.array([state.capacity_j_k for state in self.states])
    self.energy_index = len(self.states)
    self._hydraulic_signal_names = tuple(name for name in sorted(scenario.signals) if name.endswith((".speed_ratio", ".opening")))

  def hydraulic(self, time):
    key = tuple(self.scenario.signals[name].at(time) for name in self._hydraulic_signal_names)
    if key not in self.hydraulic_cache:
      if len(self.hydraulic_cache) > 256:
        self.hydraulic_cache.clear()
      self.hydraulic_cache[key] = self.hydraulics.solve(self.scenario, time)
    return self.hydraulic_cache[key]

  def evaluate(self, time, state):
    hydraulic = self.hydraulic(time)
    flows = hydraulic.volume_flows_m3_s
    rho, cp = self.model.density_kg_m3, self.model.cp_j_kg_k
    sums = {node: [0.0, 0.0] for node in self.model.junctions}
    adjacent = {node: [] for node in self.model.junctions}
    for index, (branch, flow) in enumerate(zip(self.model.branches, flows)):
      a, b = self.hydraulics.ends[index]
      cells = self.branch_states[index]
      adjacent[a].append(state[cells[0]])
      adjacent[b].append(state[cells[-1]])
      downstream = b if flow >= 0 else a
      out_temperature = state[cells[-1] if flow >= 0 else cells[0]]
      sums[downstream][0] += abs(flow) * out_temperature
      sums[downstream][1] += abs(flow)
    junction_t = {node: total / flow if flow > 1e-12 else float(np.mean(adjacent[node]))
                  for node, (total, flow) in sums.items() if adjacent[node]}
    if len(junction_t) != len(self.model.junctions):
      raise TransientFailure("A thermal junction has no storage-connected branch.")
    derivative = np.zeros(self.energy_index + 1)
    heat = np.zeros(len(self.model.branches))
    external = 0.0
    chiller_power = {}
    pump_power = {}
    branch_loss = []
    ambient_exchange = {}
    for index, (branch, flow) in enumerate(zip(self.model.branches, flows)):
      component = self.components[branch.component]
      opening = self.scenario.value(component.identity, "opening", time) if component.kind == "valve" else 1.0
      dissipation = self.hydraulics._loss(branch, flow, opening) * flow
      heat[index] += dissipation
      branch_loss.append(dissipation)
      if component.kind == "load":
        load = self.scenario.value(component.identity, "load_w", time)
        heat[index] += load
        external += load
      elif component.kind == "pump":
        speed = self.scenario.value(component.identity, "speed_ratio", time)
        hydraulic_power = self.hydraulics._head(branch, flow, speed) * flow
        if hydraulic_power < -1e-4:
          raise TransientFailure(f"{component.identity}: reverse pump/turbine power is outside this constitutive model.")
        electric = max(0.0, hydraulic_power) / component.number("efficiency", "1")
        waste = max(0.0, electric - hydraulic_power) * component.number("water_heat_fraction", "1")
        heat[index] += waste
        external += hydraulic_power + waste
        pump_power[component.identity] = electric
      elif component.kind in {"pipe", "tank", "tower"}:
        average = float(np.mean(state[self.branch_states[index]]))
        if component.kind == "tower":
          ambient = self.scenario.value(component.identity, "ambient_c", time)
          ua = component.number("ua_w_k", "W/K") * self.scenario.value(component.identity, "fan_ratio", time)
        else:
          ambient = self.scenario.value(component.identity, "ambient_c", time)
          ua = component.number("heat_loss_ua_w_k", "W/K")
        loss = ua * (average - ambient)
        ambient_exchange[index] = (ua, ambient)
        external -= loss
    for identity, position in self.chiller_states.items():
      component = self.components[identity]
      evaporator = next(i for i, b in enumerate(self.model.branches) if b.component == identity and b.circuit == "chilled")
      condenser = next(i for i, b in enumerate(self.model.branches) if b.component == identity and b.circuit == "cooling")
      flow = flows[evaporator]
      a, b = self.hydraulics.ends[evaporator]
      inlet_temperature = junction_t[a if flow >= 0 else b]
      setpoint = self.scenario.value(identity, "setpoint_c", time)
      allowed = self.scenario.value(identity, "enabled", time) and min(abs(flows[evaporator]), abs(flows[condenser])) >= component.number("minimum_flow_m3_s", "m3/s")
      request = rho * cp * abs(flow) * (inlet_temperature - setpoint) + branch_loss[evaporator]
      target = min(component.number("capacity_kw", "kW") * 1000, max(0, request)) if allowed else 0
      cooling = float(state[position])
      electric = cooling / component.number("nominal_cop", "1")
      derivative[position] = (target - cooling) / component.number("response_tau_s", "s")
      heat[evaporator] -= cooling
      heat[condenser] += cooling + electric
      external += electric
      chiller_power[identity] = electric
    for index, (branch, flow) in enumerate(zip(self.model.branches, flows)):
      cells = self.branch_states[index]
      a, b = self.hydraulics.ends[index]
      upstream = junction_t[a if flow >= 0 else b]
      order = cells if flow >= 0 else list(reversed(cells))
      for position in order:
        local_heat = heat[index] / len(cells)
        if index in ambient_exchange:
          ua, ambient = ambient_exchange[index]
          local_heat -= ua * (state[position] - ambient) / len(cells)
        derivative[position] = (rho * cp * abs(flow) * (upstream - state[position]) + local_heat) / self.capacities[position]
        upstream = state[position]
    derivative[self.energy_index] = external
    balance = float(np.dot(self.capacities, derivative[:self.energy_index])) - external
    return derivative, {"hydraulic": hydraulic, "junction_temperatures_c": junction_t,
                        "external_heat_w": external, "energy_residual_w": balance,
                        "pump_electric_w": pump_power, "chiller_electric_w": chiller_power}

  def specified_values(self):
    initial = self.scenario.initial
    expected = {state.identity for state in self.states}
    if set(initial["states"]) != expected:
      raise PhysicsInputError("Specified initialization must bind every physical state exactly once.")
    values = np.array([initial["states"][state.identity] for state in self.states], dtype=float)
    if not np.all(np.isfinite(values)):
      raise PhysicsInputError("Initial state values must be finite.")
    for value, item in zip(values, self.states):
      if item.unit == "W" and not 0 <= value <= self.components[item.component].number("capacity_kw", "kW")*1000:
        raise PhysicsInputError(f"{item.identity}: initial cooling power must be within [0, capacity].")
    return values

  def initialize(self):
    initial = self.scenario.initial
    if initial["mode"] == "specified":
      state = np.append(self.specified_values(), 0.0)
      self.evaluate(0, state)
      return state, {"mode": "specified", "status": "pass"}
    guess, lower, upper, scales = [], [], [], []
    load = sum(signal.at(0) for name, signal in self.scenario.signals.items() if name.endswith(".load_w"))
    for state in self.states:
      if state.branch_index is not None:
        guess.append(initial["temperature_guess_c"][self.model.branches[state.branch_index].medium])
        lower.append(0.05); upper.append(99.0); scales.append(state.capacity_j_k)
      else:
        capacity = self.components[state.component].number("capacity_kw", "kW") * 1000
        guess.append(min(capacity * .9, max(1, load / max(1, len(self.chiller_states)))))
        lower.append(0.0); upper.append(capacity); scales.append(self.components[state.component].number("response_tau_s", "s"))
    scales = np.array(scales)

    def residual(values):
      derivative, _ = self.evaluate(0, np.append(values, 0.0))
      return derivative[:self.energy_index] * scales / 1e5

    result = least_squares(residual, guess, bounds=(lower, upper), x_scale="jac", xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=600)
    maximum = float(np.max(np.abs(residual(result.x)))) * 1e5
    if not result.success or maximum > self.scenario.solver["initial_residual_tolerance_w"]:
      raise TransientFailure(f"Consistent steady-state initialization failed; residual={maximum:g} W. Provide a realizable operating point or complete specified states.")
    return np.append(result.x, 0.0), {"mode": "steady-state", "status": "pass", "maximum_residual_w": maximum}

  def run(self):
    initial, initialization = self.initialize()
    duration, interval = self.scenario.duration_s, self.scenario.sample_interval_s
    times = sorted({0.0, duration, *self.scenario.breakpoints, *(min(duration, i * interval) for i in range(math.floor(duration / interval) + 1))})
    samples = {0.0: initial.copy()}
    current = initial.copy()
    evaluations = 0
    options = self.scenario.solver
    atol = [options["temperature_atol_k"] if state.unit == "degC" else options["power_atol_w"] for state in self.states] + [0.01]
    for start, stop in zip(self.scenario.breakpoints, self.scenario.breakpoints[1:]):
      # Keep the old signal value through the left limit; restart integration at
      # each exact input breakpoint rather than stepping over discontinuities.
      end_left = np.nextafter(stop, start)
      solution = solve_ivp(lambda t, y: self.evaluate(min(t, end_left), y)[0], (start, stop), current,
                           method=options["method"], rtol=options["rtol"], atol=atol,
                           max_step=options["max_step_s"], dense_output=True)
      evaluations += solution.nfev
      if not solution.success or not np.all(np.isfinite(solution.y)):
        raise TransientFailure(f"Time integration failed at t={solution.t[-1]:g}: {solution.message}")
      for time in times:
        if start < time <= stop:
          samples[time] = solution.sol(time)
      current = solution.y[:, -1]
    matrix = np.array([samples[t] for t in times])
    return self.report(times, matrix, initialization, evaluations)

  def report(self, times, matrix, initialization, evaluations, backend=None):
    """Assess trajectories; external solvers must independently verify their fluxes."""
    from pathlib import Path
    import platform
    from texsys.evidence import canonical_digest, file_digest

    initial = matrix[0]
    options = self.scenario.solver
    variables = {state.identity: {"unit": state.unit, "semantic_ref": state.component,
                  "role": "state", "values": matrix[:, i].tolist()} for i, state in enumerate(self.states)}
    maximum_mass, maximum_pressure, maximum_energy = 0.0, 0.0, 0.0
    energy0 = float(np.dot(self.capacities, initial[:self.energy_index]))
    integrated_errors = []

    def append(name, unit, reference, role, value):
      variables.setdefault(name, {"unit": unit, "semantic_ref": reference, "role": role, "values": []})["values"].append(float(value))

    for time, state in zip(times, matrix):
      _, output = self.evaluate(time, state)
      hydraulic = output["hydraulic"]
      maximum_mass = max(maximum_mass, hydraulic.mass_residual_kg_s)
      maximum_pressure = max(maximum_pressure, hydraulic.pressure_residual_pa)
      maximum_energy = max(maximum_energy, abs(output["energy_residual_w"]))
      port_flows = {}
      for index, branch in enumerate(self.model.branches):
        a, b = self.hydraulics.ends[index]
        append(branch.identity + ".mass_flow_kg_s", "kg/s", branch.component, "mass-flow", hydraulic.volume_flows_m3_s[index] * self.model.density_kg_m3)
        stopped = abs(hydraulic.volume_flows_m3_s[index]) < 1e-12
        cells = self.branch_states[index]
        append(branch.identity + ".outlet_temperature_c", "degC", branch.component, "outlet-temperature", state[cells[-1]] if stopped else output["junction_temperatures_c"][b])
        append(branch.identity + ".inlet_temperature_c", "degC", branch.component, "inlet-temperature", state[cells[0]] if stopped else output["junction_temperatures_c"][a])
        append(branch.identity + ".water_temperature_c", "degC", branch.component, "fluid-temperature", np.mean(state[cells]))
        append(branch.identity + ".pressure_drop_pa", "Pa", branch.component, "pressure-drop", hydraulic.pressures_pa[a] - hydraulic.pressures_pa[b])
        port_flows[branch.inlet] = hydraulic.volume_flows_m3_s[index] * self.model.density_kg_m3
        port_flows[branch.outlet] = -port_flows[branch.inlet]
      for source, target in self.model.connections:
        reference = f"{self.model.root}:{source[len(self.model.root)+1:]}~{target[len(self.model.root)+1:]}"
        flow = -port_flows[source] if source in port_flows else port_flows.get(target)
        if flow is not None:
          append("connection:" + reference + ".mass_flow_kg_s", "kg/s", reference, "connection-flow", flow)
      for reference, value in {**output["pump_electric_w"], **output["chiller_electric_w"]}.items():
        append(reference + ".electric_power_w", "W", reference, "electric-power", value)
      for reference, signal in self.scenario.signals.items():
        append("input:" + reference, signal.unit, reference.rsplit(".", 1)[0], "input", signal.at(time))
      total_energy = float(np.dot(self.capacities, state[:self.energy_index]))
      integrated_errors.append(total_energy - energy0 - state[self.energy_index])
      append("system.external_heat_w", "W", self.model.root, "energy-boundary", output["external_heat_w"])
      append("system.energy_balance_error_j", "J", self.model.root, "conservation", integrated_errors[-1])
    assertions = []
    for declaration in self.scenario.assertions:
      variable = variables.get(declaration["variable"])
      if variable is None or variable["unit"] != declaration["unit"]:
        assertions.append({**declaration, "status": "indeterminate", "message": "Variable is missing or its unit differs."})
        continue
      actual = max(variable["values"]) if declaration["operator"] == "<=" else min(variable["values"])
      passed = actual <= declaration["value"] if declaration["operator"] == "<=" else actual >= declaration["value"]
      assertions.append({**declaration, "status": "pass" if passed else "fail", "actual": actual})
    temperatures = matrix[:, [i for i, s in enumerate(self.states) if s.unit == "degC"]]
    if np.min(temperatures) <= 0 or np.max(temperatures) >= 100:
      assertions.append({"status": "fail", "rule": "liquid-water-model-range", "message": "A temperature left the declared single-phase liquid-water range (0, 100) degC."})
    conservation = maximum_energy < 0.1 and max(abs(v) for v in integrated_errors) < 1.0
    if not conservation:
      assertions.append({"status": "fail", "rule": "energy-conservation", "message": "Thermal conservation exceeds 0.1 W or integrated 1 J numerical tolerance."})
    status = "fail" if any(a["status"] == "fail" for a in assertions) else "indeterminate" if any(a["status"] == "indeterminate" for a in assertions) else "pass"
    events = [{"time_s": t, "changes": {name: {"before": signal.at(np.nextafter(t, 0)), "after": signal.at(t)}
              for name, signal in self.scenario.signals.items() if signal.at(np.nextafter(t, 0)) != signal.at(t)}} for t in self.scenario.breakpoints[1:-1]]
    return {"schema_version": 1, "status": status, "scenario": self.scenario.identity,
            "model_digest": self.model.snapshot_digest, "scenario_digest": self.scenario.digest,
            "backend": {"name": self.version, "python": platform.python_version(), "scipy": scipy.__version__, "numpy": np.__version__,
                        "implementation_digest": canonical_digest({p.name: file_digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))}),
                        "solver": dict(options), **(backend or {})},
            "initialization": initialization, "time_s": times, "variables": variables, "events": events,
            "assertions": assertions, "checks": {"mass_residual_kg_s": maximum_mass, "pressure_residual_pa": maximum_pressure,
              "energy_residual_w": maximum_energy, "integrated_energy_error_j": max(abs(v) for v in integrated_errors)},
            "assumptions": list(self.model.assumptions), "input_evaluations": evaluations}
