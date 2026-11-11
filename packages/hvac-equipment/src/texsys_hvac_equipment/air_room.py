"""Build the air-cooled room example from SDL and its spatial design inputs."""

from dataclasses import dataclass, field
from itertools import combinations, product
import json
import math
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.evidence import write_json
from texsys.geometry import AnalyticBoxKernel, Box
from texsys.targets import Artifact, BuildContext, TargetResult
from texsys.usd_brep import _prim_name
from texsys_spatial import AxisGrid, DatumPlane
from texsys_structure import ISection, grid_frame

PALETTE = {
  "Slab": (0.42, 0.46, 0.52),
  "Axes": (0.38, 0.56, 0.75),
  "Steel": (0.23, 0.37, 0.57),
  "Supports": (0.51, 0.58, 0.66),
  "Floor": (0.77, 0.81, 0.84),
  "Grilles": (0.12, 0.52, 0.68),
  "PenetrationFrames": (0.38, 0.49, 0.58),
  "Racks": (0.11, 0.16, 0.22),
  "RackRails": (0.39, 0.48, 0.57),
  "RackFronts": (0.18, 0.36, 0.46),
  "Cooling": (0.86, 0.90, 0.93),
  "CoolingGrilles": (0.12, 0.48, 0.58),
  "Walls": (0.82, 0.87, 0.91),
}


def read_json(path: Path):
  return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Room:
  model: dict
  layout: dict
  basis: dict
  definitions: dict
  objects: dict[str, list[tuple[str, Box]]] = field(default_factory=dict)
  equipment: dict[str, Box] = field(default_factory=dict)
  grilles: list[Box] = field(default_factory=list)
  checks: list[dict] = field(default_factory=list)

  def box(self, group, owner, name, origin, size):
    if not all(math.isfinite(value) for value in (*origin, *size)):
      raise ValueError(f"{owner}/{name}: geometry must be finite.")
    self.objects.setdefault(group, []).append((owner, Box(name, tuple(origin), tuple(size))))

  def check(self, name, passed, **evidence):
    self.checks.append({"id": name, "status": "pass" if passed else "fail", **evidence})

  def parameters(self, identity):
    instance = self.model["components"][self.model["root"]]["instances"][identity]
    behavior = self.definitions[instance["component"]].get("behavior", {})
    return {**behavior.get("parameters", {}), **instance.get("parameters", {})}

  def quantity(self, identity, name, unit):
    binding = self.parameters(identity)[name]
    if binding["state"] not in {"known", "assumed"} or binding["unit"] != unit:
      raise ValueError(f"{identity}.{name} needs a known or assumed value in {unit}.")
    if binding["value"] < 0:
      raise ValueError(f"{identity}.{name} must be nonnegative.")
    return binding["value"]


def inside(box, bounds):
  x, y, _ = box.origin
  x1, y1, _ = box.maximum
  return bounds[0] - 1e-8 <= x and bounds[1] - 1e-8 <= y and x1 <= bounds[2] + 1e-8 and y1 <= bounds[3] + 1e-8


def overlap_2d(first, second):
  return all(
    min(first.maximum[axis], second.maximum[axis]) - max(first.origin[axis], second.origin[axis]) > 1e-8
    for axis in (0, 1)
  )


