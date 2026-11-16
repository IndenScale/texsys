"""Publish earth-system installation drawings and OpenUSD geometry."""

from itertools import pairwise
from xml.etree.ElementTree import SubElement, indent, tostring

from pxr import Tf, Usd, UsdGeom

from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH, space
from .earthing import MODEL
from .earth_fault import STUDY
from .exit_access_views import _root
from .lighting_distribution_scene import SCENE as BASE
from .lighting_distribution_views import _sheet_table
from .lighting_routes import carrier
from .plans import _add, _axes, _line, _north_and_scale, _rect, _text, _xy
from .site_grading import MODEL as TERRAIN, elevation
from .site_storm import MODEL as STORM
from .spatial_design import MODELS, artifact, source_path, staged_json
from .telecom_coordination import _prim

SCENE = "scenes/aidc-earthing-coordinated.usda"

_STYLE = """
.earth-ring{fill:none;stroke:#217755;stroke-width:.15}
.earth-bond{fill:none;stroke:#38699a;stroke-width:.12}
.earth-source{fill:none;stroke:#a34d23;stroke-width:.12}
.earth-rod{fill:#fafbf6;stroke:#217755;stroke-width:.08}
.earth-grade{fill:none;stroke:#855c33;stroke-width:.045}
.earth-pipe{fill:#cbdceb;stroke:#537393;stroke-width:.037}
.earth-column{fill:#465863;stroke:#32444c;stroke-width:.016}
.earth-area{fill:#e8e9e5;stroke:#667875;stroke-width:.055}
.earth-cut .earth-ring,.earth-cut .earth-bond{stroke-width:.060}
.earth-cut .earth-rod{stroke-width:.038}
"""


def _canvas(bounds, title):
  root = _root(bounds,title)
  SubElement(root,"style").text = _STYLE
  return root


def _plan_context(root, context):
  arch = staged_json(context,ARCH)
  _rect(root,_xy(arch["hall"]),"hall")
  layout = staged_json(context,f"{MODELS}/layout.json")
  for key,row in sorted(layout["objects"].items()):
    if row["zone"] in {"POWER","COOLING","STANDBY"}:
      _rect(root,_xy(row),"earth-area",data_host=key)


def _routes(root, model):
  for key,row in sorted(model["links"].items()):
    cls = {"ring":"earth-ring","source":"earth-source","bar":"earth-source","steel":"earth-bond"}[row["kind"]]
    for a,b in pairwise(row["points_m"]):
      if a[:2]!=b[:2]:
        _line(root,*a[:2],*b[:2],cls).set("data-earth-link",key)
  for key,row in sorted(model["bars"].items()):
    _rect(root,_xy(row["body"]),"earth-source",data_earth_bar=key)


def _site(context,model,_study):
  site = staged_json(context,f"{MODELS}/site.json")
  a,b,c,d = site["boundary_m"]
  root = _canvas((a-3,b-4,c+4,d+3),"场区联合接地与接地极定位")
  _rect(root,site["boundary_m"],"boundary")
  _plan_context(root,context)
  _routes(root,model)
  for key,row in sorted(model["rods"].items()):
    x,y,_ = row["top_m"]
    _add(root,"circle",cx=x,cy=-y,r=.26,**{"class":"earth-rod"},data_electrode=key)
    north = y>=26
    _text(root,x,y+(-1.02 if north else 1.0),key,.72)
  for key,row in sorted(model["bars"].items()):
    x,y,_ = row["points_m"][-1]
    _text(root,x,y+1.78,key,.78)
  _text(root,24,16.9,"主机房",1.05)
  ring = model["assumptions"]["ring"]
  _text(root,44,52.6,f"铜排环网　{ring['width_mm']:g} × {ring['thickness_mm']:g} mm",.90,"start")
  _text(root,44,49.8,"接地极编号参照 E-023",.79,"start")
  _north_and_scale(root,(a,b+3.6,c,d))
  return root


