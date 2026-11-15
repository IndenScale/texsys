"""Grade the parcel, trace connected runoff, and integrate cut and fill over a common TIN."""

from bisect import bisect_right
from heapq import heappop, heappush
from itertools import pairwise
from math import dist, hypot, isclose

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .architecture import MODEL as ARCHITECTURE
from .roof import MODEL as ROOF, _schedule
from .spatial_design import MODELS, missing_input, model_report, number, read_versioned, source_path, staged_json, vector

MODEL = f"{MODELS}/site-grading.json"
SITE = f"{MODELS}/site.json"
EPS = 1e-8


def design_source(path):
  config = read_versioned(path)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError(f"{path.name} requires an explicit assumed design basis.")
  return config


def issue(diagnostics, code, objects, message):
  diagnostics.append({"rule": f"AIDC_STORM_{code}", "status": "fail", "objects": objects, "message": message})


def _xy(point):
  return tuple(round(value, 7) for value in point[:2])


def _contains(rect, x, y):
  return rect[0]-EPS <= x <= rect[2]+EPS and rect[1]-EPS <= y <= rect[3]+EPS


def _rect(raw, parcel):
  x0, y0, x1, y1 = vector(raw, 4, "site grading boundary")
  if not x0 < x1 or not y0 < y1 or not _contains(parcel, x0, y0) or not _contains(parcel, x1, y1):
    raise ValueError("Site grading zones need positive area within the legal parcel.")
  return [x0, y0, x1, y1]


def roof_inlets(roof, config):
  if set(roof["drainage"]) != set(config["inlets"]):
    raise ValueError("Site storm interfaces must cover every roof leader exactly once.")
  inlets = {}
  for key, row in roof["drainage"].items():
    x, y, z = row["handoff_m"]
    spec = config["inlets"][key]
    dy, rim, invert = vector([spec["offset_y_m"], spec["rim_m"], spec["invert_m"]], 3, f"{key} connection")
    if dy == 0 or rim <= invert or z <= rim:
      raise ValueError(f"{key}: expected a falling connection to a recessed receiver outside the building.")
    inlets[key] = {"xy_m": _xy((x, y+dy)), "invert_m": invert, "rim_m": rim, "roof_handoff_m": [x, y, z]}
  return inlets


def _locate(axis, coordinate):
  index = min(len(axis)-2, max(0, bisect_right(axis, coordinate)-1))
  return index, (coordinate-axis[index])/(axis[index+1]-axis[index])


def _sample(x, y, levels, point):
  i, u = _locate(x, point[0])
  j, v = _locate(y, point[1])
  sw, se, nw, ne = levels[j][i], levels[j][i+1], levels[j+1][i], levels[j+1][i+1]
  if u >= v:
    return sw*(1-u)+se*(u-v)+ne*v
  return sw*(1-v)+ne*u+nw*(v-u)


def elevation(model, x, y, *, surface="proposed"):
  field = model["surface"]
  return _sample(field["x_m"], field["y_m"], field[f"{surface}_m"], (x, y))


def _mesh_axes(parcel, survey, rectangles, points):
  result = []
  for index, key in enumerate(("x_m", "y_m")):
    stations = sorted({parcel[index], parcel[index+2], *survey[key],
                       *(p[index] for p in points), *(r[pos] for r in rectangles for pos in (index, index+2))})
    if stations[0] < parcel[index] or stations[-1] > parcel[index+2]:
      raise ValueError("Terrain stations must stay within the parcel.")
    result.append([*dict.fromkeys(round(value, 7) for value in stations)])
  return result


def _controls(architecture):
  result = {}
  for name, row in architecture["doors"].items():
    (_, end), (start, _) = row["landing_stations_m"][-1], row["landing_stations_m"][-2]
    last = row["landing_stations_m"][-1][0]
    inside_y = row["inside_m"][1]
    half = row["ramp_width_m"]/2
    result[name] = {"extent_m": [min(start, last), inside_y-half, max(start, last), inside_y+half],
                    "grade_m": end, "end_xy_m": [last, inside_y]}
  return result


