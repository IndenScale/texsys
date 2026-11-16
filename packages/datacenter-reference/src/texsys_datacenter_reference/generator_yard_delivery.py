"""Project the generator yard alternative into coordinated engineering files."""

from itertools import pairwise
from xml.etree.ElementTree import SubElement, indent, tostring

from pxr import Gf, Sdf, Tf, Usd, UsdGeom

from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import space
from .cw_network import _pipe_prim
from .exit_access_views import _root
from .generator_yard import MODEL, SOURCE
from .lighting_distribution_views import _sheet_table
from .plans import _add, _line, _rect, _text, _xy
from .site_storm_scene import _vault
from .spatial_design import artifact, source_path, staged_json
from .telecom_coordination import _prim

_STYLE = """
.reserve{fill:#f0f6f7;stroke:#426875;stroke-width:.055;stroke-dasharray:.20 .10}
.skid{fill:#81a6af;stroke:#2b5562;stroke-width:.060}
.radiator{fill:#9bb9ac;stroke:#507364;stroke-width:.055}
.pad{fill:#e1e4e2;stroke:#778980;stroke-width:.046}
.storage{fill:#efdfb6;stroke:#866331;stroke-width:.061}
.bund{fill:#faf6e8;stroke:#937649;stroke-width:.089}
.old{fill:none;stroke:#837f79;stroke-width:.048;stroke-dasharray:.22 .14}
.utility{fill:none;stroke:#634c7f;stroke-width:.073;stroke-dasharray:.22 .12}
.transfer{fill:none;stroke:#a75228;stroke-width:.066}
.suction{fill:none;stroke:#a47b39;stroke-width:.073;stroke-dasharray:.18 .09}
.vault{fill:#e7dacf;stroke:#8c5c48;stroke-width:.059}
.water{fill:#bad7df;stroke:#467d92;stroke-width:.042}
.formation{fill:none;stroke:#796746;stroke-width:.050;stroke-dasharray:.20 .10}
.axisdim{fill:none;stroke:#52616a;stroke-width:.036}
.cut{fill:none;stroke:#496473;stroke-width:.047;stroke-dasharray:.45 .16 .12 .16}
.engine-return{fill:none;stroke:#685487;stroke-width:.065}
"""


def _canvas(bounds,title):
  root = _root(bounds,title)
  SubElement(root,"style").text = _STYLE
  return root


def _short(name):
  group,unit = name.split("/")
  return f"G-{group[-1]}{unit[-1]}"


def _location(root,x,y,span,label):
  _line(root,x,y,x+span,y,"axisdim")
  for pos in (x,x+span):
    _line(root,pos,y-.15,pos,y+.15,"axisdim")
  _text(root,x+span/2,y+.15,label,.30)


