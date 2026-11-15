"""Compile site makeup and process drainage from tower ports and parcel edges."""

import csv
import json
from itertools import pairwise
from math import dist, hypot, isclose, pi

from pxr import Usd

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH, space
from .cw_network import MODEL as CW, SCENE as BASE_SCENE, _pipe_prim, _port, _segments
from .fire_systems_coordination import _obstacles
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json, vector
from .telecom_coordination import _prim


MODEL = f"{MODELS}/wet-utilities.json"
SCENE = "scenes/aidc-wet-utilities-coordinated.usda"
COLORS = {"city": (.25, .55, .83), "makeup": (.15, .56, .59), "drain": (.72, .47, .23)}


def _pipe(raw, *, gravity=False):
  keys = ("manning_n",) if gravity else ("roughness_m", "elbow_k")
  material = {key: number(raw[key], key, zero=not gravity and key == "elbow_k")
              for key in ("inside_m", "outside_m", *keys)}
  if material["inside_m"] >= material["outside_m"] or not gravity and material["roughness_m"] >= material["inside_m"]:
    raise ValueError("Utility pipe needs a positive bore within its physical outside diameter.")
  return {**material, "size": raw["size"]}


def _run(service, material, points, **binding):
  lengths = [dist(a, b) for a, b in pairwise(points)]
  if not lengths or min(lengths) < material["outside_m"]:
    raise ValueError(f"{service}: every straight leg must exceed one outside diameter.")
  return {"service": service, "pipe": material, "points_m": points, "length_m": sum(lengths), **binding}


def _gravity(material, start, end, downstream, **binding):
  span = hypot(end[0]-start[0], end[1]-start[1])
  fall = start[2]-end[2]
  if span <= 0 or fall <= 0:
    raise ValueError(f"{downstream}: gravity route needs strictly falling invert along positive horizontal distance.")
  diameter = material["inside_m"]
  slope = fall/span
  capacity = (pi*diameter**2/8)*(diameter/4)**(2/3)*slope**.5/material["manning_n"]*3600
  points = [(x, y, z+diameter/2) for x, y, z in (start, end)]
  return _run("drain", material, points, invert_m=[start[2], end[2]], slope=slope,
              capacity_at_half_depth_m3_h=capacity, downstream=downstream, **binding)


def _net(root, towers, settings, service):
  net = root["nets"][settings["net"]]
  id_set = set(net["connections"])
  links = [row for row in root["connections"] if row["id"] in id_set]
  boundary = settings["boundary_sdl"]
  edges = {(link["from"], link["to"]): link["id"] for link in links}
  if service == "makeup":
    checks = {tower: (boundary, f"{tower}.makeup") for tower in sorted(towers)}
  else:
    checks = {tower: (f"{tower}.blowdown", boundary) for tower in sorted(towers)}
  expected_kind = "makeup-water" if service == "makeup" else "process-drain"
  if net["kind"] != expected_kind or edges.keys() != set(checks.values()) or len(links) != len(net["connections"]):
    raise ValueError(f"{service}: each placed tower must bind one unique SDL connection to the declared boundary.")
  return {tower: edges[edge] for tower, edge in checks.items()}


def _tank(settings):
  box = vector(settings["tank_box_m"], 6, "tank enclosure")
  fill = vector(settings["tank_fill_m"], 3, "tank fill")
  discharge = vector(settings["tank_delivery_m"], 3, "tank delivery")
  area = number(settings["wetted_area_m2"], "tank wetted area")
  low, high = (number(settings[key], key) for key in ("low_water_m", "high_water_m"))
  x0, y0, z0, x1, y1, z1 = box
  if not (0 < area <= (x1-x0)*(y1-y0) and z0 <= low < high < fill[2] <= z1
          and fill[1] == y1 and discharge[1] == y0
          and all(x0 < p[0] < x1 and z0 < p[2] < z1 for p in (fill, discharge))):
    raise ValueError("Tank fill and delivery require exterior faces, usable depth and net wetted area inside the enclosure.")
  return {"id": "TK-01", "minimum_m": box[:3], "maximum_m": box[3:],
          "wetted_area_m2": area, "water_elevation_m": [low, high], "usable_volume_m3": area*(high-low),
          "fill_m": fill, "delivery_m": discharge, "free_fill_gap_m": fill[2]-high}


