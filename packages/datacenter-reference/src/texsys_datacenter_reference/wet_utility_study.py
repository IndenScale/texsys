"""Check evaporative demand, pressure, gravity discharge and interruption storage."""

import csv

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_hvac_dynamics.hydraulics import pipe_loss
from texsys_hvac_dynamics.tower_water import TowerWater

from .cw_study import MODEL as CW
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json
from .wet_utilities import MODEL as NETWORK


MODEL = f"{MODELS}/wet-utility-study.json"
G = 9.80665


def _settings(cfg):
  keys = ("density_kg_m3", "cp_kj_kg_k", "viscosity_pa_s", "latent_heat_kj_kg", "wet_fraction", "latent_fraction",
          "spray_range_k", "drift_fraction", "cycles_of_concentration", "basin_tds_limit_mg_l", "city_tds_mg_l",
          "city_withdrawal_m3_h", "city_available_kpa", "fill_device_reserve_kpa", "booster_shutoff_kpa",
          "booster_rated_flow_m3_h", "booster_rated_kpa", "tower_inlet_required_kpa", "permitted_discharge_m3_h",
          "discharge_tds_limit_mg_l", "required_city_outage_hours", "maximum_refill_hours")
  result = {name: number(cfg[name], name, zero=name in {"drift_fraction", "city_tds_mg_l"}) for name in keys}
  if result["booster_rated_kpa"] >= result["booster_shutoff_kpa"]:
    raise ValueError("Booster rated differential must be below shutoff pressure.")
  return result


def _drop(run, flow, cfg):
  pipe = run["pipe"]
  return pipe_loss(flow/3600, diameter=pipe["inside_m"], length=run["length_m"], roughness=pipe["roughness_m"],
                   minor_loss=run["elbows"]*pipe["elbow_k"], density=cfg["density_kg_m3"],
                   viscosity=cfg["viscosity_pa_s"])/1000


def _static(height, cfg):
  return cfg["density_kg_m3"]*G*height/1000


def _paths(network):
  routes = network["runs"]
  paths = {}
  for service, prefix, node_key in (("makeup", "MU-", "to_node"), ("drain", "DR-", "from_node")):
    if service == "makeup":
      edges = {row[node_key]: (key, row["from_node"]) for key, row in routes.items() if row["service"] == service}
    else:
      edges = {row[node_key]: (key, row["to_node"]) for key, row in routes.items() if "slope" in row}
    paths[service] = {}
    for tower in network["towers"]:
      key = prefix+tower
      path = [key]
      node = routes[key]["from_node" if service == "makeup" else "to_node"]
      traversed = set()
      while node in edges:
        if node in traversed:
          raise ValueError(f"{tower}: cycle in {service} utility tree")
        traversed.add(node)
        run, node = edges[node]
        path.append(run)
      if node != (network["tank"]["id"] if service == "makeup" else network["outfall"]["id"]):
        raise ValueError(f"{tower}: {service} does not reach its declared boundary.")
      paths[service][tower] = path
  return paths


def _flows(paths, balance, field):
  flows = {}
  for tower, route in paths.items():
    for key in route:
      flows[key] = flows.get(key, 0.)+balance.get(tower, {}).get(field, 0.)
  return flows


