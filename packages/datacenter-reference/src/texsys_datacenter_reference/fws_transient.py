"""Thermal transport on the installed FWS circuit at prescribed hydraulic states."""

import csv
import math

import numpy as np

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .fws_network import MODEL as ROUTE
from .fws_network_study import _port_node
from .fws_thermal_parameters import HYDRAULIC, MODEL as PARAMETERS
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json, vector


MODEL = f"{MODELS}/fws-transient.json"
_FLOW_EPSILON = 1e-11


def _boundaries(case):
  result = [{"inlet": _port_node("supply", name), "outlet": _port_node("return", name), "id": name,
             "flow": row["flow_m3_s"], "heat_kw": row["heat_kw"]} for name, row in sorted(case["terminals"].items())]
  result.extend({"inlet": _port_node("return", name), "outlet": _port_node("supply", name), "id": name,
                 "flow": row["flow_m3_s"], "capacity_kw": row["cooling_available_kw"]} for name, row in sorted(case["duty"].items()))
  return result


class Circuit:
  """Conservative slab averages of plug flow on the fixed hydraulic graph."""

  def __init__(self, route, parameters, case, normal, settings):
    self.terminal_ids = sorted(case["terminals"])
    fluids = parameters["fluid"]
    self.rho_cp = fluids["density_kg_m3"]*fluids["cp_kj_kg_k"]
    self.setpoint = settings["supply_setpoint_c"]
    normal_return = self.setpoint + fluids["delta_t_k"]
    boundary = _boundaries(case)
    count = len(self.terminal_ids)
    self.plants = [row["id"] for row in boundary[count:]]
    self.capacity_kw = np.array([row["capacity_kw"] for row in boundary[count:]])
    self.boundary_flow = np.array([row["flow"] for row in boundary])
    self.plant_flow_heat_capacity = self.rho_cp*self.boundary_flow[count:]
    self.terminal_heat = np.array([row["heat_kw"] for row in boundary[:count]])
    self.load_kw = float(self.terminal_heat.sum())
    node = {name: index for index, name in enumerate(route["nodes"])}
    self.node_count = len(node)
    self.boundary_inlet = np.array([node[row["inlet"]] for row in boundary])
    self.boundary_outlet = np.array([node[row["outlet"]] for row in boundary])
    self.terminal_sensor = self.boundary_inlet[:count]

    upstream, downstream, volume, rates, initial = [], [], [], [], []
    for key, row in route["segments"].items():
      flow = case["pipes"][key]["flow_m3_s"]
      if abs(flow) <= _FLOW_EPSILON:
        continue
      a, b = row["from"], row["to"]
      if flow < 0:
        a, b = b, a
      upstream.append(node[a])
      downstream.append(node[b])
      volume.append(parameters["segments"][key]["water_volume_m3"])
      rates.append(abs(flow))
      flowing_before = abs(normal["pipes"][key]["flow_m3_s"]) > _FLOW_EPSILON
      if row["leg"] == "supply":
        initial.append(self.setpoint if flowing_before else settings["standby_supply_c"])
      else:
        initial.append(normal_return if flowing_before else settings["standby_return_c"])
    self.upstream, self.downstream = np.array(upstream), np.array(downstream)
    self.flow, self.initial, self.volume = np.array(rates), np.array(initial), np.array(volume)
    self.transit = self.volume/self.flow
    self.physical_inflow = self._sum(self.downstream, self.flow)
    virtual_inflow = self._sum(self.boundary_outlet, self.boundary_flow)
    self.inflow = self.physical_inflow+virtual_inflow
    outflow = self._sum(self.upstream, self.flow) + self._sum(self.boundary_inlet, self.boundary_flow)
    self.mass_residual_m3_s = float(abs(self.inflow-outflow).max())
    if self.mass_residual_m3_s > 1e-10:
      raise ValueError(f"Prescribed thermal flows must conserve nodal mass: {self.mass_residual_m3_s:g} m3/s.")
    if min(self.physical_inflow[self.boundary_inlet]) <= 0 or any(virtual_inflow[self.boundary_inlet] > 0):
      raise ValueError("A CDU or cooling source must receive flow from installed boundary pipes.")

  def _sum(self, nodes, values):
    return np.bincount(nodes, weights=values, minlength=self.node_count)

  def mix(self, outlet_temperature, standby, availability, *, buffer_state=None, buffer_factor=None):
    incoming = self._sum(self.downstream, self.flow*outlet_temperature)
    boundary_temperature = incoming[self.boundary_inlet]/self.physical_inflow[self.boundary_inlet]
    first_plant = len(self.terminal_ids)
    capacity = self.capacity_kw.copy()
    if standby is not None:
      capacity[standby] *= availability
    extract = np.clip(self.plant_flow_heat_capacity*(boundary_temperature[first_plant:]-self.setpoint), 0., capacity)
    heat = np.concatenate((self.terminal_heat, -extract))
    virtual_enthalpy = self.boundary_flow*boundary_temperature + heat/self.rho_cp
    if buffer_state is not None:
      exchanged, average = buffer_factor
      source_flow = self.boundary_flow[first_plant:]
      feed = virtual_enthalpy[first_plant:]/source_flow
      delta = buffer_state-feed
      virtual_enthalpy[first_plant:] = source_flow*(feed + delta*average)
      buffer_state -= delta*exchanged
    mixed = incoming + self._sum(self.boundary_outlet, virtual_enthalpy)
    return mixed[self.upstream]/self.inflow[self.upstream], boundary_temperature[:first_plant], float(extract.sum())

  def run(self, settings, standby_id, target_step):
    standby = self.plants.index(standby_id)
    delay, ramp, duration = (settings[key] for key in ("standby_delay_s", "standby_ramp_s", "duration_s"))
    steps = math.ceil(duration/min(target_step, float(self.transit.min())))
    step = duration/steps
    bins = np.maximum(1., self.transit/step)
    shift = np.floor(bins).astype(int)
    fraction = bins-shift
    pipe_index = np.arange(len(self.flow))
    history = np.empty((steps+1, len(self.flow)))
    history[0] = self.initial
    observations = np.empty((steps, len(self.terminal_ids)))
    removed = 0.
    times = (np.arange(steps)+.5)*step
    buffer_volume = settings["plant_outlet_buffer_m3"]
    buffer_state = np.full(len(self.plants), self.setpoint, dtype=float) if buffer_volume else None
    factor, initial_buffer = None, None
    if buffer_state is not None:
      buffer_state[standby] = settings["standby_supply_c"]
      initial_buffer = buffer_state.copy()
      rate = self.boundary_flow[len(self.terminal_ids):]*step/buffer_volume
      exchanged = -np.expm1(-rate)
      factor = (exchanged, exchanged/rate)
    for k, t in enumerate(times):
      # Reconstruct the mean outlet enthalpy over this interval from the
      # exact overlap of its retarded interval with the stored inlet slabs.
      recent = k-shift+1
      latest = history[np.maximum(recent, 0), pipe_index]
      earlier = history[np.maximum(recent-1, 0), pipe_index]
      outlets = latest*(1-fraction) + earlier*fraction
      available = min(1., max(0., (t-delay)/ramp))
      history[k+1], observations[k], extraction = self.mix(outlets, standby, available,
                                                           buffer_state=buffer_state, buffer_factor=factor)
      removed += extraction*step

    # The final contents of a pipe are precisely the retained inlet history
    # inside its transit window, plus any original water still in the pipe.
    retained = np.empty(len(self.flow))
    for index, residence in enumerate(self.transit):
      full = min(steps, shift[index])
      start = steps-full
      mean_integral = float(history[start+1:, index].sum())*step
      mean_integral += history[start, index]*(residence-full*step)
      retained[index] = mean_integral/residence
    pipe_storage = float(self.rho_cp*self.volume @ (retained-self.initial))
    buffer_storage = float((buffer_state-initial_buffer).sum()*self.rho_cp*buffer_volume) if buffer_volume else 0.
    stored = pipe_storage+buffer_storage
    thermal_input = duration*self.load_kw
    residual = thermal_input-removed-stored
    if abs(residual) > 1e-7*thermal_input or not np.isfinite(observations).all():
      raise ValueError(f"FWS energy balance failed with a residual of {residual:g} kJ.")
    return times, observations, {"input_kj": thermal_input, "removed_kj": removed,
            "stored_change_kj": stored, "pipe_stored_change_kj": pipe_storage, "buffer_stored_change_kj": buffer_storage,
            "residual_kj": residual, "active_pipe_count": len(self.flow),
            "maximum_junction_flow_residual_m3_s": self.mass_residual_m3_s, "step_s": step}