def raised_floor(room, width, length):
  floor = room.layout["raised_floor"]
  pitch, level, thickness = (floor[key] for key in ("pitch_m", "finished_elevation_m", "panel_thickness_m"))
  slab = room.layout["frame"]["slab_elevation_m"]
  nx, ny = round(width / pitch), round(length / pitch)
  if not (math.isclose(nx * pitch, width) and math.isclose(ny * pitch, length)):
    raise ValueError("Room dimensions must fit the raised-floor module.")
  openings = [room.equipment[name] for name in room.layout["cooling_units"]]
  for opening in openings:
    if not inside(opening, (0, 0, width, length)) or any(
      abs(value / pitch - round(value / pitch)) > 1e-8
      for value in (*opening.origin[:2], *opening.maximum[:2])
    ):
      raise ValueError(f"{opening.identity}: downflow opening must align with the raised-floor grid.")
    x, y, _ = opening.origin
    w, d, _ = opening.size
    rim = .08
    for name, origin, size in (
      ("West", (x, y), (rim, d)),
      ("East", (x + w - rim, y), (rim, d)),
      ("South", (x + rim, y), (w - 2 * rim, rim)),
      ("North", (x + rim, y + d - rim), (w - 2 * rim, rim)),
    ):
      room.box("PenetrationFrames", opening.identity, name, (*origin, level - thickness), (*size, thickness))
  cold = room.layout["zones"]["ColdAisle"]["bounds_m"]
  cutouts = 0
  for i, j in product(range(nx), range(ny)):
    owner = f"TILE-{i + 1:02d}-{j + 1:02d}"
    x, y = i * pitch, j * pitch
    panel = Box(owner, (x, y, level - thickness), (pitch, pitch, thickness))
    if any(inside(panel, (*opening.origin[:2], *opening.maximum[:2])) for opening in openings):
      cutouts += 1
      continue
    if inside(panel, cold):
      room.grilles.append(panel)
      for index in range(8):
        room.box("Grilles", owner, f"Bar{index}", (x + .004, y + .004 + index * (pitch - .008) / 8, level - thickness),
                 (pitch - .008, (pitch - .008) / 15, thickness))
    else:
      room.box("Floor", owner, "Panel", (x + .002, y + .002, level - thickness), (pitch - .004, pitch - .004, thickness))
  for i, j in product(range(nx + 1), range(ny + 1)):
    x, y = i * pitch, j * pitch
    if any(opening.origin[0] + 1e-8 < x < opening.maximum[0] - 1e-8
           and opening.origin[1] + 1e-8 < y < opening.maximum[1] - 1e-8 for opening in openings):
      continue
    owner = f"PED-{i:02d}-{j:02d}"
    room.box("Supports", owner, "Base", (x - .05, y - .05, slab), (.10, .10, .008))
    room.box("Supports", owner, "Stem", (x - .015, y - .015, slab + .008),
             (.03, .03, level - slab - thickness - .033))
    room.box("Supports", owner, "Head", (x - .046, y - .046, level - thickness - .025), (.092, .092, .025))
  return nx * ny - cutouts, cutouts


