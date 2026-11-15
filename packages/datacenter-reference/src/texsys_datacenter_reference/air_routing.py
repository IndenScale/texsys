"""Fit compact supply transitions and physical rack exhaust apertures in the IT pods."""

import csv
from itertools import combinations
from math import dist, isclose

from pxr import Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import bounds, space
from .checks import load_design
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _system_objects
from .pod_layout import MODEL as PODS, _world_space
from .spatial_design import (
  MODELS,
  artifact,
  missing_input,
  model_report,
  number,
  read_versioned,
  source_path,
  staged_json,
)
from .tcs_routing import MODEL as TCS, SCENE as BASE, TYPICAL as TYPICAL_BASE, _envelopes
from .telecom_coordination import _prim


MODEL = f"{MODELS}/air-routing.json"
SCENE = "scenes/aidc-air-coordinated.usda"
TYPICAL = "scenes/aidc-it-pod-air.usda"


def _box(minimum, maximum):
  return bounds(Space(tuple(minimum), tuple(maximum)))


def _dimensions(row, *fields):
  return {key: number(row[key], key) for key in fields}


def _bindings(assembly, nets):
  racks = {name for name, child in assembly.children.items() if child.family == "liquid-cooled-rack"}
  units = {name for name, child in assembly.children.items() if child.family == "precision-air-unit"}
  if set(nets) != {"A", "B"} or not racks:
    raise ValueError(f"{assembly.name}: declare both air paths and their placed racks.")
  definition = assembly.definition
  results = {}
  for side, declared in sorted(nets.items()):
    net = definition["nets"][declared]
    links = [row for row in definition["connections"] if row["id"] in net["connections"]]
    if net["kind"] != "room-air" or len(links) != len(net["connections"]):
      raise ValueError(f"{assembly.name}/{side}: expected a unique room-air net.")
    by_rack = {}
    for rack in sorted(racks):
      intake = f"{rack}.air_in_{side.lower()}"
      exhaust = f"{rack}.air_out_{side.lower()}"
      incoming = [row for row in links if row["to"] == intake]
      outgoing = [row for row in links if row["from"] == exhaust]
      if len(incoming) != 1 or len(outgoing) != 1:
        raise ValueError(f"{assembly.name}/{side}/{rack}: declare both air directions.")
      (inc,) = incoming
      (out,) = outgoing
      unit, port = inc["from"].split(".")
      if unit not in units or port != "supply" or out["to"] != f"{unit}.return":
        raise ValueError(f"{assembly.name}/{side}/{rack}: route supply and return to the paired unit.")
      by_rack[rack] = {
        "unit": unit,
        "inlet": intake,
        "outlet": exhaust,
        "supply_sdl": inc["id"],
        "return_sdl": out["id"],
      }
    results[side] = by_rack
    if len(links) != 2 * len(racks):
      raise ValueError(f"{assembly.name}/{side}: every air connection must bind this physical allocation.")
  a_units = {row["unit"] for row in results["A"].values()}
  b_units = {row["unit"] for row in results["B"].values()}
  if a_units & b_units or a_units | b_units != units:
    raise ValueError(f"{assembly.name}: each precision air unit must belong to one independent electrical side.")

  def groups(side):
    return {
      frozenset(name for name, row in side.items() if row["unit"] == unit)
      for unit in {row["unit"] for row in side.values()}
    }

  if groups(results["A"]) != groups(results["B"]):
    raise ValueError(f"{assembly.name}: provide a matched spare for each rack distribution group.")
  return results