def _hall(context,model,_study):
  arch = staged_json(context,ARCH)
  a,b,c,d = _xy(arch["hall"])
  x0,y0,x1,y1 = model["assumptions"]["ring"]["extent_m"]
  root = _canvas((x0-4.1,y0-3.52,x1+3.6,y1+3.4),"主机房钢柱等电位及北侧电源接线")
  _plan_context(root,context)
  _axes(root,staged_json(context,f"{MODELS}/grid.json"),(a,b,c,d))
  _routes(root,model)
  for key,row in sorted(model["steel_terminals"].items()):
    x,y,_ = row["points_m"][-1]
    _add(root,"circle",cx=x,cy=-y,r=.14,**{"class":"earth-rod"},data_steel_bond=key)
    align = "start" if x<(a+c)/2 else "end"
    _text(root,x+(.44 if align=="start" else -.44),y+(.55 if y<d-1 else -.66),key,.49,align)
  for key,row in sorted(model["bars"].items()):
    x,y,_ = row["points_m"][-1]
    _text(root,x,y+1.1,key,.57)
  grid = staged_json(context,f"{MODELS}/grid.json")
  for key in model["internal_columns_pending_joint_detail"]:
    _,ix,iy = key.split("-")
    x,y = grid["x"][ix],grid["y"][iy]
    _rect(root,(x-.12,y-.12,x+.12,y+.12),"earth-column",data_pending_bond=key)
  columns = model["internal_columns_pending_joint_detail"]
  _text(root,24,12.4,f"中轴 {len(columns)} 处柱脚及连接板待补节点",.63)
  _text(root,24,10.7,"北侧三条引线下绕雨水管，见 E-022",.59)
  _north_and_scale(root,(a+2.,max(y0+3.8,b+1.0),c+1.60,d+.55))
  return root


def _section(context,model,_study):
  terrain = staged_json(context,TERRAIN)
  storm = staged_json(context,STORM)
  steel = model["links"]["BOND-C-3-C"]
  x,y0,_ = steel["points_m"][-1]
  x_pe,y_pe,_ = steel["points_m"][0]
  rod = model["rods"]["ER-07"]
  y_pipe = storm["runs"]["ST-N01"]["points_m"][0][1]
  ppoints = storm["runs"]["ST-N01"]["points_m"]
  pipe_z = next(a[2]+(b[2]-a[2])*(x-a[0])/(b[0]-a[0]) for a,b in pairwise(ppoints)
                if a[1]==b[1] and min(a[0],b[0])<=x<=max(a[0],b[0]) and a[0]!=b[0])
  pipe_d = storm["runs"]["ST-N01"]["pipe"]["outside_m"]
  root = _canvas((-.6,-5.65,11.9,1.93),"钢柱跨雨水管与直埋接地极剖面")
  root.set("class","earth-cut")
  _text(root,-.05,1.38,"A—A　北墙 C-3-C 至接地环网",.29,"start")
  _text(root,7,1.38,"B—B　ER-07 接地极",.29,"start")
  for a,b in pairwise(steel["points_m"]):
    _line(root,a[1]-y0,a[2],b[1]-y0,b[2],"earth-bond").set("data-earth-link","BOND-C-3-C")
  _rect(root,(-.26,.40,.01,.73),"earth-column")
  _add(root,"circle",cx=y_pipe-y0,cy=-pipe_z,r=pipe_d/2,**{"class":"earth-pipe"},data_pipe="ST-N01")
  for start,end in pairwise((0.,.6,1.2,1.8,2.4,3.,3.6,4.2)):
    _line(root,start,elevation(terrain,x_pe,y0+start),end,elevation(terrain,x_pe,y0+end),"earth-grade")
  _text(root,3.25,-1.11,"ST-N01 雨水管",.21,"start")
  _line(root,y_pipe-y0+.18,pipe_z,3.20,-1.01,"frame-line")
  _text(root,.28,-2.24,f"跨管引线 EL {steel['points_m'][1][2]:+.2f}",.24,"start")
  _text(root,2.1,.63,f"至环网 y = {y_pe:.2f} m",.22,"start")
  rtop,rbase = rod["top_m"],rod["bottom_m"]
  grade = elevation(terrain,*rtop[:2])
  _line(root,7,grade,11.35,grade,"earth-grade")
  _line(root,7.4,rtop[2],9,rtop[2],"earth-ring")
  _line(root,9,rtop[2],9,rbase[2],"earth-ring").set("data-electrode","ER-07")
  _line(root,9.48,rtop[2],9.48,rbase[2],"frame-line")
  for z in (rtop[2],rbase[2]):
    _line(root,9.28,z,9.66,z,"frame-line")
  _text(root,9.72,(rtop[2]+rbase[2])/2,f"{rtop[2]-rbase[2]:.2f} m",.23,"start")
  _text(root,7.02,.47,f"地面 EL {grade:+.2f}",.21,"start")
  _text(root,7.02,-1.22,f"铜包钢极 Ø{model['assumptions']['electrodes']['diameter_m']*1000:g}",.21,"start")
  _text(root,7.02,-5.41,f"极底 EL {rbase[2]:+.2f}",.21,"start")
  return root