def _plan(model):
  yard = model["yard"]
  a,b,c,d = yard["extent_m"]
  root = _canvas((a-1.15,b-2.05,model["parcel_m"][2]+.95,d+1.56),"备用电源区设备与油路定位")
  _rect(root,yard["extent_m"],"bund",data_object="YARD")
  _rect(root,model["original_yard_m"],"old",data_object="prior-yard")
  _rect(root,yard["basin_inner_extent_m"],"vault",data_object="OIL-SEALED")
  v0,v1,v2,v3 = yard["basin_inner_extent_m"]
  _text(root,(v0+v2)/2,v3-.65,"OIL-SEALED",.29)
  boundary = model["parcel_m"][2]
  _line(root,boundary,b-.6,boundary,d+.60,"axisdim")
  _text(root,boundary,d+.76,"宗地东界",.27)
  for key,row in sorted(model["equipment"].items()):
    for field,cls in (("pad","pad"),("reservation","reserve"),("indicative_skid","skid"),("radiator_reservation","radiator")):
      _rect(root,_xy(row[field]),cls,data_object=key,data_geometry=field)
    x0,y0,x1,y1 = _xy(row["reservation"])
    _text(root,x0+.15,y1-.30,_short(key),.33,"start")
    _text(root,x0+.16,y0+.18,f"E {x0:.2f}  N {y0:.2f}",.26,"start")
    _text(root,x1-1.19,(y0+y1)/2-.10,"散热器",.28)
  for side,row in sorted(model["tanks"].items()):
    _rect(root,_xy(row["pad"]),"pad",data_object=f"TK-{side}",data_geometry="pad")
    for field,label in (("body","TK"),("pump","PB")):
      x0,y0,x1,y1 = _xy(row[field])
      _rect(root,(x0,y0,x1,y1),"storage",data_object=f"{label}-{side}")
      label_x = x0+.18 if label=="TK" else (x0+x1)/2
      _text(root,label_x,y1+.17,f"{label}-{side}",.28,"start" if label=="TK" else "middle")
    for key,day in row["day_tanks"].items():
      _rect(root,_xy(day),"storage",data_object=f"DT-{key}")
  for key,row in model["routes"].items():
    for p,q in pairwise(row["points_m"]):
      if p[:2]!=q[:2]:
        style = {"SU":"suction","RET":"engine-return"}.get(key.split("-")[0],"transfer")
        _line(root,*p[:2],*q[:2],style).set("data-route",key)
  p,q = model["collector"]["points_m"]
  _line(root,*p[:2],*q[:2],"utility")
  _add(root,"circle",cx=p[0],cy=-p[1],r=.13,fill="#764c39",data_object="OIL-I")
  _text(root,p[0]-.10,p[1]+.32,"OIL-I",.27,"end")
  first = next(iter(model["equipment"].values()))["reservation"]
  x0,y0,x1,y1 = _xy(first)
  tank = model["tanks"]["A"]["body"]
  tx0,ty0,tx1,ty1 = _xy(tank)
  for number,p1,p2 in (
    ("1",(x0-.29,(y0+y1)/2),(x1+.27,(y0+y1)/2)),
    ("2",((tx0+tx1)/2,ty0-.22),((tx0+tx1)/2,ty1+.22)),
    ("3",(v0-.42,v3-1.22),(v2+.42,v3-1.22)),
  ):
    _line(root,*p1,*p2,"cut").set("data-section",number)
    for px,py in (p1,p2):
      _add(root,"circle",cx=px,cy=-py,r=.126,fill="white",stroke="#496473",stroke_width=.04)
      _text(root,px,py-.075,number,.205)
  x = a+.38
  _text(root,x,b-.72,"灰虚线 原油区   蓝虚线 机位包络   橙线 分列供油   紫线 回油及事故液",.32,"start")
  _location(root,a,b-1.35,c-a,f"{(c-a)*1000:.0f}")
  _add(root,"path",d=f"M{boundary-.55},{-d+.10} l-.18,.56 .18,-.10 .18,.10 z",fill="#3d5f68")
  _text(root,boundary-.55,d-.02,"N",.30)
  return root