def _shaft(pod, name, cabinet, shell, config):
  points = cabinet["ports_m"]
  x0, y0, _ = cabinet["minimum_m"]
  x1, y1, z1 = cabinet["maximum_m"]
  port = points["air_out_a"]
  _, y, z = port
  normal = tuple(cabinet["port_normals"]["air_out_a"])
  if normal not in ((0, -1, 0), (0, 1, 0)) or not isclose(points["air_out_b"][1], y, abs_tol=1e-8):
    raise ValueError(f"{pod}/{name}: bind both exhaust paths to a common rear face.")
  south = normal[1] < 0
  wall = "D-S" if south else "D-N"
  face = y0 if south else y1
  if not isclose(face, y, abs_tol=1e-8):
    raise ValueError(f"{pod}/{name}: the air exhaust port must terminate at the cabinet boundary.")
  candidates = [
    shell[f"{wall}-{piece}"]
    for piece in ("L", "R")
    if shell[f"{wall}-{piece}"]["minimum_m"][0] <= x0 + 1e-8 and x1 <= shell[f"{wall}-{piece}"]["maximum_m"][0] + 1e-8
  ]
  if len(candidates) != 1:
    raise ValueError(f"{pod}/{name}: exhaust shaft requires backing along one flank panel.")
  (panel,) = candidates
  inside = panel["maximum_m"][1] if south else panel["minimum_m"][1]
  gap = (face - inside) * (-normal[1])
  span = x1 - x0 - 2 * config["side_inset_m"]
  finish = config["outlet_m"]
  if min(gap, span) <= 0 or not config["bottom_m"] < z < z1 < finish:
    raise ValueError(f"{pod}/{name}: the rear exhaust must have a clear slot and open above the rack.")
  for key in ("air_out_a", "air_out_b"):
    exhaust = points[key]
    if (
      tuple(cabinet["port_normals"][key]) != normal
      or not x0 + config["side_inset_m"] <= exhaust[0] <= x1 - config["side_inset_m"]
      or not config["bottom_m"] <= exhaust[2] < finish
    ):
      raise ValueError(f"{pod}/{name}: {key} must discharge into its rear slot.")
  volume = _box(
    (x0 + config["side_inset_m"], min(inside, face), config["bottom_m"]),
    (x1 - config["side_inset_m"], max(inside, face), finish),
  )
  return {
    **volume,
    "outlet_z_m": finish,
    "gap_m": gap,
    "exhaust_port_m": port,
    "flow_area_m2": gap * span,
    "hydraulic_diameter_m": 2 * span * gap / (span + gap),
    "upflow_length_m": finish - z,
  }


def _unit(name, cabinet, aisle, supply, ret):
  sx, sy, sz = cabinet["ports_m"]["supply"]
  rx, ry, rz = cabinet["ports_m"]["return"]
  toward = cabinet["port_normals"]["return"][1]
  if toward not in (-1, 1) or tuple(cabinet["port_normals"]["supply"]) != (0, 0, 1):
    raise ValueError(f"{name}: need top supply and an aisle-facing return.")
  opening = (
    aisle["minimum_m"][1] + supply["aisle_inset_m"] if toward > 0 else (aisle["maximum_m"][1] - supply["aisle_inset_m"])
  )
  depth = supply["port_depth_m"] / 2
  side = sy - toward * depth
  w = supply["duct_width_m"] / 2
  base = supply["duct_bottom_m"]
  tip = base + supply["duct_height_m"]
  if base < sz or (opening - sy) * toward <= depth:
    raise ValueError(f"{name}: supply elbow must clear the cabinet top and face the aisle.")
  neck = _box((sx - w, sy - depth, sz), (sx + w, sy + depth, base))
  outlet = _box((sx - w, min(side, opening), base), (sx + w, max(side, opening), tip))
  intake = _box(
    (rx - ret["width_m"] / 2, min(ry, ry + toward * ret["depth_m"]), rz - ret["height_m"] / 2),
    (rx + ret["width_m"] / 2, max(ry, ry + toward * ret["depth_m"]), rz + ret["height_m"] / 2),
  )
  return {
    "supply_port_m": [sx, sy, sz],
    "return_port_m": [rx, ry, rz],
    "tip_m": [sx, opening, (base + tip) / 2],
    "neck": neck,
    "elbow": outlet,
    "return_face": intake,
    "neck_area_m2": 4 * w * depth,
    "supply_area_m2": 2 * w * supply["duct_height_m"],
    "supply_length_m": abs(opening - sy) + (base + tip) / 2 - sz,
    "return_area_m2": ret["width_m"] * ret["height_m"],
  }


