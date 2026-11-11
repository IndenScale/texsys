"""Bind protective bonding to locked electrical and structural module assets."""

from dataclasses import asdict, dataclass
from itertools import combinations, pairwise
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.dependencies import _read_lock
from texsys.formal import _load_json
from texsys.module_package import read_module_manifest
from texsys.project import inspect_project
from texsys.usd_brep import _prim_name
from texsys_electrical.bonding import Bond, BondNetwork, conductor_resistance_mohm
from texsys_structure.concrete import cage_bars

from .unit import _identifiers, _number


@dataclass(frozen=True)
class DetailModule:
  identity: str
  version: str
  spec: dict
  behavior: dict | None
  layer: Sdf.Layer


def _detail_module(root, package, slug):
  if not isinstance(slug, str) or not slug or any(not (c.isascii() and (c.isalnum() or c == "-")) for c in slug):
    raise ValueError("A bonding detail must name a canonical module directory.")
  project = inspect_project(root)
  folder = (root / project.source / "library/modules" / slug).resolve()
  entry = f"{slug}.sdl.json"
  definition = _load_json((folder / entry).read_text(encoding="utf-8"))
  ports = definition["components"][definition["root"]]["ports"]
  manifest = read_module_manifest(folder / "module.toml", entry, ports)
  assets = {asset["kind"]: (key, folder / asset["path"]) for key, asset in manifest["assets"].items()}
  spec = _load_json(assets["specification"][1].read_text(encoding="utf-8"))
  geometry_key, geometry = assets["geometry"]
  symbol_key, symbol = assets["symbol"]
  layer = Sdf.Layer.OpenAsAnonymous(str(geometry))
  stage = Usd.Stage.Open(layer)
  if (not stage or not stage.GetDefaultPrim() or stage.GetCompositionErrors()
      or UsdGeom.GetStageMetersPerUnit(stage) != 1 or UsdGeom.GetStageUpAxis(stage) != "Z"):
    raise ValueError(f"{slug} needs valid meter-based, Z-up geometry.")
  anchors = {item.get("id"): item.get("data-port") for item in ET.parse(symbol).iter() if item.get("id")}
  bindings = manifest.get("port_bindings", {})
  if bindings.keys() != ports.keys() or spec["interfaces"].keys() != ports.keys():
    raise ValueError(f"{slug} must bind each declared port to its assets.")
  for name, binding in bindings.items():
    frame = UsdGeom.Xform(stage.GetPrimAtPath(binding[geometry_key]))
    if not frame or anchors.get(binding[symbol_key]) != name:
      raise ValueError(f"Unresolved asset binding for {slug}.{name}.")
    position = frame.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
    normal = frame.GetPrim().GetAttribute("texsys:normal").Get()
    interface = spec["interfaces"][name]
    if (normal is None or math.dist(position, interface["position_m"]) > 1e-8
        or math.dist(normal, interface["normal"]) > 1e-8 or not math.isclose(normal.GetLength(), 1.0)):
      raise ValueError(f"Inconsistent interface frame for {slug}.{name}.")
  behavior = _load_json(assets["behavior"][1].read_text(encoding="utf-8")) if "behavior" in assets else None
  return DetailModule(f"{package}:{slug}", manifest["version"], spec, behavior, layer)


def _point(values, label):
  if len(values) != 3 or any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
    raise ValueError(f"{label} needs three finite coordinates.")
  return tuple(values)


def _plus(a, b):
  return tuple(x + y for x, y in zip(a, b, strict=True))


def _inside(point, extent):
  return all(0 < coord < size for coord, size in zip(point, extent, strict=True))


def _route(a, b):
  points = (a, (b[0], a[1], a[2]), (b[0], b[1], a[2]), b)
  return [points[0], *(end for start, end in pairwise(points) if start != end)]


def _check_sleeve(joint, origin, envelope, bars, target):
  stage = Usd.Stage.Open(joint.layer)
  sleeve = stage.GetPrimAtPath(joint.spec["properties"]["embedded_body_prim"])
  if not sleeve:
    raise ValueError("The compression module must locate its embedded sleeve geometry.")
  bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(sleeve).ComputeAlignedRange()
  low, high = _plus(origin, bounds.GetMin()), _plus(origin, bounds.GetMax())
  if bounds.IsEmpty() or not _inside(low, envelope) or not _inside(high, envelope):
    raise ValueError("The assumed embedded sleeve must fit inside the structural envelope.")
  for bar in bars:
    if bar == target:
      continue
    half = tuple((bar.length_m if i == bar.axis else bar.diameter_m) / 2 for i in range(3))
    if all(a < center + radius and b > center - radius
           for a, b, center, radius in zip(low, high, bar.center, half, strict=True)):
      raise ValueError("The assumed sleeve envelope clashes with reinforcement other than the selected bar.")


