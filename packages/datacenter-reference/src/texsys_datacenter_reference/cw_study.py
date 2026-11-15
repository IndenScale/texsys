"""Check condenser-water heat rejection and pressure at every FWS duty point."""

import csv
from math import pi, sqrt

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_hvac_dynamics.facility_hydraulics import EquipmentLoss
from texsys_hvac_dynamics.hydraulics import pipe_loss, pump_head_m

from .checks import load_design
from .cw_network import MODEL as NETWORK
from .fws_network_study import _G, _rated
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


MODEL = f"{MODELS}/cw-study.json"
FWS = f"{MODELS}/fws-network-study.json"


def _equipment(design, network):
  result = {}
  for plant, row in network["cells"].items():
    assembly = design.instances[plant]
    chiller, cw_in = assembly.specification["boundary"]["cw_in"][0].split(".")
    if (assembly.specification["boundary"]["cw_in"] != [f"{chiller}.cw_in"]
        or assembly.specification["boundary"]["cw_out"] != [f"{chiller}.cw_out"]
        or cw_in != "cw_in"):
      raise ValueError(f"{plant}: the two CW ports must bind the same internal chiller.")
    unit, pump, tower = assembly.children[chiller], design.instances[row["pump"]], design.instances[row["tower"]]
    for equipment, family in ((unit, "water-cooled-chiller"), (pump, "condenser-water-pump"), (tower, "cooling-tower")):
      if equipment.family != family:
        raise ValueError(f"{equipment.name}: expected a {family} SDL instance.")
    result[plant] = {
      "chiller": f"{plant}/{chiller}", "pump": row["pump"], "tower": row["tower"],
      "chiller_flow_m3_s": _rated(unit, "cw_flow_m3h", "m3/h")/3600,
      "pump_flow_m3_s": _rated(pump, "flow_m3h", "m3/h")/3600, "pump_rated_head_m": _rated(pump, "head_m", "m"),
      "pump_rated_kw": _rated(pump, "power_kw", "kW"), "tower_rated_kw": _rated(tower, "heat_rejection_kw", "kW"),
      "tower_approach_c": _rated(tower, "approach_c", "degC"),
    }
  return result


def _settings(source):
  properties = {key: number(source["fluid"][key], key) for key in ("density_kg_m3", "cp_j_kg_k", "viscosity_pa_s")}
  settings = {key: number(source[key], key, zero=key == "pressure_reserve_kpa") for key in (
    "cop", "wet_bulb_c", "tower_capacity_factor", "maximum_cold_c", "maximum_hot_c",
    "condenser_drop_kpa_at_rated_flow", "tower_coil_drop_kpa_at_chiller_rated_flow",
    "pump_shutoff_to_rated_head", "pump_efficiency", "pressure_reserve_kpa")}
  if (settings["tower_capacity_factor"] > 1 or settings["pump_efficiency"] > 1
      or settings["pump_shutoff_to_rated_head"] <= 1 or settings["maximum_hot_c"] <= settings["maximum_cold_c"]):
    raise ValueError("CW settings require valid efficiency, capacity factor, pump curve and temperature limits.")
  return {"fluid": properties, **settings}


def _hydraulics(model, equipment, conditions, plant):
  fluid = conditions["fluid"]
  rho = fluid["density_kg_m3"]
  q = equipment["chiller_flow_m3_s"]
  pipe = model["pipe"]
  losses = {}
  for key in model["cells"][plant]["routes"].values():
    row = model["routes"][key]
    losses[key] = pipe_loss(q, diameter=pipe["inside_m"], length=row["length_m"], roughness=pipe["roughness_m"],
                            minor_loss=row["minor_k"], density=rho, viscosity=fluid["viscosity_pa_s"])/1000
  losses[equipment["chiller"]] = EquipmentLoss(q, conditions["condenser_drop_kpa_at_rated_flow"]).pressure_pa(q)/1000
  losses[equipment["tower"]] = EquipmentLoss(q, conditions["tower_coil_drop_kpa_at_chiller_rated_flow"]).pressure_pa(q)/1000
  pressure = sum(losses.values())
  rated = equipment["pump_rated_head_m"]
  shutoff = rated*conditions["pump_shutoff_to_rated_head"]
  curve = (shutoff-rated)/equipment["pump_flow_m3_s"]**2
  available = pump_head_m(q, shutoff, curve, 1)*rho*_G/1000
  speed = sqrt((pressure*1000/(rho*_G)+curve*q**2)/shutoff)
  power_to_water = pressure*q
  motor_kw = power_to_water/conditions["pump_efficiency"]
  velocity = q/(pi*pipe["inside_m"]**2/4)
  return {
    "flow_m3_s": q, "velocity_m_s": velocity, "loss_by_object_kpa": losses, "required_kpa": pressure,
    "required_head_m": pressure*1000/(rho*_G), "static_head_m": 0., "available_kpa": available,
    "required_speed_ratio": speed, "hydraulic_kw": power_to_water, "estimated_motor_kw": motor_kw,
    "checks": {"velocity": velocity <= pipe["max_velocity_m_s"], "pump-flow": q <= equipment["pump_flow_m3_s"],
               "pump-pressure-reserve": pressure+conditions["pressure_reserve_kpa"] <= available,
               "pump-motor": motor_kw <= equipment["pump_rated_kw"]},
  }


