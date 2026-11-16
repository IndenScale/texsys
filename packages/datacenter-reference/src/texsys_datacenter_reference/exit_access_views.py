"""Draw physical exit hardware, site evacuation routes and candidate checks."""

from itertools import pairwise
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .architecture import MODEL as ARCH
from .exit_access import MODEL
from .exit_access_checks import STUDY, _footprint
from .fws_buffer_views import _canvas
from .fws_transient_views import _text_at
from .plans import _add, _line, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json

_STYLE = """
.hall{fill:#eff2f2;stroke:#536571;stroke-width:.13}
.equipment{fill:#e6e8e7;stroke:#6d7a7e;stroke-width:.10}
.ramp{fill:#f2ead7;stroke:#826745;stroke-width:.084}
.rail{fill:none;stroke:#5d4a37;stroke-width:.115}
.swing{fill:none;stroke:#8c6540;stroke-width:.079;stroke-dasharray:.18 .12}
.leaf{fill:none;stroke:#8d6038;stroke-width:.15}
.turn{fill:#e2ead7;stroke:#58704e;stroke-width:.095;stroke-dasharray:.15 .14}
.path-west{fill:#bad8d0;stroke:#397665;stroke-width:.14}
.path-east{fill:#cddef0;stroke:#4b7192;stroke-width:.14}
.dispatch-west{fill:#d1e6c9;stroke:#4e7144;stroke-width:.16}
.dispatch-east{fill:#d1e1f2;stroke:#456786;stroke-width:.16}
.frame-line{stroke:#556676;fill:none;stroke-width:.032;stroke-dasharray:.15 .09}
.detail .ramp,.detail .rail{stroke-width:.048}
.detail-metal{stroke:#556471;stroke-width:.042;fill:none}
"""


def _root(bounds,title):
  root = _canvas(bounds,title)
  SubElement(root,"style").text = _STYLE
  return root


def _poly(root,points,style,x_axis=0,y_axis=1,**attrs):
  return _add(root,"polyline",points=" ".join(f"{row[x_axis]:.6f},{-row[y_axis]:.6f}" for row in points),
              **{"class":style},**attrs)


def _ramps_plan(arch,model):
  hx,hy,hu,hv = _xy(arch["hall"])
  extents = [panel["xy_m"] for row in model["ramps"].values() for panel in row["slab"]]
  a = min(row[0] for row in extents)
  c = max(row[2] for row in extents)
  root = _root((a-1.70,hy+.35,c+2.18,hv+.22),"主机房门扇、平台及护栏")
  _rect(root,(hx,hy+.55,hu,hv),"hall")
  for key,row in model["ramps"].items():
    for slab in row["slab"]:
      _rect(root,slab["xy_m"],"ramp",data_ramp=key)
    _rect(root,row["turn_rect_m"],"turn",data_turn=key)
    for tag,rail in row["rails"].items():
      if tag.endswith("-G"):
        _poly(root,rail["points_m"],"rail",data_rail=f"{key}/{tag}")
    for leaf in row["leaves"].values():
      if row["available"]:
        _poly(root,leaf["arc_xy_m"],"swing")
      _line(root,*leaf["hinge_xy_m"],*leaf["arc_xy_m"][-1 if row["available"] else 0],"leaf").set("data_door",key)
    x,y,_ = row["branch_m"]
    left = row["side"]=="west"
    label_x = hx+.7 if left else hu-.6
    _text(root,label_x,y+.40,key,.69,"start" if left else "end")
    _text(root,x+.76 if left else x-.76,y-1.48,f"转向净地 {model['assumptions']['ramp']['turn_space_m']:.2f} m",
          .42,"start" if left else "end")
  mid = (hx+hu)/2
  _text(root,mid,(hy+hv)/2+.75,"主机房",1.0)
  _text(root,mid,(hy+hv)/2-.72,"室内门口净宽、坡道平台与室外疏散接口",.56)
  _text(root,mid,hv-1.35,"虚线为门扇转动轨迹，绿色矩形为低端转向范围。",.56)
  return root


