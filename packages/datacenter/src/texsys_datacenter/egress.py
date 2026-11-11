"""Build a repeatable floor egress graph over the coordinated compartment plan."""

from dataclasses import asdict
from itertools import pairwise
import math
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_fire_protection import Barrier, _crossings
from texsys_fire_protection.egress import EgressLink, evaluate_egress

from .unit import _identifiers, _number


def _inside(value, low, high):
  return low < value < high


def _obstructs(a, b, width, zone):
  x, y = zone["origin_m"]
  w, h = zone["size_m"]
  # Axis-aligned corridor envelope, including the clear width perpendicular to travel.
  padding = (0, width / 2) if a[1] == b[1] else (width / 2, 0)
  return all(max(min(p, q) - pad, low) < min(max(p, q) + pad, low + size) - 1e-8
             for p, q, pad, low, size in zip(a[:2], b[:2], padding, (x, y), (w, h), strict=True))


def _wall_at(axis, station, width_extent, vertical_extent, wall):
  return Barrier(axis, station, (width_extent, vertical_extent), **wall)


def _layout(config, building, fire):
  levels = sorted(building["levels_m"], key=building["levels_m"].get)
  elevation = building["levels_m"]
  width, depth = building["floor_m"]
  zones = building["floor_zones"]
  layout = config["layout"]
  architecture = building.get("architecture")
  placed_doors = architecture["doors"] if architecture else {}
  level_top = {level: elevation[upper] for level, upper in pairwise(levels)}
  level_top[levels[-1]] = max(record["extent_m"][1][1] for key, record in fire["barriers"].items()
                             if key.endswith("/Hall-Services"))
  units = {name: zone for name, zone in zones.items() if zone["role"] == "unit"}
  services = {name: zones[name] for name in layout["service_origins"]}
  if not units or {name for name, zone in zones.items() if zone["role"] in {"electrical-room", "cooling-room"}} != services.keys():
    raise ValueError("Egress must cover all unit and declared occupied equipment-room zones.")
  west, east = layout["hall_spines_x_m"]
  service_x = layout["service_spine_x_m"]
  rows = sorted({zone["origin_m"][1] for zone in units.values()})
  offset = _number(layout["aisle_before_row_m"], "aisle_before_row_m")
  north = architecture["compartment_door_y_m"]["North"] if architecture else layout["north_aisle_y_m"]
  south = architecture["compartment_door_y_m"]["South"] if architecture else rows[0] - offset
  aisles = [south, *(row - offset for row in rows[1:]), north]
  wall_x = fire["barriers"][f"{levels[0]}/Hall-Services"]["station_m"]
  if not (0 < west < east < wall_x < service_x < width and 0 < aisles[0] < aisles[-2] < north < depth):
    raise ValueError("Egress aisles must lie within their assigned fire compartments.")
  towers = architecture["stairs"] if architecture else config["stairs"]
  if set(towers) != {"West", "East"}:
    raise ValueError("The repeatable egress layout needs the west and east attached stairs.")
  barriers = {key: Barrier(**{k: value for k, value in record.items() if k != "host"})
              for key, record in fire["barriers"].items() if record["host"] == "wall"}
  if architecture:
    for identity, wall in architecture["walls"].items():
      if wall["role"] in {"stair-entry", "stair-discharge"}:
        barriers[identity] = Barrier(wall["axis"], wall["station_m"], wall["extent_m"],
                                     thickness_mm=1000 * wall["thickness_m"], **config["stair_wall"])
  nodes, links, points, occupants, door_hosts, blocked = {}, {}, {}, {}, {}, {}
  stair_envelopes = {}
  exits = {name: {"entries": [], "outdoor": f"Outdoor/{name}"} for name in towers}
  clear_width = _number(layout["aisle_clear_width_m"], "aisle_clear_width_m")

  def door_width(identity, type_):
    return placed_doors[identity]["clear_width_m"] if architecture else config["door_types"][type_]["width_m"]

  def add(name, a, b, kind, clear, *, bends=(), own=None, group=None, host=None):
    route = [nodes[a], *bends, nodes[b]]
    if any(math.dist(first, second) <= 1e-8 for first, second in pairwise(route)):
      raise ValueError(f"Egress link {name} contains a zero-length segment.")
    links[name] = EgressLink(a, b, sum(math.dist(first, second) for first, second in pairwise(route)), clear, kind, group)
    points[name] = route
    if host is not None:
      door_hosts[name] = host
    if kind in {"access", "door"}:
      conflicts = [key for key, zone in zones.items() if key != own and any(
        _obstructs(first, second, clear, zone) for first, second in pairwise(route)
      )]
      if conflicts:
        blocked[name] = conflicts

  for level in levels:
    z = elevation[level] + 1
    hall = {}
    for row, y in enumerate(aisles):
      hall[row] = {west: f"{level}/Hall-West-{row}", east: f"{level}/Hall-East-{row}"}
      for x, name in hall[row].items():
        nodes[name] = (x, y, z)
    for zone, record in sorted(units.items()):
      x0, y0 = record["origin_m"]
      sx, sy = record["size_m"]
      x, y = x0 + sx - .35, y0 + sy - .35
      row = rows.index(y0)
      origin, join = f"{level}/{zone}", f"{level}/Aisle-{zone}"
      if not west < x < east or x in hall[row]:
        raise ValueError(f"Unit {zone} does not have a distinct aisle entry.")
      nodes[origin], nodes[join] = (x, y, z), (x, aisles[row], z)
      hall[row][x] = join
      occupants[origin] = layout["unit_occupants"]
      add(f"{origin}/Access", origin, join, "access", clear_width, own=zone)
    for row, stations in hall.items():
      for index, (a, b) in enumerate(pairwise(value for _, value in sorted(stations.items()))):
        add(f"{level}/Aisle-{row}-{index}", a, b, "access", clear_width)
    for side, x in (("West", west), ("East", east)):
      for row, (a, b) in enumerate(pairwise(stations[x] for stations in hall.values())):
        add(f"{level}/{side}-Spine-{row}", a, b, "access", clear_width)

    service_nodes = {aisles[0]: f"{level}/Services-South", north: f"{level}/Services-North"}
    for zone, record in sorted(services.items()):
      x0, y0 = record["origin_m"]
      sx, sy = record["size_m"]
      origin, join = f"{level}/{zone}", f"{level}/Corridor-{zone}"
      y = y0 + sy / 2
      if x0 + sx >= service_x or not aisles[0] < y < north or y in service_nodes:
        raise ValueError(f"Service room {zone} does not have a distinct corridor entry.")
      nodes[origin], nodes[join] = (x0 + .35, y, z), (service_x, y, z)
      service_nodes[y] = join
      occupants[origin] = layout["service_origins"][zone]
      add(f"{origin}/Access", origin, join, "access", clear_width, own=zone)
    for y, name in service_nodes.items():
      nodes[name] = (service_x, y, z)
    for index, (a, b) in enumerate(pairwise(value for _, value in sorted(service_nodes.items()))):
      add(f"{level}/Services-Spine-{index}", a, b, "access", clear_width)
    for side, row in (("South", 0), ("North", len(aisles) - 1)):
      name = f"{level}/CompartmentDoor-{side}"
      add(name, hall[row][east], service_nodes[aisles[row]], "door",
          door_width(name, "compartment"), host=(f"{level}/Hall-Services", "compartment"))

    for group, tower in sorted(towers.items()):
      y = tower["entry_y_m"]
      wall = 0 if group == "West" else width
      sign = -1 if group == "West" else 1
      if group == "West":
        if y not in aisles:
          raise ValueError("The west stair door must align to a hall aisle.")
        approach = hall[aisles.index(y)][west]
      else:
        if y not in service_nodes:
          raise ValueError("The east stair door must align to the service corridor.")
        approach = service_nodes[y]
      span = _number(tower["projection_m"], f"{group}.projection_m", minimum=2)
      half = _number(tower["half_depth_m"], f"{group}.half_depth_m", minimum=1.5)
      if not (0 <= y - half < y + half <= depth):
        raise ValueError(f"Stair {group} enclosure must align to the façade.")
      entry, host = f"{level}/Stair-{group}", f"{level}/StairWall-{group}"
      nodes[entry] = (wall + sign * .5, y, z)
      exits[group]["entries"].append(entry)
      if not architecture:
        barriers[host] = _wall_at(0, wall, (y - half, y + half), (elevation[level], level_top[level]),
                                  config["stair_wall"])
      name = f"{level}/StairDoor-{group}"
      add(name, approach, entry, "door", door_width(name, "stair"),
          host=(host, "stair"))
      if level == levels[0]:
        outer = wall + sign * span
        envelope = tower.get("envelope")
        stair_envelopes[group] = ({"min_m": envelope["minimum_m"], "max_m": envelope["maximum_m"]} if envelope else
                                  {"min_m": (min(outer, wall), y - half, elevation[level]),
                                   "max_m": (max(outer, wall), y + half, level_top[levels[-1]])})
        discharge_y = placed_doors[f"Discharge-{group}"]["center_m"][1] if architecture else y
        nodes[exits[group]["outdoor"]] = (outer + sign * 2, discharge_y, z)
        outer_host = f"StairDischargeWall-{group}"
        if not architecture:
          barriers[outer_host] = _wall_at(0, outer, (y - half, y + half), (elevation[level], level_top[level]),
                                         config["stair_wall"])
        name = f"Discharge-{group}"
        turn = tower.get("discharge_turn_x_m", nodes[entry][0])
        bends = [(turn, y, z), (turn, discharge_y, z)] if discharge_y != y else []
        add(name, entry, exits[group]["outdoor"], "discharge",
            door_width(name, "discharge"), bends=bends, group=group, host=(outer_host, "discharge"))
      else:
        below = levels[levels.index(level) - 1]
        if architecture:
          path = tower["stories"][f"{level}-{below}"]["walking_path_m"]
          bends = [(x, y, height + 1) for x, y, height in reversed(path[1:-1])]
        else:
          z_below = elevation[below] + 1
          turn = wall + sign * (span - .5)
          mid_z = (z + z_below) / 2
          bends = ((turn, y, mid_z), (turn, y + 1.2, mid_z), (wall + sign * .5, y + 1.2, z_below))
        add(f"Stair-{group}/{level}-{below}", entry, f"{below}/Stair-{group}", "stair", tower["clear_width_m"],
            bends=bends, group=group)
  return links, points, occupants, exits, barriers, door_hosts, blocked, stair_envelopes