def _pod(name, placement, binding, params):
  cabinets = placement["local_equipment"]
  racks = {
    key: _shaft(name, key, cabinets[key], placement["local_shell"], params["rack_riser"]) for key in binding["A"]
  }
  names = {row["unit"] for side in binding.values() for row in side.values()}
  units = {
    key: _unit(key, cabinets[key], placement["aisle"], params["supply"], params["return_grille"])
    for key in sorted(names)
  }
  for side, branches in binding.items():
    for key, row in branches.items():
      unit = units[row["unit"]]
      cabinet = cabinets[key]
      interface = row["inlet"].split(".")[1]
      port = cabinet["ports_m"][interface]
      normal = cabinet["port_normals"][interface][1]
      if normal * (space(placement["aisle"]).center_m[1] - port[1]) <= 0:
        raise ValueError(f"{name}/{side}/{key}: supply must face the working aisle.")
      row.update(inlet_m=port, capture_distance_m=dist(unit["tip_m"], port))
  return {"binding": binding, "racks": racks, "units": units}


def build_air_routing(context):
  path = source_path(context, "air-routing.json")
  if not path.is_file():
    return missing_input(context, path, "air-routing")
  cfg = read_versioned(path)
  if cfg.get("state") != "assumed" or not cfg.get("basis"):
    raise ValueError("A project basis is required for pod air distribution.")
  values = {
    "supply": _dimensions(
      cfg["supply"],
      "port_depth_m",
      "duct_width_m",
      "duct_bottom_m",
      "duct_height_m",
      "aisle_inset_m",
      "roughness_factor",
      "elbow_k",
      "terminal_k",
      "maximum_velocity_m_s",
    ),
    "return_grille": _dimensions(cfg["return_grille"], "width_m", "height_m", "depth_m", "local_loss_k"),
    "rack_riser": _dimensions(
      cfg["rack_riser"], "side_inset_m", "bottom_m", "outlet_m", "minimum_gap_m", "roughness_factor", "turn_k"
    ),
  }
  placement = staged_json(context, PODS)
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "air-routing", {}, design.diagnostics)
  pods = {
    name: _pod(name, record, _bindings(design.instances[name], cfg["nets"]), values)
    for name, record in placement["pods"].items()
  }
  result = model_report(
    context,
    "air-routing",
    {
      "basis": cfg["basis"],
      "parameters": values,
      "pods": pods,
      "typical": placement["typical"],
      "pod_digest": file_digest(context.build_path / PODS),
    },
  )
  target = context.build_path / "schedules/aidc-air-sections.csv"
  target.parent.mkdir(parents=True, exist_ok=True)
  with target.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("pod", "identity", "service", "flow_area_m2", "length_m", "rear_gap_m"))
    for pod, row in pods.items():
      for key, rack in row["racks"].items():
        table.writerow((pod, key, "rack-exhaust", rack["flow_area_m2"], rack["upflow_length_m"], rack["gap_m"]))
      for key, unit in row["units"].items():
        table.writerow((pod, key, "supply", unit["supply_area_m2"], unit["supply_length_m"], ""))
        table.writerow((pod, key, "free-return", unit["return_area_m2"], "", ""))
  return TargetResult(result.status, (*result.artifacts, artifact(context, "air-sections", target)))


def _parts(record):
  shafts = {f"{name}-EX": row for name, row in record["racks"].items()}
  supply = {
    f"{name}-{tag}": row[key]
    for name, row in record["units"].items()
    for tag, key in (("N", "neck"), ("S", "elbow"), ("R", "return_face"))
  }
  return shafts | supply


