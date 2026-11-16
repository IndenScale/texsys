"""Draw lightning plans, sections and surge circuits from the compiled installation."""

from itertools import pairwise
from math import dist
from xml.etree.ElementTree import SubElement, indent, tostring

from pxr import Gf, Sdf, Tf, Usd, UsdGeom

from texsys.targets import TargetResult

from .architecture import space
from .earthing_delivery import SCENE as EARTHING_BASE
from .exit_access_views import _root
from .lighting_distribution_views import _sheet_table
from .lightning import MODEL as STUDY, SOURCE as STUDY_SOURCE
from .lightning_roof import MODEL, SOURCE
from .plans import _add, _line, _rect, _text
from .power_routing import MODEL as POWER
from .roof import MODEL as ROOF, roof_surfaces
from .roof_views import _axes
from .site_grading import MODEL as TERRAIN, elevation
from .spatial_design import MODELS, artifact, source_path, staged_json
from .telecom_coordination import _prim

STYLE = """
.roof-field{fill:#eff2f2;stroke:#718289;stroke-width:.08}
.bay-roof{fill:none;stroke:#899697;stroke-width:.036}
.roof-grid{fill:none;stroke:#a6acad;stroke-width:.05;stroke-dasharray:.18 .12}
.roof-curb{fill:#ebdfc8;stroke:#937b50;stroke-width:.038}
.roof-cable{fill:#dfdac8;stroke:#9d6c49;stroke-width:.065}
.lps-net{fill:none;stroke:#a27318;stroke-width:.14}
.lps-down{fill:none;stroke:#9c4329;stroke-width:.12}
.lps-rod{stroke:#683d25;stroke-width:.095;fill:#faf5dc}
.lps-cut .lps-rod{stroke:#a67319;stroke-width:.11}
.lps-water{fill:#e2edf3;stroke:#57738d;stroke-width:.035}
.lps-fail{fill:#d46a47;stroke:none;opacity:.60}
.lps-low{fill:#e5be64;stroke:none;opacity:.56}
.lps-cover{fill:#d9eae1;stroke:none}
.lps-grade{stroke:#846a43;stroke-width:.045;fill:none}
.lps-datum{stroke:#7b8b95;stroke-width:.025;stroke-dasharray:.13 .09;fill:none}
"""


def _canvas(bounds,title):
  root = _root(bounds,title)
  SubElement(root,"style").text = STYLE
  return root


def _outline(svg,roof,show_openings=True):
  _rect(svg,roof["outline_xy_m"],"roof-field",data_roof="hall")
  for key,panel in sorted(roof["panels"].items()):
    _rect(svg,panel["extent_xy_m"],"bay-roof",data_panel=key)
  if show_openings:
    for key,row in sorted(roof["openings"].items()):
      _rect(svg,row["curb_xy_m"],"roof-curb",data_opening=key)


def _symbols(svg,model,study):
  for key,row in sorted(model["links"].items()):
    a,b = row["points_m"]
    _line(svg,*a[:2],*b[:2],"lps-net").set("data-lightning-conductor",key)
  for key,row in sorted(study["continuity"]["downleads"].items()):
    a,b = row["points_m"][:2]
    _line(svg,*a[:2],*b[:2],"lps-down").set("data-lightning-downlead",key)
    _rect(svg,(b[0]-.10,b[1]-.10,b[0]+.10,b[1]+.10),"lps-down")
  for key,row in sorted(model["masts"].items()):
    x,y,_ = row["base_m"]
    _add(svg,"circle",cx=x,cy=-y,r=.18,**{"class":"lps-rod"},data_air_terminal=key)
    _text(svg,x+.29,y+(.51 if y<25 else -.71),key,.34,"start")


def _plan(context,model,study):
  roof = staged_json(context,ROOF)
  a,b,c,d = roof["outline_xy_m"]
  svg = _canvas((a-2.35,b-2.22,c+2.65,d+2.65),"屋面接闪、引下线及断接卡定位")
  _outline(svg,roof)
  _axes(svg,roof)
  _symbols(svg,model,study)
  dimensions = roof["section"]
  _text(svg,a+.41,d+1.45,f"屋脊 EL +{roof_surfaces(roof,dimensions['ridge_y_m'])[-1]:.3f}",.51,"start")
  _text(svg,a+12,d+1.45,"接闪网沿主体轴线布置",.47,"start")
  _line(svg,24,25.9,24,26.38,"lps-datum")
  _text(svg,24.1,26.60,"A",.50,"start")
  return svg


