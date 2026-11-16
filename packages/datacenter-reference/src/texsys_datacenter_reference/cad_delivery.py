"""Export editable, source-linked engineering geometry in native CAD entities."""

from collections import Counter
from itertools import pairwise

from texsys.evidence import file_digest, write_json
from texsys.targets import TargetResult

from .building_views import _door_leaves
from .plans import _xy
from .spatial_design import MODELS, artifact, staged_json
from .structure_views import _part_bounds

APP_ID = "TEXSYS_AIDC"
LAYERS = {
  "G-SITE-PARCEL": (7,35,"CONTINUOUS"),
  "G-SITE-SETBACK": (8,18,"AIDC-DASHED"),
  "G-GRID-AXIS": (8,13,"AIDC-AXIS"),
  "G-ZONE": (4,13,"AIDC-DASHED"),
  "G-DIMENSION": (7,13,"CONTINUOUS"),
  "G-TEXT": (7,13,"CONTINUOUS"),
  "A-WALL": (7,35,"CONTINUOUS"),
  "A-DOOR": (3,25,"CONTINUOUS"),
  "A-ROOM": (8,13,"AIDC-DASHED"),
  "A-RAMP": (8,18,"CONTINUOUS"),
  "A-IT-POD": (5,18,"CONTINUOUS"),
  "S-COLUMN": (7,35,"CONTINUOUS"),
  "S-GIRDER": (5,25,"CONTINUOUS"),
  "S-TIE": (4,18,"CONTINUOUS"),
  "S-FOUNDATION": (7,35,"CONTINUOUS"),
  "S-PLINTH": (8,18,"CONTINUOUS"),
  "M-FWS-SUPPLY": (5,30,"CONTINUOUS"),
  "M-FWS-RETURN": (1,30,"AIDC-DASHED"),
  "M-FWS-OPENING": (6,25,"CONTINUOUS"),
  "E-GEN-RESERVE": (4,18,"AIDC-DASHED"),
  "E-GEN-SKID": (5,30,"CONTINUOUS"),
  "M-GEN-RADIATOR": (3,25,"CONTINUOUS"),
  "S-GEN-PAD": (8,25,"CONTINUOUS"),
  "M-FUEL-EQUIP": (30,25,"CONTINUOUS"),
  "M-FUEL-PIPE": (32,30,"CONTINUOUS"),
  "M-FUEL-RETURN": (6,25,"AIDC-DASHED"),
  "C-OIL-BUND": (32,35,"CONTINUOUS"),
  "C-OIL-SEALED": (33,30,"AIDC-DASHED"),
}


def _mm(values):
  return tuple(round(value*1000,6) for value in values)


class CadPlan:
  def __init__(self, document, inputs):
    self.doc = document
    self.model = document.modelspace()
    self.inputs = inputs
    self.entities = {}
    document.appids.new(APP_ID)
    document.linetypes.new("AIDC-DASHED",dxfattribs={"description":"600 mm dash", "pattern":[900,600,-300]})
    document.linetypes.new("AIDC-AXIS",dxfattribs={"description":"Project grid", "pattern":[2000,1300,-250,200,-250]})
    document.dimstyles.new("AIDC-MM",dxfattribs={"dimtxt":220,"dimasz":120,"dimexo":100,"dimexe":140,
                                                 "dimdec":0,"dimtad":1})

  def _attrib(self, layer):
    if layer not in self.doc.layers:
      color,weight,linetype = LAYERS[layer]
      self.doc.layers.new(layer,dxfattribs={"color":color,"lineweight":weight,"linetype":linetype})
    return {"layer":layer}

  def _mark(self, entity, name, model, *path):
    model_path = f"{MODELS}/{model}.json"
    pointer = "/" + "/".join(str(part).replace("~","~0").replace("/","~1") for part in path)
    entity.set_xdata(APP_ID,[(1000,name),(1000,model_path),(1000,pointer)])
    self.entities[entity.dxf.handle] = {"id":name,"model":model_path,"pointer":pointer,
                                       "layer":entity.dxf.layer,"type":entity.dxftype()}

  def box(self, layer, extent, name, model, *path):
    x,y,u,v = extent
    points = [_mm(p) for p in ((x,y),(u,y),(u,v),(x,v))]
    self._mark(self.model.add_lwpolyline(points,close=True,dxfattribs=self._attrib(layer)),name,model,*path)

  def line(self, layer, a, b, name, model, *path):
    self._mark(self.model.add_line(_mm(a),_mm(b),dxfattribs=self._attrib(layer)),name,model,*path)

  def arc(self, layer, at, radius, start, end, name, model, *path):
    if (end-start)%360>180:
      start,end = end,start
    entity = self.model.add_arc(_mm(at),radius*1000,start,end,dxfattribs=self._attrib(layer))
    self._mark(entity,name,model,*path)

  def route(self, layer, points, name, model, *path):
    self._mark(self.model.add_polyline3d([_mm(p) for p in points],dxfattribs=self._attrib(layer)),name,model,*path)

  def text(self, at, title, size, name, model, *path):
    style = {**self._attrib("G-TEXT"),"insert":_mm(at),"height":size*1000}
    self._mark(self.model.add_text(title,dxfattribs=style),name,model,*path)

  def dim(self, at, a, b, angle, name, model, *path):
    dim = self.model.add_linear_dim(base=_mm(at),p1=_mm(a),p2=_mm(b),angle=angle,dimstyle="AIDC-MM",
                                    dxfattribs=self._attrib("G-DIMENSION"))
    dim.render()
    self._mark(dim.dimension,name,model,*path)


