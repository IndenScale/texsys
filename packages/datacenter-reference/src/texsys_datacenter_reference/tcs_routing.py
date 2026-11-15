"""Route independent CDU secondary loops to the physical rack ports."""

import csv
from collections import defaultdict
from itertools import combinations, pairwise
from math import dist, isclose, pi

from pxr import Usd

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import space
from .checks import load_design
from .cw_network import _pipe_prim
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _system_objects
from .pod_layout import MODEL as PODS, SCENE as BASE, TYPICAL as TYPICAL_BASE, _world_space
from .spatial_design import MODELS, _world, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


MODEL = f"{MODELS}/tcs-routing.json"
SCENE = "scenes/aidc-tcs-coordinated.usda"
TYPICAL = "scenes/aidc-it-pod-tcs.usda"
SIDES = ("A", "B")
LEGS = ("supply", "return")
ROWS = ("south", "north")
COLORS = {("A", "supply"): (.16, .42, .66), ("A", "return"): (.33, .55, .78),
          ("B", "supply"): (.70, .33, .16), ("B", "return"): (.74, .55, .28)}


def _materials(config):
  pipes = {}
  for key in ("main", "branch"):
    row = config[key]
    pipe = {field: number(row[field], f"{key}.{field}")
            for field in ("bore_m", "envelope_m", "maximum_velocity_m_s", "roughness_m")}
    if not pipe["roughness_m"] < pipe["bore_m"] < pipe["envelope_m"]:
      raise ValueError(f"{key}: require roughness below bore, and bore below installed envelope.")
    pipes[key] = {"size": row["size"], **pipe}
  return pipes


def _bind(instance, side, config):
  assembly = instance.definition
  net = assembly["nets"][config["net"]]
  if net["kind"] != "tcs":
    raise ValueError(f"{instance.name}/{side}: expected a TCS net.")
  cdu = config["cdu"]
  members = {name for name, child in instance.children.items() if child.family == "liquid-cooled-rack"}
  if not members or instance.children[cdu].family != "coolant-distribution-unit":
    raise ValueError(f"{instance.name}/{side}: declare a physical CDU and rack devices.")
  for leg, port in (("supply", "facility_in"), ("return", "facility_out")):
    if f"{cdu}.{port}" not in instance.specification["boundary"][f"fws_{leg}"]:
      raise ValueError(f"{instance.name}/{side}: CDU is missing its primary-side connection.")
  links = {row["id"]: row for row in assembly["connections"] if row["id"] in net["connections"]}
  left = set(links)
  lower = side.lower()
  results = {}
  for leg in LEGS:
    supply = leg == "supply"
    manifold = config[leg]["manifold"]
    required_family = "TcsSupplyManifold" if supply else "TcsReturnManifold"
    if instance.children[manifold].family != required_family:
      raise ValueError(f"{instance.name}/{side}: invalid manifold for {leg}.")
    cport = f"{cdu}.tcs_{leg}"
    cedge = [cport, f"{manifold}.in"] if supply else [f"{manifold}.out", cport]
    leads = [row for row in links.values() if [row["from"], row["to"]] == cedge]
    if len(leads) != 1:
      raise ValueError(f"{instance.name}/{side}: each manifold needs one CDU tie.")
    lead, = leads
    left.remove(lead["id"])
    branches = {}
    for identity, record in links.items():
      edge = record["from"], record["to"]
      external, internal = tuple(reversed(edge)) if supply else edge
      rack, port = external.split(".")
      port_name = f"tcs_{'in' if supply else 'out'}_{lower}"
      pattern = f"{manifold}.{'out' if supply else 'in'}_"
      if rack in members and port == port_name and internal.startswith(pattern):
        if rack in branches or identity not in left:
          raise ValueError(f"{instance.name}/{side}: duplicate {leg} to {rack}.")
        branches[rack] = record
        left.remove(identity)
    if branches.keys() != members:
      raise ValueError(f"{instance.name}/{side}: require one {leg} on this manifold for every rack.")
    results[leg] = {"manifold": manifold, "lead": lead, "branches": branches}
  if left or len(links) != len(net["connections"]):
    raise ValueError(f"{instance.name}/{side}: the physical layout must cover every unique SDL edge.")
  return results


