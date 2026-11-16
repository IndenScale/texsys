"""Verify physical exit hardware, site travel continuity and assembly capacity."""

from itertools import pairwise, product
from math import dist, hypot

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH, space
from .cw_network import MODEL as CW, _segments
from .equipment_foundation_coordination import MODEL as FOUNDATIONS, _occupied
from .exit_access import MODEL, issue
from .facility_power import MODEL as POWER
from .fire_egress import MODEL as EGRESS, _swept
from .fire_systems_coordination import _obstacles
from .roof import _schedule
from .site_grading import MODEL as TERRAIN, elevation
from .site_storm import terrain_profile
from .spatial_design import MODELS, model_report, number, staged_json

STUDY = f"{MODELS}/exit-access-study.json"


def _contains(outer,inner):
  a,b,c,d = outer
  x,y,u,v = inner
  return a-1e-9<=x<u<=c+1e-9 and b-1e-9<=y<v<=d+1e-9


def _rect_gap(one,other):
  a,b,c,d = one
  x,y,u,v = other
  return hypot(max(a-u,x-c,0.),max(b-v,y-d,0.))


def _covered(rect, surfaces):
  """Verify the rectangular walking envelope against the union of pavement support."""
  a,b,c,d = rect
  relevant = [row for row in surfaces if _rect_gap(rect,row)<1e-9]
  xs = sorted({a,c,*(x for row in relevant for x in row[::2] if a+1e-9<x<c-1e-9)})
  ys = sorted({b,d,*(y for row in relevant for y in row[1::2] if b+1e-9<y<d-1e-9)})
  return all(u-x<1e-9 or v-y<1e-9 or any(lo-1e-9<=(x+u)/2<=hi+1e-9 and south-1e-9<=(y+v)/2<=north+1e-9
             for lo,south,hi,north in relevant) for (x,u),(y,v) in product(pairwise(xs),pairwise(ys)))


def _flat(rect,z):
  a,b,c,d = rect
  return [(a,b,z),(c,b,z),(c,d,z),(a,d,z)]


def _footprint(quad):
  a,b = (min(pt[i] for pt in quad) for i in (0,1))
  c,d = (max(pt[i] for pt in quad) for i in (0,1))
  return a,b,c,d


def _surface_offsets(terrain,quad):
  rect = _footprint(quad)
  a,b,c,d = rect
  origin,forward,_,across = quad
  dimension = int(origin[0]==forward[0])
  def heights(x,y):
    uv = (x,y)
    s = (uv[dimension]-origin[dimension])/(forward[dimension]-origin[dimension])
    t = (uv[1-dimension]-origin[1-dimension])/(across[1-dimension]-origin[1-dimension])
    return origin[2]+s*(forward[2]-origin[2])+t*(across[2]-origin[2])
  offsets = [z-ground for first,last in zip(quad,(*quad[1:],quad[0]),strict=True)
             for _,ground,z in terrain_profile(terrain,first,last)]
  field = terrain["surface"]
  offsets.extend(heights(x,y)-elevation(terrain,x,y) for x,y in product(field["x_m"],field["y_m"])
                 if a<x<c and b<y<d)
  return [min(offsets),max(offsets)]


def _hardware(row,arch,site,settings,height,width,issues):
  tag = row["door"]
  ramp = settings["ramp"]
  hardware = settings["door"]
  post_spacing = max(dist(a["points_m"][0][:2],b["points_m"][0][:2])
                     for a,b in pairwise(post for key,post in row["posts"].items() if key.startswith("N-")))
  metrics = {
    "usable":row["available"],
    "opening-clear":row["door_open_clear_m"]>=width,
    "finished-width":row["rail_clear_m"]>=arch["clear_width_m"]-1e-9,
    "leaf-to-rail":row["standoff_m"]>=-1e-9,
    "clear-beyond-swing":row["free_at_swing_m"]>=number(hardware["minimum_clear_landing_beyond_leaf_m"],
                                                      "candidate landing beyond door leaf"),
    "turn-envelope":_contains(row["landing_rect_m"],row["turn_rect_m"]),
    "on-parcel":all(_contains(site,panel["xy_m"]) for panel in row["slab"]),
    "rail-post-spacing":post_spacing<=number(ramp["maximum_post_spacing_m"],"design post spacing")+1e-9,
    "opening-headroom":arch["clear_height_m"]>=height,
  }
  for rule,value in metrics.items():
    if not value:
      issue(issues,"HARDWARE",[tag,rule],f"{tag}: installed exit check {rule} is outside the reference criteria.")
  return {"checks":metrics,"post_spacing_m":post_spacing,"standoff_m":row["standoff_m"],
          "open_width_m":row["door_open_clear_m"],"free_landing_m":row["free_at_swing_m"]}