def _electrode_schedule(_context,model,study):
  electrodes = study["electrodes"]
  rows = []
  for name,rod in sorted(model["rods"].items()):
    x,y,z = rod["top_m"]
    case = electrodes["cases"][f"without-{name}"]
    rows.append(((name,f"{x:.2f}",f"{y:.2f}",f"{z:+.2f}",f"{rod['bottom_m'][2]:+.2f}",f"{case['resistance_ohm']:.3f}"),case["status"]))
  nominal = electrodes["cases"]["all-connected"]["resistance_ohm"]
  maximum = max(row["path_mohm"] for row in study["continuity"]["normal"]["terminals"].values())
  cuts = study["continuity"]["one_ring_segment_open"]
  return _sheet_table("接地极放样及等电位连续性",f"正常联合接地 {nominal:.3f} Ω，均匀土壤候选限值 {electrodes['limit_ohm']:.1f} Ω。",
    ("接地极","X / m","Y / m","极顶 EL","极底 EL","该极退出 Ω"),rows,(8,70,116,162,211,270),
    (f"正常等电位最远路径 {maximum:.2f} mΩ。逐段断开环网共 {len(cuts)} 个工况。",
     "均匀土壤等电位模型及现场实测分开记录。接地极编号与 E-020 对应。"))


def _fault_schedule(_context,_model,study):
  groups = {}
  label_by_mode = {("EL","lighting"):"应急照明充电",("FAC","generator"):"设施动力 / 发电机",
    ("FAC","utility"):"设施动力 / 市电",("IT","battery"):"关键配电 / 电池",
    ("IT","inverter"):"关键配电 / 逆变"}
  for key,row in sorted(study["cases"].items()):
    groups.setdefault((key.partition(":")[0],row["mode"]),[]).append(row)
  rows = []
  for (group,mode),values in sorted(groups.items()):
    low = min(row["minimum_ka"] for row in values)
    high = max(row["maximum_ka"] for row in values)
    clears = [row["duty"]["latest_clear_s"] for row in values]
    failed = sum(row["status"]!="pass" for row in values)
    rows.append(((label_by_mode[group,mode],len(values),f"{low:.3f}",f"{high:.3f}",
      f"{max(clears):.3f}" if all(t is not None for t in clears) else "未覆盖",failed),"fail" if failed else "pass"))
  root = _sheet_table("相壳故障与保护导体校核",
    "L—PE 金属返回回路。电源折算阻抗、各段长度与工况数据见计算表。",
    ("回路 / 运行模式","工况","最小 kA","最大 kA","清除 s","失败"),rows,(8,100,151,204,254,291),
    ("各回路校核包括保护拾取、分断、持续时间及保护导体热稳定。",
     "厂家接地故障曲线、上下级选择性以及源侧接地和转换极数待专业核证。"),table_top=86)
  for x1,y1,x2,y2,color in (
    (39,48,92,48,"#38699a"),(108,48,274,48,"#38699a"),
    (274,48,274,66,"#a34d23"),(274,66,39,66,"#217755"),(39,66,39,48,"#217755"),
  ):
    _add(root,"line",x1=x1,y1=y1,x2=x2,y2=y2,stroke=color,stroke_width=.65)
  _add(root,"rect",x=92,y=44.5,width=16,height=7,fill="#dfeaf1",stroke="#38699a",stroke_width=.55)
  for x,y,label in ((5,58,"电源等值"),(100,40,"主保护"),(184,43,"相导体"),(251,77,"被保护设备外壳"),(135,76,"PE 金属返回导体")):
    _add(root,"text",x=x,y=y,font_size=4.1).text=label
  return root


