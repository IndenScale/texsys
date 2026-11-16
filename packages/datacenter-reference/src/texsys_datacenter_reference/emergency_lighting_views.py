"""Compose coordinated plans, an installation section, and emergency operating evidence."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .architecture import MODEL as ARCH
from .emergency_lighting import MODEL
from .emergency_lighting_study import STUDY, shapes
from .exit_access import MODEL as ACCESS
from .exit_access_views import _root, _site
from .plans import _add, _axes, _line, _north_and_scale, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json

_PLAN_STYLE = """
.pod-lighting{fill:#f4f5f3;stroke:#939b9b;stroke-width:.06}
.wall-lighting{fill:#465a64}
.charge-A{fill:#ffe7ae;stroke:#ab610c;stroke-width:.095}
.charge-B{fill:#d2edf5;stroke:#286888;stroke-width:.095}
.charge-sign{fill:#e2f0dd;stroke:#347349;stroke-width:.085}
.route-lighting{stroke:#92958a;stroke-dasharray:.23 .13;stroke-width:.066;fill:none}
.light-metal{fill:#e0e4e5;stroke:#516370;stroke-width:.041}
.light-slab{fill:#e5dfd4;stroke:#77664d;stroke-width:.045}
.light-dimension{fill:none;stroke:#566c76;stroke-width:.025}
"""


def _styles(root):
  SubElement(root,"style").text = _PLAN_STYLE
  return root


def _symbol(root, key, row, radius, *, label=True):
  x,y,_ = row["point_m"]
  if row["sign"]:
    _rect(root,(x-radius,y-radius,x+radius,y+radius),"charge-sign",data_luminaire=key)
    return
  if pole := row["pole"]:
    a,b,_ = pole["base_m"]
    _line(root,a,b,x,y,"light-dimension")
    _add(root,"circle",cx=a,cy=-b,r=radius*.56,fill="#627580",data_pole=key)
  _add(root,"circle",cx=x,cy=-y,r=radius,**{"class":"charge-"+row["group"]},data_luminaire=key)
  if label:
    _text(root,x,y+radius*2.35,key.rsplit("-",1)[-1]+" "+row["group"],max(.36,1.35*radius))


def _minima(root,study,keep,radius):
  for key,row in sorted(study["zones"].items()):
    if row["kind"] not in keep:
      continue
    which = min(("off_A_lx","off_B_lx"), key=lambda case:row[case]["value_lx"])
    x,y,_ = row[which]["point_m"]
    value = row[which]["value_lx"]
    fill = "#9c3632" if value<row["minimum_required_lx"] else "#596d70"
    node = _add(root,"path",d=f"M{x-radius},{-y} h{2*radius} M{x},{-y-radius} v{2*radius}",stroke=fill,
                stroke_width=radius*.31,fill="none",data_lighting_minimum=key,data_case=which)
    SubElement(node,"title").text = f"{key}  {which}  {value:.2f} lx"


def _hall(context, model,study):
  arch = staged_json(context,ARCH)
  grid = staged_json(context,f"{MODELS}/grid.json")
  layout = staged_json(context,f"{MODELS}/layout.json")
  a,b,c,d = _xy(arch["hall"])
  root = _styles(_root((a-4.75,b-7.35,c+2.60,d+3.16),"主机房疏散应急照明"))
  _rect(root,(a,b,c,d),"hall")
  _axes(root,grid,(a,b,c,d))
  _north_and_scale(root,(a,b,c-1.25,d-.20))
  for tag, row in arch["walls"].items():
    if not tag.startswith("W-over-"):
      _rect(root,_xy(row),"wall-lighting",data_wall=tag)
  for name,row in sorted(layout["objects"].items()):
    if name.startswith("IT"):
      x,y,u,v = _xy(row)
      _rect(root,(x,y,u,v),"pod-lighting",data_pod=name)
      _text(root,(x+u)/2,(y+v)/2,name,.39)
  for tag, room in sorted(arch["rooms"].items()):
    y = room["aisle_y_m"]
    _line(root,a,y,c,y,"route-lighting")
    _text(root,(a+c)/2,y-.78,f"{tag} 疏散通道",.39)
  for key, row in sorted(model["fixtures"].items()):
    if row["family"] != "outdoor":
      _symbol(root,key,row,.19 if row["sign"] else .22)
  _minima(root,study,{"hall"},.33)
  _text(root,a,d+1.67,"A / B 交错布灯    绿色双标记为出口指示灯    十字为较低照度点",.45,"start")
  return root


def _site_plan(context, model,study):
  access = staged_json(context,ACCESS)
  root = _styles(_site(context,access))
  for key,row in sorted(model["fixtures"].items()):
    if row["family"] == "outdoor":
      _symbol(root,key,row,.34,label=False)
  _minima(root,study,{"ramp","path","assembly"},.42)
  _text(root,4,54,"橙 A   蓝 B   圆点为杆脚，圆环为灯头，十字为较低照度点",.84,"start")
  return root


def _section(context, model,study):
  row = staged_json(context,ACCESS)["ramps"]["D-NW"]
  stations = row["walking_stations_m"]
  origin = stations[0][0]
  selected = [(key, obj) for key,obj in sorted(model["fixtures"].items()) if key.startswith("EL-D-NW-") and obj["pole"]]
  root = _styles(_root((-1.0,-.91,11.15,4.62),"D-NW 灯杆、坡道及基础纵剖"))
  deck = [(abs(x-origin),z) for x,_,z in stations]
  thickness = row["slab"][0]["depth_m"]
  profile = [*deck,*[(x,z-thickness) for x,z in reversed(deck)]]
  _add(root,"polygon",points=" ".join(f"{x:.6f},{-z:.6f}" for x,z in profile),**{"class":"light-slab"})
  for key,rail in sorted(row["rails"].items()):
    if key.startswith("N-"):
      data = [(abs(x-origin),z) for x,_,z in rail["points_m"]]
      for (a,b),(c,d) in zip(data,data[1:]):
        _line(root,a,b,c,d,"light-dimension")
  for key, obj in selected:
    for part,envelope in shapes(obj).items():
      a,b,c = envelope.minimum_m
      x,y,z = envelope.maximum_m
      low,high = sorted((abs(a-origin),abs(x-origin)))
      _rect(root,(low,c,high,z),"charge-"+obj["group"] if part=="Body" else "light-metal",
            data_luminaire=key,data_part=part)
    x,_,z = obj["point_m"]
    _text(root,abs(x-origin),z+.26,key.rsplit("-",1)[-1]+" "+obj["group"],.17)
    _text(root,abs(x-origin),z+.46,f"EL {z:+.2f}",.15)
  head = model["fixtures"]["EL-D-NW-HEAD-A"]
  x,_,z = head["point_m"]
  at = abs(x-origin)
  _rect(root,(at-.12,z,at+.12,z+.08),"charge-A",data_luminaire="EL-D-NW-HEAD-A")
  _text(root,at-.1,z-.27,"门上 A/B 灯头",.15,"start")
  _line(root,-.35,0,8.9,0,"light-dimension")
  _text(root,0,-.67,"灯杆位于坡道净宽之外。基础示意深度来自模型，承载及锚固需专项核证。",.17,"start")
  minimum = min(study["zones"]["D-NW"][case]["value_lx"] for case in ("off_A_lx","off_B_lx"))
  _text(root,.06,4.26,f"西北出口 D-NW    从门槛向下游投影    单组失效最低 {minimum:.2f} lx",.25,"start")
  return root


def _report(study, view):
  root = Element("svg",{"xmlns":"http://www.w3.org/2000/svg","viewBox":"0 0 310 195"})
  SubElement(root,"style").text = """
text{font-family:system-ui,sans-serif;fill:#25363f}
.header{fill:#e1edf0}.rule{stroke:#acbdc6;stroke-width:.35}.failed{fill:#ac3027;font-weight:bold}
"""
  def text(x, y, value, *, size=4.5, anchor="start", failed=False):
    _add(root,"text",x=x,y=y,font_size=size,text_anchor=anchor,**{"class":"failed" if failed else "ordinary"}).text = str(value)

  def headers(labels, columns, position):
    _add(root,"rect",x=5,y=position,width=297,height=10,**{"class":"header"})
    for j, name in enumerate(labels):
      text(columns[j],position+6.8,name,anchor="start" if j==0 else "middle")

  def rule(y):
    _add(root,"line",x1=5,y1=y,x2=302,y2=y,**{"class":"rule"})

  if view=="circuits":
    text(5,14,"应急灯具电源、充电支路及续航",size=7)
    text(5,29,"正常电源 → 区域充电支路 → 独立灯具电池与驱动 → 失电照明",size=5.3)
    columns = (9,104,160,229,275)
    headers(("区域充电支路","灯具数","正常充电 W","电池负载 W","应急投入"),columns,39)
    for index,(branch,row) in enumerate(sorted(study["charge_branches"].items())):
      y=55+index*9.4
      for j,value in enumerate((branch,len(row["fixtures"]),f"{row['charge_w']:.0f}",f"{row['emergency_w']:.0f}","灯具自持")):
        text(columns[j],y,value,anchor="start" if j==0 else "middle")
      rule(y+2.8)
    labels = {"hall":"室内灯具","outdoor":"室外灯具","sign":"出口标志"}
    for index,(family,row) in enumerate(sorted(study["battery_families"].items())):
      text(8,138+index*9.8,f"{labels[family]}    可用电量 {row['usable_wh']:.2f} Wh    候选续航 {row['runtime_min']:.1f} min",
           failed=row["runtime_min"]<study["required_minutes"])
    text(8,173,f"持续时间目标 {study['required_minutes']:g} min。上级充电配电及监控接口待安装设计。")
    return root

  text(5,14,"应急照明维持照度及失效工况",size=7)
  text(5,24,f"计算面为通行面。采样间距不大于 {study['sample_pitch_m']:.2f} m。照度单位 lx。",size=4.5)
  columns = (9,104,159,201,247,285)
  headers(("区域","网格点数","全部可用","A 组失效","B 组失效","目标"),columns,30)
  for index,(zone,row) in enumerate(sorted(study["zones"].items())):
    y=45+index*8.25
    values = [row[key]["value_lx"] for key in ("intact_lx","off_A_lx","off_B_lx")]
    for j,value in enumerate((zone,row["samples"],*values,row["minimum_required_lx"])):
      text(columns[j],y,f"{value:.2f}" if isinstance(value,float) else value,anchor="start" if j==0 else "middle",
           failed=j in {2,3,4} and value<row["minimum_required_lx"])
    rule(y+2.7)
  obstructed = sum(not probe["visible"] for row in study["signs"].values() for probe in row["probes"])
  text(8,180,f"出口标志灯中央视线另行校核。侧向探针有 {obstructed} 处遮挡，位置见计算文件。")
  return root


def build_emergency_lighting_view(context):
  view=context.scope["view"]
  if view in {"circuits","checks"}:
    root=_report(staged_json(context,STUDY),view)
  else:
    model=staged_json(context,MODEL)
    root={"hall":_hall,"site":_site_plan,"section":_section}[view](context,model,staged_json(context,STUDY))
  indent(root,space="  ")
  output=context.build_path/context.output
  output.parent.mkdir(parents=True,exist_ok=True)
  output.write_text(tostring(root,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult("pass",(artifact(context,f"emergency-lighting-{view}",output),))
