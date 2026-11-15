"""Lay out fire alarm rings, supervised pod interfaces and local agent piping."""

import csv
from itertools import pairwise
import math

from texsys.targets import TargetResult

from .architecture import MODEL as ARCH_MODEL
from .checks import load_design
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json, vector


MODEL = f"{MODELS}/fire-systems.json"


def _link(a, b, od):
  if sum(abs(x-y) > 1e-8 for x, y in zip(a, b, strict=True)) != 1:
    raise ValueError(f"Fire services require distinct endpoints and an orthogonal run: {a}, {b}.")
  return {"points_m": [a, b], "outside_diameter_m": od, "length_m": math.dist(a, b)}


def _panel_binding(instance, family):
  matches = [child for child in instance.children.values() if child.family == family]
  if len(matches) != 1:
    raise ValueError(f"{instance.name} must bind a single SDL local fire controller.")
  panel, = matches
  if any(panel.ports.get(name, {}).get("kind") != expected for name, expected in (
      ("power_a", "ac-power"), ("power_b", "ac-power"), ("agent", "fire-agent"), ("alarm", "signal"))):
    raise ValueError(f"{instance.name}/{panel.name}: required suppression and monitoring ports are missing.")
  sources = {edge["to"].split(".")[-1]: edge["id"] for edge in instance.definition["connections"]
             if edge["to"] in {f"{panel.name}.power_a", f"{panel.name}.power_b"}}
  if set(sources) != {"power_a", "power_b"}:
    raise ValueError(f"{instance.name}/{panel.name}: declare both local power connections.")
  return {"controller": f"{instance.name}/{panel.name}", "power_sdl_links": sources,
          "signal_port": f"{instance.name}/{panel.name}.alarm", "agent_port": f"{instance.name}/{panel.name}.agent"}


def _equipment(name, obj, binding, config):
  lo, hi = obj["minimum_m"], obj["maximum_m"]
  x, y, z = lo
  w, depth, height = [number(value, "agent cabinet envelope") for value in vector(config["cabinet_size_m"], 3, "agent cabinet")]
  ix, iy = [number(value, "agent cabinet inset") for value in vector(config["cabinet_inset_m"], 2, "agent cabinet inset")]
  od = number(config["pipe_od_m"], "agent pipe outside diameter")
  plane = hi[2]-number(config["pipe_drop_m"], "agent pipe drop below pod roof")
  origin = (x+ix, y+iy, z)
  corner = (x+ix+w, y+iy+depth, z+height)
  if not (corner[0]+od < hi[0] and corner[1]+od < hi[1] and corner[2] < plane < hi[2]-od):
    raise ValueError(f"{name}: concept agent cabinet and pipe corridor must fit inside the pod roof envelope.")
  cab_x, cab_y = x+ix+w/2, y+iy+depth/2
  line_y = (y+hi[1])/2
  stations = [x+(hi[0]-x)*fraction for fraction in (0.34, 0.70)]
  if stations[0] < cab_x < stations[-1]:
    raise ValueError(f"{name}: install the agent cabinet outside the nozzle run.")
  stations.sort(reverse=cab_x > stations[-1])
  points = [(cab_x, cab_y, z+height), (cab_x, cab_y, plane), (cab_x, line_y, plane),
            *[(station, line_y, plane) for station in stations]]
  return {**binding, "cabinet": {"minimum_m": origin, "maximum_m": corner}, "host_bounds": {
    "minimum_m": lo, "maximum_m": hi}, "pipes": {f"AP-{name}-{index:02}": _link(a, b, od)
      for index, (a, b) in enumerate(pairwise(points), 1)},
    "nozzles": {f"NZ-{name}-{index}": point for index, point in enumerate(points[-2:], 1)},
    "detectors": {f"LS-{name}-{index}": (point[0], point[1]+.28, hi[2]-.12) for index, point in enumerate(points[-2:], 1)}}


