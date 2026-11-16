"""Produce generator fuel plans, sections and containment coordination scenes."""

from itertools import pairwise
from math import ceil, dist
from xml.etree.ElementTree import SubElement, indent, tostring

from pxr import Tf, Usd, UsdGeom

from texsys.targets import TargetResult

from .architecture import space
from .cw_network import _pipe_prim
from .exit_access_views import _root
from .fuel_study import STUDY
from .fuel_system import LAYOUT, MODEL, SOURCE
from .lighting_distribution_views import _sheet_table
from .plans import _add, _line, _rect, _text, _xy
from .site_grading import MODEL as TERRAIN, elevation
from .site_storm_scene import _oil_enclosure
from .spatial_design import artifact, source_path, staged_json
from .telecom_coordination import _prim

BASE_SCENE = "scenes/aidc-lightning-coordinated.usda"
_STYLE = """
.fuel-yard{fill:#fbf8ec;stroke:#ad8355;stroke-width:.069}
.fuel-host{fill:#e3ebeb;stroke:#607887;stroke-width:.055}
.fuel-shell{fill:#eeddb9;stroke:#a47436;stroke-width:.065}
.fuel-pump{fill:#c9d3cc;stroke:#526e5c;stroke-width:.063}
.fuel-sealed{fill:#eccdc3;stroke:#9d503c;stroke-width:.069;stroke-dasharray:.18 .10}
.fuel-skid{fill:#b9c4c7;stroke:#5d6e75;stroke-width:.038}
.fuel-pressure{fill:none;stroke:#b65c26;stroke-width:.079}
.fuel-overflow{fill:none;stroke:#516781;stroke-width:.086;stroke-dasharray:.13 .07}
.fuel-suction{fill:none;stroke:#8b7648;stroke-width:.073}
.fuel-engine-return{fill:none;stroke:#72538b;stroke-width:.068}
.fuel-ancillary{fill:none;stroke:#4c776a;stroke-width:.074}
.fuel-level{fill:none;stroke:#4b718c;stroke-width:.031;stroke-dasharray:.14 .08}
.fuel-bund{fill:none;stroke:#86552e;stroke-width:.145;stroke-dasharray:.23 .12}
.fuel-ground{fill:none;stroke:#756951;stroke-width:.050}
.arrow{fill:#467681}
"""


def _canvas(bounds,title):
  root = _root(bounds,title)
  SubElement(root,"style").text = _STYLE
  return root


def _class(kind):
  return {"suction":"fuel-suction","transfer":"fuel-pressure","overflow":"fuel-overflow",
          "engine_supply":"fuel-pressure","engine_return":"fuel-engine-return",
          "vent":"fuel-ancillary","fill":"fuel-ancillary"}[kind]


def _stroke(root,key,row,y_offset=0):
  for a,b in pairwise(row["points_m"]):
    if a[:2]!=b[:2]:
      _line(root,a[0],a[1]+y_offset,b[0],b[1]+y_offset,_class(row["kind"])).set("data-fuel-run",key)


def _host(root,key,data):
  x,y,x1,y1 = _xy(data)
  _rect(root,(x,y,x1,y1),"fuel-host",data_host=key)
  _text(root,(x+x1)/2,(y+y1)/2-.07,key,.31)


def _equipment_labels(root, model):
  for key,row in sorted(model["tanks"].items()):
    x,y,u,v = _xy(row["body"])
    _rect(root,_xy(row["pad"]),"fuel-host",data_fuel_pad=key)
    _rect(root,(x,y,u,v),"fuel-shell",data_tank=key)
    _text(root,(x+u)/2,(y+v)/2-.07,key,.38)
  for key,row in sorted(model["skids"].items()):
    x,y,u,v = _xy(row["body"])
    _rect(root,(x,y,u,v),"fuel-pump",data_skid=key)
    _text(root,x-.24,y+.35,key,.26,"end")


