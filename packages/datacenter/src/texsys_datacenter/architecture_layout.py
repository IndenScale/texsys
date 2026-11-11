"""Publish a cut architectural scene using verified door and wall-penetration identities."""

from collections import defaultdict
from dataclasses import asdict
from math import isclose

from pxr import Gf, Usd, UsdGeom

from texsys.usd_brep import _prim_name
from texsys_architecture import Opening, WallPanel


def _intersects(first, second):
  return all(max(a, c) < min(b, d) - 1e-8
             for (a, b), (c, d) in zip((first.along_m, first.vertical_m),
                                      (second.along_m, second.vertical_m), strict=True))


def _box(stage, path, bounds, color, opacity=1.):
  minimum, maximum = bounds["minimum_m"], bounds["maximum_m"]
  cube = UsdGeom.Cube.Define(stage, path)
  cube.CreateSizeAttr(1)
  cube.AddTranslateOp().Set(Gf.Vec3d(*((a + b) / 2 for a, b in zip(minimum, maximum, strict=True))))
  cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*(b - a for a, b in zip(minimum, maximum, strict=True))))
  cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  cube.CreateDisplayOpacityAttr([opacity])
  return cube.GetPrim()


def _outline(stage, path, panel, hole, color):
  left, right = hole.along_m
  bottom, top = hole.vertical_m
  points = []
  for across, elevation in ((left, bottom), (left, top), (right, top), (right, bottom), (left, bottom)):
    coords = [across, elevation]
    coords.insert(panel.axis, panel.station_m)
    points.append(Gf.Vec3f(*coords))
  curve = UsdGeom.BasisCurves.Define(stage, path)
  curve.CreateTypeAttr("linear")
  curve.CreateCurveVertexCountsAttr([len(points)])
  curve.CreatePointsAttr(points)
  curve.CreateWidthsAttr([.025])
  curve.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  return curve.GetPrim()


def _annotate(prim, identity, record):
  prim.SetCustomDataByKey("identity", identity)
  for key in ("definition", "host", "role", "system"):
    if key in record:
      prim.SetCustomDataByKey(key, record[key])


