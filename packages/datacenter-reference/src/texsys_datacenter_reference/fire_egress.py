"""Evaluate explicit egress routes against placed equipment and structure."""

import csv
import json
import math
from itertools import pairwise

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH_MODEL, space
from .pod_layout import MODEL as POD_LAYOUT
from .spatial_design import MODELS, artifact, missing_input, model_report, number, source_path, staged_json
from .structure import MODEL as FRAME_MODEL


MODEL = f"{MODELS}/egress-model.json"


def _issue(rule, objects, message):
  return {"rule": rule, "status": "fail", "objects": objects, "message": message}


def _obstacles(architecture, layout, frame):
  walls = {key: space(row) for key, row in architecture["walls"].items()}
  placed = {key: space(row) for key, row in layout["objects"].items() if row["discipline"] not in {"architecture", "structure"}}
  parts = {f"{key}/{part['identity']}": Space(tuple(part["origin"]), tuple(a+b for a, b in zip(part["origin"], part["size"], strict=True)))
           for key, record in frame["members"].items() for part in record["parts"]}
  return walls | placed | parts


def _swept(a, b, width, height):
  ax, ay, az = a
  bx, by, bz = b
  if ax == bx and ay == by:
    return None
  if ax != bx and ay != by:
    raise ValueError("AIDC declared egress centerlines must be orthogonal in plan.")
  hx, hy = (width/2, 0) if ax == bx else (0, width/2)
  return Space((min(ax, bx)-hx, min(ay, by)-hy, min(az, bz)),
               (max(ax, bx)+hx, max(ay, by)+hy, max(az, bz)+height))


def _blocked(points, width, height, obstacles):
  sweeps = [_swept(a, b, width, height) for a, b in pairwise(points)]
  return sorted(name for name, shape in obstacles.items() if any(sweep and sweep.intersects(shape) for sweep in sweeps))


def _exit(tag, door, site, limits, obstacles):
  y = door["inside_m"][1]
  path = [door["inside_m"], *[[x, y, z] for x, z in door["landing_stations_m"]]]
  xmin, ymin, xmax, ymax = site["boundary_m"]
  half = door["ramp_width_m"]/2
  inside_parcel = ymin <= y-half < y+half <= ymax and all(xmin <= x <= xmax for x, _, _ in path)
  clear_width, clear_height = limits["minimum_clear_width_m"], limits["minimum_clear_height_m"]
  blockers = _blocked(path, clear_width, clear_height, obstacles)
  checks = {"available": door["available"], "door-width": door["clear_width_m"] >= clear_width,
            "door-height": door["clear_height_m"] >= clear_height, "within-parcel": inside_parcel,
            "ramp-slope": door["ramp_slope"] <= limits["maximum_ramp_slope"],
            "ramp-width": door["ramp_width_m"] >= door["clear_width_m"],
            "upper-landing": door["upper_landing_m"] >= door["clear_width_m"]/door["leaf_count"],
            "unobstructed": not blockers}
  return {"door": tag, "points_m": path, "blockers": blockers, "checks": checks, "pass": all(checks.values()),
          "grade_path_m": sum(math.dist(a, b) for a, b in pairwise(path))}


def _path(pod, pod_door, exit_record, y, floor, limits, obstacles):
  door, spec = pod_door
  x, opening, entrance = spec["world_m"]
  clear_lo, clear_hi = pod.minimum_m[1], pod.maximum_m[1]
  outward = max(0., entrance-floor)
  faces_aisle = math.isclose(opening, clear_hi if pod.center_m[1] < y else clear_lo)
  access = [x, opening, floor]
  turn = [x, y, floor]
  portal = exit_record["points_m"][0]
  indoor = [access, turn, portal]
  distance = sum(math.dist(a, b) for a, b in pairwise(indoor))
  width, height = limits["minimum_clear_width_m"], limits["minimum_clear_height_m"]
  swept_turn = Space((x-width/2, y-width/2, floor), (x+width/2, y+width/2, floor+height))
  blockers = sorted(set(_blocked(indoor, width, height, obstacles))
                    | {name for name, shape in obstacles.items() if swept_turn.intersects(shape)})
  checks = {"opens-to-aisle": faces_aisle and not clear_lo < y < clear_hi,
            "pod-opening": spec["clear_width_m"] >= width and spec["clear_height_m"] >= height,
            "floor-transition": outward <= limits["walking_floor_allowance_m"] + 1e-9,
            "building-distance": distance <= limits["design_travel_limit_m"],
            "unobstructed": not blockers, "exit-usable": exit_record["pass"]}
  return {"exit": exit_record["door"], "pod_door": door, "access_m": access, "points_m": [*indoor, *exit_record["points_m"][1:]],
          "inside_to_exit_m": distance, "access_to_grade_m": distance + exit_record["grade_path_m"],
          "blockers": blockers, "checks": checks, "pass": all(checks.values())}