def _makeup(settings, bindings, layout):
  city = vector(settings["city_connection_m"], 3, "city connection")
  tank = _tank(settings)
  city_y, main_y, main_z, riser_y = (settings[key] for key in (
    "city_riser_y_m", "main_y_m", "main_centerline_z_m", "tower_riser_y_m"))
  fill, outlet = tank["fill_m"], tank["delivery_m"]
  main_material = _pipe(settings["main_pipe"])
  branch_material = _pipe(settings["branch_pipe"])
  if not city[0] == fill[0] or not city[1] > city_y > fill[1] or not outlet[1] > main_y > riser_y:
    raise ValueError("Keep straight north-side municipal intake and north-side tower takeoffs on the declared corridors.")
  runs = {"MU-CITY": _run("city", _pipe(settings["city_pipe"]), [city, (city[0], city_y, city[2]),
                    (fill[0], city_y, fill[2]), fill], from_node="city", to_node=tank["id"], elbows=2)}
  supply_taps = {}
  for tower in bindings:
    endpoint = f"{tower}.makeup"
    start, normal = _port(layout, endpoint)
    x, y, z = start
    if normal != [0, 1, 0] or not (main_z < z and y < riser_y < main_y and x < outlet[0]):
      raise ValueError(f"{endpoint}: the makeup flange must approach the north tower corridor.")
    supply_taps[tower] = (x, main_y, main_z)
    key = f"MU-{tower}"
    runs[key] = _run("makeup", branch_material, [supply_taps[tower], (x, riser_y, main_z), (x, riser_y, z), start],
                     from_node=f"MU-J-{tower}", to_node=endpoint, tower=tower, sdl_link=bindings[tower], elbows=2)
  start = (outlet[0], main_y, main_z)
  runs["MU-BOOST"] = _run("makeup", main_material, [outlet, (outlet[0], main_y, outlet[2]), start],
                          from_node=tank["id"], to_node="MU-HEAD", elbows=1)
  stations = [("MU-HEAD", start), *[(f"MU-J-{tower}", point) for tower, point in
               sorted(supply_taps.items(), key=lambda row: row[1][0], reverse=True)]]
  for index, ((node, a), (downstream, b)) in enumerate(pairwise(stations), 1):
    key = f"MU-M{index:02d}"
    runs[key] = _run("makeup", main_material, [a, b], from_node=node, to_node=downstream, elbows=0)
  return tank, runs


def _drain(settings, bindings, layout, east):
  y = settings["collector_y_m"]
  rim = settings["receiver_flood_rim_m"]
  tip = settings["nozzle_tip_above_ground_m"]
  width = number(settings["receiver_size_m"], "receiver envelope")
  grade = number(settings["collector_grade"], "collector grade")
  branch_grade = number(settings["branch_grade"], "branch grade")
  main, branch = [_pipe(settings[key], gravity=True) for key in ("main_pipe", "branch_pipe")]
  nozzle = {"size": "自由出流短管", "outside_m": number(settings["nozzle_od_m"], "outlet OD")}
  points = {tower: _port(layout, f"{tower}.blowdown") for tower in bindings}
  left = min(point[0] for point, _ in points.values())
  invert = settings["west_junction_invert_m"]
  runs, receptors = {}, {}
  nodes = {}
  for tower, (start, normal) in sorted(points.items(), key=lambda item: item[1][0][0]):
    x, flange_y, z = start
    receiver_y = settings["receiver_y_m"]
    if normal != [0, 1, 0] or not (flange_y < receiver_y < y and z > tip):
      raise ValueError(f"{tower}: drain outlet must face the receptor and discharge downwards.")
    receptor = f"AG-{tower}"
    end = (x, y, invert-(x-left)*grade)
    branch_invert = end[2]+(y-receiver_y)*branch_grade
    inlet = (x, receiver_y, branch_invert)
    nodes[tower] = end
    part = f"DR-{tower}"
    runs[f"NOZ-{tower}"] = _run("drain", nozzle, [start, (x, receiver_y, z), (x, receiver_y, tip)],
                                 tower=tower, from_node=f"{tower}.blowdown", to_node=receptor, sdl_link=bindings[tower])
    runs[part] = _gravity(branch, inlet, end, f"DR-J-{tower}",
                           from_node=receptor, to_node=f"DR-J-{tower}", tower=tower, sdl_link=bindings[tower])
    half = width/2
    receptors[tower] = {"id": receptor, "minimum_m": [x-half, receiver_y-half, branch_invert-.12],
                        "maximum_m": [x+half, receiver_y+half, rim], "gap_m": tip-rim, "inlet_invert_m": branch_invert}
  outfall = (east, y, invert-(east-left)*grade)
  stations = [(f"DR-J-{tower}", point) for tower, point in nodes.items()]+[("MH-EAST", outfall)]
  for i, ((source, a), (target, b)) in enumerate(pairwise(stations), 1):
    key = f"DR-M{i:02d}"
    runs[key] = _gravity(main, a, b, target, from_node=source, to_node=target)
  junctions = {name: {"id": name, "minimum_m": [x-.32, yc-.32, z-.20],
                      "maximum_m": [min(east, x+.32), yc+.32, 0.], "incoming_invert_m": z}
               for name, (x, yc, z) in stations}
  junctions["MH-EAST"]["minimum_m"][2] = min(outfall[2], settings["east_boundary_invert_m"])-.20
  return runs, receptors, junctions, {"point_m": outfall, "receiving_invert_m": settings["east_boundary_invert_m"], "id": "MH-EAST"}


