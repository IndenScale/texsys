"""Compile AIDC site, grid, datums and coordinated asset placements."""

from itertools import combinations
import json
import math

from pxr import Gf, Sdf, Tf, Usd, UsdGeom

from texsys.evidence import write_json
from texsys.formal import resolve_sdl_imports
from texsys.library import library_entries
from texsys.project import inspect_project
from texsys.sdl_catalog import read_sdl_catalog
from texsys.targets import Artifact, TargetResult
from texsys_spatial import AxisGrid, DatumPlane, Space


MODELS = "datacenter-reference/spatial"
SCENE = "scenes/aidc-coordination.usda"
DISCIPLINES = {"architecture", "structure", "it", "electrical", "mechanical"}


def source_path(context, file):
  return context.root / inspect_project(context.root).source / file


def staged_json(context, file):
  return json.loads((context.build_path / file).read_text(encoding="utf-8"))


def read_versioned(path):
  data = json.loads(path.read_text(encoding="utf-8"))
  if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
    raise ValueError(f"{path.name}: expected a schema_version 1 object.")
  return data


def vector(value, size, label):
  if (not isinstance(value, (list, tuple)) or len(value) != size
      or any(type(n) not in (int, float) or not math.isfinite(n) for n in value)):
    raise ValueError(f"{label}: expected {size} finite coordinates in meters.")
  return tuple(value)


def artifact(context, identity, path, **options):
  return Artifact(identity, path.relative_to(context.root).as_posix(), **options)


def model_report(context, name, data, diagnostics=(), open_items=()):
  output = context.build_path / MODELS / f"{name}.json"
  status = "fail" if diagnostics else "indeterminate" if open_items else "pass"
  write_json(output, {"schema_version": 1, "status": status, **data})
  return TargetResult(status, (artifact(context, name, output),), tuple(diagnostics), tuple(open_items))


def missing_input(context, file, target):
  return model_report(context, target, {}, open_items=(
    {"id": "missing-source", "impact": f"Required engineering source is missing: {file.name}."},
  ))


def spatial_inputs(*files):
  def inputs(context):
    source = inspect_project(context.root).source
    return tuple(f"{source}/{file}" for file in files)
  return inputs


def build_site(context):
  path = source_path(context, "site.json")
  if not path.is_file():
    return missing_input(context, path, "site")
  data = read_versioned(path)
  if not all(isinstance(data.get(key), str) and data[key].strip() for key in ("parcel", "frame", "basis")):
    raise ValueError("The site requires parcel and frame identities and a coordinate basis.")
  if data.get("state") != "assumed" or data.get("north") != "+Y":
    raise ValueError("This example uses a synthetic rectangular parcel in a local +Y north frame.")
  bounds = vector(data.get("boundary_m"), 4, "parcel boundary")
  buildable = vector(data.get("buildable_m"), 4, "setback envelope")
  if not (bounds[0] <= buildable[0] < buildable[2] <= bounds[2]
          and bounds[1] <= buildable[1] < buildable[3] <= bounds[3]):
    raise ValueError("The buildable rectangle must have positive area within the parcel boundary.")
  return model_report(context, "site", {
    **data, "area_m2": (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]),
    "buildable_area_m2": (buildable[2] - buildable[0]) * (buildable[3] - buildable[1]),
  })


def build_grid(context):
  path = source_path(context, "grid.json")
  if not path.is_file():
    return missing_input(context, path, "grid")
  site = staged_json(context, f"{MODELS}/site.json")
  data = read_versioned(path)
  grid = AxisGrid(site["frame"], data["x"], data["y"])
  x0, y0, x1, y1 = site["buildable_m"]
  if len(grid.x) < 2 or len(grid.y) < 2:
    raise ValueError("An engineering grid needs at least two axes in each direction.")
  if not all(x0 <= x <= x1 for x in grid.x.values()) or not all(y0 <= y <= y1 for y in grid.y.values()):
    raise ValueError("Grid axes must lie within the buildable rectangle.")
  return model_report(context, "grid", {"frame": grid.frame, "x": dict(grid.x), "y": dict(grid.y)})


