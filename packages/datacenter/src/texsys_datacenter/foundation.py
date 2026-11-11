"""Close the load path from pinned steel bases through spread footings."""

from itertools import pairwise, product
from math import hypot, isclose
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_spatial import AxisGrid
from texsys_structure.foundation import footing_contact, footing_sections

from .lateral import matrix_expectations
from .unit import _identifiers, _number


def _profile(record):
  friction = _number(record["friction_coefficient"], "friction_coefficient")
  if friction > 1:
    raise ValueError("The concept soil profile requires a friction coefficient at most 1.")
  return {"friction_coefficient": friction,
          "subgrade_kn_m3": _number(record["subgrade_kn_m3"], "subgrade_kn_m3")}


def _base_detail(config, size, column):
  plate = tuple(_number(value, "base_plate.size_m") for value in config["base_plate_size_m"])
  pitch = tuple(_number(value, "anchor_pitch_m") for value in config["anchor_pitch_m"])
  diameter = _number(config["anchor_diameter_m"], "anchor_diameter_m")
  length = _number(config["anchor_embedment_m"], "anchor_embedment_m")
  edge = _number(config["anchor_plate_edge_clearance_m"], "anchor_plate_edge_clearance_m")
  if (len(plate) != 3 or len(pitch) != 2 or length >= size[2]
      or any(spacing + diameter + 2 * edge > extent or spacing - diameter < steel or extent >= footing
             for spacing, extent, steel, footing in zip(pitch, plate[:2], (column["width"], column["depth"]),
                                                        size[:2], strict=True))):
    raise ValueError("The base plate and four anchors must fit the column and concrete envelope.")
  return {
    "plate_size_m": plate, "anchor_points_m": tuple(product((-pitch[0] / 2, pitch[0] / 2), (-pitch[1] / 2, pitch[1] / 2))),
    "anchor_diameter_m": diameter, "anchor_embedment_m": length,
    "anchor_tension_limit_kn": _number(config["anchor_tension_limit_kn"], "anchor_tension_limit_kn"),
    "anchor_shear_limit_kn": _number(config["anchor_shear_limit_kn"], "anchor_shear_limit_kn"),
    "plate_compression_limit_kn": _number(config["plate_compression_limit_kn"], "plate_compression_limit_kn"),
  }


def _split_gravity(reaction, factors):
  service, strength = (factors[key] for key in ("service", "strength"))
  a, b = (service[key] for key in ("permanent", "variable"))
  c, d = (strength[key] for key in ("permanent", "variable"))
  determinant = a * d - b * c
  if abs(determinant) < 1e-10:
    raise ValueError("Independent frame combinations are needed to resolve permanent and variable reactions.")
  s, u = (reaction[key] for key in ("service", "strength"))
  permanent, variable = (d * s - b * u) / determinant, (a * u - c * s) / determinant
  if min(permanent, variable) < -1e-7:
    raise ValueError("Foundation gravity components must be nonnegative.")
  return max(0., permanent), max(0., variable)


def _contact_record(contact, force, first_moments):
  resolved = contact.resultants()
  if contact.state in ("full", "partial") and any(
    not isclose(a, b, rel_tol=1e-8, abs_tol=1e-7) for a, b in zip(resolved, (force, *first_moments), strict=True)
  ):
    raise ValueError("Footing pressure does not balance the normal force and first moments.")
  return {
    "state": contact.state, "peak_pressure_kpa": contact.peak_kpa, "contact_area_m2": contact.area_m2,
    "polygon_xy_m": contact.polygon_m, "pressure_plane": contact.pressure,
    "contact_resultants": resolved, "unbalanced_resultants": [a - b for a, b in zip((force, *first_moments), resolved, strict=True)],
  }


def _section_ratios(sections, limits):
  flexure = max(
    abs(edge["flexure_kn_m_per_m"]) / limits["bottom_flexure_kn_m_per_m" if edge["flexure_kn_m_per_m"] >= 0
                                            else "top_flexure_kn_m_per_m"]
    for edge in sections["edges"].values()
  )
  one_way = max(abs(edge["one_way_shear_kn_per_m"]) / limits["one_way_kn_per_m"] for edge in sections["edges"].values())
  punching = abs(sections["punching_kn"]) / (sections["punching_perimeter_m"] * limits["punching_kn_per_m"])
  return {"flexure": flexure, "one-way": one_way, "punching": punching}


