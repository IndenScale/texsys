"""Publish the developed site and the separately inspectable buried works."""

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import space
from .cw_network import _pipe_prim
from .roof_coordination import SCENE as BASE
from .site_grading import MODEL as GRADING
from .site_storm import MODEL
from .spatial_design import artifact, source_path, staged_json
from .telecom_coordination import _prim

SCENE = "scenes/aidc-site-coordinated.usda"
SUBSURFACE = "scenes/aidc-site-storm-underground.usda"
COLORS = {"roof": (.67, .66, .58), "hardstand": (.56, .60, .61), "landscape": (.55, .65, .51), "oil": (.70, .57, .42)}
DRAIN = (.24, .55, .70)
CONTAINMENT = (.66, .49, .34)
CONCRETE = (.57, .61, .62)


def _surface(stage, grade, preview, source):
  points = list(zip(grade["points_xy_m"], sum(grade["surface"]["proposed_m"], []), strict=True))
  for material, color in COLORS.items():
    faces = [row["vertices"] for row in grade["triangles"] if row["surface"] == material]
    indices = sorted({i for face in faces for i in face})
    if not indices:
      continue
    positions = {idx: i for i, idx in enumerate(indices)}
    mesh = UsdGeom.Mesh.Define(stage, f"/AIDC/Site/Formation/{material}")
    mesh.CreatePointsAttr([Gf.Vec3f(*points[index][0], points[index][1]) for index in indices])
    mesh.CreateFaceVertexCountsAttr([3]*len(faces))
    mesh.CreateFaceVertexIndicesAttr([positions[index] for face in faces for index in face])
    mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    identity = f"formation/{material}"
    mesh.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)
    preview[identity] = {"file": source, "path": "survey"}


def _box(stage, identity, group, coordinates, color):
  obj = Space(tuple(coordinates[:3]), tuple(coordinates[3:]))
  _prim(stage, group, identity, obj, color)


def _vault(stage, identity, path, data, color, *, cutaway):
  x0, y0, x1, y1 = data["extent_m"]
  bottom, ceiling = data["bottom_m"], data["ceiling_m"]
  wall = .20
  parts = {
    "Floor": [x0-wall, y0-wall, bottom-wall, x1+wall, y1+wall, bottom],
    "East": [x1, y0, bottom, x1+wall, y1, ceiling-wall],
    "West": [x0-wall, y0, bottom, x0, y1, ceiling-wall],
    "South": [x0-wall, y0-wall, bottom, x1+wall, y0, ceiling-wall],
    "North": [x0-wall, y1, bottom, x1+wall, y1+wall, ceiling-wall],
  }
  if not cutaway:
    parts["Cover"] = [x0-wall, y0-wall, ceiling-wall, x1+wall, y1+wall, ceiling]
  for key, box in parts.items():
    _box(stage, identity, f"{path}/{key}", box, color)


def _oil_enclosure(stage, oil, cutaway):
  _vault(stage, "OIL-SEALED", "/AIDC/Storm/OilVault", oil["sealed_storage"], CONTAINMENT, cutaway=cutaway)
  x, y = oil["collector_xy_m"]
  rim = oil["inlet_elevation_m"]
  ceiling = oil["sealed_storage"]["ceiling_m"]
  _pipe_prim(stage, "/AIDC/Storm/OilReceiver", "OIL-I", [(x, y, rim), (x, y, ceiling)], .22, CONTAINMENT, sides=8)
  if cutaway:
    return
  x0, y0, x1, y1 = oil["extent_m"]
  bottom, height = oil["perimeter_formation_m"], oil["containment_crest_m"]
  t = .10
  pieces = (
    (x0, y0, bottom, x1, y0+t, height), (x0, y1-t, bottom, x1, y1, height),
    (x0, y0+t, bottom, x0+t, y1-t, height), (x1-t, y0+t, bottom, x1, y1-t, height),
  )
  for index, box in enumerate(pieces, 1):
    _box(stage, "OIL-BUND", f"/AIDC/Site/OilBund/Bund{index}", box, CONTAINMENT)