def _section(model,door):
  row = model["ramps"][door]
  station = row["walking_stations_m"]
  # The section runs from sill towards the parcel, even where world X decreases.
  origin = station[0][0]
  length = abs(station[-1][0]-origin)
  root = _root((-.80,-.66,max(length+2.58,10.8),3.67),f"{door} 平台、坡道扶手及护栏剖面")
  root.set("class","detail")
  _text(root,.0,2.42,f"{door} 沿坡道纵剖",.26,"start")
  points = [(abs(x-origin),z) for x,_,z in station]
  depth = row["slab"][0]["depth_m"]
  _add(root,"polygon",points=" ".join(f"{x:.6f},{-z:.6f}" for x,z in
       (*points,*[(x,z-depth) for x,z in reversed(points)])),**{"class":"ramp"})
  _line(root,-.54,points[0][1],0,points[0][1],"frame-line")
  _line(root,length,points[-1][1],length+.42,points[-1][1],"frame-line")
  for key,rail in row["rails"].items():
    if key.startswith("N-"):
      _poly(root,[(abs(x-origin),z) for x,_,z in rail["points_m"]],"detail-metal",data_rail=key)
  for key,post in row["posts"].items():
    if key.startswith("N-"):
      _poly(root,[(abs(x-origin),z) for x,_,z in post["points_m"]],"detail-metal",data_post=key)
  for a,b in pairwise(points):
    mid = (a[0]+b[0])/2
    _text(root,mid,-.39,f"{(b[0]-a[0])*1000:.0f}",.16)
    _line(root,a[0],-.48,b[0],-.48,"frame-line")
  _text(root,.0,1.92,f"门扇开启净宽 {row['door_open_clear_m']:.3f} m",.17,"start")
  config = model["assumptions"]["ramp"]
  _text(root,3.12,1.92,f"坡度 {100*row['slope']:.2f}%    护栏顶 +{config['guardrail_height_m']:.2f} m",.17,"start")
  _text(root,.0,-.18,f"门槛 EL {station[0][2]:+.2f}",.16,"start")
  cross = length+1.32
  net = row["width_m"]
  half = row["slab_width_m"]/2
  _rect(root,(cross-half,1.03-depth,cross+half,1.03),"ramp")
  _text(root,cross,2.42,"踏面横剖",.23)
  for side in (-1,1):
    x = cross+side*(net/2+config["rail_outside_clear_edge_m"])
    _line(root,x,1.0,x,1.03+config["guardrail_height_m"],"detail-metal")
    for height in config["grip_heights_m"]:
      _add(root,"circle",cx=x,cy=-1.03-height,r=config["handrail_diameter_m"]/2,fill="#895f3b")
  _text(root,cross,.50,f"内净 {(row['rail_clear_m']*1000):.0f} mm",.16)
  _text(root,0,2.97,"双高度扶手和护栏随踏面标高放样，端部材料及锚固详见构造专项。",.18,"start")
  return root


def _site(context,model):
  site = staged_json(context,f"{MODELS}/site.json")
  layout = staged_json(context,f"{MODELS}/layout.json")
  arch = staged_json(context,ARCH)
  a,b,c,d = site["boundary_m"]
  root = _root((a-3.8,b-4,c+4,d+4),"主机房室外疏散路线及集合点")
  _rect(root,(a,b,c,d),"boundary")
  _rect(root,_xy(arch["hall"]),"hall")
  _text(root,24.,15.7,"主机房",.96)
  for key,row in layout["objects"].items():
    if row["zone"] in {"POWER","COOLING","STANDBY"}:
      _rect(root,_xy(row),"equipment",data_equipment=key)
  for name,row in model["ramps"].items():
    for slab in row["slab"]:
      _rect(root,slab["xy_m"],"ramp",data_ramp=name)
    x,y,_ = row["branch_m"]
    _text(root,x+2.35 if row["side"]=="west" else x+2.75,y+1.18,name,.82)
  for key,row in model["walkways"].items():
    _rect(root,_footprint(row["corners_m"]),"path-"+key.split("-")[0].lower(),data_walkway=key)
  for side,row in model["junctions"].items():
    x,y,_ = row["center_m"]
    r = row["width_m"]/2
    _rect(root,(x-r,y-r,x+r,y+r),"path-"+side,data_junction=side)
  area_side = {row["meeting"]:side for side,row in model["assumptions"]["sides"].items()}
  for key,row in model["meeting_areas"].items():
    x,y,u,v = row["extent_m"]
    _rect(root,(x,y,u,v),"dispatch-"+area_side[key],data_assembly=key)
    _text(root,(x+u)/2,(y+v)/2-.4,key,.92)
    _text(root,u+.60,(y+v)/2,f"集合区 {row['area_m2']:.1f} m²",.73,"start")
  _add(root,"path",d=f"M{c+1.9:g},{-d-.6:g} l-.55,1.5 .55,-.25 .55,.25 z",fill="#334651")
  _text(root,c+1.9,d+1.04,"N",.86)
  _line(root,a+7,b-2,a+10,b-2,"path-west")
  _text(root,a+10.5,b-2.26,"西侧路径",.74,"start")
  _line(root,a+18,b-2,a+21,b-2,"path-east")
  _text(root,a+21.5,b-2.26,"东侧路径",.74,"start")
  _text(root,a+36,b-2.26,"候选场区通道与集合区",.74,"start")
  return root