def _grid(cad, margin=1.8):
  grid = cad.inputs["grid"]
  xs,ys = tuple(grid["x"].values()),tuple(grid["y"].values())
  left,right,bottom,top = min(xs)-margin,max(xs)+margin,min(ys)-margin,max(ys)+margin
  for key,x in grid["x"].items():
    cad.line("G-GRID-AXIS",(x,bottom),(x,top),f"X-{key}","grid","x",key)
    cad.text((x-.12,bottom-.38),key,.30,f"X-{key}","grid","x",key)
  for key,y in grid["y"].items():
    cad.line("G-GRID-AXIS",(left,y),(right,y),f"Y-{key}","grid","y",key)
    cad.text((left-.48,y-.14),key,.30,f"Y-{key}","grid","y",key)
  for direction,values in (("x",grid["x"]),("y",grid["y"])):
    ordered = sorted(values.items(),key=lambda p:p[1])
    for (a,start),(b,end) in pairwise(ordered):
      if direction=="x":
        dim = ((start,bottom-1),(start,bottom),(end,bottom),0)
      else:
        dim = ((left-1,start),(left,start),(left,end),90)
      cad.dim(*dim,f"{direction.upper()}-{a}-{b}","grid",direction)


def _site(cad):
  site = cad.inputs["site"]
  cad.box("G-SITE-PARCEL",site["boundary_m"],site["parcel"],"site","boundary_m")
  cad.box("G-SITE-SETBACK",site["buildable_m"],site["parcel"],"site","buildable_m")
  x,y,u,v = site["boundary_m"]
  cad.dim((x,y-2),(x,y),(u,y),0,site["parcel"],"site","boundary_m")
  cad.dim((x-2,y),(x,y),(x,v),90,site["parcel"],"site","boundary_m")
  cad.line("G-SITE-PARCEL",(u+1.5,v-3),(u+1.5,v-1),site["parcel"],"site","north")
  cad.text((u+1.4,v-.75),"N",.45,site["parcel"],"site","north")
  _grid(cad)
  for key,row in cad.inputs["levels"]["zones"].items():
    x,y,u,v = _xy(row)
    cad.box("G-ZONE",(x,y,u,v),key,"levels","zones",key)
    cad.text((x+.4,v+.45),f"{key}   {row['level']} {row['minimum_m'][2]:+.3f} m",.40,key,"levels","zones",key)


def _architecture(cad):
  _grid(cad)
  model = cad.inputs["architecture"]
  for group,layer in (("walls","A-WALL"),("rooms","A-ROOM")):
    for key,row in model[group].items():
      if key.startswith("W-over-"):
        continue
      shape = _xy(row)
      cad.box(layer,shape,key,"architecture",group,key)
      if group!="walls":
        cad.text((shape[0]+.28,shape[1]+.32),key,.35,key,"architecture",group,key)
  for key,row in model["doors"].items():
    coord = ("architecture","doors",key)
    x,y,_ = row["inside_m"]
    half = row["ramp_width_m"]/2
    for (a,_),(b,_) in pairwise(row["landing_stations_m"]):
      cad.box("A-RAMP",(min(a,b),y-half,max(a,b),y+half),key,*coord)
    if row["available"]:
      for (ax,ay),radius,outward,toward in _door_leaves(row):
        cad.line("A-DOOR",(ax,ay),(ax+outward*radius,ay),key,*coord)
        cad.arc("A-DOOR",(ax,ay),radius,0 if outward>0 else 180,90 if toward>0 else 270,key,*coord)
    else:
      cad.line("A-DOOR",(x,row["minimum_m"][1]),(x,row["maximum_m"][1]),key,*coord)
    cad.text((x+.30,y+1.08),key,.30,key,*coord)
  for key,row in cad.inputs["layout"]["objects"].items():
    if row["zone"]==model["zone"] and row["discipline"]=="it":
      a,b,c,d = _xy(row)
      cad.box("A-IT-POD",(a,b,c,d),key,"layout","objects",key)
      cad.text((a+.21,(b+d)/2),key,.28,key,"layout","objects",key)
  h = model["hall"]
  x,y = h["minimum_m"][:2]
  levels = cad.inputs["levels"]
  level = levels["zones"][model["zone"]]["level"]
  z = levels["levels_m"][level]
  cad.text((x+.5,y+.62),f"{level}  EL {z:+.3f} m",.34,level,"levels","levels_m",level)


