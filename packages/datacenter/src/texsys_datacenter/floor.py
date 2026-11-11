"""Coordinate a concept structural bay with the loads of a hybrid unit."""

import math
from dataclasses import asdict
from pathlib import Path

from texsys.formal import _load_json, resolve_sdl_imports
from texsys.library import library_entries
from texsys.sdl_catalog import read_sdl_catalog
from texsys.usd_brep import _prim_name
from texsys_structure import ISection
from texsys_structure.deck import DeckSlab

from .bay_layout import rack_plan
from .floor_structure import evaluate_floor_structure
from .unit import _number


def _rectangle(origin, extent, bounds, label):
  if len(origin) != 2 or len(extent) != 2 or any(
    type(value) not in {int, float} or not math.isfinite(value) for value in (*origin, *extent)
  ):
    raise ValueError(f"{label} needs finite plan coordinates and extents.")
  if any(length <= 0 or start < -1e-8 or start + length > limit + 1e-8
         for start, length, limit in zip(origin, extent, bounds, strict=True)):
    raise ValueError(f"{label} must fit within the selected structural bay.")
  return {"origin_m": origin, "size_m": extent}


def _overlap(first, second):
  return all(min(a + length_a, b + length_b) - max(a, b) > 1e-8
             for a, b, length_a, length_b in zip(first["origin_m"], second["origin_m"],
                                                first["size_m"], second["size_m"], strict=True))


def _deck_definition(root, source, design, width, length):
  file = "floor.sdl.json"
  if not (source / file).is_file():
    return DeckSlab(span_m=length, **design["deck"]), None
  catalog = read_sdl_catalog(source, root)
  if catalog.diagnostics:
    raise ValueError("Floor geometry needs a valid locked SDL library.")
  documents = catalog.documents
  aliases, errors = resolve_sdl_imports(documents, library_entries(set(documents)), file)
  if errors:
    raise ValueError(f"Unresolved floor imports: {errors}.")
  model = documents[file]
  instance = model["components"][model["root"]]["instances"]["FloorDeck"]
  entry, component = aliases[instance["component"]]
  manifest = catalog.modules[entry]
  [asset] = [record["path"] for record in manifest["assets"].values() if record["kind"] == "specification"]
  spec = _load_json((catalog.files[entry].parent / asset).read_text(encoding="utf-8"))
  properties = spec["properties"]
  if (not entry.startswith("@structure/") or spec["state"] != "assumed" or properties["role"] != design["deck_role"]
      or properties["implementation"] != "texsys_structure.deck.DeckSlab"):
    raise ValueError("Floor deck must bind to a locked permanent-form structure module.")
  inputs = {**properties["profile_defaults"], **design["deck"]}
  if "panels" not in inputs:
    pitch = _number(inputs["profile_m"][-1][0], "deck panel pitch")
    inputs["panels"] = round(width / pitch)
  return DeckSlab(span_m=length, **inputs), {"entry": entry, "root": component, "version": manifest["version"]}