def _table(study):
  root = Element("svg",{"xmlns":"http://www.w3.org/2000/svg","viewBox":"0 0 310 179"})
  SubElement(root,"style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#233642}
.head{fill:#e2edf1;stroke:#547181;stroke-width:.35}
.line{stroke:#a4b6bd;stroke-width:.32}
"""
  SubElement(root,"title").text = "主机房室外疏散验证"
  _text_at(root,14,14,"室外疏散出口及集合条件",6.1,"start")
  _text_at(root,14,26,f"按 {study['occupancy']:.0f} 人检验各候选集合点。",4.1,"start")
  _add(root,"rect",x=14,y=34,width=281,height=9,**{"class":"head"})
  labels = ((20,"出口"),(70,"门开启净宽 m"),(128,"扇后余量 m"),(183,"至集合区室外行程 m"),(258,"集合点"))
  for x,label in labels:
    _text_at(root,x,40,label,4.0,"start")
  for index,(name,row) in enumerate(study["exits"].items()):
    y = 52+11*index
    hardware = study["hardware"][name]
    _text_at(root,20,y,name,4.0,"start")
    _text_at(root,94,y,f"{hardware['open_width_m']:.3f}",4)
    _text_at(root,149,y,f"{hardware['free_landing_m']:.3f}",4)
    _text_at(root,224,y,f"{row['site_distance_m']+row['ramp_to_branch_m']:.2f}",4)
    _text_at(root,258,y,row["assembly"],4.0,"start")
    _add(root,"line",x1=14,y1=y+4,x2=295,y2=y+4,**{"class":"line"})
  for index,(key,row) in enumerate(study["assembly"].items()):
    _text_at(root,14,104+index*10,f"{key}    场地 {row['area_m2']:.1f} m²    "
      f"按人员需要 {row['required_area_m2']:.1f} m²    最近候选危险边界 {row['clearance_m']:.1f} m",4.0,"start")
  longest = max(row["total_distance_m"] for entries in study["paths"].values() for row in entries.values())
  _text_at(root,14,134,f"从方舱外门至集合点最长 {longest:.2f} m。逐舱两条路径见计算表。",4.0,"start")
  _text_at(root,14,149,"实地通道权属、车行冲突、浓烟影响、照明与人员组织仍待核。",3.9,"start")
  return root


def build_exit_access_view(context):
  mode = context.scope["view"]
  model = staged_json(context,MODEL)
  if mode=="plan":
    root = _ramps_plan(staged_json(context,ARCH),model)
  elif mode=="section":
    root = _section(model,context.scope["door"])
  elif mode=="site":
    root = _site(context,model)
  elif mode=="schedule":
    root = _table(staged_json(context,STUDY))
  else:
    raise ValueError(f"Unknown project exit view {mode}.")
  path = context.build_path/context.output
  path.parent.mkdir(parents=True,exist_ok=True)
  indent(root)
  path.write_text(tostring(root,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult(artifacts=(artifact(context,"exit-access-view",path),))