def _class(point, structure, oil, hardstand):
  if _contains(structure, *point):
    return "roof"
  if _contains(oil, *point):
    return "oil"
  return "hardstand" if any(_contains(rect, *point) for rect in hardstand) else "landscape"


def _distance_to_rect(rect, x, y):
  return hypot(max(rect[0]-x, 0, x-rect[2]), max(rect[1]-y, 0, y-rect[3]))


def _terrain(parcel, config, inlets, roof_rect, arch):
  survey = config["survey"]
  sx, sy = [list(vector(survey[key], len(survey[key]), f"survey {key}")) for key in ("x_m", "y_m")]
  if (len(sx) < 2 or len(sy) < 2 or sx != sorted(set(sx)) or sy != sorted(set(sy)) or
      [sx[0], sy[0], sx[-1], sy[-1]] != parcel or len(survey["levels_m"]) != len(sy)):
    raise ValueError("Survey grid must cover the parcel with ordered stations and finite levels.")
  sampled = [vector(row, len(sx), "survey level row") for row in survey["levels_m"]]
  configured_pads = {key: {"extent_m": _rect(row["extent_m"], parcel), "grade_m": row["grade_m"]}
                     for key, row in config["platforms"].items()}
  hardstand = [_rect(row, parcel) for row in config["pavement"]]
  controls = _controls(arch)
  oil = config["oil_yard"]
  ring = _rect(oil["extent_m"], parcel)
  fuel_seed = tuple(vector(oil["inlet_xy_m"], 2, "oil yard collector"))
  if not ring[0] < fuel_seed[0] < ring[2] or not ring[1] < fuel_seed[1] < ring[3]:
    raise ValueError("The sealed yard inlet must be strictly inside its containment.")
  fuel_z, perimeter, crest = vector([oil["inlet_elevation_m"], oil["perimeter_formation_m"], oil["containment_crest_m"]],
                                  3, "fuel levels")
  if not fuel_z < perimeter < crest:
    raise ValueError("Oil yard inlet, perimeter grade and containment crest must increase.")
  pads = [*configured_pads.values(), *controls.values()]
  roof = _rect(roof_rect, parcel)
  clear = {key: (tuple(row["xy_m"]), row["rim_m"]) for key, row in inlets.items()}
  vertices = [fuel_seed, *(p for p, _ in clear.values()), *(r["end_xy_m"] for r in controls.values())]
  xs, ys = _mesh_axes(parcel, survey, [*hardstand, *(p["extent_m"] for p in pads), ring, roof], vertices)
  fall = number(config["design_fall"], "formation fall")
  transition = number(config["platform_transition_grade"], "maximum platform transition grade")
  pinned = {point: height for point, height in clear.values()}
  existing, proposed = [], []
  for y in ys:
    ex_row, design_row = [], []
    for x in xs:
      ex_row.append(round(_sample(sx, sy, sampled, (x, y)), 7))
      design = min(z+fall*dist((x, y), bench) for bench, z in clear.values())
      interior = ring[0] < x < ring[2] and ring[1] < y < ring[3]
      if interior:
        design = fuel_z+fall*dist((x, y), fuel_seed)
      edge_distance = (min(x-ring[0], ring[2]-x, y-ring[1], ring[3]-y) if interior else _distance_to_rect(ring, x, y))
      design = max(design, perimeter-transition*edge_distance)
      design = max(design, *(row["grade_m"]-transition*_distance_to_rect(row["extent_m"], x, y) for row in pads))
      owned = [row["grade_m"] for row in pads if _contains(row["extent_m"], x, y)]
      if owned:
        if max(owned) != min(owned):
          raise ValueError("Hardstand and walk-off control levels conflict.")
        design = owned[0]
        pinned[(x, y)] = design
      if edge_distance < EPS:
        pinned[(x, y)] = perimeter
      design = pinned.get((x, y), design)
      if (x, y) == fuel_seed:
        design = fuel_z
      design_row.append(round(design, 7))
    existing.append(ex_row)
    proposed.append(design_row)
  field = {"x_m": xs, "y_m": ys, "existing_m": existing, "proposed_m": proposed}
  labels = [*hardstand, *(row["extent_m"] for row in configured_pads.values())]
  return field, roof, ring, fuel_seed, labels, controls, configured_pads, pinned


