"""Compile mounted emergency fixtures from the approved hall and outdoor route geometry."""

from math import ceil, dist

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .architecture import MODEL as ARCH
from .exit_access import MODEL as ACCESS, _profile
from .roof import _schedule
from .site_grading import MODEL as TERRAIN, elevation
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json
from .structure import _assumptions

MODEL = f"{MODELS}/emergency-lighting.json"


def _group(index):
  return ("A", "B")[index % 2]


def _interpolate(a, b, fraction):
  return tuple(x+(y-x)*fraction for x, y in zip(a, b, strict=True))


def _fixture(fixtures, key, location, group, point, *, pole=None, sign=None, family="hall"):
  family = "sign" if sign else family
  fixtures[key] = {"zone": location, "group": group, "family": family, "point_m": point,
                   "normal_charge_branch": f"EL-{location}-{group}", "pole": pole, "sign": sign}


def _pole(terrain, settings, x, y):
  base = elevation(terrain, x, y)
  return {"base_m": (x, y, base), "top_m": base+settings["pole_height_m"], "depth_m": settings["pole_base_depth_m"]}


def _outside(fixtures, terrain, settings, key, location, index, base, towards, *, sign=None):
  pole = _pole(terrain, settings, *base)
  # Keep the emitting surface at the same plane as the end of the cantilever.
  x, y = (a+b*settings["pole_arm_m"] for a, b in zip(base, towards, strict=True))
  _fixture(fixtures, key, location, _group(index), (x, y, pole["top_m"]), pole=pole, sign=sign, family="outdoor")


def _hall(fixtures, arch, settings):
  x0, _, floor = arch["hall"]["minimum_m"]
  x1 = arch["hall"]["maximum_m"][0]
  spacing = settings["hall_maximum_spacing_m"]
  count = max(4, ceil((x1-x0)/spacing))
  for room, row in sorted(arch["rooms"].items()):
    zone = room[-1]
    y = row["aisle_y_m"]
    for index in range(count):
      group = _group(index)
      x = x0+(x1-x0)*(index+.5)/count
      offset = settings["hall_lateral_offset_m"]*(1 if group == "A" else -1)
      _fixture(fixtures, f"EL-H{zone}-{index+1:02d}", f"H{zone}", group,
               (x, y+offset, floor+settings["hall_height_m"]))
    for key in sorted(row["exits"]):
      door = arch["doors"][key]
      x, _, z = door["inside_m"]
      direction = 1 if door["side"] == "west" else -1
      for index, lateral in enumerate((-.26, .26)):
        group = _group(index)
        _fixture(fixtures, f"EL-SIGN-{key}-{group}", f"H{zone}", group,
                 (x+direction*.10, y+lateral, z+door["clear_height_m"]+.20),
                 sign={"kind": "exit", "destination": key, "face_xy": (direction, 0)})


