"""Compile the charging board, conduit paths, wall openings and supervised fire links."""

from itertools import combinations, pairwise

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH, bounds, space
from .electrical import MODEL as TOPOLOGY
from .emergency_lighting import MODEL as LIGHTING
from .emergency_lighting_study import _opaque, _sampling, shapes
from .exit_access import MODEL as ACCESS
from .facility_power import MODEL as FACILITY_POWER
from .fire_egress import MODEL as EGRESS
from .fire_systems import MODEL as FIRE
from .lighting_routes import Obstructions, assembled_carriers, carrier, length, plane_routes, point, simplified
from .power_routing import LAYOUT, _port
from .roof import _schedule
from .site_grading import MODEL as TERRAIN, elevation
from .site_storm import terrain_profile
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json
from .structure import _assumptions

MODEL = f"{MODELS}/lighting-distribution.json"
SITE = f"{MODELS}/site.json"


def issue(issues, suffix, keys, message):
  issues.append({"rule":f"AIDC_LIGHTING_DISTRIBUTION_{suffix}","objects":list(keys),"status":"fail","message":message})


def _record(routes, key, circuit, service, via, *, slots=(), owns=()):
  points = simplified(via)
  routes[key] = {"circuit":circuit,"service":service,"owner_objects":list(owns),"sleeves":list(slots),
                 "points_m":points,"length_m":sum(length(a,b) for a,b in pairwise(points))}


def _cabinet(config, terrain):
  x,y = config["center_xy_m"]
  sx,sy,sz = [number(v,"charging board dimension") for v in config["size_m"]]
  curb = number(config["curb_height_m"],"charging board curb")
  grade = elevation(terrain,x,y)
  bottom = grade+curb
  body = Space((x-sx/2,y-sy/2,bottom),(x+sx/2,y+sy/2,bottom+sz))
  foot = Space((x-sx/2-.045,y-sy/2-.045,grade-.12),(x+sx/2+.045,y+sy/2+.045,bottom))
  return {"id":config["id"],"body":bounds(body),"curb":bounds(foot),"grade_m":grade,
          "power_terminal_m":point((x,y-.085,bottom)),"feeder_terminal_m":point((x-.22,y-.085,bottom)),
          "signal_terminal_m":point((x+.22,y+.08,bottom))}


def _sleeve(slots, architecture, key, a, b, opening, issues):
  shape = carrier(a,b,opening)
  hosts = [(name,space(wall)) for name,wall in architecture["walls"].items() if shape.intersects(space(wall))]
  if len(hosts)!=1:
    issue(issues,"SLEEVE_HOST",[key,*[name for name,_ in hosts]],f"{key} needs exactly one exterior wall host.")
    return ()
  (host,wall), = hosts
  cut = shape.intersection(wall)
  edge = min(cut.minimum_m[1]-wall.minimum_m[1],wall.maximum_m[1]-cut.maximum_m[1],
             cut.minimum_m[2]-wall.minimum_m[2],wall.maximum_m[2]-cut.maximum_m[2])
  if edge < .05:
    issue(issues,"SLEEVE_EDGE",[key,host],f"{key} opening is within 50 mm of the wall end or header face.")
  slots[key] = {"wall":host,"fire_hours":architecture["walls"][host]["fire_hours"],"bounds":bounds(cut),
                "center_m":cut.center_m,"opening_m":opening}
  return (key,)