def _triangles(field, roof, ring, pavement):
  x, y = field["x_m"], field["y_m"]
  points = [(a, b) for b in y for a in x]
  faces = []
  for j, (a, b) in enumerate(pairwise(y)):
    for i, (c, d) in enumerate(pairwise(x)):
      sw = j*len(x)+i
      nw = sw+len(x)
      for face in ((sw, sw+1, nw+1), (sw, nw+1, nw)):
        center = [sum(points[idx][n] for idx in face)/3 for n in (0, 1)]
        kind = _class(center, roof, ring, pavement)
        faces.append((face, (b-a)*(d-c)/2, kind))
  return points, faces


def _edges(point_count, faces, *, oil_yard=False):
  adjacency = [set() for _ in range(point_count)]
  for face, _, kind in faces:
    if kind == "roof" or (kind == "oil") != oil_yard:
      continue
    for a, b in zip(face, (*face[1:], face[0]), strict=True):
      adjacency[a].add(b)
      adjacency[b].add(a)
  return adjacency


def _fill_formation(points, faces, field, collectors, pinned, config, *, oil_yard=False):
  """Raise isolated low points with the lowest connected finish that preserves design controls."""
  raw = sum(field["proposed_m"], [])
  index = {point: i for i, point in enumerate(points)}
  graph = _edges(len(points), faces, oil_yard=oil_yard)
  grade = number(config["minimum_formation_fall"], "minimum formed fall")
  queue = sorted((row["rim_m"], index[tuple(row["xy_m"])]) for row in collectors.values())
  solved = {}
  while queue:
    height, node = heappop(queue)
    if node in solved:
      continue
    solved[node] = height
    for other in sorted(graph[node]):
      if other in solved:
        continue
      if points[other] in pinned:
        target = pinned[points[other]]
        if target < height-EPS:
          continue
      else:
        target = max(raw[other], height+grade*dist(points[node], points[other]))
      heappush(queue, (target, other))
  revised = [round(solved.get(idx, value), 7) for idx, value in enumerate(raw)]
  width = len(field["x_m"])
  field["proposed_m"] = [revised[idx:idx+width] for idx in range(0, len(revised), width)]
  increases = [b-a for a, b in zip(raw, revised, strict=True)]
  return {"vertices_raised": sum(amount > EPS for amount in increases), "maximum_raise_m": max(increases)}


def _drain_graph(points, faces, levels, collectors, *, oil_yard=False):
  adjacency = _edges(len(points), faces, oil_yard=oil_yard)
  index = {point: i for i, point in enumerate(points)}
  solved = {}
  queue = [(0., name, index[tuple(row["xy_m"])]) for name, row in collectors.items()]
  queue.sort()
  while queue:
    travel, sink, node = heappop(queue)
    if node in solved:
      continue
    solved[node] = (travel, sink)
    for other in sorted(adjacency[node]):
      if other not in solved and levels[other] >= levels[node]-EPS:
        heappush(queue, (travel+dist(points[node], points[other]), sink, other))
  return solved


def _sign_volume(vertices, positive):
  polygon = []
  for current, last in zip(vertices, (*vertices[1:], vertices[0]), strict=True):
    x, y, dz = current
    nx, ny, nd = last
    value, next_value = positive*dz, positive*nd
    if value >= 0:
      polygon.append((x, y, value))
    if value*next_value < 0:
      p = value/(value-next_value)
      polygon.append((x+p*(nx-x), y+p*(ny-y), 0.))
  if len(polygon) < 3:
    return 0.
  result = 0.
  x, y, value = polygon[0]
  for (a, b, fa), (c, d, fb) in pairwise(polygon[1:]):
    area = abs((a-x)*(d-y)-(b-y)*(c-x))/2
    result += area*(value+fa+fb)/3
  return result


