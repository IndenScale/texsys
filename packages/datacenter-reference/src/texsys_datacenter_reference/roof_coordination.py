"""Verify installed roof volumes and render physical cuts in two OpenUSD scenes."""

from itertools import pairwise

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCHITECTURE, space
from .cw_network import _pipe_prim
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _obstacles, _system_objects
from .power_routing import LAYOUT, MODEL as POWER, SCENE as FEDERATED
from .roof import LAYERS, MODEL, _issue, _tiles, roof_surfaces
from .spatial_design import MODELS, artifact, model_report, number, source_path, staged_json

MODEL_CHECK = f"{MODELS}/roof-coordination.json"
SCENE = "scenes/aidc-roof-coordinated.usda"
SPECIALTY = "scenes/aidc-roof-assembly.usda"
STRUCTURE_SCENE = "scenes/aidc-structure.usda"

BACKGROUND = {"deck": (.54, .61, .68), "insulation": (.82, .74, .55), "membrane": (.68, .73, .75)}


def _extrusion(rect, low, high):
  x0, y0, x1, y1 = rect
  return Space((x0, y0, low), (x1, y1, high))


def _flashing_parts(row):
  return _tiles(row["curb_xy_m"], [row["hole_xy_m"]])


def _drain_volumes(config, row):
  half = config["gutter_width_m"]/2
  depth = config["gutter_depth_m"]
  for index, (a, b) in enumerate(pairwise(row["gutter_invert_m"]), 1):
    yield f"gutter-{index}", Space((min(a[0], b[0]), a[1]-half, min(a[2], b[2])),
                                   (max(a[0], b[0]), a[1]+half, max(a[2], b[2])+depth))
  x, y, low = row["handoff_m"]
  radius = number(config["leader_outside_diameter_m"], "rainwater leader outside diameter")/2
  yield "leader", Space((x-radius, y-radius, low), (x+radius, y+radius, row["leader_top_m"][2]))


def _installed(roof):
  for key, row in roof["panels"].items():
    for index, rect in enumerate(row["tiles_xy_m"], 1):
      top = max(roof_surfaces(roof, y)[-1] for y in (rect[1], rect[3]))
      yield f"{key}/{index}", _extrusion(rect, roof["section"]["deck_base_m"], top)
  for key, row in roof["purlins"].items():
    yield key, space(row)
  for key, row in roof["wall_heads"].items():
    for index, rect in enumerate(row["tiles_xy_m"], 1):
      yield f"{key}/head-{index}", _extrusion(rect, *row["z_m"])
  for key, row in roof["openings"].items():
    for index, rect in enumerate(_flashing_parts(row), 1):
      yield f"{key}/flashing-{index}", _extrusion(rect, row["curb_base_m"], row["curb_top_m"])
  for key, row in roof["drainage"].items():
    for label, volume in _drain_volumes(roof["assumptions"]["drainage"], row):
      yield f"{key}/{label}", volume


def build_roof_coordination(context):
  roof = staged_json(context, MODEL)
  architecture = staged_json(context, ARCHITECTURE)
  power = staged_json(context, POWER)
  obstacles = _obstacles(context, architecture)
  obstacles.update({name: space(row["envelope"]) for train in power["trains"].values()
                    for name, row in train["segments"].items()})
  fire, _ = _system_objects(staged_json(context, FIRE))
  obstacles.update({name: shape for name, (shape, _) in fire.items()})
  yard = staged_json(context, f"{MODELS}/site.json")["buildable_m"]
  issues = []
  installed = list(_installed(roof))
  for identity, volume in installed:
    if (volume.minimum_m[0] < yard[0]-1e-9 or volume.minimum_m[1] < yard[1]-1e-9 or
        volume.maximum_m[0] > yard[2]+1e-9 or volume.maximum_m[1] > yard[3]+1e-9):
      _issue(issues, "SITE_CLEARANCE", [identity], f"{identity} leaves the available site envelope.")
    for name, shape in obstacles.items():
      if volume.intersects(shape):
        _issue(issues, "SERVICE_CLASH", [identity, name], f"{identity} and {name} occupy the same volume.")
  return model_report(context, "roof-coordination", {
    "roof_digest": file_digest(context.build_path / MODEL), "installed_envelope_count": len(installed),
    "obstacle_count": len(obstacles), "checked_openings": len(roof["openings"]), "issues": issues,
    "coverage": "Positive-volume installation checks for cut roof panels, secondary steel, flashing, gutters and "
                "downpipes against staged hall obstacles and the buildable envelope. Curbs represent installation "
                "envelopes. Panel slopes are bounded conservatively across each cut tile.",
  }, issues)