def _interface_routes(config,board,arch,fire,region,obstacles,slots,routes,issues):
  settings = config["layout"]
  site_z = settings["signal_depth_z_m"]
  root = point((*board["signal_terminal_m"][:2],site_z))
  ends,tails = {},{}
  west = arch["hall"]["minimum_m"][0]
  for room,row in sorted(arch["rooms"].items()):
    section = fire["rings"][room]
    panel, = (key for key,device in section["devices"].items() if device["kind"]=="panel")
    x,y,z = section["devices"][panel]["point_m"]
    fit = west+settings["signal_portal_x_inside_m"]
    centre_y = row["aisle_y_m"]+settings["signal_offset_y_m"]
    h = settings["signal_height_m"]
    out = west-.79
    a,b = point((out,centre_y,h)),point((fit,centre_y,h))
    slip = _sleeve(slots,arch,f"EL-FS-{room[-1]}",a,b,settings["sleeve_opening_m"],issues)
    ends[panel] = point((out,centre_y,site_z))
    tails[panel] = ([a,b,(fit,y,h),(fit,y,z),(x+.09,y,z)],slip)
  connected=plane_routes(root,ends,region,settings["site_step_m"],settings["site_carrier_od_m"],obstacles)
  for panel,(tail,slip) in tails.items():
    if panel not in connected:
      issue(issues,"NO_ROUTE",[panel],f"No supervised circuit route reaches {panel}.")
      continue
    _record(routes,f"EL-SIGNAL-{panel}",f"EL-SIGNAL-{panel}","signal",[board["signal_terminal_m"],*connected[panel],*tail],
            slots=slip,owns=(board["id"],panel))
  return [carrier(a,b,settings["hall_carrier_od_m"])
          for row in routes.values() if row["service"]=="signal" for a,b in pairwise(row["points_m"])
          if a[:2]==b[:2] and a[2]!=b[2] and max(a[2],b[2])>settings["power_depth_z_m"]
          and min(a[2],b[2])<settings["power_depth_z_m"]]


def _indoor_tails(config, fixtures, arch, slots, issues, obstacles):
  settings = config["layout"]
  inner = {}
  west=arch["hall"]["minimum_m"][0]
  east=arch["hall"]["maximum_m"][0]
  z=settings["hall_trunk_height_m"]
  for room,row in sorted(arch["rooms"].items()):
    y=row["aisle_y_m"]
    zone=f"H{room[-1]}"
    inner[zone]={}
    inside=(west+.18,y,z)
    outside_x=west-.20-settings["west_wall_standoff_m"]
    offset_y=y-settings["hall_riser_clear_y_m"]
    route_in=[point(p) for p in ((outside_x,offset_y,settings["power_depth_z_m"]),(outside_x,offset_y,z),(outside_x,y,z),inside)]
    slip=_sleeve(slots,arch,f"EL-PS-{room[-1]}",route_in[-2],inside,settings["sleeve_opening_m"],issues)
    placed = {key:point((*element["point_m"][:2],z)) for key,element in sorted(fixtures.items()) if element["zone"]==zone}
    found = plane_routes(inside,placed,(west+.10,y-.48,east-.075,y+.48),settings["hall_step_m"],
                         settings["hall_carrier_od_m"],obstacles)
    for key in placed:
      if key not in found:
        issue(issues,"HALL_ROUTE",[key],f"No {room} overhead route reaches {key}.")
        continue
      x,yy,h=fixtures[key]["point_m"]
      terminal=(x,yy,h+(.10 if fixtures[key]["sign"] else .08))
      inner[zone][key]=(simplified([*route_in,*found[key],terminal]),slip)
  return inner


def _outdoor_tails(config, fixtures, arch):
  settings = config["layout"]
  results = {}
  for key, lamp in sorted(fixtures.items()):
    zone=lamp["zone"]
    if zone not in {"W","E"}:
      continue
    x,y,z = lamp["point_m"]
    depth=settings["power_depth_z_m"]
    if pole:=lamp["pole"]:
      a,b,base = pole["base_m"]
      start = point((a,b,depth))
      t = [(a,b,base+0.04),(a,b,z+.06),(x,y,z+.06)]
      results[key]=(simplified([start,*t]),())
    else:
      door=key.split("-HEAD",1)[0].removeprefix("EL-")
      center=arch["doors"][door]["inside_m"][1]
      offset=(1 if y>center else -1)*settings["head_clear_y_m"]
      h=settings["head_tail_height_m"]
      a,b = x,center+offset
      results[key]=(simplified([(a,b,depth),(a,b,h),(x,y,h),(x,y,z+.08)]),())
  return results