def _yard(context,model,_study):
  terrain = model["oil_yard"]
  a,b,c,d = terrain["extent_m"]
  root = _canvas((a-1.15,b-1.56,c+1.74,d+1.55),"备用电源区燃油设备及外管定位")
  _rect(root,(a,b,c,d),"fuel-yard")
  layout = staged_json(context,LAYOUT)
  for side in ("A","B"):
    tag = f"GenContainer{side}"
    _host(root,tag,layout["objects"][tag])
  _rect(root,terrain["sealed_storage"]["extent_m"],"fuel-sealed",data_vault="OIL-SEALED")
  x,y = terrain["inlet_xy_m"]
  _add(root,"circle",cx=x,cy=-y,r=.14,fill="#8c5c40",data_oil_inlet="OIL-I")
  _text(root,x+.28,y+.28,"OIL-I",.27,"start")
  _equipment_labels(root,model)
  for key,row in sorted(model["runs"].items()):
    if key.split("-")[0] not in ("SU","TR","OV","FL","VT"):
      continue
    _stroke(root,key,row)
    if row["kind"]=="transfer":
      x,y,_ = row["points_m"][-1]
      _text(root,x+.91,y-.42,key,.27,"start")
  _text(root,a+.25,b-1.02,"红色供油    蓝色虚线溢流    绿色注油和通气",.31,"start")
  _text(root,70.75,37.05,"密闭收集池",.27)
  _add(root,"path",d=f"M{c+.84},{-d+.50} l-.23,.60 .23,-.10 .23,.10 z",fill="#3d5f68")
  _text(root,c+.84,d-.33,"N",.25)
  for x in (a,c):
    _line(root,x,b-.20,x,b-.54,"fuel-level")
  _line(root,a,b-.50,c,b-.50,"fuel-level")
  _text(root,(a+c)/2,b-.41,f"{c-a:g} m",.25)
  return root


def _indoor(context,model,_study):
  placement = staged_json(context,LAYOUT)["objects"]
  root = _canvas((55.85,28.6,71.,38.52),"两列发电机方舱日用油箱及供回油支路")
  for side in ("A","B"):
    host = f"GenContainer{side}"
    body = placement[host]
    offset = 0 if side=="A" else -2.4
    x,y,x1,y1 = _xy(body)
    _rect(root,(x,y+offset,x1,y1+offset),"fuel-host",data_host=host)
    _text(root,x+.02,y1+offset+.31,f"{side} 列发电机方舱",.28,"start")
    for key,row in sorted(model["day_tanks"].items()):
      if row["host"]!=host:
        continue
      p,q,u,v = _xy(row["body"])
      _rect(root,(p,q+offset,u,v+offset),"fuel-shell",data_day_tank=key)
      a,b,c,d = _xy(row["engine_body"])
      _rect(root,(a,b+offset,c,d+offset),"fuel-skid",data_engine=row["engine"])
      _text(root,(p+u)/2,v+offset+.34,key,.25)
      engine_number = row["engine"].rsplit("Genset",1)[-1]
      _text(root,a-.14,b+offset+.21,f"G-{engine_number}",.23,"end")
    for key,row in sorted(model["runs"].items()):
      if row["train"]==side and key.split("-")[0] not in ("SU","TR","OV","FL","VT"):
        _stroke(root,key,row,offset)
  _text(root,57.,29.2,"红色供油    紫色发动机回油    蓝色虚线重力溢流",.27,"start")
  return root


