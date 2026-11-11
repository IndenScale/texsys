"""Compare tied footing runs with the independent pads under the same actions."""

from copy import deepcopy
from itertools import pairwise
from math import hypot, isclose
from pathlib import Path

from texsys.formal import _load_json
from texsys_spatial import AxisGrid
from texsys_structure.foundation import FootingContact, footing_sections
from texsys_structure.foundation_ties import FoundationTie, TiedPad, TieEquilibriumError, solve_tied_footings

from .foundation import _contact_record, _section_ratios, _summarize
from .lateral import matrix_expectations
from .unit import _number


def _layout(config, grid, foundation):
  bx, by, depth = foundation["foundation_size_m"]
  section = config["beam"]
  width, height = (_number(section[key], key) for key in ("width_m", "depth_m"))
  axis_height = _number(section["height_above_bearing_m"], "height_above_bearing_m")
  modulus = _number(section["elastic_modulus_gpa"], "elastic_modulus_gpa") * 1e6
  axial_factor, bending_factor = (_number(section[key], key) for key in ("effective_ea_ratio", "effective_ei_ratio"))
  density = _number(section["unit_weight_kn_m3"], "unit_weight_kn_m3")
  if width > min(bx, by) or not height / 2 <= axis_height <= depth - height / 2 or max(axial_factor, bending_factor) > 1:
    raise ValueError("The tie section and its axis must fit within both footing faces.")
  beam = FoundationTie(modulus * width * height * axial_factor, modulus * width * height ** 3 / 12 * bending_factor,
                       axis_height, width * height * density)
  runs, geometry, used = {}, {}, set()
  for name, run in config["runs"].items():
    direction = run["axis"]
    if direction not in ("x", "y"):
      raise ValueError(f"Invalid tie direction in {name}.")
    axis = int(direction == "y")
    lines, fixed = (grid.x, grid.y) if axis == 0 else (grid.y, grid.x)
    chain = tuple(run["stations"])
    if len(chain) < 2 or any(pair not in tuple(pairwise(lines)) for pair in pairwise(chain)) or run["line"] not in fixed:
      raise ValueError(f"Tie run {name} must connect consecutive grid intersections.")
    nodes = tuple(f"{station}/{run['line']}" if axis == 0 else f"{run['line']}/{station}" for station in chain)
    if used.intersection(nodes):
      raise ValueError("A footing can belong to only one straight tie run in this model.")
    used.update(nodes)
    runs[name] = {"axis": axis, "nodes": nodes, "stations_m": tuple(lines[station] for station in chain)}
    for a, b in pairwise(nodes):
      start, end = (list(foundation["nodes_xy_m"][node]) for node in (a, b))
      span_dimension = (bx, by)[axis]
      start[axis] += span_dimension / 2
      end[axis] -= span_dimension / 2
      geometry[f"{a}--{b}"] = {"nodes": (a, b), "axis": direction, "face_points_m": (start, end),
                               "centre_z_m": foundation["bearing_plane_m"] + axis_height,
                               "width_m": width, "depth_m": height}
  if not runs:
    raise ValueError("The alternative needs at least one physical tie run.")
  return runs, geometry, beam


def _solve_run(run, footings, size, tie, stiffness, mode, factor):
  axis, nodes = run["axis"], run["nodes"]
  along_across = size[:2] if axis == 0 else size[:2][::-1]
  pads = {node: TiedPad(station, along_across, footings[node]["soil"]["subgrade_kn_m3"], stiffness,
                       footings[node]["soil"]["friction_coefficient"])
          for node, station in zip(nodes, run["stations_m"], strict=True)}
  actions = {}
  for node in nodes:
    record = footings[node]
    source = record["service"] if mode == "service" else record["strength"][mode]
    mx, my = source["first_moments_kn_m"]
    shear = record["service"]["shear_kn"][axis] * factor
    transverse = record["service"]["shear_kn"][1 - axis] * factor
    actions[node] = (shear, source["total_compression_kn"], mx if axis == 0 else my, my if axis == 0 else mx, transverse)
  return solve_tied_footings(pads, dict.fromkeys(pairwise(nodes), tie), actions)


def _world_contact(local, axis):
  if axis == 0:
    return local
  a, b, c = local.pressure
  return FootingContact(local.state, local.size_m[::-1], (a, c, b), tuple((y, x) for x, y in reversed(local.polygon_m)))


