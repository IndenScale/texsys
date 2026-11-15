"""Compile A/B critical-feeder routes from SDL terminals and placed roof geometry."""

import csv
from itertools import combinations
from math import dist, isclose, pi

from pxr import Sdf, Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .air_routing import SCENE as BASE_SCENE
from .architecture import MODEL as ARCHITECTURE, bounds, space
from .electrical import MODEL as TOPOLOGY
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _obstacles, _system_objects
from .spatial_design import (
  MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json,
)
from .telecom_coordination import _prim

MODEL = f"{MODELS}/power-routing.json"
LAYOUT = f"{MODELS}/layout.json"
SCENE = "scenes/aidc-power-coordinated.usda"
ROOF_SCENE = "scenes/aidc-power-roof.usda"
COLORS = {"A": (0.84, 0.46, 0.21), "B": (0.32, 0.56, 0.83)}


def _point(values):
  return tuple(round(value, 6) for value in values)


def _port(objects, endpoint):
  owner, name = endpoint.split(".")
  obj = objects[owner]
  return _point(obj["ports_m"][name]), tuple(obj["port_normals"][name])


def _direction(first, second):
  vector = tuple(b-a for a, b in zip(first, second, strict=True))
  axes = [index for index, value in enumerate(vector) if value]
  if len(axes) != 1:
    raise ValueError(f"Cable runs must be nonzero and orthogonal: {first} to {second}.")
  axis, = axes
  return axis, tuple(int(value / abs(value)) if value else 0 for value in vector)


def _box(first, second, width, depth):
  axis, _ = _direction(first, second)
  half_sizes = ((0, width/2, depth/2), (width/2, 0, depth/2), (width/2, depth/2, 0))[axis]
  return Space(tuple(min(a, b)-pad for a, b, pad in zip(first, second, half_sizes, strict=True)),
               tuple(max(a, b)+pad for a, b, pad in zip(first, second, half_sizes, strict=True)))


def _impedance(length, config, parallels=1):
  return {f"{term}_ohm": length * number(config[f"{term}_ohm_per_km"], term) / (1000 * parallels)
          for term in ("r", "x")}


def _route(side, train, routing, layout):
  carriers, cables = routing["carriers"], routing["cables"]
  segments, rows = {}, {}
  objects = layout["objects"]
  edges = {edge["id"]: edge for edge in train["edges"]}

  def segment(identity, start, end, carrier, links, *, endpoints=()):
    dimensions = carriers[carrier]
    width = number(dimensions["width_m"], f"{carrier} width")
    depth = number(dimensions["depth_m"], f"{carrier} depth")
    cable = cables["tap" if carrier == "drop" else "feed"]
    parallels = cable["parallel"]
    if type(parallels) is not int or parallels < 1:
      raise ValueError("Cable parallel count must be a positive integer.")
    diameter = number(cable["diameter_m"], "cable diameter")
    segments[identity] = {"points_m": [start, end], "length_m": dist(start, end),
                          "kind": carrier, "links": sorted(links), "contacts": list(endpoints),
                          "width_m": width, "depth_m": depth,
                          "fill_fraction": len(links)*parallels*pi*(diameter/2)**2 / (width*depth),
                          "single_layer_width_m": len(links)*parallels*diameter,
                          "cable_diameter_m": diameter, "envelope": bounds(_box(start, end, width, depth))}
    return identity

  incoming = {bus: edges[row["feed"]] for bus, row in train["feeders"].items()}
  starts = {edge["physical_from"] for edge in incoming.values()}
  source, = starts
  start, source_normal = _port(objects, source)
  entrances = {bus: _port(objects, row["physical_to"])[0] for bus, row in incoming.items()}
  elevations = {point[2] for point in entrances.values()}
  elevation, = elevations
  config = routing["trains"][side]
  elbow_y = number(config["yard_elbow_y_m"], "outdoor riser Y", zero=True)
  spine_x = number(config["roof_spine_x_m"], "roof spine X", zero=True)
  elbow, rise, head = (start[0], elbow_y, start[2]), (start[0], elbow_y, elevation), (spine_x, elbow_y, elevation)
  if _direction(start, elbow)[1] != source_normal:
    raise ValueError(f"{source}: cable egress does not match the container outlet.")
  feed_links = [item["id"] for item in incoming.values()]
  approach = [segment(f"{side}-P{i}", a, b, "main", feed_links,
                      endpoints=(source.split(".")[0],) if i == 1 else ())
              for i, (a, b) in enumerate(zip((start, elbow, rise), (elbow, rise, head), strict=True), 1)]
  order = sorted(entrances, key=lambda bus: entrances[bus][1], reverse=True)
  if not elbow_y > entrances[order[0]][1]:
    raise ValueError(f"Train {side}: outdoor elbow must be north of its first busway.")
  previous, spine = head, []
  for index, bus in enumerate(order):
    edge = incoming[bus]
    feed = edge["id"]
    entry = entrances[bus]
    point = (spine_x, entry[1], entry[2])
    spine.append(segment(f"{side}-S{index + 1}", previous, point, "main", [incoming[k]["id"] for k in order[index:]]))
    previous = point
    _, inlet_normal = _port(objects, edge["physical_to"])
    if _direction(entry, point)[1] != inlet_normal:
      raise ValueError(f"{bus}: branch does not enter opposite its inlet normal.")
    path = [*approach, *spine, segment(f"{side}-F{index + 1}", point, entry, "branch", [feed], endpoints=(bus,))]
    length = sum(segments[key]["length_m"] for key in path)
    bus_dim = space(objects[bus]).maximum_m[0] - space(objects[bus]).minimum_m[0]
    declared = train["nodes"][bus]["parameters"]["segment_length_m"]
    if declared["unit"] != "m" or not isclose(bus_dim, declared["value"], abs_tol=1e-5):
      raise ValueError(f"{bus}: installed envelope and declared busway length disagree.")
    taps = {}
    for terminal, identity in train["feeders"][bus]["terminals"].items():
      tap = edges[identity]
      first, first_normal = _port(objects, tap["physical_from"])
      second, second_normal = _port(objects, tap["physical_to"])
      if _direction(first, second)[1] != first_normal or _direction(second, first)[1] != second_normal:
        raise ValueError(f"{identity}: routed drop does not join the declared busway and pod roof ports.")
      drop = segment(f"{side}-{terminal}", first, second, "drop", [identity], endpoints=(bus, terminal))
      bus_distance = dist((entry[0], first[1], first[2]), first)
      cable_z = _impedance(segments[drop]["length_m"], cables["tap"], cables["tap"]["parallel"])
      bus_z = _impedance(bus_distance, routing["busway_ohm_per_km"])
      taps[terminal] = {"link": identity, "segment": drop, "from": tap["physical_from"], "to": tap["physical_to"],
                        "length_m": segments[drop]["length_m"], "bus_distance_m": bus_distance,
                        "cable_impedance": cable_z, "busway_impedance": bus_z,
                        "impedance": {key: cable_z[key]+bus_z[key] for key in cable_z}}
    rows[bus] = {"link": feed, "from": source, "to": edge["physical_to"], "segments": path, "length_m": length,
                 "entry_m": entry, "terminal_paths": taps,
                 "impedance": _impedance(length, cables["feed"], cables["feed"]["parallel"])}
  return {"segments": segments, "busways": rows}


