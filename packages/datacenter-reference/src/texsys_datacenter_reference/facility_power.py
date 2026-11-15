"""Compile installed A/B feeder banks and independent cooling-cell motor branches."""

from itertools import combinations, pairwise
from math import dist, pi

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .architecture import MODEL as ARCHITECTURE, bounds, space
from .cw_network import MODEL as CW, _segments as cw_segments
from .electrical import MODEL as TOPOLOGY
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _obstacles, _system_objects
from .power_routing import LAYOUT, MODEL as CRITICAL, _box, _direction, _point, _port
from .roof import _schedule
from .site_grading import MODEL as TERRAIN
from .site_storm import MODEL as STORM, _envelope, terrain_profile
from .spatial_design import MODELS, missing_input, model_report, number, read_versioned, source_path, staged_json, vector
from .wet_utilities import MODEL as WET

MODEL = f"{MODELS}/facility-power.json"


def issue(issues, suffix, objects, message):
  issues.append({"rule": f"AIDC_FACILITY_POWER_{suffix}", "status": "fail", "objects": objects, "message": message})


def _make_segment(segments, sizes, key, a, b, carrier, links, *, owners=()):
  width, depth = vector(sizes[carrier], 2, "carrier envelope")
  segments[key] = {"points_m": [a, b], "length_m": dist(a, b), "carrier": carrier, "links": links,
                   "owners": list(owners), "width_m": width, "depth_m": depth,
                   "envelope": bounds(_box(a, b, width, depth))}
  return key


def _path(config, segments, tag, points, carrier, links, origin="", end=""):
  result = []
  for index, (a, b) in enumerate(pairwise(points), 1):
    owners = tuple(key for condition, key in ((index == 1, origin), (index == len(points)-1, end))
                   if condition and key)
    result.append(_make_segment(segments, config["carriers"], f"{tag}-{index:02}", a, b, carrier, links, owners=owners))
  return result


def _connect(objects, edge, points):
  start, outward = _port(objects, edge["physical_from"])
  end, inward = _port(objects, edge["physical_to"])
  if (points[0] != start or points[-1] != end or _direction(points[0], points[1])[1] != outward
      or _direction(points[-1], points[-2])[1] != inward):
    raise ValueError(f"{edge['id']} does not match installed outlet and inlet faces.")


def _circuit(edge, path, kind):
  return {"from": edge["physical_from"], "to": edge["physical_to"], "segments": path, "kind": kind,
          "source": edge["source"], "source_link": edge["source_link"],
          "terminal": edge["physical_to"].split(".")[0]}


def _feeds(topology, layout, side, config, segments):
  train = topology["trains"][side]
  objects = layout["objects"]
  edges = {row["id"]: row for row in train["edges"]}
  feeders = {name: edges[key] for name, key in train["facility_feeds"].items()}
  source, = {edge["physical_from"] for edge in feeders.values()}
  start, outward = _port(objects, source)
  x, y, height = start
  train_loc = config["trains"][side]
  entry_y, sy, z = vector([train_loc[key] for key in ("entry_y_m", "trunk_y_m", "trunk_z_m")], 3, "bank location")
  first, dip, head = (x, entry_y, height), (x, entry_y, z), (x, sy, z)
  if z >= 0 or _direction(start, first)[1] != outward:
    raise ValueError(f"Train {side} bank must descend from its on-site facility outlet.")
  links = [edge["id"] for edge in feeders.values()]
  approach = [start, first, dip]
  if dip != head:
    approach.append(head)
  common = _path(config, segments, f"{side}-ENTRY", approach, "bank", links, origin=source.split(".")[0])
  order = sorted(feeders, key=lambda name: _port(objects, feeders[name]["physical_to"])[0][0])
  result = {}
  current = head
  for index, name in enumerate(order):
    edge = feeders[name]
    terminal = edge["id"]
    stop, _ = _port(objects, edge["physical_to"])
    tx, ty, tz = stop
    trunk_station = (tx, sy, z)
    if tx <= current[0]:
      raise ValueError(f"{name} must be east of the preceding bank station in train {side}.")
    key = _make_segment(segments, config["carriers"], f"{side}-BANK-{index+1:02}", current, trunk_station,
                        "bank", [feeders[tail]["id"] for tail in order[index:]])
    common.append(key)
    current = trunk_station
    mid_y = round(ty-number(config["routes"]["inlet_exit_m"], "inlet setout"), 6)
    points = [trunk_station, (tx, mid_y, z), (tx, mid_y, tz), stop]
    branch = _path(config, segments, f"{side}-{name}", points, "branch", [terminal], end=name)
    _connect(objects, edge, [start, first, *points])
    result[terminal] = {**_circuit(edge, [*common, *branch], "aux" if name == train["shared_bus"] else "plant"),
                        "train": side}
  return result