def _profile(context,model,study):
  route = model["runs"]["OV-A"]
  root = _canvas((-.77,-3.70,19.12,9.87),"A 列溢流纵断及储罐液位构造")
  _text(root,.03,9.10,"A—A　日用箱至 A 列储罐溢流纵断",.48,"start")
  points = route["points_m"]
  elevation_shift = 5.2
  chainage = 0.
  terrain = staged_json(context,TERRAIN)
  grade_trace = []
  for a,b in pairwise(points):
    length = dist(a[:2],b[:2])
    _line(root,chainage,a[2]+elevation_shift,chainage+length,b[2]+elevation_shift,"fuel-overflow").set("data-fuel-run","OV-A")
    increments = ceil(length/.75)
    for index in range(increments+1):
      ratio = index/increments
      x,y = (first+(last-first)*ratio for first,last in zip(a[:2],b[:2],strict=True))
      grade_trace.append((chainage+length*ratio,elevation(terrain,x,y)+elevation_shift))
    chainage += length
  for a,b in pairwise(grade_trace):
    _line(root,*a,*b,"fuel-ground")
  _text(root,.12,4.79,"0.00 m",.26,"start")
  _text(root,chainage-.10,4.79,f"{chainage:.2f} m",.26,"end")
  slope = 100*study["overflow"]["OV-A"]["minimum_grade"]
  _text(root,.10,7.89,f"方舱溢流端 EL +{points[0][2]:.3f}",.33,"start")
  _text(root,7.0,7.58,f"{route['pipe']['size']}  全长 {route['length_m']:.2f} m  坡度 {slope:.2f}%",.32,"start")
  _text(root,chainage-.18,6.45,f"进罐 EL +{points[-1][2]:.3f}",.33,"end")
  tank = model["tanks"]["TK-A"]
  x = 4.05
  z = tank["center_m"][2]
  diameter = tank["diameter_m"]
  _text(root,.02,3.31,"B—B　储罐净容积及控制液位",.44,"start")
  _add(root,"circle",cx=x,cy=-z,r=diameter/2,**{"class":"fuel-shell"},data_section="TK-A")
  _text(root,x,z-.095,"TK-A",.24)
  for name,title in (("maximum_surface_m","最高工作液位"),("minimum_surface_m","最低有效液位")):
    level = tank[name]
    _line(root,x-.60,level,x+2.06,level,"fuel-level")
    _text(root,x+2.25,level-.04,f"{title} EL +{level:.3f}",.30,"start")
  _text(root,.05,-1.1,f"单罐净名义容量 {tank['nominal_l']:.0f} L，工作净可用 {tank['usable_l']:.0f} L",.33,"start")
  _text(root,.05,-1.8,"双层壳体、液位计和泄漏监测按设备接口图核证。",.31,"start")
  return root


def _system(_context,model,study):
  flow = min(row["single"]["flow_m3_h"] for row in study["transfer"].values())
  max_overflow = max(row["maximum_two_pump_flow_m3_h"] for row in study["overflow"].values())
  data = [
    (("正常转输","低液位请求补油","当列一台泵运行","达到停止液位"),
     "pass" if all(row["status"]=="pass" for row in study["transfer"].values()) else "fail"),
    (("工作泵退出","低液位持续","备用泵按延时接管","日用箱有效储备"),
     "pass" if all(row["status"]=="pass" for row in study["changeover"].values()) else "fail"),
    (("异常继续进油","高高液位","停泵及进油隔断","被动溢流回当列储罐"),
     "pass" if all(row["status"]=="pass" for section in ("overflow","high_high_stop") for row in study[section].values())
     else "fail"),
  ]
  root = _sheet_table("发电机燃油供给及联锁系统","单列示意。A/B 各有独立储罐、泵撬、两个日用箱和两台机组。",
    ("运行条件","信号或条件","过程","保护或验收"),data,(8,103,179,258),
    (f"不利单泵计算流量 {flow:.2f} m³/h，双泵并开最高 {max_overflow:.2f} m³/h。",
     "候选液位开关、执行元件、隔离检修及黑启动电源按厂家接口深化。"),table_top=112)
  def simple(x,y,width,title):
    _add(root,"rect",x=x,y=y,width=width,height=12,fill="#e6e9e4",stroke="#61726d",stroke_width=.54)
    _add(root,"text",x=x+width/2,y=y+8,text_anchor="middle",font_size=4.4).text=title
  definitions = _add(root,"defs")
  colors = {"supply":"#b65c26","overflow":"#516781","return":"#72538b"}
  for identity,color in colors.items():
    marker = _add(definitions,"marker",id=identity,viewBox="0 0 8 8",refX=7,refY=4,
                  markerWidth=5,markerHeight=5,orient="auto")
    _add(marker,"path",d="M 0 0 L 8 4 L 0 8 z",fill=color)
  def line(x1,y1,x2,y2,kind,terminal=False):
    node = _add(root,"line",x1=x1,y1=y1,x2=x2,y2=y2,stroke=colors[kind],stroke_width=.60)
    if kind=="overflow":
      node.set("stroke-dasharray","1.6 1.1")
    if terminal:
      node.set("marker-end",f"url(#{kind})")
  for x,y,w,title in ((10,63,36,"主储罐"),(67,63,42,"双转输泵"),(148,41,41,"1 号日用箱"),(148,78,41,"2 号日用箱"),
                       (236,41,42,"1 号机组"),(236,78,42,"2 号机组")):
    simple(x,y,w,title)
  for x1,y1,x2,y2 in ((46,69,67,69),(109,69,130,69),(130,47,130,84),(130,47,148,47),(130,84,148,84),
                        (189,47,236,47),(189,84,236,84)):
    line(x1,y1,x2,y2,"supply",x2 in (67,148,236))
  for p in ((148,51,139,51),(148,88,139,88),(139,51,139,100),(139,100,28,100),(28,100,28,75)):
    line(*p,"overflow",p[-1]==75)
  for p in ((236,51,189,51),(236,88,189,88)):
    line(*p,"return",True)
  return root


