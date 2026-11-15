"""Check physically diverse trays and reserve modeled wall openings."""

import csv
from itertools import combinations
import math

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH_MODEL, bounds, space
from .fws_network import MODEL as WATER_MODEL, pipe_envelope
from .spatial_design import MODELS, artifact, model_report, staged_json
from .structure import MODEL as FRAME_MODEL
from .telecom import MODEL, tray_space


COORDINATION = f"{MODELS}/telecom-coordination.json"
SCENE = "scenes/aidc-telecom-coordinated.usda"


def _issue(rule, a, b, message):
  return {"rule": rule, "status": "fail", "objects": [a, b], "message": message}


def _distance(a, b):
  gaps = [max(0, c0-b1, b0-c1) for b0, b1, c0, c1 in zip(a.minimum_m, a.maximum_m, b.minimum_m, b.maximum_m, strict=True)]
  return math.hypot(*gaps)


def _aperture(run, wall, allowance):
  axis = min(range(2), key=lambda i: wall.size_m[i])
  if axis != run["axis"]:
    return None
  tray = tray_space(run)
  if not tray.minimum_m[axis] < wall.minimum_m[axis] < wall.maximum_m[axis] < tray.maximum_m[axis]:
    return None
  lo = tuple(wall.minimum_m[i] if i == axis else tray.minimum_m[i]-allowance for i in range(3))
  hi = tuple(wall.maximum_m[i] if i == axis else tray.maximum_m[i]+allowance for i in range(3))
  opening = Space(lo, hi)
  # Do not cut a free edge or a horizontal joint into a slot.
  if any(not wall.minimum_m[i] < lo[i] < hi[i] < wall.maximum_m[i] for i in range(3) if i != axis):
    return None
  return axis, opening


def _cut_wall(wall, opening, normal):
  lateral = 1-normal
  result = []
  for axis in (lateral, 2):
    lo, hi = list(wall.minimum_m), list(wall.maximum_m)
    # Top/bottom faces are placed only between jambs to keep the pieces disjoint.
    if axis == 2:
      lo[lateral], hi[lateral] = opening.minimum_m[lateral], opening.maximum_m[lateral]
    before, after = hi.copy(), lo.copy()
    before[axis] = opening.minimum_m[axis]
    after[axis] = opening.maximum_m[axis]
    result.extend((Space(tuple(lo), tuple(before)), Space(tuple(after), tuple(hi))))
  return result


def _prim(stage, path, identity, shape, color):
  cube = UsdGeom.Cube.Define(stage, path)
  cube.CreateSizeAttr(1)
  cube.AddTranslateOp().Set(Gf.Vec3d(*shape.center_m))
  cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*shape.size_m))
  cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  cube.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)


def _add_scene(context, runs, cabinets, walls, openings):
  input_scene = context.build_path / "scenes/aidc-building-coordinated.usda"
  stage = Usd.Stage.Open(str(input_scene))
  if stage is None:
    raise ValueError("The staged building coordination layer is not readable.")
  for wall, shape in walls.items():
    entries = [(entry["axis"], space(entry)) for entry in openings.values() if entry["wall"] == wall]
    if not entries:
      continue
    pieces = [shape]
    for axis, aperture in entries:
      host, = [piece for piece in pieces if piece.contains_space(aperture)]
      pieces.remove(host)
      pieces.extend(_cut_wall(host, aperture, axis))
    path = f"/AIDC/Architecture/{wall.replace('-', '_')}"
    stage.RemovePrim(path)
    UsdGeom.Xform.Define(stage, path).GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(wall)
    for i, cut in enumerate(pieces, 1):
      _prim(stage, f"{path}/Pier_{i:02}", wall, cut, (.72, .73, .71))
  for label, record in runs.items():
    tone = (.45, .24, .74) if record["plane"] == "A" else (.24, .58, .36)
    _prim(stage, f"/AIDC/Telecom/{label.replace('-', '_')}", label, tray_space(record), tone)
  for label, cabinet in cabinets.items():
    _prim(stage, f"/AIDC/Telecom/Cabinet_{label}", cabinet["id"], space(cabinet), (.33, .39, .44))
  output = context.build_path / SCENE
  if not stage.Flatten(False).Export(str(output)):
    raise OSError(f"Could not export {output.name}.")
  return output


def _fire_clearance(architecture):
  corridors = {}
  for key, room in architecture["rooms"].items():
    x0, _, z0 = room["minimum_m"]
    x1, _, _ = room["maximum_m"]
    y = room["aisle_y_m"]
    half = architecture["egress"]["minimum_clear_width_m"]/2
    corridors[key] = Space((x0, y-half, z0), (x1, y+half, z0+architecture["egress"]["minimum_clear_height_m"]))
  for key, door in architecture["doors"].items():
    x, y, z = door["inside_m"]
    discharge = door["discharge_m"]
    width = door["ramp_width_m"]/2
    corridors[key] = Space((min(x, discharge[0]), y-width, discharge[2]),
                           (max(x, discharge[0]), y+width, z+architecture["egress"]["minimum_clear_height_m"]))
  return corridors