def _mesh(stage, path, identity, prisms, color):
  points, indices, positions = [], [], {}
  faces = ((3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
  for rect, elevations in prisms:
    x0, y0, x1, y1 = rect
    xy = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    corners = [(*point, elevations(*point, upper)) for upper in (False, True) for point in xy]
    for face in faces:
      for index in face:
        point = corners[index]
        if point not in positions:
          positions[point] = len(points)
          points.append(Gf.Vec3f(*point))
        indices.append(positions[point])
  mesh = UsdGeom.Mesh.Define(stage, path)
  mesh.CreatePointsAttr(points)
  mesh.CreateFaceVertexCountsAttr([4]*(len(indices)//4))
  mesh.CreateFaceVertexIndicesAttr(indices)
  mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
  mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  mesh.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)


def _scope_path(group, key):
  return "/AIDC/Roof/" + group + "/" + key.replace("-", "_")


def _draw_panels(stage, assembly, preview, source):
  for key, row in assembly["panels"].items():
    for index, layer in enumerate(LAYERS):
      def elev(_x, y, upper):
        return roof_surfaces(assembly, y)[index+int(upper)]
      identity = f"{key}/{layer}"
      path = _scope_path("Panels", key) + "/" + layer
      _mesh(stage, path, identity, [(rect, elev) for rect in row["tiles_xy_m"]], BACKGROUND[layer])
      preview[identity] = {"file": source, "path": f"layers.{layer}"}


def _draw_openings(stage, assembly, preview, context):
  for key, row in assembly["openings"].items():
    def elev(_x, y, upper):
      return row["curb_top_m"] if upper else roof_surfaces(assembly, y)[-1]-.012
    color = (.77, .57, .27) if row["service"] == "power" else (.30, .59, .65)
    _mesh(stage, _scope_path("Curbs", key), key, [(rect, elev) for rect in _flashing_parts(row)], color)
    row_src = "fws-network.json" if row["service"] == "FWS" else "system.sdl.json"
    item = f"connections/{row['sdl_links'][0]}" if row["service"] == "power" else "opening_allowance_m"
    preview[key] = {"file": source_path(context, row_src).relative_to(context.root).as_posix(), "path": item}


def _draw_wall_heads(stage, assembly, preview, source):
  for key, row in assembly["wall_heads"].items():
    def elev(_x, _y, upper):
      return row["z_m"][int(upper)]

    identity = f"{key}/head"
    _mesh(stage, _scope_path("WallHeads", key), identity, [(rect, elev) for rect in row["tiles_xy_m"]], (.69, .66, .59))
    preview[identity] = {"file": source, "path": "overhang_m"}


def _draw_rain_system(stage, assembly, preview, source):
  config = assembly["assumptions"]["drainage"]
  color = (.28, .42, .52)
  material = 0.012
  for key, row in assembly["drainage"].items():
    group = _scope_path("Rainwater", key)
    for (a, b), (label, envelope) in zip(pairwise(row["gutter_invert_m"]), _drain_volumes(config, row)):
      x0, y0, _ = envelope.minimum_m
      x1, y1, _ = envelope.maximum_m

      def elevation(x):
        return a[2]+(b[2]-a[2])*(x-a[0])/(b[0]-a[0])

      def bed(x, _y, upper):
        return elevation(x) + (material if upper else 0)

      def side(x, _y, upper):
        return elevation(x) + (config["gutter_depth_m"] if upper else material)

      _mesh(stage, f"{group}/{label.replace('-', '_')}", f"{key}/{label}",
            [([x0, y0, x1, y1], bed), ([x0, y0, x1, y0+material], side),
             ([x0, y1-material, x1, y1], side)], color)
      preview[f"{key}/{label}"] = {"file": source, "path": "drainage"}
    diameter = config["leader_outside_diameter_m"]
    x, y, bottom = row["handoff_m"]
    top = row["leader_top_m"][2]
    _pipe_prim(stage, f"{group}/Leader", key, [(x, y, bottom), (x, y, top)], diameter, color, sides=12)
    preview[key] = {"file": source, "path": "drainage"}


def _draw_purlins(stage, assembly, preview, source):
  t = assembly["assumptions"]["purlins"]["wall_m"]
  for key, row in assembly["purlins"].items():
    x0, y0, z0 = row["minimum_m"]
    x1, y1, z1 = row["maximum_m"]
    def bottom(_x, _y, upper):
      return z0+t if upper else z0

    def top(_x, _y, upper):
      return z1 if upper else z1-t

    def web(_x, _y, upper):
      return z1-t if upper else z0+t

    _mesh(stage, _scope_path("Purlins", key), key, [
      ([x0, y0, x1, y1], bottom), ([x0, y0, x1, y1], top),
      ([x0, y0, x0+t, y1], web), ([x1-t, y0, x1, y1], web),
    ], (.40, .47, .54))
    preview[key] = {"file": source, "path": "purlins"}


def build_roof_scene(context):
  assembly, layout = staged_json(context, MODEL), staged_json(context, LAYOUT)
  source = source_path(context, "roof.json").relative_to(context.root).as_posix()
  result = []
  for original, destination, identity in ((FEDERATED, SCENE, "roof-coordinated-scene"),
                                          (STRUCTURE_SCENE, SPECIALTY, "roof-specialty-scene")):
    stage = Usd.Stage.Open(str(context.build_path / original))
    if stage is None:
      raise ValueError(f"Cannot open {original}.")
    if original == FEDERATED:
      # Closed-roof overview keeps the exterior installation legs visible.
      # The enclosed pod tubing remains available in its working scene.
      if stage.GetPrimAtPath("/AIDC/TCS"):
        UsdGeom.Scope.Define(stage, "/AIDC/TCS").MakeInvisible()
      # The electrical working scene retains the flat datum markers. This composed
      # scene shows the finished weather-plane curbs in their place.
      for key, row in assembly["openings"].items():
        if row["service"] == "power":
          stage.RemovePrim(f"/AIDC/PowerRoutes/{key[0]}/{key.replace('-', '_')}_Building")
    preview = dict(layout["source_map"]) if original == FEDERATED else {}
    _draw_panels(stage, assembly, preview, source)
    _draw_wall_heads(stage, assembly, preview, source)
    _draw_openings(stage, assembly, preview, context)
    _draw_rain_system(stage, assembly, preview, source)
    _draw_purlins(stage, assembly, preview, source)
    file = context.build_path / destination
    if not stage.Flatten(False).Export(str(file)):
      raise OSError(f"Could not export {file.name}.")
    result.append(artifact(context, identity, file, preview={"kind": "scene", "source_map": preview}))
  return TargetResult("pass", tuple(result))
