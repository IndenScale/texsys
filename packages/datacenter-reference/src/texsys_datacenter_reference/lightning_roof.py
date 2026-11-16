"""Model roof lightning conductors at the installed roof and structural datum."""

from itertools import pairwise, product
from math import dist, pi

from texsys.evidence import file_digest
from texsys_spatial import Space

from .architecture import bounds, space
from .fws_network import MODEL as WATER, pipe_envelope
from .power_routing import MODEL as POWER
from .roof import MODEL as ROOF, roof_surfaces
from .spatial_design import MODELS, missing_input, model_report, number, read_versioned, source_path, staged_json

MODEL = f"{MODELS}/lightning-roof.json"
SOURCE = "lightning-roof.json"


def issue(issues, key, names, message):
  issues.append({"rule":f"AIDC_LIGHTNING_{key}","objects":list(names),"message":message,"status":"fail"})


def span(a,b,width):
  return Space(tuple(min(x,y)-(width/2 if x==y else 0) for x,y in zip(a,b,strict=True)),
               tuple(max(x,y)+(width/2 if x==y else 0) for x,y in zip(a,b,strict=True)))


def load_config(context,file=SOURCE):
  source = source_path(context,file)
  return read_versioned(source) if source.is_file() else None


def _stations(roof, settings):
  active = {axis:{name:value for name,value in roof["grid"][axis].items()
                  if roof["outline_xy_m"][i]<=value<=roof["outline_xy_m"][i+2]}
            for i,axis in enumerate(("x","y"))}
  xs,ys = (sorted(active[axis],key=active[axis].get) for axis in ("x","y"))
  if len(xs)<2 or len(ys)<2:
    raise ValueError("接闪网至少需要两组横纵轴线。")
  stations = {}
  for ix,iy in product(xs,ys):
    x,y = active["x"][ix],active["y"][iy]
    on_roof = roof_surfaces(roof,y)[-1]
    stations[f"{ix}-{iy}"] = {"base_m":[x,y,on_roof],
                               "point_m":[x,y,on_roof+settings["mesh_offset_m"]],
                               "axes":[ix,iy], "perimeter":ix in (xs[0],xs[-1]) or iy in (ys[0],ys[-1])}
  return stations,xs,ys


def _conductors(stations, xs, ys, settings, issues):
  pairs = [(f"{i}-{j}",f"{k}-{j}") for i,k in pairwise(xs) for j in ys]
  pairs.extend((f"{i}-{j}",f"{i}-{k}") for i in xs for j,k in pairwise(ys))
  links = {}
  limit = number(settings["mesh_max_span_m"],"接闪网格边长")
  for count,(start,end) in enumerate(pairs,1):
    a,b = [stations[key]["point_m"] for key in (start,end)]
    key = f"RC-{count:02}"
    length = dist(a,b)
    direction = dist(a[:2],b[:2])
    links[key] = {"from":start,"to":end,"points_m":[a,b],"length_m":length,"span_xy_m":direction}
    if direction>limit:
      issue(issues,"ROOF_MESH",[key,start,end],"接闪网格边长超过候选限值。")
  perimeter = ([f"{i}-{ys[0]}" for i in xs]+[f"{xs[-1]}-{j}" for j in ys[1:]]
               +[f"{i}-{ys[-1]}" for i in reversed(xs[:-1])]+[f"{xs[0]}-{j}" for j in reversed(ys[1:-1])])
  return links,perimeter


def _mast_section(settings,height):
  diameter = number(settings["mast_outer_m"],"接闪桅杆直径")
  wall_key = "deep_mast_wall_m" if height>=settings["deep_mast_height_m"] else "mast_wall_m"
  diameter_in = diameter-2*number(settings[wall_key],"接闪桅杆壁厚")
  if diameter_in<=0:
    raise ValueError("桅杆截面应为正壁厚钢管。")
  area = pi*(diameter**2-diameter_in**2)/4
  inertia = pi*(diameter**4-diameter_in**4)/64
  return diameter,area,inertia