def _power_routes(context, config, arch, board, fixtures, base, obstructions, slots, routes, issues):
  settings = config["layout"]
  model=staged_json(context,LAYOUT)
  topology=staged_json(context,TOPOLOGY)
  existing=staged_json(context,FACILITY_POWER)["circuits"]
  source=config["supply_port"]
  owner,_=source.split(".",1)
  outgoing,normal=_port(model["objects"],source)
  if owner!=topology["trains"]["A"]["shared_bus"] or any(row["from"]==source for row in existing.values()):
    issue(issues,"SOURCE",[source],f"{source} must be a separate installed opening on the auxiliary transfer.")
  inputs={row["train"]:key for key,row in existing.items() if row["terminal"]==owner and row["kind"]=="aux"}
  if set(inputs)!={"A","B"}:
    issue(issues,"SOURCE",[owner],f"{owner} lacks the two installed input paths.")
  stub=point(tuple(a+.39*b for a,b in zip(outgoing,normal,strict=True)))
  z=settings["power_depth_z_m"]
  root=point((*board["power_terminal_m"][:2],z))
  tails=_outdoor_tails(config,fixtures,arch)
  for local in _indoor_tails(config,fixtures,arch,slots,issues,obstructions["hall"]).values():
    tails.update(local)
  ends={key:tail[0] for key,(tail,_) in tails.items()}
  connected=plane_routes(root,ends,base,settings["site_step_m"],settings["site_carrier_od_m"],obstructions["site"],
                         reserve_risers=True)
  deep=settings["feeder_depth_z_m"]
  # Rise at the cabinet through the common power riser but place the feeder
  # below the existing facility-bank crossings along the short auxiliary yard.
  charging_entry=point((*board["feeder_terminal_m"][:2],deep))
  feeder=plane_routes(point((*stub[:2],deep)),{"EL-FEED":charging_entry},
    (min(stub[0],root[0])-3.6,min(stub[1],root[1])-1.3,max(stub[0],root[0])+3.9,max(stub[1],root[1])+1.1),
    settings["feeder_step_m"],settings["site_carrier_od_m"],obstructions["site"])
  if "EL-FEED" in feeder:
    _record(routes,"EL-FEED","EL-FEED","power",[outgoing,stub,*feeder["EL-FEED"],
                                                       board["feeder_terminal_m"]],owns=(owner,board["id"]))
  else:
    issue(issues,"NO_ROUTE",[source,board["id"]],f"Route from {source} to {board['id']} is unavailable.")
  for lamp,(tail,slip) in sorted(tails.items()):
    if lamp not in connected:
      issue(issues,"NO_ROUTE",[lamp],f"No charging route reaches {lamp}.")
      continue
    fixture=fixtures[lamp]
    hosts=[f"{lamp}/{part}" for part in shapes(fixture)]
    _record(routes,lamp,fixture["normal_charge_branch"],"power",[board["power_terminal_m"],*connected[lamp],*tail],
            slots=slip,owns=(board["id"],*hosts))
  return {"port":source,"point_m":outgoing,"input_links":inputs,"auxiliary_budget_kw":topology["facility"]["auxiliary_kw"]}


def _diameter(a,b,settings,board):
  if a[2]==b[2]<0:
    return settings["site_carrier_od_m"]
  if a[:2]==b[:2] and tuple(a[:2])==board["power_terminal_m"][:2]:
    return settings["board_riser_od_m"]
  return settings["hall_carrier_od_m"]


def _check_route_geometry(routes,obstructions,terrain,slots,settings,board,issues):
  site_od=settings["site_carrier_od_m"]
  cover_min=settings["minimum_cover_m"]
  cover_cache={}
  collisions=set()
  for name,row in sorted(routes.items()):
    for a,b in pairwise(row["points_m"]):
      buried=a[2]==b[2]<0
      shape=carrier(a,b,_diameter(a,b,settings,board))
      sleeve_hosts=[]
      for key in row["sleeves"]:
        slot=slots[key]
        walls=obstructions.volumes[slot["wall"]]
        crossing=shape.intersection(walls)
        if crossing and space(slot["bounds"]).contains_space(crossing):
          sleeve_hosts.append(slot["wall"])
      for key in obstructions.collisions(shape,(*sleeve_hosts,*row["owner_objects"])):
        if (name,key) not in collisions:
          collisions.add((name,key))
          issue(issues,"CLASH",[name,key],f"{name} conduit intersects {key} near {a}.")
      if buried and (a,b) not in cover_cache:
        axis=0 if a[0]!=b[0] else 1
        samples=[]
        for offset in (-site_od/2,0.,site_od/2):
          p,q=list(a),list(b)
          p[1-axis]+=offset
          q[1-axis]+=offset
          samples.extend(g for _,g,_ in terrain_profile(terrain,p,q))
        cover_cache[a,b]=min(samples)-shape.maximum_m[2]
        if cover_cache[a,b]<cover_min:
          issue(issues,"COVER",[name],f"Minimum cover below candidate threshold at {a}: {cover_cache[a,b]:.3f} m.")
  return min(cover_cache.values(),default=None)


