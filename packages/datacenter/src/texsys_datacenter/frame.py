"""Transfer repeated floor reactions through the building gravity frame."""

from bisect import bisect_right
from dataclasses import asdict
from itertools import pairwise, product
import math
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_spatial import AxisGrid
from texsys_structure import ISection
from texsys_structure.beam import simple_beam

from .unit import _number


COMBINATIONS = ("strength", "service")


def _overlap(a, b):
  return max(0., min(a[1], b[1]) - max(a[0], b[0]))


def _patches(x_span, y_span, units, extras, pressure, factors):
  """Project areal loads to a girder's tributary strip, excluding unit slab patches."""
  masks = [(region["x"], region["y"], -pressure) for region in units.values()]
  masks.extend((region["x"], region["y"], _pressure(region["load"], factors)) for region in extras)
  start, end = y_span
  projected = [(max(start, y[0]), min(end, y[1]), _overlap(x_span, x) * increment)
               for x, y, increment in masks if _overlap(x_span, x) > 0 and _overlap(y_span, y) > 0]
  cuts = sorted({start, end, *(value for a, b, _ in projected for value in (a, b))})
  base = (x_span[1] - x_span[0]) * pressure
  return tuple((a - start, b - start, max(0., base + sum(load for low, high, load in projected if low <= a < high)))
               for a, b in pairwise(cuts))


def _pressure(load, factors):
  if load.keys() != {"permanent_kn_m2", "variable_kn_m2"}:
    raise ValueError("Frame area loads need permanent and variable pressures.")
  return sum(_number(load[f"{key}_kn_m2"], f"area_load.{key}", inclusive=True) * factors[key]
             for key in ("permanent", "variable"))


def _units(building, floor, grid):
  units = {}
  width, depth = floor["bay_m"]
  x_offset, y_offset = floor["origin_m"]
  for name, zone in sorted(building["floor_zones"].items()):
    if zone["role"] != "unit":
      continue
    ox, oy = zone["origin_m"]
    if (min(x_offset, y_offset) < 0 or x_offset + width > zone["size_m"][0]
        or y_offset + depth > zone["size_m"][1]):
      raise ValueError(f"Floor reaction patch must fit unit {name}.")
    x, y = (ox + x_offset, ox + x_offset + width), (oy + y_offset, oy + y_offset + depth)
    axes = tuple(min(grid.x, key=lambda axis: abs(grid.x[axis] - station)) for station in x)
    if (any(not math.isclose(grid.x[axis], station, abs_tol=1e-8) for axis, station in zip(axes, x, strict=True))
        or tuple(grid.x).index(axes[1]) - tuple(grid.x).index(axes[0]) != 1):
      raise ValueError(f"Unit {name} beam ends must land on adjacent main girder lines.")
    units[name] = {"x": x, "y": y, "axes": axes}
  if not units:
    raise ValueError("Main frame needs repeated floor patches.")
  return units


def _section(record):
  shape = ISection(**record["section_m"])
  modulus = _number(record["modulus_gpa"], "modulus_gpa") * 1e6
  return shape, shape.major_inertia_m4 * modulus


def _beam_check(member, response, elastic, span):
  limits = {"moment": _number(member["moment_kn_m"], "moment_kn_m"), "shear": _number(member["shear_kn"], "shear_kn"),
            "deflection": span / _number(member["deflection_span_ratio"], "deflection_span_ratio")}
  return {"moment": response.moment_kn_m / limits["moment"], "shear": response.shear_kn / limits["shear"],
          "deflection": elastic.deflection_m / limits["deflection"]}


