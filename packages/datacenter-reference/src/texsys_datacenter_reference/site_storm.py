"""Compile roof shoes, the buried storm graph and the detention handoff."""

from itertools import combinations, pairwise
from math import hypot, isclose

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import bounds, space
from .roof import _schedule
from .site_grading import EPS, MODEL as GRADING, SITE, _locate, design_source, elevation, issue
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json, vector
from .structure import MODEL as STRUCTURE
from .wet_utilities import MODEL as WET, _gravity, _pipe

MODEL = f"{MODELS}/site-storm.json"
DETENTION_IN = "D-IN"
DETENTION_OUT = "D-OUT"
OUTFALL = "ST-OUTFALL"


def _number(config, key):
  return number(config[key], key)


def _nodes(config, terrain, site):
  nodes = {}
  receiver_width = _number(config["criteria"], "receiver_width_m")
  for key, row in terrain["inlets"].items():
    x, y = row["xy_m"]
    z = elevation(terrain, x, y)
    if not isclose(z, row["rim_m"], abs_tol=1e-6):
      raise ValueError(f"{key}: receiver rim and terrain grade disagree.")
    nodes[key] = {"point_m": [x, y, row["invert_m"]], "rim_m": z, "kind": "roof-and-surface-receiver"}
  for key, values in config["junctions"].items():
    x, y, z = vector(values, 3, f"{key} junction")
    if key in nodes or z >= elevation(terrain, x, y):
      raise ValueError(f"{key}: junction must be unique and below grade.")
    nodes[key] = {"point_m": [x, y, z], "rim_m": elevation(terrain, x, y),
                  "kind": "detention-interface" if key in (DETENTION_IN, DETENTION_OUT) else "manhole"}
  x = site["boundary_m"][2]
  y, z = vector([config["outfall"]["station_y_m"], config["outfall"]["invert_m"]], 2, "storm outfall")
  nodes[OUTFALL] = {"point_m": [x, y, z], "rim_m": elevation(terrain, x, y), "kind": "storm-boundary"}
  half = receiver_width/2
  for key, row in nodes.items():
    x, y, z = row["point_m"]
    if key != DETENTION_IN:
      row["access"] = bounds(Space((x-half, y-half, z-.18),
                                   (min(site["boundary_m"][2], x+half), y+half, row["rim_m"])))
  return nodes


def _routes(config, nodes):
  material = {key: _pipe(row, gravity=True) for key, row in sorted(config["pipes"].items())}
  routes, onward = {}, {}
  for key, (source, target, pipe) in sorted(config["runs"].items()):
    a, b = [nodes[node]["point_m"] for node in (source, target)]
    if (a[0] == b[0]) == (a[1] == b[1]) or source in onward or source == DETENTION_IN or target == DETENTION_OUT:
      raise ValueError(f"{key}: expected a straight orthogonal leg with a unique destination and no detention bypass.")
    row = _gravity(material[pipe], a, b, target, from_node=source, to_node=target)
    row["service"] = "storm"
    routes[key] = row
    onward[source] = key
  if len(onward) != len(nodes)-2 or onward.get(DETENTION_OUT) is None or routes[onward[DETENTION_OUT]]["to_node"] != OUTFALL:
    raise ValueError("A single isolated detention outlet must run to the storm boundary.")
  walks = {}
  for node, info in nodes.items():
    if info["kind"] not in ("roof-and-surface-receiver", "manhole"):
      continue
    path, visited, current = [], set(), node
    while current != DETENTION_IN:
      if current in visited or current not in onward:
        raise ValueError(f"{node}: storm branches must reach the detention inlet exactly once.")
      visited.add(current)
      identity = onward[current]
      path.append(identity)
      current = routes[identity]["to_node"]
    walks[node] = path
  return routes, walks


