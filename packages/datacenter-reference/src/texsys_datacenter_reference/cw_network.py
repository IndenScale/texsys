"""Compile closed condenser-water circuits directly from placed SDL ports."""

import csv
import json
from itertools import combinations, pairwise
from math import cos, isclose, isfinite, sin, tau

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .architecture import MODEL as ARCH_MODEL, space
from .fire_systems import MODEL as FIRE_MODEL
from .fire_systems_coordination import SCENE as BASE_SCENE, _obstacles, _system_objects, _tube
from .fws_network import MODEL as FWS_MODEL
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


MODEL = f"{MODELS}/cw-network.json"
SCENE = "scenes/aidc-cooling-water-coordinated.usda"
ROLES = ("hot", "discharge", "return")
COLORS = {"hot": (.75, .31, .19), "discharge": (.75, .31, .19), "return": (.19, .46, .64)}


def _port(layout, endpoint):
  name, port = endpoint.split(".")
  record = layout[name]
  point = record["ports_m"][port]
  normal = record["port_normals"][port]
  axes = [i for i, value in enumerate(normal) if abs(value) > 1e-8]
  if len(axes) != 1 or not isclose(abs(normal[axes[0]]), 1.):
    raise ValueError(f"{endpoint}: expected an orthogonal exterior flange normal.")
  i, = axes
  face = record["maximum_m" if normal[i] > 0 else "minimum_m"][i]
  if not isclose(point[i], face, abs_tol=1e-7):
    raise ValueError(f"{endpoint}: the modeled flange must be on the equipment envelope.")
  return point, normal


def _axis(a, b):
  active = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if not isclose(x, y, abs_tol=1e-8)]
  if len(active) != 1:
    raise ValueError(f"Adjacent CW route points must differ in exactly one axis: {a} to {b}.")
  return active[0]


def _route(layout, origin, edge, waypoints, envelope):
  start, start_normal = _port(layout, edge["from"])
  end, end_normal = _port(layout, edge["to"])
  anchors = {"from": start, "to": end}
  if any(len(point) != 3 or any(value not in anchors if isinstance(value, str)
                               else type(value) not in (float, int) or not isfinite(value)
                               for value in point) for point in waypoints):
    raise ValueError(f"{edge['id']}: specify three finite local offsets or flange coordinates per waypoint.")
  points = [start, *[[anchors[value][axis] if isinstance(value, str) else origin[axis]+value
                      for axis, value in enumerate(point)] for point in waypoints], end]
  axes = [_axis(a, b) for a, b in pairwise(points)]
  for point, adjacent, normal, axis in ((start, points[1], start_normal, axes[0]),
                                        (end, points[-2], end_normal, axes[-1])):
    if (adjacent[axis] - point[axis])*normal[axis] < envelope:
      raise ValueError(f"{edge['id']}: straight lead at {point} must extend one pipe envelope along the flange normal.")
  if any(a == b for a, b in pairwise(axes)):
    raise ValueError(f"{edge['id']}: route has a collinear or reversing waypoint.")
  lengths = [abs(b[i] - a[i]) for i, (a, b) in zip(axes, pairwise(points), strict=True)]
  if min(lengths) < envelope:
    raise ValueError(f"{edge['id']}: straight segments must be at least one pipe envelope long.")
  return {"connection": edge["id"], "from": edge["from"], "to": edge["to"], "points_m": points,
          "length_m": sum(lengths), "elbows": len(lengths)-1}


