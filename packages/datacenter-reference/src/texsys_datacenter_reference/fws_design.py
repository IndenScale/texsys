"""Bind and extract one declared FWS loop, then recompute its head budget."""

from itertools import pairwise
import json
from math import dist, isfinite

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_hvac_dynamics.facility_hydraulics import PipeHydraulics
from texsys_hvac_dynamics.hydraulics import pipe_loss

from .spatial_design import (
  MODELS,
  SCENE,
  artifact,
  missing_input,
  model_report,
  read_versioned,
  source_path,
  staged_json,
  vector,
)


def _positive(value, label):
  if type(value) not in (int, float) or not isfinite(value) or value <= 0:
    raise ValueError(f"{label}: expected a positive finite value.")
  return value


def _loop_connections(sdl, specification, leg):
  matches = {connection["id"]: connection for connection in sdl["connections"]}
  first_id, second_id = specification["connections"]
  first, second = matches[first_id], matches[second_id]
  start, first_header = first["from"], first["to"]
  second_header, end = second["from"], second["to"]
  header, kind = first_header.split(".")[0], "fws_" + leg
  expected_header = "FwsSupplyHeader" if leg == "supply" else "FwsReturnHeader"
  if (header != expected_header or second_header.split(".")[0] != header
      or start.split(".")[1] != kind or end.split(".")[1] != kind):
    raise ValueError(f"{leg}: selected links must connect matching FWS ports through {expected_header}.")
  return start, end, header


def _port(layout, reference, required_side):
  instance, port = reference.split(".")
  record = layout["objects"][instance]
  if record["module"].split("/")[-2] != required_side or record["port_normals"][port] != [0, -1, 0]:
    raise ValueError(f"{reference}: expected a south-facing port of {required_side}.")
  return tuple(record["ports_m"][port])


def _approach(port, tier, offset):
  x, y, z = port
  return port, (x, y - offset, z), (x, y - offset, tier)


def _route_points(start, end, specification):
  tier = _positive(specification["tier_m"], "route tier")
  offset = _positive(specification["port_clearance_m"], "port clearance")
  xh, yh = vector(specification["header_xy_m"], 2, "header connection point")
  if tier <= max(start[2], end[2]) or not end[1] - offset < yh < start[1] - offset:
    raise ValueError("Header point must be between the cooling and IT approaches and above both ports.")
  # The two route legs are generated in plant-to-terminal order, then oriented by SDL.
  plant, terminal = _approach(start, tier, offset), _approach(end, tier, offset)
  points = (*plant, (start[0], yh, tier), (xh, yh, tier), (xh, terminal[-1][1], tier),
            *reversed(terminal))
  return points


def _takeoff(points):
  length, bends = 0, 0
  last = None
  for start, end in pairwise(points):
    active = [(axis, 1 if b > a else -1) for axis, (a, b) in enumerate(zip(start, end, strict=True)) if a != b]
    if len(active) != 1:
      raise ValueError("Pipe routing needs nonzero orthogonal segments.")
    direction, = active
    if last is not None and last[0] == direction[0] and last != direction:
      raise ValueError("Pipe routing contains an immediate reversal.")
    bends += last is not None and last[0] != direction[0]
    length += dist(start, end)
    last = direction
  return length, bends


def _route_scene(context, routes):
  stage = Usd.Stage.Open(str(context.build_path / SCENE))
  for leg, route in routes.items():
    prefix = "/AIDC/FWS/" + leg.capitalize()
    owner = UsdGeom.Scope.Define(stage, prefix).GetPrim()
    owner.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set("FWS." + leg)
    color = Gf.Vec3f(0.1, 0.4, 0.7) if leg == "supply" else Gf.Vec3f(0.7, 0.25, 0.15)
    for index, (a, b) in enumerate(pairwise(route["points_m"]), start=1):
      pipe = UsdGeom.Cube.Define(stage, f"{prefix}/Segment_{index}")
      pipe.CreateSizeAttr(1)
      pipe.AddTranslateOp().Set(Gf.Vec3d(*((x + y) / 2 for x, y in zip(a, b, strict=True))))
      pipe.AddScaleOp().Set(Gf.Vec3f(*(abs(x - y) or route["outside_diameter_m"]
                                      for x, y in zip(a, b, strict=True))))
      pipe.CreateDisplayColorAttr([color])
  output = context.build_path / "scenes/aidc-fws.usda"
  if not stage.GetRootLayer().Export(str(output)):
    raise OSError(f"Could not export {output.name}.")
  return output


