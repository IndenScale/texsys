"""Validate building TCS deliveries against routed rack ports and FWS dispatch."""

from itertools import combinations
from math import dist

from pxr import Sdf, Usd, UsdGeom

from texsys.project import inspect_project
from texsys.sdl_catalog import read_sdl_catalog
from texsys.usd_brep import _prim_name
from texsys_hvac_equipment.room_modules import read_room_modules

from .fws import BuildingWater
from .fws_branches import _route_gap, define_pipe_routes
from .tcs import _read, tcs_solver
from .tcs_dispatch import TcsFault, redispatch
from .unit import _identifiers, _number


def _shift(point, origin):
  return tuple(a + b for a, b in zip(point, origin, strict=True))


def _fws_at_room(allocation, level, room, count):
  if "delivered_kw_by_unit_train" in allocation:
    return allocation["delivered_kw_by_unit_train"][f"{level}/{room}"]
  return {train: values.get(level, 0.) / count for train, values in allocation["delivered_kw_by_path"].items()}


def _room_faults(name, active_rooms, changes, unpowered_pumps):
  faults = {}
  for identity in sorted(active_rooms):
    change = changes.get(identity, {})
    if change.keys() - {"unavailable_segments", "unavailable_pumps", "pump_speed_limits"}:
      raise ValueError(f"{name}/{identity}: unknown TCS local change.")
    segments = _identifiers(change.get("unavailable_segments", []), "unavailable_segments")
    pumps = _identifiers(change.get("unavailable_pumps", []), "unavailable_pumps")
    speeds = change.get("pump_speed_limits", {})
    if not isinstance(speeds, dict):
      raise ValueError(f"{name}/{identity}: TCS speed limits must be keyed by CDU.")
    speed_limits = tuple((train, _number(speed, "TCS speed limit", inclusive=True))
                         for train, speed in sorted(speeds.items()))
    faults[identity] = TcsFault(frozenset(segments), frozenset(pumps | unpowered_pumps), speed_limits)
  return faults


def _evaluate_cases(source, config, model, unit, building, fws, zones):
  solver = tcs_solver(model)
  water = BuildingWater(source / "fws.study.json", _read(source, "building")["shared_heat_rejection"],
                        building["floor_zones"], building["levels_m"], unit)
  demand = {rack: value["liquid"] for rack, value in unit["rack_heat_kw"].items()}
  trains = {train: legs for trains in fws["outlets"].values() for train, rooms in trains.items()
            for legs in rooms.values()}
  feed_by_cdu = {legs["supply"]["device"]: train for train, legs in trains.items()}
  if feed_by_cdu.keys() != model["trains"].keys() or len(feed_by_cdu) != len(trains):
    raise ValueError("Each installed TCS train needs a distinct routed FWS feed.")

  def feeds(allocation, identity):
    level, room = identity.split("/")
    upstream_kw = _fws_at_room(allocation, level, room, len(zones))
    return {cdu: upstream_kw[train] for cdu, train in sorted(feed_by_cdu.items())}

  baseline_results, profiles, signatures, cases = {}, {}, {}, {}
  for name, definition in sorted(config["cases"].items()):
    upstream = building["scenarios"][definition["building_scenario"]]
    phase = building["phases"][upstream["phase"]]
    changes = definition.get("local_changes", {})
    active_rooms = {f"{level}/{room}" for level in phase["levels"] for room in zones}
    expected = definition["expected"]
    if changes.keys() - active_rooms or expected not in ("pass", "fail"):
      raise ValueError(f"{name}: invalid room changes or scenario expectation.")
    circulating = unit["scenarios"][upstream["unit_scenario"]]["circulating_cdus"]
    unpowered_pumps = {pump for cdu, bank in model["banks"].items()
                       if cdu not in circulating for pump in bank["pumps"]}
    faults = _room_faults(name, active_rooms, changes, unpowered_pumps)
    allocation = upstream["fws"]
    results = {}
    for identity, fault in faults.items():
      available = feeds(allocation, identity)
      signature = (tuple(available.items()), fault)
      if signature not in baseline_results:
        baseline_results[signature] = solver.evaluate(demand, available, fault.segments, unavailable_pumps=fault.pumps,
                                                       pump_speed_limits=dict(fault.speeds))
      results[identity] = baseline_results[signature]
    unused = {identity: surplus for identity, result in results.items()
              if (surplus := sum(feeds(allocation, identity).values()) - sum(result["delivered_kw_by_train"].values())) > 1e-5}
    if unused:
      allocation, results = redispatch(water, upstream, solver, demand, faults, feed_by_cdu)
    assignments, shortages, delivery = {}, {}, 0.
    for identity, result in results.items():
      available = feeds(allocation, identity)
      fault = faults[identity]
      rack_distribution = tuple((cdu, tuple(sorted(racks.items())))
                                for cdu, racks in sorted(result["delivered_kw_by_path"].items()))
      signature = (tuple(available.items()), fault, rack_distribution, result["hydraulics"]["method"])
      profile = signatures.get(signature)
      if profile is None:
        profile = f"Profile-{len(profiles) + 1:03d}"
        profiles[profile] = {"fws_delivered_kw_by_cdu": available, "unavailable_segments": sorted(fault.segments),
                             "unavailable_pumps": sorted(fault.pumps), "pump_speed_limits": dict(fault.speeds), **result}
        signatures[signature] = profile
      assignments[identity] = profile
      delivery += sum(result["delivered_kw_by_terminal"].values())
      shortages.update({f"{identity}/{rack}": short for rack, short in result["shortage_kw_by_terminal"].items()})
    cases[name] = {"status": "fail" if shortages else "pass", "expected": expected, "profiles_by_room": assignments,
                   "building_scenario": definition["building_scenario"],
                   "shortage_kw_by_rack": shortages, "total_delivered_kw": delivery,
                   "total_required_kw": len(active_rooms) * sum(demand.values()),
                   "dispatch": {"mode": "coupled" if unused else "baseline-feasible",
                                "baseline_unused_fws_kw_by_room": unused,
                                "fws_delivered_kw_by_train": allocation["delivered_kw_by_train"],
                                **({"joint_fws": allocation} if unused else {})}}
  if not cases:
    raise ValueError("TCS needs explicit building scenarios.")
  return profiles, cases


