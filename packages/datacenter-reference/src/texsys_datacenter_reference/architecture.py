"""Compile the AIDC hall shell, openings and level transitions from datums."""

import csv
import re

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.targets import TargetResult
from texsys_spatial import Space

from .checks import load_design
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


MODEL = f"{MODELS}/architecture.json"
ARCH_SCENE = "scenes/aidc-architecture.usda"


def bounds(space):
  return {"minimum_m": space.minimum_m, "maximum_m": space.maximum_m}


def space(record):
  return Space(tuple(record["minimum_m"]), tuple(record["maximum_m"]))


def _box(x0, y0, z0, x1, y1, z1):
  return bounds(Space((x0, y0, z0), (x1, y1, z1)))


def _tag(value):
  if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", value):
    raise ValueError("Building identities must contain letters, digits and hyphens.")
  return value.replace("-", "_")


def _north_wall(layout, design, config, room, thickness):
  x0, _, floor = room.minimum_m
  x1, north, roof = room.maximum_m
  ids = config["north_wall_instances"]
  if not ids or len(set(ids)) != len(ids):
    raise ValueError("Declare every north wall kit once.")
  kits = sorted((layout["objects"][key]["minimum_m"][0], key) for key in ids)
  cursor = x0-thickness
  pieces = {}
  for start, key in kits:
    row = layout["objects"][key]
    _, front, base = row["minimum_m"]
    end, back, top = row["maximum_m"]
    spec = design.instances[key]
    if (spec.family != "fire-partition-wall" or not cursor <= start < end <= x1
        or any(abs(a-b) > 1e-8 for a, b in ((front, north-thickness), (back, north), (base, floor), (top, roof)))):
      raise ValueError(f"{key} must occupy its full planned north wall strip between the same base and lid datums.")
    if start > cursor:
      pieces[f"W-N-infill-{key}"] = _box(cursor, front, floor, start, back, roof)
    rating = number(spec.parameters["fire_resistance_hours"]["value"], f"{key} assumed fire rating")
    pieces[key] = {**bounds(space(row)), "sdl_instance": key, "fire_hours": rating}
    cursor = end
  pieces["W-N-end"] = _box(cursor, north-thickness, floor, x1+thickness, north, roof)
  return pieces


def _arrangement(config, room, split, yard):
  x0, south, floor = room.minimum_m
  x1, north, roof = room.maximum_m
  t = number(config["wall_thickness_m"], "wall thickness")
  if not south + t < split < north - t:
    raise ValueError("Place the separating wall between the hall limits.")
  ramp = config["ramp"]
  run = number(ramp["run_length_m"], "ramp run")
  landing = number(ramp["landing_length_m"], "landing length")
  ramp_width = number(ramp["clear_width_m"], "ramp clear width")
  if yard >= floor:
    raise ValueError("This entry strategy requires a lower exterior grade datum.")
  entries = config["compartments"]
  if set(entries) != {"FC-S", "FC-N"}:
    raise ValueError("Declare the north and south compartment for the partition axis.")
  rooms, doors = {}, {}
  for key, low, high in (("FC-S", south, split-t/2), ("FC-N", split+t/2, north-t)):
    entry = entries[key]
    aisle = (south if key == "FC-S" else split) + number(entry["aisle_offset_m"], f"{key} aisle offset")
    exits = [entry["west_exit"], entry["east_exit"]]
    rooms[key] = {**_box(x0, low, floor, x1, high, roof), "aisle_y_m": aisle,
                  "exits": exits, "floor_area_m2": (x1-x0)*(high-low)}
    for side, tag in zip(("west", "east"), exits, strict=True):
      _tag(tag)
      if tag in doors:
        raise ValueError(f"Opening {tag} is assigned to more than one exit.")
      row = config["doors"][tag]
      width = number(row["clear_width_m"], f"{tag} clear width")
      height = number(row["clear_height_m"], f"{tag} clear height")
      if type(row["available"]) is not bool or not low < aisle-width/2 < aisle+width/2 < high or floor+height >= roof:
        raise ValueError(f"{tag}: door opening must fit within the floor, wall and compartment boundary.")
      if row["leaf_count"] not in (1, 2) or type(row["leaf_count"]) is not int:
        raise ValueError(f"{tag}: declare one or two equal outward swinging leaves.")
      direction = -1 if side == "west" else 1
      inside = x0 if side == "west" else x1
      outside = inside + direction*t
      rise_end = outside + direction*landing
      grade_end = rise_end + direction*run
      discharge = grade_end + direction*landing
      stations = [[outside, floor], [rise_end, floor], [grade_end, yard], [discharge, yard]]
      a, b = min(inside, outside), max(inside, outside)
      lo, hi = aisle-width/2, aisle+width/2
      threshold = _box(a, lo, floor-config["floor_thickness_m"], b, hi, floor)
      doors[tag] = {**_box(a, lo, floor, b, hi, floor+height), "threshold": threshold,
                    **row, "compartment": key, "side": side, "swing": "outward", "inside_m": [inside, aisle, floor],
                    "landing_stations_m": stations, "ramp_width_m": ramp_width, "upper_landing_m": landing,
                    "ramp_slope": (floor-yard)/run, "discharge_m": [discharge, aisle, yard]}
  if doors.keys() != config["doors"].keys():
    raise ValueError("All door schedule entries must own a modeled wall opening.")
  return rooms, doors


