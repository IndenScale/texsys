"""Resolve the floor-to-brace force path on the data center frame grid."""

from itertools import pairwise, product
from math import hypot, isclose
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_spatial import AxisGrid
from texsys_structure import ISection
from texsys_structure.diaphragm import AxialMember, Diaphragm, MembranePanel

from .lateral import matrix_expectations
from .unit import _number


def _opening(origin, size):
  x, y = origin
  w, h = size
  return x, y, x + w, y + h


def _openings(frame, floor, building, is_roof):
  holes = {name: _opening(zone["origin_m"], zone["size_m"])
           for name, zone in building["floor_zones"].items() if zone["role"] == "service-riser"}
  if not is_roof:
    holes.update({f"{unit}/{name}": _opening((patch["x"][0] + opening["origin_m"][0],
                                            patch["y"][0] + opening["origin_m"][1]), opening["size_m"])
                  for unit, patch in frame["unit_patches"].items() for name, opening in floor["opening_requests"].items()})
  return holes


def _panels(grid, thickness, holes):
  panels = {}
  for (a, b), (c, d) in product(pairwise(grid.x), pairwise(grid.y)):
    left, right, bottom, top = grid.x[a], grid.x[b], grid.y[c], grid.y[d]
    relevant = tuple(bounds for bounds in holes.values()
                     if max(left, bounds[0]) < min(right, bounds[2]) and max(bottom, bounds[1]) < min(top, bounds[3]))
    panels[f"{a}-{b}/{c}-{d}"] = MembranePanel((f"{a}/{c}", f"{b}/{c}", f"{b}/{d}", f"{a}/{d}"), thickness, relevant)
  return panels


def _component_vectors(grid, points, method, component, level, floor):
  axis = 0 if component["axis"] == "x" else 1
  transverse = 1 - axis
  force = component["level_actions_fx_fy_mz"][level][axis]
  axes = (grid.x, grid.y)
  low, high = min(axes[transverse].values()), max(axes[transverse].values())
  offset = component["load_basis"]["offset_from_plan_centre_m"][level]
  target = (low + high) / 2 + offset
  if method == "uniform-wind":
    edge = (min if component["sign"] > 0 else max)(axes[axis].values())
    stations = tuple(axes[transverse].values())
    segments = tuple(b - a for a, b in pairwise(stations))
    widths = [(a + b) / 2 for a, b in zip((0, *segments), (*segments, 0), strict=True)]
    weight_by_station = dict(zip(stations, widths, strict=True))
    weights = {key: weight_by_station[xy[transverse]] for key, xy in points.items() if xy[axis] == edge}
  else:
    weights = {key: item["service"] for key, item in floor["column_head_kn"].items()}

  total = sum(weights.values())
  centre = sum(points[key][transverse] * weight for key, weight in weights.items()) / total
  # Apply the eccentric contribution as an independent, balanced nodal couple.
  along = {key: force * weight / total for key, weight in weights.items()}
  couple = force * (target - centre) / (high - low)
  for station, sign in ((low, -1), (high, 1)):
    if method == "uniform-wind":
      nodes = [key for key in weights if points[key][transverse] == station]
    else:
      nodes = [key for key, xy in points.items() if xy[transverse] == station]
    for node in nodes:
      along[node] = along.get(node, 0.) + sign * couple / len(nodes)
  return {key: (value, 0.) if axis == 0 else (0., value) for key, value in along.items()}


def _load_vectors(grid, points, lateral, level, floor):
  resolved = {}
  for component in lateral["load_components"]:
    loads = _component_vectors(grid, points, lateral["input"]["method"], component, level, floor)
    for node, vector in loads.items():
      force = resolved.setdefault(node, [0., 0.])
      force[0] += vector[0]
      force[1] += vector[1]
  return resolved


def _brace_joints(bays, below, above):
  forces, joints = {}, {}
  for key, bay in bays.items():
    component = 0 if bay["axis"] == "x" else 1
    values = {}
    for side, story, is_lower in (("below", below, True), ("above", above, False)):
      if key not in story:
        continue
      shear = story[key]["shear_kn"]
      node = bay["chord_nodes"][int((shear >= 0) == is_lower)]
      force = -shear if is_lower else shear
      forces.setdefault(node, [0., 0.])[component] += force
      values[side] = {"node": node, "force_kn": force}
    if values:
      joints[key] = {"axis": bay["axis"], **values}
  return forces, joints


def _limits(data):
  keys = ("panel_shear_kn_m", "panel_normal_kn_m", "girder_axial_kn", "tie_axial_kn",
          "connection_kn", "connection_slip_m", "brace_joint_kn")
  if data.keys() != set(keys):
    raise ValueError("Diaphragm limits must match the supported screening checks.")
  return {key: _number(data[key], key) for key in keys}


