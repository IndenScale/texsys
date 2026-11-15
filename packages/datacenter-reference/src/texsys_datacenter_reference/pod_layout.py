"""Compile placed IT pod equipment from locked child geometry and access requirements."""

import csv
from itertools import combinations

from pxr import Gf, Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import bounds, space
from .checks import load_design
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _system_objects
from .spatial_design import MODELS, _catalog, _world, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json, vector
from .telecom_coordination import _prim
from .wet_utilities import SCENE as BASE

MODEL = f"{MODELS}/pod-layout.json"
TYPICAL = "scenes/aidc-it-pod-typical.usda"
SCENE = "scenes/aidc-pods-coordinated.usda"
FAMILIES = {"liquid-cooled-rack", "coolant-distribution-unit", "precision-air-unit"}


def _box(a, b):
  return bounds(Space(tuple(a), tuple(b)))


def _local(point, child_low, size, rotation):
  if rotation == 180:
    point = (size[0]-point[0], size[1]-point[1], point[2])
  return tuple(a+b for a, b in zip(child_low, point, strict=True))


def _world_space(row, placement):
  points = [_world(placement["origin_m"], placement["rotation_deg"], p) for p in
            (row["minimum_m"], row["maximum_m"])]
  return _box([min(a, b) for a, b in zip(*points, strict=True)], [max(a, b) for a, b in zip(*points, strict=True)])


def _fit_issue(pod, rule, first, last, text):
  return {"rule": f"AIDC_POD_{rule}", "objects": [pod, first, last], "status": "fail", "message": f"{pod}: {text}"}


def _equipment(instance, raw, overrides):
  parts = {}
  for key, item in sorted(instance.children.items()):
    if item.family not in FAMILIES and key not in raw["local_equipment_size_m"]:
      continue
    if item.family in FAMILIES and item.specification.get("frame") != {
      "origin": "cabinet-minimum-corner", "units": "m", "up_axis": "Z",
    }:
      raise ValueError(f"{instance.name}/{key}: expected locked cabinet-local geometry.")
    override = overrides.get(key, {})
    if set(override) - {"minimum_m", "rotation_deg"}:
      raise ValueError(f"{instance.name}/{key}: overrides may set only installed pose.")
    xyz = vector(override.get("minimum_m", instance.specification["instance_layout_m"][key]), 3, f"{key} placement")
    size = vector(item.specification["envelope_m"] if item.family in FAMILIES else raw["local_equipment_size_m"][key],
                  3, f"{key} installed dimensions")
    rotation = override.get("rotation_deg", raw["rotation_deg"].get(key, 0))
    if type(rotation) is not int or rotation not in (0, 180) or min(size) <= 0:
      raise ValueError(f"{instance.name}/{key} needs a positive geometry envelope and an aisle-facing orientation.")
    parts[key] = {**_box(xyz, [a+b for a, b in zip(xyz, size, strict=True)]), "dimensions_m": size,
                  "document": item.document if item.family in FAMILIES else None, "family": item.family,
                  "rotation_deg": rotation, "ports_m": {
                    port: _local(record["position_m"], xyz, size, rotation)
                    for port, record in item.specification.get("interfaces", {}).items()},
                  "port_normals": {port: ((-norm[0], -norm[1], norm[2]) if rotation else norm)
                                   for port, record in item.specification.get("interfaces", {}).items()
                                   if (norm := record["normal"]) is not None}}
  if set(overrides) - parts.keys():
    raise ValueError(f"{instance.name}: overrides must bind a modeled physical SDL child.")
  return parts