def _shoe(source, rim, xy, criteria):
  x, y, z = source
  _, end_y = xy
  direction = (1 if end_y > y else -1)
  target_z = rim+_number(criteria, "minimum_shoe_air_gap_m")+.035
  bore = _number(criteria, "roof_shoe_inside_m")
  if abs(end_y-y) < .6 or target_z+bore/2 >= z:
    raise ValueError("Roof shoe needs clearance and continuous fall to its receiver.")
  points = [[x, y, z], [x, y+.20*direction, z-.025], [x, end_y, target_z+.05], [x, end_y, target_z]]
  return {"points_m": points,
          "inside_m": bore, "outside_m": bore+.01, "air_gap_m": target_z-rim,
          "length_m": sum(hypot(q[1]-p[1], q[2]-p[2]) for p, q in pairwise(points))}


def terrain_profile(terrain, a, b):
  """Return breakpoints where an orthogonal run crosses terrain triangle edges."""
  axis = 0 if a[0] != b[0] else 1
  along, across = [terrain["surface"][label] for label in ("x_m", "y_m")][::1 if axis == 0 else -1]
  _, fraction = _locate(across, a[1-axis])
  low, high = sorted((a[axis], b[axis]))
  stations = sorted({low, high, *(t for t in along if low < t < high),
                     *(at for left, right in pairwise(along) if low < (at := left+(right-left)*fraction) < high)})
  result = []
  for station in stations if a[axis] < b[axis] else reversed(stations):
    position = list(a[:2])
    position[axis] = station
    fraction = (station-a[axis])/(b[axis]-a[axis])
    result.append([hypot(position[0]-a[0], position[1]-a[1]), elevation(terrain, *position),
                   a[2]+fraction*(b[2]-a[2])])
  return result


def _envelope(a, b, radius):
  axis = 0 if a[0] != b[0] else 1
  pad = (0, radius, radius) if axis == 0 else (radius, 0, radius)
  return Space(tuple(min(x, y)-r for x, y, r in zip(a, b, pad, strict=True)),
               tuple(max(x, y)+r for x, y, r in zip(a, b, pad, strict=True)))


def _zrange(points, overlap):
  a, b = points
  axis = 0 if a[0] != b[0] else 1
  return sorted(a[2]+(pos-a[axis])*(b[2]-a[2])/(b[axis]-a[axis])
                for pos in (overlap.minimum_m[axis], overlap.maximum_m[axis]))


def _cross_bounds(box1, box2):
  x0, y0 = (max(a, b) for a, b in zip(box1.minimum_m[:2], box2.minimum_m[:2], strict=True))
  x1, y1 = (min(a, b) for a, b in zip(box1.maximum_m[:2], box2.maximum_m[:2], strict=True))
  return Space((x0, y0, -1.), (x1, y1, 1.)) if x1-x0 > EPS and y1-y0 > EPS else None


def _crossings(routes, wet, limit, diagnostics):
  rows = []
  for name, storm in routes.items():
    first = storm["points_m"]
    radius = storm["pipe"]["outside_m"]/2
    box = _envelope(*first, radius)
    for key, existing in wet["runs"].items():
      offset = existing["pipe"]["outside_m"]/2
      for index, (a, b) in enumerate(pairwise(existing["points_m"]), 1):
        if a[:2] == b[:2]:
          dx, dy = a[:2]
          upright = Space((dx-offset, dy-offset, min(a[2], b[2])), (dx+offset, dy+offset, max(a[2], b[2])))
          if box.intersects(upright):
            issue(diagnostics, "UTILITIES_CLASH", [name, f"{key}/{index}"], f"{name} intersects a riser of {key}.")
          continue
        span = _envelope(a, b, offset)
        overlap = _cross_bounds(box, span)
        if overlap is None:
          continue
        p, q = _zrange(first, overlap)
        r, s = _zrange((a, b), overlap)
        clearance = max(p-s, r-q)-radius-offset
        rows.append({"storm": name, "other": f"{key}/{index}", "point_xy_m": overlap.center_m[:2], "clearance_m": clearance})
        if clearance < limit:
          issue(diagnostics, "UTILITIES_CLEARANCE", [name, f"{key}/{index}"], f"{name} clears {key} by {clearance:.3f} m.")
  return rows


def _foundations(structure):
  for key, row in structure["members"].items():
    if row["kind"] == "foundation":
      for part in row["parts"]:
        yield f"{key}/{part['identity']}", Space(
          tuple(part["origin"]), tuple(a+b for a, b in zip(part["origin"], part["size"], strict=True))
        )