def _opening_checks(config, fire, links, points, barriers, door_hosts, blocked, placed):
  checks, doors = {}, {}
  penetrations = fire["penetrations"]["crossings"]
  for name, route in points.items():
    hits = [(host, point, normal) for host, barrier in sorted(barriers.items())
            for point, normal in _crossings(barrier, route)]
    expected = door_hosts.get(name)
    failed = []
    if name in blocked:
      failed.append("obstructed-clear-path")
    if expected is None:
      if hits:
        failed.append("unprotected-wall-crossing")
    elif len(hits) != 1 or hits[0][0] != expected[0]:
      failed.append("door-host")
    else:
      host, point, perpendicular = hits[0]
      door = config["door_types"][expected[1]]
      barrier = barriers[host]
      across, _ = barrier.project(point)
      (a, b), (sill, head) = barrier.extent_m
      half = links[name].width_m / 2
      opening = placed.get(name)
      height = opening["height_m"] if opening else _number(door["height_m"], f"{name}.height_m")
      if opening and (opening["host"] != host or opening["type"] != expected[1]
                      or abs(across - barrier.project(opening["center_m"])[0]) > 1e-8):
        failed.append("architectural-opening-binding")
      if not perpendicular or not (a <= across - half and across + half <= b and sill + height <= head):
        failed.append("door-opening-geometry")
      if (not isinstance(door["evidence"], str) or not door["evidence"].strip()
          or door["self_closing"] is not True or door["unlocked_in_egress"] is not True):
        failed.append("door-hardware-and-evidence")
      if any(door["rating_min"].get(key, 0) < value for key, value in barrier.required_min.items()):
        failed.append("door-rating")
      for penetration in penetrations.values():
        if penetration["barrier"] != host or "selection" not in penetration:
          continue
        lateral, vertical = barrier.project(penetration["selection"]["center_m"])
        radius = penetration["selection"]["diameter_mm"] / 2000
        if across - half < lateral + radius and across + half > lateral - radius and sill + height > vertical - radius:
          failed.append("door-penetration-conflict")
          break
      doors[name] = {"host": host, "point_m": point, "clear_width_m": links[name].width_m,
                     "height_m": height, "selection": door, "wall_axis": barrier.axis,
                     **({"architecture_opening": name} if opening else {})}
    if failed:
      checks[name] = failed
  return checks, doors