def _footing_response(size, detail, section, soil, base, lateral, factors, strength_modes, bearing_limit):
  bx, by, depth = size
  shear_arm = depth + detail["plate_size_m"][2]
  fixed, imposed = _split_gravity(base["column_reaction_kn"], factors)
  footing_weight = base["footing_self_weight_kn"]
  brace_weight = lateral["brace_dead_weight_kn"]
  overturning = lateral["overturning_kn"]
  shear = lateral["horizontal_load_kn"]
  service_factors = factors["service"]
  axial = fixed * service_factors["permanent"] + imposed * service_factors["variable"] + brace_weight + overturning
  normal = axial + footing_weight
  first = [value * shear_arm for value in shear]
  contact = footing_contact((bx, by), normal, first)
  service = {"column_compression_kn": axial, "total_compression_kn": normal, "shear_kn": shear,
             "first_moments_kn_m": first, **_contact_record(contact, normal, first)}
  available = contact.state in ("full", "partial")
  friction = soil["friction_coefficient"] * max(0., normal) if available else 0.
  sliding = hypot(*shear)
  service.update({
    "bearing_limit_kpa": bearing_limit, "bearing_ok": available and contact.peak_kpa <= bearing_limit,
    "soil_sliding_demand_kn": sliding, "soil_sliding_resistance_kn": friction,
    "soil_sliding_ok": available and sliding <= friction + 1e-8,
    "peak_settlement_m": contact.peak_kpa / soil["subgrade_kn_m3"] if available else None,
    "centre_settlement_m": contact.pressure[0] / soil["subgrade_kn_m3"] if available else None,
  })
  strengths = {}
  for name, combination in strength_modes.items():
    g, q, lateral_factor = (combination[key] for key in ("permanent", "variable", "lateral"))
    steel_compression = g * (fixed + brace_weight) + q * imposed + lateral_factor * overturning
    shear_strength = lateral_factor * sliding
    normal = steel_compression + g * footing_weight
    first = [value * lateral_factor * shear_arm for value in shear]
    contact = footing_contact((bx, by), normal, first)
    anchor_tension, anchor_shear = max(0., -steel_compression) / 4, shear_strength / 4
    interaction = anchor_tension / detail["anchor_tension_limit_kn"] + anchor_shear / detail["anchor_shear_limit_kn"]
    stable = contact.state in ("full", "partial")
    sections = footing_sections(contact, detail["plate_size_m"][:2], section["effective_depth_m"],
                                g * footing_weight / (bx * by)) if stable else None
    strengths[name] = {
      "column_compression_kn": steel_compression, "total_compression_kn": normal,
      "first_moments_kn_m": first, "contact": _contact_record(contact, normal, first),
      "plate_utilization": max(0., steel_compression) / detail["plate_compression_limit_kn"],
      "per_anchor_tension_kn": anchor_tension, "per_anchor_shear_kn": anchor_shear,
      "anchor_utilization": interaction, "sections": sections,
      "section_utilization": _section_ratios(sections, section) if sections is not None else None,
    }
  return {"soil": soil, "gravity_components_kn": {"permanent": fixed, "variable": imposed, "brace": brace_weight,
                                                  "footing": footing_weight}, "service": service, "strength": strengths}


def _differential(grid, footings):
  result = {}
  axes = ((grid.x, grid.y), (grid.y, grid.x))
  for axis, (stations, transverse) in enumerate(axes):
    for (a, b), cross in product(pairwise(stations), transverse):
      nodes = (f"{a}/{cross}", f"{b}/{cross}") if axis == 0 else (f"{cross}/{a}", f"{cross}/{b}")
      first, second = (footings[node]["service"]["centre_settlement_m"] for node in nodes)
      result["--".join(nodes)] = None if first is None or second is None else abs(first - second) / (stations[b] - stations[a])
  return result


def _summarize(footings, grid, settlement_limit, differential_limit):
  stable = [key for key, item in footings.items() if item["service"]["state"] in ("full", "partial")]
  service = {key: item["service"] for key, item in footings.items()}
  differentials = _differential(grid, footings)
  values = {
    "bearing": {key: item["peak_pressure_kpa"] / item["bearing_limit_kpa"] for key, item in service.items()},
    "sliding": {key: service[key]["soil_sliding_demand_kn"] / service[key]["soil_sliding_resistance_kn"]
                for key in stable},
    "settlement": {key: service[key]["peak_settlement_m"] / settlement_limit for key in stable},
    "differential": {key: value / differential_limit for key, value in differentials.items() if value is not None},
    "plate": {}, "anchors": {}, "flexure": {}, "one-way": {}, "punching": {},
  }
  for key, item in footings.items():
    for name, mode in item["strength"].items():
      tag = f"{key}/{name}"
      values["plate"][tag] = mode["plate_utilization"]
      values["anchors"][tag] = mode["anchor_utilization"]
      if mode["section_utilization"] is not None:
        for section, utilization in mode["section_utilization"].items():
          values[section][tag] = utilization
  governing = {}
  for check, items in values.items():
    if items:
      key = max(items, key=items.__getitem__)
      governing[check] = {"id": key, "utilization": items[key]}
  checks = {key: item["utilization"] <= 1 + 1e-8 for key, item in governing.items()}
  checks.update({
    "service-contact": len(stable) == len(footings),
    "sliding": all(item["soil_sliding_ok"] for item in service.values()),
    "strength-contact": all(mode["sections"] is not None for item in footings.values() for mode in item["strength"].values()),
  })
  return checks, governing, differentials