def place_equipment(room, level, grid):
  width, depth, height = room.layout["rack"]["envelope_m"]
  mount = room.layout["rack"]["mounting_width_in"] * .0254
  rail_height = room.layout["rack"]["rack_units"] * .04445
  if mount + .05 > width or rail_height + .08 > height:
    raise ValueError("Rack mounting width or U-space exceeds the enclosure.")
  for row in room.layout["rack_rows"]:
    if "anchor" in row:
      anchor = row["anchor"]
      if "start_m" in row or not isinstance(anchor, dict) or set(anchor) != {"x_axis", "y_axis", "offset_m"}:
        raise ValueError("A rack row must use either start_m or an axis anchor with an offset.")
      start_x, y = grid.point(anchor["x_axis"], anchor["y_axis"], anchor["offset_m"])
    else:
      start_x, y = row["start_m"]
    if row["front"] not in {"+Y", "-Y"}:
      raise ValueError(f"Unsupported rack orientation: {row['front']}")
    for index, identity in enumerate(row["members"]):
      x = start_x + index * width
      if identity in room.equipment:
        raise ValueError(f"Duplicate equipment placement: {identity}")
      room.equipment[identity] = Box(identity, (x, y, level), (width, depth, height))
      front = y + depth - .018 if row["front"] == "+Y" else y
      room.box("Racks", identity, "Enclosure", (x + .009, y + .018, level + .018),
               (width - .018, depth - .036, height - .018))
      room.box("RackFronts", identity, "Door", (x + .035, front, level + .055),
               (width - .07, .018, height - .10))
      for side, rail_x in (("Left", x + (width - mount) / 2), ("Right", x + (width + mount) / 2)):
        room.box("RackRails", identity, side, (rail_x - .009, front, level + .06), (.018, .019, rail_height))
      for level_index in range(6):
        room.box("RackRails", identity, f"DoorSlat{level_index}", (x + .075, front, level + .16 + level_index * .28),
                 (width - .15, .022, .009))
  for identity, unit in room.layout["cooling_units"].items():
    if identity in room.equipment:
      raise ValueError(f"Duplicate equipment placement: {identity}")
    x, y = unit["origin_m"]
    w, d, h = unit["envelope_m"]
    room.equipment[identity] = Box(identity, (x, y, level), (w, d, h))
    room.box("Cooling", identity, "Cabinet", (x, y, level), (w, d, h))
    room.box("CoolingGrilles", identity, "Return", (x + .12, y + .12, level + h), (w - .24, d - .24, .014))
    duct_x, duct_y, duct_w, duct_d = x + .08, y + .08, w - .16, d - .16
    wall, drop = .012, .12
    for name, origin, size in (
      ("OutletWest", (duct_x, duct_y), (wall, duct_d)),
      ("OutletEast", (duct_x + duct_w - wall, duct_y), (wall, duct_d)),
      ("OutletSouth", (duct_x + wall, duct_y), (duct_w - 2 * wall, wall)),
      ("OutletNorth", (duct_x + wall, duct_y + duct_d - wall), (duct_w - 2 * wall, wall)),
    ):
      room.box("CoolingGrilles", identity, name, (*origin, level - drop), (*size, drop))


def layout_checks(room, width, length):
  system = room.model["components"][room.model["root"]]
  declared = {identity for identity, instance in system["instances"].items()
              if instance["component"] in {"Rack42U", "DownflowCRAC"}}
  room.check("equipment-placement-coverage", declared == set(room.equipment),
             missing=sorted(declared - room.equipment.keys()), unexpected=sorted(room.equipment.keys() - declared))
  outside = [box.identity for box in room.equipment.values() if not inside(box, (0, 0, width, length))]
  room.check("equipment-within-room", not outside, outside=outside)
  kernel = AnalyticBoxKernel()
  overlaps = [[a.identity, b.identity] for a, b in combinations(room.equipment.values(), 2)
              if kernel.intersects(a, b) and overlap_2d(a, b)]
  room.check("equipment-envelope-overlap", not overlaps, overlaps=overlaps)
  steel = room.objects["Steel"]
  interferences = sorted({(box.identity, owner) for box in room.equipment.values() for owner, member in steel
                          if kernel.intersects(box, member) and overlap_2d(box, member)})
  room.check("equipment-steel-interference", not interferences, overlaps=interferences)
  zones = room.layout["zones"]
  for name in ("ColdAisle", "HotSouth", "HotNorth"):
    x0, y0, x1, y1 = zones[name]["bounds_m"]
    aisle = Box(name, (x0, y0, 0), (x1 - x0, y1 - y0, 1))
    limit = room.layout["clearances_m"]["cold_aisle" if name == "ColdAisle" else "hot_aisle"]
    obstructions = [box.identity for box in room.equipment.values() if overlap_2d(aisle, box)]
    room.check(f"clear-aisle-{name}", y1 - y0 + 1e-8 >= limit and inside(aisle, (0, 0, width, length))
               and not obstructions, actual_m=round(y1 - y0, 6), minimum_m=limit, obstructions=obstructions)
  cold = zones["ColdAisle"]["bounds_m"]
  front_errors = []
  for row in room.layout["rack_rows"]:
    for identity in row["members"]:
      box = room.equipment[identity]
      face = box.maximum[1] if row["front"] == "+Y" else box.origin[1]
      edge = cold[1] if row["front"] == "+Y" else cold[3]
      if abs(face - edge) > 1e-8 or box.origin[0] < cold[0] - 1e-8 or box.maximum[0] > cold[2] + 1e-8:
        front_errors.append(identity)
  room.check("rack-fronts-face-cold-aisle", not front_errors, invalid=front_errors)
  racks = [room.equipment[name] for row in room.layout["rack_rows"] for name in row["members"]]
  left_edge, right_edge = min(box.origin[0] for box in racks), max(box.maximum[0] for box in racks)
  cooling = [room.equipment[name] for name in room.layout["cooling_units"]]
  left_obstruction = max((box.maximum[0] for box in cooling if box.origin[0] <= left_edge), default=0)
  right_obstruction = min((box.origin[0] for box in cooling if box.origin[0] >= right_edge), default=width)
  clearance = min(left_edge - left_obstruction, right_obstruction - right_edge)
  limit = room.layout["clearances_m"]["side_access"]
  room.check("end-of-row-access", clearance + 1e-8 >= limit, actual_m=round(clearance, 6), minimum_m=limit)