def _update_footing(updated, baseline, response, axis, mode, mode_factors, report):
  contact = _world_contact(response["contact"], axis)
  source = baseline["service"] if mode == "service" else baseline["strength"][mode]
  target = updated["service"] if mode == "service" else updated["strength"][mode]
  end_actions = response["tie_end_actions"]
  axis_load = sum(action["along_force_kn"] for action in end_actions)
  face_actions = []
  for end in end_actions:
    position, force, moment = (end[key] for key in ("along_position_m", "down_force_kn", "along_first_moment_kn_m"))
    face_actions.append((position, 0., force, moment, 0.) if axis == 0 else (0., position, force, 0., moment))
  normal = source["total_compression_kn"] + sum(action[2] for action in face_actions)
  first = [value + sum(action[i] * action[2] + action[3 + i] for action in face_actions)
           for i, value in enumerate(source["first_moments_kn_m"])]
  factor = 1 if mode == "service" else mode_factors["lateral"]
  shear = [value * factor for value in baseline["service"]["shear_kn"]]
  shear[axis] += axis_load
  if not isclose(shear[axis], response["along_soil_reaction_kn"], abs_tol=1e-6, rel_tol=1e-8):
    raise ValueError("Tie end actions and horizontal subgrade reaction do not balance.")
  stable = contact.state in ("full", "partial")
  friction = baseline["soil"]["friction_coefficient"] * normal if stable else 0.
  demand = hypot(*shear)
  balance = _contact_record(contact, normal, first)
  target.update({
    "total_compression_kn": normal, "first_moments_kn_m": first, "soil_shear_kn": shear,
    "soil_sliding_demand_kn": demand, "soil_sliding_resistance_kn": friction,
    "soil_sliding_ok": stable and demand <= friction + 1e-8, "tie_end_actions": end_actions,
    "along_displacement_m": response["along_displacement_m"],
    "mobilized_slip_m": response["mobilized_slip_m"],
  })
  if mode == "service":
    target.update(balance)
    target["bearing_ok"] = stable and contact.peak_kpa <= target["bearing_limit_kpa"]
    target["centre_settlement_m"] = response["centre_settlement_m"] if stable else None
    target["peak_settlement_m"] = contact.peak_kpa / baseline["soil"]["subgrade_kn_m3"] if stable else None
  else:
    target["contact"] = balance
    bx, by, _ = report["foundation_size_m"]
    dead = baseline["gravity_components_kn"]["footing"] * mode_factors["permanent"] / (bx * by)
    section = report["sections"]
    sections = footing_sections(contact, report["column_base"]["plate_size_m"][:2], section["effective_depth_m"], dead,
                                face_actions) if stable else None
    target["sections"] = sections
    target["section_utilization"] = _section_ratios(sections, section) if sections is not None else None


def _strength_sliding(footings, modes):
  for record in footings.values():
    for name, action in record["strength"].items():
      if "soil_sliding_ok" in action:
        if not action["soil_sliding_ok"]:
          return False
      elif (action["contact"]["state"] not in ("full", "partial")
            or hypot(*record["service"]["shear_kn"]) * modes[name]["lateral"]
            > record["soil"]["friction_coefficient"] * action["total_compression_kn"] + 1e-8):
        return False
  return True


