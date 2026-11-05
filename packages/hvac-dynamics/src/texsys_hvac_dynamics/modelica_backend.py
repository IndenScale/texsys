"""Independent Modelica DAE export, execution and constitutive residual audit.

The export contains equations and input signals, never precomputed trajectories.
"""

import csv
import math
import os
from pathlib import Path
import shutil
import subprocess

import numpy as np

from texsys.evidence import file_digest
from .domain import PhysicsInputError, PhysicsUnresolved
from .thermal import ThermalNetwork, TransientFailure


def _sum(items):
  return "(" + " + ".join(items) + ")" if items else "0.0"


def _num(value):
  return repr(float(value))


def export_modelica(model, scenario):
  network = ThermalNetwork(model, scenario)
  if scenario.initial["mode"] == "specified":
    network.specified_values()
  branches, components = model.branches, model.by_id
  rho, cp, gravity = model.density_kg_m3, model.cp_j_kg_k, model.gravity_m_s2
  node_ids = {node: i + 1 for i, node in enumerate(model.junctions)}
  ends = [(node_ids[a], node_ids[b]) for a, b in network.hydraulics.ends]
  declarations, equations, initial = [], [], []

  def real(name, start=None):
    declarations.append(f"  Real {name}" + (f"(start={_num(start)})" if start is not None else "") + ";")

  def equation(name, expression):
    equations.append(f"  {name} = {expression};")

  signals = {}
  for i, (name, signal) in enumerate(scenario.signals.items(), 1):
    symbol = f"u{i}"
    signals[name] = symbol
    real(symbol)
    expression = _num(signal.points[-1][1])
    for (time, value), (next_time, next_value) in reversed(list(zip(signal.points, signal.points[1:]))):
      local = _num(value) if signal.interpolation == "hold" else f"({_num(value)} + (time-{_num(time)})*{_num((next_value-value)/(next_time-time))})"
      expression = f"(if time < {_num(next_time)} then {local} else {expression})"
    equation(symbol, expression)

  def signal(identity, name):
    return signals[f"{identity}.{name}"]

  for i, state in enumerate(network.states, 1):
    guess = scenario.initial.get("temperature_guess_c", {}).get(branches[state.branch_index].medium, 20) if state.branch_index is not None else 40000
    if scenario.initial["mode"] == "specified":
      if set(scenario.initial["states"]) != {s.identity for s in network.states}:
        raise PhysicsInputError("Specified initialization must bind every state.")
      guess = scenario.initial["states"][state.identity]
      initial.append(f"  x{i} = {_num(guess)};")
    else:
      initial.append(f"  der(x{i}) = 0.0;")
    real(f"x{i}", guess)
    real(f"dx{i}")
    equation(f"der(x{i})", f"dx{i}")
  real("energy", 0)
  initial.append("  energy = 0.0;")
  real("externalHeat")
  equation("der(energy)", "externalHeat")
  adjacency = {i: set() for i in node_ids.values()}
  for a, b in ends:
    adjacency[a].add(b)
    adjacency[b].add(a)
  references, seen = set(), set()
  for node in adjacency:
    if node in seen:
      continue
    references.add(node)
    pending = [node]
    while pending:
      current = pending.pop()
      if current not in seen:
        seen.add(current)
        pending.extend(adjacency[current] - seen)
  for node in node_ids.values():
    real(f"p{node}", 0)
    real(f"j{node}", 20)
    incoming, weights, adjacent, continuity = [], [], [], []
    for i, (a, b) in enumerate(ends, 1):
      cells = network.branch_states[i-1]
      if a == node:
        continuity.append(f"(-q{i})")
        incoming.append(f"max(-q{i},0.0)*x{cells[0]+1}")
        weights.append(f"max(-q{i},0.0)")
        adjacent.append(f"x{cells[0]+1}")
      if b == node:
        continuity.append(f"q{i}")
        incoming.append(f"max(q{i},0.0)*x{cells[-1]+1}")
        weights.append(f"max(q{i},0.0)")
        adjacent.append(f"x{cells[-1]+1}")
    if not adjacent:
      raise PhysicsInputError("A junction requires a storage-connected branch.")
    equation(f"p{node}" if node in references else "0.0", "0.0" if node in references else _sum(continuity))
    total = _sum(weights)
    equation(f"j{node}", f"if noEvent({total} > 1e-12) then {_sum(incoming)}/max(1e-12,{total}) else {_sum(adjacent)}/{len(adjacent)}")
  heat = {i: [f"loss{i}*q{i}"] for i in range(1, len(branches)+1)}
  ambient_exchange, external = {}, []
  for i, branch in enumerate(branches, 1):
    component = components[branch.component]

    def number(name, unit):
      return _num(component.number(name, unit))

    real(f"q{i}", .005)
    real(f"loss{i}")
    real(f"gain{i}")
    real(f"heat{i}")
    a, b = ends[i-1]
    if component.kind == "pipe":
      loss = f"pipeLoss(q{i},{number('diameter_m','m')},{number('length_m','m')},{number('roughness_m','m')},{number('minor_loss_coefficient','1')},{_num(rho)},{_num(model.viscosity_pa_s)})"
    elif component.kind == "valve":
      opening = signal(component.identity, "opening")
      coefficient = 1e5*rho/1000*(3600/component.number('kv_m3_h','m3/h'))**2
      loss = f"if {opening} > 0 then {_num(coefficient)}*q{i}*abs(q{i})/{opening}^2 else 0.0"
    else:
      prefix = branch.circuit + "_" if component.kind == "chiller" else ""
      coefficient = component.number(prefix+'nominal_pressure_drop_pa','Pa') / component.number(prefix+'nominal_flow_m3_s','m3/s')**2
      loss = f"{_num(coefficient)}*q{i}*abs(q{i})"
    equation(f"loss{i}", loss)
    gain = f"{_num(rho*gravity)}*({number('shutoff_head_m','m')}*{signal(component.identity,'speed_ratio')}^2-{number('curve_m_per_m6_s2','s2/m5')}*q{i}*abs(q{i}))" if component.kind == "pump" else "0.0"
    equation(f"gain{i}", gain)
    elevation = rho*gravity*component.number("elevation_change_m", "m")
    drive = f"(p{a}-p{b}+gain{i}-loss{i}-{_num(elevation)})"
    if component.kind == "valve":
      equations.append(f"  if {signal(component.identity,'opening')} <= 0 then q{i}=0.0; else {drive}=0.0; end if;")
    elif component.kind == "pump" and component.number("check_valve", "1") == 1:
      # Monotone pump curve with exact check-valve complementarity.
      coefficient = rho*gravity*component.number("curve_m_per_m6_s2", "s2/m5") + component.number("nominal_pressure_drop_pa", "Pa")/component.number("nominal_flow_m3_s", "m3/s")**2
      pressure = f"(p{a}-p{b}+{_num(rho*gravity*component.number('shutoff_head_m','m'))}*{signal(component.identity,'speed_ratio')}^2-{_num(elevation)})"
      equation(f"q{i}", f"sqrt(max(0.0,{pressure})/{_num(coefficient)})")
    else:
      equation(drive, "0.0")
    if component.kind == "pump":
      real(f"power{i}")
      equation(f"power{i}", f"max(0.0,gain{i}*q{i})/{number('efficiency','1')}")
      waste = f"max(0.0,power{i}-gain{i}*q{i})*{number('water_heat_fraction','1')}"
      heat[i].append(waste)
      external.extend([f"gain{i}*q{i}", waste])
    elif component.kind == "load":
      heat[i].append(signal(component.identity, "load_w"))
      external.append(signal(component.identity, "load_w"))
    elif component.kind in {"pipe", "tank", "tower"}:
      ua = number("ua_w_k", "W/K")+"*"+signal(component.identity, "fan_ratio") if component.kind == "tower" else number("heat_loss_ua_w_k", "W/K")
      ambient = signal(component.identity, "ambient_c")
      ambient_exchange[i] = (ua, ambient)
      average = _sum([f"x{s+1}" for s in network.branch_states[i-1]])+f"/{branch.segments}"
      external.append(f"(-({ua})*({average}-{ambient}))")
  for identity, position in network.chiller_states.items():
    component = components[identity]
    evaporator = next(i for i, b in enumerate(branches, 1) if b.component == identity and b.circuit == "chilled")
    condenser = next(i for i, b in enumerate(branches, 1) if b.component == identity and b.circuit == "cooling")
    a, b = ends[evaporator-1]
    request = f"({_num(rho*cp)}*abs(q{evaporator})*((if q{evaporator}>=0 then j{a} else j{b})-{signal(identity,'setpoint_c')})+loss{evaporator}*q{evaporator})"
    allowed = f"{signal(identity,'enabled')}>0 and min(abs(q{evaporator}),abs(q{condenser}))>={_num(component.number('minimum_flow_m3_s','m3/s'))}"
    target = f"(if {allowed} then min({_num(component.number('capacity_kw','kW')*1000)},max(0.0,{request})) else 0.0)"
    equation(f"dx{position+1}", f"({target}-x{position+1})/{_num(component.number('response_tau_s','s'))}")
    power = f"x{position+1}/{_num(component.number('nominal_cop','1'))}"
    heat[evaporator].append(f"(-x{position+1})")
    heat[condenser].extend([f"x{position+1}", power])
    external.append(power)
  for i, branch in enumerate(branches, 1):
    equation(f"heat{i}", _sum(heat[i]))
    a, b = ends[i-1]
    cells = network.branch_states[i-1]
    for cell, position in enumerate(cells):
      before = f"x{cells[cell-1]+1}" if cell else f"j{a}"
      after = f"x{cells[cell+1]+1}" if cell < len(cells)-1 else f"j{b}"
      upstream = f"(if q{i}>=0 then {before} else {after})"
      local_heat = f"heat{i}/{len(cells)}"
      if i in ambient_exchange:
        ua, ambient = ambient_exchange[i]
        local_heat += f"-({ua})*(x{position+1}-{ambient})/{len(cells)}"
      equation(f"dx{position+1}", f"({_num(rho*cp)}*abs(q{i})*({upstream}-x{position+1})+{local_heat})/{_num(network.capacities[position])}")
  equation("externalHeat", _sum(external))
  function = """  function pipeLoss
    input Real q; input Real d; input Real l; input Real rough; input Real minor; input Real rho; input Real mu;
    output Real dp;
  protected
    Real area; Real re; Real lam; Real f; Real turbulent; Real blend;
  algorithm
    area := 3.141592653589793*d*d/4;
    re := rho*abs(q)*d/(mu*area);
    lam := 128*mu*l*q/(3.141592653589793*d^4);
    if re <= 2300 then dp := lam;
    else
      f := (-1.8*log((rough/(3.7*d))^1.11+6.9/re)/log(10.0))^(-2);
      turbulent := f*l/d*rho*q*abs(q)/(2*area^2);
      blend := min(1.0,(re-2300)/1700);
      dp := lam*(1-blend)+turbulent*blend;
    end if;
    dp := dp+minor*rho*q*abs(q)/(2*area^2);
  end pipeLoss;"""
  return "within ;\nmodel TexsysWaterNetwork\n"+function+"\n"+"\n".join(declarations)+"\ninitial equation\n"+"\n".join(initial)+"\nequation\n"+"\n".join(equations)+"\nend TexsysWaterNetwork;\n"