def _foundations(config, modules, building):
  beam, joint, tape = (modules[name] for name in ("beam", "joint", "strip"))
  bar = beam.spec["properties"]["reinforcement"]
  tape_spec = tape.spec["properties"]
  behavior = joint.behavior
  if (behavior is None or behavior["state"] != "assumed"
      or behavior["mechanism"] != joint.spec["properties"]["mechanism"]
      or behavior["rebar"] != {"material": "reinforcing-steel", "diameter_mm": 1000 * bar["bar_diameter_m"]}
      or any(tape_spec[key] != value for key, value in behavior["tape"].items())):
    raise ValueError("The compression detail must match the locked flat strip and reinforcing bar.")
  spec_port = beam.spec["interfaces"]["rebar_bond_point"]
  joint_port = joint.spec["interfaces"]["rebar"]
  if spec_port["normal"] != joint_port["normal"]:
    raise ValueError("The compression detail's rebar axis must align with the structural interface.")
  envelope = beam.spec["envelope_m"]
  bars = cage_bars(envelope, **{key: value for key, value in bar.items() if key != "pattern"})
  axis = bar["axis"]
  anchor = tuple(spec_port["position_m"])
  if tuple(map(abs, spec_port["normal"])) != tuple(int(i == axis) for i in range(3)):
    raise ValueError("The structural attachment normal must follow the longitudinal bar axis.")
  longitudinal = [item for item in bars if item.axis == axis and all(
    math.isclose(item.center[i], anchor[i], abs_tol=1e-8) for i in range(3) if i != axis
  )]
  if len(longitudinal) != 1:
    raise ValueError("Structural rebar attachment must resolve to one longitudinal bar.")
  actual = longitudinal[0]
  joint_local = _plus(anchor, tuple(-value for value in joint_port["position_m"]))
  _check_sleeve(joint, joint_local, envelope, bars, actual)
  details = {}
  for name, record in sorted(config.items()):
    origin = _point(record["origin_m"], f"{name}.origin_m")
    if (not _inside(origin[:2], building["floor_m"])
        or not _inside(_plus(origin[:2], envelope[:2]), building["floor_m"])
        or origin[2] + envelope[2] > min(building["levels_m"].values())):
      raise ValueError(f"Foundation {name} must fit below the modeled building floor.")
    if (record["environment"] != behavior["environment"] or record["state"] != "assumed"
        or any(not isinstance(record.get(key), str) or not record[key].strip()
               for key in behavior["installation_records"])):
      raise ValueError(f"Foundation {name} needs a compatible connection and explicit tool and die records.")
    distance = _number(record["reference_distance_m"], f"{name}.reference_distance_m")
    end = _plus(anchor, tuple(distance * value for value in spec_port["normal"]))
    if any(abs(point[axis] - actual.center[axis]) > actual.length_m / 2 + 1e-8 for point in (anchor, end)):
      raise ValueError(f"Foundation {name} contact and reference must lie on the named continuous bar.")
    rebar = _plus(origin, anchor)
    joint_origin = _plus(origin, joint_local)
    tape_port = joint.spec["interfaces"]["tape"]
    tape_point = _plus(joint_origin, tape_port["position_m"])
    approach = _plus(tape_point, tuple(.15 * value for value in tape_port["normal"]))
    details[name] = {"beam_origin_m": origin, "joint_origin_m": joint_origin, "tape_m": tape_point,
                     "rebar_m": rebar, "reference_m": _plus(origin, end), "tape_approach_m": approach,
                     "selection": record}
  for (first, a), (second, b) in combinations(details.items(), 2):
    if all(abs(x - y) < span for x, y, span in zip(a["beam_origin_m"], b["beam_origin_m"], envelope, strict=True)):
      raise ValueError(f"Foundation grounding details overlap: {first} and {second}.")
  return details


