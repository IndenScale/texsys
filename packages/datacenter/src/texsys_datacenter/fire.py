"""Bind fire barriers and penetrations to building spaces and service topology."""

from dataclasses import asdict
from itertools import pairwise
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_fire_protection import Barrier, Firestop, Route, Selection, check_penetrations

from .unit import _number


def _json(source: Path, name: str) -> dict:
  return _load_json((source / f"{name}.study.json").read_text(encoding="utf-8"))


def _building_barriers(plan, building, floor):
  width, depth = building["floor_m"]
  levels = sorted(building["levels_m"].items(), key=lambda item: item[1])
  top = _number(plan["top_m"], "top_m", minimum=levels[-1][1] + building["unit_height_m"], inclusive=True)
  partition = _number(plan["partition_x_m"], "partition_x_m")
  if partition >= width:
    raise ValueError("The fire partition must lie within the building floor.")
  areas = {"Hall": partition * depth, "Services": (width - partition) * depth}
  if plan["area_limit_m2"].keys() != areas.keys():
    raise ValueError("The fire plan needs declared area limits for both compartment roles.")
  placement, misplaced = {"Hall": [], "Services": []}, []
  for zone_id, zone in sorted(building["floor_zones"].items()):
    x, _ = zone["origin_m"]
    extent = zone["size_m"][0]
    actual = "Hall" if x + extent <= partition else "Services" if x >= partition else None
    expected = "Hall" if zone["role"] == "unit" else "Services"
    if actual is not None:
      placement[actual].append(zone_id)
    if actual != expected:
      misplaced.append({"zone": zone_id, "expected": expected, "actual": actual})
  shafts = {name: zone for name, zone in building["floor_zones"].items() if zone["role"] == "service-riser"}
  compartments, barriers = {}, {}
  for (level, bottom), (_, ceiling) in pairwise([*levels, (None, top)]):
    for role, area in areas.items():
      limit = _number(plan["area_limit_m2"][role], f"{role}.area_limit_m2")
      compartments[f"{level}/{role}"] = {
        "area_m2": area, "limit_m2": limit, "zones": placement[role], "status": "pass" if area <= limit else "fail",
      }
    if "architecture" not in building:
      barriers[f"{level}/Hall-Services"] = Barrier(0, partition, ((0, depth), (bottom, ceiling)), **plan["wall"])
      for shaft, zone in sorted(shafts.items()):
        x, y = zone["origin_m"]
        sx, sy = zone["size_m"]
        for face, axis, station, extent in (
          ("West", 0, x, (y, y + sy)), ("East", 0, x + sx, (y, y + sy)),
          ("South", 1, y, (x, x + sx)), ("North", 1, y + sy, (x, x + sx)),
        ):
          barriers[f"{level}/{shaft}/{face}"] = Barrier(axis, station, (extent, (bottom, ceiling)), **plan["shaft_wall"])
  if "architecture" in building:
    for identity, wall in building["architecture"]["walls"].items():
      fire_type = {"partition": "wall", "shaft": "shaft_wall"}.get(wall["role"])
      if fire_type:
        barriers[identity] = Barrier(wall["axis"], wall["station_m"], wall["extent_m"],
                                    thickness_mm=1000 * wall["thickness_m"], **plan[fire_type])
  for level, elevation in levels[1:]:
    barriers[f"{level}/Slab"] = Barrier(2, elevation, ((0, width), (0, depth)),
                                       thickness_mm=1000 * floor["concrete_min_depth_m"], **plan["slab"])
  return compartments, barriers, misplaced, top, shafts


def _inside_reserve(point, zone):
  return all(a < p < a + size for p, a, size in zip(point, zone["origin_m"], zone["size_m"], strict=True))