def build_levels(context):
  path = source_path(context, "levels.json")
  if not path.is_file():
    return missing_input(context, path, "levels")
  data = read_versioned(path)
  axes = staged_json(context, f"{MODELS}/grid.json")
  grid = AxisGrid(axes["frame"], axes["x"], axes["y"])
  levels = {name: DatumPlane(grid.frame, name, z).elevation_m for name, z in data["levels_m"].items()}
  if not levels or len(set(levels.values())) != len(levels):
    raise ValueError("Each level requires its own elevation datum.")
  zones = {}
  for identity, zone in data["zones"].items():
    minimum = (*grid.point(*zone["minimum_axes"]), levels[zone["level"]])
    maximum = (*grid.point(*zone["maximum_axes"]), levels[zone["ceiling"]])
    space = Space(minimum, maximum)
    overlap = next((name for name, other in zones.items()
                    if space.intersects(Space(other["minimum_m"], other["maximum_m"]))), None)
    if overlap is not None:
      raise ValueError(f"Zones {overlap} and {identity} overlap in three dimensions.")
    zones[identity] = {**zone, "minimum_m": minimum, "maximum_m": maximum}
  if not zones:
    raise ValueError("The level model requires at least one named spatial zone.")
  return model_report(context, "levels", {"frame": grid.frame, "levels_m": levels, "zones": zones})


