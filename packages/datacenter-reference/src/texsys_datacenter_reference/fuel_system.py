"""Compile generator fuel storage, dual transfer units and independent wet paths."""

from itertools import pairwise
from math import acos, dist, hypot, pi, sqrt

from pxr import Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH, bounds
from .emergency_lighting_study import _opaque
from .exit_access import MODEL as ACCESS
from .lightning import _world_bounds
from .lighting_routes import Obstructions, point
from .roof import _schedule
from .site_grading import MODEL as TERRAIN, elevation
from .spatial_design import MODELS, SCENE, missing_input, model_report, number, read_versioned, source_path, staged_json

SOURCE = "fuel-system.json"
MODEL = f"{MODELS}/fuel-system.json"
LAYOUT = f"{MODELS}/layout.json"


def issue(issues, key, objects, message):
  issues.append({"rule":f"AIDC_FUEL_{key}","objects":list(objects),"message":message,"status":"fail"})


def _box(center,size):
  return Space(tuple(v-d/2 for v,d in zip(center,size,strict=True)),
               tuple(v+d/2 for v,d in zip(center,size,strict=True)))


def _area(radius,height):
  offset = radius-height
  return radius**2*acos(offset/radius)-offset*sqrt(max(0,radius**2-offset**2))


def _profile(points):
  result = []
  for row in map(point,points):
    if not result or result[-1]!=row:
      result.append(row)
  return result


def _gravity_line(a,b,xy):
  all_xy = [a[:2],*xy,b[:2]]
  stations = [0.]
  for first,last in pairwise(all_xy):
    stations.append(stations[-1]+dist(first,last))
  length = stations[-1]
  if length<=0.:
    raise ValueError("Overflow needs a continuous horizontal stationing.")
  return [(x,y,a[2]+(b[2]-a[2])*s/length) for (x,y),s in zip(all_xy,stations,strict=True)]


def _run(config, key, kind, start, end, points, runs, *contacts):
  points = _profile(points)
  if len(points)<2:
    raise ValueError(f"{key} has no routed length.")
  for a,b in pairwise(points):
    if a[0]!=b[0] and a[1]!=b[1]:
      raise ValueError(f"{key} has a diagonal plan segment.")
  spans = [dist(a,b) for a,b in pairwise(points)]
  runs[key] = {"kind":kind, "from":start,"to":end,"points_m":points,"length_m":sum(spans),
               "fittings":max(0,len(spans)-1),"pipe":config["pipes"][kind],"contacts":list(contacts)}