def _checks(_context,_model,study):
  labels = []
  sections = (
    ("续航","h",[(key,min(row["endurance_h"] for row in values.values()),
                  "pass" if all(row["status"]=="pass" for row in values.values()) else "fail")
                 for key,values in study["independent_storage"].items()]),
    ("单泵冷态转输","m³/h",[(key,row["single"]["flow_m3_h"],row["status"]) for key,row in study["transfer"].items() if "cold-dirty" in key]),
    ("发动机最低进油压力","kPa",[(key,row["minimum_supply_kpa"],row["status"]) for key,row in study["engine_ports"].items()]),
  )
  for title,unit,values in sections:
    for name,val,status in values:
      labels.append(((title,name.replace(":cold-dirty:"," / "),unit,f"{val:.2f}"),status))
  return _sheet_table("柴油备源工况及控制值","单列储罐维持 12 h，同时复核机组进回油及泵切换。",
    ("检查","路径","单位","计算值"),labels,(8,137,218,267),
    ("逐工况结果含吸入压力余量、不利溢流以及日用箱液位变化。",
     "机组实际耗油、启动油泵电源、回油温升和检修空间保留待审。"))


def _containment(context,model,study):
  yard = model["oil_yard"]
  a,b,c,d = yard["extent_m"]
  root = _canvas((a-1.32,b-1.2,c+2.2,d+2.2),"发电区事故液密闭收集")
  _rect(root,yard["extent_m"],"fuel-yard")
  _rect(root,yard["extent_m"],"fuel-bund",data_containment="oil-yard")
  sealed = yard["sealed_storage"]
  _rect(root,sealed["extent_m"],"fuel-sealed",data_vault="OIL-SEALED")
  layout = staged_json(context,LAYOUT)
  for side in ("A","B"):
    tag = f"GenContainer{side}"
    _host(root,tag,layout["objects"][tag])
  _equipment_labels(root,model)
  x,y = yard["collector_xy_m"]
  _add(root,"circle",cx=x,cy=-y,r=.18,fill="#8b5a3c",data_collector="OIL-I")
  for u,v in ((69.40,36.25),(69.44,33.4),(69.58,41.15)):
    _line(root,u,v,x,y,"fuel-level")
  _text(root,x+.27,y+.29,"OIL-I",.30,"start")
  _text(root,70.73,37.02,f"{sealed['working_capacity_m3']:.2f} m³",.27)
  case = min(study["containment"]["cases"].values(),key=lambda row:row["free_storage_m3"])
  _text(root,a+.12,d+.87,f"最长工况  需求 {case['demand_m3']:.2f} m³  剩余 {case['free_storage_m3']:.2f} m³",.34,"start")
  _text(root,a+.12,b-.62,f"围堰顶 EL +{yard['containment_crest_m']:.2f} m  密闭收集  吸排车清运",.30,"start")
  _add(root,"path",d=f"M{c+.84},{-d+.30} l-.23,.60 .23,-.10 .23,.10 z",fill="#3d5f68")
  _text(root,c+.84,d-.14,"N",.25)
  return root


