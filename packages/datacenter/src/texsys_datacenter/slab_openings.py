"""Resolve service penetrations and structural cutouts on the same floor planes."""

from itertools import combinations
from math import hypot, isclose
from pathlib import Path

from texsys.formal import _load_json
from texsys.usd_brep import _prim_name
from texsys_structure.deck import DeckSlab
from texsys_structure.openings import PlanRect, Sleeve, slab_regions

from .unit import _number


def _rectangle(origin, size):
  x, y = origin
  w, h = size
  return PlanRect(x, y, x + w, y + h)


def _steel_rect(start, end, cross, width, along_x):
  half = width / 2
  return PlanRect(start, cross - half, end, cross + half) if along_x else PlanRect(cross - half, start, cross + half, end)


def _member_footprints(frame, reference, level):
  primary = {name: _steel_rect(*item["start_end_cross_m"], frame["sections"][item["kind"]]["width"], item["kind"] == "tie")
             for name, item in reference[level]["members"].items()}
  if level != "Roof":
    primary.update({
      f"{unit}/{name}": _steel_rect(*patch["x"], patch["y"][0] + beam["y_m"], beam["section_m"]["width"], True)
      for unit, patch in frame["unit_patches"].items() for name, beam in frame["secondary_beams"].items()
    })
  return primary


def _slab_crossings(fire, levels, concrete_depth, apertures):
  crossings = {}
  for key, entry in fire["penetrations"]["crossings"].items():
    barrier = fire["barriers"][entry["barrier"]]
    if barrier["axis"] != 2:
      continue
    level, _, name = entry["barrier"].partition("/")
    if name != "Slab" or not isclose(barrier["station_m"], levels[level], abs_tol=1e-8):
      raise ValueError("A floor fire barrier must coincide with a structural slab datum.")
    route = fire["routes"][entry["route"]]
    selection = entry.get("selection")
    if selection is None:
      raise ValueError(f"Floor crossing {key} needs a firestop sleeve selection.")
    x, y, z = selection["center_m"]
    if not isclose(z, levels[level], abs_tol=1e-8):
      raise ValueError(f"Sleeve {key} must use its slab datum.")
    sleeve = Sleeve((x, y), selection["diameter_mm"] / 2000)
    shaft = fire["shaft_by_route"].get(entry["route"])
    if shaft is not None and shaft not in apertures:
      raise ValueError(f"Route {entry['route']} names an unknown structural shaft.")
    system = fire["firestop_systems"][selection["system"]]
    crossings[key] = {
      "level": level, "route": entry["route"], "source_crossing": key, "shaft": shaft,
      "host": "shaft-closure" if shaft else "main-slab", "pipe_crossing_m": entry["point_m"],
      "pipe_outside_mm": route["outside_mm"], "sleeve_centre_m": selection["center_m"],
      "sleeve_diameter_mm": selection["diameter_mm"], "cut_bounds_m": sleeve.bounds.xyxy,
      "firestop_system": selection["system"], "firestop_state": entry["evidence_state"], "firestop_status": entry["status"],
      "host_concrete_min_depth_m": concrete_depth, "required_edge_clearance_m": system["edge_spacing_mm"] / 1000,
      "host_compatible": (entry["status"] == "pass" and barrier["substrate"] == system["substrate"]
                          and 1000 * concrete_depth + 1e-8 >= system["min_thickness_mm"]
                          and hypot(x - entry["point_m"][0], y - entry["point_m"][1]) + route["outside_mm"] / 2000
                          <= sleeve.radius_m + 1e-9),
    }
  return crossings