def build_power_routing(context):
  source = source_path(context, "power-routing.json")
  if not source.is_file():
    return missing_input(context, source, "power-routing")
  config = read_versioned(source)
  if config.get("state") != "assumed" or not config.get("basis"):
    raise ValueError("Power installation inputs must declare their assumption basis.")
  layout, topology = staged_json(context, LAYOUT), staged_json(context, TOPOLOGY)
  if set(config["trains"]) != set(topology["trains"]):
    raise ValueError("Physical power routing must cover both declared supply trains.")
  return model_report(context, "power-routing", {
    "assumptions": config,
    "input_digests": {name: file_digest(context.build_path / name) for name in (LAYOUT, TOPOLOGY)},
    "trains": {side: _route(side, row, config, layout) for side, row in topology["trains"].items()},
  })


def _clash(issues, rule, first, second, detail):
  issues.append({"rule": rule, "status": "fail", "objects": [first, second], "message": detail})


def build_power_coordination(context):
  model = staged_json(context, MODEL)
  architecture = staged_json(context, ARCHITECTURE)
  layout = staged_json(context, LAYOUT)
  hall = space(architecture["hall"])
  obstacles = _obstacles(context, architecture)
  fire, _ = _system_objects(staged_json(context, FIRE))
  obstacles.update({key: volume for key, (volume, _) in fire.items()})
  sleeve = model["assumptions"]["penetration"]
  width = number(sleeve["sleeve_width_m"], "sleeve width")
  edge = number(sleeve["minimum_roof_edge_m"], "roof edge clearance")
  issues, penetrations = [], {}
  all_runs = [(side, name, row, space(row["envelope"]))
              for side, train in model["trains"].items() for name, row in train["segments"].items()]
  for side, name, row, volume in all_runs:
    for other, blocker in obstacles.items():
      if other not in row["contacts"] and volume.intersects(blocker):
        _clash(issues, "AIDC_POWER_SERVICE_CLASH", name, other, f"{name} intersects {other}.")
    if row["kind"] != "drop":
      continue
    _, pod = row["contacts"]
    x, y, pod_roof = row["points_m"][-1]
    roof_z = hall.maximum_m[2]
    pod_bounds = space(layout["objects"][pod])
    penetrations[name] = {"train": side, "pod": pod, "link": row["links"][0], "width_m": width,
                          "building_datum_m": [x, y, roof_z], "pod_roof_m": [x, y, pod_roof]}
    if not pod_roof < roof_z < row["points_m"][0][2] or width < max(row["width_m"], row["depth_m"]):
      _clash(issues, "AIDC_POWER_PENETRATION", name, pod, f"{name} does not fit both vertical roof interfaces.")
    for host, shape in (("IT-HALL", hall), (pod, pod_bounds)):
      if any(not lo+edge+width/2 <= center <= hi-edge-width/2
             for center, lo, hi in zip((x, y), shape.minimum_m[:2], shape.maximum_m[:2], strict=True)):
        _clash(issues, "AIDC_POWER_PENETRATION", name, host, f"{name} lacks roof edge clearance at {host}.")
    for z in (roof_z, pod_roof):
      sleeve_space = _box((x, y, z-0.04), (x, y, z+0.04), width, width)
      for other, blocker in obstacles.items():
        if other != pod and sleeve_space.intersects(blocker):
          _clash(issues, "AIDC_POWER_PENETRATION_CLASH", name, other, f"{name} roof sleeve intersects {other}.")
  for (side, name, row, volume), (other_side, other, other_row, blocker) in combinations(all_runs, 2):
    if side == other_side and set(map(tuple, row["points_m"])) & set(map(tuple, other_row["points_m"])):
      continue
    if volume.intersects(blocker):
      _clash(issues, "AIDC_POWER_ROUTE_SEPARATION", name, other, f"{name} and {other} occupy the same envelope.")
  report = model_report(context, "power-coordination", {
    "route_digest": file_digest(context.build_path / MODEL),
    "penetrations": penetrations, "runs_checked": len(all_runs), "obstacles_checked": len(obstacles), "issues": issues,
  }, issues)
  file = context.build_path / "schedules/aidc-power-penetrations.csv"
  file.parent.mkdir(parents=True, exist_ok=True)
  with file.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("drop", "train", "pod", "tap_sdl_link", "interface", "x_m", "y_m", "z_m", "sleeve_width_mm"))
    for key, row in penetrations.items():
      for field in ("building_datum_m", "pod_roof_m"):
        table.writerow((key, row["train"], row["pod"], row["link"], field, *row[field], 1000*row["width_m"]))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "power-penetration-schedule", file)),
                      report.diagnostics)