def _rail_obstacles(model):
  volumes = {}
  for name,row in model["ramps"].items():
    for member,data in (row["rails"]|row["posts"]).items():
      radius = data["diameter_m"]/2
      for i,(p,q) in enumerate(pairwise(data["points_m"]),1):
        low = tuple(min(a,b)-radius for a,b in zip(p,q,strict=True))
        high = tuple(max(a,b)+radius for a,b in zip(p,q,strict=True))
        volumes[f"{name}/{member}/{i}"] = Space(low,high)
  return volumes


def _site_geometry(model,arch,site,terrain,context,occupancy,issues):
  cfg = model["assumptions"]
  bounds = site["boundary_m"]
  paving,criteria = (cfg[key] for key in ("paving","criteria"))
  max_fill = number(paving["maximum_terrain_adjustment_m"],"maximum paving cut or fill")
  obstacles = _obstacles(context,arch,reserved_clearance=False)
  power = staged_json(context,POWER)
  obstacles.update({key:space(row["envelope"]) for key,row in power["segments"].items()})
  obstacles.update({f"{key}/{n}":volume for key,n,volume in _segments(staged_json(context,CW))})
  obstacles.update({key:_occupied(row) for key,row in staged_json(context,FOUNDATIONS)["foundations"].items()})
  # Drain covers may only sit within the clear reserved route after their actual levels and slip resistance are known.
  for key,row in terrain["inlets"].items():
    x,y = row["xy_m"]
    z = row["rim_m"]
    obstacles[key] = Space((x-.22,y-.22,z-.02),(x+.22,y+.22,z+.09))
  obstacles.update(_rail_obstacles(model))
  segment_report,assembly_report,ramp_ties = {},{},{}
  for key,row in model["walkways"].items():
    quad = row["corners_m"]
    extent = _footprint(quad)
    if not _contains(bounds,extent):
      issue(issues,"PARCEL",[key],f"{key}: walkway lies outside the project parcel.")
      continue
    adjusted = _surface_offsets(terrain,quad)
    if max(map(abs,adjusted))>max_fill+1e-9:
      issue(issues,"FORMATION",[key],f"{key}: pavement cut or fill {adjusted} exceeds the declared limits.")
    if (row["longitudinal_slope"]>number(paving["maximum_longitudinal_slope"],"site longitudinal grade")
        or row["crossfall"]>number(paving["maximum_crossfall"],"maximum site crossfall")):
      issue(issues,"GRADE",[key],f"{key}: proposed walkway gradient exceeds the design envelope.")
    # Abutting lower landing, elbow and assembly edges are at the local centre-line elevation.
    step = row["width_m"]*row["crossfall"]/2
    if step>number(paving["maximum_joint_step_m"],"maximum pavement step")+1e-9:
      issue(issues,"JOINT_STEP",[key],f"{key}: pavement crossfall produces an excessive level change at a joint.")
    segment_report[key] = {"cut_fill_range_m":adjusted,"finished_slope":row["longitudinal_slope"],
                           "crossfall":row["crossfall"],"maximum_joint_step_m":step}
  for key,row in model["ramps"].items():
    land = row["landing_rect_m"]
    if not _contains(bounds,land):
      continue
    sample = _surface_offsets(terrain,_flat(land,row["branch_m"][2]))
    if max(map(abs,sample))>max_fill+1e-9:
      issue(issues,"FORMATION",[key],f"{key}: extended turning landing exceeds the declared site cut or fill.")
    a,b,c,d = row["turn_rect_m"]
    z = row["branch_m"][2]
    reserve = Space((a,b,z),(c,d,z+arch["egress"]["minimum_clear_height_m"]))
    blocked = sorted(name for name,volume in obstacles.items() if reserve.intersects(volume))
    if blocked:
      issue(issues,"TURN_CLASH",[key,*blocked],f"{key}: the landing turning space has a physical obstruction.")
    ramp_ties[key] = sample
  layout = staged_json(context,f"{MODELS}/layout.json")
  occupancy_area = occupancy*number(criteria["area_per_person_m2"],"candidate assembly capacity per person")
  clearance = number(criteria["assembly_clearance_m"],"candidate assembly clearance")
  hazard = {name:[*row["minimum_m"][:2],*row["maximum_m"][:2]]
            for name,row in layout["objects"].items() if row["zone"] in {"POWER","COOLING","STANDBY"}}
  hazard["IT-HALL"] = [*arch["hall"]["minimum_m"][:2],*arch["hall"]["maximum_m"][:2]]
  for key,row in model["meeting_areas"].items():
    extent = row["extent_m"]
    z = row["elevation_m"]
    if not _contains(bounds,extent):
      issue(issues,"PARCEL",[key],f"{key}: the assembly envelope crosses the project parcel.")
      continue
    sample = _surface_offsets(terrain,_flat(extent,z))
    if max(map(abs,sample))>max_fill+1e-9:
      issue(issues,"FORMATION",[key],f"{key}: assembly platform needs cut or fill beyond the declared limit.")
    a,b,c,d = extent
    occupied = Space((a,b,z),(c,d,z+arch["egress"]["minimum_clear_height_m"]))
    blocking = sorted(name for name,volume in obstacles.items() if occupied.intersects(volume))
    if blocking:
      issue(issues,"ASSEMBLY_CLASH",[key,*blocking],f"{key}: the reserved assembly area has physical obstructions.")
    nearest = min(hazard,key=lambda name:_rect_gap(extent,hazard[name]))
    separation = _rect_gap(extent,hazard[nearest])
    if separation<clearance:
      issue(issues,"ASSEMBLY_SEPARATION",[key,nearest],f"{key}: separation {separation:.2f} m is below the candidate limit.")
    if row["area_m2"]+1e-9<occupancy_area:
      issue(issues,"ASSEMBLY_CAPACITY",[key],f"{key}: {row['area_m2']:.2f} m² does not hold {occupancy} modeled occupants.")
    assembly_report[key] = {"area_m2":row["area_m2"],"required_area_m2":occupancy_area,"occupied_count":occupancy,
                            "nearest_hazard":nearest,"clearance_m":separation,"cut_fill_range_m":sample}
  return segment_report,assembly_report,ramp_ties,obstacles


