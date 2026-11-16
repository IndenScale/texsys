"""Compile the TN-S protective conductors and equipotential installation."""

from itertools import pairwise
from math import dist, pi

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_electrical.bonding import conductor_resistance_mohm
from texsys_spatial import Space

from .architecture import MODEL as ARCH, bounds, space
from .emergency_lighting import MODEL as FIXTURES
from .emergency_lighting_study import _opaque, shapes
from .exit_access import MODEL as ACCESS
from .facility_power import MODEL as FACILITY
from .lighting_distribution import MODEL as LIGHTING
from .lighting_routes import Obstructions, carrier, point
from .power_routing import MODEL as POWER
from .roof import _schedule
from .site_grading import MODEL as TERRAIN
from .site_storm import terrain_profile
from .spatial_design import MODELS, missing_input, model_report, read_versioned, source_path, staged_json
from .structure import MODEL as STRUCTURE

MODEL = f"{MODELS}/earthing.json"


def issue(issues, suffix, objects, message):
  issues.append({"rule":f"AIDC_EARTHING_{suffix}", "objects":list(objects), "message":message, "status":"fail"})


def _run(config, kind, length_m, carrier_ids):
  spec = config["pe"][kind]
  return {"kind":kind, "section_mm2":spec["section_mm2"], "length_m":length_m, "carriers":carrier_ids,
          "cold_mohm":conductor_resistance_mohm(length_m,spec["section_mm2"],config["metal"]["copper_cold_ohm_mm2_m"]),
          "hot_mohm":conductor_resistance_mohm(length_m,spec["section_mm2"],config["metal"]["copper_hot_ohm_mm2_m"])}


def _route_carriers(route, carriers):
  ids = set()
  for a,b in pairwise(route["points_m"]):
    axis, = [i for i in range(3) if a[i]!=b[i]]
    low,high = sorted((a[axis],b[axis]))
    for key,row in carriers.items():
      if row["service"]!="power" or route["circuit"] not in row["circuits"]:
        continue
      u,v = row["points_m"]
      aligned = all(a[i]==u[i]==v[i] for i in range(3) if i!=axis)
      if aligned and min(high,v[axis])>max(low,u[axis]):
        ids.add(key)
  return sorted(ids)


