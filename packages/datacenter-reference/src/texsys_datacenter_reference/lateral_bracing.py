"""Locate tension rods on named frame bays and coordinate their free lengths."""

from itertools import combinations, pairwise
from math import dist, pi

from texsys.evidence import file_digest

from .architecture import MODEL as ARCH, space
from .fire_egress import MODEL as EGRESS, _swept
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _obstacles, _system_objects
from .power_routing import MODEL as POWER
from .roof import MODEL as ROOF
from .roof_coordination import _installed
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json, vector
from .structure import MODEL as FRAME, _assumptions

MODEL = f"{MODELS}/lateral-bracing.json"
COORDINATION = f"{MODELS}/lateral-coordination.json"


def _point(first, last, fraction):
  return tuple(a + (b-a)*fraction for a, b in zip(first, last, strict=True))


def _bay(frame, name, record, low, high, setback, split, diameter):
  first, last = (tuple(record[field]) for field in ("from_axes", "to_axes"))
  start, end = (tuple(frame[axis][label] for axis, label in zip("xy", point, strict=True))
                for point in (first, last))
  changed = tuple(axis for axis, (a, b) in enumerate(zip(first, last, strict=True)) if a != b)
  if len(changed) != 1:
    raise ValueError(f"{name}: expected one orthogonal frame bay.")
  axis, = changed
  fixed = 1-axis
  span = end[axis]-start[axis]
  collector = (f"G-{first[0]}-{last[0]}-{first[1]}" if axis == 0
               else f"T-{first[0]}-{first[1]}-{last[1]}")
  if span <= 2*setback or collector not in frame["members"]:
    raise ValueError(f"{name}: brace axes must follow an existing beam in ascending order.")
  offset, = vector([record["offset_m"]], 1, f"{name} plane offset")
  section = frame["settings"]["sections_m"]["column"]
  face = (section["depth"] if axis == 0 else section["width"])/2
  if abs(offset) + split/2 + diameter/2 > face:
    raise ValueError(f"{name}: brace planes lie outside the supported column width.")
  rods = {}
  for key, dz, shift in (("P", (low, high), -split/2), ("N", (high, low), split/2)):
    a, b = list(start), list(end)
    a[fixed] += offset+shift
    b[fixed] += offset+shift
    p, q = (*a, dz[0]), (*b, dz[1])
    points = [_point(p, q, setback/span), _point(q, p, setback/span)]
    rods[f"{name}-{key}"] = {"points_m": points, "length_m": dist(*points),
                            "ideal_length_m": dist(p, q), "eccentricity_m": offset+shift}
  return {
    "axis": "xy"[axis], "axes": [first, last], "start_m": start, "end_m": end,
    "columns": ["C-" + "-".join(point) for point in (first, last)], "collector": collector,
    "lower_m": low, "upper_m": high, "offset_m": offset, "span_m": span, "rods": rods,
  }


def build_lateral_bracing(context):
  path = source_path(context, "lateral-bracing.json")
  if not path.is_file():
    return missing_input(context, path, "lateral-bracing")
  frame = staged_json(context, FRAME)
  config = _assumptions(path)
  column = next(row for row in frame["members"].values() if row["kind"] == "column")
  roof = next(row["end_m"][2] for row in frame["members"].values() if row["kind"] == "girder")
  low = column["start_m"][2] + number(config["lower_above_column_base_m"], "lower node offset")
  high = roof - number(config["upper_below_beam_top_m"], "upper node offset")
  diameter = number(config["rod_diameter_m"], "rod diameter")
  split = number(config["plane_separation_m"], "crossed rod plane separation")
  setback = number(config["joint_setback_m"], "rod setback at collector and column joints")
  clearance = number(config["installation_clearance_m"], "rod installation clearance")
  if not column["start_m"][2] < low < column["end_m"][2] < high <= roof or split < diameter+2*clearance:
    raise ValueError("Brace elevations must span from the column into the collector depth with room for crossed rods.")
  bays = {name: _bay(frame, name, row, low, high, setback, split, diameter)
          for name, row in config["bays"].items()}
  if not bays or len({row["collector"] for row in bays.values()}) != len(bays):
    raise ValueError("Provide distinct brace bays.")
  return model_report(context, "lateral-bracing", {
    "frame_digest": file_digest(context.build_path / FRAME), "assumptions": config,
    "plan_m": [[min(frame[axis].values()), max(frame[axis].values())] for axis in "xy"],
    "lower_joint_m": low, "upper_joint_m": high, "roof_beam_top_m": roof,
    "area_m2": pi*diameter**2/4, "bays": bays,
  })


