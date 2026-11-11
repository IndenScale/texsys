"""Resolve independent overhead facility-water branches to each unit CDU."""

from collections import defaultdict
from itertools import combinations, pairwise
from math import dist, isclose
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_hvac_dynamics.facility_distribution import FacilityDistribution

from .architecture import read_architecture
from .fire import _drain_routes, _water_routes
from .unit import _number


def _point(value, label):
  if len(value) != 3:
    raise ValueError(f"{label} needs three coordinates.")
  return tuple(_number(item, label, inclusive=True) for item in value)


class FwsBranches:
  def __init__(self, source, water, network, rejection, zones, levels, unit, *, check_geometry=False):
    config = _load_json((source / "fws-branches.study.json").read_text(encoding="utf-8"))
    if config["schema_version"] != 1 or config["state"] != "assumed" or not config["basis"].strip():
      raise ValueError("FWS branches need schema version 1 and an assumed basis.")
    building = _load_json((source / "building.study.json").read_text(encoding="utf-8"))
    fire = _load_json((source / "fire.study.json").read_text(encoding="utf-8"))
    drainage = _load_json((source / "drainage.study.json").read_text(encoding="utf-8"))
    bound = {"floor_zones": zones, "levels_m": levels, "floor_m": building["layout"]["floor_m"],
             "unit_height_m": building["unit_height_m"]}
    geometry = read_architecture(source) or fire["plan"]
    routes = _water_routes(fire["fws_routing"], water, bound, geometry["partition_x_m"], geometry["top_m"])
    self.frame = building["frame"]
    self.basis = config["basis"]
    self.levels = levels
    self.routes, self.outlets, self.distributions = {}, {}, {}
    self.conflicts = []
    density, viscosity = (water["hydraulics"][key] for key in ("density_kg_m3", "viscosity_pa_s"))
    if config["trains"].keys() != network.train_names:
      raise ValueError("FWS branch geometry needs every upstream train.")
    devices = {}
    unit_zones = {name: zone for name, zone in zones.items() if zone["role"] == "unit"}
    rows = defaultdict(list)
    for name, zone in unit_zones.items():
      rows[zone["origin_m"][1]].append(name)
    if not rows:
      raise ValueError("FWS branches need repeated unit zones.")
    for names in rows.values():
      names.sort(key=lambda name: zones[name]["origin_m"][0], reverse=True)
    clearance = _number(config["minimum_pipe_clearance_m"], "minimum_pipe_clearance_m")
    self.minimum_clearance_m = clearance
    horizontal = _number(config["minimum_overhead_m"], "minimum_overhead_m")
    spine = _number(config["spine_x_m"], "spine_x_m")
    if not max(zone["origin_m"][0] for zone in unit_zones.values()) < spine < geometry["partition_x_m"]:
      raise ValueError("FWS spine must be east of all unit origins and inside the hall.")
    pipe_types = config["pipe_types"]
    for key in ("spine", "row", "drop"):
      pipe = pipe_types[key]
      outside, inside = (_number(pipe[name], name) for name in ("outside_m", "diameter_m"))
      if outside <= inside:
        raise ValueError(f"FWS {key} pipe outside diameter must exceed its bore.")
    bend = _number(config["elbow_loss_coefficient"], "elbow_loss_coefficient", inclusive=True)
    for train, layout in sorted(config["trains"].items()):
      device = layout["cdu"]
      if device in devices or unit["cdus"][device]["plant"] not in rejection[train]["unit_plants"]:
        raise ValueError("Each train must bind a distinct unit CDU from its own plant group.")
      devices[device] = train
      placement = unit.get("equipment_layout", {}).get(device)
      if layout["legs"].keys() != {"supply", "return"}:
        raise ValueError("Each floor train needs both supply and return geometry.")
      shared = {}
      for level, z in sorted(levels.items(), key=lambda item: item[1]):
        root = water["trains"][train]["terminals"][level]
        segments, hydraulics, outlets = [], {}, {}
        for leg, leg_layout in sorted(layout["legs"].items()):
          port_name = leg_layout["port"]
          direction = "in" if leg == "supply" else "out"
          if unit["cdus"][device]["facility_ports"].get(port_name) != direction:
            raise ValueError(f"FWS {train}/{leg} must bind a CDU facility port with direction {direction}.")
          if placement is not None:
            if "port_offset_m" in leg_layout or placement["port_normals"][port_name] != [0, 0, 1]:
              raise ValueError(f"FWS {train}/{leg} requires the module's upward-facing CDU port.")
            offset = _point(placement["ports_m"][port_name], port_name)
          else:
            offset = _point(leg_layout["port_offset_m"], "port_offset_m")
          tier = _number(leg_layout["overhead_m"], "overhead_m")
          row_offset = _number(leg_layout["row_offset_m"], "row_offset_m")
          od = _number(pipe_types["spine"]["outside_m"], "spine outside diameter")
          if tier - od / 2 < horizontal or tier + od / 2 > building["unit_height_m"]:
            raise ValueError(f"FWS {train}/{leg} overhead tier leaves the declared clear envelope.")
          upstream = next(network.segments[name] for name in network.paths[train, level]
                          if network.segments[name].leg == leg
                          and (network.segments[name].end if leg == "supply" else network.segments[name].start) == root)
          start = tuple(routes[upstream.id].points_m[-1 if leg == "supply" else 0])
          height = z + tier

          def add(kind, a, b, points):
            points = tuple(dict.fromkeys(points))
            if leg == "return":
              a, b, points = b, a, points[::-1]
            key = f"{train}/{leg}/{kind}/{a}--{b}"
            settings = pipe_types[kind]
            length = sum(dist(p, q) for p, q in pairwise(points))
            if length <= 0 or any(sum(x != y for x, y in zip(p, q, strict=True)) != 1 for p, q in pairwise(points)):
              raise ValueError("Floor branch segments must be positive and orthogonal.")
            capacity = _number(settings["capacity_kg_s"], "pipe capacity")
            segments.append({"id": key, "start": a, "end": b, "leg": leg, "capacity_kg_s": capacity})
            hydraulics[key] = {"length_m": length, **{name: settings[name] for name in
                               ("diameter_m", "roughness_m", "max_velocity_m_s")},
                               "minor_loss_coefficient": settings["minor_loss_coefficient"] + bend * (len(points) - 2)}
            self.routes[f"{level}/{key}"] = {
              "level": level, "train": train, "leg": leg, "kind": kind, "nodes": (a, b), "points_m": points,
              "outside_m": settings["outside_m"], "hydraulic_length_m": length,
              "inside_m": settings["diameter_m"], "upstream_segment": upstream.id if "Header" in (a, b) else None,
            }

          _, y0, _ = start
          centre = (spine, y0, height)
          add("spine", "Header", "Spine", (start, (start[0], y0, height), centre))
          for direction in (-1, 1):
            previous, point = "Spine", centre
            candidates = sorted((row for row in rows if direction * (row + row_offset - y0) >= 0),
                                key=lambda row: abs(row + row_offset - y0))
            for row in candidates:
              node = f"Row-{row:g}"
              tap_y = row + row_offset
              row_point = (spine, tap_y, height)
              if tap_y == y0:
                raise ValueError("Place row take-offs away from the upstream spine connection.")
              add("spine", previous, node, (point, row_point))
              previous, point = node, row_point
              current, point_on_row = node, row_point
              for unit_name in rows[row]:
                ox, oy = unit_zones[unit_name]["origin_m"]
                sx, sy = unit_zones[unit_name]["size_m"]
                port = (ox + offset[0], oy + offset[1], z + offset[2])
                if not (0 < offset[0] < sx and 0 < offset[1] < sy and 0 < offset[2] < tier
                        and 0 < row_offset < sy and port[0] < point_on_row[0]):
                  raise ValueError("Each CDU port and row branch must fit its declared repeated zone.")
                tap = (port[0], tap_y, height)
                tap_node = f"Tap-{unit_name}"
                add("row", current, tap_node, (point_on_row, tap))
                add("drop", tap_node, unit_name, (tap, (port[0], port[1], height), port))
                current, point_on_row = tap_node, tap
                outlets.setdefault(unit_name, {})[leg] = {"device": device, "port": port_name, "point_m": port,
                                                          "fws_net": unit["cdus"][device]["fws_net"]}
        if outlets.keys() != unit_zones.keys():
          raise ValueError("FWS branch geometry must serve every unit.")
        definition = {"plant_node": "Header", "segments": segments, "terminals": {name: name for name in outlets}}
        signature = tuple((name, round(pipe["length_m"], 8)) for name, pipe in hydraulics.items())
        if signature not in shared:
          shared[signature] = FacilityDistribution(definition, hydraulics, density, viscosity)
        self.distributions[train, level] = shared[signature]
        self.outlets.setdefault(level, {})[train] = outlets
    if devices.keys() != unit["cdus"].keys():
      raise ValueError("FWS branch configuration must bind every CDU family instance.")
    if check_geometry:
      self._check_geometry(routes | _drain_routes(fire["drainage_routing"], drainage, bound), clearance)

  def _check_geometry(self, upstream, clearance):
    for level in self.levels:
      local = [(key, route) for key, route in self.routes.items() if route["level"] == level]
      for (key, first), (other, second) in combinations(local, 2):
        joint = first["train"] == second["train"] and first["leg"] == second["leg"] and bool(set(first["nodes"]) & set(second["nodes"]))
        if _route_gap(first, second, joint) + 1e-9 < clearance:
          self.conflicts.append({"level": level, "route": key, "other": other, "check": "branch-clearance"})
      for key, first in local:
        for other, route in upstream.items():
          second = {"points_m": route.points_m, "outside_m": route.outside_mm / 1000}
          if _route_gap(first, second, first["upstream_segment"] == other) + 1e-9 < clearance:
            self.conflicts.append({"level": level, "route": key, "other": other, "check": "upstream-clearance"})