def _detention(config, nodes, site):
  data = config["detention"]
  x0, y0, x1, y1 = vector(data["extent_m"], 4, "detention footprint")
  bottom, roof = vector([data["bottom_m"], data["ceiling_m"]], 2, "detention levels")
  depth = _number(data, "working_depth_m")
  aperture = _number(data, "outlet_diameter_m")
  aperture_z = bottom+_number(data, "outlet_center_above_floor_m")
  outlet_crown = nodes[DETENTION_OUT]["point_m"][2]+config["pipes"]["outlet"]["inside_m"]
  inlet, outlet = [nodes[key]["point_m"] for key in (DETENTION_IN, DETENTION_OUT)]
  east = config["outfall"]
  rect = site["buildable_m"]
  if (not rect[0] <= x0 < x1 <= rect[2] or not rect[1] <= y0 < y1 <= rect[3]
      or inlet[0] != x0 or not x1 < outlet[0] < x1+.8 or not y0 < inlet[1] == outlet[1] < y1
      or not bottom < bottom+depth < inlet[2] < roof
      or not bottom <= aperture_z-aperture/2 or outlet_crown >= aperture_z-aperture/2
      or not east["receiving_invert_m"] < nodes[OUTFALL]["point_m"][2]):
    raise ValueError("Detention, permitted outfall, ground limits and connection levels do not stack.")
  return {**data, "orifice_points_m": [[x1, outlet[1], aperture_z], [outlet[0], outlet[1], aperture_z]],
          "plan_area_m2": (x1-x0)*(y1-y0), "working_capacity_m3": (x1-x0)*(y1-y0)*depth,
          "shell": bounds(Space((x0, y0, bottom), (x1, y1, roof)))}


def _coordination(routes, nodes, detention, structure, wet, terrain, site, criteria):
  minimum_grade = _number(criteria, "minimum_grade")
  required_cover = _number(criteria, "minimum_cover_m")
  issues, min_cover = [], {}
  steel = dict(_foundations(structure))
  extent = site["boundary_m"]
  for key, row in routes.items():
    a, b = row["points_m"]
    profile = terrain_profile(terrain, a, b)
    axis = 0 if a[0] != b[0] else 1
    radius = row["pipe"]["outside_m"]/2
    grid = terrain["surface"]["y_m" if axis == 0 else "x_m"]
    offset = {a[1-axis]-radius, a[1-axis], a[1-axis]+radius,
              *(p for p in grid if abs(p-a[1-axis]) < radius)}
    tails = []
    for across in sorted(offset):
      p, q = list(a), list(b)
      p[1-axis] = q[1-axis] = across
      tails.extend(terrain_profile(terrain, p, q))
    cover = min(grade-invert-radius for _, grade, invert in tails)
    min_cover[key] = cover
    row["surface_profile_m"] = profile
    row["outside_cover_m"] = cover
    if row["slope"]+EPS < minimum_grade:
      issue(issues, "PIPE_GRADE", [key], f"{key} does not meet the declared gravity grade.")
    if cover+EPS < required_cover:
      issue(issues, "PIPE_COVER", [key], f"{key} has {cover:.3f} m of cover, needs {required_cover:.3f} m.")
    if radius*2 >= criteria["receiver_width_m"]:
      issue(issues, "CHAMBER_OPENING", [key], f"{key} exceeds the clear opening of the selected chamber.")
    pipe = _envelope(a, b, radius)
    if any(a < lo-EPS or b > hi+EPS for a, b, lo, hi in zip(pipe.minimum_m[:2], pipe.maximum_m[:2],
                                                          extent[:2], extent[2:], strict=True)):
      issue(issues, "PARCEL", [key], f"{key} extends outside the parcel.")
    for obstacle, volume in steel.items():
      if pipe.intersects(volume):
        issue(issues, "FOUNDATION_CLASH", [key, obstacle], f"{key} clashes with {obstacle}.")
  for name, row in nodes.items():
    if "access" in row:
      for obstacle, volume in steel.items():
        if space(row["access"]).intersects(volume):
          issue(issues, "FOUNDATION_CLASH", [name, obstacle], f"{name} access chamber clashes with {obstacle}.")
  for (key, a), (other, b) in combinations(routes.items(), 2):
    if {a["from_node"], a["to_node"]} & {b["from_node"], b["to_node"]}:
      continue
    av = _envelope(*a["points_m"], a["pipe"]["outside_m"]/2)
    bv = _envelope(*b["points_m"], b["pipe"]["outside_m"]/2)
    if av.intersects(bv):
      issue(issues, "SELF_CLASH", [key, other], f"{key} intersects the independent run {other}.")
  tank_x0, tank_y0, tank_x1, tank_y1 = detention["extent_m"]
  xm = [tank_x0, tank_x1, *(x for x in terrain["surface"]["x_m"] if tank_x0 < x < tank_x1)]
  ym = [tank_y0, tank_y1, *(y for y in terrain["surface"]["y_m"] if tank_y0 < y < tank_y1)]
  detention_cover = min(elevation(terrain, x, y) for x in xm for y in ym)-detention["ceiling_m"]
  if detention_cover+EPS < required_cover:
    issue(issues, "TANK_COVER", [DETENTION_IN], f"Detention roof cover {detention_cover:.3f} m is below the required cover.")
  crossings = _crossings(routes, wet, _number(criteria, "minimum_crossing_clearance_m"), issues)
  return min_cover, detention_cover, crossings, issues


