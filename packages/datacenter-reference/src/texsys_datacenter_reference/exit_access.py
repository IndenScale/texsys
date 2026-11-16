"""Compile installed egress interfaces and site walking surfaces from architecture and terrain."""

from itertools import pairwise
from math import atan2, ceil, cos, hypot, radians, sin

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .architecture import MODEL as ARCH
from .roof import _schedule
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json, vector
from .structure import _assumptions

MODEL = f"{MODELS}/exit-access.json"


def issue(issues, rule, objects, message):
  issues.append({"rule": f"AIDC_EXIT_ACCESS_{rule}", "objects": objects, "message": message, "status": "fail"})


def _profile(stations, at):
  for (a, za), (b, zb) in pairwise(stations):
    if a - 1e-8 <= at <= b + 1e-8:
      return za + (zb-za)*(at-a)/(b-a)
  raise ValueError("Exit station is outside the compiled ramp profile.")


def _ramp_slab(stations, outside, direction, cy, half, depth):
  return [
    {"xy_m": [min(outside+a*direction,outside+b*direction),cy-half,
              max(outside+a*direction,outside+b*direction),cy+half],
     "west_z_m": za if direction>0 else zb, "east_z_m": zb if direction>0 else za,
     "depth_m": depth}
    for (a,za),(b,zb) in pairwise(stations)
  ]


def _ramp(name, door, settings, hardware, side):
  west = door["side"]=="west"
  direction = -1 if west else 1
  cy = door["inside_m"][1]
  source = door["landing_stations_m"]
  outside = source[0][0]
  sections = [(direction*(x-outside), z) for x,z in source]
  (_, floor), (top, _), (foot, grade), (end, _) = sections
  feet_to_turn = number(side["offset_from_ramp_foot_m"], "landing branch station")
  turn_size = number(settings["turn_space_m"], "turning space")
  turn_at = foot+feet_to_turn
  land_end = max(end, turn_at+turn_size/2+.015)
  extended = [*sections[:3],(land_end,grade)]
  deck_half = door["ramp_width_m"]/2 + number(settings["structural_edge_extension_m"],"slab extension")
  walking_half = door["ramp_width_m"]/2
  depth = number(settings["slab_depth_m"],"ramp slab depth")
  inset = number(settings["rail_outside_clear_edge_m"],"rail outside pedestrian line")
  start = number(settings["rail_start_offset_m"],"rail reveal")
  stop = foot+number(settings["rail_lower_extension_m"],"rail foot extension")
  if not 0<start<top<foot<stop<land_end:
    raise ValueError(f"{name}: inconsistent ramp rail stationing.")
  spacing = number(settings["maximum_post_spacing_m"],"rail post spacing")
  post_size = number(settings["post_diameter_m"],"rail post diameter")
  hand_size = number(settings["handrail_diameter_m"],"handrail diameter")
  guard_size = number(settings["guardrail_diameter_m"],"guardrail diameter")
  grip = tuple(number(z,"handrail height") for z in settings["grip_heights_m"])
  guard = number(settings["guardrail_height_m"],"guardrail height")
  if len(grip)!=2 or not grip[0]<grip[1]<guard or walking_half+inset+post_size/2>deck_half+1e-9:
    raise ValueError(f"{name}: rail grip levels or mounting inset are inconsistent.")
  supports = []
  breaks = [start,top,foot,stop]
  for a,b in pairwise(breaks):
    count = ceil((b-a)/spacing)
    supports.extend(a+(b-a)*i/count for i in range(count))
  supports.append(stop)
  metal = {}
  rails = {}
  for label, sign in (("N",1),("S",-1)):
    y = cy+sign*(walking_half+inset)
    for tag,height,diam in (("H1",grip[0],hand_size),("H2",grip[1],hand_size),("G",guard,guard_size)):
      points = [(outside+direction*s,y,_profile(extended,s)+height) for s in breaks]
      rails[f"{label}-{tag}"] = {"points_m":points,"diameter_m":diam,"height_m":height,"role":tag}
    for i,s in enumerate(supports,1):
      x = outside+direction*s
      base = _profile(extended,s)
      metal[f"{label}-P{i:02d}"] = {"points_m":[(x,y,base-.05),(x,y,base+guard)],
                                   "diameter_m":post_size,"role":"post"}
  curb_height = number(settings["edge_curb_height_m"],"wheel protection edge")
  curb = {
    label: _ramp_slab(breaks_for_curb,outside,direction,cy+sign*(walking_half+.033),.033,curb_height)
    for label,sign in (("N",1),("S",-1))
    for breaks_for_curb in [[(s,_profile(extended,s)+curb_height) for s in (start,top,foot)]]
  }
  aperture = door["clear_width_m"]
  leaf_count = door["leaf_count"]
  travel = aperture/leaf_count
  thickness = number(hardware["leaf_thickness_m"],"candidate leaf thickness")
  hardware_gap = number(hardware["hardware_sweep_allowance_m"],"door hardware allowance")
  degrees = number(hardware["opening_degrees"],"outward opening")
  if degrees>90:
    raise ValueError("The modeled ramp interface supports an outward opening up to 90 degrees.")
  turn_x = outside+direction*turn_at
  xmin,xmax = sorted((outside+direction*foot,outside+direction*land_end))
  angles = [radians(i*degrees/18) for i in range(19)]
  leaves = {}
  for i,sign in enumerate((-1,1)[:leaf_count],1):
    hinge = (outside,cy+sign*aperture/2)
    arc = [(hinge[0]+direction*travel*sin(a),hinge[1]-sign*travel*cos(a)) for a in angles]
    leaves[f"L{i}"] = {"hinge_xy_m":hinge,"arc_xy_m":arc,"radius_m":travel}
  furthest = min(angles[-1],atan2(travel,thickness/2))
  advance = travel*sin(furthest)+thickness/2*cos(furthest)+hardware_gap
  inward_at_stop = travel*cos(angles[-1])+thickness/2
  return {
    "door": name,"side":door["side"],"inside_m": door["inside_m"],"available":door["available"],
    "slab": _ramp_slab(extended,outside,direction,cy,deck_half,depth),
    "curbs":curb,"rails":rails,"posts":metal,"leaves":leaves,
    "leaf_height_m":door["clear_height_m"],"leaf_thickness_m":thickness,
    "width_m":door["ramp_width_m"],"slab_width_m":2*deck_half,
    "rail_clear_m":min(door["ramp_width_m"],2*(walking_half+inset)-max(post_size,hand_size,guard_size)),
    "door_open_clear_m":aperture-leaf_count*inward_at_stop,
    "leaf_sweep_radius_m":travel+hardware_gap, "leaf_sweep_forward_m":advance,
    "upper_landing_m":top,"free_at_swing_m":top-advance,"slope":door["ramp_slope"],
    "grade_foot_m":[outside+direction*foot,cy,grade],"branch_m":[turn_x,cy,grade],
    "turn_rect_m":[turn_x-turn_size/2,cy-turn_size/2,turn_x+turn_size/2,cy+turn_size/2],
    "landing_rect_m":[xmin,cy-walking_half,xmax,cy+walking_half],
    "walking_stations_m":[[outside+direction*s,cy,z] for s,z in extended],
    "standoff_m":walking_half+inset-aperture/2-max(post_size,guard_size)/2-hardware_gap-thickness/2,
  }


