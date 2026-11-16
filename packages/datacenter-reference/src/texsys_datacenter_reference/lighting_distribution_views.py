"""Draw coordinated charging layouts, termination details and verification schedules."""

from itertools import pairwise
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .architecture import MODEL as ARCH
from .emergency_lighting import MODEL as LIGHTING
from .emergency_lighting_views import _styles, _symbol
from .exit_access import MODEL as ACCESS
from .exit_access_views import _root, _site
from .facility_power import MODEL as FACILITY
from .lighting_distribution import MODEL
from .lighting_distribution_study import STUDY
from .plans import _add, _axes, _line, _north_and_scale, _rect, _text, _xy
from .site_grading import MODEL as TERRAIN, elevation
from .spatial_design import MODELS, artifact, staged_json

_STYLE="""
.el-charge{fill:none;stroke:#bd5819;stroke-width:.11}
.el-feeder{fill:none;stroke:#8f3727;stroke-width:.19}
.el-signal{fill:none;stroke:#5c4689;stroke-dasharray:.23 .12;stroke-width:.093}
.el-cut{fill:#c0c4bd;stroke:#505d5d;stroke-width:.036}
.el-layer{fill:#f3e9de;stroke:#915537;stroke-width:.030}
.el-board{fill:#5c7695;stroke:#293e57;stroke-width:.07}
.el-terminal{fill:#fdfbf1;stroke:#6f4e36;stroke-width:.065}
.el-datum{fill:none;stroke:#597379;stroke-dasharray:.21 .13;stroke-width:.035}
"""


def _style(root):
  _styles(root)
  SubElement(root,"style").text=_STYLE
  return root


def _conduits(root,model,predicate):
  for key,row in sorted(model["carriers"].items()):
    if not predicate(row):
      continue
    a,b=row["points_m"]
    cls="el-signal" if row["service"]=="signal" else "el-feeder" if "EL-FEED" in row["circuits"] else "el-charge"
    if a[:2]==b[:2]:
      _add(root,"circle",cx=a[0],cy=-a[1],r=.095,**{"class":"el-terminal"},data_carrier=key)
    else:
      _line(root,*a[:2],*b[:2],cls).set("data-carrier",key)


def _site_view(context,model,_study):
  root=_style(_site(context,staged_json(context,ACCESS)))
  box=model["cabinet"]
  _conduits(root,model,lambda row:any(p[2]<0 for p in row["points_m"]))
  _rect(root,_xy(box["body"]),"el-board",data_board=box["id"])
  x,y=box["body"]["maximum_m"][:2]
  _text(root,x+.6,y+.2,box["id"],.76,"start")
  a,b,_=model["supply"]["point_m"]
  _add(root,"circle",cx=a,cy=-b,r=.20,**{"class":"el-terminal"})
  _text(root,a,b+2.1,"AuxFeed",.81)
  _text(root,4,53.8,"橙线为充电，紫色虚线为消防指令，红线为主馈线",.85,"start")
  return root


def _hall_view(context,model,_study):
  arch=staged_json(context,ARCH)
  grid=staged_json(context,f"{MODELS}/grid.json")
  layout=staged_json(context,f"{MODELS}/layout.json")
  light=staged_json(context,LIGHTING)
  a,b,c,d=_xy(arch["hall"])
  root=_style(_root((a-4.75,b-7.35,c+2.60,d+3.16),"主机房应急照明充电分支"))
  _rect(root,(a,b,c,d),"hall")
  _axes(root,grid,(a,b,c,d))
  _north_and_scale(root,(a,b,c-1.25,d-.20))
  for key,row in sorted(arch["walls"].items()):
    if not key.startswith("W-over"):
      _rect(root,_xy(row),"wall-lighting",data_wall=key)
  for name,row in sorted(layout["objects"].items()):
    if name.startswith("IT"):
      x,y,u,v=_xy(row)
      _rect(root,(x,y,u,v),"pod-lighting",data_pod=name)
      _text(root,(x+u)/2,(y+v)/2,name,.40)
  _conduits(root,model,lambda row:any("EL-H" in key for key in row["circuits"]) and
            all(a-1<p[0]<c+.01 and p[2]>2.25 for p in row["points_m"]))
  for key,row in sorted(light["fixtures"].items()):
    if row["zone"].startswith("H"):
      _symbol(root,key,row,.145,label=False)
  for key,row in sorted(model["sleeves"].items()):
    _,y,_=row["center_m"]
    _rect(root,_xy(row["bounds"]),"charge-sign",data_sleeve=key)
    _line(root,a,y,a+.43,y+.30,"el-datum")
    _text(root,a+.51,y+.34,key,.41,"start")
  _text(root,a,d+1.55,"主机房两分区分设入口。预留孔及防火封堵见 E-019。",.48,"start")
  return root