def _floor_frame(level, elevation, grid, units, beams, extras, pressure, factors, config, shapes, cooling):
  axes = tuple(grid.x)
  ys = tuple(grid.y)
  stations = tuple(grid.y.values())
  boundaries = [grid.x[axes[0]], *(sum(pair) / 2 for pair in pairwise(grid.x.values())), grid.x[axes[-1]]]
  columns = {f"{x}/{y}": dict.fromkeys(COMBINATIONS, 0.) for x, y in product(axes, ys)}
  cooling_columns = {axis: dict.fromkeys(COMBINATIONS, 0.) for axis in columns}
  point_loads, members = {}, {}
  applied = {combo: {"unit_reactions_kn": 0., "infill_kn": 0., "equipment_kn": 0., "beam_self_weight_kn": 0.}
             for combo in COMBINATIONS}
  for unit, patch in units.items():
    for beam, record in beams.items():
      station = patch["y"][0] + record["y_m"]
      if not patch["y"][0] - 1e-8 <= station <= patch["y"][1] + 1e-8:
        raise ValueError("A secondary beam reaction lies outside its floor patch.")
      span = min(bisect_right(stations, station) - 1, len(stations) - 2)
      for axis, end in zip(patch["axes"], ("start", "end"), strict=True):
        loads = {combo: record["strength" if combo == "strength" else "deflection"][f"reaction_{end}_kn"]
                 for combo in COMBINATIONS}
        point_loads.setdefault((axis, span), []).append({
          "source": f"{level}/{unit}/{beam}/{end}", "station_m": station - stations[span], "load_kn": loads,
          "cooling_equipment_share_kn": {name: {combo: shares[combo][end] for combo in COMBINATIONS}
                                        for name, shares in cooling.get(beam, {}).items()},
        })
        for combo in COMBINATIONS:
          applied[combo]["unit_reactions_kn"] += loads[combo]

  unit_area = sum((patch["x"][1] - patch["x"][0]) * (patch["y"][1] - patch["y"][0]) for patch in units.values())
  area = (boundaries[-1] - boundaries[0]) * (stations[-1] - stations[0])
  for combo in COMBINATIONS:
    applied[combo]["infill_kn"] = _pressure(pressure, factors[combo]) * (area - unit_area)
    applied[combo]["equipment_kn"] = sum((p["x"][1] - p["x"][0]) * (p["y"][1] - p["y"][0])
                                          * _pressure(p["load"], factors[combo]) for p in extras)

  def member(name, kind, coordinates, ends, points=(), strip=None):
    start, end, cross = coordinates
    span = end - start
    shape, ei = shapes[kind]
    weight = shape.area_m2 * config["steel_unit_weight_kn_m3"]
    responses, line_loads = {}, {}
    for combo in COMBINATIONS:
      uniform = weight * factors[combo]["permanent"]
      applied[combo]["beam_self_weight_kn"] += uniform * span
      patches = (_patches(strip, (start, end), units, extras, _pressure(pressure, factors[combo]), factors[combo])
                 if strip is not None else ())
      response = simple_beam(span, ei, uniform, tuple((load["station_m"], load["load_kn"][combo]) for load in points),
                             patches=patches)
      responses[combo] = response
      line_loads[combo] = {"self_weight_kn_m": uniform, "area_patches_start_end_kn_m": patches}
      columns[ends[0]][combo] += response.reaction_start_kn
      columns[ends[1]][combo] += response.reaction_end_kn
      for load in points:
        fraction = load["station_m"] / span
        cooling_share = sum(amount[combo] for amount in load["cooling_equipment_share_kn"].values())
        cooling_columns[ends[0]][combo] += (1 - fraction) * cooling_share
        cooling_columns[ends[1]][combo] += fraction * cooling_share
    members[name] = {
      "kind": kind, "start_end_cross_m": coordinates, "elevation_m": elevation, "columns": ends,
      "secondary_reactions": points, "line_loads": line_loads,
      "response": {key: asdict(value) for key, value in responses.items()},
      "utilization": _beam_check(config[kind], responses["strength"], responses["service"], span),
    }

  for index, (axis, x) in enumerate(grid.x.items()):
    strip = (boundaries[index], boundaries[index + 1])
    for span, (a, b) in enumerate(pairwise(ys)):
      member(f"{axis}/{a}-{b}", "girder", (grid.y[a], grid.y[b], x), (f"{axis}/{a}", f"{axis}/{b}"),
             point_loads.get((axis, span), ()), strip)
  for y_axis, y in grid.y.items():
    for a, b in pairwise(axes):
      member(f"{a}-{b}/{y_axis}", "tie", (grid.x[a], grid.x[b], y), (f"{a}/{y_axis}", f"{b}/{y_axis}"))
  for combo in COMBINATIONS:
    expected = sum(applied[combo].values())
    reaction = sum(values[combo] for values in columns.values())
    if not math.isclose(expected, reaction, abs_tol=1e-6, rel_tol=1e-10):
      raise ValueError(f"Frame load balance did not close at {level}/{combo}.")
  return {"members": members, "column_head_kn": columns, "load_components_kn": applied, "unit_patch_area_m2": unit_area,
          "cooling_equipment_column_reaction_kn": cooling_columns}


