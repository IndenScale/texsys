"""Route independent optical distribution from the hall grid and placed pods."""

import csv
from itertools import pairwise
import math

from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH_MODEL, bounds
from .checks import load_design
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json, vector


MODEL = f"{MODELS}/telecom.json"
PLANES = ("A", "B")


def dimensions(row):
  return {key: number(row[key], key) for key in ("width_m", "depth_m", "drop_width_m", "opening_allowance_m",
                                                "interplane_clearance_m", "bend_radius_m", "usable_fill_fraction")}


def tray_space(run):
  a, b = run["points_m"]
  axis = run["axis"]
  return Space(tuple(min(v, w) - (run["section_m"][i]/2 if i != axis else 0) for i, (v, w) in enumerate(zip(a, b, strict=True))),
               tuple(max(v, w) + (run["section_m"][i]/2 if i != axis else 0) for i, (v, w) in enumerate(zip(a, b, strict=True))))


def _plane(key, cfg, tray, cable, sizes, grid, architecture, layout, pods):
  xaxis, yaxis = cfg["axes"]
  dx, dy, dz = vector(cfg["offset_m"], 3, "cabinet displacement")
  base = (grid["x"][xaxis]+dx, grid["y"][yaxis]+dy, architecture["yard_m"]+dz)
  if cfg["power_dependency"] not in layout["objects"] or layout["objects"][cfg["power_dependency"]]["discipline"] != "electrical":
    raise ValueError(f"{key}: bind the cabinet to a placed electrical dependency.")
  x, y, z = base
  width, depth, height = sizes
  cabinet = Space((x-width/2, y-depth/2, z), (x+width/2, y+depth/2, z+height))
  x0, y0, floor = architecture["hall"]["minimum_m"]
  x1, y1, lid = architecture["hall"]["maximum_m"]
  inset = number(cfg["side_inset_m"], f"{key} spine inset")
  tier = floor + number(cfg["tray_offset_m"], f"{key} tray elevation")
  spine = x0+inset if key == "A" else x1-inset
  if not x0 < spine < x1 or not y0 < y < y1 or not cabinet.maximum_m[2] < tier < lid:
    raise ValueError(f"{key}: require an aligned interior spine above the cabinet and below the roof.")
  if (cabinet.maximum_m[0] >= x0 if key == "A" else cabinet.minimum_m[0] <= x1):
    raise ValueError(f"{key}: locate the cabinet outside its matching west or east hall face.")
  if not isinstance(cfg["cabinet"], str) or not cfg["cabinet"].strip():
    raise ValueError(f"{key}: declare the cabinet identity.")
  nodes = {"CAB": (x, y, cabinet.maximum_m[2]), "HEAD": (x, y, tier)}
  runs, adjacency = {}, {}

  def connect(suffix, a, b, kind):
    start, stop = nodes[a], nodes[b]
    axes = [i for i, (v, w) in enumerate(zip(start, stop, strict=True)) if not math.isclose(v, w, abs_tol=1e-8)]
    identity = f"T{key}-{suffix}"
    if len(axes) != 1 or identity in runs:
      raise ValueError(f"{identity}: cable tray runs must have unique identities and an orthogonal nonzero length.")
    axis, = axes
    section = ((tray["drop_width_m"],)*2 + (0.,)) if axis == 2 else (
      (0., tray["width_m"], tray["depth_m"]) if axis == 0 else (tray["width_m"], 0., tray["depth_m"]))
    runs[identity] = {"plane": key, "kind": kind, "points_m": (start, stop), "nodes": [a, b], "axis": axis,
                      "section_m": section, "length_m": math.dist(start, stop), "cables": []}
    adjacency.setdefault(a, []).append((b, identity))
    adjacency.setdefault(b, []).append((a, identity))

  groups = {}
  offset = number(cfg["row_inset_m"], f"{key} row inset")
  panel_offset = number(cfg["panel_offset_x_m"], f"{key} panel offset")
  for name, obj in pods.items():
    lo, hi = obj["minimum_m"], obj["maximum_m"]
    bed = lo[1]+offset if key == "A" else hi[1]-offset
    port_x = lo[0]+panel_offset
    if not (lo[0] < port_x < hi[0] and lo[1] < bed < hi[1] and hi[2] < tier):
      raise ValueError(f"{name}/{key}: put its panel on the pod roof and the feeder above it.")
    groups.setdefault(bed, []).append((port_x, name, hi[2]))

  stations = {position: f"SP-{i:02}" for i, position in enumerate(sorted(set(groups) | {y}), 1)}
  nodes.update({node: (spine, coord, tier) for coord, node in stations.items()})
  connect("E01", "CAB", "HEAD", "cabinet-riser")
  connect("E02", "HEAD", stations[y], "building-entry")
  for index, (a, b) in enumerate(pairwise(stations.values()), 1):
    connect(f"S{index:02}", a, b, "spine")
  for index, (coord, row) in enumerate(sorted(groups.items()), 1):
    before = stations[coord]
    for col, (px, name, pz) in enumerate(sorted(row, reverse=key == "B"), 1):
      branch, panel = f"TAKE-{name}", f"PP-{name}"
      nodes[branch] = (px, coord, tier)
      nodes[panel] = (px, coord, pz)
      connect(f"R{index:02}-{col:02}", before, branch, "row")
      connect(f"D-{name}", branch, panel, "panel-drop")
      before = branch

  discovery, parents = ["CAB"], {"CAB": None}
  for node in discovery:
    for neighbour, run in adjacency[node]:
      if neighbour not in parents:
        discovery.append(neighbour)
        parents[neighbour] = (node, run)
  cables = {}
  radius = tray["bend_radius_m"]
  for pod in pods:
    label, node = f"FO-{key}-{pod}", f"PP-{pod}"
    route = []
    while node != "CAB":
      node, run = parents[node]
      route.append(run)
      runs[run]["cables"].append(label)
    route.reverse()
    legs = [runs[run] for run in route]
    bends = [a["axis"] != b["axis"] for a, b in pairwise(legs)]
    fits = all(leg["length_m"]+1e-8 >= radius*(before+after)
               for leg, before, after in zip(legs, [False, *bends], [*bends, False], strict=True))
    length = sum(leg["length_m"] for leg in legs)
    # Takeoff stays conservative at the orthogonal centerline and adds the declared service loop.
    purchased = length*(1+cable["length_reserve_fraction"]) + cable["patch_allowance_m"]
    cables[label] = {"plane": key, "pod": pod, "panel": f"PP-{key}-{pod}", "panel_point_m": nodes[f"PP-{pod}"],
                     "runs": route, "routed_m": length, "budget_length_m": purchased, "bend_count": sum(bends),
                     "bend_clearance": fits}
  return {"cabinet": {**bounds(cabinet), "id": cfg["cabinet"], "power_dependency": cfg["power_dependency"]},
          "tier_m": tier, "runs": runs, "cables": cables}