def air_checks(room):
  basis = room.basis
  racks = [name for row in room.layout["rack_rows"] for name in row["members"]]
  units = room.layout["cooling_units"]
  system = room.model["components"][room.model["root"]]
  expected = {("SupplyPlenum.outlet", "ColdAisle.inlet")}
  for name in units:
    expected.update({("RoomReturn.outlet", f"{name}.return"), (f"{name}.downflow", "SupplyPlenum.inlet")})
  for name in racks:
    expected.update({("ColdAisle.outlet", f"{name}.front"), (f"{name}.rear", "RoomReturn.inlet")})
  air_ports = {f"{name}.{port}" for name, instance in system["instances"].items()
               for port, signature in room.definitions[instance["component"]].get("ports", {}).items()
               if signature["kind"] == "room-air"}
  air_connections = [(edge["from"], edge["to"]) for edge in system["connections"]
                     if edge["from"] in air_ports or edge["to"] in air_ports]
  actual = set(air_connections)
  room.check("air-path", expected == actual and len(actual) == len(air_connections),
             missing=sorted(expected - actual), unexpected=sorted(actual - expected))
  it_load = sum(room.quantity(name, "it_load_kw", "kW") for name in racks)
  load = it_load + basis["additional_room_load_kw"]
  denominator = basis["air_density_kg_m3"] * basis["air_cp_kj_kg_k"] * (
    basis["return_temperature_c"] - basis["supply_temperature_c"])
  if denominator <= 0 or load < 0:
    raise ValueError("Airflow check requires positive air heat transport and nonnegative room load.")
  needed = load / denominator
  grille_flow = len(room.grilles) * room.layout["raised_floor"]["grille_airflow_m3_s"]
  for name, scenario in basis["scenarios"].items():
    unavailable = set(scenario["unavailable_cooling_units"])
    if unavailable - units.keys():
      raise ValueError(f"{name}: unknown unavailable cooling unit.")
    active = sorted(units.keys() - unavailable)
    capacity = sum(room.quantity(unit, "sensible_capacity_kw", "kW") for unit in active)
    air = sum(room.quantity(unit, "supply_airflow_m3_s", "m3/s") for unit in active)
    room.check(f"air-budget-{name}", capacity >= load and min(air, grille_flow) >= needed, active=active,
               it_load_kw=it_load, total_load_kw=load, sensible_capacity_kw=capacity,
               required_airflow_m3_s=round(needed, 4), nominal_unit_airflow_m3_s=round(air, 4),
               nominal_grille_airflow_m3_s=round(grille_flow, 4), status_basis=basis["status"])