def _site_routes(model,arch,egress,site,obstacles,issues):
  height = arch["egress"]["minimum_clear_height_m"]
  width = number(model["assumptions"]["paving"]["width_m"],"clear site walking width")
  limit = number(model["assumptions"]["criteria"]["maximum_route_to_assembly_m"],"modeled total evacuation path limit")
  surfaces = [row["landing_rect_m"] for row in model["ramps"].values()]
  surfaces.extend(_footprint(row["corners_m"]) for row in model["walkways"].values())
  surfaces.extend(row["extent_m"] for row in model["meeting_areas"].values())
  for junction in model["junctions"].values():
    x,y,_ = junction["center_m"]
    half = junction["width_m"]/2
    surfaces.append((x-half,y-half,x+half,y+half))
  routes = {}
  for key,row in model["paths"].items():
    points = row["points_m"]
    shapes = [shape for a,b in pairwise(points) if (shape:=_swept(a,b,width,height))]
    blockers = sorted(name for name,volume in obstacles.items() if any(shape.intersects(volume) for shape in shapes))
    footprints = [(*shape.minimum_m[:2],*shape.maximum_m[:2]) for shape in shapes]
    exits_parcel = any(not _contains(site["boundary_m"],rect) for rect in footprints)
    supported = all(_covered(rect,surfaces) for rect in footprints)
    if width+1e-9 < arch["egress"]["minimum_clear_width_m"]:
      issue(issues,"ROUTE_WIDTH",[key],f"{key}: outdoor path is narrower than the reference path width.")
    if exits_parcel:
      issue(issues,"PARCEL",[key],f"{key}: the clear walking envelope crosses the parcel.")
    if not supported:
      issue(issues,"PAVEMENT_GAP",[key],f"{key}: part of the clear walking strip has no pavement support.")
    if blockers:
      issue(issues,"ROUTE_CLASH",[key,*blockers],f"{key}: outdoor path intersects {', '.join(blockers)}.")
    ramp = model["ramps"][key]
    grade = [ramp["inside_m"],*ramp["walking_stations_m"][:3],ramp["branch_m"]]
    routes[key] = {"assembly":row["assembly"],"site_distance_m":sum(dist(a,b) for a,b in pairwise(points)),
                   "ramp_to_branch_m":sum(dist(a,b) for a,b in pairwise(grade)),"blockers":blockers,
                   "supported_pavement":supported}
  joined = {}
  for item,entry in egress["routes"].items():
    observations = {}
    assignments = [routes[row["exit"]]["assembly"] for row in entry["paths"]]
    if len(set(assignments))<2:
      issue(issues,"INDEPENDENT_DESTINATIONS",[item,*assignments],f"{item}: both design alternatives share an assembly area.")
    for row in entry["paths"]:
      name = row["exit"]
      record = routes[name]
      combined = row["inside_to_exit_m"]+record["ramp_to_branch_m"]+record["site_distance_m"]
      if combined>limit+1e-9:
        issue(issues,"TRAVEL",[item,name],f"{item}/{name}: length {combined:.2f} m exceeds the project assumption.")
      observations[name] = {**record,"indoor_distance_m":row["inside_to_exit_m"],"total_distance_m":combined,
                            "indoor_pass":row["pass"]}
    joined[item] = observations
  return routes,joined