def evaluate_foundation_ties(source: Path, foundation: dict, frame: dict):
  config = _load_json((source / "foundation-ties.study.json").read_text(encoding="utf-8"))
  if (type(config["schema_version"]) is not int or config["schema_version"] != 1
      or config["state"] != "assumed" or not config["basis"].strip() or foundation["frame"] != frame["frame"]):
    raise ValueError("Tied foundation study requires schema version 1, an assumed basis and a shared frame.")
  grid = AxisGrid(frame["frame"], **frame["grid"])
  runs, geometry, tie = _layout(config, grid, foundation)
  stiffness = _number(config["horizontal_soil_stiffness_kn_m"], "horizontal_soil_stiffness_kn_m")
  limits = {key: _number(config["limits"][key], key) for key in
            ("tension_kn", "compression_kn", "shear_kn", "moment_kn_m", "service_displacement_m", "strength_displacement_m")}
  matrix_cases = matrix_expectations(foundation, "foundation-ties")
  if config["expected"].keys() & matrix_cases.keys():
    raise ValueError("Explicit tie and seismic matrix cases must have distinct identities.")
  expectations = config["expected"] | matrix_cases
  if expectations.keys() != foundation["cases"].keys() or any(
    status not in ("pass", "fail") for status in expectations.values()
  ):
    raise ValueError("Tie screening expectations must cover exactly the independent footing cases.")
  modes = foundation["strength_combinations"]
  cases = {}
  for name, independent in foundation["cases"].items():
    record = {"independent_status": independent["status"], "expected": expectations[name]}
    if "footings" not in independent:
      cases[name] = {**record, "status": "fail", "checks": independent["checks"]}
      continue
    original = independent["footings"]
    footings, beams, residuals = deepcopy(original), {}, {}
    try:
      for run_name, run in runs.items():
        residuals[run_name] = {}
        for mode, factors in (("service", {"permanent": 1, "lateral": 1}), *modes.items()):
          factored_tie = FoundationTie(tie.axial_rigidity_kn, tie.flexural_rigidity_kn_m2, tie.height_m,
                                       tie.weight_kn_m * factors["permanent"])
          result = _solve_run(run, original, foundation["foundation_size_m"], factored_tie, stiffness, mode, factors["lateral"])
          residuals[run_name][mode] = result["max_residual_kn_or_kn_m"]
          for node, response in result["pads"].items():
            _update_footing(footings[node], original[node], response, run["axis"], mode, factors, foundation)
          for pair, beam in result["beams"].items():
            beams.setdefault("--".join(pair), {})[mode] = beam
    except TieEquilibriumError as error:
      cases[name] = {**record, "status": "fail", "checks": {"tied-static-equilibrium": False},
                     "failure": {"run": run_name, "combination": mode, "detail": str(error)}}
      continue
    checks, governing, differences = _summarize(
      footings, grid, foundation["settlement_limit_m"], foundation["differential_limit_ratio"]
    )
    checks["strength-sliding"] = _strength_sliding(footings, modes)
    ratios = {"tie-axial-flexure": {}, "tie-shear": {}, "tie-service-displacement": {}, "tie-strength-displacement": {}}
    for key, beam in beams.items():
      for mode in modes:
        item = beam[mode]
        axial = item["tension_kn"]
        ratios["tie-axial-flexure"][f"{key}/{mode}"] = (
          abs(axial) / limits["tension_kn" if axial >= 0 else "compression_kn"] + item["max_moment_kn_m"] / limits["moment_kn_m"]
        )
        ratios["tie-shear"][f"{key}/{mode}"] = item["max_shear_kn"] / limits["shear_kn"]
    for run in runs.values():
      for node in run["nodes"]:
        ratios["tie-service-displacement"][node] = abs(footings[node]["service"]["along_displacement_m"]) / limits["service_displacement_m"]
        for mode in modes:
          displacement = footings[node]["strength"][mode]["along_displacement_m"]
          ratios["tie-strength-displacement"][f"{node}/{mode}"] = abs(displacement) / limits["strength_displacement_m"]
    for check, values in ratios.items():
      key = max(values, key=values.__getitem__)
      checks[check] = values[key] <= 1 + 1e-8
      governing[check] = {"id": key, "utilization": values[key]}
    cases[name] = {**record, "status": "pass" if all(checks.values()) else "fail", "checks": checks, "footings": footings,
                   "governing": governing, "differential_settlement": differences, "beams": beams, "equilibrium_residuals": residuals}
  design_cases = foundation["design_screening"]["cases"]
  failures = [name for name in design_cases if cases[name]["status"] != "pass"]
  if "footings" not in cases.get(config["layout_case"], {}):
    raise ValueError("Tied foundation layout must reference a solved case.")
  layout_keys = ("frame", "nodes_xy_m", "foundation_size_m", "column_base_m", "column_base", "concrete_top_m", "bearing_plane_m")
  return {
    **{key: foundation[key] for key in layout_keys}, "schema_version": 1, "state": config["state"],
    "basis": config["basis"], "formal_review": "pending", "layout_case": config["layout_case"], "ties": geometry,
    "status": "pass" if all(case["status"] == case["expected"] for case in cases.values()) else "fail",
    "design_screening": {"status": "revision-required" if failures else "pass", "cases": design_cases, "failed_cases": failures},
    "independent_design_screening": foundation["design_screening"],
    "limits": limits, "beam_inputs": config["beam"], "horizontal_soil_stiffness_kn_m": stiffness, "cases": cases,
    "pending": ["site-soil-and-groundwater", "cyclic-friction-and-slip-history", "biaxial-grade-beam-grid",
                "grade-beam-rebar-anchorage-and-footing-interface", "eccentric-punching-and-local-reinforcement",
                "foundation-to-superstructure-coupling-and-seismic-combinations"],
  }