def evaluate_foundations(source: Path, frame: dict, lateral: dict):
  config = _load_json((source / "foundation.study.json").read_text(encoding="utf-8"))
  if (type(config["schema_version"]) is not int or config["schema_version"] != 1
      or config["state"] != "assumed" or not config["basis"].strip() or frame["frame"] != lateral["frame"]):
    raise ValueError("Foundation study requires schema version 1, an assumed basis and a shared frame.")
  grid = AxisGrid(frame["frame"], **frame["grid"])
  points = {f"{x}/{y}": grid.point(x, y) for x, y in product(grid.x, grid.y)}
  size = tuple(frame["foundation_size_m"])
  detail = _base_detail(config["column_base"], size, frame["sections"]["column"])
  section = {key: _number(config["sections"][key], key) for key in (
    "effective_depth_m", "bottom_flexure_kn_m_per_m", "top_flexure_kn_m_per_m", "one_way_kn_per_m", "punching_kn_per_m"
  )}
  if section["effective_depth_m"] >= size[2]:
    raise ValueError("Effective footing depth must be less than the overall depth.")
  settlements = _number(config["settlement_limit_m"], "settlement_limit_m")
  differential = _number(config["differential_limit_ratio"], "differential_limit_ratio")
  profiles = {key: _profile(item) for key, item in config["soil_profiles"].items()}
  factors = frame["area_load_inputs"]["factors"]
  modes = {
    "compression": {**factors["strength"], "lateral": _number(config["strength_lateral_factor"], "strength_lateral_factor")},
    "uplift": {"permanent": _number(config["uplift_permanent_factor"], "uplift_permanent_factor"), "variable": 0,
               "lateral": _number(config["strength_lateral_factor"], "strength_lateral_factor")},
  }
  no_lateral = {"brace_dead_weight_kn": 0., "overturning_kn": 0., "horizontal_load_kn": (0., 0.)}
  matrix_cases = {name: {"lateral_case": name, "expected": status, "soil_profile": config["matrix_soil_profile"]}
                  for name, status in matrix_expectations(lateral, "foundation").items()}
  if config["cases"].keys() & matrix_cases.keys():
    raise ValueError("Explicit foundation and seismic matrix cases must have distinct identities.")
  cases = {}
  gravity_sources, lateral_sources = set(), set()
  for key, case in (config["cases"] | matrix_cases).items():
    if case["expected"] not in ("pass", "fail") or ("gravity_case" in case) == ("lateral_case" in case):
      raise ValueError(f"Foundation case {key} needs exactly one upstream source and a pass/fail expectation.")
    default = profiles[case["soil_profile"]]
    soil = {node: profiles[name] for node, name in case.get("soil_overrides", {}).items()}
    if soil.keys() - points.keys():
      raise ValueError(f"Foundation case {key} contains unknown soil override axes.")
    record = {"input": case, "expected": case["expected"]}
    if "lateral_case" in case:
      lateral_sources.add(case["lateral_case"])
      response = lateral["cases"][case["lateral_case"]]
      if not response["checks"]["stability"]:
        cases[key] = {**record, "status": "fail", "checks": {"lateral-stability": False}}
        continue
      gravity = frame["cases"][response["input"]["gravity_case"]]
      actions = response["foundations"]
    else:
      gravity_sources.add(case["gravity_case"])
      gravity = frame["cases"][case["gravity_case"]]
      actions = dict.fromkeys(points, no_lateral)
    footings = {node: _footing_response(size, detail, section, soil.get(node, default), base, actions[node],
                                         factors, modes, gravity["bearing_limit_kpa"])
                for node, base in gravity["foundations"].items()}
    checks, governing, differences = _summarize(footings, grid, settlements, differential)
    cases[key] = {**record, "status": "pass" if all(checks.values()) else "fail", "checks": checks,
                  "governing": governing, "differential_settlement": differences, "footings": footings}
  if gravity_sources != set(frame["cases"]) or lateral_sources != set(lateral["cases"]):
    raise ValueError("Foundation cases must cover each gravity and lateral source scenario.")
  if "footings" not in cases.get(config["layout_case"], {}):
    raise ValueError("The foundation layout must reference a solved case.")
  design_cases = _identifiers(config["design_cases"], "design_cases") | matrix_cases.keys()
  if not design_cases or design_cases - cases.keys():
    raise ValueError("Foundation design screening must list known case identities.")
  design_failures = sorted(name for name in design_cases if cases[name]["status"] != "pass")
  top = frame["column_base_m"] - detail["plate_size_m"][2]
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "formal_review": "pending",
    "status": "pass" if all(case["status"] == case["expected"] for case in cases.values()) else "fail",
    "design_screening": {"status": "revision-required" if design_failures else "pass",
                         "cases": sorted(design_cases), "failed_cases": design_failures},
    "seismic_matrix": lateral.get("seismic_matrix"),
    "frame": grid.frame, "nodes_xy_m": points, "foundation_size_m": size, "column_base_m": frame["column_base_m"],
    "layout_case": config["layout_case"], "concrete_top_m": top, "bearing_plane_m": top - size[2],
    "column_base": detail, "sections": section, "soil_profiles": profiles, "strength_combinations": modes,
    "settlement_limit_m": settlements, "differential_limit_ratio": differential, "cases": cases,
    "pending": ["site-investigation-bearing-and-settlement", "groundwater-and-buoyancy", "soil-reaction-and-base-slip",
                "unbalanced-punching-moment", "anchor-breakout-prying-and-grout", "footing-reinforcement-and-development",
                "seismic-load-combinations-and-tie-beams"],
  }