def _water_routes(config, data, building, partition, top):
  levels, zones = building["levels_m"], building["floor_zones"]
  level_order = sorted(levels, key=levels.get)
  ceilings = dict(zip(level_order, [*(levels[level] for level in level_order[1:]), top], strict=True))
  if config.keys() != data["trains"].keys():
    raise ValueError("Fire routing must cover the building's FWS trains.")
  routes = {}
  for name, train in sorted(data["trains"].items()):
    layout = config[name]
    shaft = layout["shaft"]
    if zones[shaft]["role"] != "service-riser" or zones[layout["plant_reserve"]]["role"] != "cooling-room":
      raise ValueError(f"FWS fire routing needs a shaft and plant reserve for {name}.")
    terminals, riser_nodes = train["terminals"], layout["riser_nodes_by_level"]
    if (terminals.keys() != riser_nodes.keys() or train["plant_node"] in riser_nodes.values()
        or set(terminals.values()) & set(riser_nodes.values()) or len(set(riser_nodes.values())) != len(riser_nodes)):
      raise ValueError(f"FWS fire routing needs distinct riser nodes for each terminal of {name}.")
    if layout["legs"].keys() != {"supply", "return"}:
      raise ValueError(f"FWS fire routing needs separate supply and return geometry for {name}.")
    for leg, geometry in sorted(layout["legs"].items()):
      hall_x, hall_y = geometry["hall_xy_m"]
      if (not _inside_reserve(geometry["shaft_xy_m"], zones[shaft])
          or not _inside_reserve(geometry["plant_xy_m"], zones[layout["plant_reserve"]])
          or not 0 < hall_x < partition or not 0 < hall_y < building["floor_m"][1]):
        raise ValueError(f"FWS fire routing leaves its declared spaces for {name}/{leg}.")
      offset = _number(geometry["above_floor_m"], "above_floor_m")
      if any(levels[level] + offset >= ceilings[level] for level in terminals.keys() | {layout["plant_level"]}):
        raise ValueError(f"FWS fire routing elevation must be within each assigned storey for {name}/{leg}.")
      x, y = geometry["shaft_xy_m"]
      z_plant = levels[layout["plant_level"]] + offset
      locations = {train["plant_node"]: (*geometry["plant_xy_m"], z_plant)}
      locations.update({node: (x, y, levels[level] + offset) for level, node in riser_nodes.items()})
      locations.update({node: (*geometry["hall_xy_m"], levels[level] + offset)
                        for level, node in terminals.items()})
      for segment in train["segments"]:
        if segment["leg"] != leg:
          continue
        identity = segment["id"]
        a, b = locations[segment["start"]], locations[segment["end"]]
        riser = segment.get("riser")
        if ((riser is not None and riser != shaft)
            or (a[2] != b[2] and (riser != shaft or a[:2] != b[:2]))):
          raise ValueError(f"FWS segment {identity} crosses levels outside its declared riser.")
        diameter = geometry["riser_outside_mm"] if riser else geometry["branch_outside_mm"]
        if identity in routes:
          raise ValueError(f"Duplicate routed service {identity}.")
        via = []
        plant = train["plant_node"]
        if plant in (segment["start"], segment["end"]):
          via = [(*point, z_plant) for point in geometry.get("plant_via_xy_m", [])]
          if segment["end"] == plant:
            via.reverse()
        elif {segment["start"], segment["end"]} & set(terminals.values()):
          via = [(*point, a[2]) for point in geometry.get("branch_via_xy_m", [])]
          if segment["start"] in terminals.values():
            via.reverse()
        routes[identity] = Route((a, *via, b), "fws-metal-pipe", geometry["insulation"], diameter)
  return routes


def _drain_routes(config, data, building):
  nodes = config["nodes_m"]
  segments = data["segments"]
  required = {segment[key] for segment in segments.values() for key in ("start", "end")}
  if nodes.keys() != required or config["via_m"].keys() - segments.keys():
    raise ValueError("Drain fire routing must bind the drainage network nodes and segment bends.")
  for identity, receiver in data["receivers"].items():
    x, y, z = nodes[identity]
    level = building["levels_m"][receiver["level"]]
    if not _inside_reserve((x, y), building["floor_zones"][receiver["reserve"]]) or not (
      level < z < level + building["unit_height_m"]
    ):
      raise ValueError(f"Drain receiver {identity} must be within its declared reserve.")
  return {name: Route((nodes[segment["start"]], *config["via_m"].get(name, []), nodes[segment["end"]]),
                      "drain-metal-pipe", config["insulation"], config["outside_mm"])
          for name, segment in sorted(segments.items())}