def _summary(times, curve, limit):
  peak = int(curve.argmax())
  over = np.flatnonzero(curve > limit)
  first = float(times[over[0]]) if len(over) else None
  if len(over) and over[0] > 0:
    i = over[0]
    first = float(times[i-1] + (times[i]-times[i-1])*(limit-curve[i-1])/(curve[i]-curve[i-1]))
  return {"peak_c": float(curve[peak]), "peak_time_s": float(times[peak]),
          "first_above_limit_s": first, "final_c": float(curve[-1])}


def _settings(config):
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("Describe the assumed thermal switchover basis.")
  key_t = ("supply_setpoint_c", "standby_supply_c", "standby_return_c", "terminal_inlet_limit_c")
  values = dict(zip(key_t, vector([config[key] for key in key_t], 4, "facility temperatures in Celsius"), strict=True))
  values.update({key: number(config[key], key, zero=key in {"standby_delay_s", "plant_outlet_buffer_m3"}) for key in
                 ("standby_delay_s", "standby_ramp_s", "duration_s", "solver_step_s", "sample_interval_s",
                  "peak_refinement_tolerance_c", "recovery_tolerance_c", "plant_outlet_buffer_m3")})
  if (values["terminal_inlet_limit_c"] <= values["supply_setpoint_c"]
      or values["standby_delay_s"]+values["standby_ramp_s"] >= values["duration_s"]):
    raise ValueError("Require inlet temperature headroom and a duration that includes full standby power.")
  return values