def _openings(model, runs, obstructions, walls, issues):
  openings = {}
  for key, run in runs.items():
    envelope = tray_space(run)
    for blocker, volume in obstructions.items():
      if envelope.intersects(volume):
        issues.append(_issue("AIDC_TELECOM_CLASH", key, blocker, f"{key} tray clashes with {blocker}."))
    for wall, volume in walls.items():
      if not envelope.intersects(volume):
        continue
      allocated = _aperture(run, volume, model["tray"]["opening_allowance_m"])
      if allocated is None:
        issues.append(_issue("AIDC_TELECOM_WALL", key, wall, f"{key}: no full opening fits within {wall}."))
        continue
      normal, aperture = allocated
      tag = f"OP-{key}-{wall}"
      openings[tag] = {**bounds(aperture), "axis": normal, "wall": wall, "run": key, "plane": run["plane"],
                      "opening_size_m": [aperture.size_m[1-normal], aperture.size_m[2]], "center_m": aperture.center_m,
                      "assembly_state": "pending-firestop-selection"}
      for blocker, body in obstructions.items():
        if aperture.intersects(body):
          issues.append(_issue("AIDC_TELECOM_OPENING_CLASH", tag, blocker, f"{tag} reservation overlaps {blocker}."))
  for (a, first), (b, second) in combinations(sorted(openings.items()), 2):
    if first["wall"] == second["wall"] and space(first).intersects(space(second)):
      issues.append(_issue("AIDC_TELECOM_OPENING_OVERLAP", a, b, f"{a} and {b} are not independent wall openings."))
  return openings


def build_telecom_coordination(context):
  model = staged_json(context, MODEL)
  architecture = staged_json(context, ARCH_MODEL)
  frame = staged_json(context, FRAME_MODEL)
  water = staged_json(context, WATER_MODEL)
  layout = staged_json(context, f"{MODELS}/layout.json")
  site = staged_json(context, f"{MODELS}/site.json")
  walls = {key: space(row) for key, row in architecture["walls"].items()}
  obstacles = {f"{key}/{part['identity']}": Space(tuple(part["origin"]), tuple(a+b for a, b in zip(part["origin"], part["size"], strict=True)))
               for key, record in frame["members"].items() for part in record["parts"]}
  obstacles.update({name: space(row) for name, row in layout["objects"].items() if row["discipline"] not in {"architecture", "structure"}})
  obstacles.update({key: pipe_envelope(pipe) for key, pipe in water["segments"].items()})
  obstacles.update({name: Space(tuple(x-d/2 for x, d in zip(row["point_m"], row["size_m"], strict=True)),
                                tuple(x+d/2 for x, d in zip(row["point_m"], row["size_m"], strict=True)))
                    for name, row in water["valves"].items()})
  obstacles.update(_fire_clearance(architecture))
  runs = {key: row for assembly in model["planes"].values() for key, row in assembly["runs"].items()}
  cabinets = {key: row["cabinet"] for key, row in model["planes"].items()}
  xmin, ymin, xmax, ymax = site["boundary_m"]
  issues = []
  obstacles_with_walls = obstacles | walls
  for record in cabinets.values():
    key, envelope = record["id"], space(record)
    a, b, _ = envelope.minimum_m
    c, d, _ = envelope.maximum_m
    if not (xmin <= a < c <= xmax and ymin <= b < d <= ymax):
      issues.append(_issue("AIDC_TELECOM_SITE", key, "parcel", f"{key} lies outside the parcel."))
    for other, blocker in obstacles_with_walls.items():
      if envelope.intersects(blocker):
        issues.append(_issue("AIDC_TELECOM_CABINET_CLASH", key, other, f"{key} cabinet clashes with {other}."))
  openings = _openings(model, runs, obstacles, walls, issues)
  envelopes = {key: tray_space(run) for key, run in runs.items()}
  gap = math.inf
  clearance = model["tray"]["interplane_clearance_m"]
  for a, b in combinations(sorted(runs), 2):
    first, second = runs[a], runs[b]
    if first["plane"] == second["plane"]:
      connected = set(first["nodes"]) & set(second["nodes"])
      if not connected and envelopes[a].intersects(envelopes[b]):
        issues.append(_issue("AIDC_TELECOM_TRAY_INTERSECTION", a, b, f"{a} and {b} overlap away from a common fitting."))
      continue
    actual = _distance(envelopes[a], envelopes[b])
    gap = min(gap, actual)
    if actual < clearance-1e-8:
      issues.append(_issue("AIDC_TELECOM_PLANE_SEPARATION", a, b, f"{a} and {b} have {actual:.3f} m separation."))
  values = {"source_digests": {path: file_digest(context.build_path / path) for path in (MODEL, ARCH_MODEL, WATER_MODEL, FRAME_MODEL)},
            "issues": issues, "minimum_interplane_clearance_m": gap, "wall_openings": openings,
            "coverage": "Orthogonal support envelopes and reservations versus modeled steel, FWS, walls and clear egress volumes."}
  report = model_report(context, "telecom-coordination", values, issues)
  # A conflicted aperture must never be presented as an approved cut in the combined scene.
  scene_artifacts = [] if issues else [artifact(context, "telecom-scene", _add_scene(context, runs, cabinets, walls, openings))]
  schedule = context.build_path / "schedules/aidc-telecom-wall-openings.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("reservation", "plane", "tray", "wall", "x_m", "y_m", "z_m", "width_mm", "height_mm", "assembly_state"))
    for tag, row in sorted(openings.items()):
      table.writerow((tag, row["plane"], row["run"], row["wall"], *[round(value, 5) for value in row["center_m"]],
                      *[round(value*1000, 2) for value in row["opening_size_m"]], row["assembly_state"]))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "telecom-openings", schedule), *scene_artifacts),
                      report.diagnostics)