def build_fuel_view(context):
  view = context.scope["view"]
  draw = {"yard":_yard,"sections":_profile,"indoor":_indoor,"system":_system,
          "checks":_checks,"containment":_containment}[view]
  svg = draw(context,staged_json(context,MODEL),staged_json(context,STUDY))
  output = context.build_path/context.output
  output.parent.mkdir(parents=True,exist_ok=True)
  indent(svg,space="  ")
  output.write_text(tostring(svg,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult("pass",(artifact(context,f"fuel-{view}",output),))


def _add_fuel(stage,model,ids,source):
  base = "/AIDC/Fuel"
  for key,row in sorted(model["tanks"].items()):
    root = f"{base}/Storage/{Tf.MakeValidIdentifier(key)}"
    _pipe_prim(stage,root+"/Shell",key,row["centerline_m"],row["diameter_m"],(.74,.54,.25),sides=16)
    _prim(stage,root+"/Pad",key,space(row["pad"]),(.62,.64,.62))
    for i,saddle in enumerate(row["saddles"],1):
      _prim(stage,f"{root}/Saddle_{i}",key,space(saddle),(.57,.52,.46))
    for i,endpoints in enumerate(row["upper_nozzle_legs_m"],1):
      _pipe_prim(stage,f"{root}/Nozzle_{i}",key,endpoints,.09,(.77,.56,.24),sides=8)
    ids[key] = {"file":source,"path":"storage"}
  for key,row in sorted(model["skids"].items()):
    root = f"{base}/Transfer/{Tf.MakeValidIdentifier(key)}"
    _prim(stage,root+"/Skid",key,space(row["body"]),(.56,.65,.58))
    for name,body in sorted(row["pumps"].items()):
      _prim(stage,f"{root}/{Tf.MakeValidIdentifier(name)}",name,space(body),(.35,.51,.43))
      ids[name] = {"file":source,"path":"hydraulics"}
    ids[key] = {"file":source,"path":"storage.sides"}
  for key,row in sorted(model["day_tanks"].items()):
    path = f"{base}/DayTanks/{Tf.MakeValidIdentifier(key)}"
    _prim(stage,path,key,space(row["body"]),(.78,.60,.33))
    ids[key] = {"file":source,"path":"cabinet"}
  colors = {"transfer":(.81,.36,.19),"overflow":(.32,.47,.62),"suction":(.68,.51,.26),"fill":(.29,.56,.44),
            "vent":(.29,.56,.44),"engine_supply":(.81,.36,.19),"engine_return":(.58,.38,.69)}
  for key,row in sorted(model["runs"].items()):
    id_ = Tf.MakeValidIdentifier(key)
    path = f"{base}/Piping/{id_}"
    _pipe_prim(stage,path,key,row["points_m"],row["pipe"]["outside_m"],colors[row["kind"]],sides=8)
    ids[key] = {"file":source,"path":"pipes."+row["kind"]}


def build_fuel_scene(context):
  model = staged_json(context,MODEL)
  layout = staged_json(context,LAYOUT)
  source = source_path(context,SOURCE).relative_to(context.root).as_posix()
  out = []
  for name,combined in (("aidc-fuel",False),("aidc-fuel-coordinated",True)):
    stage = Usd.Stage.Open(str(context.build_path/BASE_SCENE)) if combined else Usd.Stage.CreateInMemory()
    if stage is None:
      raise ValueError("缺少全场协调基底。")
    if not combined:
      stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,"/AIDC").GetPrim())
      UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
      UsdGeom.SetStageMetersPerUnit(stage,1)
      _oil_enclosure(stage,model["oil_yard"],cutaway=True)
      for key,row in sorted(model["day_tanks"].items()):
        _prim(stage,f"/AIDC/Fuel/GeneratorInterfaces/{Tf.MakeValidIdentifier(key)}",row["engine"],
              space(row["engine_body"]),(.55,.62,.63))
    ids = dict(layout["source_map"]) if combined else {}
    _add_fuel(stage,model,ids,source)
    if combined:
      for scope in ("/AIDC/FWSIsolation","/AIDC/FireSystems/Pods"):
        if stage.GetPrimAtPath(scope).IsValid():
          UsdGeom.Scope.Define(stage,scope).MakeInvisible()
    path = context.build_path/f"scenes/{name}.usda"
    path.parent.mkdir(parents=True,exist_ok=True)
    if not stage.Flatten(False).Export(str(path)):
      raise OSError(f"Could not export {path.name}.")
    out.append(artifact(context,name,path,preview={"kind":"scene","source_map":ids}))
  return TargetResult("pass",tuple(out))
