"""Screen the building's lateral load path against frame and door geometry."""

from itertools import pairwise, product
from math import hypot, isclose, isfinite, sqrt
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_spatial import AxisGrid
from texsys_structure.lateral import BraceBay, LateralMechanism, solve_braced_frame

from .unit import _identifiers, _number


def _brace_layout(grid, config):
  modulus = _number(config["modulus_gpa"], "brace.modulus_gpa") * 1e6
  area = _number(config["area_m2"], "brace.area_m2")
  bays, chords = {}, {}
  for name, record in config["bays"].items():
    axis, fixed = record["axis"], record["line"]
    if axis not in ("x", "y"):
      raise ValueError(f"Invalid brace direction for {name}.")
    stations, cross = (grid.x, grid.y) if axis == "x" else (grid.y, grid.x)
    pair = tuple(record["between"])
    if pair not in pairwise(stations) or fixed not in cross:
      raise ValueError(f"Brace {name} must span adjacent main frame axes.")
    nodes = tuple((station, fixed) if axis == "x" else (fixed, station) for station in pair)
    keys = tuple(f"{x}/{y}" for x, y in nodes)
    if keys in chords.values():
      raise ValueError(f"Brace bay {name} repeats an existing span.")
    a, b = ((grid.x[x], grid.y[y]) for x, y in nodes)
    chords[name], bays[name] = keys, BraceBay(axis, a, b, area, modulus)
  if not bays:
    raise ValueError("The study requires at least one configured brace bay.")
  return bays, chords


def _door_conflicts(bays, egress):
  conflicts = []
  for name, door in egress["doors"].items():
    normal = door["wall_axis"]
    along = 1 - normal
    center = door["point_m"]
    half_width = door["clear_width_m"] / 2
    for bay_name, bay in bays.items():
      if ((bay.axis == "x") == (along == 0)
          and isclose(bay.start_m[normal], center[normal], abs_tol=1e-8)
          and max(bay.start_m[along], center[along] - half_width) < min(bay.end_m[along], center[along] + half_width)):
        conflicts.append({"brace": bay_name, "door": name})
  return conflicts


def _directional_actions(case, component, elevations, base, plan, gravity, grid):
  axis, sign = component["axis"], component["sign"]
  if axis not in ("x", "y") or type(sign) is not int or sign not in (-1, 1):
    raise ValueError("A lateral action requires direction x or y and sign -1 or 1.")
  normal = 1 if axis == "x" else 0
  center = sum(plan[normal]) / 2
  eccentricity = component["eccentricity_m"]
  if type(eccentricity) not in (int, float) or not isfinite(eccentricity):
    raise ValueError("Load eccentricity must be a finite signed length.")
  method = case["method"]
  levels = tuple(elevations)
  if method == "uniform-wind":
    pressure = _number(case["pressure_kpa"], "pressure_kpa")
    distances = [b - a for a, b in pairwise(elevations.values())]
    tributaries = [(a + b) / 2 for a, b in zip((0, *distances), (*distances, 0), strict=True)]
    forces = [pressure * (plan[normal][1] - plan[normal][0]) * width for width in tributaries]
    offsets = [eccentricity] * len(levels)
    basis = {"tributary_facade_height_m": dict(zip(levels, tributaries, strict=True))}
  elif method == "equivalent-seismic":
    coefficient = _number(case["base_shear_coefficient"], "base_shear_coefficient")
    exponent = _number(case["height_exponent"], "height_exponent", inclusive=True)
    weights, offsets = [], []
    stations = grid.y if normal else grid.x
    for level in levels:
      heads = gravity["floors"][level]["column_head_kn"]
      weight = sum(item["service"] for item in heads.values())
      centroid = sum(stations[key.split("/")[normal]] * item["service"] for key, item in heads.items()) / weight
      weights.append(weight)
      offsets.append(centroid - center + eccentricity)
    vertical = [w * (elevations[level] - base) ** exponent for level, w in zip(levels, weights, strict=True)]
    forces = [coefficient * sum(weights) * w / sum(vertical) for w in vertical]
    basis = {"weight_source": "gravity service floor head reactions, including floor and beam loads",
             "effective_floor_weight_kn": dict(zip(levels, weights, strict=True))}
  else:
    raise ValueError(f"Unknown lateral load method: {method}.")
  basis["offset_from_plan_centre_m"] = dict(zip(levels, offsets, strict=True))
  actions = {}
  for level, force, offset in zip(levels, forces, offsets, strict=True):
    signed = sign * component.get("scale", 1.) * force
    actions[level] = (signed, 0., -signed * offset) if axis == "x" else (0., signed, signed * offset)
  return actions, basis