def evaluate_building_egress(source: Path, building: dict, fire: dict):
  config = _load_json((source / "egress.study.json").read_text(encoding="utf-8"))
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Egress study needs schema_version 1 and an explicit assumed basis.")
  links, points, occupants, exits, barriers, hosts, blocked, stair_envelopes = _layout(config, building, fire)
  invalid, doors = _opening_checks(config, fire, links, points, barriers, hosts, blocked,
                                   building.get("architecture", {}).get("doors", {}))
  policy = {key: {kind: _number(value, f"{key}.{kind}") for kind, value in config[key].items()}
            for key in ("minimum_width_m", "people_per_m")}
  cases = {}
  for name, case in sorted(config["cases"].items()):
    if case["expected"] not in {"pass", "fail"}:
      raise ValueError(f"Invalid egress expectation in {name}.")
    closed = _identifiers(case["closed_links"], "closed_links") | invalid.keys()
    common_limit = _number(case.get("max_common_access_m", config["max_common_access_m"]),
                           "max_common_access_m", inclusive=True)
    result = evaluate_egress(
      links, occupants, exits, policy["minimum_width_m"], policy["people_per_m"],
      closed_links=closed, closed_exits=_identifiers(case["closed_exits"], "closed_exits"),
      width_overrides=case.get("width_overrides_m", {}),
      max_access_m=_number(case["max_access_m"], "max_access_m"), min_exits=case["min_exits"],
      max_common_access_m=common_limit,
    )
    cases[name] = {**result, "expected": case["expected"], "closed_exits": case["closed_exits"],
                   "closed_links": sorted(closed), "width_overrides_m": case.get("width_overrides_m", {}),
                   "max_access_m": case["max_access_m"], "min_exits": case["min_exits"],
                   "max_common_access_m": common_limit}
  if not cases:
    raise ValueError("Egress study requires explicit cases.")
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "formal_review": "pending",
    "status": "pass" if not invalid and all(case["status"] == case["expected"] for case in cases.values()) else "fail",
    "frame": building["frame"], "occupants": sum(occupants.values()),
    "doors": doors, "exits": exits, "stair_envelopes": stair_envelopes,
    "invalid_links": invalid, "obstructions": blocked, "cases": cases,
    "links": {name: {**asdict(edge), "points_m": points[name]} for name, edge in sorted(links.items())},
    "policy": policy,
  }


