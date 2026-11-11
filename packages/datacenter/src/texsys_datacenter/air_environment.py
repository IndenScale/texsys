"""Bind rack air distribution and environmental criteria to the repeated room."""

from dataclasses import replace
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_hvac_dynamics.room_airflow import RackAirflow, SharedAirBranch, SupplyAirflow, room_airflow
from texsys_structure.openings import PlanRect

from .unit import _identifiers, _number


def _layout(config, floor, building):
  cold = PlanRect(*config["cold_aisle_xyxy_m"])
  minimum = _number(config["minimum_aisle_width_m"], "minimum_aisle_width_m")
  height = _number(config["rack_height_m"], "rack_height_m")
  if height > building["unit_height_m"]:
    raise ValueError("The rack envelope exceeds the repeated room height.")
  rack_layout, conflicts = {}, []
  if config["rack_fronts"].keys() != floor["rack_loads"].keys():
    raise ValueError("Every placed rack needs a front orientation.")
  for name, item in floor["rack_loads"].items():
    x, y = item["origin_m"]
    w, d = item["size_m"]
    rect = PlanRect(x, y, x + w, y + d)
    direction = config["rack_fronts"][name]
    if direction not in ("+Y", "-Y"):
      raise ValueError("Rack fronts must face one of the two cold-aisle sides.")
    face, edge = (rect.top, cold.bottom) if direction == "+Y" else (rect.bottom, cold.top)
    if abs(face - edge) > 1e-8 or rect.left < cold.left or rect.right > cold.right or rect.overlaps(cold):
      conflicts.append({"rack": name, "check": "front-to-cold-aisle"})
    rack_layout[name] = {"bounds_m": rect.xyxy, "height_m": height, "front": direction,
                         "inlet_point_m": ((rect.left + rect.right) / 2, face, height / 2)}
  if cold.top - cold.bottom + 1e-8 < minimum:
    conflicts.append({"check": "aisle-width", "actual_m": cold.top - cold.bottom, "minimum_m": minimum})
  offset_x, offset_y = floor["origin_m"]
  unit_cold = PlanRect(cold.left + offset_x, cold.bottom + offset_y, cold.right + offset_x, cold.top + offset_y)
  if any(not PlanRect(0, 0, *zone["size_m"]).contains(unit_cold)
         for zone in building["floor_zones"].values() if zone["role"] == "unit"):
    conflicts.append({"check": "cold-aisle-inside-unit"})
  return {"cold_aisle_m": cold.xyxy, "racks": rack_layout, "bay_offset_m": floor["origin_m"]}, conflicts


def _supplies(config, unit, distribution):
  if config.keys() != unit["air_units"].keys():
    raise ValueError("Air distribution must cover each SDL precision air unit.")
  result = {}
  for name, data in config.items():
    unit_air = unit["air_units"][name]
    if unit_air["air_ports"].get(data["supply_port"]) != "out" or unit_air["air_ports"].get(data["return_port"]) != "in":
      raise ValueError(f"Supply and return must bind the SDL air ports of {name}.")
    field = "terminal_loss_coefficient" if distribution else "branch_resistance"
    if data[field].keys() != unit["rack_heat_kw"].keys():
      raise ValueError(f"Each precision air unit needs a branch to every rack: {name}.")
    conversion, shared, common = 1., (), 0.
    if distribution:
      if "branch_resistance" in data:
        raise ValueError("Routed air distribution derives branch resistance from geometry and terminal loss.")
      fan = distribution["fans"][name]
      conversion = unit["room_air"]["density_kg_m3"] / (2 * distribution["terminal_opening_m2"]**2)
      shared = tuple(SharedAirBranch(group, tuple(item["racks"]), item["resistance"])
                     for group, item in fan["groups"].items())
      common = fan["common_resistance"]
    result[name] = SupplyAirflow(
      unit_air["flow_m3_s"], unit_air["capacity_kw"], _number(data["static_limit_pa"], "static_limit_pa"),
      {rack: conversion * _number(value, field) for rack, value in data[field].items()},
      _number(data["bypass_conductance"], "bypass_conductance", inclusive=True), common, shared,
    )
  return result


