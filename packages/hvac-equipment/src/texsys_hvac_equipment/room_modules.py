"""Validate and compose the assets supplied by the air-room module library."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.dependencies import LOCK_NAME, _read_lock
from texsys.formal import check_sdl_sources, read_sdl_sources
from texsys.library import library_entries
from texsys.module_package import read_module_manifests
from texsys.project import inspect_project
from texsys.usd_brep import _prim_name


@dataclass(frozen=True)
class RoomModule:
  entry: str
  manifest: dict
  geometry: Path
  bounds: Gf.Range3d
  envelope: tuple[float, float, float]
  ports: dict[str, Gf.Vec3d]
  properties: dict

  def place(self, stage, path, origin, angle=0):
    node = UsdGeom.Xform.Define(stage, path)
    prim = node.GetPrim()
    prim.GetReferences().AddReference(str(self.geometry))
    prim.SetCustomDataByKey("texsys:definition", self.entry)
    prim.SetCustomDataByKey("texsys:version", self.manifest["version"])
    node.AddTranslateOp().Set(Gf.Vec3d(*origin))
    if angle:
      node.AddRotateZOp().Set(angle)
    return node


def read_room_modules(root: Path, source: Path, *, namespace: str = "") -> dict[str, RoomModule]:
  documents, diagnostics, _ = read_sdl_sources(source, root)
  diagnostics.extend(check_sdl_sources(documents, source.relative_to(root).as_posix()))
  entries = library_entries(set(documents))
  manifests, errors = read_module_manifests(source, entries, documents)
  if diagnostics or errors:
    messages = [f"{item.file}: {item.message}" for item in diagnostics]
    messages.extend(f"{path.relative_to(root)}: {message}" for path, message in errors.items())
    raise ValueError("\n".join(messages))
  modules = {}
  for entry, manifest in manifests.items():
    folder = (source / entry).parent
    assets = manifest["assets"]
    geometry_id, symbol_id, specification_id = (
      _review_asset(assets, kind, entry) for kind in ("geometry", "symbol", "specification")
    )
    geometry = folder / assets[geometry_id]["path"]
    stage = Usd.Stage.Open(str(geometry))
    if (not stage or stage.GetCompositionErrors() or not stage.GetDefaultPrim()
        or UsdGeom.GetStageMetersPerUnit(stage) != 1 or UsdGeom.GetStageUpAxis(stage) != "Z"):
      raise ValueError(f"{entry}: expected composed geometry in meters, Z up, with a default prim.")
    bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(
      stage.GetDefaultPrim()).ComputeAlignedRange()
    spec = json.loads((folder / assets[specification_id]["path"]).read_text(encoding="utf-8"))
    envelope = tuple(spec["envelope_m"])
    if bounds.IsEmpty() or not all(
      math.isfinite(measured) and math.isclose(measured, expected, rel_tol=1e-6, abs_tol=1e-9)
      for measured, expected in zip(bounds.GetSize(), envelope, strict=True)
    ):
      raise ValueError(f"{entry}: declared envelope does not match the geometry.")
    symbol = ET.parse(folder / assets[symbol_id]["path"]).getroot()
    anchors = {element.get("id"): element.get("data-port") for element in symbol.iter() if element.get("id")}
    definition = documents[entry]["components"][documents[entry]["root"]]
    bindings = manifest.get("port_bindings", {})
    if set(bindings) != set(definition.get("ports", {})):
      raise ValueError(f"{entry}: the example binds every declared port in both drawing and geometry.")
    interfaces = spec.get("interfaces")
    if interfaces is not None and interfaces.keys() != bindings.keys():
      raise ValueError(f"{entry}: declared interfaces must cover all bound ports.")
    ports = {}
    for port, locators in bindings.items():
      frame = UsdGeom.Xform(stage.GetPrimAtPath(locators[geometry_id]))
      if not frame or anchors.get(locators[symbol_id]) != port:
        raise ValueError(f"{entry}: unresolved geometry or drawing anchor for port {port}.")
      position = frame.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
      normal = frame.GetPrim().GetAttribute("texsys:normal").Get()
      if not all(map(math.isfinite, position)) or normal is None or not math.isclose(normal.GetLength(), 1.0):
        raise ValueError(f"{entry}: invalid coordinate or normal for port {port}.")
      if interfaces is not None and not all(
        math.isclose(actual, expected, abs_tol=1e-8)
        for actual, expected in zip((*position, *normal),
                                    (*interfaces[port]["position_m"], *interfaces[port]["normal"]), strict=True)
      ):
        raise ValueError(f"{entry}: geometry differs from the declared interface for port {port}.")
      ports[port] = position
    key = f"{namespace}:{entry}" if namespace else entry
    modules[key] = RoomModule(key, manifest, geometry, bounds, envelope, ports, spec["properties"])
  return modules


def read_project_module_library(root: Path, source: Path) -> dict[str, RoomModule]:
  modules = read_room_modules(root, source)
  if (root / LOCK_NAME).is_file():
    # The build runner verifies the dependency lock before and after this target.
    _, dependencies = _read_lock(root)
    for dependency in dependencies:
      if dependency.name == "structure":
        project = inspect_project(dependency.root)
        if project is None:
          raise ValueError("The locked structure library is missing its project manifest.")
        modules.update(read_room_modules(dependency.root, dependency.root / project.source, namespace=dependency.name))
  return modules


def _review_asset(assets: dict, kind: str, entry: str) -> str:
  keys = [key for key, asset in assets.items() if asset["kind"] == kind]
  if len(keys) != 1:
    raise ValueError(f"{entry}: the room review needs exactly one {kind} asset.")
  return keys[0]


def write_module_library(modules: dict[str, RoomModule], path: Path) -> None:
  """Build a portable review scene from the manifest-declared geometries."""
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  root = UsdGeom.Xform.Define(stage, "/RoomModuleLibrary")
  stage.SetDefaultPrim(root.GetPrim())
  pitch_x, pitch_y = (max(2.0, max((module.envelope[axis] for module in modules.values()), default=0) + .35)
                     for axis in (0, 1))
  for index, (entry, module) in enumerate(sorted(modules.items())):
    row, column = divmod(index, 4)
    min_corner = module.bounds.GetMin()
    namespace, _, _ = entry.rpartition(":")
    name = Path(entry).stem.removesuffix(".sdl")
    identity = _prim_name(f"{namespace}:{name}" if namespace else name)
    node = module.place(stage, f"/RoomModuleLibrary/{identity}",
                        (column * pitch_x - min_corner[0], row * pitch_y - min_corner[1], -min_corner[2]))
    node.GetPrim().SetDisplayName(module.manifest["name"])
  if stage.GetCompositionErrors():
    raise ValueError("The module library scene has unresolved USD dependencies.")
  if not stage.Flatten(addSourceFileComment=False).Export(str(path)):
    raise OSError(f"Cannot export {path}.")


def place_rack_modules(stage, room, modules: dict[str, RoomModule]) -> None:
  """Use the placed rack frames to mount declared equipment and locate cable ends."""
  layout = room.layout.get("rack_equipment", {})
  if not layout:
    return
  rack_nodes = {}
  rack_module = modules[room.model["imports"]["Rack42U"]]
  frame = rack_module.properties
  for row in room.layout["rack_rows"]:
    for identity in row["members"]:
      box = room.equipment[identity]
      turned = row["front"] == "+Y"
      if not all(math.isclose(a, b) for a, b in zip(box.size, rack_module.envelope, strict=True)):
        raise ValueError(f"{identity}: layout envelope differs from the rack module.")
      x, y, _ = box.maximum if turned else box.origin
      rack_nodes[identity] = rack_module.place(stage, f"/AirCooledRoom/Racks/{_prim_name(identity)}",
                                              (x, y, box.origin[2]), 180 if turned else 0)
      rack_nodes[identity].GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)

  system = room.model["components"][room.model["root"]]
  connections = {(record["from"], record["to"]) for record in system["connections"]}
  occupied: dict[str, set[int]] = {}
  equipment = {}
  for identity, placement in sorted(layout.items()):
    instance = system["instances"][identity]
    module = modules[room.model["imports"][instance["component"]]]
    rack_id, unit = placement["rack"], placement["u"]
    count = module.properties["rack_units"]
    if type(unit) is not int or unit < 1 or unit + count - 1 > frame["rack_units"]:
      raise ValueError(f"{identity}: rack U position exceeds available mounting space.")
    slots = set(range(unit, unit + count))
    used = occupied.setdefault(rack_id, set())
    if slots & used or (f"{identity}.mount", f"{rack_id}.rails") not in connections:
      raise ValueError(f"{identity}: overlapping rack U positions or missing SDL mount connection.")
    used.update(slots)
    width, depth, _ = module.envelope
    rack_width, rack_depth, _ = rack_module.envelope
    if width > rack_width or frame["rail_front_m"] + depth > rack_depth:
      raise ValueError(f"{identity}: equipment envelope exceeds the rack.")
    # Put equipment beneath the rack transform but outside its referenced geometry scope.
    local = ((rack_width - width) / 2, frame["rail_front_m"],
             frame["rail_base_m"] + (unit - 1) * frame["unit_pitch_m"])
    node = module.place(stage, f"{rack_nodes[rack_id].GetPath()}/Equipment/{_prim_name(identity)}", local)
    node.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)
    equipment[identity] = (node, module)

  def world_port(reference):
    identity, port = reference.split(".")
    node, module = equipment[identity]
    return node.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).Transform(module.ports[port])

  routes = room.layout.get("cable_routes", {})
  if routes:
    UsdGeom.Scope.Define(stage, "/AirCooledRoom/CableRoutes")
  for identity, route in sorted(routes.items()):
    instance = system["instances"][identity]
    module = modules[room.model["imports"][instance["component"]]]
    if ((route["from"], f"{identity}.end_a") not in connections
        or (f"{identity}.end_b", route["to"]) not in connections):
      raise ValueError(f"{identity}: cable route does not match the SDL connections.")
    points = [world_port(route["from"]), *(Gf.Vec3d(*point) for point in route["via"]), world_port(route["to"])]
    if not all(math.isfinite(coordinate) for point in points for coordinate in point):
      raise ValueError(f"{identity}: route points must be finite.")
    curve = UsdGeom.BasisCurves.Define(stage, f"/AirCooledRoom/CableRoutes/{_prim_name(identity)}")
    curve.CreateTypeAttr("linear")
    curve.CreateCurveVertexCountsAttr([len(points)])
    curve.CreatePointsAttr([Gf.Vec3f(*point) for point in points])
    curve.CreateWidthsAttr([module.properties["jacket_diameter_m"]])
    curve.SetWidthsInterpolation("constant")
    curve.CreateDisplayColorAttr([Gf.Vec3f(*module.properties["route_color"])])
    prim = curve.GetPrim()
    prim.SetCustomDataByKey("texsys:definition", module.entry)
    prim.SetCustomDataByKey("texsys:version", module.manifest["version"])
    prim.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)
    length = sum((stop - start).GetLength() for start, stop in zip(points, points[1:]))
    prim.SetCustomDataByKey("texsys:routeLengthM", length)