def _installed_pe(config, power, facility, lighting, issues):
  runs, allocation = {}, {}
  critical, facility_paths, lighting_paths = {}, {}, {}
  for side, train in sorted(power["trains"].items()):
    critical[side] = {}
    for name, bus in sorted(train["busways"].items()):
      feeder = f"PE-{name}"
      runs[feeder] = _run(config,"critical_feeder",bus["length_m"],bus["segments"])
      critical[side][name] = [feeder]
      previous = 0.
      rail = []
      for pod, tap in sorted(bus["terminal_paths"].items(),key=lambda entry: entry[1]["bus_distance_m"]):
        edge = f"PE-{name}-{pod}"
        tail = f"PE-{side}-{pod}"
        length = tap["bus_distance_m"]-previous
        runs[edge] = _run(config,"critical_busbar",length,[name])
        runs[tail] = _run(config,"critical_tap",tap["length_m"],[tap["segment"]])
        rail.append(edge)
        critical[side][pod] = [feeder,*rail,tail]
        previous = tap["bus_distance_m"]
    for key, row in sorted(train["segments"].items()):
      additions = sorted(name for name, run in runs.items() if key in run["carriers"])
      extra = [config["pe"][runs[name]["kind"]]["outer_m"] for name in additions]
      fill = row["fill_fraction"]+pi/4*sum(d*d for d in extra)/(row["width_m"]*row["depth_m"])
      width = row["single_layer_width_m"]+sum(extra)
      limits = power["assumptions"]["limits"]
      allocation[key] = {"fill":fill,"limit":limits["carrier_fill_fraction"],"width_m":width,"width_limit_m":row["width_m"],"pe":additions}
  for key, row in sorted(facility["circuits"].items()):
    name = f"PE-{key}"
    runs[name] = _run(config,row["kind"],row["length_m"],row["segments"])
    facility_paths[key] = name
  for key, row in sorted(facility["segments"].items()):
    additions = [facility_paths[link] for link in row["links"]]
    diameters = [config["pe"][runs[name]["kind"]]["outer_m"] for name in additions]
    fill = row["fill_fraction"]+pi/4*sum(d*d for d in diameters)/(row["width_m"]*row["depth_m"])
    limit = facility["assumptions"]["study"]["max_fill"]
    cables = facility["assumptions"]["cables"]
    width = sum(cables[facility["circuits"][item]["kind"]]["parallel"]*
                cables[facility["circuits"][item]["kind"]]["od_m"] for item in row["links"])+sum(diameters)
    allocation[key] = {"fill":fill,"limit":limit,"width_m":width,"width_limit_m":row["width_m"],"pe":additions}
  lighting_paths["EL-MDB"] = ["PE-EL-FEED"]
  lighting_carriers = lighting["carriers"]
  for tag, route in sorted(lighting["routes"].items()):
    if route["service"]!="power":
      continue
    name = f"PE-{tag}"
    kind = "lighting_feeder" if tag=="EL-FEED" else "lighting_tap"
    runs[name] = _run(config,kind,route["length_m"],
                      _route_carriers(route,lighting_carriers))
    if tag!="EL-FEED":
      lighting_paths[tag] = ["PE-EL-FEED",name]
  # Shared branches form one installed conductor per circuit/carrier pair.
  lighting_quantities = {}
  for row in lighting_carriers.values():
    for circuit in row["circuits"]:
      if row["service"]=="power":
        lighting_quantities[circuit] = lighting_quantities.get(circuit,0.)+dist(*row["points_m"])
  for key,row in allocation.items():
    if row["fill"] > row["limit"] or row.get("width_m",0)>row.get("width_limit_m",0):
      issue(issues,"CARRIER_CAPACITY",[key,*row["pe"]],"PE 安装后的占用率或单层排布超过载体限制。")
  return runs, {"critical":critical,"facility":facility_paths,"lighting":lighting_paths}, allocation, lighting_quantities


def _steel(config, structure, ring):
  x0,y0,x1,y1 = ring
  columns = {key:row for key,row in structure["members"].items() if row["kind"]=="column"}
  bounds = (
    min(row["start_m"][0] for row in columns.values()), min(row["start_m"][1] for row in columns.values()),
    max(row["start_m"][0] for row in columns.values()), max(row["start_m"][1] for row in columns.values()),
  )
  offset = config["steel"]["base_offset_m"]
  z, top = config["ring"]["center_z_m"], config["steel"]["terminal_height_m"]
  result = {}
  for key,row in sorted(columns.items()):
    x,y,_ = row["start_m"]
    if x==bounds[0]:
      anchor,end = (x0,y,z),(x-offset,y,top)
    elif x==bounds[2]:
      anchor,end = (x1,y,z),(x+offset,y,top)
    elif y==bounds[1]:
      anchor,end = (x,y0,z),(x,y-offset,top)
    elif y==bounds[3]:
      anchor,end = (x,y1,z),(x,y+offset,top)
    else:
      continue
    if y==bounds[3] and bounds[0]<x<bounds[2]:
      deep = config["steel"]["north_crossing_z_m"]
      route = [anchor,(*anchor[:2],deep),(*end[:2],deep),end]
    else:
      route = [anchor,(*end[:2],z),end]
    result[key] = {"source_member":key,"pad":row["pad"],"points_m":route}
  return result, sorted(columns.keys()-result.keys())