def _shell(size, raw, headroom):
  length, width, height = size
  t = number(raw["wall_thickness_m"], "module wall thickness")
  roof = number(raw["roof_thickness_m"], "module roof thickness")
  skin = number(raw.get("flank_skin_m", t), "flank enclosure thickness")
  if skin > t:
    raise ValueError("The flank skin must fit within the wall allocation.")
  ax, a, bx, b = vector(raw["working_aisle_bounds_m"], 4, "working aisle bounds")
  if not (t <= ax < bx <= length-t and t <= a < b <= width-t and height-roof >= headroom):
    raise ValueError("Working aisle needs containment within the clear pod envelope and sufficient headroom.")
  shell = {"W-L": _box((0, 0, 0), (t, width, height-roof)),
           "W-R": _box((length-t, 0, 0), (length, width, height-roof)),
           "ROOF": _box((0, 0, height-roof), (length, width, height))}
  aisle = _box((ax, a, 0), (bx, b, headroom))
  doors = {}
  for key, entry in raw["doors"].items():
    center = number(entry["center_x_m"], f"{key} center")
    clear = number(entry["clear_width_m"], f"{key} opening width")
    high = number(entry["clear_height_m"], f"{key} opening head")
    lo, hi = center-clear/2, center+clear/2
    side = entry["side"]
    if not ax <= lo < hi <= bx or not headroom <= high < height-roof or side not in {"north", "south"}:
      raise ValueError(f"{key}: place a full-height doorway wholly within reach of the working aisle.")
    low, upper = (0., skin) if side == "south" else (width-skin, width)
    exterior, end = (0., a) if side == "south" else (width, b)
    for suffix, x0, x1, z0 in (("L", t, lo, 0), ("R", hi, length-t, 0), ("H", lo, hi, high)):
      shell[f"{key}-{suffix}"] = _box((x0, low, z0), (x1, upper, height-roof))
    doors[key] = {"side": side, "clear_width_m": clear, "clear_height_m": high, "exterior_m": [center, exterior, 0.],
                  "access": _box((lo, min(exterior, end), 0), (hi, max(exterior, end), headroom))}
  if {ent["side"] for ent in doors.values()} != {"north", "south"} or len(doors) != 2:
    raise ValueError("The current IT pod requires one door to each hall flank.")
  return shell, aisle, doors


def _fit(pod, size, shell, aisle, doors, equipment, limits):
  issues = []
  clear = space(aisle)
  width = clear.size_m[1]
  if width+1e-8 < limits["minimum_aisle_width_m"]:
    issues.append(_fit_issue(pod, "AISLE", "AISLE", "width", f"working aisle is only {width:.3f} m clear."))
  volume = Space((0., 0., 0.), tuple(size))
  checked = {key: space(row) for key, row in equipment.items()}
  routes = {"AISLE": clear, **{key: space(row["access"]) for key, row in doors.items()}}
  for name, shape in checked.items():
    if not volume.contains_space(shape):
      issues.append(_fit_issue(pod, "ENVELOPE", name, "POD", f"{name} leaves the pod envelope."))
    for feature, barrier in routes.items():
      if barrier.intersects(shape):
        issues.append(_fit_issue(pod, "ACCESS", name, feature, f"{name} obstructs {feature}."))
    for wall, record in shell.items():
      if shape.intersects(space(record)):
        issues.append(_fit_issue(pod, "SHELL", name, wall, f"{name} intersects shell part {wall}."))
  for first, second in combinations(checked, 2):
    if checked[first].intersects(checked[second]):
      issues.append(_fit_issue(pod, "COLLISION", first, second, f"{first} overlaps {second}."))
  clearances = {}
  for key, row in equipment.items():
    if row["family"] not in FAMILIES:
      continue
    # Device -Y is the declared service face. A 180-degree installation faces north.
    minimum, maximum = row["minimum_m"], row["maximum_m"]
    depth = limits["minimum_equipment_service_depth_m"]
    positive = row["rotation_deg"] == 180
    front = maximum[1] if positive else minimum[1]
    facing_limit = clear.maximum_m[1] if positive else clear.minimum_m[1]
    clearance = facing_limit-front if positive else front-facing_limit
    clearances[key] = {"service_face": "+Y" if positive else "-Y", "depth_to_aisle_limit_m": clearance}
    if clearance < depth:
      issues.append(_fit_issue(pod, "SERVICE_DEPTH", key, "AISLE", f"{key} needs {depth:g} m service depth."))
    lo, hi = (front, front+depth) if positive else (front-depth, front)
    work = Space((minimum[0], lo, minimum[2]), (maximum[0], hi, maximum[2]))
    if not volume.contains_space(work) or any(work.intersects(space(part)) for part in shell.values()):
      issues.append(_fit_issue(pod, "SERVICE_WALL", key, "SHELL", f"{key} service space intersects or leaves the shell."))
    for other, obstruction in checked.items():
      if other != key and work.intersects(obstruction):
        issues.append(_fit_issue(pod, "SERVICE", key, other, f"{other} intrudes into {key} service space."))
  return clearances, issues