def evaluate_main_frame(source: Path, building: dict, floor: dict) -> dict:
  config = _load_json((source / "frame.study.json").read_text(encoding="utf-8"))
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Main frame needs schema_version 1 and an explicit assumed basis.")
  grid = AxisGrid(building["frame"], **config["grid"])
  if (len(grid.x) < 2 or len(grid.y) < 2 or any("/" in name for name in grid.x.keys() | grid.y.keys())
      or min(grid.x.values()) != 0 or min(grid.y.values()) != 0
      or [max(grid.x.values()), max(grid.y.values())] != building["floor_m"]):
    raise ValueError("Main frame axes must bound the entire building footprint.")
  _number(config["steel_unit_weight_kn_m3"], "steel_unit_weight_kn_m3")
  factors = config["factors"]
  if factors.keys() != set(COMBINATIONS) or any(values.keys() != {"permanent", "variable"} for values in factors.values()):
    raise ValueError("Frame requires strength and service factors for permanent and variable loads.")
  for values in factors.values():
    for key, value in values.items():
      _number(value, f"frame.factors.{key}")
  units = _units(building, floor, grid)
  shapes = {kind: _section(config[kind]) for kind in ("girder", "tie")}
  column = ISection(**config["column"]["section_m"])
  clashing_racks, clashing_cooling = [], []
  for unit, patch in units.items():
    for name, rack in (floor["rack_loads"] | floor.get("cooling_equipment_loads", {})).items():
      ox, oy = rack["origin_m"]
      sx, sy = rack["size_m"]
      rx = (patch["x"][0] + ox, patch["x"][0] + ox + sx)
      ry = (patch["y"][0] + oy, patch["y"][0] + oy + sy)
      for ax, ay in product(grid.x, grid.y):
        x, y = grid.point(ax, ay)
        if _overlap(rx, (x - column.width / 2, x + column.width / 2)) > 1e-8 and (
          _overlap(ry, (y - column.depth / 2, y + column.depth / 2)) > 1e-8
        ):
          if name in floor["rack_loads"]:
            clashing_racks.append({"unit": unit, "rack": name, "column": f"{ax}/{ay}"})
          else:
            clashing_cooling.append({"unit": unit, "equipment": name, "column": f"{ax}/{ay}"})
  column_limit = _number(config["column"]["axial_limit_kn"], "column.axial_limit_kn")
  foundation = config["foundation"]
  base = foundation["column_base_m"]
  if type(base) not in {int, float} or not math.isfinite(base) or base >= min(building["levels_m"].values()):
    raise ValueError("The column base must be below the lowest suspended level.")
  fx, fy, depth = (_number(value, "foundation.size_m") for value in foundation["size_m"])
  if any(b - a < dimension for values, dimension in ((grid.x.values(), fx), (grid.y.values(), fy))
         for a, b in pairwise(values)):
    raise ValueError("Concept footing footprints overlap.")
  footing_weight = fx * fy * depth * _number(foundation["unit_weight_kn_m3"], "foundation.unit_weight_kn_m3")
  levels = dict(sorted(building["levels_m"].items(), key=lambda item: item[1]))
  if "Roof" in levels:
    raise ValueError("The roof frame requires a distinct level identity.")
  levels["Roof"] = _number(config["roof"]["elevation_m"], "roof.elevation_m",
                           minimum=max(levels.values()) + building["unit_height_m"], inclusive=True)
  source_cases = floor["structural_screening"]["cases"]
  selected = {key: source_cases[config["unit_cases"][key]] for key in ("occupied", "empty")}
  if any(case["phase"] != "service" or not case["steel_support_beams"]
         or case["include_rack_loads"] != (key == "occupied") for key, case in selected.items()):
    raise ValueError("Frame needs occupied and empty service reactions on the same floor patch.")
  if floor.get("cooling_equipment_loads") and not selected["occupied"]["include_cooling_loads"]:
    raise ValueError("The occupied frame requires installed cooling equipment reactions.")
  if len({beam["section_m"]["depth"] for beam in selected["occupied"]["steel_support_beams"].values()}) != 1:
    raise ValueError("The concept bearing arrangement needs one common secondary-beam depth.")

  def area_loads(loads):
    extras = []
    for zone, load in sorted(loads.items()):
      record = building["floor_zones"][zone]
      if record["role"] not in {"electrical-room", "cooling-room"}:
        raise ValueError("Equipment pressures must bind to declared equipment reserves.")
      x, y = record["origin_m"]
      w, h = record["size_m"]
      extras.append({"x": (x, x + w), "y": (y, y + h), "load": load})
    return extras

  extras = area_loads(config["equipment_zone_loads"])
  roof_equipment = area_loads(config["roof"].get("equipment_zone_loads", {}))
  cases, cache = {}, {}
  for name, case in sorted(config["cases"].items()):
    occupied = set(building["phases"][case["phase"]]["levels"])
    bearing_limit = _number(case.get("bearing_limit_kpa", foundation["bearing_limit_kpa"]), "bearing_limit_kpa")
    if case["expected"] not in {"pass", "fail"}:
      raise ValueError(f"Invalid main frame expectation for {name}.")
    floors = {}
    for level, elevation in levels.items():
      role = "roof" if level == "Roof" else "occupied" if level in occupied else "empty"
      key = level, role
      if key not in cache:
        is_roof = role == "roof"
        beams = {} if is_roof else selected[role]["steel_support_beams"]
        cache[key] = _floor_frame(level, elevation, grid, {} if is_roof else units, beams,
                                  roof_equipment if is_roof else extras,
                                  config["roof"]["load"] if is_roof else config["infill_load"], factors, config, shapes,
                                  {} if is_roof else selected[role].get("cooling_beam_shares_kn", {}))
      floors[level] = cache[key]

    columns, bases = {}, {}
    column_density = column.area_m2 * config["steel_unit_weight_kn_m3"]
    for axis in floors["Roof"]["column_head_kn"]:
      carried = dict.fromkeys(COMBINATIONS, 0.)
      carried_cooling = dict.fromkeys(COMBINATIONS, 0.)
      for (lower, bottom), (upper, top) in reversed(tuple(pairwise([("Foundation", base), *levels.items()]))):
        head = {combo: carried[combo] + floors[upper]["column_head_kn"][axis][combo] for combo in COMBINATIONS}
        weight = column_density * (top - bottom)
        carried = {combo: head[combo] + weight * factors[combo]["permanent"] for combo in COMBINATIONS}
        for combo in COMBINATIONS:
          carried_cooling[combo] += floors[upper]["cooling_equipment_column_reaction_kn"][axis][combo]
        columns[f"{axis}/{lower}-{upper}"] = {"axis": axis, "bottom_m": bottom, "top_m": top,
                                              "head_kn": head, "foot_kn": carried, "self_weight_kn": weight,
                                              "axial_utilization": carried["strength"] / column_limit}
      pressure = (carried["service"] + footing_weight) / (fx * fy)
      bases[axis] = {"column_reaction_kn": carried, "footing_self_weight_kn": footing_weight,
                     "cooling_equipment_reaction_kn": carried_cooling,
                     "bearing_kpa": pressure, "bearing_utilization": pressure / bearing_limit}
    balance = {}
    for combo in COMBINATIONS:
      floors_total = sum(sum(item["load_components_kn"][combo].values()) for item in floors.values())
      column_total = sum(item["self_weight_kn"] for item in columns.values()) * factors[combo]["permanent"]
      reactions = sum(item["column_reaction_kn"][combo] for item in bases.values())
      if not math.isclose(floors_total + column_total, reactions, abs_tol=1e-6, rel_tol=1e-10):
        raise ValueError(f"Building base reaction does not balance gravity loads in {name}/{combo}.")
      balance[combo] = {"floor_and_beam_kn": floors_total, "column_self_weight_kn": column_total,
                        "column_base_reactions_kn": reactions}
      equipment_reactions = sum(base["cooling_equipment_reaction_kn"][combo] for base in bases.values())
      factor = "strength" if combo == "strength" else "deflection"
      equipment_per_floor = len(units) * sum(item["vertical_load_kn"] for item in floor.get("cooling_equipment_loads", {}).values())
      floor_counts = {"occupied": len(occupied), "empty": len(building["levels_m"]) - len(occupied)}
      equipment_demand = sum(count * equipment_per_floor * selected[role]["load_factors"][factor]["point"]
                             for role, count in floor_counts.items() if selected[role].get("include_cooling_loads"))
      if not math.isclose(equipment_reactions, equipment_demand, abs_tol=1e-6):
        raise ValueError(f"Cooling equipment reactions must balance at the foundations in {name}/{combo}.")
      balance[combo]["cooling_equipment_reactions_kn"] = equipment_reactions
    peaks = {mode: max((item["utilization"][mode], f"{level}/{member}")
                       for level, data in floors.items() for member, item in data["members"].items())
             for mode in ("moment", "shear", "deflection")}
    peaks["column-axial"] = max((item["axial_utilization"], member) for member, item in columns.items())
    peaks["bearing"] = max((item["bearing_utilization"], axis) for axis, item in bases.items())
    checks = {mode: value <= 1 + 1e-9 for mode, (value, _) in peaks.items()}
    cases[name] = {"phase": case["phase"], "occupied_levels": sorted(occupied), "expected": case["expected"],
                   "status": "pass" if all(checks.values()) else "fail", "checks": checks,
                   "governing": {mode: {"utilization": value, "member": member} for mode, (value, member) in peaks.items()},
                   "bearing_limit_kpa": bearing_limit, "load_balance": balance,
                   "floors": floors, "columns": columns, "foundations": bases}
  if not cases:
    raise ValueError("Main frame needs explicit gravity cases.")
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "formal_review": "pending",
    "status": "pass" if not (clashing_racks or clashing_cooling)
    and all(case["status"] == case["expected"] for case in cases.values()) else "fail",
    "frame": grid.frame, "grid": {"x": dict(grid.x), "y": dict(grid.y)},
    "levels_m": levels, "rack_column_clashes": clashing_racks,
    "cooling_column_clashes": clashing_cooling,
    "sections": {**{kind: asdict(shape) for kind, (shape, _) in shapes.items()}, "column": asdict(column)},
    "member_ratings": {name: config[name] for name in ("girder", "tie", "column", "foundation")},
    "area_load_inputs": {name: config[name] for name in ("factors", "infill_load", "equipment_zone_loads", "roof")},
    "foundation_size_m": (fx, fy, depth), "column_base_m": base, "cases": cases,
    "unit_patches": units, "secondary_beams": selected["occupied"]["steel_support_beams"],
    "unit_floor_deck_depth_m": floor["deck_parameters"]["slab_top_m"] - floor["deck_bottom_min_m"],
    "unit_floor_top_m": floor["deck_parameters"]["slab_top_m"],
    "pending": ["detailed-lateral-and-seismic-design", "member-and-connection-code-checks",
                "temporary-shoring-to-foundation", "foundation-flexure-punching-and-settlement",
                "coordination-of-columns-and-foundation-footprints"],
  }