def _level_actions(case, elevations, base, plan, gravity, grid):
  components = case.get("components", (case,))
  if not components or len({component["axis"] for component in components}) != len(components):
    raise ValueError("Lateral cases need one component per loaded axis.")
  resolved = []
  for component in components:
    _number(component.get("scale", 1.), "directional_scale")
    actions, basis = _directional_actions(case, component, elevations, base, plan, gravity, grid)
    resolved.append({
      "axis": component["axis"], "sign": component["sign"], "scale": component.get("scale", 1.),
      "eccentricity_m": component["eccentricity_m"], "level_actions_fx_fy_mz": actions, "load_basis": basis,
    })
  total = {level: tuple(sum(item["level_actions_fx_fy_mz"][level][i] for item in resolved) for i in range(3))
           for level in elevations}
  return total, resolved[0]["load_basis"], resolved


def _seismic_matrix(config, plan):
  if config is None:
    return {}, None
  gravity = config["gravity_case"]
  eccentricity_ratio = _number(config["eccentricity_ratio"], "eccentricity_ratio")
  orthogonal = _number(config["orthogonal_ratio"], "orthogonal_ratio")
  if orthogonal >= 1 or eccentricity_ratio >= .5:
    raise ValueError("Matrix orthogonal scale must be below 1 and eccentricity below half the transverse width.")
  common = {
    "gravity_case": gravity, "method": "equivalent-seismic",
    "base_shear_coefficient": _number(config["base_shear_coefficient"], "base_shear_coefficient"),
    "height_exponent": _number(config["height_exponent"], "height_exponent", inclusive=True), "unavailable_bays": [],
  }
  signs = {-1: "neg", 1: "pos"}
  aliases = config.get("aliases", {})
  configured = {}
  for index, sign, eccentricity_sign in product(range(2), signs, signs):
    axis, secondary = "xy"[index], "yx"[index]
    lo, hi = plan[1 - index]
    eccentricity = eccentricity_sign * eccentricity_ratio * (hi - lo)
    primary = {"axis": axis, "sign": sign, "scale": 1., "eccentricity_m": eccentricity}
    base = f"{gravity}-seismic-{axis}-{signs[sign]}-e{signs[eccentricity_sign]}"
    configured[base] = [primary]
    for second_sign, label in signs.items():
      configured[f"{base}-{secondary}-{label}"] = [
        primary, {"axis": secondary, "sign": second_sign, "scale": orthogonal, "eccentricity_m": 0.},
      ]
  if aliases.keys() - configured.keys() or any(not isinstance(name, str) or not name for name in aliases.values()):
    raise ValueError("Seismic matrix aliases must name generated cases and use nonempty identities.")
  cases = {aliases.get(name, name): {**common, "components": components} for name, components in configured.items()}
  if len(cases) != len(configured):
    raise ValueError("Seismic matrix case identities must be unique.")
  failures = {}
  for stage in ("lateral", "diaphragm", "foundation", "foundation-ties"):
    expectation = config["expected_failures"][stage]
    listed = set(cases) if expectation == "all" else _identifiers(expectation, f"seismic_matrix.{stage}")
    if listed - cases.keys():
      raise ValueError(f"Seismic matrix includes unknown expected failures for {stage}.")
    failures[stage] = sorted(listed)
  for name, case in cases.items():
    case["expected"] = "fail" if name in failures["lateral"] else "pass"
  return cases, {
    "cases": sorted(cases), "gravity_case": gravity, "orthogonal_ratio": orthogonal, "eccentricity_ratio": eccentricity_ratio,
    "base_shear_coefficient": common["base_shear_coefficient"], "height_exponent": common["height_exponent"],
    "expected_failures": failures,
  }


def matrix_expectations(report, stage):
  matrix = report.get("seismic_matrix")
  if matrix is None:
    return {}
  failures = set(matrix["expected_failures"][stage])
  return {name: "fail" if name in failures else "pass" for name in matrix["cases"]}