def build_earthing_view(context):
  name = context.scope["view"]
  render = {"site":_site,"hall":_hall,"section":_section,"electrodes":_electrode_schedule,"fault":_fault_schedule}[name]
  root = render(context,staged_json(context,MODEL),staged_json(context,STUDY))
  output = context.build_path/context.output
  output.parent.mkdir(parents=True,exist_ok=True)
  indent(root,space="  ")
  output.write_text(tostring(root,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult("pass",(artifact(context,f"earthing-{name}",output),))


def _draw_scene(stage, model, source, identities):
  root = "/AIDC/ProtectiveEarthing"
  sections = {"ring":"ring","bar":"bars","steel":"steel","source":"bars"}
  for name,row in sorted(model["links"].items()):
    color = (.20,.65,.40) if row["kind"]=="ring" else (.83,.47,.21)
    for index,(a,b) in enumerate(pairwise(row["points_m"]),1):
      shape = carrier(a,b,row["outer_m"])
      if row["kind"]=="ring":
        half = model["assumptions"]["ring"]["thickness_mm"]/2000
        shape = Space((*shape.minimum_m[:2],a[2]-half),(*shape.maximum_m[:2],a[2]+half))
      _prim(stage,f"{root}/Conductors/{Tf.MakeValidIdentifier(name)}/Part_{index}",name,shape,color)
    identities[name] = {"file":source,"path":sections[row["kind"]]}
  for name,row in sorted(model["rods"].items()):
    section = carrier(row["top_m"],row["bottom_m"],model["assumptions"]["electrodes"]["diameter_m"])
    _prim(stage,f"{root}/Electrodes/{Tf.MakeValidIdentifier(name)}",name,section,(.78,.46,.21))
    identities[name] = {"file":source,"path":"electrodes"}
  for name,row in sorted(model["bars"].items()):
    _prim(stage,f"{root}/Bars/{Tf.MakeValidIdentifier(name)}",name,space(row["body"]),(.77,.41,.22))
    identities[name] = {"file":source,"path":"bars"}


def build_earthing_scene(context):
  model = staged_json(context,MODEL)
  source = source_path(context,"earthing.json").relative_to(context.root).as_posix()
  outputs = []
  for combined in (False,True):
    layout = staged_json(context,f"{MODELS}/layout.json")
    identities = dict(layout["source_map"]) if combined else {}
    stage = Usd.Stage.Open(str(context.build_path/BASE)) if combined else Usd.Stage.CreateInMemory()
    if stage is None:
      raise ValueError(f"Cannot read the coordinated scene {BASE}.")
    if not combined:
      UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
      UsdGeom.SetStageMetersPerUnit(stage,1)
      stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,"/AIDC").GetPrim())
    _draw_scene(stage,model,source,identities)
    key = "aidc-earthing-coordinated" if combined else "aidc-earthing"
    path = context.build_path/(SCENE if combined else f"scenes/{key}.usda")
    path.parent.mkdir(parents=True,exist_ok=True)
    if not stage.Flatten(False).Export(str(path)):
      raise OSError(f"Export failed for {path.name}.")
    outputs.append(artifact(context,key,path,preview={"kind":"scene","source_map":identities}))
  return TargetResult("pass",tuple(outputs))
