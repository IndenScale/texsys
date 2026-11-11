"""Repeat a verified concept unit and check shared supplies and spaces."""

from itertools import combinations, product
import math
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_spatial import DatumPlane

from .architecture import compile_architecture, read_architecture
from .floor import _overlap, _rectangle
from .fws import BuildingWater
from .unit import _identifiers, _number


def _floor_zones(layout: dict, bay: list[float]) -> dict:
  bounds, size, pitch, start = (layout[name] for name in ("floor_m", "unit_m", "pitch_m", "origin_m"))
  if len(bounds) != 2 or len(size) != 2 or len(pitch) != 2 or len(start) != 2:
    raise ValueError("Building floor layout dimensions must be two-dimensional.")
  if any(required > declared for required, declared in zip(bay, size, strict=True)):
    raise ValueError("The structural bay does not fit the repeated unit zone.")
  columns, rows = layout["count"]
  if any(type(value) is not int or not 1 <= value <= 50 for value in (columns, rows)):
    raise ValueError("Building unit rows and columns must be integers from 1 to 50.")
  zones = {}
  for row, column in product(range(rows), range(columns)):
    identity = f"Unit-{row + 1:02d}-{column + 1:02d}"
    origin = (start[0] + column * pitch[0], start[1] + row * pitch[1])
    zones[identity] = {**_rectangle(origin, size, bounds, identity), "role": "unit"}
  for identity, reserve in layout["reserves"].items():
    if identity in zones or not isinstance(reserve["role"], str) or reserve["role"] in {"", "unit"}:
      raise ValueError(f"Invalid building reserve {identity}.")
    zones[identity] = {**_rectangle(reserve["origin_m"], reserve["size_m"], bounds, identity), "role": reserve["role"]}
  for first, second in combinations(zones, 2):
    if _overlap(zones[first], zones[second]):
      raise ValueError(f"Building zones overlap: {first}, {second}.")
  return zones