def _section(model):
  key,row = next(iter(sorted(model["equipment"].items())))
  root = _canvas((-.9,-3.65,23.8,9.0),"机组纵剖及油罐与事故池剖面")
  x0 = row["reservation"]["minimum_m"][0]
  for field,cls in (("reservation","reserve"),("pad","pad"),("indicative_skid","skid"),("radiator_reservation","radiator")):
    x,y,z = row[field]["minimum_m"]
    u,v,w = row[field]["maximum_m"]
    _rect(root,(x-x0,z,u-x0,w),cls,data_object=key,data_geometry=field)
  floor = model["yard"]["formation_m"]
  _line(root,-.45,floor,9.6,floor,"formation")
  _text(root,4.55,5.08,f"1–1  {_short(key)} 沿纵轴",.45)
  _text(root,3.65,1.82,"表列机座参考限界",.35)
  _text(root,8.12,2.10,"散热器",.30)
  _text(root,3.48,-.56,f"基础顶 EL {row['pad']['maximum_m'][2]:+.2f} m",.31)
  _location(root,0,-1.13,row["reservation"]["maximum_m"][0]-x0,
            f"{(row['reservation']['maximum_m'][0]-x0)*1000:.0f}  罩体预留")
  tank = model["tanks"]["A"]
  center = tank["centerline_m"][0][2]
  r = tank["centerline_diameter_m"]/2
  x = 12.0
  py,pz = tank["pad"]["minimum_m"][1:]
  qy,qz = tank["pad"]["maximum_m"][1:]
  half = (qy-py)/2
  _rect(root,(x-half,pz,x+half,qz),"pad")
  _,py,pz = tank["saddles"][0]["minimum_m"]
  _,qy,qz = tank["saddles"][0]["maximum_m"]
  half = (qy-py)/2
  _rect(root,(x-half,pz,x+half,qz),"pad")
  _add(root,"circle",cx=x,cy=-center,r=r,**{"class":"storage"},data_object="TK-A")
  for elev,label in ((tank["maximum_surface_m"],"最高工作液位"),(tank["minimum_surface_m"],"最低工作液位")):
    _line(root,x-r+.2,elev,x+r+1.28,elev,"utility")
    _text(root,x+r+.27,elev+.13,f"{label} {elev:+.2f}",.26,"start")
  _text(root,12.4,5.08,"2–2  TK-A 横剖",.45)
  _text(root,12.0,-.56,f"净用油量 {tank['usable_l']:.0f} L",.31)
  yard = model["yard"]
  w = yard["basin_inner_extent_m"][2]-yard["basin_inner_extent_m"][0]
  bottom,ceiling = yard["basin_bottom_m"],yard["basin_ceiling_m"]
  top = bottom+yard["basin_working_depth_m"]
  x = 18.5
  _rect(root,(x-.20,bottom-.15,x+w+.20,ceiling),"vault",data_object="OIL-SEALED")
  _rect(root,(x,bottom,x+w,top),"water")
  _line(root,17.45,floor,22.76,floor,"formation")
  _text(root,19.8,5.08,"3–3  事故池横剖",.45)
  _text(root,19.8,2.82,"池工作液位与进管高程",.34)
  _line(root,x-1.04,yard["basin_pipe_invert_m"],x,yard["basin_pipe_invert_m"],"utility")
  _text(root,x+.05,ceiling+.33,f"管底 EL {yard['basin_pipe_invert_m']:+.2f}",.30,"start")
  _text(root,x+w/2,top-.55,f"最高工作 EL {top:+.2f}",.29)
  _text(root,x+w/2,bottom+.28,f"池底 EL {bottom:+.2f}",.32)
  return root


def _adequacy(model):
  rows = []
  for key,row in sorted(model["power_and_fuel_cases"].items()):
    status = "pass" if row["status"]==row["fuel_status"]=="pass" else "fail"
    rows.append(((_short(key),_short(row["running"]),f"{row['demand_kw']:.0f}",f"{row['active_margin_kw']:+.1f}",
                  f"{row['generator_a']:.0f}",f"{row['endurance_h']:.2f}",f"{row['sensitivity_margin_kw']:+.1f}"),status))
  ref = model["reference"]
  ratings = model["ratings"]["A"]
  case = next(iter(model["power_and_fuel_cases"].values()))
  root = _sheet_table("备用发电出力及供油工况","逐列失去任一机组；按整列设施用电与本机散热计算。",
    ("失效机组","运行机组","设施 kW","余量 kW","机端 A","续航 h","折减余量 kW"),rows,
    (8,86,127,167,206,243,283),
    (f"参考 {ref['document']}  {ref['voltage_v']} V / {ref['frequency_hz']} Hz；散热器 {ref['radiator_kw']} kW。",
     f"原罐按同一厂家保守工况只能运行 {case['original_storage_h_on_same_duty']:.2f} h；场地降容及成套性能待签核。"))
  for x,y,w,h,fill in ((6,85,300,74,"#f5f8f9"),(15,107,60,28,"#e1eaee"),
                       (102,107,63,28,"#e5eee8"),(200,107,93,28,"#e1eaee")):
    _add(root,"rect",x=x,y=y,width=w,height=h,fill=fill,stroke="#8ca1a6",stroke_width=.46)
  for x1,x2 in ((75,102),(165,200)):
    _add(root,"line",x1=x1,y1=122,x2=x2,y2=122,stroke="#52747f",stroke_width=1)
  def label(x,y,txt,size=4.7):
    _add(root,"text",x=x,y=y,font_size=size,text_anchor="middle").text = txt
  label(150,98,"每列机端、风机分支和设施转换接口")
  label(45,118,"一台 DCC 机组")
  label(45,130,f"{ratings['nameplate_a']:.0f} A  机端额定")
  label(133,118,"发电馈线")
  label(133,130,f"{ratings['generator_feeder_ampacity_a']:.0f} A  候选")
  label(247,118,"设施转换 ATS")
  label(247,130,f"{ratings['facility_transfer_ampacity_a']:.0f} A  候选")
  _add(root,"path",d="M183 122 V150 H201",fill="none",stroke="#52747f",stroke_width=.80)
  label(243,151,f"散热器风机  {ref['radiator_kw']} kW")
  return root