def _geometry_conflicts(model, zones, levels, height, fws, clearance):
  conflicts = []
  routes = model["routes"]
  for (a, first), (b, second) in combinations(routes.items(), 2):
    shared = (first["cdu"], first["leg"]) == (second["cdu"], second["leg"]) and bool(
      set(first["nodes"]) & set(second["nodes"]))
    if _route_gap(first, second, shared) + 1e-8 < clearance:
      conflicts.append({"route": a, "other": b, "check": "tcs-clearance"})
  # Only facility-water pipe near the vertical band of the room TCS can clash.
  peak = max(max(point[2] for point in route["points_m"]) + route["outside_m"] / 2 for route in routes.values())
  for level, elevation in levels.items():
    lower_routes = {key: route for key, route in fws["routes"].items() if route["level"] == level
                    and min(point[2] for point in route["points_m"]) - route["outside_m"] / 2 < elevation + peak + clearance}
    for room, zone in zones.items():
      ox, oy = zone["origin_m"]
      sx, sy = zone["size_m"]
      nearby = {key: route for key, route in lower_routes.items() if all(
        min(point[axis] for point in route["points_m"]) < lo + size + clearance + route["outside_m"] / 2
        and max(point[axis] for point in route["points_m"]) > lo - clearance - route["outside_m"] / 2
        for axis, (lo, size) in enumerate(((ox, sx), (oy, sy)))
      )}
      for name, route in routes.items():
        radius = route["outside_m"] / 2
        if any(not all(radius <= value <= limit - radius for value, limit in zip(point, (sx, sy, height), strict=True))
               for point in route["points_m"]):
          conflicts.append({"level": level, "room": room, "route": name, "check": "room-envelope"})
        translated = {**route, "points_m": [_shift(point, (ox, oy, elevation)) for point in route["points_m"]]}
        for other, branch in nearby.items():
          if _route_gap(translated, branch, False) + 1e-8 < clearance:
            conflicts.append({"level": level, "room": room, "route": name, "other": other, "check": "fws-tcs-clearance"})
  return conflicts