def _buried_checks(runs, receptors, outfall, parcel, settings):
  x0, y0, x1, y1 = parcel
  tolerance = 1e-8
  envelopes, buried, covers = {}, {}, {}
  cover_limit = number(settings["minimum_cover_m"], "minimum cover")
  separation = number(settings["minimum_buried_crossing_clearance_m"], "utility crossing clearance")
  gap = number(settings["minimum_receiver_air_break_m"], "air break design gap")
  issues = []

  def fail(rule, first, last, message):
    issues.append({"rule": rule, "status": "fail", "objects": [first, last], "message": message})

  for key, row in runs.items():
    radius = row["pipe"]["outside_m"]/2
    for index, (a, b) in enumerate(pairwise(row["points_m"]), 1):
      ref = f"{key}/{index}"
      low = [min(p, q)-(radius if p == q else 0) for p, q in zip(a, b, strict=True)]
      high = [max(p, q)+(radius if p == q or row.get("slope") and axis == 2 else 0)
              for axis, (p, q) in enumerate(zip(a, b, strict=True))]
      if row.get("slope"):
        low[2] -= radius
      box = Space(low, high)
      if min(low[0]-x0, low[1]-y0, x1-high[0], y1-high[1]) < -tolerance:
        fail("AIDC_WET_PARCEL", ref, "parcel", f"{ref} leaves the modeled parcel.")
      envelopes[ref] = box
      if a[:2] != b[:2] and max(a[2], b[2]) < 0:
        covers[ref] = -high[2]
        buried[ref] = (row["service"], box, a, b, radius)
        if -high[2] < cover_limit:
          fail("AIDC_WET_COVER", ref, "grade", f"{ref} pipe crown requires at least {cover_limit:g} m cover.")
  crossings = []
  for key, (service, box, a, b, radius) in buried.items():
    if service == "drain":
      continue
    for other, (other_service, volume, c, d, other_radius) in buried.items():
      if other_service != "drain":
        continue
      overlap = [(max(lo, v_lo), min(hi, v_hi)) for lo, hi, v_lo, v_hi in
                 zip(box.minimum_m[:2], box.maximum_m[:2], volume.minimum_m[:2], volume.maximum_m[:2], strict=True)]
      if any(high-low <= tolerance for low, high in overlap):
        continue
      cross = [(lo+hi)/2 for lo, hi in overlap]
      index = 0 if c[0] != d[0] else 1
      rise_per_meter = (d[2]-c[2])/(d[index]-c[index])
      gravity_z = [c[2]+rise_per_meter*(value-c[index]) for value in overlap[index]]
      clearance = max(min(a[2], b[2])-max(gravity_z), min(gravity_z)-max(a[2], b[2]))-radius-other_radius
      crossings.append({"pressure_run": key, "gravity_run": other, "coordinate_xy_m": cross,
                        "gravity_centerline_m": c[2]+rise_per_meter*(cross[index]-c[index]), "vertical_clearance_m": clearance})
      if clearance < separation:
        fail("AIDC_WET_CROSSING", key, other, f"{key} clears {other} by {clearance:.3f} m, below {separation:.3f} m.")
  for name, row in receptors.items():
    if row["gap_m"] < gap:
      fail("AIDC_WET_AIR_BREAK", row["id"], f"{name}.blowdown", f"{name} free outlet does not clear its receptor rim.")
  if outfall["point_m"][2] <= outfall["receiving_invert_m"]:
    fail("AIDC_WET_OUTFALL", outfall["id"], "receiving-invert", "Receiving invert must be lower than site collector.")
  return envelopes, covers, crossings, issues