def _screen(result, joints, members, limits):
  demand = {
    "panel-shear": (limits["panel_shear_kn_m"],
                    {key: item["max_abs_shear_kn_m"] for key, item in result["panels"].items()}),
    "panel-normal": (limits["panel_normal_kn_m"],
                     {key: item["max_abs_normal_kn_m"] for key, item in result["panels"].items()}),
    "steel-axial": (None, {key: abs(value) for key, value in result["axial_kn"].items()}),
    "slab-to-steel": (limits["connection_kn"],
                      {key: hypot(*item["to_steel_kn"]) for key, item in result["connections"].items()}),
    "connection-slip": (limits["connection_slip_m"],
                         {key: item["slip_m"] for key, item in result["connections"].items()}),
    "brace-joint": (limits["brace_joint_kn"],
                    {f"{key}/{side}": abs(item[side]["force_kn"]) for key, item in joints.items()
                     for side in ("above", "below") if side in item}),
  }
  governing = {}
  for mode, (limit, values) in demand.items():
    if mode == "steel-axial":
      ratings = {key: limits[f"{members[key]['kind']}_axial_kn"] for key in values}
    else:
      ratings = dict.fromkeys(values, limit)
    key = max(values, key=lambda name: values[name] / ratings[name])
    governing[mode] = {"id": key, "demand": values[key], "limit": ratings[key], "utilization": values[key] / ratings[key]}
  return governing