def _draw_system(stage, model, grade, preview, storm_source, grade_source, cutaway):
  for key, row in model["runs"].items():
    tag = key.replace("-", "_")
    _pipe_prim(stage, f"/AIDC/Storm/Pipes/{tag}", key, row["points_m"], row["pipe"]["outside_m"], DRAIN, sides=12)
    preview[key] = {"file": storm_source, "path": f"runs.{key}"}
  for key, row in model["roof_shoes"].items():
    _pipe_prim(stage, f"/AIDC/Storm/Shoes/{key.replace('-', '_')}", f"{key}/shoe", row["points_m"],
               row["outside_m"], DRAIN, sides=12)
    preview[f"{key}/shoe"] = {"file": storm_source, "path": f"inlets.{key}"}
  for key, row in model["nodes"].items():
    if "access" not in row:
      continue
    shape = space(row["access"])
    _prim(stage, f"/AIDC/Storm/Chambers/{key.replace('-', '_')}", key, shape, CONCRETE)
    if row["kind"] == "roof-and-surface-receiver" and not cutaway:
      x0, y0 = shape.minimum_m[:2]
      x1, y1, rim = shape.maximum_m
      _box(stage, f"{key}/grate", f"/AIDC/Storm/Grates/{key.replace('-', '_')}",
           [x0, y0, rim-.007, x1, y1, rim+.020], DRAIN)
      preview[f"{key}/grate"] = {"file": storm_source, "path": f"inlets.{key}"}
    preview[key] = {"file": storm_source, "path": f"inlets.{key}" if key in model["roof_shoes"] else "junctions"}
  tank = model["detention"]
  _vault(stage, "DETENTION", "/AIDC/Storm/Detention", tank, CONCRETE, cutaway=cutaway)
  _pipe_prim(stage, "/AIDC/Storm/FlowControl", "DETENTION/ORIFICE", tank["orifice_points_m"], tank["outlet_diameter_m"], DRAIN, sides=12)
  preview["DETENTION"] = {"file": storm_source, "path": "detention"}
  preview["DETENTION/ORIFICE"] = {"file": storm_source, "path": "detention.outlet_diameter_m"}
  _oil_enclosure(stage, grade["oil_yard"], cutaway)
  for key in ("OIL-SEALED", "OIL-I", "OIL-BUND"):
    preview[key] = {"file": grade_source, "path": "oil_yard"}


def build_site_storm_scene(context):
  terrain, storm = [staged_json(context, name) for name in (GRADING, MODEL)]
  grade_src, storm_src = [source_path(context, name).relative_to(context.root).as_posix()
                         for name in ("site-grading.json", "site-storm.json")]
  combined = Usd.Stage.Open(str(context.build_path / BASE))
  if combined is None:
    raise ValueError("Could not open the coordinated roof stage.")
  underground = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageUpAxis(underground, UsdGeom.Tokens.z)
  UsdGeom.SetStageMetersPerUnit(underground, 1)
  underground.SetDefaultPrim(UsdGeom.Xform.Define(underground, "/AIDC").GetPrim())
  results = []
  for stage, destination, identity, section in ((combined, SCENE, "site-coordinated-scene", False),
                                                 (underground, SUBSURFACE, "storm-underground-scene", True)):
    preview = {}
    if not section:
      _surface(stage, terrain, preview, grade_src)
    _draw_system(stage, storm, terrain, preview, storm_src, grade_src, section)
    file = context.build_path / destination
    if not stage.Flatten(False).Export(str(file)):
      raise OSError(f"Could not export {file.name}.")
    results.append(artifact(context, identity, file, preview={"kind": "scene", "source_map": preview}))
  return TargetResult("pass", tuple(results))