def build_egress_model(context):
  arch = staged_json(context, ARCH_MODEL)
  site = staged_json(context, f"{MODELS}/site.json")
  layout = staged_json(context, f"{MODELS}/layout.json")
  frame = staged_json(context, FRAME_MODEL)
  pod_layout = staged_json(context, POD_LAYOUT)["pods"]
  limits = {key: number(arch["egress"][key], key) for key in ("design_travel_limit_m", "minimum_clear_width_m",
                                                            "minimum_clear_height_m", "maximum_ramp_slope",
                                                            "walking_floor_allowance_m")}
  obstacles = _obstacles(arch, layout, frame)
  exits = {name: _exit(name, door, site, limits, obstacles) for name, door in arch["doors"].items()}
  compartments = {name: space(room) for name, room in arch["rooms"].items()}
  destinations = {name: [] for name in compartments}
  routes, issues = {}, []
  for name, record in sorted(layout["objects"].items()):
    if record["module"].split("/")[-2] != "it-container" or record["zone"] != arch["zone"]:
      continue
    pod = space(record)
    candidates = [key for key, envelope in compartments.items() if envelope.contains_space(pod)]
    if len(candidates) != 1:
      issues.append(_issue("AIDC_FIRE_COMPARTMENT", [name], f"{name} must fit wholly in one fire compartment."))
      continue
    room, = candidates
    destinations[room].append(name)
    datum = arch["rooms"][room]
    pod_door = min(pod_layout[name]["doors"].items(), key=lambda item: abs(item[1]["world_m"][1]-datum["aisle_y_m"]))
    paths = [_path(pod, pod_door, exits[door], datum["aisle_y_m"], datum["minimum_m"][2], limits, obstacles)
             for door in datum["exits"]]
    valid = sorted((path["inside_to_exit_m"], path["exit"]) for path in paths if path["pass"])
    routes[name] = {"compartment": room, "paths": paths, "usable_exits": [door for _, door in valid],
                    "nearest_exit_m": valid[0][0] if valid else None}
    if len(valid) < 2:
      obstructions = sorted({item for path in paths for item in path["blockers"]})
      issues.append(_issue("AIDC_EGRESS_TWO_EXITS", [name, room, *obstructions],
                           f"{name}: only {len(valid)} of two egress routes are usable."))
  for name, record in exits.items():
    failed = [key for key, passed in record["checks"].items() if not passed]
    if failed:
      issues.append(_issue("AIDC_EGRESS_EXIT", [name, *record["blockers"]], f"{name}: {', '.join(failed)}."))
  for room, members in destinations.items():
    if not members:
      issues.append(_issue("AIDC_FIRE_COMPARTMENT_EMPTY", [room], f"{room} needs placed hall equipment."))
  coverage = "Hall and discharge path lengths from modeled aisle-facing pod doors; interior access is checked by pod coordination."
  report = model_report(context, "egress-model", {"architecture_digest": file_digest(context.build_path / ARCH_MODEL),
                        "limits": limits, "exits": exits, "compartment_occupancy": destinations,
                        "routes": routes, "issues": issues, "coverage": coverage})
  output = context.build_path / "schedules/aidc-egress-routes.csv"
  output.parent.mkdir(parents=True, exist_ok=True)
  with output.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("origin", "compartment", "exit", "inside_to_exit_m", "access_to_grade_m", "usable", "obstructions"))
    for name, row in routes.items():
      for path in row["paths"]:
        table.writerow((name, row["compartment"], path["exit"], round(path["inside_to_exit_m"], 3),
                        round(path["access_to_grade_m"], 3), path["pass"], " ".join(path["blockers"])))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "egress-routes", output)), report.diagnostics)


def build_egress_check(context):
  basis_file = source_path(context, "design-basis.json")
  if not basis_file.is_file():
    return missing_input(context, basis_file, "fire-egress")
  arch = staged_json(context, ARCH_MODEL)
  model = staged_json(context, MODEL)
  basis = json.loads(basis_file.read_text(encoding="utf-8"))
  requirement, = [row for row in basis["requirements"] if row["id"] == "GB-13.2.4"]
  criteria = requirement["parameters"]
  issues = list(model["issues"])
  wall_min = number(criteria["min_wall_fire_hours"], "design-basis wall hours")
  slab_min = number(criteria["min_slab_fire_hours"], "design-basis slab hours")
  insufficient = [key for key, wall in arch["walls"].items() if wall["fire_hours"] < wall_min]
  if insufficient or arch["slab"]["fire_hours"] < slab_min:
    issues.append(_issue("AIDC_FIRE_RATING_BASIS", insufficient, "Declared wall or slab hours fall below the design basis."))
  return model_report(context, "fire-egress", {"egress_digest": file_digest(context.build_path / MODEL),
                      "rating_basis": {"project_requirement": requirement["id"], "minimum_wall_hours": wall_min,
                                       "minimum_slab_hours": slab_min, "source_state": requirement["state"]},
                      "checked_routes": sum(len(row["paths"]) for row in model["routes"].values()),
                      "nearest_exit_max_m": max((row["nearest_exit_m"] for row in model["routes"].values()
                                                 if row["nearest_exit_m"] is not None), default=None),
                      "issues": issues, "coverage": model["coverage"]}, issues)
