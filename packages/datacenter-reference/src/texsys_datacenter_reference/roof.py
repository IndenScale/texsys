"""Compile the hall roof, secondary steel and service openings from one geometry."""

import csv
from itertools import combinations, pairwise
from math import isclose

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCHITECTURE, bounds, space
from .fws_network import MODEL as FWS, pipe_envelope
from .power_routing import MODEL as POWER
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json
from .structure import MODEL as STRUCTURE


MODEL = f"{MODELS}/roof-assembly.json"
LAYERS = ("deck", "insulation", "membrane")


def _rect(x0, y0, x1, y1):
  return [x0, y0, x1, y1]


def _area(rect):
  x0, y0, x1, y1 = rect
  return (x1-x0)*(y1-y0)


def _intersection(a, b):
  clip = _rect(max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
  return clip if clip[2] > clip[0]+1e-9 and clip[3] > clip[1]+1e-9 else None


def _contains(outer, inner):
  return _intersection(outer, inner) == inner


def _square(point, size):
  x, y, _ = point
  half = size/2
  return _rect(x-half, y-half, x+half, y+half)


def roof_surfaces(assembly, y):
  """Return elevations of the underside and the top of each physical layer."""
  section = assembly["section"]
  result = [section["deck_base_m"]]
  for layer in LAYERS:
    thickness = section["layers"][layer]["eave_thickness_m"]
    if layer == "insulation":
      thickness += section["slope"] * min(y-section["eave_y_m"], section["north_eave_y_m"]-y)
    result.append(result[-1]+thickness)
  return result


def _volume(rect, roof, layer):
  index = LAYERS.index(layer)
  slices = [rect[1], rect[3]]
  ridge = roof["section"]["ridge_y_m"]
  if rect[1] < ridge < rect[3]:
    slices.insert(1, ridge)
  volume = 0.
  for a, b in pairwise(slices):
    low, high = (roof_surfaces(roof, y) for y in (a, b))
    volume += (low[index+1]-low[index]+high[index+1]-high[index])/2*(b-a)*(rect[2]-rect[0])
  return volume


def _measure(rect, openings, roof):
  cuts = [cut for row in openings.values() if (cut := _intersection(rect, row["hole_xy_m"]))]
  return {
    "gross_area_m2": _area(rect),
    "net_area_m2": _area(rect)-sum(map(_area, cuts)),
    "volume_m3": {layer: _volume(rect, roof, layer)-sum(_volume(cut, roof, layer) for cut in cuts) for layer in LAYERS},
  }


def _tiles(rect, holes):
  """Tessellate material into rectangles, leaving holes out of the actual mesh."""
  x0, y0, x1, y1 = rect
  stations = sorted({x0, x1, *(x for h in holes for x in (h[0], h[2]))})
  result = []
  for a, b in pairwise(stations):
    mid = (a+b)/2
    cursor = y0
    for low, high in sorted((lo, hi) for left, lo, right, hi in holes if left < mid < right):
      if low > cursor+1e-9:
        result.append(_rect(a, cursor, b, low))
      cursor = max(cursor, high)
    if y1 > cursor+1e-9:
      result.append(_rect(a, cursor, b, y1))
  return result


def _opening_sources(fws, power):
  openings = {}
  for key, row in fws["penetrations"].items():
    segment = fws["segments"][row["segment"]]
    openings[key] = (row["point_m"], row["square_width_m"], pipe_envelope(segment), "FWS", row["segment"],
                     segment["sdl_links"])
  for train in power["trains"].values():
    for key, row in train["segments"].items():
      if row["kind"] == "drop":
        size = power["assumptions"]["penetration"]["sleeve_width_m"]
        openings[key] = (row["points_m"][0], size, space(row["envelope"]), "power", key, row["links"])
  return openings


def _issue(diagnostics, rule, objects, message):
  diagnostics.append({"rule": f"AIDC_ROOF_{rule}", "status": "fail", "objects": objects, "message": message})


def _openings(roof, regions, rooms, candidates, diagnostics):
  curb = roof["assumptions"]["curbs"]
  flange = number(curb["flange_width_m"], "curb flange width")
  upstand = number(curb["upstand_m"], "curb upstand height")
  clearance = number(curb["minimum_clearance_m"], "curb service clearance", zero=True)
  openings = {}
  for key, (point, size, route, service, run, links) in candidates.items():
    sq = _square(point, number(size, "opening width"))
    outer = _square(point, size+2*flange)
    low, high = outer[1], outer[3]
    # Keep the panel seam, roof ridge and fire partition continuous around each hole.
    owners = [name for name, shape in regions.items() if _contains(shape, outer)]
    compartment = [name for name, room in rooms.items() if _contains([*room["minimum_m"][:2], *room["maximum_m"][:2]], sq)]
    levels = [roof_surfaces(roof, y)[-1] for y in (low, high)]
    top = max(levels) + upstand
    underside = roof["section"]["deck_base_m"]
    if len(owners) != 1 or len(compartment) != 1:
      _issue(diagnostics, "OPENING_BOUNDARY", [key], f"{key} crosses a panel bay boundary or lacks a unique fire compartment.")
    if route.minimum_m[2] >= underside or route.maximum_m[2] <= top:
      _issue(diagnostics, "OPENING_HEIGHT", [key, run], f"{run} does not extend through the deck and curb at {key}.")
    inner = [sq[0]+clearance, sq[1]+clearance, sq[2]-clearance, sq[3]-clearance]
    footprint = [*route.minimum_m[:2], *route.maximum_m[:2]]
    if not _contains(inner, footprint):
      _issue(diagnostics, "SERVICE_CLEARANCE", [key, run], f"{key} lacks its declared clearance around {run}.")
    openings[key] = {"panel": owners[0] if len(owners) == 1 else None,
                     "compartment": compartment[0] if len(compartment) == 1 else None,
                     "point_m": [*point[:2], roof_surfaces(roof, point[1])[-1]],
                     "hole_xy_m": sq, "curb_xy_m": outer, "curb_top_m": top,
                     "curb_base_m": min(levels), "width_m": size, "service": service, "run": run,
                     "service_envelope": bounds(route), "sdl_links": links,
                     "seal_state": "pending-firestop-and-weatherproofing-selection"}
  for (first, a), (second, b) in combinations(openings.items(), 2):
    if _intersection(a["curb_xy_m"], b["curb_xy_m"]):
      _issue(diagnostics, "CURB_CLASH", [first, second], f"{first} and {second} have overlapping flashing.")
  return openings


def _members(roof, regions, axes_x, axes_y, openings, diagnostics):
  config = roof["assumptions"]["purlins"]
  width = number(config["width_m"], "purlin width")
  depth = roof["section"]["purlin_depth_m"]
  base = roof["section"]["steel_top_m"]
  positions = [number(value, "purlin station fraction") for value in config["bay_fractions"]]
  if not positions or positions != sorted(set(positions)) or positions[-1] >= 1:
    raise ValueError("Purlin fractions must increase strictly within each structural bay.")
  spacing = number(config["max_spacing_m"], "maximum deck support spacing")
  overhang = number(config["max_deck_overhang_m"], "maximum deck edge overhang")
  result = {}
  stations = []
  roof_x0, _, roof_x1, _ = roof["outline_xy_m"]
  for (ax0, x0), (ax1, x1) in pairwise(axes_x):
    centers = [x0+p*(x1-x0) for p in positions]
    stations.extend(centers)
    for (ay0, y0), (ay1, y1) in pairwise(axes_y):
      panel_id = f"RF-{ax0}-{ax1}-{ay0}-{ay1}"
      panel = regions[panel_id]
      boundaries = [panel[0], *((a+b)/2 for a, b in pairwise(centers)), panel[2]]
      for index, x in enumerate(centers, 1):
        key = f"PU-{ax0}-{ax1}-{ay0}-{ay1}-{index:02}"
        member = Space((x-width/2, y0, base), (x+width/2, y1, base+depth))
        for opening, row in openings.items():
          if _intersection([x-width/2, y0, x+width/2, y1], row["hole_xy_m"]):
            _issue(diagnostics, "PURLIN_OPENING", [key, opening], f"{key} obstructs the service opening {opening}.")
        strip = _rect(boundaries[index-1], panel[1], boundaries[index], panel[3])
        result[key] = {**bounds(member), "panel": panel_id, "center_x_m": x, "span_m": y1-y0,
                       "supports": [f"G-{ax0}-{ax1}-{axis}" for axis in (ay0, ay1)],
                       "tributary_strip_xy_m": strip, **_measure(strip, openings, roof)}
  for a, b in pairwise(stations):
    if b-a > spacing:
      _issue(diagnostics, "DECK_SPAN", ["roof-deck"], f"Deck support spacing {b-a:.3f} m exceeds {spacing:.3f} m at X={a:.3f}.")
  if max(stations[0]-roof_x0, roof_x1-stations[-1], roof["assumptions"]["overhang_m"]) > overhang:
    _issue(diagnostics, "EAVE_OVERHANG", ["roof-deck"], "Roof overhang exceeds the declared deck limit.")
  return result


def _wall_heads(roof, walls, diagnostics):
  undersurface = roof["section"]["deck_base_m"]
  result = {}
  for key, wall in walls.items():
    x0, y0, _ = wall["minimum_m"]
    x1, y1, top = wall["maximum_m"]
    head = _rect(x0, y0, x1, y1)
    if not _contains(roof["outline_xy_m"], head) or top >= undersurface:
      _issue(diagnostics, "WALL_HEAD", [key], f"{key} head cannot connect to the underside of the roof deck.")
      continue
    cuts = {}
    for name, purlin in roof["purlins"].items():
      a, b, low = purlin["minimum_m"]
      c, d, high = purlin["maximum_m"]
      if low < undersurface and high > top and (cut := _intersection(head, [a, b, c, d])):
        cuts[name] = cut
    result[key] = {"z_m": [top, undersurface], "finish_state": "pending-movement-joint-and-firestop",
                   "tiles_xy_m": _tiles(head, list(cuts.values())), "purlin_joints": cuts}
  return result


def _drainage(roof, yard):
  config = roof["assumptions"]["drainage"]
  width = number(config["gutter_width_m"], "gutter width")
  depth = number(config["gutter_depth_m"], "gutter depth")
  pitch = number(config["longitudinal_slope"], "gutter slope")
  offset = number(config["outlet_offset_m"], "gutter outlet offset")
  leader_bottom = yard + number(config["handoff_above_yard_m"], "rainwater handoff")
  x0, y0, x1, y1 = roof["outline_xy_m"]
  center = (x0+x1)/2
  ridge = roof["section"]["ridge_y_m"]
  outlets = {}
  if offset >= (x1-x0)/2:
    raise ValueError("Rainwater outlet offset must fit its eave reach.")
  for edge, eave, sign in (("S", y0, -1), ("N", y1, 1)):
    gutter_y = eave + sign*width/2
    for direction, limit, x in (("W", x0, x0+offset), ("E", x1, x1-offset)):
      crown = roof_surfaces(roof, eave)[-1]-depth
      low = crown-pitch*abs(center-x)
      gutter = [[center, gutter_y, crown], [x, gutter_y, low], [limit, gutter_y, low+pitch*offset]]
      catchment = _rect(min(center, limit), min(eave, ridge), max(center, limit), max(eave, ridge))
      key = f"RW-{edge}-{direction}"
      outlets[key] = {"catchment_xy_m": catchment, "catchment_m2": _area(catchment), "gutter_invert_m": gutter,
                      "leader_top_m": gutter[1], "handoff_m": [x, gutter_y, leader_bottom],
                      "compartment_side": edge}
  return outlets


def _schedule(context, filename, columns, rows):
  output = context.build_path / f"schedules/{filename}"
  output.parent.mkdir(parents=True, exist_ok=True)
  with output.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(columns)
    writer.writerows(rows)
  return artifact(context, filename.removesuffix(".csv"), output)


def build_roof(context):
  source = source_path(context, "roof.json")
  if not source.is_file():
    return missing_input(context, source, "roof-assembly")
  config = read_versioned(source)
  if config.get("state") != "assumed" or not config.get("basis"):
    raise ValueError("The roof assembly requires an explicit design basis.")
  arch, steel = staged_json(context, ARCHITECTURE), staged_json(context, STRUCTURE)
  fws, power = staged_json(context, FWS), staged_json(context, POWER)
  x = sorted(steel["x"].items(), key=lambda item: item[1])
  y = sorted(steel["y"].items(), key=lambda item: item[1])
  ridge = steel["y"][config["ridge_axis"]]
  room = space(arch["hall"])
  z = room.maximum_m[2]
  if not y[0][1] < ridge < y[-1][1]:
    raise ValueError("The ridge must be on an internal structural support axis.")
  overhang = number(config["overhang_m"], "roof overhang")
  ox0, oy0, ox1, oy1 = _rect(x[0][1]-overhang, y[0][1]-overhang, x[-1][1]+overhang, y[-1][1]+overhang)
  depth = number(config["purlins"]["depth_m"], "purlin depth")
  for name in LAYERS:
    number(config["layers"][name]["eave_thickness_m"], f"{name} eave thickness")
    number(config["layers"][name]["effective_unit_weight_kn_m3"], f"{name} effective unit weight")
  roof = {"state": config["state"], "assumptions": config, "outline_xy_m": [ox0, oy0, ox1, oy1],
          "grid": {"x": dict(x), "y": dict(y)},
          "section": {"steel_top_m": z, "purlin_depth_m": depth, "deck_base_m": z+depth, "ridge_y_m": ridge,
                      "eave_y_m": oy0, "north_eave_y_m": oy1, "layers": config["layers"],
                      "primary_girder_m": steel["settings"]["sections_m"]["girder"],
                      "slope": number(config["slope"], "roof slope")}}
  if not isclose(ridge-oy0, oy1-ridge, abs_tol=1e-7):
    raise ValueError("The declared equal roof slopes require equal spans from each eave to the ridge.")
  regions = {}
  for (ax0, x0), (ax1, x1) in pairwise(x):
    for (ay0, y0), (ay1, y1) in pairwise(y):
      regions[f"RF-{ax0}-{ax1}-{ay0}-{ay1}"] = _rect(
        ox0 if x0 == x[0][1] else x0, oy0 if y0 == y[0][1] else y0,
        ox1 if x1 == x[-1][1] else x1, oy1 if y1 == y[-1][1] else y1,
      )
  diagnostics = []
  openings = _openings(roof, regions, arch["rooms"], _opening_sources(fws, power), diagnostics)
  roof["openings"] = openings
  roof["purlins"] = _members(roof, regions, x, y, openings, diagnostics)
  roof["wall_heads"] = _wall_heads(roof, arch["walls"], diagnostics)
  roof["panels"] = {}
  for key, rect in regions.items():
    holes = {name: row for name, row in openings.items() if row["panel"] == key}
    tiles = _tiles(rect, [row["hole_xy_m"] for row in holes.values()])
    quantities = _measure(rect, holes, roof)
    if not diagnostics and not isclose(sum(map(_area, tiles)), quantities["net_area_m2"], abs_tol=1e-8):
      raise ValueError(f"Material area does not close across the generated openings in {key}.")
    roof["panels"][key] = {"extent_xy_m": rect, "tiles_xy_m": tiles, "openings": list(holes), **quantities}
  yard = arch["yard_m"]
  roof["drainage"] = _drainage(roof, yard)
  roof["input_digests"] = {name: file_digest(context.build_path / name) for name in (ARCHITECTURE, STRUCTURE, FWS, POWER)}
  report = model_report(context, "roof-assembly", roof, diagnostics)
  schedule = _schedule(context, "aidc-roof-openings.csv",
                       ("opening", "panel", "compartment", "service", "source_run", "x_m", "y_m", "weather_surface_m",
                        "clear_width_mm", "curb_top_m", "sdl_links", "seal_state"),
                       ((key, row["panel"], row["compartment"], row["service"], row["run"], *row["point_m"],
                         row["width_m"]*1000, row["curb_top_m"], " ".join(row["sdl_links"]), row["seal_state"])
                        for key, row in openings.items()))
  quantities = _schedule(context, "aidc-roof-panels.csv", ("panel", "gross_m2", "net_m2", *[f"{name}_m3" for name in LAYERS]),
                         ((key, row["gross_area_m2"], row["net_area_m2"], *row["volume_m3"].values())
                          for key, row in roof["panels"].items()))
  return TargetResult(report.status, (*report.artifacts, schedule, quantities), report.diagnostics)