def _alarm_ring(key, room, doors, grid, pods, loop, omitted):
  xmin, ymin, floor = room["minimum_m"]
  xmax, ymax, roof = room["maximum_m"]
  tier = roof-loop["below_roof_m"]
  left, right = xmin+loop["side_inset_m"], xmax-loop["side_inset_m"]
  if not xmin < left < right < xmax or floor+loop["panel_height_m"] >= tier:
    raise ValueError(f"{key}: install fire alarm routes within compartment bounds.")
  rows = [ymin + (ymax-ymin)*fraction for fraction in (0.25, 0.75)]
  cuts = sorted({xmin, xmax} | {x for x in grid["x"].values() if xmin < x < xmax})
  devices, nodes, drops, cells = {}, {}, {}, {}

  def add(name, point, kind=None):
    nodes[name] = point
    if kind:
      devices[name] = {"kind": kind, "point_m": point, "address": None}
    return name

  stations = [[], []]
  return_cuts = [ymin, (ymin+ymax)/2, ymax]
  for row, lane in enumerate(rows):
    for index, (low, high) in enumerate(pairwise(cuts), 1):
      name = f"SD-{key}-{row+1}{index}"
      point = ((low+high)/2, lane, tier)
      cells[name] = {"center_m": point, "bounds_m": [low, return_cuts[row], high, return_cuts[row+1]]}
      stations[row].append(add(name, point, None if name in omitted else "hall-smoke"))
  for name, pod in pods.items():
    x0, y0, _ = pod["minimum_m"]
    x1, y1, z1 = pod["maximum_m"]
    row = min(range(2), key=lambda index: abs((y0+y1)/2-rows[index]))
    at = (x0+pod["supervision_x_offset_m"], rows[row], tier)
    if not (x0 < at[0] < x1 and y0 < at[1] < y1 and z1 < tier):
      raise ValueError(f"{name} fire supervisory interface must sit above its placed pod.")
    address = add(f"IO-{name}", at, "pod-interface")
    stations[row].append(address)
    drops[f"FD-{name}"] = {**_link(at, (at[0], at[1], z1), loop["conduit_od_m"]), "address": address, "pod": name}

  aisle = room["aisle_y_m"]
  half = loop["drop_spacing_m"]/2

  def stepped(label, x, y, height, kind):
    if not rows[0] < y-half < y+half < rows[1]:
      raise ValueError(f"{label} station does not fit between the two alarm runs.")
    low, high, z = y-half, y+half, floor+height
    return [add(f"{label}-up1", (x, low, tier)), add(f"{label}-low1", (x, low, z)), add(label, (x, y, z), kind),
            add(f"{label}-low2", (x, high, z)), add(f"{label}-up2", (x, high, tier))]

  west_door, east_door = room["exits"]
  if doors[west_door]["side"] != "west" or doors[east_door]["side"] != "east":
    raise ValueError(f"{key}: use the modeled compartment exits for alarm stations.")
  center = f"FACP-{key}"
  west = stepped(f"MCP-{west_door}", left, aisle-loop["exit_offset_m"], loop["station_height_m"], "manual-call")
  west.append(add(f"AV-{west_door}", (left, aisle-.21, tier), "notification"))
  west += stepped(center, left, aisle+loop["panel_offset_y_m"], loop["panel_height_m"], "panel")
  east = [add(f"AV-{east_door}", (right, aisle-.36, tier), "notification")]
  east += stepped(f"MCP-{east_door}", right, aisle+loop["exit_offset_m"], loop["station_height_m"], "manual-call")
  if any(nodes[a][1] > nodes[b][1] for side in (west, east) for a, b in pairwise(side)):
    raise ValueError(f"{key}: local panel and call point detours must fit in sequence.")
  low, high = rows
  cycle = [add("SW", (left, low, tier)), *sorted(stations[0], key=lambda k: nodes[k][0]),
           add("SE", (right, low, tier)), *east, add("NE", (right, high, tier)),
           *sorted(stations[1], key=lambda k: nodes[k][0], reverse=True), add("NW", (left, high, tier)),
           *reversed(west)]
  runs = {f"FA-{key}-{index:02}": {**_link(nodes[a], nodes[b], loop["conduit_od_m"]), "nodes": [a, b]}
          for index, (a, b) in enumerate(pairwise([*cycle, cycle[0]]), 1)}
  start = cycle.index(center)
  ordered = cycle[start+1:] + cycle[:start]
  for index, name in enumerate((name for name in ordered if name in devices), 1):
    devices[name]["address"] = index
  return {"panel": center, "tier_m": tier, "devices": devices, "runs": runs, "cells": cells, "supervision": drops}