def write_egress_usd(report, path):
  from pxr import Gf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Egress").GetPrim())
  for name, bounds in report["stair_envelopes"].items():
    envelope = UsdGeom.Cube.Define(stage, f"/Egress/StairEnvelope_{_prim_name(name)}")
    envelope.CreateSizeAttr(1)
    extent = tuple(zip(bounds["min_m"], bounds["max_m"], strict=True))
    envelope.AddTranslateOp().Set(Gf.Vec3d(*((a + b) / 2 for a, b in extent)))
    envelope.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*(b - a for a, b in extent)))
    envelope.CreateDisplayOpacityAttr([.16])
    envelope.CreateDisplayColorAttr([Gf.Vec3f(.25, .75, .47)])
  for name, link in report["links"].items():
    curve = UsdGeom.BasisCurves.Define(stage, f"/Egress/{_prim_name(name)}")
    curve.CreateTypeAttr("linear")
    curve.CreateCurveVertexCountsAttr([len(link["points_m"])])
    curve.CreatePointsAttr([Gf.Vec3f(*point) for point in link["points_m"]])
    curve.SetWidthsInterpolation("constant")
    curve.CreateWidthsAttr([.09])
    color = (.88, .2, .18) if name in report["invalid_links"] else (
      (.25, .75, .47) if link["kind"] in {"stair", "discharge", "door"} else (.25, .54, .82)
    )
    curve.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    curve.GetPrim().SetCustomDataByKey("identity", name)
    curve.GetPrim().SetCustomDataByKey("kind", link["kind"])
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