def _tank(side,spec,terrain,oil,density,issues):
  tag = f"TK-{side}"
  x,y = spec["sides"][side]["tank_xy_m"]
  radius = number(spec["inner_diameter_m"],"储罐内径")/2
  length = number(spec["inner_length_m"],"储罐净段长度")
  outer = radius+number(spec["radial_jacket_m"],"双层储罐径向保护")
  end = length/2+spec["end_jacket_m"]
  z = elevation(terrain,x,y)+spec["shaft_height_m"]
  body = Space((x-end,y-outer,z-outer),(x+end,y+outer,z+outer))
  edge = spec["pad_edge_m"]
  ext = (x-end-edge,y-outer-edge,x+end+edge,y+outer+edge)
  floor,ceiling = spec["minimum_liquid_depth_m"],spec["maximum_liquid_depth_m"]
  if not 0.<floor<ceiling<2*radius:
    raise ValueError("储罐最低、最高液位须在圆截面内递增。")
  capacity = pi*radius**2*length
  volume_low,volume_high = length*_area(radius,floor),length*_area(radius,ceiling)
  samples = [elevation(terrain,a,b) for a in (ext[0],x,ext[2]) for b in (ext[1],y,ext[3])]
  top = max(samples)+spec["pad_top_above_grade_m"]
  pad = Space((*ext[:2],min(samples)-spec["pad_thickness_m"]),(*ext[2:],top))
  loads = .00980665*(spec["tank_dry_kg"]+density*volume_high)+spec["pad_unit_weight_kn_m3"]*pad.volume_m3
  bearing = loads/(pad.size_m[0]*pad.size_m[1])
  a,b,c,d = oil["extent_m"]
  if not (a<ext[0]<ext[2]<c and b<ext[1]<ext[3]<d and top<body.minimum_m[2]+.025):
    issue(issues,"TANK_SEAT",[tag],"储罐座或垫层超出防护区，或底部标高与垫层冲突。")
  if capacity*1000<spec["minimum_nominal_l"] or bearing>spec["bearing_limit_kpa"]:
    issue(issues,"TANK_CAPACITY",[tag],"储罐名义容量或基础平均压力不满足候选限值。")
  ports = {"suction":[x-end,y,z-.58],"overflow":[x+.20,y+.28,z+outer+.03],
           "fill":[x-.61,y+.48,z+outer+.04],"vent":[x+.95,y-.32,z+outer+.04]}
  legs = [[[u,v,z+sqrt(outer**2-(v-y)**2)],[u,v,w]] for u,v,w in
          (ports[key] for key in ("overflow","fill","vent"))]
  saddles = [bounds(Space((xx-.22,y-.68,top),(xx+.22,y+.68,body.minimum_m[2]+.19)))
             for xx in (x-.29*length,x+.29*length)]
  return {"body":bounds(body),"pad":bounds(pad),"saddles":saddles,"upper_nozzle_legs_m":legs,"center_m":[x,y,z],"diameter_m":outer*2,
          "length_m":end*2,"nominal_l":capacity*1000,"maximum_l":volume_high*1000,"unusable_l":volume_low*1000,
          "usable_l":(volume_high-volume_low)*1000,"centerline_m":[[x-end,y,z],[x+end,y,z]],
          "ports_m":ports,
          "minimum_surface_m":z-radius+floor,"maximum_surface_m":z-radius+ceiling,
          "full_pad_pressure_kpa":bearing,"credible_leak_l":volume_high*1000}


def _skid(side,spec,terrain):
  x,y = spec["sides"][side]["skid_xy_m"]
  z = elevation(terrain,x,y)
  return {"id":f"PB-{side}", "body":bounds(_box((x,y,z+.29),(.66,.43,.42))),
          "pumps":{f"P-{side}-{i}":bounds(_box((x+offset,y,z+.31),(.22,.30,.27)))
                   for i,offset in enumerate((-.16,.16),1)},
          "ports_m":{"in":[x-.33,y,z+.26],"out":[x,y+.215,z+.41]}}