def _circuit(system, cfg, layout, pipe):
  root = system["components"][system["root"]]
  net = root["nets"][cfg["net"]]
  edges = {(edge["from"], edge["to"]): edge for edge in root["connections"] if edge["id"] in net["connections"]}
  if net["kind"] != "cw" or len(edges) != len(net["connections"]):
    raise ValueError("CW requires a closed condenser-water SDL net with unique directed connections.")
  plants = {name for name, obj in layout.items() if obj["module"].split("/")[-2] == "cooling-container"}
  if not plants or set(cfg["cells"]) != plants:
    raise ValueError("Every placed cooling container requires exactly one cooling-water circuit.")
  cells, routes = {}, {}
  equipment = set()
  for plant, cell in sorted(cfg["cells"].items()):
    pump, tower = cell["pump"], cell["tower"]
    if pump in equipment or tower in equipment:
      raise ValueError("Each cooling cell requires its own pump and tower.")
    equipment.update((pump, tower))
    cells[plant] = {**cell, "routes": {}}
    directed = ((f"{plant}.cw_out", f"{pump}.in"), (f"{pump}.out", f"{tower}.cw_in"),
                (f"{tower}.cw_out", f"{plant}.cw_in"))
    for role, endpoints in zip(ROLES, directed, strict=True):
      edge = edges.pop(endpoints, None)
      if edge is None:
        raise ValueError(f"{plant}: SDL is missing the {role} connection {endpoints}.")
      route = _route(layout, layout[plant]["origin_m"], edge, cfg["waypoints_from_plant_origin_m"][role], pipe["envelope_m"])
      key = edge["id"]
      cells[plant]["routes"][role] = key
      routes[key] = {**route, "role": role, "plant": plant, "minor_k": pipe["elbow_k"]*route["elbows"]}
  if edges:
    raise ValueError("CW physical routing must cover every SDL edge in the declared net.")
  return cells, routes


def build_cw_network(context):
  source = source_path(context, "cw-network.json")
  system = source_path(context, "system.sdl.json")
  for path in (source, system):
    if not path.is_file():
      return missing_input(context, path, "cw-network")
  cfg = read_versioned(source)
  if cfg.get("state") != "assumed" or not isinstance(cfg.get("basis"), str) or not cfg["basis"].strip():
    raise ValueError("CW design requires an explicit assumed engineering basis.")
  spec = cfg["pipe"]
  pipe = {name: number(spec[name], name, zero=name in {"roughness_m", "insulation_m", "elbow_k"})
          for name in ("inside_m", "outside_m", "insulation_m", "roughness_m", "max_velocity_m_s", "elbow_k")}
  if not 0 <= pipe["roughness_m"] < pipe["inside_m"] < pipe["outside_m"]:
    raise ValueError("CW pipe requires roughness below bore, and bore below outside diameter.")
  pipe.update(designation=spec["designation"], envelope_m=pipe["outside_m"]+2*pipe["insulation_m"])
  layout = staged_json(context, f"{MODELS}/layout.json")
  cells, routes = _circuit(json.loads(system.read_text(encoding="utf-8")), cfg, layout["objects"], pipe)
  report = model_report(context, "cw-network", {"basis": cfg["basis"], "cells": cells, "routes": routes,
                                               "pipe": pipe, "frame": layout["frame"]})
  schedule = context.build_path / "schedules/aidc-cw-pipes.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("connection", "cell", "role", "from", "to", "size", "centerline_m", "elbows", "minor_k"))
    for key, row in routes.items():
      table.writerow((key, row["plant"], row["role"], row["from"], row["to"], pipe["designation"],
                      f"{row['length_m']:.3f}", row["elbows"], f"{row['minor_k']:.3f}"))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "cw-pipes", schedule)))


def _segments(model):
  diameter = model["pipe"]["envelope_m"]
  for key, route in model["routes"].items():
    points = route["points_m"]
    for index, (a, b) in enumerate(pairwise(points)):
      axis = _axis(a, b)
      radius = diameter/2
      low, high = list(a), list(b)
      direction = 1 if b[axis] > a[axis] else -1
      if index > 0:
        low[axis] -= direction*radius
      if index < len(points)-2:
        high[axis] += direction*radius
      yield key, index, _tube({"points_m": (low, high), "outside_diameter_m": diameter})