def _route_junctions(carriers,settings,board,issues):
  segments={}
  junctions={}
  possible=[]
  for key,row in carriers.items():
    a,b=row["points_m"]
    axis, = (i for i in range(3) if a[i]!=b[i])
    outside=_diameter(a,b,settings,board)
    outline=carrier(a,b,outside)
    row["outside_m"]=outside
    row["envelope"]=bounds(outline)
    segments[key]=(axis,a,b,outline)
  for (first,(i,a,b,p)),(second,(j,c,d,q)) in combinations(segments.items(),2):
    common=carriers[first]["service"]==carriers[second]["service"]
    if i!=j and common and a[3-i-j]==c[3-i-j] and a[i]<=c[i]<=b[i] and c[j]<=a[j]<=d[j]:
      crossing=list(a)
      crossing[i]=c[i]
      junctions.setdefault(point(crossing),set()).update((first,second))
    elif overlap:=p.intersection(q):
      possible.append((first,second,overlap,common))
  fittings={}
  stations={name:[(coordinate,members) for coordinate,members in junctions.items() if name in members]
            for name in segments}
  for first,second,box,common in possible:
    # Two short offsets into the same tee can share a physical fitting. Both
    # sockets must lie on the same intervening run and the overlap has to fit
    # inside the local junction enclosure. Long parallel overlaps are rejected.
    valid=[]
    if common:
      for a,aa in stations[first]:
        for b,bb in stations[second]:
          bridge=aa&bb
          if not bridge or not 0<length(a,b)<=.10:
            continue
          fitting=Space(tuple(min(u,v)-.055 for u,v in zip(a,b,strict=True)),
                        tuple(max(u,v)+.055 for u,v in zip(a,b,strict=True)))
          if fitting.contains_space(box):
            valid.append(fitting)
    if valid:
      fittings[f"EL-T{len(fittings)+1:03}"]={"carriers":[first,second],"bounds":bounds(valid[0])}
    else:
      issue(issues,"CARRIER_SEPARATION",[first,second],f"{first} and {second} overlap outside a shared service fitting.")
  nodes={f"EL-J{index:03}":{"point_m":key,"carriers":sorted(items)}
         for index,(key,items) in enumerate(sorted(junctions.items()),1)}
  return nodes,fittings


def _feeder_crossings(feed,facility,settings,board,issues):
  criterion=facility["assumptions"]["study"]["minimum_ab_crossing_clearance_m"]
  crossings={}
  if not feed:
    return crossings
  for a,b in pairwise(feed["points_m"]):
    if a[2]!=b[2] or a[2]>=0:
      continue
    subway=carrier(a,b,_diameter(a,b,settings,board))
    for key,row in facility["segments"].items():
      bank=space(row["envelope"])
      if bank.maximum_m[2]>0 or any(min(a1,b1)<=max(a0,b0) for a0,a1,b0,b1 in
          zip(subway.minimum_m[:2],subway.maximum_m[:2],bank.minimum_m[:2],bank.maximum_m[:2],strict=True)):
        continue
      clear=max(bank.minimum_m[2]-subway.maximum_m[2],subway.minimum_m[2]-bank.maximum_m[2])
      old=crossings.get(key)
      if old and old["clearance_m"]<=clear:
        continue
      crossings[key]={"clearance_m":clear,"required_m":criterion,"feeder_points_m":[a,b],
                      "bank_frame":row["envelope"]}
      if clear<criterion:
        issue(issues,"CROSSING_CLEARANCE",["EL-FEED",key],
              f"EL-FEED clears {key} by {clear:.3f} m against the yard crossing criterion of {criterion:g} m.")
  return crossings