def _surface_checks(field, points, faces, inlets, controls, config, parcels, oil_collector):
  projected, existing = (sum(field[key], []) for key in ("proposed_m", "existing_m"))
  claimed = _drain_graph(points, faces, projected, inlets)
  contained = _drain_graph(points, faces, projected, oil_collector, oil_yard=True)
  limits = config["runoff_coefficients"]
  coefficients = {key: number(limits[key], f"{key} runoff coefficient") for key in ("hardstand", "landscape")}
  if max(coefficients.values()) > 1:
    raise ValueError("Runoff coefficients must not exceed one.")
  grade_tolerance = number(config["door_landing_tolerance_m"], "door landing tolerance")
  checks, routed = [], []
  maximum_edge_grade = number(config["maximum_ground_grade"], "maximum terrain edge grade")
  peak = 0.
  earthwork = {"cut_m3": 0., "fill_m3": 0., "signed_m3": 0.}
  groups = {name: {"area_m2": 0., "effective_area_m2": 0., "source_types_m2": dict.fromkeys(coefficients, 0.)}
            for name in inlets}
  reserved = dict.fromkeys(("oil", "roof", "ponding"), 0.)
  wet_oil_area = 0.
  for face, area, kind in faces:
    graph = contained if kind == "oil" else claimed
    levels = [(projected[node], graph.get(node, (float("inf"), None))[0], node) for node in face]
    node = min(levels)[-1]
    discharge = claimed.get(node, (0, None))[1] if kind not in {"roof", "oil"} else None
    if kind == "oil" and node not in contained:
      wet_oil_area += area
    if discharge is not None:
      row = groups[discharge]
      row["area_m2"] += area
      row["effective_area_m2"] += area*coefficients[kind]
      row["source_types_m2"][kind] += area
    else:
      reserved[kind if kind in reserved else "ponding"] += area
    routed.append({"vertices": face, "surface": kind, "catchment": discharge, "area_m2": area})
    v = [(*points[node], projected[node]-existing[node]) for node in face]
    earthwork["cut_m3"] += _sign_volume(v, -1)
    earthwork["fill_m3"] += _sign_volume(v, 1)
    earthwork["signed_m3"] += area*sum(row[2] for row in v)/3
    if kind != "roof":
      for a, b in zip(face, (*face[1:], face[0]), strict=True):
        peak = max(peak, abs(projected[b]-projected[a])/dist(points[a], points[b]))
  if not isclose(earthwork["fill_m3"]-earthwork["cut_m3"], earthwork["signed_m3"], abs_tol=1e-7):
    raise ValueError("Terrain cut and fill integration does not close.")
  if not isclose(sum(v["area_m2"] for v in groups.values())+sum(reserved.values()), parcels, abs_tol=1e-7):
    raise ValueError("Roof, dirty yard and site runoff do not partition the parcel.")
  if reserved["ponding"] > EPS:
    issue(checks, "PONDING", ["site-formation"], f"{reserved['ponding']:.2f} m² cannot reach an inlet by non-rising terrain edges.")
  if wet_oil_area > EPS:
    issue(checks, "OIL_YARD_PONDING", ["oil-yard"], f"{wet_oil_area:.2f} m² does not drain to the sealed yard inlet.")
  if peak > maximum_edge_grade:
    issue(checks, "SITE_GRADE", ["site-formation"], f"Proposed terrain edge grade {peak:.3f} exceeds {maximum_edge_grade:.3f}.")
  for name, control in controls.items():
    for point in [control["end_xy_m"], control["extent_m"][:2], control["extent_m"][2:]]:
      z = _sample(field["x_m"], field["y_m"], field["proposed_m"], point)
      if abs(z-control["grade_m"]) > grade_tolerance:
        issue(checks, "DOOR_TIE_IN", [name], f"{name} landing does not meet the formation.")
        break
  return routed, earthwork, groups, reserved, peak, checks