def _profile_view(context,model,_study):
  active=model["routes"].get("EL-FEED")
  feed=active["points_m"] if active else [model["supply"]["point_m"],model["cabinet"]["feeder_terminal_m"]]
  yard=staged_json(context,FACILITY)
  terrain=staged_json(context,TERRAIN)
  origin=model["cabinet"]["body"]["minimum_m"][1]-.38
  x0=model["cabinet"]["power_terminal_m"][0]
  start,end=min(p[1] for p in feed),max(p[1] for p in feed)
  span=end-origin
  root=_style(_root((-.6,-2.65,max(10.,span+4.),3.02),"AuxFeed 至 EL-MDB 纵剖与交叉"))
  _text(root,.18,2.59,"沿北侧动力区纵向投影",.23,"start")
  for a,b in pairwise(feed if active else []):
    if a[1:]!=b[1:]:
      _line(root,a[1]-origin,a[2],b[1]-origin,b[2],"el-feeder")
  for tag,segment in sorted(yard["segments"].items()):
    x,y,u,v=_xy(segment["envelope"])
    z=segment["envelope"]["minimum_m"][2]
    zz=segment["envelope"]["maximum_m"][2]
    if x-.16<=x0<=u+.16 and start-.15<=y<=v<=end+.10 and zz<.02:
      _rect(root,(y-origin,z,v-origin,zz),"el-cut",data_crossing=tag)
      _text(root,(y+v)/2-origin,zz+.23,tag,.14)
  _rect(root,(model["cabinet"]["body"]["minimum_m"][1]-origin,model["cabinet"]["body"]["minimum_m"][2],
              model["cabinet"]["body"]["maximum_m"][1]-origin,model["cabinet"]["body"]["maximum_m"][2]),"el-board")
  _text(root,model["cabinet"]["power_terminal_m"][1]-origin,1.94,"EL-MDB",.20)
  _text(root,span,1.58,"AuxFeed",.20)
  for a,b in pairwise([(start-.28+i*(end-start+.56)/18) for i in range(19)]):
    _line(root,a-origin,elevation(terrain,x0,a),b-origin,elevation(terrain,x0,b),"el-datum")
  depth=model["assumptions"]["layout"]["feeder_depth_z_m"]
  _text(root,span+1.0,depth+.10,f"主馈线中心 EL {depth:+.2f} m",.17,"start")
  if model["feeder_crossings"]:
    lowest=min(row["clearance_m"] for row in model["feeder_crossings"].values())
    _text(root,span+1.0,depth+.45,f"既有动力管群最小竖向净距 {1000*lowest:.0f} mm",.17,"start")
  caption=f"主馈线展开 {active['length_m']:.2f} m。竖向交叉按编译模型核对。" if active else "主馈线路径缺失，查阅模型诊断。"
  _text(root,.18,-2.20,caption,.17,"start")
  return root


