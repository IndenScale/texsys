"""Coordinate the actual foundation, bedding and anchor reservations below the yard."""

from itertools import combinations
from math import dist

from pxr import Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import space
from .cw_network import MODEL as CW, _pipe_prim, _segments
from .equipment_foundations import LAYOUT, MODEL, issue
from .facility_power import MODEL as POWER, _underground_water
from .fws_network import MODEL as FWS, pipe_envelope
from .power_routing import MODEL as CRITICAL
from .roof_transfer_delivery import SCENE as BASE_SCENE
from .site_grading import MODEL as TERRAIN
from .site_storm import MODEL as STORM
from .spatial_design import MODELS, artifact, model_report, number, scene_quantity, scene_tags, source_path, staged_json
from .telecom_coordination import _prim
from .wet_utilities import MODEL as WET

SCENE = "scenes/aidc-equipment-foundations-coordinated.usda"
SPECIALTY = "scenes/aidc-equipment-foundations.usda"


def _separation(a, b):
  gaps = [max(lo-hi, start-far, 0.) for lo,hi,start,far in
          zip(a.minimum_m,b.maximum_m,b.minimum_m,a.maximum_m,strict=True)]
  return dist(gaps,(0.,0.,0.))


def _occupied(row):
  return Space(row["bedding"]["minimum_m"],row["pad"]["maximum_m"])


def _utilities(context):
  machinery = staged_json(context,POWER)
  critical = staged_json(context,CRITICAL)
  water = staged_json(context,FWS)
  cw = staged_json(context,CW)
  storm = staged_json(context,STORM)
  wet = staged_json(context,WET)
  shapes = {key:space(row["envelope"]) for key,row in machinery["segments"].items()}
  shapes.update({key:space(row["envelope"]) for train in critical["trains"].values() for key,row in train["segments"].items()})
  shapes.update({key:pipe_envelope(row) for key,row in water["segments"].items()})
  shapes.update(_underground_water(context))
  shapes.update({f"{key}/{i}":volume for key,i,volume in _segments(cw)})
  structures = (("Detention",storm["detention"]),("OilCollection",staged_json(context,TERRAIN)["oil_yard"]["sealed_storage"]))
  for key,data in structures:
    x0,y0,x1,y1 = data["extent_m"]
    shapes[key] = Space((x0,y0,data["bottom_m"]),(x1,y1,data["ceiling_m"]))
  for row in wet["receivers"].values():
    shapes[row["id"]] = space(row)
  return shapes


def build_equipment_foundation_coordination(context):
  model = staged_json(context,MODEL)
  utilities = _utilities(context)
  frame = staged_json(context,f"{MODELS}/structure.json")
  structures = {f"{key}/{part['identity']}":Space(part["origin"],tuple(a+b for a,b in zip(
    part["origin"],part["size"],strict=True)))
    for key,row in frame["members"].items() if row["kind"]=="foundation" for part in row["parts"]}
  surfaces = {name:_occupied(row) for name,row in model["foundations"].items()}
  issues = list(model["issues"])
  clearance = number(model["assumptions"]["typical"]["utility_gap_m"],"below-grade utility separation")
  gaps = {}
  for name,shape in surfaces.items():
    for key,obstacle in (utilities|structures).items():
      distance = _separation(shape,obstacle)
      gaps[name] = min(distance,gaps.get(name,distance))
      if distance<clearance-1e-9:
        issue(issues,"TRANSVERSE_CLEARANCE",[name,key],f"{name} and {key}: clearance {distance:.3f} m.")
  joint = number(model["assumptions"]["typical"]["isolation_joint_m"],"minimum foundation isolation joint")
  nearest = None
  for a,b in combinations(surfaces,2):
    distance = _separation(surfaces[a],surfaces[b])
    nearest = distance if nearest is None else min(distance,nearest)
    if distance<joint-1e-9:
      issue(issues,"FOUNDATION_CLASH",[a,b],f"{a} and {b}: concrete isolation gap {distance:.3f} m.")
  return model_report(context,"equipment-foundation-coordination",{
    "geometry_digest":file_digest(context.build_path/MODEL),"utilities_checked":len(utilities),
    "other_foundation_parts":len(structures),"pads_checked":len(surfaces),"minimum_clearance_m":gaps,
    "minimum_pad_gap_m":nearest,"required_pad_gap_m":joint,
    "required_utility_gap_m":clearance,"issues":issues,
    "coverage":"Positive solid envelopes of concrete and bedding versus electrical banks, chilled and condenser water, "
               "site water and drainage, and existing pads. Threaded joint sleeves and construction tolerances require confirmation.",
  },issues)