def _cable_spec(row):
  spec = {key: number(row[key], key, zero=key == "length_reserve_fraction") for key in
          ("outside_diameter_m", "length_reserve_fraction", "patch_allowance_m")}
  for key in ("fiber_count", "fiber_pairs_per_rack"):
    if type(row[key]) is not int or row[key] < 1:
      raise ValueError(f"{key} must be a positive integer.")
    spec[key] = row[key]
  return spec


def _rack_allocation(design, planes, cable):
  connections = {}
  for plane, assembly in planes.items():
    port = 0
    for label, row in assembly["cables"].items():
      pod = row["pod"]
      racks = sorted(key for key, asset in design.instances[pod].children.items() if asset.family == "liquid-cooled-rack")
      if not racks:
        raise ValueError(f"{pod}: load actual SDL rack identities before allocating links.")
      for index, rack in enumerate(racks):
        port += 1
        count = 2*cable["fiber_pairs_per_rack"]
        first = index*count+1
        end = first+count-1
        name = f"{pod}/{rack}"
        connections[f"{plane}-{pod}-{rack}"] = {"plane": plane, "rack": name, "cable": label, "panel": row["panel"],
                                                 "cabinet_port": f"{assembly['cabinet']['id']}/P{port:03}",
                                                 "rack_port": f"{name}/NET-{plane}", "first_core": first, "last_core": end}
  return connections


def build_telecom(context):
  path = source_path(context, "telecom.json")
  if not path.is_file():
    return missing_input(context, path, "telecom")
  cfg = read_versioned(path)
  if cfg.get("state") != "assumed" or not isinstance(cfg.get("basis"), str) or not cfg["basis"].strip():
    raise ValueError("Declare the facility telecom design basis.")
  if set(cfg["planes"]) != set(PLANES):
    raise ValueError("AIDC telecom requires independent A and B planes.")
  architecture = staged_json(context, ARCH_MODEL)
  if architecture["zone"] != cfg["zone"]:
    raise ValueError("Bind telecom routing to the modeled main hall.")
  grid = staged_json(context, f"{MODELS}/grid.json")
  layout = staged_json(context, f"{MODELS}/layout.json")
  pods = {name: obj for name, obj in sorted(layout["objects"].items())
          if obj["zone"] == cfg["zone"] and obj["module"].split("/")[-2] == "it-container"}
  if not pods:
    raise ValueError("The telecom design needs placed IT pods.")
  tray = dimensions(cfg["tray"])
  if tray["usable_fill_fraction"] > 1:
    raise ValueError("Cable tray fill fraction is at most one.")
  cable = _cable_spec(cfg["cable"])
  sizes = [number(item, "cabinet envelope") for item in vector(cfg["cabinet_size_m"], 3, "cabinet envelope")]
  planes = {key: _plane(key, cfg["planes"][key], tray, cable, sizes, grid, architecture, layout, pods) for key in PLANES}
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "telecom", {}, design.diagnostics)
  allocation = _rack_allocation(design, planes, cable)
  report = model_report(context, "telecom", {"state": cfg["state"], "basis": cfg["basis"], "zone": cfg["zone"],
                        "planes": planes, "tray": tray, "cable": cable, "allocation": allocation})
  root = context.build_path / "schedules"
  root.mkdir(parents=True, exist_ok=True)
  routing = root / "aidc-telecom-cables.csv"
  with routing.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("cable", "plane", "pod", "panel", "routed_m", "budget_length_m", "bend_count", "tray_runs"))
    for assembly in planes.values():
      for label, row in assembly["cables"].items():
        table.writerow((label, row["plane"], row["pod"], row["panel"], round(row["routed_m"], 3),
                        round(row["budget_length_m"], 3), row["bend_count"], " ".join(row["runs"])))
  fiber_map = root / "aidc-telecom-fibers.csv"
  with fiber_map.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    fields = ("plane", "rack", "cable", "panel", "cabinet_port", "rack_port", "first_core", "last_core")
    table.writerow(("link", *fields))
    for name, record in allocation.items():
      table.writerow((name, *(record[key] for key in fields)))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "telecom-cable-schedule", routing),
                                      artifact(context, "telecom-fiber-schedule", fiber_map)), report.diagnostics)