def _equipment(topology, layout, config, segments):
  train = topology["trains"]["A"]
  objects = layout["objects"]
  edges = {row["id"]: row for row in train["edges"]}
  result = {}
  for cell, targets in train["cell_feeds"].items():
    for device, link in targets.items():
      edge = edges[link]
      kind = {"condenser-water-pump": "pump", "cooling-tower": "tower"}[train["nodes"][device]["family"]]
      start, _ = _port(objects, edge["physical_from"])
      stop, _ = _port(objects, edge["physical_to"])
      x, y, z = start
      tx, ty, _ = stop
      lane = round(y+number(config["routes"][f"{kind}_start_setout_m"], "equipment setout"), 6)
      up = number(config["routes"][f"{kind}_carrier_z_m"], "equipment tray elevation")
      points = [start, (x, lane, z), (x, lane, up), (x, ty, up), (tx, ty, up), stop]
      _connect(objects, edge, points)
      path = _path(config, segments, f"LOCAL-{device}", points, "equipment", [link], origin=cell, end=device)
      result[link] = {**_circuit(edge, path, kind), "cell": cell}
  [(panel, link)] = list(train["shared_feeds"].items())
  edge = edges[link]
  points = [_port(objects, edge[field])[0] for field in ("physical_from", "physical_to")]
  _connect(objects, edge, points)
  paths = _path(config, segments, "CMD", points, "control", [link], origin=train["shared_bus"], end=panel)
  result[link] = {**_circuit(edge, paths, "control"), "cell": train["shared_bus"]}
  return result


def _fit(segments, circuits, config):
  issues = []
  limit = number(config["study"]["max_fill"], "maximum duct fill")
  if limit >= 1:
    raise ValueError("Maximum filled area must be less than the full carrier section.")
  for key, row in segments.items():
    cables = [config["cables"][circuits[link]["kind"]] for link in row["links"]]
    numbers = [c["parallel"] for c in cables]
    if any(type(n) is not int or n < 1 for n in numbers):
      raise ValueError("Cable parallel sets must be identified by a positive integer.")
    diameters = [number(c["od_m"], "cable OD") for c in cables]
    filled = sum(n*pi*d*d*.25 for n, d in zip(numbers, diameters, strict=True))
    row["fill_fraction"] = filled/(row["width_m"]*row["depth_m"])
    if (row["fill_fraction"] > limit or max(diameters) > row["depth_m"]
        or sum(n*d for n, d in zip(numbers, diameters, strict=True)) > row["width_m"]):
      issue(issues, "CARRIER_FILL", [key], f"{key} cannot contain the number of declared sets.")
  return issues


def build_facility_power(context):
  source = source_path(context, "facility-power.json")
  if not source.is_file():
    return missing_input(context, source, "facility-power")
  config = read_versioned(source)
  if config.get("state") != "assumed" or not config.get("basis"):
    raise ValueError("Facility power inputs require an engineering assumption basis.")
  layout, topology = [staged_json(context, path) for path in (LAYOUT, TOPOLOGY)]
  segments, circuits = {}, {}
  for side in ("A", "B"):
    circuits.update(_feeds(topology, layout, side, config, segments))
  circuits.update(_equipment(topology, layout, config, segments))
  issues = _fit(segments, circuits, config)
  for row in circuits.values():
    cable = config["cables"][row["kind"]]
    length = sum(segments[key]["length_m"] for key in row["segments"])
    row["length_m"] = length
    row["impedance_ohm"] = {f"{quantity}_ohm": length*number(cable[f"{quantity}_ohm_per_km"], "cable impedance")/
                            (1000*cable["parallel"]) for quantity in ("r", "x")}
  model = model_report(context, "facility-power", {
    "input_digests": {path: file_digest(context.build_path / path) for path in (LAYOUT, TOPOLOGY)},
    "assumptions": config, "segments": segments, "circuits": circuits, "issues": issues,
  })
  schedule = _schedule(context, "aidc-facility-power-circuits.csv",
                       ("link", "from", "to", "kind", "route_m", "r_ohm", "x_ohm", "route_segments"),
                       ((key, row["from"], row["to"], row["kind"], row["length_m"],
                         row["impedance_ohm"]["r_ohm"], row["impedance_ohm"]["x_ohm"], " ".join(row["segments"]))
                        for key, row in circuits.items()))
  return TargetResult(model.status, (*model.artifacts, schedule), model.diagnostics)