def build_wet_utilities(context):
  path = source_path(context, "wet-utilities.json")
  if not path.is_file():
    return missing_input(context, path, "wet-utilities")
  cfg = read_versioned(path)
  if cfg.get("state") != "assumed" or not cfg.get("basis"):
    raise ValueError("Site utility geometry needs an explicit assumed basis.")
  sdl = json.loads(path.with_name("system.sdl.json").read_text(encoding="utf-8"))
  root = sdl["components"][sdl["root"]]
  layout = staged_json(context, f"{MODELS}/layout.json")["objects"]
  site = staged_json(context, f"{MODELS}/site.json")
  cw = staged_json(context, CW)
  towers = {cell["tower"] for cell in cw["cells"].values()}
  supply = _net(root, towers, cfg["supply"], "makeup")
  drain = _net(root, towers, cfg["drain"], "drain")
  tank, pressure = _makeup(cfg["supply"], supply, layout)
  if not isclose(pressure["MU-CITY"]["points_m"][0][1], site["boundary_m"][3]):
    raise ValueError("Municipal connection must be anchored to the declared north parcel boundary.")
  gravity, receptors, junctions, outfall = _drain(cfg["drain"], drain, layout, site["boundary_m"][2])
  result = model_report(context, "wet-utilities", {
    "basis": cfg["basis"], "frame": site["frame"], "parcel_m": site["boundary_m"], "tank": tank,
    "runs": pressure | gravity, "receivers": receptors, "junctions": junctions, "outfall": outfall, "towers": sorted(towers),
    "criteria": {key: cfg[key] for key in ("minimum_cover_m", "minimum_buried_crossing_clearance_m",
                                             "minimum_receiver_air_break_m")},
  })
  schedule = context.build_path / "schedules/aidc-wet-utility-pipes.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("id", "service", "from", "to", "size", "centerline_m", "upstream_invert_m",
                    "downstream_invert_m", "slope", "sdl_link"))
    for key, row in sorted((pressure | gravity).items()):
      table.writerow((key, row["service"], row["from_node"], row["to_node"], row["pipe"]["size"],
                      f"{row['length_m']:.3f}", *row.get("invert_m", ("", "")), row.get("slope", ""), row.get("sdl_link", "")))
  return TargetResult(result.status, (*result.artifacts, artifact(context, "wet-utility-pipes", schedule)))


def build_wet_coordination(context):
  model = staged_json(context, MODEL)
  arch = staged_json(context, ARCH)
  envelopes, covers, crossings, issues = _buried_checks(model["runs"], model["receivers"], model["outfall"],
                                                     model["parcel_m"], model["criteria"])
  tank = model["tank"]
  if tank["free_fill_gap_m"] < model["criteria"]["minimum_receiver_air_break_m"]:
    issues.append({"rule": "AIDC_WET_TANK_AIR_BREAK", "status": "fail", "objects": [tank["id"]],
                   "message": "Tank fill opening does not achieve the declared free air gap above high water."})
  obstacles = _obstacles(context, arch)
  obstacles.update({f"CW-{key}/{index+1}": shape for key, index, shape in _segments(staged_json(context, CW))})
  structures = [tank, *model["receivers"].values(), *model["junctions"].values()]
  shapes = envelopes | {row["id"]: space(row) for row in structures}
  for name, shape in shapes.items():
    for blocker, volume in obstacles.items():
      if shape.intersects(volume):
        issues.append({"rule": "AIDC_WET_CLASH", "status": "fail", "objects": [name, blocker],
                       "message": f"{name} intersects {blocker}."})
  report = model_report(context, "wet-utility-coordination", {
    "model_digest": file_digest(context.build_path / MODEL), "minimum_cover_m": min(covers.values()),
    "run_cover_m": covers, "crossings": crossings, "issues": issues,
  }, issues)
  if issues:
    return report
  stage = Usd.Stage.Open(str(context.build_path / BASE_SCENE))
  if stage is None:
    raise ValueError("Cannot open the cooling-water coordination scene.")
  for key, row in model["runs"].items():
    path = f"/AIDC/WetUtilities/{key.replace('-', '_')}"
    _pipe_prim(stage, path, key, row["points_m"], row["pipe"]["outside_m"], COLORS[row["service"]])
  for row in structures:
    _prim(stage, f"/AIDC/WetUtilities/{row['id'].replace('-', '_')}", row["id"], space(row), (.37, .53, .55))
  output = context.build_path / SCENE
  if not stage.Flatten(False).Export(str(output)):
    raise OSError(f"Could not export {output.name}.")
  config = source_path(context, "wet-utilities.json").relative_to(context.root).as_posix()
  refs = {key: {"file": config, "path": "drain" if row["service"] == "drain" else "supply"}
          for key, row in model["runs"].items()}
  refs.update({row["id"]: {"file": config, "path": "supply" if row["id"] == tank["id"] else "drain"} for row in structures})
  return TargetResult(report.status, (*report.artifacts, artifact(context, "wet-utilities-scene", output,
                        preview={"kind": "scene", "source_map": refs})))