def evaluate_diaphragm(source: Path, frame: dict, floor: dict, building: dict, lateral: dict, openings_report=None):
  config = _load_json((source / "diaphragm.study.json").read_text(encoding="utf-8"))
  if type(config["schema_version"]) is not int or config["schema_version"] != 1 or config["state"] != "assumed" or not config["basis"].strip():
    raise ValueError("Diaphragm needs schema_version 1 and an explicit assumed basis.")
  if not frame["frame"] == lateral["frame"] == building["frame"]:
    raise ValueError("Diaphragm must use the building frame and brace coordinates.")
  grid = AxisGrid(frame["frame"], **frame["grid"])
  points = {f"{x}/{y}": grid.point(x, y) for x, y in product(grid.x, grid.y)}
  levels = tuple(sorted(frame["levels_m"], key=frame["levels_m"].__getitem__))
  if openings_report is not None and (
    openings_report["frame"] != frame["frame"] or openings_report["levels_m"] != frame["levels_m"]
  ):
    raise ValueError("Coordinated openings must use the diaphragm frame and slab datums.")
  modulus = _number(config["concrete_modulus_gpa"], "concrete_modulus_gpa") * 1e6
  poisson = _number(config["concrete_poisson"], "concrete_poisson", inclusive=True)
  stiffness = _number(config["connection_stiffness_kn_m"], "connection_stiffness_kn_m")
  limits = _limits(config["limits"])
  collector_bays = {tuple(bay["chord_nodes"]) for bay in lateral["bays"].values()}
  boundary = {(axis, edge) for axis, stations in enumerate((grid.x, grid.y))
              for edge in (min(stations.values()), max(stations.values()))}
  steel_ea = {key: ISection(**frame["sections"][key]).area_m2 * frame["member_ratings"][key]["modulus_gpa"] * 1e6
              for key in ("girder", "tie")}
  models, layout, conflicts = {}, {}, []
  reference = next(iter(frame["cases"].values()))["floors"]
  for level in levels:
    roof = level == "Roof"
    if openings_report is None:
      thickness = (_number(config["roof_concrete_thickness_m"], "roof_concrete_thickness_m")
                   if roof else floor["concrete_min_depth_m"])
      holes = _openings(frame, floor, building, roof)
    else:
      coordinated = openings_report["levels"][level]
      thickness = coordinated["depth_m"]
      holes = {key: cut["bounds_m"] for key, cut in coordinated["cuts"].items()}
    members = reference[level]["members"]
    for node, (x, y) in points.items():
      for name, (a, b, c, d) in holes.items():
        if a <= x <= c and b <= y <= d:
          conflicts.append({"level": level, "node": node, "opening": name})
    panels = _panels(grid, thickness, holes)
    models[level] = Diaphragm(
      points, panels, {key: AxialMember(tuple(item["columns"]), steel_ea[item["kind"]]) for key, item in members.items()},
      dict.fromkeys(points, stiffness), modulus, poisson,
    )
    member_layout = {}
    for key, member in members.items():
      nodes = tuple(member["columns"])
      a, b = (points[node] for node in nodes)
      roles = ["transfer-member"]
      if any(a[axis] == b[axis] == edge for axis, edge in boundary):
        roles.append("perimeter-chord")
      if nodes in collector_bays:
        roles.append("brace-bay-collector")
      member_layout[key] = {"nodes": nodes, "kind": member["kind"], "roles": roles}
    layout[level] = {"thickness_m": thickness, "openings_xyxy_m": holes, "members": member_layout}

  matrix_cases = {name: {"lateral_case": name, "expected": status}
                  for name, status in matrix_expectations(lateral, "diaphragm").items()}
  if config["cases"].keys() & matrix_cases.keys():
    raise ValueError("Explicit diaphragm and seismic matrix cases must have distinct identities.")
  inputs = config["cases"] | matrix_cases
  if not inputs:
    raise ValueError("Diaphragm study needs explicit cases.")
  sources = {case["lateral_case"] for case in inputs.values()}
  if sources != set(lateral["cases"]):
    raise ValueError("Diaphragm cases must cover every lateral scenario.")
  cases = {}
  for name, item in inputs.items():
    case = lateral["cases"][item["lateral_case"]]
    if item["expected"] not in ("pass", "fail"):
      raise ValueError(f"Invalid diaphragm case expectation for {name}.")
    record = {"input": item, "expected": item["expected"]}
    if not case["checks"]["stability"]:
      cases[name] = {**record, "status": "fail", "checks": {"lateral-stability": False},
                     "mechanism_at": case["mechanism_at"]}
      continue
    case_limits = _limits({**limits, **item.get("limits", {})})
    floors = frame["cases"][case["input"]["gravity_case"]]["floors"]
    response = {}
    for index, level in enumerate(levels):
      above = case["storeys"][levels[index + 1]]["bays"] if index + 1 < len(levels) else {}
      steel, joints = _brace_joints(lateral["bays"], case["storeys"][level]["bays"], above)
      slab = _load_vectors(grid, points, case, level, floors[level])
      result = models[level].solve(slab, steel)
      governing = _screen(result, joints, floors[level]["members"], case_limits)
      response[level] = {**result, "slab_actions_kn": slab, "steel_joint_actions_kn": steel,
                         "brace_joints": joints, "governing": governing}
    modes = next(iter(response.values()))["governing"]
    governing = {}
    for mode in modes:
      level = max(levels, key=lambda key: response[key]["governing"][mode]["utilization"])
      governing[mode] = {**response[level]["governing"][mode], "level": level}
    checks = {"lateral-stability": True, **{mode: record["utilization"] <= 1 for mode, record in governing.items()}}
    cases[name] = {**record, "status": "pass" if all(checks.values()) else "fail", "limits": case_limits,
                   "checks": checks, "governing": governing, "levels": response}
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "formal_review": "pending",
    "status": "pass" if not conflicts and all(case["status"] == case["expected"] for case in cases.values()) else "fail",
    "frame": grid.frame, "levels_m": frame["levels_m"], "nodes_xy_m": points,
    "layout": layout, "opening_connection_conflicts": conflicts, "cases": cases,
    "opening_basis": "slab-openings.json" if openings_report is not None else "unit-templates-and-shaft-reservations",
    "pending": ["refined-opening-edge-forces-and-reinforcement", "code-diaphragm-and-collector-load-combinations",
                "axial-flexure-buckling-and-splices", "stud-group-and-gusset-design",
                "eccentricity-and-local-slab-to-beam-transfer", "coupled-diaphragm-and-lateral-frame-analysis"],
  }


def write_diaphragm_usd(report, path):
  from pxr import Gf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Diaphragm").GetPrim())
  for level, record in report["layout"].items():
    z = report["levels_m"][level]
    parent = f"/Diaphragm/{_prim_name(level)}"
    UsdGeom.Scope.Define(stage, parent)
    for name, member in record["members"].items():
      a, b = (Gf.Vec3d(*report["nodes_xy_m"][node], z) for node in member["nodes"])
      delta = b - a
      is_x = not isclose(delta[0], 0)
      cube = UsdGeom.Cube.Define(stage, f"{parent}/{_prim_name(name)}")
      cube.CreateSizeAttr(1)
      cube.AddTranslateOp().Set((a + b) / 2)
      cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(abs(delta[0]) if is_x else .12, .12 if is_x else abs(delta[1]), .10)
      )
      color = (.72, .24, .15) if "brace-bay-collector" in member["roles"] else (.2, .55, .65)
      cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    for node, (x, y) in report["nodes_xy_m"].items():
      cube = UsdGeom.Cube.Define(stage, f"{parent}/Connection_{_prim_name(node)}")
      cube.CreateSizeAttr(.22)
      cube.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
      cube.CreateDisplayColorAttr([Gf.Vec3f(.85, .62, .21)])
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
