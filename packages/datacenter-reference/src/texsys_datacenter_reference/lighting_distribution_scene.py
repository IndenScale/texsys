"""Place lighting cable carriers, the charging board and coordinated facade openings in USD."""

from pxr import Usd

from texsys.targets import TargetResult

from .architecture import MODEL as ARCH, space
from .emergency_lighting import MODEL as LIGHTING
from .emergency_lighting_scene import SCENE as FEDERATED, SPECIALTY as LIGHT_SCENE
from .lighting_distribution import MODEL
from .spatial_design import MODELS, artifact, source_path, staged_json
from .telecom_coordination import _cut_wall, _prim

SPECIALTY = "scenes/aidc-lighting-distribution.usda"
SCENE = "scenes/aidc-lighting-distribution-coordinated.usda"


def _cut_openings(stage, slots, architecture, source, identities):
  root=stage.GetPrimAtPath("/AIDC/Architecture")
  for name,row in sorted(slots.items()):
    wall=row["wall"]
    matches=[p for p in root.GetChildren() if p.GetAttribute("texsys:identity").Get()==wall]
    if len(matches)!=1:
      raise ValueError(f"{wall}: expected one installed facade solid for {name}.")
    prim_path=str(matches[0].GetPath())
    stage.RemovePrim(prim_path)
    for index,volume in enumerate(_cut_wall(space(architecture["walls"][wall]),space(row["bounds"]),0),1):
      _prim(stage,f"{prim_path}/Panel_{index}",wall,volume,(.67,.69,.67))
    identities[wall]={"file":source,"path":"layout"}


def build_lighting_distribution_scene(context):
  model=staged_json(context,MODEL)
  lighting=staged_json(context,LIGHTING)
  architecture=staged_json(context,ARCH)
  layout=staged_json(context,f"{MODELS}/layout.json")
  source=source_path(context,"lighting-distribution.json").relative_to(context.root).as_posix()
  fixture_source=source_path(context,"emergency-lighting.json").relative_to(context.root).as_posix()
  outputs=[]
  for previous,path,key,prior in ((LIGHT_SCENE,SPECIALTY,"lighting-distribution-scene",{}),
                                   (FEDERATED,SCENE,"lighting-distribution-coordinated-scene",layout["source_map"])):
    stage=Usd.Stage.Open(str(context.build_path/previous))
    if stage is None:
      raise ValueError(f"Cannot read the lighting scene {previous}.")
    identities=dict(prior)
    identities.update({key:{"file":fixture_source,"path":"mounting"} for key in lighting["fixtures"]})
    _cut_openings(stage,model["sleeves"],architecture,source,identities)
    board=model["cabinet"]
    root="/AIDC/LightingDistribution"
    _prim(stage,root+"/Board/Cabinet",board["id"],space(board["body"]),(.27,.39,.54))
    _prim(stage,root+"/Board/Curb",board["id"]+"/Curb",space(board["curb"]),(.58,.60,.61))
    identities[board["id"]]={"file":source,"path":"cabinet"}
    for name,row in model["carriers"].items():
      box=space(row["envelope"])
      group="Signal" if row["service"]=="signal" else "Charging"
      color=(.55,.38,.68) if group=="Signal" else (.83,.47,.23)
      _prim(stage,f"{root}/{group}/{name.replace('-','_')}",name,box,color)
      identities[name]={"file":source,"path":"layout"}
    for name,row in model["fittings"].items():
      _prim(stage,f"{root}/Fittings/{name.replace('-','_')}",name,space(row["bounds"]),(.65,.53,.39))
      identities[name]={"file":source,"path":"layout"}
    file=context.build_path/path
    if not stage.Flatten(False).Export(str(file)):
      raise OSError(f"Export failed for {file.name}.")
    outputs.append(artifact(context,key,file,preview={"kind":"scene","source_map":identities}))
  return TargetResult("pass",tuple(outputs))
