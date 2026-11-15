"""Secondary rack duty and geometry back-annotation at required and feasible flows."""

import csv
from functools import lru_cache
from math import pi

from scipy.optimize import brentq

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_hvac_dynamics.hydraulics import pipe_loss

from .checks import load_design
from .fws_network_study import _rated
from .fws_transient import MODEL as TRANSIENT
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json
from .tcs_routing import MODEL as ROUTING, SIDES


MODEL = f"{MODELS}/tcs-study.json"
FWS = f"{MODELS}/fws-network-study.json"


def _settings(source):
  keys = ("secondary_target_supply_c", "secondary_rack_rise_k", "cdu_approach_allowance_k", "cdu_reference_flow_l_s",
          "cdu_shutoff_kpa", "cdu_head_kpa_at_reference_flow", "cdu_secondary_drop_kpa_at_reference_flow",
          "cdu_pump_efficiency", "minimum_terminal_control_kpa", "maximum_terminal_trim_kpa", "pressure_reserve_kpa")
  data = {key: number(source[key], key, zero=key in {"cdu_approach_allowance_k", "pressure_reserve_kpa"}) for key in keys}
  if not data["cdu_head_kpa_at_reference_flow"] < data["cdu_shutoff_kpa"] or data["cdu_pump_efficiency"] > 1:
    raise ValueError("TCS pump requires a falling head curve and efficiency at most one.")
  data["fluid"] = {key: number(source["fluid"][key], key)
                   for key in ("density_kg_m3", "cp_kj_kg_k", "viscosity_pa_s")}
  return data


def _rack(spec):
  fields = (("rated_circuit_flow", "kg/s"), ("maximum_circuit_flow", "kg/s"), ("manifold_pair_drop", "kPa"),
            ("quick_disconnect_pair_drop", "kPa"), ("cold_plate_circuit_drop", "kPa"),
            ("maximum_inlet_temperature", "degC"), ("maximum_return_temperature", "degC"))
  return {key: _rated(spec, key, unit) for key, unit in fields}


def _cdu(spec):
  return {"nameplate_kw": _rated(spec, "nameplate_capacity", "kW"), "minimum_approach_k": _rated(spec, "minimum_approach", "K"),
          "electrical_rating_kw": _rated(spec, "electrical_input", "kW")}


def _measure(geometry, materials, rack_data, equipment, cfg, design_flows, ratio):
  fluid = cfg["fluid"]
  flows = {name: flow*ratio for name, flow in design_flows.items()}
  total = sum(flows.values())
  cast = total/(cfg["cdu_reference_flow_l_s"]/1000)
  available = cfg["cdu_shutoff_kpa"]-(cfg["cdu_shutoff_kpa"]-cfg["cdu_head_kpa_at_reference_flow"])*cast**2
  common = cfg["cdu_secondary_drop_kpa_at_reference_flow"]*cast**2
  pipes = {}
  for key, segment in geometry["pipes"].items():
    material = materials[segment["material"]]
    q = sum(flows[rack] for rack in segment["racks"])
    diameter = material["bore_m"]
    loss = pipe_loss(q, diameter=diameter, length=segment["length_m"], roughness=material["roughness_m"],
                     minor_loss=segment["minor_k"], density=fluid["density_kg_m3"], viscosity=fluid["viscosity_pa_s"])/1000
    pipes[key] = {"flow_m3_s": q, "drop_kpa": loss, "velocity_m_s": q/(pi*diameter**2/4),
                  "velocity_limit_m_s": material["maximum_velocity_m_s"],
                  "residence_seconds": segment["fluid_volume_m3"]/q if q else None}
  racks = {}
  for name, spec in rack_data.items():
    path = geometry["paths"]["supply"][name]+geometry["paths"]["return"][name]
    path_drop = sum(pipes[key]["drop_kpa"] for key in path)
    weight = flows[name]*fluid["density_kg_m3"]
    multiplier = (weight/spec["rated_circuit_flow"])**2
    drops = {key: multiplier*spec[key] for key in ("manifold_pair_drop", "quick_disconnect_pair_drop", "cold_plate_circuit_drop")}
    racks[name] = {"flow_m3_s": flows[name], "flow_kg_s": weight, "installed_pipe_kpa": path_drop,
                   "equipment_drop_kpa": sum(drops.values()), "equipment_drop_parts_kpa": drops,
                   "unbalanced_branch_kpa": path_drop+sum(drops.values()), "pipe_path": path}
  critical = max(racks, key=lambda name: racks[name]["unbalanced_branch_kpa"])
  worst = racks[critical]["unbalanced_branch_kpa"]
  required = worst+common+cfg["minimum_terminal_control_kpa"] if ratio else 0.
  pump_motor = required*total/cfg["cdu_pump_efficiency"]
  for rack in racks.values():
    rack["additional_trim_kpa"] = worst-rack["unbalanced_branch_kpa"]
  margins = {
    "pump-head": (available-required-cfg["pressure_reserve_kpa"])/cfg["cdu_shutoff_kpa"],
    "pump-flow": 1-cast,
    "pump-power": 1-pump_motor/equipment["electrical_rating_kw"],
    "pipe-velocity": min(1-row["velocity_m_s"]/row["velocity_limit_m_s"] for row in pipes.values()),
    "rack-flow": min(1-row["flow_kg_s"]/rack_data[name]["maximum_circuit_flow"] for name,row in racks.items()),
    "branch-trim": 1-max(row["additional_trim_kpa"] for row in racks.values())/cfg["maximum_terminal_trim_kpa"],
  }
  return {"total_flow_m3_s": total, "cdu_required_kpa": required, "cdu_available_kpa": available,
          "cdu_hx_loss_kpa": common, "motor_estimate_kw": pump_motor, "hydraulic_pump_kw": required*total,
          "critical_rack": critical, "racks": racks, "pipes": pipes, "normalized_margins": margins}