def _span(name, a, b, width, thickness, crossfall):
  dx,dy = b[0]-a[0],b[1]-a[1]
  if (dx==0)==(dy==0):
    raise ValueError(f"{name}: pavement segments must be nonzero and orthogonal.")
  half = width/2
  ox,oy = (half,0.) if dx==0 else (0.,half)
  dz = half*crossfall
  corners = [(a[0]-ox,a[1]-oy,a[2]+dz),(b[0]-ox,b[1]-oy,b[2]+dz),
             (b[0]+ox,b[1]+oy,b[2]-dz),(a[0]+ox,a[1]+oy,a[2]-dz)]
  length = hypot(dx,dy)
  return {"points_m": [a,b], "corners_m":corners, "width_m":width,
          "thickness_m":thickness, "area_m2":width*length, "longitudinal_slope":abs(b[2]-a[2])/length,
          "crossfall":abs(crossfall)}


def _side(name, ramps, settings, surfaces, areas):
  selected = [row for row in ramps.values() if row["side"]==name]
  station_y, = vector([settings["route_station_y_m"]],1,"assembly connection station")
  offset = number(surfaces["width_m"],"walking corridor width")
  slab = number(surfaces["thickness_m"],"pavement thickness")
  crossfall = number(surfaces["crossfall"],"assigned walkway crossfall",zero=True)
  assembly = settings["meeting"]
  area = areas[assembly]
  x,y = selected[0]["branch_m"][:2]
  if any(abs(row["branch_m"][0]-x)>1e-9 for row in selected):
    raise ValueError(f"{name}: the exit landings no longer meet their shared walking strip.")
  if min(row["branch_m"][1] for row in selected)<=station_y<=max(row["branch_m"][1] for row in selected):
    raise ValueError(f"{name}: assembly station must be beyond both door routes.")
  shift = 1 if station_y>y else -1
  selected.sort(key=lambda row:shift*row["branch_m"][1])
  z = area["elevation_m"]
  a,b,c,d = area["extent_m"]
  dock = [a if x<a else c,station_y,z]
  elbow = [x,station_y,z]
  if not b+offset/2<=station_y<=d-offset/2 or a<=x<=c:
    raise ValueError(f"{assembly}: site walkway must meet the edge of its candidate meeting area.")
  side_crossfall = -crossfall if name=="west" else crossfall
  nodes = [*[row["branch_m"] for row in selected],elbow,dock]
  site_segments, schedule = {}, []
  for index,(p,q) in enumerate(pairwise(nodes),1):
    key = f"{name.upper()}-{index:02d}"
    if index<=len(selected):
      start = [p[0],p[1]+shift*selected[index-1]["width_m"]/2,p[2]]
      stop_half = selected[index]["width_m"]/2 if index<len(selected) else offset/2
      end = [q[0],q[1]-shift*stop_half,q[2]]
      if shift*(end[1]-start[1])<=0:
        raise ValueError(f"{key}: distinct landing and pavement spans are required.")
      fall = side_crossfall
    else:
      start = [p[0]+(offset/2 if q[0]>p[0] else -offset/2),p[1],p[2]]
      end = q
      if (q[0]-p[0])*(end[0]-start[0])<=0:
        raise ValueError(f"{key}: turning pad is too close to the assembly edge.")
      fall = crossfall
    site_segments[key] = _span(key,start,end,offset,slab,fall)
    schedule.append((start,end,q))
  paths = {row["door"]: {"segments":list(site_segments)[i:], "assembly":assembly,
                         "points_m":[nodes[i],*(point for points in schedule[i:] for point in points),
                                     [(a+c)/2,station_y,z]]} for i,row in enumerate(selected)}
  return site_segments, paths, {"center_m":elbow,"width_m":offset,"thickness_m":slab}