def _containment(model):
  labels = {"design":"设计雨况","longer":"长历时","shorter":"短历时"}
  components = (("prior_m3","既存","#b8a490"),("spill_m3","事故","#c59954"),
                ("rain_m3","降雨","#75a9bb"),("remaining_m3","净余量","#cfddd4"))
  rows = [((labels.get(key,key),f"{row['rain_m3']:.2f}",
            f"{row['spill_m3']:.2f}",f"{row['prior_m3']:.2f}",f"{row['capacity_m3']:.2f}",
            f"{row['remaining_m3']:.2f}"),model["checks"][f"RETENTION_{key}"]["status"])
          for key,row in sorted(model["rain_cases"].items())]
  ctl = model["checks"]["COLLECTOR"]
  root = _sheet_table("候选油区事故液容量","围堰地面汇流到 OIL-I，经重力管进入密闭池。按全过程零外排计算。",
    ("雨况","降雨 m³","事故 m³","既存 m³","池净 m³","净余量 m³"),rows,
    (8,91,137,181,224,274),
    (f"最小地表坡度 {model['minimum_formation_grade']*100:.2f}%；重力连接管 {ctl['capacity_m3_h']:.1f} m³/h。",
     f"峰值降雨入流 {ctl['peak_rain_m3_h']:.1f} m³/h；独立于场区雨水与工艺排水。"))
  total = max(sum(max(0,row[field]) for field,_,_ in components) for row in model["rain_cases"].values())
  for i,(key,case) in enumerate(sorted(model["rain_cases"].items(),key=lambda pair: pair[1]["rain_m3"])):
    y = 96+i*17
    _add(root,"text",x=8,y=y+6,font_size=4.65).text = labels.get(key,key)
    x = 62
    for field,_,color in components:
      size = max(0,case[field])*227/total
      _add(root,"rect",x=x,y=y,width=size,height=8.5,fill=color,stroke="#fff",stroke_width=.3)
      x += size
  for i,(_,label,color) in enumerate(components):
    x = 62+i*54
    _add(root,"rect",x=x,y=151,width=6,height=4.3,fill=color)
    _add(root,"text",x=x+8,y=155,font_size=4.35).text = label
  return root