def _scenario(route, parameters, case, normal, plant, settings):
  active = set(normal["duty"])
  standby, = set(case["duty"])-active
  step, sample = settings["solver_step_s"], settings["sample_interval_s"]
  circuit = Circuit(route, parameters, case, normal, settings)
  _, coarse_temp, coarse_energy = circuit.run(settings, standby, step)
  times, temps, energy = circuit.run(settings, standby, step/2)
  trace_steps = max(1, math.floor(sample/energy["step_s"]))
  stations = sorted({0, len(times)-1, *range(0, len(times), trace_steps), *temps.argmax(axis=0)})
  limit, setpoint = settings["terminal_inlet_limit_c"], settings["supply_setpoint_c"]
  coarse_peaks = coarse_temp.max(axis=0)
  peaks = temps.max(axis=0)
  terminals = {}
  for i, name in enumerate(circuit.terminal_ids):
    local = _summary(times, temps[:, i], limit)
    envelope = float(max(peaks[i], coarse_peaks[i]))
    delta = float(abs(peaks[i]-coarse_peaks[i]))
    checks = {"temperature": envelope <= limit, "resolution": delta <= settings["peak_refinement_tolerance_c"],
              "recovery": abs(local["final_c"]-setpoint) <= settings["recovery_tolerance_c"]}
    terminals[name] = {**local, "coarse_peak_c": float(coarse_peaks[i]), "comparison_peak_c": envelope,
                       "refinement_delta_c": delta, "checks": checks, "pass": all(checks.values())}
  governing = max(terminals, key=lambda name: terminals[name]["comparison_peak_c"])
  return {"unavailable_plant": plant, "active_plants": circuit.plants, "standby_plant": standby,
          "governing_terminal": governing, "terminals": terminals, "fine_energy": energy, "coarse_energy": coarse_energy,
          "trace": {"time_s": times[stations].tolist(),
                    "cdu_inlet_c": {name: temps[stations, i].tolist() for i, name in enumerate(circuit.terminal_ids)}},
          "pass": all(row["pass"] for row in terminals.values())}


def build_transient(context):
  path = source_path(context, "fws-transient.json")
  if not path.is_file():
    return missing_input(context, path, "fws-transient")
  source = read_versioned(path)
  settings = _settings(source)
  route = staged_json(context, ROUTE)
  parameters = staged_json(context, PARAMETERS)
  hydraulic = staged_json(context, HYDRAULIC)
  normal = hydraulic["cases"]["normal"]
  baseline = Circuit(route, parameters, normal, normal, settings)
  propagated, _, _ = baseline.mix(baseline.initial, None, 1.)
  initial_residual_c = float(np.abs(propagated-baseline.initial).max())
  if initial_residual_c > 1e-7:
    raise ValueError("The initial FWS temperatures must satisfy the normal-flow heat balance.")
  cases = {}
  for plant in sorted(normal["duty"]):
    key = f"plant-off:{plant}"
    cases[key] = _scenario(route, parameters, hydraulic["cases"][key], normal, plant, settings)
  model = model_report(context, "fws-transient", {"basis": source["basis"], "state": source["state"], "settings": settings,
                       "source_digests": {path: file_digest(context.build_path / path) for path in (ROUTE, PARAMETERS, HYDRAULIC)},
                       "equilibrium_max_initial_error_c": initial_residual_c, "scenarios": cases,
                       "engineering_status": "pass" if all(row["pass"] for row in cases.values()) else "fail",
                       "method": "fixed-flow-plug-transport/1"})
  table_path = context.build_path / "schedules/aidc-fws-transient-terminals.csv"
  table_path.parent.mkdir(parents=True, exist_ok=True)
  with table_path.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("case", "standby", "terminal", "peak_c", "peak_time_s", "envelope_c", "refinement_delta_c", "final_c", "status"))
    for name, case in cases.items():
      for terminal, row in case["terminals"].items():
        table.writerow((name, case["standby_plant"], terminal, round(row["peak_c"], 4), round(row["peak_time_s"], 3),
                        round(row["comparison_peak_c"], 4), round(row["refinement_delta_c"], 6), round(row["final_c"], 4), row["pass"]))
  return TargetResult(model.status, (*model.artifacts, artifact(context, "fws-transient-terminals", table_path)), model.diagnostics)


def build_transient_check(context):
  model = staged_json(context, MODEL)
  issues = [{"rule": "AIDC_FWS_TRANSIENT_LIMIT", "status": "fail", "objects": [name, terminal],
             "message": f"{name}/{terminal}: {', '.join(key for key, passed in row['checks'].items() if not passed)}; "
                        f"temperature envelope {row['comparison_peak_c']:.3f} °C."}
            for name, case in model["scenarios"].items() for terminal, row in case["terminals"].items() if not row["pass"]]
  return model_report(context, "fws-transient-check", {"source_digest": file_digest(context.build_path / MODEL),
                      "case_count": len(model["scenarios"]), "issues": issues}, issues)