def _write_layout(root, source, model, levels, zones, equipment, output):
  catalog = read_sdl_catalog(source, root)
  package = catalog.packages["hvac-equipment"]
  library = read_room_modules(package.root, package.root / inspect_project(package.root).source)
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/TechnologyCooling").GetPrim())
  UsdGeom.Scope.Define(stage, "/TechnologyCooling/Racks")
  UsdGeom.Scope.Define(stage, "/TechnologyCooling/Pipes")
  routes, rack_count, rack_port_count, cdu_port_count = {}, 0, 0, 0
  for level, elevation in sorted(levels.items()):
    for room, zone in sorted(zones.items()):
      origin = (*zone["origin_m"], elevation)
      for rack, placement in model["rack_layout"].items():
        identity = f"{level}/{room}/{rack}"
        module = library[placement["entry"].removeprefix("@hvac-equipment/")]
        if (module.manifest["version"] != placement["module_version"] or dist(module.bounds.GetMin(), (0, 0, 0)) > 1e-8
            or tuple(placement["envelope_m"]) != module.envelope):
          raise ValueError(f"{rack}: rack module geometry differs from the routed layout.")
        world = list(_shift(placement["origin_m"], origin))
        angle = placement["angle_deg"]
        if angle:
          world[0] += module.envelope[0]
          world[1] += module.envelope[1]
        node = module.place(stage, f"/TechnologyCooling/Racks/{_prim_name(identity)}", world, angle)
        node.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)
        node.GetPrim().SetCustomDataByKey("texsys:definition", placement["entry"])
        transform = node.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        rack_count += 1
        for cdu, legs in model["outlets"].items():
          for leg, racks in legs.items():
            port = racks[rack]["port"]
            route = model["routes"][f"{cdu}/{leg}/drop-{rack}"]
            endpoint = route["points_m"][-1 if leg == "supply" else 0]
            if dist(transform.Transform(module.ports[port]), _shift(endpoint, origin)) > 1e-8:
              raise ValueError(f"{identity}.{port}: routed outlet differs from the installed rack port.")
            rack_port_count += 1
      for cdu in model["trains"]:
        identity = f"{level}/{room}/{cdu}"
        for leg, index in (("supply", 0), ("return", -1)):
          endpoint = model["routes"][f"{cdu}/{leg}/header"]["points_m"][index]
          if dist(equipment[identity]["ports_m"][f"tcs_{leg}"], _shift(endpoint, origin)) > 1e-8:
            raise ValueError(f"{identity}.tcs_{leg}: route differs from the installed CDU port.")
          cdu_port_count += 1
      routes.update({f"{level}/{room}/{key}": {**route, "points_m": [_shift(point, origin) for point in route["points_m"]]}
                     for key, route in model["routes"].items()})
  define_pipe_routes(stage, "/TechnologyCooling/Pipes", routes)
  if stage.GetCompositionErrors() or not stage.Flatten(addSourceFileComment=False).Export(str(output)):
    raise ValueError("Cannot export the installed TCS layout.")
  return {"racks": rack_count, "rack_ports": rack_port_count, "cdu_ports": cdu_port_count, "pipe_routes": len(routes)}


def evaluate_tcs(root, source, unit, building, fws, equipment, output):
  config = _read(source, "tcs")
  model = unit["tcs_model"]
  levels = building["levels_m"]
  zones = {key: value for key, value in building["floor_zones"].items() if value["role"] == "unit"}
  if not fws["frame"] == equipment["frame"] == building["frame"]:
    raise ValueError("FWS, TCS and equipment must share the building frame.")
  clearance = _number(config["minimum_pipe_clearance_m"], "minimum TCS pipe clearance")
  conflicts = _geometry_conflicts(model, zones, levels, building["unit_height_m"], fws, clearance)
  profiles, cases = _evaluate_cases(source, config, model, unit, building, fws, zones)
  counts = _write_layout(root, source, model, levels, zones, equipment["placements"], output)
  mismatches = [name for name, case in cases.items() if case["expected"] != case["status"]]
  return {"schema_version": 1, "state": "assumed", "basis": model["basis"], "frame": building["frame"],
          "status": "fail" if conflicts or mismatches else "pass", "counts": counts, "conflicts": conflicts,
          "mismatched_cases": mismatches, "profiles": profiles, "cases": cases,
          "unit_routes": model["routes"], "unit_outlets": model["outlets"],
          "pending": ["manufacturer-performance-and-dual-circuit-evidence", "hydraulic-transients-and-commissioned-control",
                      "server-level-parallel-branches", "support-and-other-mep-clearance", "rack-thermal-transient"]}