def build_lighting_distribution(context):
  source=source_path(context,"lighting-distribution.json")
  if not source.is_file():
    return missing_input(context,source,"lighting-distribution")
  config=_assumptions(source)
  layout=config["layout"]
  if not layout["feeder_depth_z_m"]<layout["signal_depth_z_m"]<layout["power_depth_z_m"]<0:
    raise ValueError("Buried branches, signal and feeder require distinct installation depths.")
  arch,access,terrain,light,fire,site,egress=[staged_json(context,path) for path in (ARCH,ACCESS,TERRAIN,LIGHTING,FIRE,SITE,EGRESS)]
  board=_cabinet(config["cabinet"],terrain)
  fixtures=light["fixtures"]
  solid=_opaque(context,arch,access)
  solid.update({f"{key}/{part}":shape for key,row in fixtures.items() for part,shape in shapes(row).items()})
  _,reserved=_sampling(arch,egress,access,8.)
  solid.update({f"clearance:{key}":shape for key,shape in reserved.items()})
  # Keep field-serviceable conduits outside the hall foundation footprint.
  # They enter the building only through their above-grade facade sleeves.
  low,high=arch["hall"]["minimum_m"],arch["hall"]["maximum_m"]
  solid["hall-underfloor"]=Space((*low[:2],layout["feeder_depth_z_m"]-.4),(*high[:2],0.))
  x0,y0,x1,y1=site["boundary_m"]
  dx,dy=layout["site_grid_offset_m"]
  region=(x0+dx,y0+dy,x1-.20,y1-.20)
  slot_map,routes,issues={}, {}, []
  for part in ("body","curb"):
    for other in Obstructions(solid).collisions(space(board[part])):
      issue(issues,"CABINET_CLASH",[board["id"],other],f"{board['id']} {part} intersects {other}.")
  indexed=Obstructions(solid)
  # Keep each signal riser independent of the charging carriers through the shared zone.
  signal_tails=_interface_routes(config,board,arch,fire,region,indexed,slot_map,routes,issues)
  power_solids={**solid,**{f"signal-riser-{n}":v for n,v in enumerate(signal_tails)}}
  power_index={"hall":indexed,"site":Obstructions(power_solids)}
  supply=_power_routes(context,config,arch,board,fixtures,region,power_index,slot_map,routes,issues)
  cover=_check_route_geometry(routes,indexed,terrain,slot_map,layout,board,issues)
  carriers=assembled_carriers(routes)
  nodes,fittings=_route_junctions(carriers,layout,board,issues)
  for key,row in fittings.items():
    for other in indexed.collisions(space(row["bounds"])):
      issue(issues,"FITTING_CLASH",[key,other],f"{key} fitting envelope intersects {other}.")
  crosses=_feeder_crossings(routes.get("EL-FEED"),staged_json(context,FACILITY_POWER),layout,board,issues)
  report=model_report(context,"lighting-distribution",{
    "input_digests":{p:file_digest(context.build_path/p) for p in (LAYOUT,TOPOLOGY,LIGHTING,FACILITY_POWER,ARCH,ACCESS,FIRE,TERRAIN,SITE)},
    "source_digest":file_digest(source),"assumptions":config,"supply":supply,"cabinet":board,"sleeves":slot_map,
    "routes":routes,"carriers":carriers,"junctions":nodes,"fittings":fittings,
    "feeder_crossings":crosses,"issues":issues,"minimum_cover_m":cover,
  })
  schedule=_schedule(context,"aidc-lighting-conduits.csv",
                     ("segment","medium","conduit_length_m","circuits","start_x_m","start_y_m","start_z_m",
                      "end_x_m","end_y_m","end_z_m"),
                     ((key,row["service"],row["length_m"]," ".join(row["circuits"]),*row["points_m"][0],*row["points_m"][1])
                      for key,row in carriers.items()))
  return TargetResult(report.status,(*report.artifacts,schedule),report.diagnostics)