def _cover(context,model,study):
  roof = staged_json(context,ROOF)
  a,b,c,d = study["coverage"]["service_volume_selection_xy_m"]
  svg = _canvas((a-2.08,b-2.55,c+2.45,d+2.46),"屋面和外露机电的滚球覆盖")
  _outline(svg,roof,show_openings=False)
  by_host = {}
  for row in study["coverage"]["tiles"]:
    by_host.setdefault(row["host"],[]).append(row)
  targets = study["coverage"]["targets"]
  for key,rows in sorted(by_host.items()):
    rects = [row["xy_m"] for row in rows]
    extent = (min(r[0] for r in rects),min(r[1] for r in rects),max(r[2] for r in rects),max(r[3] for r in rects))
    if key.startswith("FWS"):
      cls = "lps-water"
    elif key.startswith("ROOF"):
      cls = "lps-cover"
    else:
      cls = "roof-cable"
    _rect(svg,extent,cls,data_coverage=key)
    for row in rows:
      val = row["margin_m"]
      if val < .23:
        _rect(svg,row["xy_m"],"lps-low" if val>0 else "lps-fail",data_control=key)
  _axes(svg,roof)
  _symbols(svg,model,study)
  key,result = min(targets.items(),key=lambda item:item[1]["minimum_margin_m"])
  x,y,_ = result["controlling_position_m"]
  _add(svg,"circle",cx=x,cy=-y,r=.25,**{"class":"lps-rod"},data_minimum=key)
  _line(svg,x,y,x+.57,y+1.11,"lps-datum")
  _text(svg,x+.65,y+1.29,f"{key}  {result['minimum_margin_m']:+.3f} m",.46,"start")
  _text(svg,a+.62,b-1.85,f"滚球半径 {study['coverage']['sphere_radius_m']:g} m",.50,"start")
  _text(svg,a+16,b-1.85,"黄色为覆盖余量小于 0.23 m",.46,"start")
  return svg


def _section(context,model,study):
  roof = staged_json(context,ROOF)
  segment = staged_json(context,POWER)["trains"]["B"]["segments"]["B-P3"]
  rod = model["masts"]["AT-3-C"]
  down = study["continuity"]["downleads"]["DL-3-C"]
  profile = down["points_m"]
  origin = 13.2
  root = _canvas((-.60,-1.92,22.38,14.85),"北墙引下线、断接卡及接闪桅杆")
  root.set("class","lps-cut")
  y0 = roof["section"]["ridge_y_m"]
  y1 = roof["section"]["north_eave_y_m"]
  pole_x = rod["base_m"][1]-origin
  title = "A—A　3 轴北向剖面"
  _text(root,.0,14.10,title,.52,"start")
  _text(root,.0,13.09,"B 路进线按北侧相邻轴位投影",.37,"start")
  for level,cls in ((-1,"roof-field"),(0,"lps-datum")):
    _line(root,y0-origin,roof_surfaces(roof,y0)[level],y1-origin,roof_surfaces(roof,y1)[level],cls)
  _line(root,y0-origin,.4,y1-origin,.4,"lps-datum")
  terrain = staged_json(context,TERRAIN)
  x = rod["base_m"][0]
  grounds = [(y-origin,elevation(terrain,x,y)) for y in range(13,34)]
  for a,b in pairwise(grounds):
    _line(root,*a,*b,"lps-grade")
  _line(root,pole_x,rod["base_m"][2],pole_x,rod["tip_m"][2],"lps-rod").set("data-air-terminal","AT-3-C")
  _text(root,pole_x+.43,rod["tip_m"][2]-.32,f"桅尖 EL +{rod['tip_m'][2]:.2f}",.40,"start")
  for a,b in pairwise(profile):
    _line(root,a[1]-origin,a[2],b[1]-origin,b[2],"lps-down").set("data-downlead","DL-3-C")
  cap = segment["envelope"]
  _rect(root,(cap["minimum_m"][1]-origin,cap["minimum_m"][2],
              cap["maximum_m"][1]-origin,cap["maximum_m"][2]),"roof-cable",data_nearby="B-P3")
  _text(root,cap["maximum_m"][1]-origin+.47,cap["maximum_m"][2]+.06,"B-P3",.39,"start")
  pos = down["test_joint_m"]
  _rect(root,(pos[1]-origin-.075,pos[2]-.12,pos[1]-origin+.075,pos[2]+.12),"lps-rod",data_test_joint="DL-3-C")
  _line(root,pos[1]-origin+.10,pos[2],pos[1]-origin+1.64,pos[2]+.5,"lps-datum")
  _text(root,pos[1]-origin+1.78,pos[2]+.55,f"断接卡 EL +{pos[2]:.2f}",.37,"start")
  _text(root,pos[1]-origin+1.14,.34,"接 BOND-C-3-C",.36,"start")
  _text(root,pole_x+.45,9.82,f"桅杆高 {rod['tip_m'][2]-rod['base_m'][2]:.2f} m",.41,"start")
  return root