def _coordination(air, layout, piping, fire):
  issues = []
  for pod, record in air["pods"].items():
    placed = layout["pods"][pod]
    sides = piping["pods"][pod]
    pipe = {
      f"{key}/{index + 1}": shape
      for key, index, _, shape in _envelopes(sides["A"]["pipes"] | sides["B"]["pipes"], piping["materials"])
    }
    local = {key: space(row) for key, row in (placed["local_equipment"] | placed["local_shell"]).items()}
    local["aisle"] = space(placed["aisle"])
    local.update({key: space(row["access"]) for key, row in placed["doors"].items()})
    parts = {key: space(row) for key, row in _parts(record).items()}

    def issue(key, other, rule):
      issues.append(
        {
          "rule": rule,
          "status": "fail",
          "objects": [pod, key, other],
          "message": f"{pod}/{key}: air installation conflicts with {other}.",
        }
      )

    for key, rack in record["racks"].items():
      if rack["gap_m"] + 1e-8 < air["parameters"]["rack_riser"]["minimum_gap_m"]:
        issue(key, "rear-gap", "AIDC_AIR_REAR_GAP")
    for key, shape in parts.items():
      for other, obstacle in (local | pipe).items():
        if shape.intersects(obstacle):
          issue(key, other, "AIDC_AIR_CLEARANCE")
      world = space(_world_space(bounds(shape), placed["placement"]))
      for other, obstacle in fire[pod]["objects"].items():
        if world.intersects(obstacle):
          issue(key, other, "AIDC_AIR_FIRE")
    for (key, shape), (other, remote) in combinations(parts.items(), 2):
      if shape.intersects(remote):
        issue(key, other, "AIDC_AIR_OVERLAP")
  return issues


def _save(context, air, layout, *, typical=False):
  source, destination = (TYPICAL_BASE, TYPICAL) if typical else (BASE, SCENE)
  stage = Usd.Stage.Open(str(context.build_path / source))
  if stage is None:
    raise ValueError(f"Cannot open {source}.")
  selected = [air["typical"]] if typical else list(air["pods"])
  if not typical:
    # Keep the equipment assemblies in the aggregate model with their primary
    # cabinet shapes. The typical pod preserves every equipment detail.
    for prim in stage.Traverse():
      path = str(prim.GetPath())
      if (
        "/Equipment/" in path
        and "/Asset/Geometry/" in path
        and prim.GetTypeName() == "Cube"
        and prim.GetName() != "Cabinet"
      ):
        UsdGeom.Imageable(prim).MakeInvisible()
  preview = {}
  design_path = source_path(context, "air-routing.json").relative_to(context.root).as_posix()
  for name in selected:
    placed = layout["pods"][name]["placement"]
    for key, row in _parts(air["pods"][name]).items():
      identity = f"{name}/{key}"
      color = (0.73, 0.45, 0.26) if key.endswith(("-R", "-EX")) else (0.32, 0.64, 0.72)
      shell = row if typical else _world_space(row, placed)
      _prim(stage, f"/AIDC/Air/{name}/{key.replace('-', '_')}", identity, space(shell), color)
      field = "rack_riser" if key.endswith("-EX") else "return_grille" if key.endswith("-R") else "supply"
      preview[identity] = {"file": design_path, "path": field}
  file = context.build_path / destination
  if not stage.Flatten(False).Export(str(file)):
    raise OSError(f"Cannot export {file.name}.")
  return artifact(
    context, "air-typical-scene" if typical else "air-scene", file, preview={"kind": "scene", "source_map": preview}
  )


def build_air_coordination(context):
  air = staged_json(context, MODEL)
  layout = staged_json(context, PODS)
  _, fire = _system_objects(staged_json(context, FIRE))
  issues = _coordination(air, layout, staged_json(context, TCS), fire)
  return model_report(
    context,
    "air-coordination",
    {
      "issues": issues,
      "source_digest": file_digest(context.build_path / MODEL),
      "pods_checked": len(air["pods"]),
      "installation_volumes": sum(len(_parts(row)) for row in air["pods"].values()),
    },
    issues,
  )


def build_air_scene(context):
  air = staged_json(context, MODEL)
  layout = staged_json(context, PODS)
  return TargetResult("pass", (_save(context, air, layout), _save(context, air, layout, typical=True)))