def build_pod_layout(context):
  source = source_path(context, "pod-layout.json")
  if not source.is_file():
    return missing_input(context, source, "pod-layout")
  config = read_versioned(source)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("Specify the assumed IT pod arrangement and clearance basis.")
  layout = staged_json(context, f"{MODELS}/layout.json")
  design = load_design(source.parent, context.root)
  if design.diagnostics:
    return model_report(context, "pod-layout", {}, design.diagnostics)
  limits = {name: number(config[name], name) for name in (
    "minimum_aisle_width_m", "minimum_aisle_headroom_m", "minimum_equipment_service_depth_m")}
  pods, issues = {}, []
  overrides = config.get("installation_overrides", {})
  for name, instance in sorted(design.instances.items()):
    if instance.family != "it-container":
      continue
    placed = layout["objects"][name]
    spec = instance.specification
    raw = spec["construction"]
    size = spec["envelope_m"]
    equipment = _equipment(instance, raw, overrides.get(name, {}))
    shell, aisle, doors = _shell(size, raw, limits["minimum_aisle_headroom_m"])
    clearances, findings = _fit(name, size, shell, aisle, doors, equipment, limits)
    issues.extend(findings)
    pods[name] = {"size_m": size, "local_equipment": equipment, "local_shell": shell, "aisle": aisle,
                  "doors": {key: {**record, "world_m": _world(placed["origin_m"], placed["rotation_deg"], record["exterior_m"])}
                            for key, record in doors.items()}, "placement": placed, "service_clearances": clearances}
  if set(overrides) - pods.keys():
    raise ValueError("Pod installation overrides must identify a placed SDL IT pod.")
  typical = config["typical_pod"]
  if typical not in pods:
    raise ValueError("Select a placed SDL IT pod for typical deliverables.")
  report = model_report(context, "pod-layout", {
    "basis": config["basis"], "limits": limits, "issues": issues,
    "layout_digest": file_digest(context.build_path/f"{MODELS}/layout.json"), "pods": pods, "typical": typical,
  })
  target = context.build_path/"schedules/aidc-pod-equipment.csv"
  target.parent.mkdir(parents=True, exist_ok=True)
  with target.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("pod", "equipment", "family", "document", "world_x", "world_y", "world_z",
                    "dx_m", "dy_m", "dz_m", "local_rotation_deg", "service_depth_m"))
    for pod, row in pods.items():
      for key, part in row["local_equipment"].items():
        xyz = _world_space(part, row["placement"])["minimum_m"]
        clearance = row["service_clearances"].get(key, {}).get("depth_to_aisle_limit_m")
        table.writerow((pod, key, part["family"], part["document"], *(f"{value:.3f}" for value in xyz),
                        *part["dimensions_m"], part["rotation_deg"], "" if clearance is None else f"{clearance:.3f}"))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "pod-equipment", target)))