def write_scene(room, path, level, modules):
  from texsys_hvac_equipment.room_modules import place_rack_modules

  stage = Usd.Stage.CreateInMemory()
  root = UsdGeom.Xform.Define(stage, "/AirCooledRoom").GetPrim()
  stage.SetDefaultPrim(root)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  root.SetCustomDataByKey("frame", room.layout["frame"]["id"])
  root.SetCustomDataByKey("raisedFloorFinish", room.layout["raised_floor"]["solid_finish"])

  def anchor(parent, name, identity):
    prim = UsdGeom.Xform.Define(stage, f"{parent}/{_prim_name(name)}").GetPrim()
    if identity:
      prim.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String, custom=True).Set(identity)
    return str(prim.GetPath())

  for group, items in room.objects.items():
    if room.layout.get("rack_equipment") and group in {"Racks", "RackFronts", "RackRails"}:
      continue
    parent = anchor("/AirCooledRoom", group, None)
    owners = {}
    for owner, box in items:
      if owner not in owners:
        owners[owner] = anchor(parent, owner, owner)
      cube = UsdGeom.Cube.Define(stage, f"{owners[owner]}/{_prim_name(box.identity)}")
      cube.CreateSizeAttr(1.0)
      cube.CreateDisplayColorAttr([Gf.Vec3f(*PALETTE[group])])
      cube.AddTranslateOp().Set(Gf.Vec3d(*(a + b / 2 for a, b in zip(box.origin, box.size))))
      cube.AddScaleOp().Set(Gf.Vec3f(*box.size))

  zones = anchor("/AirCooledRoom", "Zones", None)
  for name, zone in room.layout["zones"].items():
    prim = stage.GetPrimAtPath(anchor(zones, name, name))
    x0, y0, x1, y1 = zone["bounds_m"]
    bottom, top = ((room.layout["frame"]["slab_elevation_m"], level) if zone["kind"] == "underfloor-supply"
                   else (level, room.layout["steel"]["beam_top_m"]))
    prim.SetCustomDataByKey("kind", zone["kind"])
    prim.SetCustomDataByKey("minMeters", Gf.Vec3d(x0, y0, bottom))
    prim.SetCustomDataByKey("maxMeters", Gf.Vec3d(x1, y1, top))
  place_rack_modules(stage, room, modules)
  if stage.GetCompositionErrors():
    raise ValueError("The room scene has unresolved USD dependencies.")
  path.parent.mkdir(parents=True, exist_ok=True)
  if not stage.Flatten(addSourceFileComment=False).Export(str(path)):
    raise OSError(f"Cannot export {path}.")