def build_exit_access_study(context):
  paths = (MODEL,ARCH,EGRESS,f"{MODELS}/site.json",TERRAIN)
  model,arch,egress,site,terrain = (staged_json(context,name) for name in paths)
  config = model["assumptions"]
  headroom = arch["egress"]["minimum_clear_height_m"]
  width = arch["egress"]["minimum_clear_width_m"]
  criteria = config["criteria"]
  per_pod = number(criteria["people_per_pod"],"candidate personnel in each IT pod")
  extra = number(criteria["additional_staff"],"candidate transient personnel",zero=True)
  occupants = per_pod*len(egress["routes"])+extra
  issues = []
  hardware = {key:_hardware(row,arch["doors"][key],site["boundary_m"],config,headroom,width,issues)
              for key,row in model["ramps"].items()}
  segments,areas,ties,obstacles = _site_geometry(model,arch,site,terrain,context,occupants,issues)
  exits,joined = _site_routes(model,arch,egress,site,obstacles,issues)
  result = model_report(context,"exit-access-study",{
    "input_digests": {name:file_digest(context.build_path/name) for name in paths},
    "exits":exits,"paths":joined,"hardware":hardware,"paving":segments,"assembly":areas,"ramp_ties_m":ties,
    "issues":issues,"occupancy":occupants,
    "coverage":"Candidate hall door and ramp opening, geometry-driven walking surfaces and two on-site assembly areas. "
               "The open-lot reference does not model real fences, smoke, actual occupant flow, slip resistance or "
               "code-specific accessibility obligations. Verify operator boundaries and field hardware separately.",
  })
  schedule = _schedule(context,"aidc-evacuation-to-assembly.csv",
                       ("origin","door","assembly","indoor_m","ramp_m","outside_m","total_m","indoor_pass","outdoor_blockers"),
                       ((name,door,row["assembly"],row["indoor_distance_m"],row["ramp_to_branch_m"],
                         row["site_distance_m"],row["total_distance_m"],row["indoor_pass"]," ".join(row["blockers"]))
                        for name,raw in joined.items() for door,row in raw.items()))
  return TargetResult(result.status,(*result.artifacts,schedule),result.diagnostics)


def build_exit_access_check(context):
  result = staged_json(context,STUDY)
  return model_report(context,"exit-access-check",{
    "study_digest":file_digest(context.build_path/STUDY),"issues":result["issues"],
    "path_count":sum(map(len,result["paths"].values())),"assembly_count":len(result["assembly"]),
  },result["issues"])