def _sleeves_view(_context,model,_study):
  root=_style(_root((-.55,-.40,4.3,2.75),"西外墙预留洞和线路隔离"))
  _text(root,0,2.55,"西外墙预留洞正视及洞口构造控制",.14,"start")
  for i,(name,row) in enumerate(sorted(model["sleeves"].items())):
    x=(i%2)*2.13
    z=1.42-(i//2)*1.34
    y_level=row["center_m"][2]
    size=row["opening_m"]
    _rect(root,(x,z-.21,x+.83,z+.55),"el-cut")
    _rect(root,(x+.415-size/2,z+.11-size/2,x+.415+size/2,z+.11+size/2),"el-layer",data_sleeve=name)
    _text(root,x+.94,z+.44,name,.12,"start")
    _text(root,x+.94,z+.25,f"中心 EL {y_level:+.2f}",.10,"start")
    _text(root,x+.94,z+.06,f"预留 {size*1000:.0f}×{size*1000:.0f}",.10,"start")
    _text(root,x+.94,z-.15,f"墙体 {row['fire_hours']:g} h",.10,"start")
  _text(root,0,-.34,"洞口由线路和墙体相交提取，封堵组件按认证体系及责任审查确定。",.09,"start")
  return root


def _sheet_table(title,subtitle,headers,rows,xs,footer,table_top=32):
  root=Element("svg",{"xmlns":"http://www.w3.org/2000/svg","viewBox":"0 0 312 190"})
  SubElement(root,"style").text="text{font-family:system-ui,sans-serif;fill:#243741}.row{stroke:#b1bdc1;stroke-width:.32}.failed{fill:#a1332b}"
  def label(x,y,data,size=4.45,anchor="start",cls=""):
    _add(root,"text",x=x,y=y,font_size=size,text_anchor=anchor,**{"class":cls}).text=str(data)
  label(5,13,title,7.1)
  label(5,25,subtitle,4.45)
  _add(root,"rect",x=5,y=table_top,width=301,height=10,fill="#dfeaf1")
  for i,heading in enumerate(headers):
    label(xs[i],table_top+7,heading,anchor="start" if i==0 else "middle")
  for n,(values,status) in enumerate(rows):
    y=table_top+18+n*8.05
    for i,value in enumerate(values):
      label(xs[i],y,value,anchor="start" if i==0 else "middle",cls="failed" if status=="fail" else "")
    _add(root,"line",x1=5,y1=y+2.65,x2=306,y2=y+2.65,**{"class":"row"})
  for n,line in enumerate(footer):
    label(6,172+n*7,line,4.1)
  return root


def _single_line(_context,model,study):
  branches=study["branches"]
  rows=[((key,row["phase"],len(row["points"]),f"{row['charge_w']:.0f}",f"{row['installed_cable_m']:.1f}",
          f"{row['maximum_drop_pct']:.3f}",f"{row['minimum_fault_a']:.0f}"),
          "pass" if all(row["protection"].values()) else "fail") for key,row in sorted(branches.items())]
  power=study["normal_distribution"]
  root=_sheet_table("应急照明配电与分支校核",
    f"{model['supply']['port']} 至 EL-MDB，400 / 230 V 候选配电，各灯具带独立蓄电。",
    ("出线","相别","灯数","W","电缆 m","最大压降 %","最小故障电流 A"),rows,(8,80,117,150,191,230,280),
    (f"总充电负荷 {power['connected_charge_w']:.0f} W，管内最大填充 {power['maximum_fill']*100:.1f}%。",
     "支路与主馈线的选择性、实测电源阻抗和实际保护整定由责任专业核证。"),table_top=76)
  def draw(x1,y1,x2,y2):
    _add(root,"line",x1=x1,y1=y1,x2=x2,y2=y2,stroke="#345472",stroke_width=.64)
  def label(x,y,value):
    _add(root,"text",x=x,y=y,font_size=3.4,text_anchor="middle").text=value
  draw(12,46,50,46)
  _add(root,"rect",x=26,y=43,width=9,height=6,fill="#e9edf3",stroke="#345472",stroke_width=.55)
  label(30.5,39,"Q0")
  draw(50,46,297,46)
  for index,key in enumerate(sorted(model["assumptions"]["electrical"]["phase_by_branch"]),1):
    x=36+index*31
    draw(x,46,x,53)
    draw(x,53,x+2.8,58.2)
    draw(x,59.2,x,64.1)
    label(x+6.5,59,"K"+key[-1])
    label(x,70,key)
  return root


def _sequence(_context,_model,study):
  labels={"fire-n":"北区火警","fire-s":"南区火警","open-supervision":"信号断路","short-supervision":"信号短路",
          "normal-feeder-loss":"正常馈线失电","alarm-with-A-contact-weld":"火警且 A 触点粘连",
          "alarm-with-B-contact-weld":"火警且 B 触点粘连","manual-test":"就地测试"}
  rows=[]
  for key,row in study["emergency_cases"].items():
    light=min(item["minimum_lx"] for item in row["zones"].values())
    rows.append(((labels[key],"有" if row["normal_supply"] else "无"," / ".join(row["energized_battery_groups"]),
                  f"{row['pickup_s']:.2f}",f"{light:.2f}","满足" if row["status"]=="pass" else "失败"),row["status"]))
  for row in study["reset_timeline"]:
    if row["elapsed_s"] in {20,40,60,70}:
      rows.append(((f"{row['elapsed_s']} s 复位工况","有" if row["normal_supply"] else "无",
                    "恢复充电" if row["charge_command"] else "保持应急","—","—","—"),"pass"))
  return _sheet_table("消防指令、失电响应及复位",
    "两条独立监督输入控制充电接触器，灯内蓄电池在充电端失电后供光。",
    ("命令或工况","正常供电","供光灯组","投入 s","最小照度 lx","候选判定"),rows,(8,116,161,208,250,289),
    ("报警、断短路均置应急，故障和火警解除后按独立复位顺序恢复。",
     "接触器、监督模块和调试动作属于候选功能架构。现场产品接口、消防联动和切换须实测。"))


def build_lighting_distribution_view(context):
  name=context.scope["view"]
  model=staged_json(context,MODEL)
  study=staged_json(context,STUDY) if name in {"single-line","sequence"} else None
  root={"site":_site_view,"hall":_hall_view,"section":_profile_view,
        "sleeves":_sleeves_view,"single-line":_single_line,"sequence":_sequence}[name](context,model,study)
  output=context.build_path/context.output
  output.parent.mkdir(parents=True,exist_ok=True)
  indent(root,space="  ")
  output.write_text(tostring(root,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult("pass",(artifact(context,f"lighting-distribution-{name}",output),))