def _exterior(config,side,host,tank,skid,runs):
  y = host["minimum_m"][1]
  lot = config["storage"]["sides"][side]
  stop = host["ports_m"]["fuel_in"]
  if host["port_normals"]["fuel_in"]!=[0,-1,0]:
    raise ValueError(f"{side} fuel handoff needs the declared south-side installation.")
  source = tank["ports_m"]["suction"]
  inlet,outlet = (skid["ports_m"][name] for name in ("in","out"))
  stem = source[0]-.23
  _run(config,f"SU-{side}","suction",f"TK-{side}",skid["id"],
       [source,[stem,source[1],source[2]],[stem,inlet[1],source[2]],[stem,inlet[1],inlet[2]],inlet],
       runs,f"TK-{side}",skid["id"])
  y_entry = y-lot["pressure_wall_offset_m"]
  z = outlet[2]
  x_feed = stop[0]
  if side=="A":
    x_outer = lot["west_pressure_x_m"]
    ys = outlet[1]+.13
    route = [[outlet[0],ys,z],[x_outer,ys,z],[x_outer,y_entry,z],[x_feed,y_entry,z]]
  else:
    route = [[x_feed,outlet[1],z],[x_feed,y_entry,z]]
  _run(config,f"TR-{side}","transfer",skid["id"],f"GenContainer{side}.fuel_in",
       [outlet,*route,[x_feed,y_entry,stop[2]],stop],runs,skid["id"],f"GenContainer{side}")
  day = config["cabinet"]
  overflow_wall = (stop[0]+day["return_wall_x_offset_m"],y,host["minimum_m"][2]+day["overflow_wall_z_m"])
  dest = tank["ports_m"]["overflow"]
  ov_y = y-lot["return_wall_offset_m"]
  if side=="A":
    x_outer = lot["west_return_x_m"]
    xy = [(overflow_wall[0],ov_y),(x_outer,ov_y),(x_outer,dest[1])]
  else:
    xy = [(overflow_wall[0],ov_y),(dest[0],ov_y)]
  _run(config,f"OV-{side}","overflow",f"OV-CAB-{side}",f"TK-{side}",_gravity_line(overflow_wall,dest,xy),
       runs,f"TK-{side}",f"GenContainer{side}")
  x,y,z = tank["ports_m"]["fill"]
  edge = tank["body"]["minimum_m"][1]-.46
  _run(config,f"FL-{side}","fill",f"FILL-{side}",f"TK-{side}",
       [(x,edge,1.17),(x,edge,z+.31),(x,y,z+.31),(x,y,z)],runs,f"TK-{side}")
  x,y,z = tank["ports_m"]["vent"]
  _run(config,f"VT-{side}","vent",f"TK-{side}",f"VENT-{side}",
       [(x,y,z),(x,y,3.5),(x,y-.16,3.5)],runs,f"TK-{side}")
  return overflow_wall


def _day_rows(config,side,host,engine_boxes,overflow_wall,runs):
  spec = config["cabinet"]
  owner = f"GenContainer{side}"
  ox,oy,oz = host["minimum_m"]
  top = oz+spec["day_bottom_m"]+spec["day_size_m"][2]
  sy = oy+spec["day_y_m"]
  start = host["ports_m"]["fuel_in"]
  header_y,header_z = oy+.66,oz+spec["transfer_header_z_m"]
  terminal = (start[0],header_y,header_z)
  _run(config,f"HDR-{side}","transfer",f"{owner}.fuel_in",f"FUEL-HDR-{side}",
       [start,(start[0],oy+.14,start[2]),(start[0],oy+.14,header_z),terminal],runs,owner)
  over_join = (overflow_wall[0],oy+.37,overflow_wall[2]+.008)
  _run(config,f"OVH-{side}","overflow",f"OVH-{side}",f"OV-CAB-{side}",[over_join,overflow_wall],runs,owner)
  days = {}
  for i,eng in enumerate(engine_boxes,1):
    tag,engine = f"DT-{side}{i}",f"{owner}/Genset{i}"
    x = eng.maximum_m[0]+spec["day_offset_from_engine_right_m"]
    bottom = top-spec["day_size_m"][2]
    dx,dy,_ = spec["day_size_m"]
    body = _box((x,sy,(bottom+top)/2),spec["day_size_m"])
    feed = (x-.23,sy,top)
    _run(config,f"fill-{tag}","transfer",f"FUEL-HDR-{side}",tag,
         [terminal,(feed[0],header_y,header_z),(feed[0],sy,header_z),feed],runs,owner,tag)
    overflow = (x+.24,body.minimum_m[1],bottom+spec["day_overflow_depth_m"])
    _run(config,f"overflow-{tag}","overflow",tag,f"OVH-{side}",
         _gravity_line(overflow,over_join,[(overflow[0],over_join[1])]),runs,owner,tag)
    engine_x = eng.center_m[0]
    face = eng.maximum_m[1]
    supply_target = (engine_x,face,eng.center_m[2]-.015)
    return_target = (engine_x+.15,face,eng.center_m[2]+.125)
    nozzle = (body.minimum_m[0],sy,bottom+.085)
    drop_x = nozzle[0]-.22
    _run(config,f"feed-{engine}","engine_supply",tag,engine,[nozzle,(drop_x,sy,nozzle[2]),
       (drop_x,sy,supply_target[2]),(engine_x,sy,supply_target[2]),supply_target],runs,owner,tag,engine)
    tail = (x+.18,sy,top)
    back = sy+.45
    above = top+.22
    _run(config,f"return-{engine}","engine_return",engine,tag,[return_target,(return_target[0],back,return_target[2]),
       (tail[0],back,return_target[2]),(tail[0],back,above),(tail[0],sy,above),tail],runs,owner,tag,engine)
    area = dx*dy
    days[tag] = {"body":bounds(body),"engine_body":bounds(eng),"engine":engine,"engine_supply_m":supply_target,"engine_return_m":return_target,
                 "gross_l":area*spec["day_size_m"][2]*1000,"levels_m":{
                   name:bottom+spec[f"day_{name}_depth_m"] for name in ("low","start","stop","high_high","overflow")},
                 "area_m2":area,"host":owner}
    if not Space(tuple(host["minimum_m"]),tuple(host["maximum_m"])).contains_space(body):
      raise ValueError(f"{tag}: candidate day tank leaves the declared module envelope.")
  levels = [spec[f"day_{key}_depth_m"] for key in ("low","start","stop","high_high","overflow")]
  if sorted(set(levels))!=levels or not 0<levels[0]<levels[-1]<spec["day_size_m"][2]:
    raise ValueError("日用箱液位开关须在有效深度内严格递增。")
  return days