def _profile(unit, upstream, racks, supplies, limits, changes):
  valid = {"unavailable_air_units", "blocked_branches", "minimum_recirculation", "dew_point_c"}
  if changes.keys() - valid:
    raise ValueError("Unsupported local room air change.")
  air = unit["room_air"]
  stopped = _identifiers(changes.get("unavailable_air_units", []), "unavailable_air_units")
  blocked = {name: _identifiers(values, f"{name}.blocked_branches")
             for name, values in changes.get("blocked_branches", {}).items()}
  leaks = changes.get("minimum_recirculation", {})
  if stopped - supplies.keys() or blocked.keys() - supplies.keys() or leaks.keys() - racks.keys() or any(
    keys - racks.keys() for keys in blocked.values()
  ):
    raise ValueError("Local air changes must refer to declared rack and precision air identities.")
  active = {name: replace(supplies[name], branch_resistance={rack: loss for rack, loss in supplies[name].branch_resistance.items()
                                                           if rack not in blocked.get(name, ())})
            for name in upstream["active_air_units"] if name not in stopped}
  powered = upstream["power"]["paths"]
  loads = {name: replace(rack, sensible_kw=rack.sensible_kw if name in powered else 0.,
                         minimum_recirculation=leaks.get(name, rack.minimum_recirculation))
           for name, rack in racks.items()}
  dew_point = changes.get("dew_point_c", air["dew_point_c"])
  result = room_airflow(
    loads, active, density_kg_m3=air["density_kg_m3"], cp_kj_kgk=air["specific_heat_kj_kgk"],
    supply_c=air["supply_c"], other_room_kw=unit["heat_budget_kw"]["other_room"], dew_point_c=dew_point,
  )
  failures = {}
  for name, rack in result["racks"].items():
    inlet, humidity = rack["inlet_c"], rack["inlet_relative_humidity"]
    checks = {
      "inlet-temperature": inlet is not None and limits["inlet_min_c"] <= inlet <= limits["inlet_max_c"],
      "relative-humidity": humidity is not None and limits["rh_min"] <= humidity <= limits["rh_max"],
    }
    failures[name] = [key for key, passed in checks.items() if not passed]
  margin = air["min_surface_c"] - dew_point
  return_c = result["mixed_return_c"]
  checks = {
    "supply-setpoint": result["maintains_supply_setpoint"],
    "mixed-return": return_c is not None and return_c <= air["max_return_c"],
    "rack-environment": not any(failures.values()),
    "dew-point": dew_point <= limits["max_dew_point_c"],
    "condensation-margin": margin >= limits["min_condensation_margin_c"],
  }
  if result["maintains_supply_setpoint"]:
    checks["sensible-energy-balance"] = max(abs(result[key]) for key in ("energy_residual_kw", "hot_pool_residual_kw")) <= 1e-7
  return {**result, "checks": checks, "status": "pass" if all(checks.values()) else "fail", "dew_point_c": dew_point,
          "surface_margin_c": margin, "rack_failures": {name: items for name, items in failures.items() if items},
          "local_changes": changes}