def _pipe_prim(stage, path, key, points_m, diameter, color, *, sides=16, caps=True):
  mesh = UsdGeom.Mesh.Define(stage, path)
  points = [Gf.Vec3d(*point) for point in points_m]
  directions = [(b-a).GetNormalized() for a, b in pairwise(points)]
  reference = Gf.Vec3d(0, 0, 1) if directions[0][2] == 0 else Gf.Vec3d(1, 0, 0)
  transverse = Gf.Cross(directions[0], reference).GetNormalized()
  normal = Gf.Cross(directions[0], transverse)
  radius = diameter/2
  vertices, faces = [], []
  for index, point in enumerate(points):
    before = directions[max(0, index-1)]
    after = directions[min(index, len(directions)-1)]
    bisector = before+after

    def miter(radial):
      return radial-before*(Gf.Dot(radial, bisector)/Gf.Dot(before, bisector))

    for side in range(sides):
      angle = tau*side/sides
      offset = miter(transverse*cos(angle)+normal*sin(angle))
      vertices.append(Gf.Vec3f(*(point+radius*offset)))
    if index:
      low, high = (index-1)*sides, index*sides
      faces.extend([low+i, low+(i+1)%sides, high+(i+1)%sides, high+i] for i in range(sides))
    projected = miter(transverse)
    transverse = (projected-after*Gf.Dot(projected, after)).GetNormalized()
    normal = Gf.Cross(after, transverse)
  if caps:
    faces.extend((list(reversed(range(sides))), list(range(len(vertices)-sides, len(vertices)))))
  mesh.CreatePointsAttr(vertices)
  mesh.CreateFaceVertexCountsAttr([len(face) for face in faces])
  mesh.CreateFaceVertexIndicesAttr([index for face in faces for index in face])
  mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
  mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  mesh.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(key)

def build_cw_coordination(context):
  model = staged_json(context, MODEL)
  arch = staged_json(context, ARCH_MODEL)
  fire, _ = _system_objects(staged_json(context, FIRE_MODEL))
  obstacles = _obstacles(context, arch)
  water = staged_json(context, FWS_MODEL)
  obstacles.update({f"{name}/{part}": space(row[part]) for name, row in water["buffers"].items()
                    for part in ("body", "foundation", "pedestal", "service_clearance")})
  obstacles.update({key: shape for key, (shape, _) in fire.items()})
  segments = list(_segments(model))
  conflicts = [[f"{key}/{index+1}", obstacle] for key, index, shape in segments
               for obstacle, obstacle_shape in obstacles.items() if shape.intersects(obstacle_shape)]
  x0, y0, x1, y1 = staged_json(context, f"{MODELS}/site.json")["buildable_m"]
  layout = staged_json(context, f"{MODELS}/layout.json")
  for key, index, shape in segments:
    a, b, z = shape.minimum_m
    c, d, _ = shape.maximum_m
    grade = layout["objects"][model["routes"][key]["plant"]]["origin_m"][2]
    if not (x0 <= a < c <= x1 and y0 <= b < d <= y1 and z >= grade):
      conflicts.append([f"{key}/{index+1}", "above-grade buildable zone"])
  for first, second in combinations(segments, 2):
    key, index, shape = first
    other, second_index, other_shape = second
    if key == other and abs(index-second_index) == 1:
      continue
    if shape.intersects(other_shape):
      conflicts.append([f"{key}/{index+1}", f"{other}/{second_index+1}"])
  issues = [{"rule": "AIDC_CW_CLEARANCE", "status": "fail", "objects": pair,
             "message": f"{pair[0]} overlaps {pair[1]}."} for pair in conflicts]
  report = model_report(context, "cw-coordination", {
    "route_digest": file_digest(context.build_path / MODEL), "base_digest": file_digest(context.build_path / BASE_SCENE),
    "segment_count": len(segments), "obstacle_count": len(obstacles), "issues": issues,
    "coverage": "Insulated orthogonal CW envelope against modeled obstacles and other CW runs.",
  }, issues)
  if issues:
    return report
  stage = Usd.Stage.Open(str(context.build_path / BASE_SCENE))
  if stage is None:
    raise ValueError("Cannot open the federated source scene.")
  for key, route in model["routes"].items():
    path = f"/AIDC/CoolingWater/{key.replace('-', '_')}"
    _pipe_prim(stage, path, key, route["points_m"], model["pipe"]["envelope_m"], COLORS[route["role"]])
  scene = context.build_path / SCENE
  if not stage.Flatten(False).Export(str(scene)):
    raise OSError(f"Could not export {scene.name}.")
  path = source_path(context, "cw-network.json").relative_to(context.root).as_posix()
  refs = {key: {"file": path, "path": f"waypoints_from_plant_origin_m.{route['role']}"}
          for key, route in model["routes"].items()}
  preview = {"kind": "scene", "source_map": {**layout["source_map"], **refs}}
  return TargetResult(report.status, (*report.artifacts, artifact(context, "cw-coordination-scene", scene, preview=preview)))
