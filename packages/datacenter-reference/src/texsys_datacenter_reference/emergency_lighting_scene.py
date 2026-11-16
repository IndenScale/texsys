"""Publish emergency fixtures alongside the building and the full site model."""

from pxr import Usd

from texsys.targets import TargetResult

from .emergency_lighting import MODEL
from .emergency_lighting_study import shapes
from .exit_access_scene import SCENE as COORDINATED
from .exit_access_scene import SPECIALTY as ACCESS
from .spatial_design import MODELS, artifact, source_path, staged_json
from .telecom_coordination import _prim

SPECIALTY = "scenes/aidc-emergency-lighting.usda"
SCENE = "scenes/aidc-emergency-lighting-coordinated.usda"

_COLORS = {"A": (.91, .61, .24), "B": (.21, .55, .70), "sign": (.22, .62, .38),
           "Pedestal": (.55, .56, .58), "Post": (.37, .43, .45), "Arm": (.48, .50, .48)}


def build_emergency_lighting_scene(context):
  model = staged_json(context, MODEL)
  layout = staged_json(context,f"{MODELS}/layout.json")
  source = source_path(context,"emergency-lighting.json").relative_to(context.root).as_posix()
  files = []
  for previous, name, identity, base_map in (
    (ACCESS, SPECIALTY, "emergency-lighting-scene", {}),
    (COORDINATED, SCENE, "emergency-lighting-coordinated-scene", layout["source_map"]),
  ):
    stage = Usd.Stage.Open(str(context.build_path/previous))
    if stage is None:
      raise ValueError(f"{previous}: candidate exit geometry could not be read.")
    source_map = dict(base_map)
    for key, row in sorted(model["fixtures"].items()):
      for part, volume in shapes(row).items():
        semantic_ref = key if part == "Body" else f"{key}/{part}"
        path = "/AIDC/EmergencyLighting/"+key.replace("-","_")+"/"+part
        color = _COLORS["sign" if row["sign"] else row["group"]] if part == "Body" else _COLORS[part]
        _prim(stage,path,semantic_ref,volume,color)
        source_map[semantic_ref] = {"file":source, "path":"mounting"}
    output = context.build_path/name
    if not stage.Flatten(False).Export(str(output)):
      raise OSError(f"{output.name}: scene export failed.")
    files.append(artifact(context,identity,output,preview={"kind":"scene","source_map":source_map}))
  return TargetResult("pass",tuple(files))