def build_fire_systems(context):
  path = source_path(context, "fire-systems.json")
  if not path.is_file():
    return missing_input(context, path, "fire-systems")
  config = read_versioned(path)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("The facility fire systems require an explicit engineering basis.")
  omitted = config["omitted_hall_detectors"]
  if not isinstance(omitted, list) or any(not isinstance(item, str) for item in omitted) or len(set(omitted)) != len(omitted):
    raise ValueError("Omitted detector IDs must be unique strings.")
  architecture = staged_json(context, ARCH_MODEL)
  grid = staged_json(context, f"{MODELS}/grid.json")
  layout = staged_json(context, f"{MODELS}/layout.json")
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "fire-systems", {}, design.diagnostics)
  loop = {key: number(config["loop"][key], key) for key in ("side_inset_m", "below_roof_m", "conduit_od_m", "station_height_m",
                                                           "panel_height_m", "exit_offset_m", "panel_offset_y_m",
                                                           "drop_spacing_m", "detector_radius_m")}
  local, allocation = {}, {key: {} for key in architecture["rooms"]}
  offset = number(config["pod"]["supervision_x_offset_m"], "supervisory interface offset")
  for name, obj in sorted(layout["objects"].items()):
    if obj["zone"] != architecture["zone"] or obj["module"].split("/")[-2] != "it-container":
      continue
    owners = [key for key, room in architecture["rooms"].items()
              if all(a <= c and d <= b for a, b, c, d in zip(room["minimum_m"], room["maximum_m"],
                                                            obj["minimum_m"], obj["maximum_m"], strict=True))]
    if len(owners) != 1:
      raise ValueError(f"{name} must be wholly contained in a declared fire compartment.")
    owner, = owners
    allocation[owner][name] = {**obj, "supervision_x_offset_m": offset}
    binding = _panel_binding(design.instances[name], config["pod"]["panel_family"])
    local[name] = {**_equipment(name, obj, binding, config["pod"]), "compartment": owner}
  if not local or not all(allocation.values()):
    raise ValueError("Each alarm compartment must identify its placed IT pods.")
  rings = {key: _alarm_ring(key, room, architecture["doors"], grid, allocation[key], loop, set(omitted))
           for key, room in architecture["rooms"].items()}
  if set(omitted) - {item for ring in rings.values() for item in ring["cells"]}:
    raise ValueError("Omitted detector IDs must refer to the planned hall coverage cells.")
  result = model_report(context, "fire-systems", {"basis": config["basis"], "state": config["state"], "rings": rings, "pods": local,
                        "geometric_detector_radius_m": loop["detector_radius_m"]})
  schedule = context.build_path / "schedules/aidc-fire-equipment.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("id", "kind", "compartment", "pod", "loop_address", "x_m", "y_m", "z_m", "sdl_controller"))
    for key, ring in rings.items():
      for tag, row in ring["devices"].items():
        table.writerow((tag, row["kind"], key, "", row["address"] or "", *[round(a, 4) for a in row["point_m"]], ""))
    for name, pod in local.items():
      table.writerow((f"AG-{name}", "agent-cabinet", pod["compartment"], name, "", *pod["cabinet"]["minimum_m"], pod["controller"]))
      for field, kind in (("nozzles", "agent-nozzle"), ("detectors", "local-detector")):
        for tag, point in pod[field].items():
          table.writerow((tag, kind, pod["compartment"], name, "", *[round(a, 4) for a in point], pod["controller"]))
  return TargetResult(result.status, (*result.artifacts, artifact(context, "fire-equipment", schedule)), result.diagnostics)