def evaluate_building(source: Path, unit: dict, floor: dict, *, root=None) -> dict:
  config = _load_json((source / "building.study.json").read_text(encoding="utf-8"))
  if config.get("schema_version") != 1 or config.get("state") != "assumed":
    raise ValueError("Building study requires assumed schema_version 1 input.")
  design_it = _number(config["design_it_kw"], "design_it_kw")
  zones = _floor_zones(config["layout"], floor["bay_m"])
  frame = config["frame"]
  levels = {name: DatumPlane(frame, name, elevation).elevation_m for name, elevation in config["levels_m"].items()}
  if not levels or len(set(levels.values())) != len(levels):
    raise ValueError("Building study needs distinct level elevations.")
  unit_height = _number(config["unit_height_m"], "unit_height_m")
  ordered = sorted(levels.values())
  if any(upper - lower < unit_height for lower, upper in zip(ordered, ordered[1:])):
    raise ValueError("Building levels overlap in elevation.")
  architecture = read_architecture(source)
  if architecture is not None:
    architecture = compile_architecture(root, source, architecture, frame, zones, levels,
                                        config["layout"]["floor_m"], unit_height)

  feeds, rejection = config["shared_power"], config["shared_heat_rejection"]
  nominal = unit["scenarios"][config["nominal_unit_scenario"]]
  supply_ids = set(unit["power_sources"])
  mapped = [feed["unit_source"] for feed in feeds.values()]
  if set(mapped) != supply_ids or len(mapped) != len(supply_ids):
    raise ValueError("Shared power must map each unit source exactly once.")
  plant_ids = set(nominal["liquid_available_by_plant_kw"])
  local_plants = [plant for group in rejection.values() for plant in group["unit_plants"]]
  if set(local_plants) != plant_ids or len(local_plants) != len(plant_ids):
    raise ValueError("Shared heat rejection must map each unit plant exactly once.")
  if any(group["power_feed"] not in feeds for group in rejection.values()):
    raise ValueError("Heat rejection needs a declared shared power feed.")

  fws_file = source / "fws.study.json"
  water = BuildingWater(fws_file, rejection, zones, levels, unit) if fws_file.is_file() else None
  units = [name for name, zone in zones.items() if zone["role"] == "unit"]
  phases, results = {}, {}
  for phase, definition in sorted(config["phases"].items()):
    included = _identifiers(definition["levels"], f"{phase}.levels")
    if not included or included - levels.keys():
      raise ValueError(f"Phase {phase} needs known levels.")
    count = len(included) * len(units)
    it = count * unit["heat_budget_kw"]["it"]
    if not math.isclose(it, _number(definition["it_kw"], f"{phase}.it_kw")):
      raise ValueError(f"Phase {phase} allocated IT power differs from the declared total.")
    heat = {name: value * count for name, value in unit["heat_budget_kw"].items()}
    phases[phase] = {"levels": sorted(included), "unit_count": count, "it_kw": it, "heat_budget_kw": heat}
  full = phases[config["full_phase"]]
  if set(full["levels"]) != set(levels) or not math.isclose(full["it_kw"], design_it):
    raise ValueError("Full phase must include all levels and match the design IT target.")

  for name, case in sorted(config["scenarios"].items()):
    phase = phases[case["phase"]]
    count = phase["unit_count"]
    local = unit["scenarios"][case["unit_scenario"]]
    lost_power = _identifiers(case["unavailable_power"], f"{name}.unavailable_power")
    lost_heat = _identifiers(case["unavailable_heat_rejection"], f"{name}.unavailable_heat_rejection")
    if lost_power - feeds.keys() or lost_heat - rejection.keys() or case["expected"] not in ("pass", "fail"):
      raise ValueError(f"Invalid shared system scenario {name}.")
    shared_power = {}
    for identity, feed in feeds.items():
      capacity = _number(feed["capacity_kw"], f"{identity}.capacity_kw")
      shared_power[identity] = {
        "demand_kw": count * local["power"]["flow_kw"][feed["unit_source"]],
        "available_kw": 0 if identity in lost_power else capacity,
      }
    shared_heat, source_kw, terminal_kw = {}, {}, {}
    for identity, group in rejection.items():
      capacity = _number(group["capacity_kw"], f"{identity}.capacity_kw")
      enabled = identity not in lost_heat and group["power_feed"] not in lost_power
      local_kw = sum(local["liquid_available_by_plant_kw"][plant] for plant in group["unit_plants"])
      source_kw[identity] = capacity if enabled else 0.0
      terminal_kw[identity] = len(units) * local_kw
      shared_heat[identity] = min(source_kw[identity], count * local_kw)
    checks = {
      "unit-scenario": local["status"] == "pass",
      "shared-power": all(feed["demand_kw"] <= feed["available_kw"] + 1e-8 for feed in shared_power.values()),
      "shared-liquid-heat-rejection": sum(shared_heat.values()) + 1e-8 >= phase["heat_budget_kw"]["liquid_it"],
    }
    water_report = {}
    if water is not None:
      demand = dict.fromkeys(phase["levels"], len(units) * unit["heat_budget_kw"]["liquid_it"])
      water_report = water.evaluate(case, demand, source_kw, terminal_kw)
      shortages = water_report["shortage_kw_by_terminal"]
      water_report["units_at_risk"] = (sorted(water_report["shortage_kw_by_unit"])
                                       if "shortage_kw_by_unit" in water_report else
                                       [f"{level}/{identity}" for level in sorted(shortages) for identity in units])
      checks["shared-liquid-heat-rejection"] = not shortages
      checks["leak-isolation"] = all(action["isolation_confirmed"] for action in water_report["interlocks"].values())
    elif any(key in case for key in ("unavailable_fws_segments", "unavailable_risers", "leak_zones",
                                     "unavailable_detectors", "stuck_open_valves", "unavailable_fws_pumps",
                                     "fws_pump_speed_limits", "unavailable_fws_branch_segments")):
      raise ValueError(f"Scenario {name} requires fws.study.json.")
    results[name] = {"phase": case["phase"], "unit_scenario": case["unit_scenario"],
                     "status": "pass" if all(checks.values()) else "fail",
                     "expected": case["expected"], "checks": checks, "shared_power": shared_power,
                     "shared_liquid_available_kw": shared_heat}
    if water is not None:
      results[name]["fws"] = water_report
      results[name]["fws_source_kw"] = source_kw
      results[name]["fws_terminal_kw"] = terminal_kw
  if not results:
    raise ValueError("Building study needs explicit scenarios.")
  return {
    "schema_version": 1, "state": config["state"], "frame": frame, "design_it_kw": design_it,
    "floor_m": config["layout"]["floor_m"],
    "levels_m": levels, "unit_height_m": unit_height, "floor_zones": zones, "phases": phases, "scenarios": results,
    "fws_basis": water.basis if water is not None else None,
    **({"architecture": architecture} if architecture is not None else {}),
  }


def write_building_usd(report: dict, path: Path) -> None:
  from pxr import Gf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  root = UsdGeom.Xform.Define(stage, "/Datacenter")
  stage.SetDefaultPrim(root.GetPrim())
  height = report["unit_height_m"]
  for level, elevation in sorted(report["levels_m"].items()):
    parent = f"/Datacenter/{_prim_name(level)}"
    UsdGeom.Xform.Define(stage, parent)
    for identity, zone in sorted(report["floor_zones"].items()):
      x, y = zone["origin_m"]
      width, depth = zone["size_m"]
      cube = UsdGeom.Cube.Define(stage, f"{parent}/{_prim_name(identity)}")
      cube.CreateSizeAttr(1)
      cube.AddTranslateOp().Set(Gf.Vec3d(x + width / 2, y + depth / 2, elevation + height / 2))
      cube.AddScaleOp().Set(Gf.Vec3f(width, depth, height))
      cube.GetPrim().SetCustomDataByKey("role", zone["role"])
      cube.GetPrim().SetCustomDataByKey("identity", f"{level}/{identity}")
      color = (0.20, 0.54, 0.67) if zone["role"] == "unit" else (0.72, 0.44, 0.25)
      cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