def _span(a,b,outer):
  # A sloped overflow pipe is bounded conservatively by its two end elevations.
  return Space(tuple(min(x,y)-(outer/2 if x==y or axis==2 else 0) for axis,(x,y) in enumerate(zip(a,b,strict=True))),
               tuple(max(x,y)+(outer/2 if x==y or axis==2 else 0) for axis,(x,y) in enumerate(zip(a,b,strict=True))))


def _components(prim,cache):
  return {child.GetName():_world_bounds(child,cache) for child in prim.GetChildren() if child.GetName()!="Envelope"}


def _coordination(context,terrain,components,tanks,skids,days,runs,issues):
  volumes = _opaque(context,staged_json(context,ARCH),staged_json(context,ACCESS))
  volumes.update(components)
  proposed = {key:Space(tuple(row["body"]["minimum_m"]),tuple(row["body"]["maximum_m"]))
              for key,row in (tanks|skids|days).items()}
  exterior = set(tanks)|set(skids)
  oil = terrain["oil_yard"]["extent_m"]
  for key,shape in sorted(proposed.items()):
    owner = days[key]["host"] if key in days else None
    hits = [name for name,volume in sorted((volumes|proposed).items())
            if name not in (key,owner) and shape.intersects(volume)]
    if hits:
      issue(issues,"EQUIPMENT_CLASH",[key,*hits],"燃油设备与既有构件或相邻设备相交。")
    if key in exterior and not (oil[0]<shape.minimum_m[0]<shape.maximum_m[0]<oil[2]
                                  and oil[1]<shape.minimum_m[1]<shape.maximum_m[1]<oil[3]):
      issue(issues,"YARD_BOUNDARY",[key],"室外燃油设备超出事故液控制区。")
  volumes.update(proposed)
  blockers = Obstructions(volumes)
  spans = []
  for key,row in sorted(runs.items()):
    for a,b in pairwise(row["points_m"]):
      shape = _span(a,b,row["pipe"]["outside_m"])
      contacts = row["contacts"]
      bad = blockers.collisions(shape,contacts)
      if bad:
        issue(issues,"INSTALLATION_CLASH",[key,*bad],f"{key} 在 {a} 附近与安装实体相交。")
      for other,p,q,volume in spans:
        if key==other or not shape.intersects(volume):
          continue
        neighbor = runs[other]
        join = {tuple(a),tuple(b)} & {tuple(p),tuple(q)}
        nodes = {row["from"],row["to"]} & {neighbor["from"],neighbor["to"]}
        connected = join and nodes and row["kind"]==neighbor["kind"]
        if not connected:
          issue(issues,"PIPE_SEPARATION",[key,other],"两段燃油管相交，交点缺少同回路端口定义。")
      spans.append((key,a,b,shape))
  return proposed


