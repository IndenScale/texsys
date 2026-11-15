"""Render individually selectable steel tension rods in structure and site scenes."""

from pxr import Sdf, Usd

from texsys.targets import TargetResult

from .cw_network import _pipe_prim
from .facility_power_scene import SCENE as SITE_SCENE
from .lateral_bracing import MODEL
from .spatial_design import MODELS, artifact, source_path, staged_json

SPECIALTY = "scenes/aidc-braced-frame.usda"
SCENE = "scenes/aidc-lateral-coordinated.usda"


def build_lateral_scene(context):
  model = staged_json(context, MODEL)
  objects = staged_json(context, f"{MODELS}/layout.json")
  source = source_path(context, "lateral-bracing.json").relative_to(context.root).as_posix()
  outputs = []
  for original, destination, identity, prefix, preview in (
      ("scenes/aidc-structure.usda", SPECIALTY, "braced-frame-scene", "/Frame/Bracing", {}),
      (SITE_SCENE, SCENE, "lateral-coordinated-scene", "/AIDC/StructureBracing", dict(objects["source_map"]))):
    stage = Usd.Stage.Open(str(context.build_path / original))
    if stage is None:
      raise ValueError(f"Cannot open the structural scene input {original}.")
    diameter = model["assumptions"]["rod_diameter_m"]
    for bay, row in model["bays"].items():
      for name, rod in row["rods"].items():
        path = f"{prefix}/{bay.replace('-', '_')}/{name[-1]}"
        color = (.84, .45, .17) if name.endswith("-P") else (.65, .26, .15)
        _pipe_prim(stage, path, name, rod["points_m"], diameter, color)
        prim = stage.GetPrimAtPath(path)
        prim.CreateAttribute("texsys:collector", Sdf.ValueTypeNames.String).Set(row["collector"])
        prim.CreateAttribute("texsys:columns", Sdf.ValueTypeNames.StringArray).Set(row["columns"])
        preview[name] = {"file": source, "path": f"bays.{bay}"}
    output = context.build_path / destination
    if not stage.Flatten(False).Export(str(output)):
      raise OSError(f"Could not export {destination}.")
    outputs.append(artifact(context, identity, output, preview={"kind": "scene", "source_map": preview}))
  return TargetResult("pass", tuple(outputs))