def _main(config, structure, placement, terrain, obstacles, issues):
  ring = config["ring"]
  extent = ring["extent_m"]
  x0,y0,x1,y1 = extent
  z = ring["center_z_m"]
  steel, internal = _steel(config,structure,extent)
  rods = {f"ER-{i:02d}":{"top_m":point((*xy,z)),
       "bottom_m":point((*xy,z-config["electrodes"]["length_m"]))}
       for i,xy in enumerate(config["electrodes"]["top_xy_m"],1)}
  bars = {}
  heights = config["bars"]
  for side,x in sorted(heights["cabinet_lugs_m"].items()):
    source = f"PowerPath{side}"
    node = placement["objects"][source]
    y = node["minimum_m"][1]
    lead_y = y-heights["south_offset_m"]
    top = heights["terminal_height_m"]
    anchor,end = (x,y1,z),(x,lead_y,top)
    if not (node["minimum_m"][0]<=x<=node["maximum_m"][0] and node["minimum_m"][2]<=top<=node["maximum_m"][2]):
      issue(issues,"SOURCE_INTERFACE",[source],"柜体移动后指定接地端子已离开柜体表面。")
    level = ring["north_tie_b_z_m"] if side=="B" else z
    route = [anchor]
    if level!=z:
      route.append((x,y1,level))
    route.extend(((x,lead_y,level),end))
    size = heights["body_m"]
    body = Space(tuple(v-d/2 for v,d in zip(end,size,strict=True)),tuple(v+d/2 for v,d in zip(end,size,strict=True)))
    bars[f"MEB-{side}"] = {"source":source,"points_m":route,"port_m":(x,y,top),"body":bounds(body)}
    blocked = obstacles.collisions(body)
    if blocked:
      issue(issues,"BAR_CLEARANCE",[source,*blocked],"接地汇流排占用已建模的安装空间。")
  tracks = {(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z)}
  tracks.update(row["top_m"] for row in rods.values())
  tracks.update(point(row["points_m"][0]) for row in [*steel.values(),*bars.values()])
  def order(p):
    x,y,_ = p
    if y==y0 and x0<=x<=x1:
      return x-x0
    if x==x1 and y0<=y<=y1:
      return x1-x0+y-y0
    if y==y1 and x0<=x<=x1:
      return x1-x0+y1-y0+x1-x
    if x==x0 and y0<=y<=y1:
      return 2*(x1-x0)+y1-y0+y1-y
    raise ValueError(f"接地极或接线点未布置在环网上：{p}")
  stations = sorted(tracks,key=order)
  names = {p:f"R-{n:02d}" for n,p in enumerate(stations,1)}
  links = {}
  rho = config["metal"]["copper_cold_ohm_mm2_m"]
  def link(name,kind,start,end,route,section,outer,excluded=(),joint=0.):
    points = [point(p) for p in route]
    length = sum(dist(a,b) for a,b in pairwise(points))
    blocked = sorted({key for a,b in pairwise(points) for key in obstacles.collisions(carrier(a,b,outer),excluded)})
    if blocked:
      issue(issues,"COORDINATION",[name,*blocked],"接地导体或接地极与既有实体相交。")
    links[name] = {"kind":kind,"from":start,"to":end,"points_m":points,"length_m":length,
                   "section_mm2":section,"resistance_mohm":conductor_resistance_mohm(length,section,rho)+joint,
                   "outer_m":outer,"allowed_contacts":list(excluded)}
  for idx,(a,b) in enumerate(pairwise([*stations,stations[0]]),1):
    key = f"RING-{idx:02d}"
    link(key,"ring",names[a],names[b],[a,b],ring["width_mm"]*ring["thickness_mm"],ring["width_mm"]/1000)
    axis = 1 if a[0]!=b[0] else 0
    half_width = ring["width_mm"]/2000
    offset_points = [([v+offset if i==axis else v for i,v in enumerate(a)],
                      [v+offset if i==axis else v for i,v in enumerate(b)]) for offset in (-half_width,0.,half_width)]
    covers = [level-z-ring["thickness_mm"]/2000
              for p,q in offset_points for _,level,_ in terrain_profile(terrain,p,q)]
    links[key]["minimum_cover_m"] = min(covers)
    if min(covers) < ring["cover_limit_m"]:
      issue(issues,"BURIAL_COVER",[key],"接地干线覆土低于参考设计限值。")
  sp = config["pe"]["metal_bond"]["section_mm2"]
  for key,row in steel.items():
    route = row["points_m"]
    column = row["source_member"]
    pad = row["pad"]
    link(f"BOND-{key}","steel",names[point(route[0])],key,route,sp,.020,
         (f"{column}/Plate",f"{pad}/Pad",f"{pad}/Plinth"),config["steel"]["joint_mohm"])
  for key,row in bars.items():
    route = row["points_m"]
    link(f"TIE-{key}","bar",names[point(route[0])],key,route,sp,.020)
    link(f"LUG-{key}","source",key,row["source"],[route[-1],row["port_m"]],sp,.016,
         (row["source"],),heights["source_port_contact_mohm"])
  for key,row in rods.items():
    diameter = config["electrodes"]["diameter_m"]
    if obstacles.collisions(carrier(row["top_m"],row["bottom_m"],diameter)):
      issue(issues,"ROD_COORDINATION",[key],"接地极打设范围与既有实体相交。")
    row["ring_station"] = names[row["top_m"]]
  return links, rods, steel, internal, bars