def _endpoint(equipment, reference):
  name, port = reference.split(".")
  row = equipment[name]
  return row["ports_m"][port], row["port_normals"][port]


def _path_tree(segments, root, endpoints):
  adjacent = defaultdict(list)
  for key, record in segments.items():
    a, b = record["nodes"]
    adjacent[a].append((b, key))
    adjacent[b].append((a, key))
  sequence, parents = [root], {root: None}
  for node in sequence:
    for remote, key in adjacent[node]:
      if remote not in parents:
        parents[remote] = node, key
        sequence.append(remote)
  if len(sequence) != len(segments)+1 or any(point not in parents for point in endpoints.values()):
    raise ValueError("Secondary manifold must be a connected supply or return tree.")
  traces, owners = {}, defaultdict(list)
  for rack, endpoint in sorted(endpoints.items()):
    path = []
    current = endpoint
    while current != root:
      current, run = parents[current]
      path.append(run)
      owners[run].append(rack)
    traces[rack] = list(reversed(path))
  if owners.keys() != segments.keys():
    raise ValueError("Secondary manifold has dead physical branches.")
  return traces, owners, parents


def _manifold(side, leg, bind, cfg, equipment, materials, minor, aisle):
  rule = cfg[leg]
  cross = number(rule["crossover_x_m"], "crossover X")
  z = number(rule["tier_m"], "manifold height")
  y = {row: number(rule[f"{row}_y_m"], f"{row} manifold lane") for row in ROWS}
  cdu_ref = f"{cfg['cdu']}.tcs_{leg}"
  cdu, normal = _endpoint(equipment, cdu_ref)
  supply = leg == "supply"
  lead = number(cfg["skid_lead_y_m"], "detachable lead takeoff")
  feed_row = cfg["feed_row"]
  if not (normal[1]*(lead-cdu[1]) > 0 and normal[0] == normal[2] == 0 and cdu[2] < z):
    raise ValueError(f"{cdu_ref}: detachable lead must leave the CDU along the modeled port normal.")
  prefix = f"{side}-{'S' if supply else 'R'}"
  pipes, stations, terminals = {}, {row: {cross: f"{prefix}-{row}-X"} for row in ROWS}, {}

  def node(row, x, name):
    return stations[row].setdefault(x, f"{prefix}-{row}-{name}")

  def add(key, a, b, points, kind, losses, link=None):
    points = [tuple(point) for point in points]
    compact = [points[0]]
    for point in points[1:]:
      if dist(point, compact[-1]) > 1e-8:
        compact.append(point)
    length = sum(dist(p, q) for p, q in pairwise(compact))
    if len(compact) < 2 or any(sum(abs(x-v) > 1e-8 for x, v in zip(p, q, strict=True)) != 1
                              for p, q in pairwise(compact)):
      raise ValueError(f"{key}: all installed lengths must be positive and orthogonal.")
    diameter = materials[kind]["bore_m"]
    pipes[key] = {"side": side, "leg": leg, "material": kind, "manifold": bind["manifold"],
                  "nodes": [a, b] if supply else [b, a], "points_m": compact if supply else compact[::-1],
                  "length_m": length, "fluid_volume_m3": length*pi*diameter**2/4, "minor_k": losses,
                  "sdl_link": link}

  x = cdu[0]
  root = node(feed_row, x, "CDU")
  add(f"{prefix}-CDU", cdu_ref, root,
      [cdu, (x, lead, cdu[2]), (x, lead, z), (x, y[feed_row], z)], "main",
      2*minor["elbow"]+minor["skid_valve_allowance"], bind["lead"]["id"])
  for rack, edge in sorted(bind["branches"].items()):
    endpoint = edge["to" if supply else "from"]
    port, normal = _endpoint(equipment, endpoint)
    if normal != [0, 0, 1] or port[2] >= z:
      raise ValueError(f"{endpoint}: require a top-facing rack port below its manifold.")
    row = "south" if port[1] < sum((aisle["minimum_m"][1], aisle["maximum_m"][1]))/2 else "north"
    x, yp, _ = port
    tap = node(row, x, rack)
    add(f"{prefix}-{rack}", tap, endpoint, [(x, y[row], z), (x, yp, z), port], "branch",
        minor["rack_takeoff"]+(minor["elbow"] if not isclose(y[row], yp, abs_tol=1e-8) else 0),
        edge["id"])
    terminals[rack] = endpoint
  for row, entries in stations.items():
    for i, ((x1, a), (x2, b)) in enumerate(pairwise(sorted(entries.items())), 1):
      add(f"{prefix}-{row}-{i:02}", a, b, [(x1, y[row], z), (x2, y[row], z)], "main", minor["header_through"])
  add(f"{prefix}-X", stations["south"][cross], stations["north"][cross],
      [(cross, y["south"], z), (cross, y["north"], z)], "main", 2*minor["elbow"])
  paths, owners, parents = _path_tree(pipes, cdu_ref, terminals)
  for key, row in pipes.items():
    first, second = row["nodes"]
    first_is_upstream = parents[second] == (first, key)
    if first_is_upstream != supply:
      row["nodes"].reverse()
      row["points_m"].reverse()
    row["racks"] = owners[key]
  return pipes, paths