def _hits(start, end, volume, radius):
  """Clip a rod centreline against a conservatively expanded obstacle box."""
  low, high = 0., 1.
  for a, b, near, far in zip(start, end, volume.minimum_m, volume.maximum_m, strict=True):
    delta = b-a
    near, far = near-radius, far+radius
    if delta == 0:
      if not near < a < far:
        return False
      continue
    enters, leaves = sorted(((near-a)/delta, (far-a)/delta))
    low, high = max(low, enters), min(high, leaves)
    if high <= low:
      return False
  return True


def _routes(egress):
  width = egress["limits"]["minimum_clear_width_m"]
  height = egress["limits"]["minimum_clear_height_m"]
  for origin, record in egress["routes"].items():
    approaches = dict.fromkeys((tuple(a), tuple(b)) for path in record["paths"]
                              for a, b in pairwise(path["points_m"][:3]))
    for i, (first, last) in enumerate(approaches, 1):
      shape = _swept(first, last, width, height)
      if shape is not None:
        yield f"EG-{origin}-{i}", shape


def _rod_distance(first, second):
  a, b = first
  c, d = second
  u = tuple(end-start for start, end in zip(a, b, strict=True))
  v = tuple(end-start for start, end in zip(c, d, strict=True))
  w = tuple(start-end for start, end in zip(a, c, strict=True))
  def dot(left, right):
    return sum(x*y for x, y in zip(left, right, strict=True))

  uu, uv, vv = dot(u, u), dot(u, v), dot(v, v)
  uw, vw = dot(u, w), dot(v, w)

  def on_segment(value):
    return min(1., max(0., value))

  candidates = [(0., on_segment(vw/vv)), (1., on_segment((vw+uv)/vv)),
                (on_segment(-uw/uu), 0.), (on_segment((uv-uw)/uu), 1.)]
  determinant = uu*vv-uv**2
  if determinant > 1e-12*uu*vv:
    s = (uv*vw-vv*uw)/determinant
    t = (uu*vw-uv*uw)/determinant
    if 0 < s < 1 and 0 < t < 1:
      candidates.append((s, t))
  return min(dist(_point(a, b, s), _point(c, d, t)) for s, t in candidates)


def build_lateral_coordination(context):
  model = staged_json(context, MODEL)
  architecture = staged_json(context, ARCH)
  power = staged_json(context, POWER)
  egress = staged_json(context, EGRESS)
  roof = staged_json(context, ROOF)
  obstacles = _obstacles(context, architecture)
  obstacles.update({f"Roof/{key}": shape for key, shape in _installed(roof)})
  obstacles.update({name: shape for name, (shape, _) in _system_objects(staged_json(context, FIRE))[0].items()})
  obstacles.update({name: space(row["envelope"]) for train in power["trains"].values()
                    for name, row in train["segments"].items()})
  obstacles.update(_routes(egress))
  config = model["assumptions"]
  radius = config["rod_diameter_m"]/2+config["installation_clearance_m"]
  issues = []
  rods = {name: rod["points_m"] for row in model["bays"].values() for name, rod in row["rods"].items()}
  for name, (first, last) in rods.items():
    for blocker, volume in obstacles.items():
      if _hits(first, last, volume, radius):
        issues.append({"rule": "AIDC_LATERAL_ROD_CLASH", "status": "fail", "objects": [name, blocker],
                       "message": f"{name} intersects reserved space {blocker}."})
  gaps = {(first, last): _rod_distance(rods[first], rods[last])-config["rod_diameter_m"]
          for first, last in combinations(rods, 2)}
  for (first, last), gap in gaps.items():
    if gap+1e-9 < 2*config["installation_clearance_m"]:
      issues.append({"rule": "AIDC_LATERAL_ROD_CLASH", "status": "fail", "objects": [first, last],
                     "message": f"{first} and {last} overlap their radial installation allowances."})
  return model_report(context, "lateral-coordination", {
    "bracing_digest": file_digest(context.build_path / MODEL), "obstacles_checked": len(obstacles),
    "rods_checked": len(rods), "minimum_rod_surface_separation_m": min(gaps.values()), "issues": issues,
    "coverage": "Free rod lengths plus radial allowance against physical objects and declared egress envelopes. "
                "End hardware, wall sealing and ear plates require connection design.",
  }, issues)