def write_foundations_usd(report, path):
  from pxr import Gf, Sdf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Foundations").GetPrim())
  sx, sy, depth = report["foundation_size_m"]
  seat = report["column_base_m"]
  top = report["concrete_top_m"]
  detail = report["column_base"]
  px, py, plate_depth = detail["plate_size_m"]
  layout_case = report["layout_case"]
  results = report["cases"][layout_case]["footings"]

  def box(parent, name, centre, size, colour):
    cube = UsdGeom.Cube.Define(stage, f"{parent}/{name}")
    cube.CreateSizeAttr(1)
    cube.AddTranslateOp().Set(Gf.Vec3d(*centre))
    cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*size))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*colour)])

  for node, (x, y) in report["nodes_xy_m"].items():
    parent = f"/Foundations/{_prim_name(node)}"
    prim = UsdGeom.Xform.Define(stage, parent).GetPrim()
    prim.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(node)
    service = results[node]["service"]
    prim.SetCustomDataByKey("case", layout_case)
    prim.SetCustomDataByKey("contactState", service["state"])
    prim.SetCustomDataByKey("peakPressureKpa", service["peak_pressure_kpa"])
    color = (.67, .64, .56) if service["bearing_ok"] and service["soil_sliding_ok"] else (.74, .31, .19)
    box(parent, "Concrete", (x, y, top - depth / 2), (sx, sy, depth), color)
    box(parent, "BasePlate", (x, y, seat - plate_depth / 2), (px, py, plate_depth), (.27, .4, .53))
    diameter = detail["anchor_diameter_m"]
    embed = detail["anchor_embedment_m"]
    length = embed + plate_depth
    for index, (dx, dy) in enumerate(detail["anchor_points_m"], start=1):
      rod = UsdGeom.Cylinder.Define(stage, f"{parent}/Anchor{index}")
      rod.CreateAxisAttr(UsdGeom.Tokens.z)
      rod.CreateRadiusAttr(diameter / 2)
      rod.CreateHeightAttr(length)
      rod.AddTranslateOp().Set(Gf.Vec3d(x + dx, y + dy, seat - length / 2))
    polygon = service["polygon_xy_m"]
    if polygon:
      patch = UsdGeom.Mesh.Define(stage, f"{parent}/SoilContact")
      patch.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
      patch.CreateDoubleSidedAttr(True)
      patch.CreatePointsAttr([Gf.Vec3f(x + a, y + b, report["bearing_plane_m"] - .002) for a, b in polygon])
      patch.CreateFaceVertexCountsAttr([len(polygon)])
      patch.CreateFaceVertexIndicesAttr(list(range(len(polygon))))
      patch.CreateDisplayColorAttr([Gf.Vec3f(.22, .56, .46)])
  for key, tie in report.get("ties", {}).items():
    (ax, ay), (bx, by) = tie["face_points_m"]
    width = tie["width_m"]
    size = (bx - ax, width, tie["depth_m"]) if tie["axis"] == "x" else (width, by - ay, tie["depth_m"])
    box("/Foundations", f"Tie_{_prim_name(key)}", ((ax + bx) / 2, (ay + by) / 2, tie["centre_z_m"]),
        size, (.28, .52, .62))
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