def _capacity(network, equipment, racks, cfg, nominal_heat):
  rho_cp = cfg["fluid"]["density_kg_m3"]*cfg["fluid"]["cp_kj_kg_k"]
  flow = nominal_heat/(len(racks)*rho_cp*cfg["secondary_rack_rise_k"])
  target = {name: flow for name in racks}

  @lru_cache(maxsize=16)
  def at(ratio):
    return _measure(network, cfg["materials"], racks, equipment, cfg, target, ratio)

  full = at(1.)
  margin = min(full["normalized_margins"].values())
  at_zero = min(at(0.)["normalized_margins"].values()) if margin < 0 else 1.
  if margin >= 0:
    limit = 1.
  elif at_zero <= 0:
    limit = 0.
  else:
    limit = brentq(lambda x: min(at(x)["normalized_margins"].values()), 0., 1., xtol=1e-10)
  return at, {"cdu": equipment, "rack_ratings": racks, "required_duty": full, "hydraulic_flow_fraction": limit,
              "fluid_volume_m3": network["fluid_volume_m3"]}


def _cases(fws, transient):
  nominal_c = transient["settings"]["supply_setpoint_c"]
  normal = fws["cases"]["normal"]
  cases = {case: (record, {}, nominal_c, "A") for case, record in fws["cases"].items()}
  for off in SIDES:
    other, = [side for side in SIDES if side != off]
    cases[f"local-cdu-off:{off}"] = (normal, {}, nominal_c, other)
    for case, scenario in transient["scenarios"].items():
      temperature = {pod: row["comparison_peak_c"] for pod, row in scenario["terminals"].items()}
      cases[f"fws-peak:{case}:local-cdu-off:{off}"] = (fws["cases"][case], temperature, nominal_c, other)
  return cases


def _terminal(pod, model, solvers, fws, temperature, requested_side, lockout, force, cfg, nominal_kw):
  other, = [side for side in SIDES if side != requested_side]
  choices = (requested_side,) if force else (requested_side, other)
  side = next((candidate for candidate in choices if f"{pod}/{model[candidate]['cdu_name']}" not in lockout), None)
  demand_racks = model[requested_side]["rack_ratings"]
  duty = nominal_kw/len(demand_racks)
  if side is None:
    return {"available_side": None, "requested_kw": nominal_kw, "unserved_kw": nominal_kw, "status": "fail",
            "racks": {name: {"flow_m3_s": 0., "capacity_kw": 0., "requested_kw": duty, "pass": False} for name in demand_racks},
            "limiting_checks": ["cdu-unavailable"]}
  row = model[side]
  fluid = fws["fluid"]
  upstream_limit = fws["terminal"]["flow_m3_s"]*fluid["density_kg_m3"]*fluid["cp_kj_kg_k"]*fluid["delta_t_k"]
  ratio = min(1., row["hydraulic_flow_fraction"], row["cdu"]["nameplate_kw"]/nominal_kw, upstream_limit/nominal_kw)
  circulation = solvers[side](ratio)
  inlet = max(cfg["secondary_target_supply_c"], temperature+row["cdu"]["minimum_approach_k"]+cfg["cdu_approach_allowance_k"])
  return_c = inlet+cfg["secondary_rack_rise_k"] if ratio else inlet
  checks = [name for name, margin in row["required_duty"]["normalized_margins"].items() if margin < 0]
  if upstream_limit+1e-7 < nominal_kw:
    checks.append("facility-flow-capacity")
  if row["cdu"]["nameplate_kw"] < nominal_kw:
    checks.append("cdu-nameplate")
  if not fws["terminal"].get("pass", False):
    checks.append("facility-terminal")
  racks = {}
  for name, hydraulic in circulation["racks"].items():
    limit = row["rack_ratings"][name]
    qualified = inlet <= limit["maximum_inlet_temperature"] and return_c <= limit["maximum_return_temperature"]
    delivered = duty*ratio if qualified else 0.
    racks[name] = {"flow_m3_s": hydraulic["flow_m3_s"], "capacity_kw": delivered, "requested_kw": duty,
                   "tcs_supply_c": inlet, "tcs_return_c": return_c, "temperature_qualified": qualified,
                   "pass": qualified and delivered+1e-7 >= duty}
  if not all(rack["temperature_qualified"] for rack in racks.values()):
    checks.append("rack-temperature")
  delivered = sum(value["capacity_kw"] for value in racks.values())
  return {"available_side": side, "requested_kw": nominal_kw, "capacity_kw": delivered,
          "unserved_kw": max(0., nominal_kw-delivered), "facility_inlet_c": temperature, "facility_capacity_kw": upstream_limit,
          "cdu_required_kpa": circulation["cdu_required_kpa"], "cdu_available_kpa": circulation["cdu_available_kpa"],
          "fluid_volume_m3": row["fluid_volume_m3"], "motor_estimate_kw": circulation["motor_estimate_kw"],
          "racks": racks, "limiting_checks": checks, "status": "fail" if checks or delivered+1e-7 < nominal_kw else "pass"}


