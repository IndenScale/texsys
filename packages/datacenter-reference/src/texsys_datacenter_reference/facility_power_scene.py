"""Publish the plant electrical installation with and without adjacent disciplines."""

from pxr import Sdf, Usd, UsdGeom

from texsys.targets import TargetResult

from .architecture import space
from .facility_power import MODEL
from .power_routing import LAYOUT
from .site_storm_scene import SCENE as BASE
from .spatial_design import artifact, source_path, staged_json
from .telecom_coordination import _prim

SCENE = "scenes/aidc-facility-power-coordinated.usda"
SPECIALTY = "scenes/aidc-facility-power.usda"
COLORS = {"A": (.83, .53, .24), "B": (.33, .54, .78), "LOCAL": (.34, .62, .51), "CMD": (.55, .49, .61)}


def build_facility_power_scene(context):
  model, layout = [staged_json(context, path) for path in (MODEL, LAYOUT)]
  combined = Usd.Stage.Open(str(context.build_path / BASE))
  if combined is None:
    raise ValueError("Cannot open the coordinated site reference.")
  solitary = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageUpAxis(solitary, UsdGeom.Tokens.z)
  UsdGeom.SetStageMetersPerUnit(solitary, 1.)
  solitary.SetDefaultPrim(UsdGeom.Xform.Define(solitary, "/AIDC").GetPrim())
  equipment = {row[field].split(".")[0] for row in model["circuits"].values() for field in ("from", "to")}
  for name in sorted(equipment):
    row = layout["objects"][name]
    _prim(solitary, f"/AIDC/Equipment/{name}", row["semantic_ref"], space(row), (.53, .57, .59))
  source = source_path(context, "facility-power.json").relative_to(context.root).as_posix()
  sdl = source_path(context, "system.sdl.json").relative_to(context.root).as_posix()
  outputs = []
  for stage, name, identity in ((combined, SCENE, "facility-power-coordinated-scene"),
                                (solitary, SPECIALTY, "facility-power-specialty-scene")):
    preview = dict(layout["source_map"])
    for key, row in model["segments"].items():
      role = key.split("-")[0]
      path = f"/AIDC/FacilityPower/{role}/{key.replace('-', '_')}"
      _prim(stage, path, key, space(row["envelope"]), COLORS[role])
      stage.GetPrimAtPath(path).CreateAttribute("texsys:sdlLinks", Sdf.ValueTypeNames.StringArray).Set(row["links"])
      preview[key] = ({"file": sdl, "path": f"connections/{row['links'][0]}"} if len(row["links"]) == 1 else
                      {"file": source, "path": f"trains.{role}"})
    file = context.build_path/name
    if not stage.Flatten(False).Export(str(file)):
      raise OSError(f"Could not export {file.name}.")
    outputs.append(artifact(context, identity, file, preview={"kind": "scene", "source_map": preview}))
  return TargetResult("pass", tuple(outputs))