def _surge(_context,_model,study):
  rows = []
  for key,row in sorted(study["surge_protection"].items()):
    result = "pass" if all(row["checks"].values()) else "fail"
    rows.append(((key,row["class"],f"{row['impulse_ka']:g}",f"{row['local_length_m']:.2f}",
                  f"{row['installed_protection_kv']:.2f}",f"{row['withstand_kv']:.2f}"),result))
  root = _sheet_table("低压转换母线与关键出线 SPD","A/B 独立布置。上图为单列，对应柜内同一等电位参考。",
    ("保护器编号","类型","冲击 kA","引线 m","合成 kV","耐受 kV"),rows,(8,111,153,194,239,282),
    ("T1+T2 按候选 10/350 µs 校核，T2 按候选 8/20 µs 校核。",
     "保护器级间配合、上游熔断器、短路耐受及实测厂商波形仍待核证。"),table_top=92)
  def line(x1,y1,x2,y2,color="#416b86"):
    _add(root,"line",x1=x1,y1=y1,x2=x2,y2=y2,stroke=color,stroke_width=.55)
  line(18,49,289,49)
  for x,top,label in ((72,"转换母线","T1+T2"),(242,"关键出线母线","T2")):
    _add(root,"text",x=x,y=42,text_anchor="middle",font_size=4.6).text=top
    line(x,49,x,55)
    _add(root,"rect",x=x-3.4,y=55,width=6.8,height=6.9,fill="#dce5ec",stroke="#416b86",stroke_width=.49)
    _add(root,"text",x=x+6,y=61,font_size=4.5).text=label
    line(x,62,x,73,"#337e57")
  _add(root,"rect",x=137,y=45.5,width=34,height=7,fill="#e7edf2",stroke="#416b86",stroke_width=.49)
  _add(root,"text",x=154,y=50.75,text_anchor="middle",font_size=4.1).text="UPS 组"
  line(57,73,259,73,"#337e57")
  _add(root,"text",x=155,y=83,text_anchor="middle",font_size=4.4).text="柜内等电位排及 MEB"
  return root


def _checks(_context,model,study):
  rows = []
  for key,row in sorted(model["masts"].items()):
    rows.append(((key,f"{row['tip_m'][2]:+.2f}",f"{row['mass_kg']:.1f}",f"{row['shear_kn']:.2f}",f"{row['moment_kn_m']:.2f}",
      f"{row['deflection_m']*1000:.0f}"),"pass" if row["utilization"]<=1 else "fail"))
  paths = study["continuity"]["cases"]
  baseline = max(row["path_mohm"] or 0 for row in paths["normal"]["terminals"].values())
  coverage = study["coverage"]
  unattested = sum(row["not_certified_one_mast_out"] for row in coverage["targets"].values())
  return _sheet_table("接闪桅杆作用、导通与覆盖记录",
    f"接闪网 {len(model['links'])} 段，引下线 {len(paths)-1} 个单路切除工况，正常最长电阻 {baseline:.2f} mΩ。",
    ("桅杆","桅尖 EL","质量 kg","剪力 kN","弯矩 kN·m","桅尖位移 mm"),rows,(8,77,123,167,218,270),
    (f"单桅杆失效包络中，有 {unattested} 块面片须复核。完整计算范围见覆盖表。",
     "桅杆重量沿柱轴反注，接闪带重量分配到檩条。桅杆节点及实测风参数待核证。"))


def build_lightning_view(context):
  name = context.scope["view"]
  method = {"roof":_plan,"coverage":_cover,"section":_section,"surge":_surge,"checks":_checks}[name]
  svg = method(context,staged_json(context,MODEL),staged_json(context,STUDY))
  output = context.build_path/context.output
  output.parent.mkdir(parents=True,exist_ok=True)
  indent(svg,space="  ")
  output.write_text(tostring(svg,encoding="unicode")+"\n",encoding="utf-8")
  return TargetResult("pass",(artifact(context,f"lightning-{name}",output),))