def build_air_room(context: BuildContext) -> TargetResult:
  from texsys.project import inspect_project
  from texsys_hvac_equipment.air_room_plan import write_plan
  from texsys_hvac_equipment.room_modules import read_project_module_library, write_module_library

  project = inspect_project(context.root)
  source, assets = context.root / project.source, context.root / project.assets
  model = read_json(source / "system.sdl.json")
  definitions = dict(model["components"])
  for alias, path in model.get("imports", {}).items():
    document = read_json(source / path)
    definitions[alias] = document["components"][document["root"]]
  room = Room(model, read_json(source / "room.layout.json"), read_json(assets / "air-design-basis.json"), definitions)
  modules = read_project_module_library(context.root, source) if room.layout.get("rack_equipment") else {}
  frame = room.layout["frame"]
  if frame["units"] != "m" or frame["up_axis"] != "Z":
    raise ValueError("The room layout uses meters with Z up.")
  axes = room.layout["axes"]
  grid = AxisGrid(frame["id"], axes["x"], axes["y"])
  if any(len(axis) < 2 or next(iter(axis.values())) != 0 for axis in (grid.x, grid.y)):
    raise ValueError("Each room axis set needs at least two lines starting at 0.")
  width, length = max(grid.x.values()), max(grid.y.values())
  level = room.layout["raised_floor"]["finished_elevation_m"]
  steel = room.layout["steel"]
  slab_datum = DatumPlane(grid.frame, "structural-slab", frame["slab_elevation_m"])
  beam_datum = DatumPlane(grid.frame, "steel-beam-top", steel["beam_top_m"])
  slab = slab_datum.elevation_m
  room.box("Slab", "L01-Slab", "StructuralSlab", (-.2, -.2, slab - .20), (width + .4, length + .4, .20))
  for x_id, x in grid.x.items():
    room.box("Axes", f"AX-{x_id}", "Line", (x - .007, -.75, slab + .004), (.014, length + 1.5, .009))
  for y_id, y in grid.y.items():
    room.box("Axes", f"AX-{y_id}", "Line", (-.75, y - .007, slab + .004), (width + 1.5, .014, .009))
  for owner, part in grid_frame(grid, steel["column_lines"], slab_datum, beam_datum,
                                ISection(*steel["column_section_m"]), ISection(*steel["beam_section_m"]),
                                base_plate=(.34, .34, .024)):
    room.box("Steel", owner, part.identity, part.origin, part.size)
  place_equipment(room, level, grid)
  total_panels, cutouts = raised_floor(room, width, length)
  if room.layout["door"]["wall"] != "east":
    raise ValueError("This room layout expects an east wall opening.")
  door_start, door_stop = room.layout["door"]["opening_y_m"]
  if not 0 < door_start < door_stop < length:
    raise ValueError("The east wall opening must lie between the corner axes.")
  room.box("Walls", "EastWall", "SouthReturn", (width, 0, level), (.14, door_start, 2.7))
  room.box("Walls", "EastWall", "NorthReturn", (width, door_stop, level), (.14, length - door_stop, 2.7))
  room.box("Walls", "EastWall", "Lintel", (width, door_start, level + 2.1), (.14, door_stop - door_start, .6))
  room.box("Walls", "NorthWall", "Wall", (0, length, level), (width, .14, 2.7))
  room.box("Walls", "SouthWall", "Cutaway", (0, -.14, slab), (width, .14, level - slab + .10))
  room.box("Walls", "WestWall", "Cutaway", (-.14, 0, slab), (.14, length, level - slab + .10))
  layout_checks(room, width, length)
  air_checks(room)
  status = "pass" if all(check["status"] == "pass" for check in room.checks) else "fail"
  directory = context.build_path / "hvac" / "air-room"
  scene, plan, report = (directory / name for name in ("room.usda", "floor-plan.svg", "checks.json"))
  products = [("room-scene", scene), ("room-plan", plan), ("room-checks", report)]
  if modules:
    library_scene = directory / "module-library.usda"
    directory.mkdir(parents=True, exist_ok=True)
    write_module_library(modules, library_scene)
    products.append(("room-module-library", library_scene))
  write_scene(room, scene, level, modules)
  write_plan(room, plan, width, length, grid)
  counts = {
    "racks": sum(len(row["members"]) for row in room.layout["rack_rows"]),
    "cooling_units": len(room.layout["cooling_units"]),
    "floor_panels": total_panels,
    "floor_slots_used_for_downflow_openings": cutouts,
    "solid_panels": total_panels - len(room.grilles),
    "supply_grilles": len(room.grilles),
    "pedestals": len({owner for owner, _ in room.objects["Supports"]}),
    "steel_columns": len({owner for owner, _ in room.objects["Steel"] if owner.startswith("COL-")}),
    "steel_beams": len({owner for owner, _ in room.objects["Steel"] if owner.startswith("BEAM-")}),
  }
  write_json(report, {"schema_version": 1, "status": status, "basis": room.basis,
                      "room_m": [width, length], "raised_floor_m": level - slab, "counts": counts,
                      "module_packages": {entry: module.manifest["version"] for entry, module in modules.items()},
                      "checks": room.checks})
  diagnostics = tuple({"code": check["id"], "message": f"Air room check failed: {check['id']}"}
                      for check in room.checks if check["status"] == "fail")
  return TargetResult(status, tuple(Artifact(name, path.relative_to(context.root).as_posix()) for name, path in products),
                      diagnostics=diagnostics, details=counts)