def _case(cw_case, equipment, network, cfg, tower_model, paths):
  towers = {equipment[plant]["tower"]: tower_model.balance(row["rejection_kw"])
            for plant, row in cw_case["duty"].items()}
  makeup = sum(row["makeup_m3_h"] for row in towers.values())
  blowdown = sum(row["blowdown_m3_h"] for row in towers.values())
  evaporation = sum(row["evaporation_m3_h"] for row in towers.values())
  drift = sum(row["drift_m3_h"] for row in towers.values())
  attainable_cycles = 1+evaporation/(blowdown+drift) if blowdown+drift else None
  # TowerWater accepts heat-rejection multipliers. Here the input is already the
  # condenser duty, so the factor is unity and its first field denotes that duty.
  for row in towers.values():
    row.pop("cooling_kw")
  supplied = _flows(paths["makeup"], towers, "makeup_m3_h")
  discharge = _flows(paths["drain"], towers, "blowdown_m3_h")
  losses = {key: _drop(network["runs"][key], flow, cfg) for key, flow in supplied.items()}
  pressures = {}
  available = cfg["booster_shutoff_kpa"] - (cfg["booster_shutoff_kpa"]-cfg["booster_rated_kpa"])*(
    makeup/cfg["booster_rated_flow_m3_h"])**2
  cities = network["runs"]["MU-CITY"]
  city_required = (_static(cities["points_m"][-1][2]-cities["points_m"][0][2], cfg)
                   + _drop(cities, cfg["city_withdrawal_m3_h"], cfg)+cfg["fill_device_reserve_kpa"])
  for tower, path in paths["makeup"].items():
    if tower in towers:
      lift = network["runs"][path[0]]["points_m"][-1][2]-network["tank"]["water_elevation_m"][0]
      pressures[tower] = available-_static(lift, cfg)-sum(losses[key] for key in path)
  limits = {key: network["runs"][key]["capacity_at_half_depth_m3_h"] for key in discharge}
  held_volume = makeup*cfg["required_city_outage_hours"]
  remaining = network["tank"]["usable_volume_m3"]-held_volume
  recharge = cfg["city_withdrawal_m3_h"]-makeup
  refill = held_volume/recharge if recharge > 0 else None
  tds = cfg["city_tds_mg_l"]*attainable_cycles if attainable_cycles is not None else 0.
  checks = {
    "upstream-cw-case": cw_case["status"] == "pass",
    "municipal-inlet-pressure": city_required <= cfg["city_available_kpa"],
    "makeup-withdrawal": makeup <= cfg["city_withdrawal_m3_h"],
    "booster-rated-flow": makeup <= cfg["booster_rated_flow_m3_h"],
    "booster-residual": all(value >= cfg["tower_inlet_required_kpa"] for value in pressures.values()),
    "gravity-discharge": all(flow <= limits[key] for key, flow in discharge.items()),
    "approved-discharge-flow": blowdown <= cfg["permitted_discharge_m3_h"],
    "source-tds": cfg["city_tds_mg_l"] <= tower_model.makeup_tds_limit_mg_l,
    "basin-tds": tds <= cfg["basin_tds_limit_mg_l"],
    "discharge-tds": tds <= cfg["discharge_tds_limit_mg_l"],
    "bounded-city-outage": remaining >= 0,
    "outage-recovery": refill is not None and refill <= cfg["maximum_refill_hours"],
  }
  return {"towers": towers, "makeup_m3_h": makeup, "blowdown_m3_h": blowdown, "basin_tds_mg_l": tds,
          "achievable_cycles": attainable_cycles, "city_refill_required_kpa": city_required,
          "tower_makeup_residual_kpa": pressures, "booster_available_kpa": available, "pipe_loss_kpa": losses,
          "makeup_flow_by_run_m3_h": supplied, "discharge_flow_by_run_m3_h": discharge,
          "gravity_capacity_m3_h": limits, "outage_reserve_required_m3": held_volume,
          "outage_end_remaining_m3": remaining, "refill_hours": refill, "checks": checks,
          "status": "pass" if all(checks.values()) else "fail"}


def build_wet_study(context):
  path = source_path(context, "wet-utility-study.json")
  if not path.is_file():
    return missing_input(context, path, "wet-utility-study")
  source = read_versioned(path)
  if source.get("state") != "assumed" or not source.get("basis"):
    raise ValueError("Makeup and drainage study requires an explicit assumed basis.")
  cfg = _settings(source)
  model = TowerWater(1., cfg["wet_fraction"], cfg["latent_fraction"], cfg["density_kg_m3"], cfg["latent_heat_kj_kg"],
                     cfg["cp_kj_kg_k"], cfg["spray_range_k"], cfg["drift_fraction"],
                     cfg["cycles_of_concentration"], cfg["basin_tds_limit_mg_l"])
  cw = staged_json(context, CW)
  network = staged_json(context, NETWORK)
  paths = _paths(network)
  cases = {key: _case(record, cw["equipment"], network, cfg, model, paths) for key, record in cw["cases"].items()}
  report = model_report(context, "wet-utility-study", {
    "basis": source["basis"], "parameters": cfg, "tank_usable_volume_m3": network["tank"]["usable_volume_m3"],
    "geometry_digest": file_digest(context.build_path/NETWORK), "cooling_duty_digest": file_digest(context.build_path/CW),
    "cases": cases, "engineering_result": "pass" if all(row["status"] == "pass" for row in cases.values()) else "fail",
  })
  schedule = context.build_path/"schedules/aidc-wet-utility-duty.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    fields = ("makeup_m3_h", "blowdown_m3_h", "basin_tds_mg_l", "city_refill_required_kpa",
              "outage_reserve_required_m3", "outage_end_remaining_m3", "refill_hours")
    table.writerow(("case", *fields, "min_tower_makeup_kpa", "result", "failed_checks"))
    for key, row in cases.items():
      values = [row[name] for name in fields]+[min(row["tower_makeup_residual_kpa"].values(), default=None)]
      failed = [check for check, passes in row["checks"].items() if not passes]
      table.writerow((key, *("" if value is None else f"{value:.4f}" for value in values), row["status"], " ".join(failed)))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "wet-utility-duty", schedule)))


def build_wet_check(context):
  study = staged_json(context, MODEL)
  failures = [{"rule": "AIDC_WET_UTILITY_DUTY", "status": "fail", "objects": [case, check],
               "message": f"{case}: actual wet-utility duty fails {check}."}
              for case, row in study["cases"].items() for check, passed in row["checks"].items() if not passed]
  return model_report(context, "wet-utility-check", {"case_count": len(study["cases"]),
                                                      "study_digest": file_digest(context.build_path/MODEL),
                                                      "issues": failures}, failures)