def evaluate_floor(source: Path, usd_path: Path, *, root=None, unit_report=None) -> dict:
  design = _load_json((source / "floor.study.json").read_text(encoding="utf-8"))
  if type(design.get("schema_version")) is not int or design["schema_version"] != 1:
    raise ValueError("Floor study schema_version must be 1.")
  if design.get("state") != "assumed" or design.get("deck_role") != "permanent-form":
    raise ValueError("The concept floor requires assumed parameters for a permanent-form deck.")
  unit = _load_json((source / "unit.study.json").read_text(encoding="utf-8"))
  frame, corner_a, bounds, placements = rack_plan(design)
  width, length = bounds
  slab, deck_definition = _deck_definition(root, source, design, width, length)
  if not math.isclose(width, slab.width_m):
    raise ValueError("The deck panel widths must cover the declared bay.")
  if placements.keys() != unit["racks"].keys():
    raise ValueError("Floor placements must cover the same rack instances as the unit study.")
  racks = {}
  for name, placement in sorted(placements.items()):
    plan = _rectangle(placement["origin_m"], placement["size_m"], bounds, name)
    weight_kn = _number(unit["racks"][name]["mass_kg"], f"{name}.mass_kg") * 9.80665 / 1000
    racks[name] = {**plan, "vertical_load_kn": weight_kn}
  cooling = {}
  for name, placement in sorted((unit_report or {}).get("equipment_layout", {}).items()):
    support = placement["installation_support"]
    ox, oy, oz = placement["origin_m"]
    if support["state"] != "assumed" or not math.isclose(oz, 0., abs_tol=1e-8):
      raise ValueError(f"{name}: floor-mounted equipment needs an assumed support record and floor datum.")
    plan = _rectangle((ox - corner_a[0], oy - corner_a[1]), placement["envelope_m"][:2], bounds, name)
    cooling[name] = {**plan, "vertical_load_kn": _number(support["installed_mass_kg"], f"{name}.mass_kg") * 9.80665 / 1000,
                     "bearing_size_m": support["bearing_size_m"], "feet": support["feet"], "definition": placement["entry"]}
  openings = {}
  for name, request in sorted(design["opening_requests"].items()):
    if not isinstance(request["service"], str) or not request["service"].strip():
      raise ValueError(f"Opening {name} needs a service identity.")
    plan = _rectangle(request["origin_m"], request["size_m"], bounds, name)
    openings[name] = {**plan, "service": request["service"]}
  equipment = racks | cooling
  clashes = [[rack, opening] for rack, placement in equipment.items() for opening, request in openings.items()
             if _overlap(placement, request)]
  positions = sorted(equipment)
  clashes.extend([a, b] for index, a in enumerate(positions) for b in positions[index + 1:]
                 if _overlap(equipment[a], equipment[b]))

  volume = slab.volume_m3()
  weights = design["loads"]
  steel_unit_weight = _number(weights["steel_unit_weight_kn_m3"], "steel_unit_weight_kn_m3")
  deck = volume["steel"] * steel_unit_weight
  area = width * length
  phases = {}
  for phase in ("pour", "service"):
    concrete = volume["concrete"] * _number(weights[phase]["concrete_unit_weight_kn_m3"], f"{phase}.concrete")
    additional = _number(weights[phase]["additional_uniform_kn_m2"], f"{phase}.additional", inclusive=True)
    phases[phase] = {"deck_kn": deck, "concrete_kn": concrete, "additional_uniform_kn_m2": additional,
                     "gross_uniform_kn_m2": (deck + concrete) / area + additional}
  depth = slab.slab_top_m - slab.thickness_m - max(z for _, z in slab.profile_m)
  screening = (evaluate_floor_structure(design["structural_screening"], bounds, phases, racks, openings, steel_unit_weight,
                                        cooling=cooling, concrete_min_depth=depth)
               if "structural_screening" in design else None)
  usd_path.parent.mkdir(parents=True, exist_ok=True)
  slab.write_usd(usd_path)
  return {
    "schema_version": 1, "state": design["state"], "frame": frame, "origin_m": corner_a,
    "bay_m": bounds, "deck_role": design["deck_role"], "volume_m3": volume, "phases": phases,
    "deck_bottom_min_m": min(point[1] for point in slab.profile_m),
    "concrete_min_depth_m": depth, "deck_parameters": asdict(slab), "deck_definition": deck_definition,
    "rack_loads": racks, "cooling_equipment_loads": cooling, "opening_requests": openings, "plan_clashes": clashes,
    "structural_screening": screening, "structural_review": "pending",
  }


def write_floor_assembly(report: dict, deck_path: Path, target: Path) -> bool:
  screening = report["structural_screening"]
  if screening is None:
    return False
  service = next(case for case in screening["cases"].values() if case["phase"] == "service")
  beams = service["steel_support_beams"]
  if not beams:
    return False
  from pxr import Gf, Sdf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/FloorAssembly").GetPrim())
  deck_layer = Sdf.Layer.OpenAsAnonymous(str(deck_path))
  if not Sdf.CopySpec(deck_layer, f"/{deck_layer.defaultPrim}", stage.GetRootLayer(), "/FloorAssembly/DeckSlab"):
    raise ValueError("Unable to include the deck geometry in the floor assembly.")
  for name, beam in sorted(beams.items()):
    parent = f"/FloorAssembly/{_prim_name(name)}"
    member = UsdGeom.Xform.Define(stage, parent)
    member.GetPrim().SetCustomDataByKey("identity", name)
    parts = ISection(**beam["section_m"]).beam(0, beam["span_m"], beam["y_m"],
                                               report["deck_bottom_min_m"], along_x=True)
    for part in parts:
      cube = UsdGeom.Cube.Define(stage, f"{parent}/{part.identity}")
      cube.CreateSizeAttr(1)
      cube.AddTranslateOp().Set(Gf.Vec3d(*(a + b / 2 for a, b in zip(part.origin, part.size, strict=True))))
      cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*part.size))
      cube.CreateDisplayColorAttr([Gf.Vec3f(0.28, 0.39, 0.48)])
  if not stage.GetRootLayer().Export(str(target)):
    raise OSError(f"Unable to export {target}.")
  return True