def _pod_prims(stage, catalog, pod, row, *, cutaway=False):
  root = f"/AIDC/ITPods/{pod}"
  group = UsdGeom.Xform.Define(stage, root)
  loc = row["placement"]["origin_m"] if not cutaway else (0., 0., 0.)
  group.AddTranslateOp().Set(Gf.Vec3d(*loc))
  group.AddRotateZOp().Set(row["placement"]["rotation_deg"] if not cutaway else 0)
  for key, part in row["local_equipment"].items():
    target = f"{root}/Equipment/{key}"
    origin = list(part["minimum_m"])
    if part["document"]:
      document = part["document"]
      geometry = catalog.files[document].parent/catalog.modules[document]["assets"]["geometry"]["path"]
      dimensions = part["dimensions_m"]
      transform = UsdGeom.Xform.Define(stage, target)
      rotation = part["rotation_deg"]
      if rotation:
        origin[0] += dimensions[0]
        origin[1] += dimensions[1]
      transform.AddTranslateOp().Set(Gf.Vec3d(*origin))
      transform.AddRotateZOp().Set(rotation)
      asset = stage.DefinePrim(target+"/Asset")
      if not asset.GetReferences().AddReference(str(geometry)):
        raise ValueError(f"Cannot compose locked child asset for {pod}/{key}.")
      asset.SetCustomDataByKey("identity", f"{pod}/{key}")
    else:
      _prim(stage, target, f"{pod}/{key}", space(part), (.45, .52, .63))
  for key, item in row["local_shell"].items():
    if cutaway and (key == "ROOF" or key.startswith("D-S") or key == "W-L"):
      continue
    path = f"{root}/Shell/{key.replace('-', '_')}"
    _prim(stage, path, f"{pod}/{key}", space(item), (.58, .65, .69))
    stage.GetPrimAtPath(path).SetCustomDataByKey("envelope", True)
  if cutaway:
    _prim(stage, root+"/WorkingAisle", f"{pod}/通道", Space((.08, .08, -.035), (row["size_m"][0]-.08, row["size_m"][1]-.08, 0.)),
          (.73, .77, .77))


def _preview(context, pods):
  source = source_path(context, "pod-layout.json").relative_to(context.root).as_posix()
  layout = staged_json(context, f"{MODELS}/layout.json")
  refs = {f"{pod}/{key}": {"file": source, "path": f"installation_overrides.{pod}.{key}"}
          for pod, row in pods.items() for key in row["local_equipment"]}
  return {"kind": "scene", "source_map": {**layout["source_map"], **refs}}


def build_pod_scene(context):
  model = staged_json(context, MODEL)
  source = source_path(context, "pod-layout.json")
  catalog, _, _, _ = _catalog(source.parent, context)
  typical = model["typical"]
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/AIDC").GetPrim())
  _pod_prims(stage, catalog, typical, model["pods"][typical], cutaway=True)
  target = context.build_path/TYPICAL
  target.parent.mkdir(parents=True, exist_ok=True)
  if not stage.Flatten(False).Export(str(target)):
    raise OSError(f"Cannot export {target.name}.")
  return TargetResult("pass", (artifact(context, "typical-pod-scene", target, preview=_preview(context, {typical: model["pods"][typical]})),))


def build_pod_coordination(context):
  model = staged_json(context, MODEL)
  _, fire = _system_objects(staged_json(context, FIRE))
  issues = list(model["issues"])
  for pod, row in model["pods"].items():
    excluded = {key: _world_space(part, row["placement"]) for key, part in
                (row["local_equipment"] | row["local_shell"]).items()}
    for key, volume in fire[pod]["objects"].items():
      issues.extend(_fit_issue(pod, "FIRE_CLASH", key, name, f"{key} overlaps {name}.")
                    for name, shape in excluded.items() if space(shape).intersects(volume))
  result = model_report(context, "pod-coordination", {
    "issues": issues, "pod_count": len(model["pods"]), "equipment_count": sum(len(r["local_equipment"]) for r in model["pods"].values()),
    "layout_digest": file_digest(context.build_path/MODEL), "base_scene_digest": file_digest(context.build_path/BASE),
  }, issues)
  if issues:
    return result
  source = source_path(context, "pod-layout.json")
  catalog, _, _, _ = _catalog(source.parent, context)
  stage = Usd.Stage.Open(str(context.build_path/BASE))
  if stage is None:
    raise ValueError("Cannot open the federated building scene.")
  for pod, row in model["pods"].items():
    stage.RemovePrim(f"{row['placement']['prim_path']}/Asset/Geometry/Envelope")
    _pod_prims(stage, catalog, pod, row)
  target = context.build_path/SCENE
  if not stage.Flatten(False).Export(str(target)):
    raise OSError(f"Cannot export {target.name}.")
  return TargetResult(result.status, (*result.artifacts, artifact(context, "pod-coordination-scene", target, preview=_preview(context, model["pods"]))))