def build_site_grading(context):
  source, connections = [source_path(context, name) for name in ("site-grading.json", "site-storm.json")]
  for path in (source, connections):
    if not path.is_file():
      return missing_input(context, path, "site-grading")
  config = design_source(source)
  site, arch, roof = (staged_json(context, name) for name in (SITE, ARCHITECTURE, ROOF))
  inlets = roof_inlets(roof, design_source(connections))
  field, roof_rect, dirty, seed, hardstand, controls, pads, pinned = _terrain(
    site["boundary_m"], config, inlets, roof["outline_xy_m"], arch
  )
  points, faces = _triangles(field, roof_rect, dirty, hardstand)
  clean_changes = _fill_formation(points, faces, field, inlets, pinned, config)
  oil = config["oil_yard"]
  oil_inlet = {"OIL-I": {"xy_m": seed, "rim_m": oil["inlet_elevation_m"]}}
  oil_changes = _fill_formation(points, faces, field, oil_inlet, pinned, config, oil_yard=True)
  grades = sum(field["proposed_m"], [])
  edge = {p: grades[idx] for idx, p in enumerate(points) if _contains(dirty, *p) and
          (p[0] in dirty[::2] or p[1] in dirty[1::2])}
  routed, volumes, areas, reserved, peak, issues = _surface_checks(
    field, points, faces, inlets, controls, config, site["area_m2"], oil_inlet
  )
  if max(edge.values()) >= oil["containment_crest_m"]:
    issue(issues, "OIL_YARD_CREST", ["oil-yard"], "Perimeter crest must be above the finished formation along the entire bund.")
  candidate = oil["sealed_storage"]
  vault_x0, vault_y0, vault_x1, vault_y1 = rectangle = _rect(candidate["extent_m"], dirty)
  depth = number(candidate["working_depth_m"], "oil isolation working depth")
  bottom, ceiling = vector([candidate["bottom_m"], candidate["ceiling_m"]], 2, "oil isolation storage")
  if not _contains(rectangle, *seed) or not bottom < bottom+depth < ceiling < oil["inlet_elevation_m"]:
    raise ValueError("Oil collector and sealed containment must stack vertically.")
  storage = {**candidate, "working_capacity_m3": (vault_x1-vault_x0)*(vault_y1-vault_y0)*depth}
  report = model_report(context, "site-grading", {
    "frame": site["frame"], "parcel": site["parcel"], "extent_m": site["boundary_m"],
    "source_digests": {name: file_digest(context.build_path / name) for name in (SITE, ARCHITECTURE, ROOF)},
    "state": config["state"], "basis": config["basis"], "surface": field, "points_xy_m": points, "triangles": routed,
    "earthwork": volumes, "catchments": areas, "reserved_areas_m2": reserved,
    "inlets": inlets, "hardstands": pads, "landing_controls": controls, "maximum_edge_grade": peak,
    "formation_adjustments": {"clean": clean_changes, "oil": oil_changes},
    "oil_yard": {**oil, "sealed_storage": storage, "collector_xy_m": seed, "plan_area_m2": reserved["oil"]},
    "checks": "fail" if issues else "pass", "issues": issues,
  })
  quantities = _schedule(context, "aidc-site-grading.csv",
                         ("x_m", "y_m", "existing_m", "formation_m", "fill_positive_m"),
                         ((x, y, field["existing_m"][j][i], field["proposed_m"][j][i],
                           round(field["proposed_m"][j][i]-field["existing_m"][j][i], 7))
                          for j, y in enumerate(field["y_m"]) for i, x in enumerate(field["x_m"])))
  return TargetResult(report.status, (*report.artifacts, quantities), report.diagnostics)


def build_site_grading_check(context):
  model = staged_json(context, MODEL)
  return model_report(context, "site-grading-check", {
    "geometry_digest": file_digest(context.build_path / MODEL), "issues": model["issues"],
  }, model["issues"])