def _structure(cad, foundations=False):
  _grid(cad)
  for key,row in cad.inputs["structure"]["members"].items():
    group = row["kind"]
    if (group=="foundation")!=foundations:
      continue
    for i,part in enumerate(row["parts"]):
      layer = "S-PLINTH" if part["identity"]=="Plinth" else f"S-{group.upper()}"
      cad.box(layer,_part_bounds(part,(0,1)),key,"structure","members",key,"parts",i)
    a,b,_ = row["start_m"]
    x,y,_ = row["end_m"]
    cad.text(((a+x)/2+.38,(b+y)/2+.38),key,.27,key,"structure","members",key)


def _foundations(cad):
  _structure(cad,foundations=True)


def _facilities_water(cad):
  _grid(cad)
  net = cad.inputs["fws-network"]
  for key,row in net["segments"].items():
    leg = row["leg"].upper()
    cad.route(f"M-FWS-{leg}",row["points_m"],key,"fws-network","segments",key)
  for key,row in net["penetrations"].items():
    x,y,z = row["point_m"]
    r = row["square_width_m"]/2
    cad.box("M-FWS-OPENING",(x-r,y-r,x+r,y+r),key,"fws-network","penetrations",key)
  cad.text((8,28),"FWS  |  3D CENTRELINES  |  TOP VIEW",.35,"FWS","fws-network","frame")


def _generator(cad):
  model = cad.inputs["generator-yard"]
  name = "generator-yard"
  yard = model["yard"]
  x,y,u,v = yard["extent_m"]
  cad.box("C-OIL-BUND",(x,y,u,v),"YARD",name,"yard","extent_m")
  cad.dim((x,y-1.3),(x,y),(u,y),0,"YARD",name,"yard","extent_m")
  cad.box("C-OIL-SEALED",yard["basin_inner_extent_m"],"OIL-SEALED",name,"yard","basin_inner_extent_m")
  for key,row in model["equipment"].items():
    for field,layer in (("reservation","E-GEN-RESERVE"),("pad","S-GEN-PAD"),("indicative_skid","E-GEN-SKID"),
                        ("radiator_reservation","M-GEN-RADIATOR")):
      cad.box(layer,_xy(row[field]),key,name,"equipment",key,field)
    x,y = row["reservation"]["minimum_m"][:2]
    cad.text((x+.15,y+.17),key,.27,key,name,"equipment",key)
  for side,row in model["tanks"].items():
    for field,label in (("body","TK"),("pump","PB")):
      cad.box("M-FUEL-EQUIP",_xy(row[field]),f"{label}-{side}",name,"tanks",side,field)
    for key,box in row["day_tanks"].items():
      cad.box("M-FUEL-EQUIP",_xy(box),f"DT-{key}",name,"tanks",side,"day_tanks",key)
  for key,row in model["routes"].items():
    layer = "M-FUEL-RETURN" if key.startswith("RET-") else "M-FUEL-PIPE"
    cad.route(layer,row["points_m"],key,name,"routes",key)
  cad.route("C-OIL-SEALED",model["collector"]["points_m"],"OIL-I",name,"collector")


VIEWS = {
  "site":(_site,("site","grid","levels")),
  "architecture-l01":(_architecture,("grid","levels","layout","architecture")),
  "structure-l01":(_structure,("grid","structure")),
  "structure-footings":(_foundations,("grid","structure")),
  "facilities-water":(_facilities_water,("grid","fws-network")),
  "generator-yard":(_generator,("generator-yard",)),
}


def build_cad_view(context):
  import ezdxf

  view = context.scope["view"]
  projection,models = VIEWS[view]
  inputs = {key:staged_json(context,f"{MODELS}/{key}.json") for key in models}
  output = context.build_path/context.output
  if output.suffix.lower() != ".dxf":
    raise ValueError("CAD view paths require the .dxf extension.")
  options = ezdxf.options
  previous = options.write_fixed_meta_data_for_testing
  options.write_fixed_meta_data_for_testing = True
  try:
    doc = ezdxf.new("R2013",units=4)
    doc.header["$LWDISPLAY"] = True
    cad = CadPlan(doc,inputs)
    projection(cad)
    extents = list(cad.model)
    if not extents or len(extents)!=len(cad.entities):
      raise ValueError(f"{view} produced unlinked CAD entities.")
    audit = doc.audit()
    if audit.has_errors or audit.has_fixes:
      raise ValueError(f"{view} has {len(audit.errors)} DXF errors and {len(audit.fixes)} fixes.")
    from ezdxf import zoom
    zoom.extents(cad.model,factor=1.15)
    output.parent.mkdir(parents=True,exist_ok=True)
    doc.saveas(output)
  finally:
    options.write_fixed_meta_data_for_testing = previous
  report = output.with_name(output.stem+"-cad.json")
  write_json(report,{
    "schema_version":1,"view":view,"units":"mm","space":"model","dxf_version":doc.dxfversion,
    "entity_count":len(cad.entities),"layers":dict(Counter(row["layer"] for row in cad.entities.values())),
    "dxf_digest":file_digest(output),"entity_map":cad.entities,
    "model_digests":{path:file_digest(context.build_path/path) for path in (f"{MODELS}/{key}.json" for key in models)},
  })
  return TargetResult("pass",(artifact(context,f"{view}-cad",output),artifact(context,f"{view}-cad-map",report)))