def evaluate_grounding(root: Path, source: Path, building: dict, floor: dict, unit: dict) -> tuple[dict, dict]:
  config = _load_json((source / "grounding.study.json").read_text(encoding="utf-8"))
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Grounding study needs schema_version 1 and an explicit assumed basis.")
  _, dependencies = _read_lock(root)
  by_package = {dependency.name: dependency.root for dependency in dependencies}
  packages = {"beam": "structure", "joint": "electrical", "strip": "electrical"}
  modules = {key: _detail_module(by_package[package], package, config["modules"][key])
             for key, package in packages.items()}
  details = _foundations(config["foundations"], modules, building)
  if len(details) < 2 or config["riser_offsets_m"].keys() != details.keys():
    raise ValueError("Each independent grounding riser must reach a distinct modeled foundation branch.")
  tape = modules["strip"].spec["properties"]
  copper = config["copper"]
  materials = {"flat-strip": (tape["width_mm"] * tape["thickness_mm"], tape["resistivity_ohm_mm2_m"]),
               "unit-feeder": (copper["feeder_mm2"], copper["resistivity_ohm_mm2_m"]),
               "equipment-spur": (copper["spur_mm2"], copper["resistivity_ohm_mm2_m"])}
  levels = sorted(building["levels_m"], key=building["levels_m"].get)
  room = building["floor_zones"][config["electrical_reserve"]]
  if room["role"] != "electrical-room":
    raise ValueError("Floor equipotential bars must bind to the electrical room reserve.")
  bus = _point(config["floor_bus_offset_m"], "floor_bus_offset_m")
  risers = {name: _point(offset, name) for name, offset in config["riser_offsets_m"].items()}
  if len(set(risers.values())) != len(risers) or bus in risers.values():
    raise ValueError("Riser and equipotential-bar points must be distinct.")
  if any(not _inside(point, (*room["size_m"], building["unit_height_m"])) for point in (bus, *risers.values())):
    raise ValueError("Grounding floor buses and risers must fit the electrical reserve.")
  if any(point[:2] == bus[:2] for point in risers.values()):
    raise ValueError("A riser must have a distinct horizontal location from the floor bar.")
  local_bar = _point(config["unit_bus_m"], "unit_bus_m")
  equipment = {name: _point(point, name) for name, point in config["equipment_points_m"].items()}
  if equipment.keys() & floor["rack_loads"].keys() or equipment.keys() - unit["power_model"]["demand_kw"].keys():
    raise ValueError("Additional protective bonding points must bind to distinct unit SDL loads.")
  for name, rack in floor["rack_loads"].items():
    x, y = _plus(rack["origin_m"], tuple(value / 2 for value in rack["size_m"]))
    equipment[name] = (x, y, _number(config["rack_bond_height_m"], "rack_bond_height_m"))

  nodes, edges, bonds, terminals, references = {}, {}, {}, set(), set()

  def bond(name, a, b, kind, resistance=None, via=()):
    points = [nodes[a]]
    for start, end in pairwise((nodes[a], *via, nodes[b])):
      points.extend(_route(start, end)[1:])
    length = sum(math.dist(start, end) for start, end in pairwise(points))
    if resistance is None:
      resistance = conductor_resistance_mohm(length, *materials[kind])
    bonds[name] = Bond(a, b, resistance)
    edges[name] = {**asdict(bonds[name]), "kind": kind, "points_m": points, "length_m": length}

  for level in levels:
    z = building["levels_m"][level]
    floor_bar = f"{level}/FloorBond"
    room_origin = (*room["origin_m"], z)
    nodes[floor_bar] = _plus(room_origin, bus)
    for name, offset in risers.items():
      node = f"{level}/Riser-{name}"
      nodes[node] = _plus(room_origin, offset)
      bond(f"{level}/CrossBond-{name}", floor_bar, node, "flat-strip")
    for unit_id, zone in sorted(building["floor_zones"].items()):
      if zone["role"] != "unit":
        continue
      if any(not _inside(point, (*zone["size_m"], building["unit_height_m"]))
             for point in (local_bar, *equipment.values())):
        raise ValueError("Equipment bonding anchors must fit the repeated unit.")
      prefix = f"{level}/{unit_id}"
      origin = (*zone["origin_m"], z)
      nodes[f"{prefix}/BondBar"] = _plus(origin, local_bar)
      bond(f"{prefix}/UnitFeeder", f"{prefix}/BondBar", floor_bar, "unit-feeder")
      for name, point in equipment.items():
        terminal = f"{prefix}/{name}"
        nodes[terminal] = _plus(origin, point)
        terminals.add(terminal)
        bond(f"{terminal}/PE", terminal, f"{prefix}/BondBar", "equipment-spur")

  for name, detail in details.items():
    for low, high in pairwise(levels):
      bond(f"Riser-{name}/{low}-{high}", f"{low}/Riser-{name}", f"{high}/Riser-{name}", "flat-strip")
    tape_id, rebar_id, reference = (f"Foundation-{name}/{part}" for part in ("Tape", "Rebar", "Reference"))
    nodes.update({tape_id: detail["tape_m"], rebar_id: detail["rebar_m"], reference: detail["reference_m"]})
    references.add(reference)
    bond(f"Foundation-{name}/Downlead", f"{levels[0]}/Riser-{name}", tape_id, "flat-strip",
         via=(detail["tape_approach_m"],))
    selection = detail["selection"]
    bond(f"Foundation-{name}/Compression", tape_id, rebar_id, "compression", selection["contact_mohm"])
    bond(f"Foundation-{name}/CagePath", rebar_id, reference, "rebar-continuity", selection["cage_path_mohm"])

  budget = _number(config["max_path_mohm"], "max_path_mohm")
  cases = {}
  for name, case in sorted(config["cases"].items()):
    if case["expected"] not in {"pass", "fail"}:
      raise ValueError(f"Invalid protective bonding expectation in {name}.")
    adjusted = bonds.copy()
    for edge, value in case.get("resistance_overrides_mohm", {}).items():
      previous = adjusted[edge]
      adjusted[edge] = Bond(previous.start, previous.end, value)
    network = BondNetwork(set(nodes), adjusted, references)
    result = network.evaluate(terminals, _identifiers(case["unavailable_bonds"], "unavailable_bonds"), budget)
    counts = {state: sum(item["status"] == state for item in result["terminals"].values())
              for state in ("pass", "disconnected", "over-budget")}
    cases[name] = {**result, "counts": counts, "expected": case["expected"],
                   "resistance_overrides_mohm": case.get("resistance_overrides_mohm", {}),
                   "status": "fail" if result["disconnected"] or result["over_budget"] else "pass"}
  if not cases:
    raise ValueError("Grounding study needs explicit continuity scenarios.")
  report = {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "formal_review": "pending",
    "status": "pass" if all(case["status"] == case["expected"] for case in cases.values()) else "fail",
    "max_path_mohm": budget, "reference_nodes": sorted(references), "terminal_count": len(terminals),
    "modules": {key: {"definition": item.identity, "version": item.version} for key, item in modules.items()},
    "foundations": details, "conductors_and_joints": edges, "cases": cases,
    "pending": ["qualified-connector-and-tool-evidence", "continuity-test-and-full-cage-survey",
                "earth-electrode-and-soil-resistance", "fault-loop-and-protective-disconnection",
                "corrosion-and-penetration-detail"],
  }
  return report, modules