def _walls(room, config, layout, design, doors, split):
  x0, y0, floor = room.minimum_m
  x1, y1, roof = room.maximum_m
  t = config["wall_thickness_m"]
  fire = number(config["wall_fire_hours"], "assumed wall fire rating")
  pieces = {"W-SOUTH": _box(x0-t, y0-t, floor, x1+t, y0, roof),
            "W-DIVIDE": _box(x0, split-t/2, floor, x1, split+t/2, roof),
            **_north_wall(layout, design, config, room, t)}
  for side, lo, hi in (("west", x0-t, x0), ("east", x1, x1+t)):
    sequence = sorted((door["minimum_m"][1], tag) for tag, door in doors.items() if door["side"] == side)
    cursor = y0
    for start, tag in sequence:
      door = doors[tag]
      end, head = door["maximum_m"][1:]
      pieces[f"W-before-{tag}"] = _box(lo, cursor, floor, hi, start, roof)
      pieces[f"W-over-{tag}"] = _box(lo, start, head, hi, end, roof)
      cursor = end
    pieces[f"W-{side}-end"] = _box(lo, cursor, floor, hi, y1-t, roof)
  return {name: {"fire_hours": fire, **record} for name, record in pieces.items()}


def _solid(stage, identity, record, color):
  shape = space(record)
  cube = UsdGeom.Cube.Define(stage, f"/AIDC/Architecture/{_tag(identity)}")
  cube.CreateSizeAttr(1)
  cube.AddTranslateOp().Set(Gf.Vec3d(*shape.center_m))
  cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*shape.size_m))
  cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  cube.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)


def _ramp_geometry(stage, identity, door):
  y = door["inside_m"][1]
  half = door["ramp_width_m"]/2
  stations = sorted(door["landing_stations_m"])
  points = [(x, y+sign*half, z) for x, z in stations for sign in (-1, 1)]
  mesh = UsdGeom.Mesh.Define(stage, f"/AIDC/Architecture/Ramp_{_tag(identity)}")
  mesh.CreatePointsAttr([Gf.Vec3f(*point) for point in points])
  mesh.CreateFaceVertexCountsAttr([4]*(len(stations)-1))
  mesh.CreateFaceVertexIndicesAttr([vertex for i in range(0, len(points)-2, 2) for vertex in (i, i+2, i+3, i+1)])
  mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
  mesh.CreateDoubleSidedAttr(True)
  mesh.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(f"RAMP-{identity}")


def _door_geometry(stage, identity, door):
  x0, y0, z0 = door["minimum_m"]
  x1, y1, z1 = door["maximum_m"]
  width = door["clear_width_m"]/door["leaf_count"]
  outside = x0 if door["side"] == "west" else x1
  terminal = outside-width if door["side"] == "west" else outside+width
  # Available leaves are represented open. A blocked exit is shown closed.
  if door["available"]:
    for index, y in enumerate((y0, y1)[:door["leaf_count"]], 1):
      _solid(stage, f"{identity}-{index}", _box(min(terminal, outside), y-.018, z0, max(terminal, outside), y+.018, z1),
             (.36, .49, .44))
  else:
    _solid(stage, identity, _box(outside-.018, y0, z0, outside+.018, y1, z1), (.68, .24, .20))


def build_architecture(context):
  path = source_path(context, "architecture.json")
  if not path.is_file():
    return missing_input(context, path, "architecture")
  config = read_versioned(path)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("Declare the assumptions for the hall design.")
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "architecture", {}, design.diagnostics)
  layout = staged_json(context, f"{MODELS}/layout.json")
  grid = staged_json(context, f"{MODELS}/grid.json")
  levels = staged_json(context, f"{MODELS}/levels.json")
  room = space(levels["zones"][config["zone"]])
  split = grid["y"][config["partition_axis"]]
  yard = levels["levels_m"][config["yard_datum"]]
  rooms, doors = _arrangement(config, room, split, yard)
  walls = _walls(room, config, layout, design, doors, split)
  x0, y0, floor = room.minimum_m
  x1, y1, _ = room.maximum_m
  slab = {**_box(x0, y0, floor-number(config["floor_thickness_m"], "slab depth"), x1, y1, floor),
          "fire_hours": number(design.placement["slab_fire_resistance_hours"], "assumed slab fire rating")}
  scene_path = context.build_path / ARCH_SCENE
  scene_path.parent.mkdir(parents=True, exist_ok=True)
  stage = Usd.Stage.CreateNew(str(scene_path))
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/AIDC").GetPrim())
  _solid(stage, "L01-SLAB", slab, (.51, .54, .55))
  for name, wall in walls.items():
    _solid(stage, name, wall, (.72, .73, .71))
  for name, door in doors.items():
    _ramp_geometry(stage, name, door)
    _door_geometry(stage, name, door)
    _solid(stage, f"THRESHOLD-{name}", door["threshold"], (.51, .54, .55))
  stage.GetRootLayer().Save()
  report = model_report(context, "architecture", {"state": config["state"], "basis": config["basis"],
                        "hall": bounds(room), "zone": config["zone"], "yard_m": yard, "partition_axis": config["partition_axis"],
                        "rooms": rooms, "walls": walls, "doors": doors, "slab": slab, "egress": config["egress"]})
  schedule = context.build_path / "schedules/aidc-doors.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("door", "compartment", "elevation_side", "clear_width_m", "clear_height_m", "leaf_count", "swing", "available",
                    "ramp_clear_width_m", "ramp_slope", "discharge_x_m", "discharge_y_m", "discharge_z_m"))
    for name, door in sorted(doors.items()):
      table.writerow((name, door["compartment"], door["side"], door["clear_width_m"], door["clear_height_m"], door["leaf_count"], door["swing"],
                      door["available"], door["ramp_width_m"], round(door["ramp_slope"], 6), *door["discharge_m"]))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "architecture-scene", scene_path),
                                      artifact(context, "door-schedule", schedule)), report.diagnostics)