def _floor_layout(level, frame, requests, crossings, apertures, slab, members, clearance):
  records = {}
  for unit, patch in frame["unit_patches"].items() if level != "Roof" else ():
    for name, request in requests.items():
      x, y = request["origin_m"]
      rectangle = _rectangle((patch["x"][0] + x, patch["y"][0] + y), request["size_m"])
      records[f"{unit}/{name}"] = {
        "role": "unit-reservation", "service": request["service"], "unit": unit, "template": name,
        "bounds_m": rectangle.xyxy, "binding": "unassigned",
      }
  active_shafts = {entry["shaft"] for entry in crossings.values() if entry["shaft"] is not None}
  for shaft in sorted(active_shafts):
    records[shaft] = {"role": "shaft-aperture", "bounds_m": apertures[shaft].xyxy, "binding": "shaft-reservation"}
  for key, entry in crossings.items():
    if entry["shaft"] is None:
      records[key] = {"role": "main-slab-sleeve", "bounds_m": entry["cut_bounds_m"], "binding": "routed", "crossing": key}

  issues = []
  bounds = {key: PlanRect(*record["bounds_m"]) for key, record in records.items()}
  for key, cut in bounds.items():
    if not slab.contains(cut, clearance):
      issues.append({"opening": key, "check": "slab-edge-clearance"})
    # Structural cuts and keep-outs use the sleeve's conservative outer rectangle.
    separations = {name: cut.gap(beam) for name, beam in members.items()}
    nearest = min(separations, key=separations.__getitem__)
    records[key]["nearest_steel"] = {"member": nearest, "clearance_m": separations[nearest]}
    clashes = [name for name, gap in separations.items() if gap + 1e-9 < clearance]
    if clashes:
      issues.append({"opening": key, "check": "steel-clearance", "members": sorted(clashes)})
  for (key, cut), (other, neighbour) in combinations(bounds.items(), 2):
    if cut.gap(neighbour) + 1e-9 < clearance:
      issues.append({"opening": key, "other": other, "check": "cut-clearance"})

  sleeves = {key: Sleeve(tuple(entry["sleeve_centre_m"][:2]), entry["sleeve_diameter_mm"] / 2000)
             for key, entry in crossings.items()}
  for key, entry in crossings.items():
    sleeve = sleeves[key]
    host = apertures[entry["shaft"]] if entry["shaft"] is not None else slab
    edge = max(clearance, entry["required_edge_clearance_m"])
    if not host.contains(sleeve.bounds, edge):
      issues.append({"opening": key, "check": "sleeve-host-edge-clearance", "host": entry["shaft"] or "main-slab"})
    if not entry["host_compatible"]:
      issues.append({"opening": key, "check": "firestop-host-or-service-binding"})
    if entry["shaft"] is None:
      for other, cut in bounds.items():
        if other != key and sleeve.gap(cut) + 1e-9 < edge:
          issues.append({"opening": key, "other": other, "check": "sleeve-adjacent-cut-clearance"})
  for (key, first), (other, second) in combinations(sleeves.items(), 2):
    if crossings[key]["shaft"] == crossings[other]["shaft"]:
      edge = max(clearance, crossings[key]["required_edge_clearance_m"], crossings[other]["required_edge_clearance_m"])
      gap = hypot(*(a - b for a, b in zip(first.centre_m, second.centre_m, strict=True))) - first.radius_m - second.radius_m
      if gap + 1e-9 < edge:
        issues.append({"opening": key, "other": other, "check": "sleeve-spacing"})
  return records, issues