def build_exit_access(context):
  source = source_path(context,"exit-access.json")
  if not source.is_file():
    return missing_input(context,source,"exit-access")
  config = _assumptions(source)
  arch = staged_json(context,ARCH)
  areas = {}
  for name,row in config["meeting_areas"].items():
    a,b,c,d = vector(row["extent_m"],4,f"{name} bounds")
    z, = vector([row["elevation_m"]],1,"meeting area elevation")
    if c<=a or d<=b:
      raise ValueError(f"{name}: assembly site bounds must have positive area.")
    areas[name] = {"extent_m":[a,b,c,d],"elevation_m":z,"area_m2":(c-a)*(d-b)}
  ramps = {name:_ramp(name,row,config["ramp"],config["door"],config["sides"][row["side"]])
           for name,row in arch["doors"].items()}
  walk,paths,junctions = {},{},{}
  for name,settings in config["sides"].items():
    segments,connections,junction = _side(name,ramps,settings,config["paving"],areas)
    walk.update(segments)
    paths.update(connections)
    junctions[name] = junction
  report = model_report(context,"exit-access",{
    "assumptions":config,"source_digest":file_digest(source),"architecture_digest":file_digest(context.build_path/ARCH),
    "ramps":ramps,"walkways":walk,"paths":paths,"junctions":junctions,"meeting_areas":areas,
  })
  rails = _schedule(context,"aidc-exit-hardware.csv",
                    ("door","member","role","outside_diameter_m","cut_length_m","base_x_m","base_y_m","base_z_m"),
                    ((name,key,obj["role"],obj["diameter_m"],
                      sum(hypot(*(b-a for a,b in zip(p,q,strict=True))) for p,q in pairwise(obj["points_m"])),
                      *obj["points_m"][0]) for name,ramp in ramps.items()
                     for key,obj in (ramp["rails"]|ramp["posts"]).items()))
  pavement = _schedule(context,"aidc-evacuation-pavement.csv",
                       ("segment","width_m","longitudinal_slope","crossfall","area_m2","paving_m3","start_x_m","start_y_m","start_z_m",
                        "end_x_m","end_y_m","end_z_m"),
                       ((key,row["width_m"],row["longitudinal_slope"],row["crossfall"],
                         row["area_m2"],row["area_m2"]*row["thickness_m"],
                         *row["points_m"][0],*row["points_m"][1]) for key,row in walk.items()))
  return TargetResult(report.status,(*report.artifacts,rails,pavement),report.diagnostics)
