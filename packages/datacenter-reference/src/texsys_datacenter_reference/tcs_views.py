"""Issue split A/B shop coordination plans, section and verified duty diagram."""

from itertools import pairwise
from math import dist
from xml.etree.ElementTree import SubElement, indent, tostring

from texsys.targets import TargetResult

from .plans import _add, _line, _rect, _text, _xy
from .pod_layout import MODEL as PODS
from .pod_views import _base, _symbol
from .spatial_design import artifact, staged_json
from .tcs_routing import MODEL, SIDES
from .tcs_study import MODEL as STUDY


_STYLE = """
.Asupply,.Areturn,.Bsupply,.Breturn{fill:none;stroke-width:.035;stroke-linejoin:round}
.Asupply,.Areturn{stroke:#205984}
.Bsupply,.Breturn{stroke:#8a4f26}
.Areturn,.Breturn{stroke-dasharray:.105 .055}
.section-pipe{stroke-dasharray:none}
.bubble{fill:white;stroke:#4c636b;stroke-width:.024}
.pipesymbol{fill:white;stroke:#547188;stroke-width:.020}
.arrow{stroke-dasharray:none}
"""


def _sheet(bounds, title):
  svg = _base(bounds, title)
  SubElement(svg, "style").text = _STYLE
  return svg


def _arrow(svg, a, b, style, size):
  dx, dy = b[0]-a[0], b[1]-a[1]
  length = dist(a[:2], b[:2])
  if length < 5*size:
    return
  dx, dy = dx/length, dy/length
  x, y = (a[0]+b[0])/2, (a[1]+b[1])/2
  _add(svg, "path", d=f"M{x-size*(dx+dy*.6):g},{-y+size*(dy-dx*.6):g} "
                     f"L{x:g},{-y:g} L{x-size*(dx-dy*.6):g},{-y+size*(dy+dx*.6):g}",
       **{"class": f"{style} arrow"})


def _plan(pod, layout, system, side):
  length, width, _ = layout["size_m"]
  svg = _sheet((-.12, -.64, length+.49, width+.95), f"{pod} TCS {side} 路二次侧接管平面")
  _rect(svg, (0,0,length,width), "aisle", data_host=pod)
  for key, shape in layout["local_shell"].items():
    if key not in ("ROOF", "D-N-H", "D-S-H"):
      _rect(svg, _xy(shape), "shell", data_shell=key)
  for key, shape in layout["local_equipment"].items():
    if key.startswith("Rack") or key == system["cdu"]:
      x,y,w,h = _xy(shape)
      _rect(svg, (x,y,w,h), _symbol(shape), data_equipment=key)
      label_y = (y+h)/2
      if side == "B":
        label_y = h-.13 if y > width/2 else y+.11
      _text(svg, (x+w)/2, label_y, key.replace("Rack", "R"), .112)
  for key, row in system["pipes"].items():
    group = _add(svg, "g", data_pipe=key, data_link=row["sdl_link"] or "")
    style = side+row["leg"]
    for a,b in pairwise(row["points_m"]):
      if a[:2] != b[:2]:
        _line(group, *a[:2], *b[:2], style)
        _arrow(group, a, b, style, .125)
      else:
        _add(group, "circle", cx=a[0], cy=-a[1], r=.041, **{"class": "bubble"})
  _text(svg, 0, width+.56, f"{pod}   {side} 路二次冷却水接管平面", .166, "start")
  _text(svg, length-.02, width+.56, f"{len(system['paths']['supply'])} 台机柜", .127, "end")
  a=.15
  for index,(leg,label) in enumerate((("supply","供水"),("return","回水"))):
    x=a+index*1.42
    _line(svg, x,-.31,x+.40,-.31,side+leg)
    _text(svg,x+.51,-.35,label,.113,"start")
  _add(svg, "circle", cx=3.16, cy=.31,r=.041,**{"class":"bubble"})
  _text(svg,3.29,-.35,"竖向接管",.113,"start")
  sec = _section_x(layout)
  for y in (-.09, width+.12):
    offset = -.08 if y < 0 else .075
    _line(svg,sec-.10,y,sec+.10,y,"cutting")
    _line(svg,sec,y,sec,y+offset,"cutting")
    _text(svg,sec+.15,y-.04,"1",.125)
  return svg


def _section_x(layout):
  rack = layout["local_equipment"]["Rack03"]
  return rack["minimum_m"][0]+rack["dimensions_m"][0]/2