def _linear(stage,path,key,a,b,width,depth,color):
  cube = UsdGeom.Cube.Define(stage,path)
  cube.CreateSizeAttr(1.)
  cube.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*[(x+y)/2 for x,y in zip(a,b,strict=True)]))
  direction = Gf.Vec3d(*[y-x for x,y in zip(a,b,strict=True)])
  quat = Gf.Rotation(Gf.Vec3d(0,0,1),direction).GetQuat()
  cube.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(quat)
  cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(width,depth,dist(a,b)))
  cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
  cube.GetPrim().CreateAttribute("texsys:identity",Sdf.ValueTypeNames.String).Set(key)


def _scene_content(stage,net,study,roof_source,study_source):
  ids = {}
  root = "/AIDC/Lightning"
  width = net["geometry_settings"]["ribbon_width_m"]
  depth = net["geometry_settings"]["ribbon_thickness_m"]
  for key,row in sorted(net["masts"].items()):
    path = f"{root}/AirTerminals/{Tf.MakeValidIdentifier(key)}"
    _prim(stage,path+"/Foot",key,space(row["pad"]),(.52,.48,.40))
    _prim(stage,path+"/Mast",key,space(row["body"]),(.85,.51,.15))
    ids[key] = {"file":roof_source,"path":"roof.mast_height_m"}
  paths = {"RoofMesh":net["links"],"Downleads":study["continuity"]["downleads"]}
  for group,routes in paths.items():
    for key,row in sorted(routes.items()):
      path = f"{root}/{group}/{Tf.MakeValidIdentifier(key)}"
      for i,(a,b) in enumerate(pairwise(row["points_m"]),1):
        _linear(stage,f"{path}/Run_{i}",key,a,b,width,depth,(.83,.43,.17))
      if group=="Downleads":
        x,y,z = row["test_joint_m"]
        _linear(stage,path+"/TestJoint",key,(x,y,z-.12),(x,y,z+.12),.13,.09,(.57,.46,.29))
      ids[key] = {"file":roof_source,"path":"roof"} if group=="RoofMesh" else {"file":study_source,"path":"downs"}
  for key,row in sorted(study["surge_protection"].items()):
    path = f"{root}/Surge/{Tf.MakeValidIdentifier(key)}"
    _prim(stage,path+"/Body",key,space(row["body"]),(.52,.36,.68))
    for kind,points in (("Local",row["local_leads_m"]),("Bond",row["equipotential_bond_m"])):
      for i,(a,b) in enumerate(pairwise(points),1):
        if a!=b:
          _linear(stage,f"{path}/{kind}_{i}",key,a,b,.02,.02,(.46,.36,.63))
    ids[key] = {"file":study_source,"path":"surge"}
  return ids


def build_lightning_scene(context):
  net,study = (staged_json(context,key) for key in (MODEL,STUDY))
  roof_source,study_source = (source_path(context,doc).relative_to(context.root).as_posix()
                             for doc in (SOURCE,STUDY_SOURCE))
  outputs = []
  for key,base in (("aidc-lightning",None),("aidc-lightning-coordinated",EARTHING_BASE)):
    if base:
      stage = Usd.Stage.Open(str(context.build_path/base))
      if stage is None:
        raise ValueError("缺少全场协调场景。")
      # This closed-roof view presents exterior coordination. Detailed indoor
      # air routes retain their geometry in the layer and their dedicated scene.
      indoor_air = stage.GetPrimAtPath("/AIDC/Air")
      if indoor_air.IsValid():
        UsdGeom.Scope.Define(stage,indoor_air.GetPath()).MakeInvisible()
      ids = dict(staged_json(context,f"{MODELS}/layout.json")["source_map"])
    else:
      stage = Usd.Stage.CreateInMemory()
      UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
      UsdGeom.SetStageMetersPerUnit(stage,1.)
      stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,"/AIDC").GetPrim())
      ids = {}
    ids.update(_scene_content(stage,net,study,roof_source,study_source))
    path = context.build_path/f"scenes/{key}.usda"
    path.parent.mkdir(parents=True,exist_ok=True)
    if not stage.Flatten(False).Export(str(path)):
      raise OSError(f"Cannot export {path.name}.")
    outputs.append(artifact(context,key,path,preview={"kind":"scene","source_map":ids}))
  return TargetResult("pass",tuple(outputs))