def build_tcs_routing(context):
  path = source_path(context, "tcs-routing.json")
  if not path.is_file():
    return missing_input(context, path, "tcs-routing")
  config = read_versioned(path)
  if config.get("state") != "assumed" or not config.get("basis"):
    raise ValueError("Specify an assumed project basis for TCS routing.")
  if set(config["sides"]) != set(SIDES):
    raise ValueError("Declare both independent secondary networks.")
  materials = _materials(config)
  minor = {key: number(config["minor_k"][key], key, zero=True)
           for key in ("header_through", "rack_takeoff", "elbow", "skid_valve_allowance")}
  placements = staged_json(context, PODS)
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "tcs-routing", {}, design.diagnostics)
  pods = {}
  for pod, placed in placements["pods"].items():
    equipment = placed["local_equipment"]
    instance = design.instances[pod]
    sides = {}
    if len({cfg["cdu"] for cfg in config["sides"].values()}) != len(SIDES):
      raise ValueError("Secondary sides must use independent CDU units.")
    for side, setup in sorted(config["sides"].items()):
      binding = _bind(instance, side, setup)
      pipes, paths = {}, {}
      for leg in LEGS:
        runs, paths[leg] = _manifold(side, leg, binding[leg], setup, equipment, materials, minor, placed["aisle"])
        pipes.update(runs)
      sides[side] = {"cdu": setup["cdu"], "pipes": pipes, "paths": paths,
                     "fluid_volume_m3": sum(row["fluid_volume_m3"] for row in pipes.values())}
    pods[pod] = sides
  result = model_report(context, "tcs-routing", {"basis": config["basis"], "pod_layout_digest": file_digest(context.build_path/PODS),
                                                "typical": placements["typical"], "pods": pods, "materials": materials})
  schedule = context.build_path/"schedules/aidc-tcs-pipes.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("pod", "side", "line", "leg", "from", "to", "size", "length_m", "fluid_litres", "sdl_link", "manifold"))
    for pod, sides in pods.items():
      for side, system in sides.items():
        for key, pipe in sorted(system["pipes"].items()):
          table.writerow((pod, side, key, pipe["leg"], *pipe["nodes"], materials[pipe["material"]]["size"],
                          f"{pipe['length_m']:.4f}", f"{pipe['fluid_volume_m3']*1000:.4f}", pipe["sdl_link"], pipe["manifold"]))
  return TargetResult(result.status, (*result.artifacts, artifact(context, "tcs-pipes", schedule)))


def _envelopes(pipes, materials):
  for key, row in pipes.items():
    points = row["points_m"]
    radius = materials[row["material"]]["envelope_m"]/2
    for index, (a, b) in enumerate(pairwise(points)):
      axis, = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if abs(x-y) > 1e-8]
      start, stop = list(a), list(b)
      direction = 1 if b[axis] > a[axis] else -1
      if index:
        start[axis] -= direction*radius
      if index < len(points)-2:
        stop[axis] += direction*radius
      low = [min(x,y)-(radius if i != axis else 0) for i, (x,y) in enumerate(zip(start,stop,strict=True))]
      high = [max(x,y)+(radius if i != axis else 0) for i, (x,y) in enumerate(zip(start,stop,strict=True))]
      yield key, index, {tuple(a),tuple(b)}, Space(low, high)