def _route_gap(first, second, joint):
  minimum = float("inf")
  for a, b in pairwise(first["points_m"]):
    for c, d in pairwise(second["points_m"]):
      if joint and ({tuple(a), tuple(b)} & {tuple(c), tuple(d)}):
        continue
      delta = [max(0., min(x, y) - max(u, v), min(u, v) - max(x, y))
               for x, y, u, v in zip(a, b, c, d, strict=True)]
      minimum = min(minimum, dist(delta, (0, 0, 0)))
  return minimum - (first["outside_m"] + second["outside_m"]) / 2


def _box_gap(route, bounds):
  return min(dist(tuple(max(0., lo - max(a[i], b[i]), min(a[i], b[i]) - hi)
                        for i, (lo, hi) in enumerate(bounds)), (0, 0, 0))
             for a, b in pairwise(route["points_m"])) - route["outside_m"] / 2


def evaluate_fws_branches(model, building, frame):
  if model.frame != building["frame"] or model.frame != frame["frame"]:
    raise ValueError("FWS branches must use the frame and building coordinates.")
  section = frame["sections"]["column"]
  x_half, y_half = section["width"] / 2, section["depth"] / 2
  top = max(frame["levels_m"].values())
  columns = {f"{a}/{b}": ((x - x_half, x + x_half), (y - y_half, y + y_half), (frame["column_base_m"], top))
             for a, x in frame["grid"]["x"].items() for b, y in frame["grid"]["y"].items()}
  clearance = model.minimum_clearance_m
  conflicts = list(model.conflicts)
  for key, route in model.routes.items():
    if any(_box_gap(route, bounds) + 1e-9 < clearance for bounds in columns.values()):
      conflicts.append({"level": route["level"], "route": key, "check": "column-clearance"})
  by_elevation = sorted(frame["levels_m"].values())
  # Keep all new routes clear of the structural floor and the framing below the next floor.
  downstand = max(item["depth"] for item in frame["sections"].values())
  downstand += max((item["section_m"]["depth"] for item in frame["secondary_beams"].values()), default=0.)
  downstand += frame.get("unit_floor_deck_depth_m", 0.)
  crossings = []
  for key, route in model.routes.items():
    bottom = model.levels[route["level"]]
    ceiling = next(elevation for elevation in by_elevation if elevation > bottom)
    radius = route["outside_m"] / 2
    low, high = min(point[2] for point in route["points_m"]) - radius, max(point[2] for point in route["points_m"]) + radius
    if low <= bottom or high >= ceiling:
      crossings.append(key)
    if low < bottom + clearance or high > ceiling - downstand - clearance:
      conflicts.append({"level": route["level"], "route": key, "check": "floor-or-beam-clearance"})

  cases = {}
  for name, scenario in building["scenarios"].items():
    allocation = scenario["fws"]
    paths, units = {}, {}
    for (train, level), distribution in model.distributions.items():
      bank = allocation["hydraulics"]["banks"][train]
      if level not in allocation["delivered_kw_by_terminal"]:
        continue
      flow = bank["flow_m3_s_by_terminal"].get(level, 0.)
      heat = allocation["delivered_kw_by_path"][train].get(level, 0.)
      detail = bank.get("floor_outlets_by_terminal", {}).get(level, {})
      flows = detail.get("flow_m3_s_by_unit")
      if flows is None and "delivered_kw_by_unit_train" in allocation:
        flows = dict.fromkeys(distribution.paths, 0.)
      result = distribution.report(flow, outlet_flows=flows,
                                   critical_unit=detail.get("critical_unit"))
      expected = bank["pressure_components_kpa_by_terminal"].get(level, {}).get("floor_distribution", 0.)
      if not isclose(result["critical_loss_kpa"], expected, abs_tol=1e-8):
        raise ValueError("Floor branch pressure must match the upstream dispatch.")
      paths[f"{level}/{train}"] = {**result, "delivered_kw": heat}
      share = heat / len(distribution.paths)
      for unit in distribution.paths:
        units.setdefault(f"{level}/{unit}", {})[train] = share
    units = allocation.get("delivered_kw_by_unit_train", units)
    cases[name] = {"floor_paths": paths, "delivered_kw_by_unit_train": units,
                   "shortage_kw_by_unit": allocation.get("shortage_kw_by_unit"),
                   "unavailable_branch_segments": allocation.get("unavailable_branch_segments", []),
                   "total_delivered_kw": sum(sum(trains.values()) for trains in units.values())}
  return {
    "schema_version": 1, "state": "assumed", "formal_review": "pending", "frame": model.frame, "basis": model.basis,
    "status": "fail" if conflicts else "pass", "routes": model.routes, "outlets": model.outlets, "conflicts": conflicts,
    "structural_floor_crossings": crossings, "cases": cases,
    "unit_paths": {f"{level}/{train}": {unit: [f"{level}/{key}" for key in path]
                                        for unit, path in distribution.paths.items()}
                   for (train, level), distribution in model.distributions.items()},
    "pending": ["manufacturer-cdu-connection-dimensions-and-valves", "field-balancing-and-individual-outage-dispatch",
                "supports-insulation-and-thermal-expansion", "other-mep-services-and-installation-clearance"],
  }


def define_pipe_routes(stage, root, routes):
  from pxr import Gf, Sdf, UsdGeom

  for key, route in routes.items():
    parent = f"{root}/{_prim_name(key)}"
    group = UsdGeom.Scope.Define(stage, parent).GetPrim()
    group.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(key)
    for i, (a, b) in enumerate(pairwise(route["points_m"])):
      cube = UsdGeom.Cube.Define(stage, f"{parent}/Segment_{i}")
      cube.CreateSizeAttr(1)
      cube.AddTranslateOp().Set(Gf.Vec3d(*((x + y) / 2 for x, y in zip(a, b, strict=True))))
      cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(*(abs(x - y) or route["outside_m"] for x, y in zip(a, b, strict=True)))
      )
      cube.CreateDisplayColorAttr([Gf.Vec3f(*((.16, .47, .76) if route["leg"] == "supply" else (.74, .46, .25)))])


def write_fws_branches_usd(report, path: Path):
  from pxr import Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/FwsBranches").GetPrim())
  define_pipe_routes(stage, "/FwsBranches", report["routes"])
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