def _foundation_response(bays, chords, storeys, gravity, footing_area, weight_density, anchor_limit):
  increments = dict.fromkeys(gravity["foundations"], 0.)
  weights = increments.copy()
  horizontal = {key: [0., 0.] for key in increments}
  bottom = next(iter(storeys.values()))
  for key, force in bottom["bays"].items():
    shear = force["shear_kn"]
    active_chord = chords[key][0 if shear >= 0 else 1]
    horizontal[active_chord][0 if bays[key].axis == "x" else 1] += shear
  for story in storeys.values():
    height = story["top_m"] - story["bottom_m"]
    for key, bay in bays.items():
      start, end = chords[key]
      weight = bay.area_m2 * hypot(bay.span_m, height) * weight_density
      weights[start] += weight
      weights[end] += weight
    for key, force in story["bays"].items():
      start, end = chords[key]
      increments[start] -= force["compression_at_end_kn"]
      increments[end] += force["compression_at_end_kn"]
  foundations = {}
  for key, base in gravity["foundations"].items():
    original = base["column_reaction_kn"]["service"] + base["footing_self_weight_kn"]
    compression = original + weights[key] + increments[key]
    bearing = max(0., compression) / footing_area
    uplift = max(0., -compression)
    foundations[key] = {
      "gravity_kn": original, "brace_dead_weight_kn": weights[key], "overturning_kn": increments[key],
      "horizontal_load_kn": horizontal[key], "net_compression_kn": compression,
      "mean_bearing_kpa": bearing, "net_uplift_kn": uplift,
      "bearing_utilization": bearing / gravity["bearing_limit_kpa"],
      "uplift_ok": uplift <= anchor_limit + 1e-8,
    }
  return foundations


def _equilibrium(storeys, foundations, actions, elevations, base, grid):
  expected = [sum(actions[name][axis] * (elevation - base) for name, elevation in elevations.items())
              for axis in (0, 1)]
  resolved = [sum(stations[key.split("/")[axis]] * item["overturning_kn"] for key, item in foundations.items())
              for axis, stations in enumerate((grid.x, grid.y))]
  residual = [a - b for a, b in zip(resolved, expected, strict=True)]
  vertical = sum(item["overturning_kn"] for item in foundations.values())
  bottom = next(iter(storeys.values()))
  horizontal = [sum(item["horizontal_load_kn"][i] for item in foundations.values()) - demand
                for i, demand in enumerate(bottom["shear_kn"])]
  story_ok = all(
    max(map(abs, (*story["equilibrium_residual"]["force_kn"], story["equilibrium_residual"]["torsion_kn_m"]))) < 1e-6
    for story in storeys.values()
  )
  ok = story_ok and max(map(abs, (vertical, *horizontal))) < 1e-6 and all(
    isclose(a, b, abs_tol=1e-6, rel_tol=1e-9) for a, b in zip(resolved, expected, strict=True)
  )
  return {
    "ok": ok, "vertical_residual_kn": vertical, "base_shear_residual_kn": horizontal,
    "overturning_demand_kn_m": expected,
    "overturning_from_reactions_kn_m": resolved, "overturning_residual_kn_m": residual,
  }