def evaluate_air_environment(source: Path, unit: dict, floor: dict, building: dict, distribution=None):
  config = _load_json((source / "air-environment.study.json").read_text(encoding="utf-8"))
  if type(config["schema_version"]) is not int or config["schema_version"] != 1 or config["state"] != "assumed" or not config["basis"].strip():
    raise ValueError("Air environment needs schema version 1 and an assumed basis.")
  layout, conflicts = _layout(config["layout"], floor, building)
  if layout["racks"].keys() != unit["rack_heat_kw"].keys():
    raise ValueError("Rack air loads and physical rack placements must share their identities.")
  limits = {key: _number(config["limits"][key], key, inclusive=True) for key in
            ("inlet_min_c", "inlet_max_c", "rh_min", "rh_max", "max_dew_point_c", "min_condensation_margin_c")}
  if not (limits["inlet_min_c"] < limits["inlet_max_c"] and 0 <= limits["rh_min"] < limits["rh_max"] <= 1):
    raise ValueError("Air environment limits need ordered inlet temperature and humidity ranges.")
  rack_flow = _number(config["rack_airflow_m3_s"], "rack_airflow_m3_s")
  recirculation = _number(config["minimum_recirculation"], "minimum_recirculation", inclusive=True)
  racks = {name: RackAirflow(rack_flow, load["air"], recirculation) for name, load in unit["rack_heat_kw"].items()}
  supplies = _supplies(config["supplies"], unit, distribution)
  zones = {name: zone for name, zone in building["floor_zones"].items() if zone["role"] == "unit"}
  cases = {}
  for name, case in config["cases"].items():
    upstream = building["scenarios"][case["building_scenario"]]
    phase = building["phases"][upstream["phase"]]
    states = {f"{level}/{room}": "typical" for level in phase["levels"] for room in zones}
    if not states or case["expected"] not in ("pass", "fail"):
      raise ValueError(f"Invalid air environment case {name}.")
    local = case.get("local_changes", {})
    if local.keys() - states.keys():
      raise ValueError(f"Air environment case {name} has changes outside its active phase.")
    baseline = unit["scenarios"][upstream["unit_scenario"]]
    profiles = {"typical": _profile(unit, baseline, racks, supplies, limits, {})}
    for identity, changes in local.items():
      profiles[identity] = _profile(unit, baseline, racks, supplies, limits, changes)
      states[identity] = identity
    if distribution:
      for profile in profiles.values():
        for fan, result in profile["fans"].items():
          physical = distribution["fans"][fan]
          flows = {key: result["flow_m3_s"] for key in physical["main"]}
          for group in physical["groups"].values():
            flow = sum(result["branch_flow_m3_s"].get(rack, 0.) for rack in group["racks"])
            flows.update(dict.fromkeys(group["routes"], flow))
          result["duct_velocity_m_s"] = {key: flow / physical["routes"][key]["area_m2"] for key, flow in flows.items()}
    failures = {identity: [mode for mode, passed in profiles[key]["checks"].items() if not passed]
                for identity, key in states.items() if profiles[key]["status"] == "fail"}
    samples = [(rack["inlet_c"], identity, rack_name)
               for identity, key in states.items() for rack_name, rack in profiles[key]["racks"].items()
               if rack["inlet_c"] is not None]
    peak, room, rack = max(samples, default=(None, None, None))
    unresolved = [f"{identity}/{name}" for identity, key in states.items() for name, rack in profiles[key]["racks"].items()
                  if rack["inlet_c"] is None]
    cases[name] = {"expected": case["expected"], "status": "pass" if not failures else "fail",
                   "building_scenario": case["building_scenario"], "unit_profiles": states, "profiles": profiles,
                   "failed_units": failures, "worst_inlet": {"c": peak, "unit": room, "rack": rack},
                   "unresolved_rack_inlets": unresolved,
                   "units": len(states), "racks": len(states) * len(racks)}
  if config["layout_case"] not in cases:
    raise ValueError("Air environment layout needs a modeled case.")
  design_cases = _identifiers(config["design_cases"], "design_cases")
  if not design_cases or design_cases - cases.keys():
    raise ValueError("Air environment design screening needs modeled case identities.")
  failed_design = sorted(name for name in design_cases if cases[name]["status"] != "pass")
  return {
    "schema_version": 1, "state": "assumed", "formal_review": "pending", "basis": config["basis"], "frame": building["frame"],
    "status": "pass" if not conflicts and all(case["status"] == case["expected"] for case in cases.values()) else "fail",
    "design_screening": {"status": "revision-required" if conflicts or failed_design else "pass",
                         "cases": sorted(design_cases), "failed_cases": failed_design},
    "layout_case": config["layout_case"], "limits": limits, "layout": layout,
    "air_distribution_basis": "routed-ducts-and-open-hot-room-return" if distribution else "declared-branch-resistances",
    "plan_conflicts": conflicts, "cases": cases, "levels_m": building["levels_m"], "unit_origins_m": {
      name: zone["origin_m"] for name, zone in zones.items()
    },
    "pending": ["fan-curves-and-commissioned-balancing", "vertical-inlet-gradient-and-cfd",
                "air-leakage-measurement-and-latent-loads", "duct-fittings-acoustics-and-shop-details"],
  }


def write_air_environment_usd(report, path):
  from pxr import Gf, Sdf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/AirEnvironment").GetPrim())

  def box(parent, name, bounds, height, color):
    x, y, xx, yy, z = bounds
    cube = UsdGeom.Cube.Define(stage, f"{parent}/{_prim_name(name)}")
    cube.CreateSizeAttr(1)
    cube.AddTranslateOp().Set(Gf.Vec3d((x + xx) / 2, (y + yy) / 2, z + height / 2))
    cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(xx - x, yy - y, height))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    return cube.GetPrim()

  case = report["cases"][report["layout_case"]]
  for identity, profile_id in case["unit_profiles"].items():
    level, unit = identity.split("/", 1)
    origin_x, origin_y = report["unit_origins_m"][unit]
    origin_x += report["layout"]["bay_offset_m"][0]
    origin_y += report["layout"]["bay_offset_m"][1]
    z = report["levels_m"][level]
    parent = f"/AirEnvironment/{_prim_name(identity)}"
    UsdGeom.Scope.Define(stage, parent)

    def at(bounds):
      a, b, c, d = bounds
      return origin_x + a, origin_y + b, origin_x + c, origin_y + d, z

    box(parent, "ColdAisle", at(report["layout"]["cold_aisle_m"]), .025, (.35, .67, .75))
    profile = case["profiles"][profile_id]
    for name, rack in report["layout"]["racks"].items():
      color = (.80, .23, .16) if name in profile["rack_failures"] else (.20, .45, .62)
      prim = box(parent, name, at(rack["bounds_m"]), rack["height_m"], color)
      prim.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(f"{identity}/{name}")
      inlet = profile["racks"][name]["inlet_c"]
      if inlet is not None:
        prim.SetCustomDataByKey("inletC", inlet)
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