def build_generator_yard_view(context):
  kind = context.scope["view"]
  model = staged_json(context,MODEL)
  root = {"plan":_plan,"section":_section,"adequacy":_adequacy,"containment":_containment}[kind](model)
  output = context.build_path/context.output
  output.parent.mkdir(parents=True,exist_ok=True)
  indent(root,space="  ")
  output.write_text(tostring(root,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult("pass",(artifact(context,f"generator-yard-{kind}",output),))


def build_generator_yard_scene(context):
  model = staged_json(context,MODEL)
  source = source_path(context,SOURCE).relative_to(context.root).as_posix()
  stage = Usd.Stage.CreateInMemory()
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,"/AIDC").GetPrim())
  UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
  UsdGeom.SetStageMetersPerUnit(stage,1)
  source_map = {}
  yard = model["yard"]
  a,b,c,d = yard["extent_m"]
  z = yard["formation_m"]
  x,y = yard["collector_xy_m"]
  p = yard["collector_rim_m"]
  mesh = UsdGeom.Mesh.Define(stage,"/AIDC/GeneratorYard/Formation")
  mesh.CreatePointsAttr([Gf.Vec3f(*v) for v in ((a,b,z),(c,b,z),(c,d,z),(a,d,z),(x,y,p))])
  mesh.CreateFaceVertexCountsAttr([3]*4)
  mesh.CreateFaceVertexIndicesAttr([0,1,4,1,2,4,2,3,4,3,0,4])
  mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
  mesh.CreateDisplayColorAttr([Gf.Vec3f(.75,.70,.55)])
  mesh.GetPrim().CreateAttribute("texsys:identity",Sdf.ValueTypeNames.String).Set("YARD")
  source_map["YARD"] = {"file":source,"path":"yard"}
  def draw(identity,group,record,color,path):
    _prim(stage,f"/AIDC/GeneratorYard/{group}",identity,space(record),color)
    source_map[identity] = {"file":source,"path":path}
  height = yard["bund_crest_m"]
  for index,(lo,hi) in enumerate((((a,b,z),(c,b+.10,height)),((a,d-.10,z),(c,d,height)),
                                 ((a,b+.10,z),(a+.10,d-.10,height)),((c-.10,b+.10,z),(c,d-.10,height))),1):
    _prim(stage,f"/AIDC/GeneratorYard/Bund_{index}","YARD",Space(lo,hi),(.49,.42,.32))
  for key,row in sorted(model["equipment"].items()):
    parent = f"Equipment/{Tf.MakeValidIdentifier(key)}"
    for field,color in (("pad",(.69,.71,.71)),("indicative_skid",(.27,.47,.53)),("radiator_reservation",(.40,.60,.50))):
      draw(key,f"{parent}/{field}",row[field],color,"equipment.origins_m")
  for side,row in sorted(model["tanks"].items()):
    identity = f"TK-{side}"
    _pipe_prim(stage,f"/AIDC/GeneratorYard/Fuel/Tank_{side}",identity,row["centerline_m"],
               row["centerline_diameter_m"],(.78,.59,.29),sides=24)
    source_map[identity] = {"file":source,"path":f"fuel.tanks.{side}"}
    for name,base in (("Pad",row["pad"]),*((f"Saddle_{i}",box) for i,box in enumerate(row["saddles"],1))):
      draw(identity,f"Fuel/Tank{side}_{name}",base,(.62,.63,.58),f"fuel.tanks.{side}")
    draw(f"PB-{side}",f"Fuel/Pump_{side}",row["pump"],(.57,.55,.39),f"fuel.tanks.{side}")
    for key,box in sorted(row["pumps"].items()):
      draw(key,f"Fuel/Pump_{side}/{Tf.MakeValidIdentifier(key)}",box,(.39,.52,.36),f"fuel.tanks.{side}")
    for key,box in sorted(row["day_tanks"].items()):
      draw(f"DT-{key}",f"Fuel/Day_{Tf.MakeValidIdentifier(key)}",box,(.83,.59,.33),"fuel")
  for key,row in sorted(model["routes"].items()):
    color = {"RET":(.53,.40,.64),"SU":(.67,.51,.28)}.get(key.split("-")[0],(.78,.32,.17))
    _pipe_prim(stage,f"/AIDC/GeneratorYard/Pipes/{Tf.MakeValidIdentifier(key)}",key,row["points_m"],
               row["pipe"]["outside_m"],color,sides=10)
    source_map[key] = {"file":source,"path":"fuel.tanks"}
  collector = model["collector"]
  _pipe_prim(stage,"/AIDC/GeneratorYard/Pipes/Collector","OIL-I",collector["points_m"],
             collector["pipe"]["inside_m"]+.025,(.55,.37,.66),sides=12)
  source_map["OIL-I"] = {"file":source,"path":"yard.collector_xy_m"}
  basin = {"extent_m":yard["basin_inner_extent_m"],"bottom_m":yard["basin_bottom_m"],"ceiling_m":yard["basin_ceiling_m"]}
  _vault(stage,"OIL-SEALED","/AIDC/GeneratorYard/AccidentBasin",basin,(.59,.49,.45),cutaway=True)
  source_map["OIL-SEALED"] = {"file":source,"path":"yard.basin_inner_extent_m"}
  output = context.build_path/"scenes/aidc-generator-yard-alternative.usda"
  output.parent.mkdir(parents=True,exist_ok=True)
  if not stage.Flatten(False).Export(str(output)):
    raise OSError(f"Could not export {output.name}.")
  return TargetResult("pass",(artifact(context,"generator-yard-alternative",output,
    preview={"kind":"scene","source_map":source_map}),))