def _section(pod, layout, systems, materials):
  _, width,height = layout["size_m"]
  station = _section_x(layout)
  svg = _sheet((-.76, -1.03, width+1., height+.92), f"{pod} TCS 机柜顶配管横剖面")
  for key, shape in layout["local_shell"].items():
    (x, y, z), (end_x, end_y, end_z) = shape["minimum_m"], shape["maximum_m"]
    if x < station < end_x:
      _rect(svg, (y, z, end_y, end_z), "shell", data_shell=key)
  for key, shape in layout["local_equipment"].items():
    (x, y, z), (end_x, end_y, end_z) = shape["minimum_m"], shape["maximum_m"]
    if x < station < end_x:
      _rect(svg,(y, z, end_y, end_z),_symbol(shape),data_equipment=key)
      _text(svg,(y+end_y)/2, (z+end_z)/2,key,.128)
  lo,hi=layout["aisle"]["minimum_m"],layout["aisle"]["maximum_m"]
  _rect(svg,(lo[1], lo[2], hi[1], hi[2]),"aisle",data_access="headroom")
  _text(svg,(lo[1]+hi[1])/2,1.18,"检修通道",.139)
  _text(svg,(lo[1]+hi[1])/2,.84,f"{(hi[1]-lo[1])*1000:.0f} mm",.130)
  legend = {}
  for side, system in systems.items():
    for key,row in system["pipes"].items():
      for a,b in pairwise(row["points_m"]):
        if min(a[0],b[0]) < station < max(a[0],b[0]):
          center_y,center_z=a[1:]
          radius=materials[row["material"]]["envelope_m"]/2
          _add(svg,"circle",cx=center_y,cy=-center_z,r=radius,data_pipe=key,
               **{"class":f"{side}{row['leg']} section-pipe"})
          legend[side,row["leg"]]=center_z
  _text(svg,-.41,height+.62,f"1—1  {pod}  TCS 管路与机柜横剖面",.156,"start")
  _text(svg,-.41,height+.38,f"剖切 X {station:.2f} m  ·  管标高以舱内地板为基准",.119,"start")
  for i,((side,leg),z) in enumerate(sorted(legend.items())):
    x=.03+(i%2)*2.20
    yy = -.38-(i//2)*.34
    _line(svg,x,yy,x+.25,yy,side+leg)
    _text(svg,x+.32,yy-.04,f"{side} {'供' if leg=='supply' else '回'}   +{z:.3f}",.112,"start")
  return svg


def _diagram(pod, model, study):
  svg = _sheet((0,0,27,16.8), f"{pod} 独立 A/B 二次水回路及工况")
  racks = list(model["A"]["paths"]["supply"])
  for index,side in enumerate(SIDES):
    y=11.2-index*7.0
    data=study["sides"][pod][side]
    solid,broken=side+"supply",side+"return"
    group=_add(svg,"g",data_side=side)
    _rect(group,(.7,y-.8,4.9,y+.99),"cdu")
    _text(group,2.8,y+.10,model[side]["cdu"],.41)
    _text(group,2.8,y-.51,"运行" if side=="A" else "备用",.30)
    _line(group,4.9,y+.73,25.3,y+.73,solid)
    _line(group,4.9,y-.58,25.3,y-.58,broken)
    for column,name in enumerate(racks):
      x=6.5+column*2.58
      _line(group,x,y+.73,x,y+.34,solid)
      _add(group,"circle",cx=x,cy=-(y+.09),r=.25,data_rack=name,**{"class":"pipesymbol"})
      _line(group,x,y-.16,x,y-.58,broken)
      _text(group,x,y+.99,name.replace("Rack","R"),.29)
    row=data["required_duty"]
    _text(group,.7,y-1.60,
          f"{side} 路独立运行   {row['total_flow_m3_s']*1000:.2f} L/s   需压差 {row['cdu_required_kpa']:.1f} kPa"
          f"   几何储液量 {data['fluid_volume_m3']*1000:.1f} L",.41,"start")
  _text(svg,.7,15.55,"设施水在 CDU 换热   ·   机柜 A/B 接口独立",.55,"start")
  _text(svg,.7,1.12,f"两路单独承担每舱设计液侧负荷   ·   {len(study['cases'])} 项工况见机柜计算表",.44,"start")
  return svg


def build_tcs_view(context):
  model = staged_json(context, MODEL)
  pod = context.scope.get("pod", model["typical"])
  routing = model["pods"][pod]
  storage = staged_json(context,PODS)
  layout = storage["pods"][pod]
  view = context.scope["view"]
  if view == "plan":
    side = context.scope["side"]
    svg = _plan(pod,layout,routing[side],side)
  elif view == "section":
    svg = _section(pod,layout,routing,model["materials"])
  elif view == "diagram":
    svg = _diagram(pod,routing,staged_json(context,STUDY))
  else:
    raise ValueError(f"Unknown TCS view {view}.")
  output = context.build_path/context.output
  output.parent.mkdir(parents=True,exist_ok=True)
  indent(svg)
  output.write_text(tostring(svg,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult("pass",(artifact(context,f"tcs-{view}-view",output),))