def _masts(stations,settings,service,curbs,roof,issues):
  half_pad = settings["support_pad_m"]/2
  result = {}
  for node,value in sorted(settings["mast_height_m"].items()):
    height = number(value,f"{node} 桅杆高度")
    diameter,area,inertia = _mast_section(settings,height)
    q = settings["candidate_wind_kpa"]*settings["drag_factor"]*diameter
    rigidity = settings["elastic_modulus_gpa"]*1e6*inertia
    a = stations[node]["base_m"]
    x,y,z = a
    tip = [x,y,z+height]
    key = f"AT-{node}"
    pad = Space((x-half_pad,y-half_pad,z-.025),(x+half_pad,y+half_pad,z+.08))
    body = span([x,y,z+.08],tip,diameter)
    limits = roof["outline_xy_m"]
    if not (limits[0]<x-half_pad<x+half_pad<limits[2] and limits[1]<y-half_pad<y+half_pad<limits[3]):
      issue(issues,"MAST_SUPPORT",[key],"桅杆底座超出屋面边缘。")
    clash = [other for other,box in sorted(service.items()) if box.intersects(body) or box.intersects(pad)]
    clash.extend(other for other,box in sorted(curbs.items()) if box.intersects(pad))
    if clash:
      issue(issues,"MAST_COORDINATION",[key,*clash],"桅杆或底座与屋面设备相交。")
    force,moment,sway = q*height,q*height**2/2,q*height**4/(8*rigidity)
    utilization = max(force/settings["base_shear_limit_kn"],moment/settings["base_moment_limit_kn_m"],
                      sway/(height/settings["tip_deflection_ratio"]))
    if utilization>1:
      issue(issues,"MAST_WIND",[key],"候选风下桅杆底座作用或桅尖位移超过限值。")
    mass = area*height*settings["steel_density_kg_m3"]+settings["foot_mass_kg"]
    result[key] = {"node":node,"base_m":a,"tip_m":tip,"pad":bounds(pad),"body":bounds(body),
                   "column":f"C-{node}","shear_kn":force,"moment_kn_m":moment,"deflection_m":sway,
                   "utilization":utilization,"mass_kg":mass}
  if not result:
    raise ValueError("接闪设计缺少桅杆。")
  return result


def services(power,water):
  items = {key:space(row["envelope"]) for train in power["trains"].values() for key,row in train["segments"].items()}
  items.update({f"FWS-{key}":pipe_envelope(row) for key,row in water["segments"].items()})
  # Topology includes Busway segments as installed layout and power-carrier terminal runs.
  return items


def build_lightning_roof(context):
  source = source_path(context,SOURCE)
  config = load_config(context)
  if config is None:
    return missing_input(context,source,"lightning-roof")
  if config.get("state")!="assumed" or not config.get("basis"):
    raise ValueError("防雷设计输入应声明候选依据。")
  references = (ROOF,POWER,WATER)
  roof,power,water = (staged_json(context,key) for key in references)
  settings = config["roof"]
  stations,xs,ys = _stations(roof,settings)
  issues = []
  links,perimeter = _conductors(stations,xs,ys,settings,issues)
  items = services(power,water)
  curbs = {f"CURB-{key}":Space((*row["curb_xy_m"][:2],row["curb_base_m"]),
                               (*row["curb_xy_m"][2:],row["curb_top_m"])) for key,row in roof["openings"].items()}
  masts = _masts(stations,settings,items,curbs,roof,issues)
  edges = items|curbs
  for key,row in sorted(links.items()):
    wire = span(*row["points_m"],settings["ribbon_width_m"])
    clashed = [name for name,box in sorted(edges.items()) if box.intersects(wire)]
    if clashed:
      issue(issues,"ROOF_CONDUCTOR",[key,*clashed],"屋面接闪带与现有管道、桥架或洞口构造相交。")
  mesh_mass = sum(row["length_m"] for row in links.values())*settings["copper_density_kg_m3"]*(
    settings["ribbon_width_m"]*settings["ribbon_thickness_m"])
  return model_report(context,"lightning-roof",{
    "source_digest":file_digest(source),"input_digests":{key:file_digest(context.build_path/key) for key in references},
    "geometry_settings":settings,"stations":stations,"links":links,"perimeter":perimeter,"masts":masts,"mesh_mass_kg":mesh_mass,
    "geometry_basis":"Rooftop mast supports at primary structural axes. Mast shells and baseline load allow candidate field comparison.",
    "issues":issues,
  })
