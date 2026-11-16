"""Draw outdoor equipment foundation setout, representative section and check envelope."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .equipment_foundation_study import STUDY
from .equipment_foundations import MODEL
from .fws_buffer_views import _canvas
from .fws_transient_views import _text_at
from .plans import _add, _line, _rect, _text, _xy
from .site_grading import MODEL as TERRAIN
from .site_storm import terrain_profile
from .spatial_design import MODELS, artifact, staged_json

_STYLE = """
.pad-outline{fill:#e8ecec;stroke:#234e63;stroke-width:.078}
.asset-outline{fill:#f8f9f9;stroke:#697b84;stroke-width:.053;stroke-dasharray:.15 .1}
.setout{stroke:#456674;fill:none;stroke-width:.041;stroke-dasharray:.16 .11}
.anchor-setout{fill:#ba7442;stroke:#73442a;stroke-width:.018}
.leader{fill:none;stroke:#526974;stroke-width:.036}
.profile{stroke:#516d50;stroke-width:.015;fill:none}
.section .pad-outline,.section .asset-outline{stroke-width:.012}
.bedding{fill:#e9dfc5;stroke:#8c7c56;stroke-width:.012}
.mat-line{stroke:#395e76;stroke-width:.014}
.section-axis{stroke:#60727f;stroke-width:.009;stroke-dasharray:.038 .027}
.section-bolt{stroke:#95532d;stroke-width:.016;fill:#c88450}
"""


def _root(bounds,title):
  root = _canvas(bounds,title)
  SubElement(root,"style").text = _STYLE
  return root


def _plan(context,model,zone):
  group = {id:row for id,row in model["foundations"].items() if row["zone"]==zone}
  extents = [_xy(row["pad"]) for row in group.values()]
  x0 = min(a for a,_,_,_ in extents)
  y0 = min(b for _,b,_,_ in extents)
  x1 = max(c for _,_,c,_ in extents)
  y1 = max(d for _,_,_,d in extents)
  title = {"POWER":"供配电设备基础及锚栓定位", "COOLING":"冷源与给水设备基础定位",
           "STANDBY":"备用发电机组基础与安装定位"}[zone]
  root = _root((x0-1.5,y0-2.3,x1+3.45,y1+2.35),title)
  axes = staged_json(context,f"{MODELS}/grid.json")
  for axis, marks in (("x",axes["x"]),("y",axes["y"])):
    for name, value in marks.items():
      if axis=="x" and x0-1.12<=value<=x1+.5:
        _line(root,value,y0-.85,value,y1+.54,"grid")
        _add(root,"circle",cx=value,cy=-y0+1.02,r=.235,**{"class":"axis"})
        _text(root,value,y0-1.11,name,.27)
      if axis=="y" and y0-.55<=value<=y1+.55:
        _line(root,x0-.7,value,x1+.35,value,"grid")
        _add(root,"circle",cx=x0-.88,cy=-value,r=.235,**{"class":"axis"})
        _text(root,x0-.88,value-.075,name,.27)
  for id,row in group.items():
    a,b,c,d = _xy(row["pad"])
    _rect(root,(a,b,c,d),"pad-outline",data_foundation=id)
    _rect(root,_xy(row["equipment_bounds"]),"asset-outline",data_equipment=row["equipment"])
    for key,bolt in row["anchors"].items():
      x,y,_ = bolt["points_m"][1]
      _add(root,"circle",cx=x,cy=-y,r=.052,**{"class":"anchor-setout"},data_anchor=f"{id}/{key}")
    x,y = row["center_m"]
    if d-b<1.45:
      _line(root,c,y,c+.51,y,"leader").set("data-leader",id)
      _text(root,c+.62,y-.10,id,.30,"start")
    else:
      _text(root,x,d+.35,id,.30)
    if d-b>1.6:
      _text(root,x,y-.07,f"EL {row['pad']['maximum_m'][2]:+.2f}",.29)
  _line(root,x0,y0-1.74,x0+.57,y0-1.74,"pad-outline")
  _text(root,x0+.70,y0-1.84,"钢筋混凝土基础",.30,"start")
  _add(root,"circle",cx=x0+4.43,cy=-y0+1.74,r=.066,**{"class":"anchor-setout"})
  _text(root,x0+4.57,y0-1.84,"预埋锚栓",.30,"start")
  _text(root,x0,y1+1.56,"X、Y 坐标以项目轴网为准，逐台明细见基础与锚栓表。",.32,"start")
  _add(root,"path",d=f"M{x1+2.7:g},{-y1-1.08:g} l-.19,.55 .19,-.09 .19,.09 z",fill="#293845")
  _text(root,x1+2.7,y1+1.24,"N",.31)
  return root


def _section(context,model,id):
  row = model["foundations"][id]
  x, _ = row["center_m"]
  _,a,bottom = row["pad"]["minimum_m"]
  _,b,top = row["pad"]["maximum_m"]
  _,_,bed = row["bedding"]["minimum_m"]
  _,e0,_ = row["equipment_bounds"]["minimum_m"]
  _,e1,_ = row["equipment_bounds"]["maximum_m"]
  root = _root((a-.62,bed-.39,b+1.42,top+1.39),f"{id} 基础、垫层及锚栓横剖")
  root.set("class","section")
  _rect(root,(a,bed,b,bottom),"bedding",data_level="bedding")
  _rect(root,(a,bottom,b,top),"pad-outline",data_foundation=id)
  _rect(root,(e0,top,e1,top+.67),"asset-outline",data_equipment=row["equipment"])
  foundation_site = staged_json(context,TERRAIN)
  left,right = a-.55,b+.58
  terrain = terrain_profile(foundation_site,(x,left,0.),(x,right,0.))
  _add(root,"polyline",points=" ".join(f"{left+offset:.6f},{-z:.6f}" for offset,z,_ in terrain),
       **{"class":"profile"})
  _line(root,a-.45,top,b+.81,top,"section-axis")
  _text(root,b+.84,top+.02,f"基础顶 {top:+.2f}",.082,"start")
  _text(root,b+.84,bottom-.02,f"混凝土底 {bottom:+.2f}",.080,"start")
  _text(root,(e0+e1)/2,top+.38,row["equipment"],.096)
  levels = sorted({bar["center_m"][2] for bar in row["bars"].values() if bar["axis"]==1})
  cover = model["assumptions"]["typical"]["mat_cover_m"]
  for z in levels:
    _line(root,a+cover,z,b-cover,z,"mat-line")
  bolts = {}
  for bolt in row["anchors"].values():
    p,q = bolt["points_m"]
    bolts[p[1]] = (p,q,bolt["type"])
  for y,(p,q,name) in sorted(bolts.items()):
    _line(root,y,p[2],y,q[2]+.065,"section-bolt")
    _add(root,"circle",cx=y,cy=-q[2]-.054,r=.027,**{"class":"section-bolt"})
    _text(root,y,q[2]+.19,name,.087)
  _line(root,a,bed-.15,b,bed-.15,"section-axis")
  for side in (a,b):
    _line(root,side-.045,bed-.20,side+.045,bed-.10,"section-axis")
  _text(root,(a+b)/2,bed-.115,f"{(b-a)*1000:.0f}",.085)
  typical = model["assumptions"]["typical"]
  _text(root,a,top+1.04,f"两面双向钢筋 Ø{typical['bar_diameter_m']*1000:.0f}  净保护层 {cover*1000:.0f} mm",.090,"start")
  _text(root,a,top+.88,f"粒料垫层 {typical['bedding_m']*1000:.0f} mm   钢筋避让与锚栓坐标见明细。",.082,"start")
  return root


def _matrix(model,study):
  size = max(203,80+6.1*len(model["foundations"]))
  root = Element("svg",{"xmlns":"http://www.w3.org/2000/svg","viewBox":f"0 0 310 {size:g}"})
  SubElement(root,"title").text = "室外设备基础与锚栓控制表"
  SubElement(root,"style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#243641}
.header{stroke:#647984;stroke-width:.36;fill:#e4edf0}
.row{stroke:#abb9be;stroke-width:.28}
"""
  _text_at(root,12,12,"室外设备基础与锚栓控制表",5.8,"start")
  cases = sum(map(len,study["foundations"].values()))
  _text_at(root,12,23,f"{len(model['foundations'])} 座，已算 {cases} 个候选工况。",3.8,"start")
  _add(root,"rect",x=12,y=30,width=285,height=9,**{"class":"header"})
  for x,label in ((17,"基础编号"),(83,"锚栓"),(124,"砼 m³"),(156,"边缘压力 kPa"),(208,"抗滑比"),(252,"锚栓利用率")):
    _text_at(root,x,36.1,label,3.65,"start")
  for index,(id,row) in enumerate(model["foundations"].items()):
    y = 45+6.1*index
    data = study["foundations"].get(id,{})
    bond = next(iter(row["anchors"].values()))["type"]
    _text_at(root,17,y,id,3.55,"start")
    _text_at(root,83,y,f"{len(row['anchors'])} × {bond}",3.55,"start")
    _text_at(root,124,y,f"{row['concrete_m3']:.2f}",3.55,"start")
    if data:
      pressure = max(case["peak_soil_kpa"] for case in data.values())
      slide = max(case["utilization"]["sliding_torsion"] for case in data.values())
      bolt = max(case["utilization"]["bolt"] for case in data.values())
      for x,value in ((167,pressure),(216,slide),(267,bolt)):
        _text_at(root,x,y,f"{value:.2f}",3.5)
    _add(root,"line",x1=12,x2=297,y1=y+2.51,y2=y+2.51,**{"class":"row"})
  low = 59+6.1*len(model["foundations"])
  _text_at(root,12,low,"各列分别取工况包络。设备质量、压力和底座边界均为本项目假设。",3.75,"start")
  _text_at(root,12,low+11,"锚栓型式、混凝土锥体、冲切与动力隔振由专项核证。",3.75,"start")
  return root


def build_equipment_foundation_view(context):
  model = staged_json(context,MODEL)
  mode = context.scope["view"]
  if mode=="plan":
    root = _plan(context,model,context.scope["zone"])
  elif mode=="section":
    root = _section(context,model,context.scope["foundation"])
  elif mode=="schedule":
    root = _matrix(model,staged_json(context,STUDY))
  else:
    raise ValueError(f"Unknown equipment foundation view {mode}.")
  path = context.build_path/context.output
  path.parent.mkdir(parents=True,exist_ok=True)
  indent(root)
  path.write_text(tostring(root,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult(artifacts=(artifact(context,"equipment-foundation-view",path),))