def _world(origin, rotation, point):
  x, y, z = point
  x, y = {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[rotation]
  return tuple(a + b for a, b in zip(origin, (x, y, z), strict=True))


def _asset(catalog, document):
  assets = catalog.modules[document]["assets"]
  source = catalog.files[document].parent
  geometry = source / assets["geometry"]["path"]
  spec = read_versioned(source / assets["specification"]["path"])
  expected_frame = {"origin": "module-minimum-corner", "units": "m", "up_axis": "Z"}
  if spec.get("frame") != expected_frame:
    raise ValueError(f"{document}: unsupported envelope coordinate frame.")
  dimensions = vector(spec.get("envelope_m"), 3, document)
  if not all(n > 0 for n in dimensions) or not geometry.is_file():
    raise ValueError(f"{document}: expected a positive envelope and a USD asset.")
  return geometry, spec, dimensions


def _add_geometry(stage, name, geometry, origin, rotation, semantic):
  path = "/AIDC/Objects/" + name
  placed = UsdGeom.Xform.Define(stage, path)
  placed.AddTranslateOp().Set(Gf.Vec3d(*origin))
  placed.AddRotateZOp().Set(rotation)
  prim = placed.GetPrim()
  prim.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(semantic)
  reference = stage.DefinePrim(path + "/Asset")
  if not reference.GetReferences().AddReference(str(geometry)):
    raise ValueError(f"{semantic}: USD reference could not be composed.")
  envelope = stage.GetPrimAtPath(path + "/Asset/Geometry/Envelope")
  if not envelope.IsA(UsdGeom.Cube):
    raise ValueError(f"{semantic}: USD asset is missing its envelope.")
  envelope.SetCustomDataByKey("envelope", True)
  return path, envelope


def _catalog(source, context):
  name = "system.sdl.json"
  catalog = read_sdl_catalog(source, context.root)
  if catalog.diagnostics:
    raise ValueError("SDL catalog contains diagnostics: " + "; ".join(d.message for d in catalog.diagnostics))
  document = catalog.documents[name]
  imports, errors = resolve_sdl_imports(catalog.documents, library_entries(set(catalog.documents)), name)
  if errors:
    raise ValueError("SDL imports could not be resolved: " + ", ".join(errors.values()))
  return catalog, document["root"], document["components"][document["root"]]["instances"], imports


def build_layout(context):
  path = source_path(context, "coordination.json")
  for required in (path, path.with_name("system.sdl.json"), path.with_name("placement.study.json")):
    if not required.is_file():
      return missing_input(context, required, "layout")
  design = read_versioned(path)
  axes = staged_json(context, f"{MODELS}/grid.json")
  levels = staged_json(context, f"{MODELS}/levels.json")
  grid = AxisGrid(axes["frame"], axes["x"], axes["y"])
  catalog, root, instances, imports = _catalog(path.parent, context)
  physical = {name for name, binding in instances.items() if binding["component"] in imports}
  placed = design["placements"]
  nonspatial = design["nonspatial"]
  unknown = (set(placed) - physical) | (set(nonspatial) - (set(instances) - physical))
  if unknown:
    raise ValueError(f"Invalid physical or logical instance bindings: {', '.join(sorted(unknown))}.")
  unbound = (physical - set(placed)) | ((set(instances) - physical) - set(nonspatial))
  if unbound:
    return model_report(context, "layout", {"unbound_instances": sorted(unbound)}, open_items=(
      {"id": "unbound-instances", "impact": "Every top-level SDL instance needs a placement or a logical disposition."},
    ))
  if any(not isinstance(reason, str) or not reason.strip() for reason in nonspatial.values()):
    raise ValueError("Each logical disposition needs an explicit reason.")
  functional = read_versioned(path.with_name("placement.study.json"))["zones"]
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/AIDC").GetPrim())
  bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
  diagnostics, objects, source_map, prim_names = [], {}, {}, set()
  for name, record in sorted(placed.items()):
    zone_id = record["zone"]
    zone = levels["zones"][zone_id]
    if name not in functional[zone["functional_zone"]]:
      raise ValueError(f"{name}: physical zone {zone_id} disagrees with functional assignment.")
    discipline = record["discipline"]
    rotation = record.get("rotation_deg", 0)
    if discipline not in DISCIPLINES or type(rotation) is not int or rotation not in {0, 90, 180, 270}:
      raise ValueError(f"{name}: placement requires a supported discipline and orthogonal rotation.")
    dx, dy, dz = vector(record["offset_m"], 3, name + ".offset_m")
    origin = (*grid.point(*record["axes"], (dx, dy)), levels["levels_m"][zone["level"]] + dz)
    binding = instances[name]
    document, component = imports[binding["component"]]
    geometry, specification, size = _asset(catalog, document)
    end = _world(origin, rotation, size)
    minimum = tuple(min(a, b) for a, b in zip(origin, end, strict=True))
    maximum = tuple(max(a, b) for a, b in zip(origin, end, strict=True))
    semantic = f"{root}.{name}"
    prim_name = Tf.MakeValidIdentifier(name)
    if prim_name in prim_names:
      raise ValueError(f"USD instance identifiers collide for {name}.")
    prim_names.add(prim_name)
    prim_path, envelope = _add_geometry(stage, prim_name, geometry, origin, rotation, semantic)
    world_box = bounds.ComputeWorldBound(envelope).ComputeAlignedRange()
    for declared, measured in ((minimum, world_box.GetMin()), (maximum, world_box.GetMax())):
      if math.dist(declared, measured) > 1e-5:
        raise ValueError(f"{semantic}: USD envelope disagrees with the module's specification.")
    if not Space(zone["minimum_m"], zone["maximum_m"]).contains_space(Space(minimum, maximum)):
      diagnostics.append({"rule": "AIDC_ZONE_CONTAINMENT", "status": "fail", "objects": [name, zone_id],
                          "message": f"{name} envelope leaves {zone_id}: {minimum} to {maximum} m."})
    source_map[semantic] = {
      "file": path.with_name("system.sdl.json").relative_to(context.root).as_posix(),
      "path": f"components.{root}.instances.{name}",
      "geometry": {"file": path.relative_to(context.root).as_posix(), "path": f"placements.{name}"},
    }
    objects[name] = {
      "semantic_ref": semantic, "prim_path": prim_path, "module": document,
      "discipline": discipline, "zone": zone_id, "origin_m": origin, "rotation_deg": rotation,
      "minimum_m": minimum, "maximum_m": maximum,
      "ports_m": {key: _world(origin, rotation, vector(port["position_m"], 3, key))
                  for key, port in specification["interfaces"].items()},
      "port_normals": {key: _world((0, 0, 0), rotation, vector(port["normal"], 3, key))
                       for key, port in specification["interfaces"].items()},
    }
    if discipline == "it":
      definition = catalog.documents[document]["components"][component]
      load = binding.get("parameters", {}).get("it_load_kw", definition["behavior"]["parameters"]["it_load_kw"])
      if load.get("unit") != "kW" or type(load.get("value")) not in (int, float) or not math.isfinite(load["value"]):
        raise ValueError(f"{name}: IT load must bind a finite value in kW.")
      objects[name]["design_it_kw"] = load["value"]
  equipment = {name: Space(record["minimum_m"], record["maximum_m"]) for name, record in objects.items()
               if record["discipline"] in {"it", "electrical", "mechanical"}}
  for first, second in combinations(equipment, 2):
    if equipment[first].intersects(equipment[second]):
      diagnostics.append({"rule": "AIDC_ASSET_CLASH", "status": "fail", "objects": [first, second],
                          "message": f"{first} and {second} occupy the same envelope volume."})
  output = context.build_path / SCENE
  output.parent.mkdir(parents=True, exist_ok=True)
  if not stage.Flatten().Export(str(output)):
    raise OSError(f"Could not export {output.name}.")
  result = model_report(context, "layout", {"frame": grid.frame, "objects": objects, "nonspatial": nonspatial,
                                            "source_map": source_map}, diagnostics)
  return TargetResult(result.status, (*result.artifacts, artifact(
    context, "coordination-scene", output, preview={"kind": "scene", "source_map": source_map},
  )), result.diagnostics)