def build_equipment_foundation_scene(context):
  model = staged_json(context,MODEL)
  layout = staged_json(context,LAYOUT)
  zones = staged_json(context,f"{MODELS}/levels.json")["zones"]
  source = source_path(context,"equipment-foundations.json").relative_to(context.root).as_posix()
  combined = Usd.Stage.Open(str(context.build_path/BASE_SCENE))
  if combined is None:
    raise ValueError("The coordinated yard scene could not be opened.")
  specialty = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(specialty,1)
  UsdGeom.SetStageUpAxis(specialty,UsdGeom.Tokens.z)
  specialty.SetDefaultPrim(UsdGeom.Xform.Define(specialty,"/AIDC").GetPrim())
  outputs = []
  for stage, output, identifier, is_specialty in (
      (specialty,SPECIALTY,"equipment-foundation-scene",True),
      (combined,SCENE,"equipment-foundation-coordinated-scene",False)):
    preview = dict(layout["source_map"]) if not is_specialty else {}
    for key,row in model["foundations"].items():
      path = "/AIDC/EquipmentFoundations/"+key.replace("-","_")
      owner = UsdGeom.Xform.Define(stage,path).GetPrim()
      scene_tags(owner,assembly=key,discipline="structure",zone=row["zone"],level=zones[row["zone"]]["level"])
      _prim(stage,path+"/Concrete",key,space(row["pad"]),(.62,.69,.72))
      concrete = stage.GetPrimAtPath(path+"/Concrete")
      scene_tags(concrete,equipment=row["equipment"],material="混凝土",role="基础")
      scene_quantity(concrete,"concreteVolume",row["concrete_m3"],"m³")
      _prim(stage,path+"/Bedding",key+"/bedding",space(row["bedding"]),(.72,.61,.43))
      scene_tags(stage.GetPrimAtPath(path+"/Bedding"),role="垫层")
      if is_specialty:
        _prim(stage,f"/AIDC/Equipment/{row['equipment'].replace('-','_')}",row["equipment"],
              space(row["equipment_bounds"]),(.82,.87,.89))
      for name,bolt in row["anchors"].items():
        _pipe_prim(stage,path+"/Anchors/"+name,key+"/"+name,bolt["points_m"],bolt["diameter_m"],(.79,.40,.19),sides=8)
      reinforcement = UsdGeom.Scope.Define(stage,path+"/Reinforcement").GetPrim()
      scene_tags(reinforcement,material="钢筋",role="配筋")
      for name,bar in row["bars"].items():
        a,b = (list(bar["center_m"]),list(bar["center_m"]))
        axis = bar["axis"]
        a[axis] -= bar["length_m"]/2
        b[axis] += bar["length_m"]/2
        bar_path = path+"/Reinforcement/"+name
        _pipe_prim(stage,bar_path,key+"/"+name,(a,b),bar["diameter_m"],(.30,.44,.55),sides=6)
        scene_quantity(stage.GetPrimAtPath(bar_path),"diameter",bar["diameter_m"]*1000,"mm")
        preview[key+"/"+name] = {"file":source,"path":f"families.{row['family']}"}
      preview[key] = {"file":source,"path":f"families.{row['family']}"}
    file = context.build_path/output
    if not stage.Flatten(False).Export(str(file)):
      raise OSError(f"Could not export {output}.")
    outputs.append(artifact(context,identifier,file,preview={"kind":"scene","source_map":preview}))
  return TargetResult("pass",tuple(outputs))