def build_site_storm(context):
  source = source_path(context, "site-storm.json")
  if not source.is_file():
    return missing_input(context, source, "site-storm")
  settings = design_source(source)
  terrain, site, steel, wet = (staged_json(context, path) for path in (GRADING, SITE, STRUCTURE, WET))
  nodes = _nodes(settings, terrain, site)
  routes, paths = _routes(settings, nodes)
  detention = _detention(settings, nodes, site)
  shoes = {key: _shoe(row["roof_handoff_m"], row["rim_m"], row["xy_m"], settings["criteria"])
           for key, row in terrain["inlets"].items()}
  pipe_covers, tank_cover, crossings, diagnostics = _coordination(
    routes, nodes, detention, steel, wet, terrain, site, settings["criteria"]
  )
  for key, row in shoes.items():
    radius = row["outside_m"]/2
    ground_gap = min(z-grade-radius
                     for a, b in pairwise(row["points_m"]) if a[:2] != b[:2]
                     for _, grade, z in terrain_profile(terrain, a, b))
    row["minimum_ground_gap_m"] = ground_gap
    if ground_gap < 0:
      issue(diagnostics, "ROOF_SHOE_GRADE", [key], f"{key} shoe intersects the formed site surface.")
  if settings["outfall"]["tailwater_m"] > nodes[OUTFALL]["point_m"][2]:
    issue(diagnostics, "TAILWATER", [OUTFALL], "Declared receiving tailwater submerges the site invert and needs a backwater study.")
  report = model_report(context, "site-storm", {
    "input_digests": {path: file_digest(context.build_path / path) for path in (GRADING, SITE, STRUCTURE, WET)},
    "assumptions": settings, "frame": site["frame"], "nodes": nodes, "runs": routes, "catchment_paths": paths,
    "roof_shoes": shoes, "detention": detention, "covers_m": pipe_covers, "detention_cover_m": tank_cover,
    "crossings": crossings, "checks": "fail" if diagnostics else "pass", "issues": diagnostics,
  })
  schedule = _schedule(context, "aidc-storm-pipes.csv",
                       ("route", "from", "to", "size", "length_m", "upstream_invert_m", "downstream_invert_m",
                        "grade_pct", "cover_m", "half_full_l_s"),
                       ((key, row["from_node"], row["to_node"], row["pipe"]["size"], row["length_m"], *row["invert_m"],
                         row["slope"]*100, row["outside_cover_m"], row["capacity_at_half_depth_m3_h"]/3.6)
                        for key, row in routes.items()))
  return TargetResult(report.status, (*report.artifacts, schedule), report.diagnostics)