def build_routes(context):
  path = source_path(context, "fws-routing.json")
  if not path.is_file():
    return missing_input(context, path, "fws-routing")
  specification = read_versioned(path)
  if specification.get("state") != "assumed" or not specification.get("basis"):
    raise ValueError("FWS routing requires a declared assumption basis.")
  layout = staged_json(context, f"{MODELS}/layout.json")
  sdl = json.loads(path.with_name("system.sdl.json").read_text(encoding="utf-8"))
  assembly = sdl["components"][sdl["root"]]
  site = staged_json(context, f"{MODELS}/site.json")
  xmin, ymin, xmax, ymax = site["boundary_m"]
  routes = {}
  for leg in ("supply", "return"):
    record = specification["legs"][leg]
    start, end, header = _loop_connections(assembly, record, leg)
    plant, terminal = (start, end) if leg == "supply" else (end, start)
    points = _route_points(_port(layout, plant, "cooling-container"), _port(layout, terminal, "it-container"), record)
    if leg == "return":
      points = tuple(reversed(points))
    if not all(xmin <= x <= xmax and ymin <= y <= ymax for x, y, _ in points):
      raise ValueError(f"FWS {leg} route leaves the parcel.")
    length, bends = _takeoff(points)
    routes[leg] = {"from": start, "to": end, "header": header, "connections": record["connections"],
                   "points_m": points, "length_m": length, "elbows": bends,
                   "tier_m": record["tier_m"], "header_xy_m": record["header_xy_m"],
                   "outside_diameter_m": _positive(record["outside_diameter_m"], f"{leg} outside diameter"),
                   "baseline_length_m": _positive(record["baseline_length_m"], f"{leg} design budget")}
  connections = (routes["supply"]["from"], routes["return"]["to"],
                 routes["supply"]["to"], routes["return"]["from"])
  plant_in, plant_out, terminal_in, terminal_out = (name.split(".")[0] for name in connections)
  if plant_in != plant_out or terminal_in != terminal_out:
    raise ValueError("Supply and return must close on the same plant and terminal instances.")
  output = _route_scene(context, routes)
  report = model_report(context, "fws-routing", {
    "frame": site["frame"], "basis": specification["basis"], "state": specification["state"],
    "routes": routes, "plant": plant_in, "terminal": terminal_in, "scope": "one conceptual plant-to-IT FWS loop",
    "terminal_design_it_kw": layout["objects"][terminal_in]["design_it_kw"],
  })
  return TargetResult(report.status, (*report.artifacts, artifact(context, "fws-scene", output, preview={
    "kind": "scene", "source_map": {
      **layout["source_map"],
      **{f"FWS.{leg}": {"file": path.relative_to(context.root).as_posix(), "path": f"legs.{leg}"} for leg in routes},
    },
  })))


def build_hydraulics(context):
  path = source_path(context, "fws-hydraulics.json")
  if not path.is_file():
    return missing_input(context, path, "fws-hydraulics")
  criteria = read_versioned(path)
  route_model = staged_json(context, f"{MODELS}/fws-routing.json")
  if criteria["terminal"] != route_model["terminal"] or criteria.get("state") != "assumed" or not criteria.get("basis"):
    raise ValueError("Hydraulic criteria must bind the routed terminal and declare an assumption basis.")
  positive = {key: _positive(criteria.get(key), key) for key in (
    "liquid_fraction", "delta_t_k", "cp_kj_kg_k", "density_kg_m3", "viscosity_pa_s",
    "inside_diameter_m", "max_velocity_m_s", "elbow_k", "head_available_kpa",
    "other_losses_kpa",
  )}
  if positive["liquid_fraction"] > 1:
    raise ValueError("Liquid heat fraction cannot exceed one.")
  design_load = _positive(route_model["terminal_design_it_kw"], "terminal design IT load")
  heat = design_load * positive["liquid_fraction"]
  density, viscosity = positive["density_kg_m3"], positive["viscosity_pa_s"]
  flow = heat / (positive["delta_t_k"] * positive["cp_kj_kg_k"] * density)
  legs, diagnostics = {}, []
  for leg, route in route_model["routes"].items():
    pipe = PipeHydraulics(route["length_m"], positive["inside_diameter_m"], criteria["roughness_m"],
                          route["elbows"] * positive["elbow_k"], positive["max_velocity_m_s"])
    if pipe.diameter_m >= route["outside_diameter_m"]:
      raise ValueError(f"{leg}: pipe bore must be smaller than its outside diameter.")
    actual = pipe_loss(flow, length=pipe.length_m, diameter=pipe.diameter_m, roughness=pipe.roughness_m,
                       minor_loss=pipe.minor_loss_coefficient, density=density, viscosity=viscosity) / 1000
    velocity = flow / pipe.area_m2
    legs[leg] = {"connections": route["connections"], "extracted_length_m": pipe.length_m,
                 "baseline_length_m": route["baseline_length_m"], "elbows": route["elbows"],
                 "minor_loss_coefficient": pipe.minor_loss_coefficient, "pressure_drop_kpa": actual,
                 "velocity_m_s": velocity}
    if velocity > pipe.max_velocity_m_s:
      diagnostics.append({"rule": "AIDC_FWS_VELOCITY", "status": "fail", "objects": route["connections"],
                          "message": f"{leg}: velocity {velocity:.3f} m/s exceeds {pipe.max_velocity_m_s:g} m/s."})
  required = sum(result["pressure_drop_kpa"] for result in legs.values()) + positive["other_losses_kpa"]
  margin = positive["head_available_kpa"] - required
  if margin < 0:
    diagnostics.append({"rule": "AIDC_FWS_HEAD", "status": "fail", "objects": [route_model["terminal"]],
                        "message": f"Required {required:.2f} kPa exceeds available {positive['head_available_kpa']:g} kPa."})
  return model_report(context, "fws-hydraulics", {
    "basis": criteria["basis"], "state": criteria["state"], "terminal": route_model["terminal"],
    "method": "Fixed-flow closed-loop Darcy-Weisbach (Haaland), no shared-branch balancing",
    "criteria": {**positive, "roughness_m": criteria["roughness_m"]}, "flow_m3_s": flow, "legs": legs,
    "terminal_design_it_kw": design_load, "liquid_heat_kw": heat,
    "required_kpa": required, "head_margin_kpa": margin,
    "energy_balance_kw": density * flow * positive["delta_t_k"] * positive["cp_kj_kg_k"],
    "route_evidence": {"file": f"{MODELS}/fws-routing.json",
                       "digest": file_digest(context.build_path / MODELS / "fws-routing.json")},
  }, diagnostics)