def build_fuel_system(context):
  source = source_path(context,SOURCE)
  if not source.is_file():
    return missing_input(context,source,"fuel-system")
  config = read_versioned(source)
  if config.get("state")!="assumed" or not config.get("basis"):
    raise ValueError("燃油系统输入须声明候选状态和适用边界。")
  layout,terrain = (staged_json(context,key) for key in (LAYOUT,TERRAIN))
  oil = terrain["oil_yard"]
  storage = config["storage"]
  density = number(config["hydraulics"]["density_kg_m3"],"燃油密度")
  stage = Usd.Stage.Open(str(context.build_path/SCENE))
  if stage is None:
    raise ValueError("缺少发电机方舱安装场景。")
  cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),[UsdGeom.Tokens.default_])
  issues,tanks,skids,days,runs,components = [],{},{},{},{},{}
  for side in ("A","B"):
    owner = f"GenContainer{side}"
    host = layout["objects"][owner]
    geometry = stage.GetPrimAtPath(host["prim_path"]+"/Asset/Geometry")
    interiors = _components(geometry,cache)
    components.update({f"{owner}/{key}":shape for key,shape in interiors.items()})
    tank = _tank(side,storage,terrain,oil,density,issues)
    skid = _skid(side,storage,terrain)
    tanks[f"TK-{side}"],skids[skid["id"]] = tank,skid
    earlier = set(runs)
    overflow_wall = _exterior(config,side,host,tank,skid,runs)
    days.update(_day_rows(config,side,host,[interiors["Genset1"],interiors["Genset2"]],overflow_wall,runs))
    for key in runs.keys()-earlier:
      runs[key]["train"] = side
  gap = tanks["TK-B"]["body"]["minimum_m"][0]-tanks["TK-A"]["body"]["maximum_m"][0]
  if gap < storage["minimum_parting_m"]:
    issue(issues,"TANK_PARTING",tanks,"两列储罐实际间距低于维护候选限值。")
  for key,row in runs.items():
    pipe = row["pipe"]
    if number(pipe["inside_m"],key+" 净管径")>=number(pipe["outside_m"],key+" 管外径"):
      raise ValueError(f"{key}: 燃油管须有正壁厚。")
  exhaust = [row["ports_m"]["exhaust"] for key,row in layout["objects"].items() if key.startswith("GenContainer")]
  for key,row in runs.items():
    if row["kind"] in ("vent","fill"):
      point_m = row["points_m"][-1 if row["kind"]=="vent" else 0]
      if min(hypot(p[0]-point_m[0],p[1]-point_m[1]) for p in exhaust)<config["hydraulics"]["ignition_and_exhaust_clearance_m"]:
        issue(issues,"EXHAUST_SETBACK",[key],"通气或加油接口与仓体排烟边界的平面间距不足。")
  _coordination(context,terrain,components,tanks,skids,days,runs,issues)
  report = model_report(context,"fuel-system",{
    "source_digest":file_digest(source),"input_digests":{key:file_digest(context.build_path/key) for key in (LAYOUT,TERRAIN,SCENE)},
    "tanks":tanks,"skids":skids,"day_tanks":days,"runs":runs,"family_assumptions":config,
    "tank_gap_m":gap,"oil_yard":oil,"issues":issues,
  })
  schedule = _schedule(context,"aidc-fuel-pipe-schedule.csv",("id","kind","size","from","to","length_m","fittings"),
    ((key,row["kind"],row["pipe"]["size"],row["from"],row["to"],row["length_m"],row["fittings"])
     for key,row in sorted(runs.items())))
  return TargetResult(report.status,(*report.artifacts,schedule),report.diagnostics)