def _clearance(model, placement, fire):
  issues = []
  for pod, sides in model["pods"].items():
    placed = placement["pods"][pod]
    pipes = sides["A"]["pipes"] | sides["B"]["pipes"]
    geometry = list(_envelopes(pipes, model["materials"]))
    access = {"AISLE": placed["aisle"], **{key: row["access"] for key, row in placed["doors"].items()}}
    obstacles = {key: space(row) for key, row in (placed["local_equipment"] | placed["local_shell"] | access).items()}
    host = Space((0, 0, 0), placed["size_m"])

    def issue(first, second, rule):
      issues.append({"rule": rule, "status": "fail", "objects": [pod, first, second],
                     "message": f"{pod}/{first} has a clearance conflict with {second}."})

    for key, index, _, volume in geometry:
      label = f"{key}/{index+1}"
      if not host.contains_space(volume):
        issue(label, "pod-envelope", "AIDC_TCS_CONTAINMENT")
      for obstruction, shape in obstacles.items():
        if volume.intersects(shape):
          issue(label, obstruction, "AIDC_TCS_CLEARANCE")
      world = space(_world_space({"minimum_m": volume.minimum_m, "maximum_m": volume.maximum_m}, placed["placement"]))
      for obstruction, shape in fire[pod]["objects"].items():
        if world.intersects(shape):
          issue(label, obstruction, "AIDC_TCS_FIRE_CLASH")
    for (key, ix, ends, volume), (other, iy, other_ends, other_volume) in combinations(geometry, 2):
      if (key == other and abs(ix-iy) == 1) or (ends & other_ends and set(pipes[key]["nodes"]) & set(pipes[other]["nodes"])):
        continue
      if volume.intersects(other_volume):
        issue(f"{key}/{ix+1}", f"{other}/{iy+1}", "AIDC_TCS_PIPE_CLASH")
  return issues


def _export(context, model, storage, *, typical=False):
  scene, destination = (TYPICAL_BASE, TYPICAL) if typical else (BASE, SCENE)
  stage = Usd.Stage.Open(str(context.build_path/scene))
  if stage is None:
    raise ValueError(f"Cannot open {scene}.")
  keys = [model["typical"]] if typical else model["pods"]
  preview = {}
  source = source_path(context, "tcs-routing.json").relative_to(context.root).as_posix()
  for pod in keys:
    equipment = storage["pods"][pod]["placement"]
    for side, system in model["pods"][pod].items():
      for key, row in system["pipes"].items():
        identity = f"{pod}/{key}"
        path = f"/AIDC/TCS/{pod}/{key.replace('-', '_')}"
        points = row["points_m"] if typical else [_world(equipment["origin_m"], equipment["rotation_deg"], p)
                                                  for p in row["points_m"]]
        # Remove faces hidden by adjacent physical connections in the full-site preview.
        _pipe_prim(stage, path, identity, points, model["materials"][row["material"]]["envelope_m"],
                   COLORS[side, row["leg"]], sides=16 if typical else 6, caps=typical)
        preview[identity] = {"file": source, "path": f"sides.{side}.{row['leg']}"}
  file = context.build_path/destination
  if not stage.Flatten(False).Export(str(file)):
    raise OSError(f"Cannot export {file.name}.")
  return artifact(context, "tcs-typical-scene" if typical else "tcs-scene", file,
                  preview={"kind": "scene", "source_map": preview})


def build_tcs_coordination(context):
  model = staged_json(context, MODEL)
  storage = staged_json(context, PODS)
  _, fire = _system_objects(staged_json(context, FIRE))
  issues = _clearance(model, storage, fire)
  result = model_report(context, "tcs-coordination", {"issues": issues, "routed_pods": len(model["pods"]),
                                                      "site_mesh_sides": 6, "typical_mesh_sides": 16,
                                                      "routing_digest": file_digest(context.build_path/MODEL)}, issues)
  if issues:
    return result
  return TargetResult(result.status, (*result.artifacts, _export(context, model, storage), _export(context, model, storage, typical=True)))
