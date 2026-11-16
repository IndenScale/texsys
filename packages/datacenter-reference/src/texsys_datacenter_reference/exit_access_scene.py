"""Publish the ramp hardware and site egress surfaces in OpenUSD."""

from pxr import Gf, Sdf, Usd, UsdGeom
from math import hypot

from texsys.targets import TargetResult

from .architecture import ARCH_SCENE
from .cw_network import _pipe_prim
from .equipment_foundation_coordination import SCENE as FEDERATED
from .exit_access import MODEL
from .exit_access_checks import _flat
from .spatial_design import MODELS, artifact, source_path, staged_json

SCENE = "scenes/aidc-exit-access-coordinated.usda"
SPECIALTY = "scenes/aidc-exit-access.usda"


def _mesh(stage, path, key, top, depth, color):
  mesh = UsdGeom.Mesh.Define(stage,path)
  points = [*top,*[(x,y,z-depth) for x,y,z in top]]
  indices = [0,1,2,3, 7,6,5,4, 0,4,5,1, 1,5,6,2, 2,6,7,3, 3,7,4,0]
  mesh.CreatePointsAttr([Gf.Vec3f(*point) for point in points])
  mesh.CreateFaceVertexCountsAttr([4]*6)
  mesh.CreateFaceVertexIndicesAttr(indices)
  mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
  mesh.CreateDoubleSidedAttr(True)
  mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  mesh.GetPrim().CreateAttribute("texsys:identity",Sdf.ValueTypeNames.String).Set(key)


def _ramps(stage, model, source, preview):
  obsolete = {identity for key in model["ramps"] for identity in (f"RAMP-{key}",key,f"{key}-1",f"{key}-2")}
  for prim in stage.GetPrimAtPath("/AIDC/Architecture").GetChildren():
    if prim.GetAttribute("texsys:identity").Get() in obsolete:
      UsdGeom.Imageable(prim).MakeInvisible()
  for name,row in model["ramps"].items():
    path = "/AIDC/ExitAccess/Ramps/"+name.replace("-","_")
    for tag,leaf in row["leaves"].items():
      a = leaf["hinge_xy_m"]
      b = leaf["arc_xy_m"][-1 if row["available"] else 0]
      dx,dy = (v-u for u,v in zip(a,b,strict=True))
      span = hypot(dx,dy)
      offset = row["leaf_thickness_m"]/2
      z = row["inside_m"][2]+row["leaf_height_m"]
      ex,ey = -offset*dy/span,offset*dx/span
      plane = [(a[0]-ex,a[1]-ey,z),(b[0]-ex,b[1]-ey,z),(b[0]+ex,b[1]+ey,z),(a[0]+ex,a[1]+ey,z)]
      key = f"EA-{name}-{tag}"
      _mesh(stage,path+f"/Leaf_{tag}",key,plane,row["leaf_height_m"],(.35,.51,.43) if row["available"] else (.71,.29,.21))
      preview[key] = {"file":source,"path":"door"}
    for i,slab in enumerate(row["slab"],1):
      a,b,c,d = slab["xy_m"]
      west,east = slab["west_z_m"],slab["east_z_m"]
      points = [(a,b,west),(c,b,east),(c,d,east),(a,d,west)]
      key = f"EA-{name}-SLAB-{i}"
      _mesh(stage,path+f"/Slab_{i}",key,points,slab["depth_m"],(.64,.67,.67))
      preview[key] = {"file":source,"path":"ramp"}
    for side,curbs in row["curbs"].items():
      for i,curb in enumerate(curbs,1):
        a,b,c,d = curb["xy_m"]
        left,right = curb["west_z_m"],curb["east_z_m"]
        top = [(a,b,left),(c,b,right),(c,d,right),(a,d,left)]
        _mesh(stage,path+f"/Curb_{side}_{i}",f"EA-{name}-CURB-{side}-{i}",top,curb["depth_m"],(.74,.73,.68))
    for key,rail in (row["rails"]|row["posts"]).items():
      identifier = f"EA-{name}-{key}"
      _pipe_prim(stage,path+"/"+key.replace("-","_"),identifier,rail["points_m"],rail["diameter_m"],
                 (.27,.37,.42) if rail["role"]=="post" else (.69,.47,.23),sides=10)
      preview[identifier] = {"file":source,"path":"ramp"}


def _pavements(stage,model,source,preview):
  base = "/AIDC/ExitAccess/Site"
  color = (.50,.68,.56)
  for key,row in model["walkways"].items():
    _mesh(stage,base+"/"+key.replace("-","_"),key,row["corners_m"],row["thickness_m"],color)
    side = key.split("-")[0].lower()
    preview[key] = {"file":source,"path":f"sides.{side}"}
  for name,row in model["junctions"].items():
    x,y,z = row["center_m"]
    half = row["width_m"]/2
    top = _flat((x-half,y-half,x+half,y+half),z)
    _mesh(stage,base+f"/Junction_{name}",f"{name}-turn",top,row["thickness_m"],color)
  for key,row in model["meeting_areas"].items():
    top = _flat(row["extent_m"],row["elevation_m"])
    _mesh(stage,base+"/"+key.replace("-","_"),key,top,model["assumptions"]["paving"]["thickness_m"],(.62,.75,.65))
    preview[key] = {"file":source,"path":f"meeting_areas.{key}"}


def build_exit_access_scene(context):
  model = staged_json(context,MODEL)
  layout = staged_json(context,f"{MODELS}/layout.json")
  source = source_path(context,"exit-access.json").relative_to(context.root).as_posix()
  outputs = []
  for original,target,identity,prior in ((ARCH_SCENE,SPECIALTY,"exit-access-scene",{}),
                                         (FEDERATED,SCENE,"exit-access-coordinated-scene",layout["source_map"])):
    stage = Usd.Stage.Open(str(context.build_path/original))
    if stage is None:
      raise ValueError(f"Could not open {original}.")
    preview = dict(prior)
    _ramps(stage,model,source,preview)
    _pavements(stage,model,source,preview)
    file = context.build_path/target
    if not stage.Flatten(False).Export(str(file)):
      raise OSError(f"Could not export {target}.")
    outputs.append(artifact(context,identity,file,preview={"kind":"scene","source_map":preview}))
  return TargetResult("pass",tuple(outputs))