def _exterior(fixtures, access, terrain, settings):
  for key, ramp in sorted(access["ramps"].items()):
    zone = key[-1]
    origin, y, _ = ramp["walking_stations_m"][0]
    direction = -1 if ramp["side"] == "west" else 1
    stations = [(direction*(x-origin), z) for x, _, z in ramp["walking_stations_m"]]
    for index, offset in enumerate(settings["ramp_offsets_m"]):
      if not 0 < offset < stations[-1][0]:
        raise ValueError(f"{key}: lighting pole station lies outside the compiled ramp.")
      x = origin+direction*offset
      z = _profile(stations, offset)
      side = 1 if index % 2 else -1
      pole = _pole(terrain, settings, x, y+side*settings["ramp_lateral_m"])
      point = (x, pole["base_m"][1]-side*settings["pole_arm_m"], z+settings["pole_height_m"])
      pole["top_m"] = point[2]
      _fixture(fixtures, f"EL-{key}-{index+1:02d}", zone, _group(index), point, pole=pole, family="outdoor")
    for index, lateral in enumerate((-.26, .26)):
      z = stations[0][1]+ramp["leaf_height_m"]+.17
      point = (origin+direction*.235, y+lateral, z)
      _fixture(fixtures, f"EL-{key}-HEAD-{_group(index)}", zone, _group(index), point, family="outdoor")

  assemblies = {name: access["meeting_areas"][row["meeting"]]
                for name,row in access["assumptions"]["sides"].items()}

  for key, strip in sorted(access["walkways"].items()):
    a, b = strip["points_m"]
    length = dist(a[:2], b[:2])
    if length < strip["width_m"]:
      continue
    along_y = a[0] == b[0]
    side = 1 if key.startswith("WEST") else -1
    normal = (1, 0) if along_y else (0, 1)
    count = max(2, ceil(length/settings["path_spacing_m"]))
    for index in range(count):
      station = _interpolate(a, b, (index+.5)/count)
      base = tuple(v+side*n*settings["path_offset_m"] for v, n in zip(station[:2], normal, strict=True))
      if along_y:
        loc = "west" if side == 1 else "east"
        lo, south, hi, north = assemblies[loc]["extent_m"]
        if lo-.16<base[0]<hi+.16 and south-.16<base[1]<north+.16:
          base = (base[0], south-.19 if b[1]>a[1] else north+.19)
      _outside(fixtures, terrain, settings, f"EL-{key}-{index+1:02d}", key[0], index, base,
               tuple(-side*n for n in normal))
  for key, pad in sorted(access["meeting_areas"].items()):
    x0, y0, x1, y1 = pad["extent_m"]
    inset = (x1-x0)*.18
    offset = settings["assembly_offset_m"]
    for index, (x, y, side) in enumerate(((x0+inset, y0-offset, 1), (x1-inset, y0-offset, 1),
                                          (x1-inset, y1+offset, -1), (x0+inset, y1+offset, -1))):
      _outside(fixtures, terrain, settings, f"EL-{key}-{index+1:02d}", key[-1], index, (x, y), (0, side))


def build_emergency_lighting(context):
  source = source_path(context, "emergency-lighting.json")
  if not source.is_file():
    return missing_input(context, source, "emergency-lighting")
  config = _assumptions(source)
  raw = config["mounting"]
  settings = {key: number(raw[key], key, zero=key=="hall_lateral_offset_m") for key in (
    "hall_height_m", "hall_maximum_spacing_m", "hall_lateral_offset_m", "ramp_lateral_m", "pole_height_m",
    "pole_arm_m", "path_spacing_m", "path_offset_m", "assembly_offset_m", "pole_base_depth_m",
  )}
  settings["ramp_offsets_m"] = [number(value, "ramp lamp station") for value in raw["ramp_offsets_m"]]
  if sorted(set(settings["ramp_offsets_m"])) != settings["ramp_offsets_m"] or len(settings["ramp_offsets_m"]) < 2:
    raise ValueError("Ramp fixtures require at least two distinct increasing route stations.")
  if settings["pole_arm_m"] >= min(settings["ramp_lateral_m"], settings["path_offset_m"]):
    raise ValueError("Candidate cantilever reaches past the center of the path.")
  arch, access, terrain = (staged_json(context, path) for path in (ARCH, ACCESS, TERRAIN))
  fixtures = {}
  _hall(fixtures, arch, settings)
  _exterior(fixtures, access, terrain, settings)
  report = model_report(context, "emergency-lighting", {
    "source_digest": file_digest(source),
    "input_digests": {path: file_digest(context.build_path/path) for path in (ARCH, ACCESS, TERRAIN)},
    "assumptions": config, "fixtures": fixtures,
  })
  schedule = _schedule(context, "aidc-emergency-luminaires.csv",
                       ("fixture", "zone", "group", "normal_charge_branch", "family", "x_m", "y_m", "z_m", "sign_destination"),
                       ((key, row["zone"], row["group"], row["normal_charge_branch"], row["family"],
                         *row["point_m"], row["sign"]["destination"] if row["sign"] else "") for key, row in fixtures.items()))
  return TargetResult(report.status, (*report.artifacts, schedule), report.diagnostics)