def evaluate_architecture_layout(building, fire, egress, output):
  model = building.get("architecture")
  if model is None:
    raise ValueError("Architecture layout requires the project's architectural definitions.")
  if not model["frame"] == fire["frame"] == egress["frame"]:
    raise ValueError("Architecture, fire and egress must share the building frame.")
  panels = {key: WallPanel(**{name: record[name] for name in ("axis", "station_m", "extent_m", "thickness_m")})
            for key, record in model["walls"].items()}
  cuts = defaultdict(dict)
  door_cuts, service_cuts, issues = {}, {}, []
  if model["doors"].keys() != egress["doors"].keys():
    issues.append({"check": "egress-door-coverage"})
  for identity, door in model["doors"].items():
    verified = egress["doors"].get(identity, {})
    if verified.get("architecture_opening") != identity or verified.get("host") != door["host"]:
      issues.append({"check": "egress-door-binding", "opening": identity})
    host = door["host"]
    cut = Opening(**door["opening"])
    cuts[host][identity] = cut
    door_cuts[identity] = door
  for identity, penetration in fire["penetrations"]["crossings"].items():
    host = penetration["barrier"]
    record = fire["barriers"][host]
    if record["host"] == "floor":
      continue
    panel = panels[host]
    if (penetration["status"] != "pass" or panel.axis != record["axis"]
        or not isclose(panel.thickness_m * 1000, record["thickness_mm"], abs_tol=1e-8)
        or panel.station_m != record["station_m"] or panel.extent_m != record["extent_m"]):
      issues.append({"check": "fire-penetration-binding", "opening": identity})
      continue
    selection = penetration["selection"]
    center = selection["center_m"]
    across = center[1 - panel.axis]
    height = center[2]
    radius = selection["diameter_mm"] / 2000
    cut = Opening((across - radius, across + radius), (height - radius, height + radius))
    if not panel.contains(cut):
      issues.append({"check": "wall-opening-edge", "opening": identity})
      continue
    if any(_intersects(cut, other) for key, other in cuts[host].items() if key in door_cuts):
      issues.append({"check": "door-sleeve-overlap", "opening": identity, "host": host})
    cuts[host][identity] = cut
    service_cuts[identity] = {"host": host, "route": penetration["route"], "center_m": center,
                             "diameter_mm": selection["diameter_mm"], "system": selection["system"],
                             "opening": asdict(cut), "shape": "rectangular-coordination-envelope"}

  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Architecture").GetPrim())
  for group in ("Rooms", "Walls", "Doors", "Sleeves", "Shafts", "Stairs"):
    UsdGeom.Scope.Define(stage, f"/Architecture/{group}")
  for group, records, color in (("Rooms", model["rooms"], (.24, .48, .75)), ("Shafts", model["shafts"], (.26, .67, .5))):
    for identity, record in records.items():
      prim = _box(stage, f"/Architecture/{group}/{_prim_name(identity)}", record, color, .08)
      _annotate(prim, identity, record)
  wall_pieces = {}
  for identity, panel in panels.items():
    path = f"/Architecture/Walls/{_prim_name(identity)}"
    prim = UsdGeom.Scope.Define(stage, path).GetPrim()
    _annotate(prim, identity, model["walls"][identity])
    pieces = [asdict(piece) for piece in panel.solids(cuts[identity].values())]
    wall_pieces[identity] = len(pieces)
    for index, bounds in enumerate(pieces):
      _box(stage, f"{path}/Piece_{index}", bounds, (.7, .73, .77))
  for group, records, color in (("Doors", door_cuts, (.85, .51, .2)), ("Sleeves", service_cuts, (.23, .64, .49))):
    for identity, record in records.items():
      host = record["host"]
      prim = _outline(stage, f"/Architecture/{group}/{_prim_name(identity)}", panels[host], cuts[host][identity], color)
      _annotate(prim, identity, record)
  stair_checks = {}
  for identity, stair in model["stairs"].items():
    path = f"/Architecture/Stairs/{_prim_name(identity)}"
    _annotate(UsdGeom.Scope.Define(stage, path).GetPrim(), identity, stair)
    landings, checks = [], {}
    for story, geometry in stair["stories"].items():
      link = egress["links"][f"Stair-{identity}/{story}"]
      matches = isclose(geometry["travel_m"], link["length_m"], abs_tol=1e-8)
      if not matches:
        issues.append({"check": "stair-walking-line", "stair": identity, "story": story})
      checks[story] = {"steps": len(geometry["steps"]), "riser_m": geometry["riser_m"],
                       "same_lane_headroom_m": geometry["same_lane_headroom_m"],
                       "travel_m": geometry["travel_m"], "egress_distance_matches": matches}
      story_path = f"{path}/{_prim_name(story)}"
      UsdGeom.Scope.Define(stage, story_path)
      for index, bounds in enumerate(geometry["steps"]):
        _box(stage, f"{story_path}/Step_{index}", bounds, (.45, .5, .56))
      for bounds in geometry["landings"]:
        if bounds not in landings:
          landings.append(bounds)
    landings.extend(stair["discharge_landings"])
    for index, bounds in enumerate(landings):
      _box(stage, f"{path}/Landing_{index}", bounds, (.52, .57, .62))
    stair_checks[identity] = checks
  if not stage.GetRootLayer().Export(str(output)):
    raise OSError(f"Cannot export {output}.")
  return {**model, "status": "fail" if issues else "pass", "issues": issues,
          "wall_sleeves": service_cuts, "wall_solid_pieces": wall_pieces, "stair_checks": stair_checks,
          "counts": {**{key: len(model[key]) for key in ("rooms", "walls", "doors", "shafts", "stairs")},
                     "wall_sleeves": len(service_cuts)},
          "pending": ["exterior-envelope-and-equipment-room-walls", "handrails-and-structural-stair-connections",
                      "circular-sleeve-shop-details", "code-and-product-evidence"]}