def evaluate_fire(source: Path, building: dict, floor: dict) -> dict:
  config = _json(source, "fire")
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Fire study requires schema_version 1 and an explicit assumed basis.")
  plan = config["plan"]
  if "architecture" in building:
    geometry = building["architecture"]
    plan = {**plan, **{key: geometry[key] for key in ("partition_x_m", "top_m")}}
  compartments, barriers, misplaced, top, shafts = _building_barriers(plan, building, floor)
  water = _json(source, "fws")
  fws = _water_routes(config["fws_routing"], water, building, plan["partition_x_m"], top)
  drain = _drain_routes(config["drainage_routing"], _json(source, "drainage"), building)
  if fws.keys() & drain.keys():
    raise ValueError("FWS and drain fire routes need distinct identities.")
  routes = {**fws, **drain}
  width, depth = building["floor_m"]
  bottom = min(building["levels_m"].values())
  if any(not (0 < x < width and 0 < y < depth and bottom < z < top)
         for route in routes.values() for x, y, z in route.points_m):
    raise ValueError("Fire coordination routes must stay within the modeled building envelope.")
  systems = {key: Firestop(**record) for key, record in config["firestop_systems"].items()}
  selections = {key: Selection(**record) for key, record in config["selections"].items()}
  result = check_penetrations(barriers, routes, selections, systems)
  checks = {"compartment-area": all(item["status"] == "pass" for item in compartments.values()),
            "zone-placement": not misplaced, "modeled-penetrations": result["status"] == "pass"}
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "frame": building["frame"],
    "status": "pass" if all(checks.values()) else "fail", "checks": checks,
    "compartments": compartments, "misplaced_zones": misplaced, "penetrations": result,
    "shaft_enclosures": shafts,
    "barriers": {key: {**asdict(barrier), "host": barrier.host} for key, barrier in sorted(barriers.items())},
    "firestop_systems": {key: asdict(system) for key, system in sorted(systems.items())},
    "routes": {key: asdict(route) for key, route in sorted(routes.items())},
    "route_sources": {"fws.study.json": sorted(fws), "drainage.study.json": sorted(drain)},
    "shaft_by_route": {segment["id"]: segment["riser"] for train in water["trains"].values()
                       for segment in train["segments"] if segment.get("riser") is not None},
    "structural_opening_templates_pending": floor["opening_requests"],
    "formal_review": "pending",
    "pending": ["project-compartment-and-shaft-design", "classified-assembly-and-firestop-evidence",
                "structural-openings-and-multi-service-details", "detailed-door-and-egress-design",
                "cable-and-duct-routes", "detection-suppression-and-smoke-control"],
  }


def write_fire_usd(report: dict, path: Path) -> None:
  from pxr import Gf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/FireCoordination").GetPrim())
  for group in ("Barriers", "Routes", "Crossings"):
    UsdGeom.Scope.Define(stage, f"/FireCoordination/{group}")
  for name, barrier in sorted(report["barriers"].items()):
    center = [(start + end) / 2 for start, end in barrier["extent_m"]]
    size = [end - start for start, end in barrier["extent_m"]]
    center.insert(barrier["axis"], barrier["station_m"])
    size.insert(barrier["axis"], barrier["thickness_mm"] / 1000)
    surface = UsdGeom.Cube.Define(stage, f"/FireCoordination/Barriers/{_prim_name(name)}")
    surface.CreateSizeAttr(1)
    surface.AddTranslateOp().Set(Gf.Vec3d(*center))
    surface.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*size))
    surface.CreateDisplayColorAttr([Gf.Vec3f(0.77, 0.32, 0.17)])
    surface.CreateDisplayOpacityAttr([0.17])
    surface.GetPrim().SetCustomDataByKey("identity", name)
  for name, route in sorted(report["routes"].items()):
    line = UsdGeom.BasisCurves.Define(stage, f"/FireCoordination/Routes/{_prim_name(name)}")
    line.CreateTypeAttr("linear")
    line.CreateCurveVertexCountsAttr([len(route["points_m"])])
    line.CreatePointsAttr([Gf.Vec3f(*point) for point in route["points_m"]])
    line.SetWidthsInterpolation("constant")
    line.CreateWidthsAttr([0.06])
    line.CreateDisplayColorAttr([Gf.Vec3f(0.14, 0.59, 0.76)])
    line.GetPrim().SetCustomDataByKey("identity", name)
  for name, crossing in sorted(report["penetrations"]["crossings"].items()):
    marker = UsdGeom.Sphere.Define(stage, f"/FireCoordination/Crossings/{_prim_name(name)}")
    marker.CreateRadiusAttr(0.13)
    marker.AddTranslateOp().Set(Gf.Vec3d(*crossing["point_m"]))
    color = (0.13, 0.66, 0.36) if crossing["status"] == "pass" else (0.84, 0.14, 0.11)
    marker.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    marker.GetPrim().SetCustomDataByKey("identity", name)
    marker.GetPrim().SetCustomDataByKey("status", crossing["status"])
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