def evaluate_slab_openings(source: Path, building: dict, frame: dict, floor: dict, fire: dict, deck_path: Path,
                           fws_branches=None):
  config = _load_json((source / "slab-openings.study.json").read_text(encoding="utf-8"))
  if (type(config["schema_version"]) is not int or config["schema_version"] != 1
      or config["state"] != "assumed" or not config["basis"].strip()):
    raise ValueError("Slab opening coordination requires schema version 1 and an assumed design basis.")
  if building["frame"] != frame["frame"] or fire["frame"] != frame["frame"]:
    raise ValueError("Slab opening sources must share the building coordinate frame.")
  clearance = _number(config["minimum_steel_clearance_m"], "minimum_steel_clearance_m")
  inset = _number(config["shaft_aperture_inset_m"], "shaft_aperture_inset_m")
  roof_depth = _number(config["roof_coordination_depth_m"], "roof_coordination_depth_m")
  levels = dict(sorted(frame["levels_m"].items(), key=lambda item: item[1]))
  requests = floor["opening_requests"]
  archived = {}
  policy = config.get("unit_fws_opening_policy", "retain")
  if policy == "retire-when-served-above-floor":
    if (not fws_branches or fws_branches["frame"] != frame["frame"] or fws_branches["status"] != "pass"
        or fws_branches["structural_floor_crossings"] or fws_branches["outlets"].keys() != building["levels_m"].keys()
        or any(set(units) != frame["unit_patches"].keys()
               for trains in fws_branches["outlets"].values() for units in trains.values())):
      raise ValueError("Retiring FWS cutouts requires coordinated same-floor routes to all unit CDU ports.")
    archived = {name: request for name, request in requests.items() if request["service"] == "FWS"}
    requests = {name: request for name, request in requests.items() if name not in archived}
  elif policy != "retain":
    raise ValueError("Unsupported unit FWS opening policy.")
  slab = PlanRect(0., 0., *building["floor_m"])
  depth = floor["concrete_min_depth_m"]
  apertures = {name: _rectangle(zone["origin_m"], zone["size_m"]).inset(inset)
               for name, zone in fire["shaft_enclosures"].items()}
  crossings = _slab_crossings(fire, levels, depth, apertures)
  reference = next(iter(frame["cases"].values()))["floors"]
  reports, issues = {}, []
  for level in levels:
    sources = {key: item for key, item in crossings.items() if item["level"] == level}
    cuts, failed = _floor_layout(level, frame, requests, sources, apertures, slab,
                                 _member_footprints(frame, reference, level), clearance)
    issues.extend({"level": level, **item} for item in failed)
    reports[level] = {"cuts": cuts, "crossings": sorted(sources), "depth_m": roof_depth if level == "Roof" else depth}
  pending = [f"{level}/{key}" for level, report in reports.items() for key, cut in report["cuts"].items()
             if cut["binding"] == "unassigned"]
  deck = DeckSlab(**floor["deck_parameters"])
  unit_cuts = tuple(_rectangle(request["origin_m"], request["size_m"]) for request in floor["opening_requests"].values())
  deck.write_usd(deck_path, unit_cuts)
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "frame": frame["frame"],
    "formal_review": "pending", "status": "pass" if not issues else "fail",
    "routing_status": "routing-required" if pending else "bound",
    "plan_bounds_m": slab.xyxy, "levels_m": levels, "levels": reports, "crossings": crossings,
    "unbound_unit_reservations": pending, "conflicts": issues,
    "retired_unit_reservations": [f"{level}/{unit}/{name}" for level in building["levels_m"]
                                  for unit in frame["unit_patches"] for name in archived],
    "unit_fws_opening_policy": policy,
    "minimum_steel_clearance_m": clearance, "shaft_aperture_inset_m": inset,
    "unit_deck_detail": {"basis": "unit-template-with-retired-fws-option" if archived else "unit-opening-template",
                         "gross_volume_m3": deck.volume_m3(), "net_volume_m3": deck.net_volume_m3(unit_cuts),
                         "cuts_xyxy_m": {name: _rectangle(req["origin_m"], req["size_m"]).xyxy
                                         for name, req in floor["opening_requests"].items()}},
    "pending": (["unit-branch-pipe-routes"] if pending else []) + [
      "shaft-closure-support-and-rated-system-evidence", "steel-deck-trimmers-and-local-opening-reinforcement",
      "wall-opening-structural-coordination",
    ],
  }


def write_slab_openings_usd(report, path):
  from pxr import Gf, Sdf, Usd, UsdGeom

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/SlabOpenings").GetPrim())

  def panel(parent, name, regions, top, thickness, color):
    points, faces = [], []
    for region in regions:
      a, b, c, d = region.xyxy
      corners = ((a, b), (c, b), (c, d), (a, d))
      start = len(points)
      points.extend(Gf.Vec3f(x, y, z) for z in (top - thickness, top) for x, y in corners)
      faces.extend(tuple(start + index for index in face) for face in (
        (3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
      ))
    if not points:
      raise ValueError(f"Slab panel {name} has no remaining material.")
    mesh = UsdGeom.Mesh.Define(stage, f"{parent}/{_prim_name(name)}")
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr([4] * len(faces))
    mesh.CreateFaceVertexIndicesAttr([i for face in faces for i in face])
    mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    mesh.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(name)

  slab = PlanRect(*report["plan_bounds_m"])
  for level, item in report["levels"].items():
    top = report["levels_m"][level]
    parent = f"/SlabOpenings/{_prim_name(level)}"
    UsdGeom.Scope.Define(stage, parent)
    cutouts = [PlanRect(*cut["bounds_m"]) for cut in item["cuts"].values()]
    panel(parent, f"{level}/MainSlab", slab_regions(slab, cutouts), top, item["depth_m"], (.65, .63, .56))
    for key, cut in item["cuts"].items():
      if cut["role"] != "shaft-aperture":
        continue
      sleeves = [PlanRect(*report["crossings"][source]["cut_bounds_m"]) for source in item["crossings"]
                 if report["crossings"][source]["shaft"] == key]
      panel(parent, f"{level}/{key}/Closure", slab_regions(PlanRect(*cut["bounds_m"]), sleeves),
            top, item["depth_m"], (.47, .65, .63))
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