def evaluate_lateral(source: Path, frame: dict, egress: dict):
  config = _load_json((source / "lateral.study.json").read_text(encoding="utf-8"))
  if config["schema_version"] != 1 or config["state"] != "assumed" or not config["basis"].strip():
    raise ValueError("Lateral study needs schema version 1 and an assumed design basis.")
  if egress["frame"] != frame["frame"]:
    raise ValueError("Brace bays and egress doors must share the same coordinate frame.")
  grid = AxisGrid(frame["frame"], **frame["grid"])
  elevations = dict(sorted(frame["levels_m"].items(), key=lambda item: item[1]))
  plan = tuple((min(stations.values()), max(stations.values())) for stations in (grid.x, grid.y))
  base = frame["column_base_m"]
  bays, chords = _brace_layout(grid, config["brace"])
  conflicts = _door_conflicts(bays, egress)
  area = frame["foundation_size_m"][0] * frame["foundation_size_m"][1]
  axial_limit = _number(config["brace"]["tension_limit_kn"], "tension_limit_kn")
  density = _number(config["brace"]["unit_weight_kn_m3"], "unit_weight_kn_m3")
  drift_limit = _number(config["drift_limit_ratio"], "drift_limit_ratio")
  anchor_limit = _number(config["footing_net_uplift_limit_kn"], "footing_net_uplift_limit_kn", inclusive=True)
  matrix_cases, matrix = _seismic_matrix(config.get("seismic_matrix"), plan)
  if matrix_cases.keys() & config["cases"].keys():
    raise ValueError("Explicit lateral cases and seismic matrix cases must have distinct identities.")
  cases = {}
  for name, case in (config["cases"] | matrix_cases).items():
    unavailable = _identifiers(case["unavailable_bays"], "unavailable_bays")
    if unavailable - bays.keys() or case["expected"] not in ("pass", "fail"):
      raise ValueError(f"Invalid unavailable bays or expected status in {name}.")
    active = {key: bay for key, bay in bays.items() if key not in unavailable}
    gravity = frame["cases"][case["gravity_case"]]
    actions, basis, components = _level_actions(case, elevations, base, plan, gravity, grid)
    record = {"input": case, "expected": case["expected"], "level_actions_fx_fy_mz": actions,
              "load_basis": basis, "load_components": components}
    try:
      storeys = solve_braced_frame(elevations, base, plan, active, actions)
    except LateralMechanism as error:
      cases[name] = {**record, "status": "fail", "checks": {"stability": False}, "mechanism_at": error.level}
      continue
    foundations = _foundation_response(bays, chords, storeys, gravity, area, density, anchor_limit)
    balance = _equilibrium(storeys, foundations, actions, elevations, base, grid)
    drift, drift_level = max((story["max_corner_drift_ratio"], key) for key, story in storeys.items())
    tension, tension_level, tension_bay = max((force["tension_kn"], level, key)
                                              for level, story in storeys.items() for key, force in story["bays"].items())
    bearing_key = max(foundations, key=lambda key: foundations[key]["mean_bearing_kpa"])
    uplift_key = max(foundations, key=lambda key: foundations[key]["net_uplift_kn"])
    checks = {"stability": True, "equilibrium": balance["ok"], "drift": drift <= drift_limit,
              "brace-tension": tension <= axial_limit, "foundation-bearing": foundations[bearing_key]["bearing_utilization"] <= 1,
              "foundation-uplift": foundations[uplift_key]["uplift_ok"]}
    cases[name] = {
      **record, "status": "pass" if all(checks.values()) else "fail", "checks": checks,
      "storeys": storeys, "foundations": foundations, "equilibrium": balance,
      "governing": {
        "drift": {"level": drift_level, "ratio": drift},
        "brace-tension": {"level": tension_level, "bay": tension_bay, "demand_kn": tension},
        "foundation-bearing": {"chord": bearing_key, "demand_kpa": foundations[bearing_key]["mean_bearing_kpa"]},
        "foundation-uplift": {"chord": uplift_key, "demand_kn": foundations[uplift_key]["net_uplift_kn"]},
      },
    }
  if not cases:
    raise ValueError("Lateral study requires explicit cases.")
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "formal_review": "pending",
    "status": "pass" if not conflicts and all(case["status"] == case["expected"] for case in cases.values()) else "fail",
    "frame": grid.frame, "levels_m": elevations, "base_m": base, "door_conflicts": conflicts,
    "limits": {"drift_ratio": drift_limit, "tension_kn": axial_limit, "net_uplift_kn": anchor_limit},
    "bays": {name: {"axis": bay.axis, "start_m": bay.start_m, "end_m": bay.end_m,
                    "chord_nodes": chords[name], "area_m2": bay.area_m2, "modulus_kn_m2": bay.modulus_kn_m2}
             for name, bay in bays.items()},
    "cases": cases,
    "seismic_matrix": matrix,
    "pending": ["site-wind-and-seismic-basis", "code-load-combinations-and-second-order-effects",
                "detailed-diaphragm-collectors-and-connections", "brace-cyclic-and-chord-member-checks",
                "column-base-anchors-and-foundation-soil-interaction"],
  }


def write_lateral_usd(report, path):
  from pxr import Gf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/LateralFrame").GetPrim())
  levels = sorted(report["levels_m"].items(), key=lambda item: item[1])
  for name, bay in report["bays"].items():
    parent = f"/LateralFrame/{_prim_name(name)}"
    UsdGeom.Scope.Define(stage, parent).GetPrim().SetCustomDataByKey("identity", name)
    for (_, bottom), (level, top) in pairwise([("Foundation", report["base_m"]), *levels]):
      a, b = bay["start_m"], bay["end_m"]
      for diagonal, (start, end) in enumerate(((a, b), (b, a)), start=1):
        p, q = Gf.Vec3d(*start, bottom), Gf.Vec3d(*end, top)
        vector = q - p
        width = sqrt(bay["area_m2"])
        rotation = Gf.Rotation(Gf.Vec3d(0, 0, 1), vector.GetNormalized())
        cube = UsdGeom.Cube.Define(stage, f"{parent}/{_prim_name(level)}_{diagonal}")
        cube.CreateSizeAttr(1)
        cube.AddTranslateOp().Set((p + q) / 2)
        cube.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(rotation.GetQuat())
        cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(width, width, vector.GetLength()))
        cube.CreateDisplayColorAttr([Gf.Vec3f(.83, .45, .18)])
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