def _draw_routes(context, stage, model, report):
  preview = {}
  sdl = source_path(context, "system.sdl.json").relative_to(context.root).as_posix()
  design = source_path(context, "power-routing.json").relative_to(context.root).as_posix()
  for side, train in model["trains"].items():
    for key, row in train["segments"].items():
      name = key.replace("-", "_")
      prim_path = f"/AIDC/PowerRoutes/{side}/{name}"
      _prim(stage, prim_path, key, space(row["envelope"]), COLORS[side])
      stage.GetPrimAtPath(prim_path).CreateAttribute("texsys:sdlLinks", Sdf.ValueTypeNames.StringArray).Set(row["links"])
      preview[key] = (
        {"file": sdl, "path": f"connections/{row['links'][0]}"}
        if len(row["links"]) == 1 else {"file": design, "path": f"trains.{side}"}
      )
  for key, row in report["penetrations"].items():
    side = row["train"]
    width = row["width_m"]
    for building, point in (("Building", row["building_datum_m"]), ("Pod", row["pod_roof_m"])):
      x, y, z = point
      identity = f"{key}-{building}"
      shape = _box((x, y, z-.04), (x, y, z+.04), width, width)
      _prim(stage, f"/AIDC/PowerRoutes/{side}/{identity.replace('-', '_')}", identity, shape, COLORS[side])
      preview[identity] = {"file": sdl, "path": f"connections/{row['link']}"}
  return preview


def build_power_scene(context):
  model = staged_json(context, MODEL)
  report = staged_json(context, f"{MODELS}/power-coordination.json")
  layout = staged_json(context, LAYOUT)
  federated = Usd.Stage.Open(str(context.build_path / BASE_SCENE))
  if federated is None:
    raise ValueError(f"Cannot open {BASE_SCENE}.")
  roof = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageUpAxis(roof, UsdGeom.Tokens.z)
  UsdGeom.SetStageMetersPerUnit(roof, 1)
  roof.SetDefaultPrim(UsdGeom.Xform.Define(roof, "/AIDC").GetPrim())
  equipment = {}
  for side, train in model["trains"].items():
    for bus, row in train["busways"].items():
      equipment[bus] = COLORS[side]
      for name in (*row["terminal_paths"], row["from"].split(".")[0]):
        equipment[name] = (.55, .60, .64)
  for name, color in equipment.items():
    placed = layout["objects"][name]
    _prim(roof, f"/AIDC/Equipment/{name}", placed["semantic_ref"], space(placed), color)
  artifacts = []
  for stage, destination, identity in ((federated, SCENE, "power-scene"), (roof, ROOF_SCENE, "power-roof-scene")):
    preview = {**layout["source_map"], **_draw_routes(context, stage, model, report)}
    file = context.build_path / destination
    if not stage.Flatten(False).Export(str(file)):
      raise OSError(f"Cannot export {file.name}.")
    artifacts.append(artifact(context, identity, file, preview={"kind": "scene", "source_map": preview}))
  return TargetResult("pass", tuple(artifacts))