def build_tcs_study(context):
  path = source_path(context, "tcs-study.json")
  if not path.is_file():
    return missing_input(context, path, "tcs-study")
  source = read_versioned(path)
  if source.get("state") != "assumed" or not source.get("basis"):
    raise ValueError("Specify the assumed TCS duty basis.")
  cfg = _settings(source)
  routing = staged_json(context, ROUTING)
  fws = staged_json(context, FWS)
  transient = staged_json(context, TRANSIENT)
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "tcs-study", {}, design.diagnostics)
  nominal = {pod: row["heat_kw"] for pod, row in fws["cases"]["normal"]["terminals"].items()}
  if set(nominal) != routing["pods"].keys():
    raise ValueError("FWS and TCS must serve the same placed pods.")
  cfg["materials"] = routing["materials"]
  models, solvers = {}, {}
  for pod, sides in routing["pods"].items():
    models[pod], solvers[pod] = {}, {}
    children = design.instances[pod].children
    for side, system in sides.items():
      racks = {name: _rack(children[name]) for name in system["paths"]["supply"]}
      cdu = _cdu(children[system["cdu"]])
      at, record = _capacity(system, cdu, racks, cfg, nominal[pod])
      models[pod][side] = {**record, "cdu_name": system["cdu"]}
      solvers[pod][side] = at
  locked = set(source["unavailable_cdus"])
  if locked - {f"{pod}/{row['cdu_name']}" for pod, sides in models.items() for row in sides.values()}:
    raise ValueError("Lockout must name a placed secondary-side CDU as pod/equipment.")
  cases = {}
  for name, (upstream, temperatures, supply_c, side) in sorted(_cases(fws, transient).items()):
    force = ":local-cdu-off:" in name or name.startswith("local-cdu-off:")
    terminals = {pod: _terminal(pod, model, solvers[pod], {"fluid": upstream["fluid"],
                                                            "terminal": upstream["terminals"][pod]},
                              temperatures.get(pod, supply_c), side, locked, force, cfg, nominal[pod])
                 for pod, model in models.items()}
    cases[name] = {"pods": terminals, "failed_pods": [pod for pod, row in terminals.items() if row["status"] != "pass"]}
  result = model_report(context, "tcs-study", {"basis": source["basis"], "parameters": cfg, "sides": models, "cases": cases,
                                               "routing_digest": file_digest(context.build_path/ROUTING),
                                               "steady_digest": file_digest(context.build_path/FWS),
                                               "transient_digest": file_digest(context.build_path/TRANSIENT)})
  output = context.build_path/"schedules/aidc-tcs-rack-duty.csv"
  output.parent.mkdir(parents=True, exist_ok=True)
  with output.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("case", "pod", "side", "rack", "flow_l_s", "required_kW", "verified_kW", "inlet_degC", "return_degC",
                    "pass", "limiting_checks"))
    for case, record in cases.items():
      for pod, row in record["pods"].items():
        for rack, duty in row["racks"].items():
          table.writerow((case, pod, row["available_side"], rack, f"{duty['flow_m3_s']*1000:.4f}", duty["requested_kw"],
                          f"{duty['capacity_kw']:.4f}", duty.get("tcs_supply_c"), duty.get("tcs_return_c"), duty["pass"],
                          " ".join(row["limiting_checks"])))
  return TargetResult(result.status, (*result.artifacts, artifact(context, "tcs-rack-duty", output)))


def build_tcs_check(context):
  model = staged_json(context, MODEL)
  issues = [{"rule": "AIDC_TCS_DUTY", "status": "fail", "objects": [name, pod, *row["limiting_checks"]],
             "message": f"{name}/{pod}: TCS duty fails {', '.join(row['limiting_checks'])}."}
            for name, case in model["cases"].items() for pod,row in case["pods"].items() if row["status"] != "pass"]
  return model_report(context, "tcs-check", {"issues": issues, "cases_checked": len(model["cases"]),
                                             "study_digest": file_digest(context.build_path/MODEL)}, issues)