def _place(stage, path, module, origin):
  if not Sdf.CopySpec(module.layer, f"/{module.layer.defaultPrim}", stage.GetRootLayer(), path):
    raise ValueError(f"Unable to place bonding module {module.identity}.")
  shape = UsdGeom.Xform(stage.GetPrimAtPath(path))
  shape.AddTranslateOp().Set(Gf.Vec3d(*origin))
  shape.GetPrim().SetCustomDataByKey("identity", module.identity)
  return shape


def write_grounding_usd(report: dict, modules: dict, path: Path):
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Grounding").GetPrim())
  for group in ("Foundations", "SteelStrips", "CopperRoutes"):
    UsdGeom.Scope.Define(stage, f"/Grounding/{group}")
  for name, detail in sorted(report["foundations"].items()):
    parent = f"/Grounding/Foundations/{_prim_name(name)}"
    UsdGeom.Xform.Define(stage, parent)
    _place(stage, f"{parent}/Reinforcement", modules["beam"], detail["beam_origin_m"])
    _place(stage, f"{parent}/Compression", modules["joint"], detail["joint_origin_m"])
  copper = {"unit-feeder": [], "equipment-spur": []}
  tape = modules["strip"]
  for name, edge in sorted(report["conductors_and_joints"].items()):
    if edge["kind"] in copper:
      copper[edge["kind"]].append(edge["points_m"])
    if edge["kind"] != "flat-strip":
      continue
    parent = f"/Grounding/SteelStrips/{_prim_name(name)}"
    UsdGeom.Xform.Define(stage, parent)
    for index, (a, b) in enumerate(pairwise(edge["points_m"]), 1):
      shape = _place(stage, f"{parent}/Part{index}", tape, a)
      direction = Gf.Vec3d(*b) - Gf.Vec3d(*a)
      rotation = Gf.Rotation(Gf.Vec3d(1, 0, 0), direction)
      shape.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(rotation.GetQuat())
      shape.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(direction.GetLength() / tape.spec["properties"]["nominal_length_m"], 1, 1)
      )
  for name, routes in copper.items():
    if not routes:
      continue
    curves = UsdGeom.BasisCurves.Define(stage, f"/Grounding/CopperRoutes/{_prim_name(name)}")
    curves.CreateTypeAttr("linear")
    curves.CreateCurveVertexCountsAttr([len(route) for route in routes])
    curves.CreatePointsAttr([Gf.Vec3f(*point) for route in routes for point in route])
    curves.SetWidthsInterpolation("constant")
    curves.CreateWidthsAttr([.012 if name == "unit-feeder" else .006])
    curves.CreateDisplayColorAttr([Gf.Vec3f(.8, .37, .11)])
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