def _duty(evaporator_kw, hydraulic, equipment, conditions):
  compressor = evaporator_kw/conditions["cop"]
  reject = evaporator_kw + compressor + hydraulic["hydraulic_kw"]
  fluid = conditions["fluid"]
  rise = reject*1000/(hydraulic["flow_m3_s"]*fluid["density_kg_m3"]*fluid["cp_j_kg_k"])
  cold = conditions["wet_bulb_c"] + equipment["tower_approach_c"]
  hot = cold+rise
  capacity = equipment["tower_rated_kw"]*conditions["tower_capacity_factor"]
  return {"evaporator_kw": evaporator_kw, "compressor_kw": compressor, "rejection_kw": reject,
          "available_rejection_kw": capacity, "cold_c": cold, "hot_c": hot, "range_k": rise,
          "checks": {"tower-capacity": reject <= capacity, "cold-water-temperature": cold <= conditions["maximum_cold_c"],
                     "hot-water-temperature": hot <= conditions["maximum_hot_c"], **hydraulic["checks"]}}


def _cases(fws, equipment, hydraulic, conditions, locked):
  cases = {name: (name, set()) for name in fws["cases"]}
  for plant, machines in equipment.items():
    for kind in ("pump", "tower"):
      name = machines[kind]
      cases[f"cw-{kind}-off:{name}"] = (f"plant-off:{plant}", {name})
  results = {}
  for name, (dispatch, off) in sorted(cases.items()):
    row = fws["cases"][dispatch]
    duty = {}
    for plant, entry in row["duty"].items():
      item = equipment[plant]
      duty[plant] = _duty(entry["heat_kw"], hydraulic[plant], item, conditions)
      duty[plant]["checks"]["equipment-available"] = not ({item["pump"], item["tower"]} & (locked | off))
    failures = [[plant, check] for plant, cell in duty.items() for check, passed in cell["checks"].items() if not passed]
    results[name] = {"fws_dispatch": dispatch, "fws_status": row["status"], "unavailable": sorted(locked | off),
                     "requested_fws_kw": row["requested_heat_kw"], "duty": duty, "failed_checks": failures,
                     "status": "pass" if row["status"] == "pass" and not failures else "fail"}
  return results


def build_cw_study(context):
  path = source_path(context, "cw-study.json")
  if not path.is_file():
    return missing_input(context, path, "cw-study")
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "cw-study", {}, design.diagnostics)
  source = read_versioned(path)
  if source.get("state") != "assumed" or not isinstance(source.get("basis"), str) or not source["basis"].strip():
    raise ValueError("CW design requires an explicit assumed engineering basis.")
  conditions = _settings(source)
  network, fws = staged_json(context, NETWORK), staged_json(context, FWS)
  equipment = _equipment(design, network)
  if equipment.keys() != fws["plants"].keys():
    raise ValueError("CW and facility-water must serve the same chiller assemblies.")
  locked = set(source["unavailable"])
  if locked - {row[kind] for row in equipment.values() for kind in ("pump", "tower")}:
    raise ValueError("Unavailable CW equipment must identify a pump or tower in a declared cell.")
  hydraulic = {name: _hydraulics(network, row, conditions, name) for name, row in equipment.items()}
  cases = _cases(fws, equipment, hydraulic, conditions, locked)
  report = model_report(context, "cw-study", {
    "basis": source["basis"], "parameters": conditions, "equipment": equipment, "hydraulics": hydraulic, "cases": cases,
    "route_digest": file_digest(context.build_path / NETWORK), "fws_dispatch_digest": file_digest(context.build_path / FWS),
    "engineering_result": "pass" if all(row["status"] == "pass" for row in cases.values()) else "fail",
  })
  schedule = context.build_path / "schedules/aidc-cw-duty.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("case", "fws_dispatch", "result", "cell", "flow_m3h", "evaporator_kw", "reject_kw", "reserve_kw",
                    "cold_c", "hot_c", "required_kpa", "pump_available_kpa", "motor_kw", "failed_checks"))
    for name, case in cases.items():
      for plant, row in case["duty"].items():
        demand = hydraulic[plant]
        values = (demand["flow_m3_s"]*3600, row["evaporator_kw"], row["rejection_kw"],
                  row["available_rejection_kw"]-row["rejection_kw"], row["cold_c"], row["hot_c"],
                  demand["required_kpa"], demand["available_kpa"], demand["estimated_motor_kw"])
        table.writerow((name, case["fws_dispatch"], case["status"], plant, *(f"{value:.3f}" for value in values),
                        " ".join(check for check, passed in row["checks"].items() if not passed)))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "cw-duty", schedule)))


def build_cw_check(context):
  study = staged_json(context, MODEL)
  issues = []
  for name, row in study["cases"].items():
    if row["fws_status"] != "pass":
      issues.append({"rule": "AIDC_CW_FWS_UNSERVED", "status": "fail", "objects": [name, row["fws_dispatch"]],
                     "message": f"{name}: facility-water dispatch did not meet the design duty."})
    for plant, check in row["failed_checks"]:
      issues.append({"rule": "AIDC_CW_DUTY", "status": "fail", "objects": [name, plant, check],
                     "message": f"{name}: {plant} failed {check} at the computed design point."})
  return model_report(context, "cw-check", {"study_digest": file_digest(context.build_path / MODEL),
                                           "case_count": len(study["cases"]), "issues": issues}, issues)