def _underground_water(context):
  result = {}
  for path in (STORM, WET):
    model = staged_json(context, path)
    for key, row in model["runs"].items():
      size = row["pipe"]["outside_m"]/2
      for index, (a, b) in enumerate(pairwise(row["points_m"]), 1):
        if a[:2] != b[:2]:
          result[f"{key}/{index}"] = _envelope(a, b, size)
        else:
          result[f"{key}/{index}"] = _box(_point(a), _point(b), size*2, size*2)
  return result


def build_facility_coordination(context):
  model = staged_json(context, MODEL)
  obstacles = _obstacles(context, staged_json(context, ARCHITECTURE))
  fire, _ = _system_objects(staged_json(context, FIRE))
  obstacles.update({name: shape for name, (shape, _) in fire.items()})
  obstacles.update(_underground_water(context))
  obstacles.update({f"{key}/{index}": volume for key, index, volume in cw_segments(staged_json(context, CW))})
  critical = staged_json(context, CRITICAL)
  obstacles.update({key: space(row["envelope"]) for train in critical["trains"].values() for key, row in train["segments"].items()})
  terrain = staged_json(context, TERRAIN)
  issues, covers, crossings = list(model["issues"]), {}, []
  minimum = number(model["assumptions"]["study"]["minimum_cover_m"], "burial cover")
  separation = number(model["assumptions"]["study"]["minimum_ab_crossing_clearance_m"], "A/B crossing clearance")
  shapes = {key: space(row["envelope"]) for key, row in model["segments"].items()}
  for key, row in model["segments"].items():
    shape = shapes[key]
    for other, volume in obstacles.items():
      if other not in row["owners"] and shape.intersects(volume):
        issue(issues, "SERVICE_CLASH", [key, other], f"{key} intersects {other}.")
    a, b = row["points_m"]
    if a[2] == b[2] < 0:
      radius = row["width_m"]/2
      axis = 0 if a[0] != b[0] else 1
      ground = []
      for offset in (-radius, 0., radius):
        p, q = list(a), list(b)
        p[1-axis] += offset
        q[1-axis] += offset
        ground.extend(grade for _, grade, _ in terrain_profile(terrain, p, q))
      cover = min(ground)-shape.maximum_m[2]
      covers[key] = cover
      if cover < minimum:
        issue(issues, "COVER", [key], f"{key} has {cover:.3f} m cover below its declared minimum.")
  for (key, first), (other, second) in combinations(model["segments"].items(), 2):
    joined = {tuple(p) for p in first["points_m"]} & {tuple(p) for p in second["points_m"]}
    common_bank = key[0] == other[0] and key[0] in {"A", "B"}
    if joined and (common_bank or set(first["links"]) & set(second["links"])):
      continue
    shape, volume = shapes[key], shapes[other]
    if shape.intersects(volume):
      issue(issues, "ROUTE_SEPARATION", [key, other], f"{key} and {other} occupy the same volume.")
    if key in covers and other in covers and {key[0], other[0]} == {"A", "B"} and all(
      min(a1, b1) > max(a0, b0) for a0, a1, b0, b1 in zip(shape.minimum_m[:2], shape.maximum_m[:2],
                                                         volume.minimum_m[:2], volume.maximum_m[:2], strict=True)
    ):
      clearance = max(shape.minimum_m[2]-volume.maximum_m[2], volume.minimum_m[2]-shape.maximum_m[2])
      crossings.append({"routes": [key, other], "clearance_m": clearance})
      if clearance < separation:
        issue(issues, "AB_CLEARANCE", [key, other], f"{key} and {other} clear by only {clearance:.3f} m.")
  return model_report(context, "facility-power-coordination", {
    "route_digest": file_digest(context.build_path / MODEL), "obstacles_checked": len(obstacles),
    "segments_checked": len(shapes), "burial_cover_m": covers, "ab_crossings": crossings, "issues": issues,
  }, issues)