def write_frame_usd(report, path, deck_path):
  from pxr import Gf, Sdf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/MainFrame").GetPrim())
  case = next(iter(report["cases"].values()))
  shapes = {name: ISection(**spec) for name, spec in report["sections"].items()}
  secondary_depth = max(item["section_m"]["depth"] for item in report["secondary_beams"].values())
  deck_layer = Sdf.Layer.OpenAsAnonymous(str(deck_path))
  prototype = "/_DeckBayPrototype"
  if not Sdf.CopySpec(deck_layer, f"/{deck_layer.defaultPrim}", stage.GetRootLayer(), prototype):
    raise ValueError("Unable to include the reusable slab bay in the building frame.")
  stage.GetRootLayer().GetPrimAtPath(prototype).specifier = Sdf.SpecifierClass

  def box(parent, name, origin, size, color):
    cube = UsdGeom.Cube.Define(stage, f"{parent}/{name}")
    cube.CreateSizeAttr(1)
    cube.AddTranslateOp().Set(Gf.Vec3d(*(a + b / 2 for a, b in zip(origin, size, strict=True))))
    cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*size))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])

  def steel(group, name, parts):
    parent = f"/MainFrame/{group}/{_prim_name(name)}"
    node = UsdGeom.Xform.Define(stage, parent)
    node.GetPrim().SetCustomDataByKey("identity", name)
    for part in parts:
      box(parent, part.identity, part.origin, part.size, (.33, .45, .53))

  for group in ("Primary", "Secondary", "Columns", "Footings", "FloorBays"):
    UsdGeom.Scope.Define(stage, f"/MainFrame/{group}")
  for level, data in case["floors"].items():
    elevation = report["levels_m"][level]
    steel_bearing = elevation - report["unit_floor_deck_depth_m"]
    top = elevation if level == "Roof" else steel_bearing - secondary_depth
    for name, member in data["members"].items():
      kind = member["kind"]
      steel("Primary", f"{level}/{name}", shapes[kind].beam(*member["start_end_cross_m"], top, along_x=kind == "tie"))
    if level != "Roof":
      for unit, patch in report["unit_patches"].items():
        slab = UsdGeom.Xform.Define(stage, f"/MainFrame/FloorBays/{_prim_name(f'{level}/{unit}')}")
        slab.GetPrim().GetReferences().AddInternalReference(prototype)
        slab.GetPrim().SetInstanceable(True)
        slab.GetPrim().SetCustomDataByKey("identity", f"{level}/{unit}")
        slab.AddTranslateOp().Set(Gf.Vec3d(patch["x"][0], patch["y"][0], elevation - report["unit_floor_top_m"]))
        for name, beam in report["secondary_beams"].items():
          shape = ISection(**beam["section_m"])
          steel("Secondary", f"{level}/{unit}/{name}",
                shape.beam(*patch["x"], patch["y"][0] + beam["y_m"], steel_bearing, along_x=True))
  for name, item in case["columns"].items():
    x, y = item["axis"].split("/")
    center = report["grid"]["x"][x], report["grid"]["y"][y]
    steel("Columns", name, shapes["column"].column(center, item["bottom_m"], item["top_m"]))
  sx, sy, sz = report["foundation_size_m"]
  for axis in case["foundations"]:
    x, y = axis.split("/")
    origin = (report["grid"]["x"][x] - sx / 2, report["grid"]["y"][y] - sy / 2, report["column_base_m"] - sz)
    box("/MainFrame/Footings", _prim_name(axis), origin, (sx, sy, sz), (.68, .64, .56))
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