def run_modelica(model, scenario, directory):
  directory.mkdir(parents=True, exist_ok=True)
  for name in ("TexsysWaterNetwork_res.csv", "openmodelica.log", "run.mos"):
    (directory / name).unlink(missing_ok=True)
  source = directory / "TexsysWaterNetwork.mo"
  source.write_text(export_modelica(model, scenario), encoding="utf-8")
  binary = os.environ.get("TEXSYS_OMC") or shutil.which("omc")
  if not binary or not Path(binary).is_file():
    raise PhysicsUnresolved("OpenModelica is unavailable. Install omc or set TEXSYS_OMC; Modelica source was exported.")
  binary = str(Path(binary).resolve())
  csv_path = directory / "TexsysWaterNetwork_res.csv"
  csv_path.unlink(missing_ok=True)
  try:
    version_run = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
    if version_run.returncode:
      raise PhysicsUnresolved("OpenModelica cannot start: "+version_run.stderr[-2000:])
    script = directory / "run.mos"
    intervals = math.ceil(scenario.duration_s/scenario.sample_interval_s)
    script.write_text('loadFile("TexsysWaterNetwork.mo");\ngetErrorString();\n'+f'simulate(TexsysWaterNetwork, stopTime={scenario.duration_s}, numberOfIntervals={intervals}, tolerance={scenario.solver["rtol"]}, method="dassl", outputFormat="csv", simflags="-maxStepSize={scenario.solver["max_step_s"]}");\ngetErrorString();\n', encoding="utf-8")
    completed = subprocess.run([binary, "run.mos"], cwd=directory, capture_output=True, text=True, timeout=180)
  except subprocess.TimeoutExpired as error:
    raise TransientFailure("OpenModelica exceeded the compile/simulation timeout.") from error
  (directory / "openmodelica.log").write_text(completed.stdout+completed.stderr, encoding="utf-8")
  if completed.returncode or not csv_path.is_file():
    raise TransientFailure("OpenModelica produced no trajectory. See openmodelica.log: "+(completed.stdout+completed.stderr)[-2000:])
  with csv_path.open() as stream:
    rows = list(csv.DictReader(stream))
  if not rows:
    raise TransientFailure("OpenModelica returned an empty trajectory.")
  # The CSV contains left/right event iterations with slightly different time
  # roundoff. Retain the right-hand input state at the canonical knot, never
  # assess a left-limit derivative using right-limit controls.
  unique = {}
  for row in rows:
    time = float(row["time"])
    knot = next((t for t in scenario.breakpoints if abs(t-time) < 1e-6), None)
    if knot is not None:
      time = knot
    matches = all(math.isclose(float(row[f"u{i}"]), signal.at(time), abs_tol=1e-7, rel_tol=1e-9)
                  for i, signal in enumerate(scenario.signals.values(), 1))
    if not matches:
      if knot is not None:
        continue
      raise TransientFailure("External input signal differs from the scenario away from an event.")
    unique[time] = row
  times = sorted(unique)
  network = ThermalNetwork(model, scenario)
  matrix = np.array([[float(unique[t][f"x{i+1}"]) for i in range(len(network.states))]+[float(unique[t]["energy"])] for t in times])
  if not np.all(np.isfinite(matrix)) or abs(times[-1]-scenario.duration_s) > 1e-6:
    raise TransientFailure("OpenModelica returned nonfinite values or stopped early.")
  maximum_flow, maximum_rhs, maximum_pressure, maximum_mass = 0.0, 0.0, 0.0, 0.0
  node_ids = {node: i + 1 for i, node in enumerate(model.junctions)}
  scales = [state.capacity_j_k if state.branch_index is not None else model.by_id[state.component].number("response_tau_s", "s") for state in network.states]
  for time, state in zip(times, matrix):
    derivative, output = network.evaluate(time, state)
    row = unique[time]
    maximum_flow = max(maximum_flow, max(abs(float(row[f"q{i+1}"])-flow) for i, flow in enumerate(output["hydraulic"].volume_flows_m3_s)))
    maximum_rhs = max(maximum_rhs, max(abs(float(row[f"dx{i+1}"])-value)*scales[i] for i, value in enumerate(derivative[:-1])))
    continuity = {node: 0.0 for node in model.junctions}
    for i, branch in enumerate(model.branches, 1):
      a, b = network.hydraulics.ends[i-1]
      flow = float(row[f"q{i}"])
      continuity[a] -= flow
      continuity[b] += flow
      component = model.by_id[branch.component]
      opening = scenario.value(component.identity, "opening", time) if component.kind == "valve" else 1.0
      speed = scenario.value(component.identity, "speed_ratio", time) if component.kind == "pump" else 0.0
      residual = float(row[f"p{node_ids[a]}"]) - float(row[f"p{node_ids[b]}"]) + network.hydraulics._head(branch, flow, speed) - network.hydraulics._loss(branch, flow, opening) - model.density_kg_m3*model.gravity_m_s2*component.number("elevation_change_m", "m")
      if component.kind == "valve" and opening == 0:
        continue
      if component.kind == "pump" and component.number("check_valve", "1") == 1 and abs(flow) < 1e-12:
        residual = max(0.0, residual)  # Closed check valve may support reverse pressure.
      maximum_pressure = max(maximum_pressure, abs(residual))
    maximum_mass = max(maximum_mass, max(abs(q)*model.density_kg_m3 for q in continuity.values()))
  if maximum_flow > 1e-7 or maximum_rhs > 0.1 or maximum_pressure > scenario.solver["hydraulic_pressure_tolerance_pa"] or maximum_mass > scenario.solver["hydraulic_mass_tolerance_kg_s"]:
    raise TransientFailure(f"External constitutive audit failed: flow={maximum_flow:g} m3/s, thermal equation={maximum_rhs:g} W.")
  initial_residual = max(abs(value)*scale for value, scale in zip(network.evaluate(0, matrix[0])[0][:-1], scales))
  if scenario.initial["mode"] == "steady-state" and initial_residual > scenario.solver["initial_residual_tolerance_w"]:
    raise TransientFailure(f"External steady initialization residual={initial_residual:g} W.")
  result = network.report(times, matrix, {"mode": scenario.initial["mode"], "status": "pass", "solver": "OpenModelica initialization", "maximum_residual_w": initial_residual}, len(rows),
      {"name": "openmodelica", "compiler": version_run.stdout.strip(), "compiler_entry_digest": file_digest(Path(binary)), "export_digest": file_digest(source), "method": "dassl", "reference_evaluation": "Python constitutive residual audit only"})
  result["external_audit"] = {"maximum_flow_difference_m3_s": maximum_flow, "maximum_thermal_equation_residual_w": maximum_rhs,
                              "maximum_pressure_residual_pa": maximum_pressure, "maximum_mass_residual_kg_s": maximum_mass}
  return result