def build_earthing(context):
  source = source_path(context,"earthing.json")
  if not source.is_file():
    return missing_input(context,source,"earthing")
  config = read_versioned(source)
  if config.get("state")!="assumed" or not config.get("basis"):
    raise ValueError("接地输入必须声明参考工况及设计依据。")
  digest = file_digest(source)
  structure,power,facility,lighting,terrain = (staged_json(context,key) for key in (STRUCTURE,POWER,FACILITY,LIGHTING,TERRAIN))
  placement = staged_json(context,f"{MODELS}/layout.json")
  volumes = _opaque(context,staged_json(context,ARCH),staged_json(context,ACCESS))
  volumes.update({f"EL/{key}":space(row["envelope"]) for key,row in lighting["carriers"].items()})
  fixtures = staged_json(context,FIXTURES)
  volumes.update({f"{key}/{part}":shape for key,row in fixtures["fixtures"].items()
                  for part,shape in shapes(row).items()})
  issues = []
  links, rods, steel, internal, bars = _main(config,structure,placement,terrain,Obstructions(volumes),issues)
  runs, paths, allocation, lighting_quantities = _installed_pe(config,power,facility,lighting,issues)
  result = model_report(context,"earthing",{
    "source_digest":digest, "input_digests":{key:file_digest(context.build_path/key)
      for key in (STRUCTURE,POWER,FACILITY,LIGHTING,FIXTURES,TERRAIN,f"{MODELS}/layout.json")},
    "assumptions":config,"links":links,"rods":rods,"steel_terminals":steel,
    "internal_columns_pending_joint_detail":internal,"bars":bars,"pe_runs":runs,"paths":paths,
    "carrier_allocation":allocation,"lighting_installed_conductor_m":lighting_quantities,"issues":issues,
  })
  schedule = _schedule(context,"aidc-earthing-conductors.csv",
    ("id","kind","length_m","section_mm2","first_node","last_node","carrier_ids"),
    [(key,row["kind"],row["length_m"],row["section_mm2"],row["from"],row["to"],"") for key,row in sorted(links.items())]+
    [(key,row["kind"],row["length_m"],row["section_mm2"],"",""," ".join(row["carriers"]))
     for key,row in sorted(runs.items()) if row["kind"] not in ("lighting_feeder","lighting_tap")]+
    [(key,"lighting_integrated_pe",value,config["pe"]["lighting_feeder" if key=="EL-FEED" else "lighting_tap"]["section_mm2"],"","","")
     for key,value in sorted(lighting_quantities.items())])
  return TargetResult(result.status,(*result.artifacts,schedule),result.diagnostics)
