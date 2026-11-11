"""Place the locked cooling modules and reconcile the overhead water outlets."""

from itertools import combinations
from math import dist

from pxr import Sdf, Usd, UsdGeom

from texsys.project import inspect_project
from texsys.sdl_catalog import read_sdl_catalog
from texsys.usd_brep import _prim_name
from texsys_hvac_equipment.room_modules import read_room_modules

from .floor import _overlap, _rectangle


def evaluate_cooling_equipment(root, source, unit, floor, building, branches, output):
  catalog = read_sdl_catalog(source, root)
  if catalog.diagnostics:
    raise ValueError("Cooling equipment requires a valid locked SDL catalog.")
  package = catalog.packages["hvac-equipment"]
  library = read_room_modules(package.root, package.root / inspect_project(package.root).source)
  equipment = unit["equipment_layout"]
  if equipment.keys() != (unit["cdus"].keys() | set(unit["air_units"])):
    raise ValueError("Every room CDU and precision air unit needs a module placement.")
  if building["frame"] != branches["frame"]:
    raise ValueError("Cooling equipment and water branches must share the building frame.")
  supports = floor.get("cooling_equipment_loads", {})
  if supports and supports.keys() != equipment.keys():
    raise ValueError("Structural load records must cover all installed cooling equipment.")

  zones = {name: zone for name, zone in building["floor_zones"].items() if zone["role"] == "unit"}
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  scene = UsdGeom.Xform.Define(stage, "/CoolingEquipment")
  stage.SetDefaultPrim(scene.GetPrim())
  modules, placements, conflicts = {}, {}, []
  for device, placement in sorted(equipment.items()):
    entry = placement["entry"]
    module = library[entry.removeprefix("@hvac-equipment/")]
    if (placement["module_version"] != module.manifest["version"]
        or dist(module.bounds.GetMin(), (0, 0, 0)) > 1e-8
        or tuple(placement["envelope_m"]) != module.envelope):
      raise ValueError(f"{device}: geometry, specification and module version must agree.")
    modules[device] = module

  for level, elevation in sorted(building["levels_m"].items()):
    for zone_name, zone in sorted(zones.items()):
      origin = (*zone["origin_m"], elevation)
      local_plans = {}
      for device, placement in sorted(equipment.items()):
        module = modules[device]
        offset = placement["origin_m"]
        identity = f"{level}/{zone_name}/{device}"
        local_plans[device] = _rectangle(offset[:2], module.envelope[:2], zone["size_m"], identity)
        if offset[2] + module.envelope[2] > building["unit_height_m"]:
          raise ValueError(f"{identity}: equipment exceeds the room height.")
        world = tuple(a + b for a, b in zip(origin, offset, strict=True))
        node = module.place(stage, f"/CoolingEquipment/{_prim_name(identity)}", world)
        node.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)
        node.GetPrim().SetCustomDataByKey("texsys:definition", placement["entry"])
        transform = node.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        placements[identity] = {"entry": placement["entry"], "origin_m": world,
                                "ports_m": {port: tuple(transform.Transform(point))
                                            for port, point in module.ports.items()}}
        if supports:
          support = supports[device]
          placements[identity]["structural_support"] = {
            "floor_bay": f"{level}/{zone_name}", "load_kn": support["vertical_load_kn"],
            "contact_ids": [f"{identity}/{name}" for name in sorted(support["feet"])],
          }
      occupied = {**floor["rack_loads"], **local_plans}
      conflicts.extend({"level": level, "zone": zone_name, "objects": [a, b], "check": "equipment-overlap"}
                       for a, b in combinations(occupied, 2) if _overlap(occupied[a], occupied[b]))

  actual_connections = set()
  for level, trains in branches["outlets"].items():
    for zones_by_train in trains.values():
      for zone, legs in zones_by_train.items():
        for outlet in legs.values():
          identity, port = f"{level}/{zone}/{outlet['device']}", outlet["port"]
          if ((identity, port) in actual_connections
              or dist(placements[identity]["ports_m"][port], outlet["point_m"]) > 1e-8):
            raise ValueError(f"{identity}.{port}: branch outlet must match a unique placed CDU port.")
          actual_connections.add((identity, port))
  expected_connections = {(f"{level}/{zone}/{device}", port)
                          for level in building["levels_m"] for zone in zones
                          for device, cdu in unit["cdus"].items() for port in cdu["facility_ports"]}
  if actual_connections != expected_connections:
    raise ValueError("Every placed CDU facility port must have a branch outlet.")
  if stage.GetCompositionErrors() or not stage.Flatten(addSourceFileComment=False).Export(str(output)):
    raise ValueError("Cannot export the placed cooling equipment.")
  return {
    "schema_version": 1, "state": "assumed", "frame": building["frame"], "status": "fail" if conflicts else "pass",
    "modules": equipment, "placements": placements, "conflicts": conflicts,
    "counts": {"placements": len(placements), "fws_connections": len(actual_connections)},
    "pending": ["manufacturer-selection", "equipment-anchorage", "maintenance-clearance",
                "technology-water-routing", "air-routing", "power-routing"],
  }
