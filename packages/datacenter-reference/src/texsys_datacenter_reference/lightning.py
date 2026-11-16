"""Connect the roof lightning network to installed electrodes and LV surge taps."""

from itertools import pairwise
from math import dist, sqrt

from pxr import Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_electrical.bonding import Bond, BondNetwork, conductor_resistance_mohm
from texsys_spatial import Space

from .architecture import MODEL as ARCH, bounds
from .earth_fault import STUDY as EARTHING_STUDY
from .earthing import MODEL as EARTHING
from .electrical import MODEL as TOPOLOGY
from .emergency_lighting_study import _opaque
from .exit_access import MODEL as ACCESS
from .fws_network import MODEL as WATER
from .lightning_coverage import evaluate_coverage
from .lightning_roof import MODEL as ROOF_SYSTEM, issue, load_config, span
from .power_routing import MODEL as POWER
from .roof import MODEL as ROOF, _schedule
from .site_grading import MODEL as TERRAIN
from .spatial_design import MODELS, SCENE as LAYOUT_SCENE, missing_input, model_report, number, source_path, staged_json
from .telecom_coordination import _distance

MODEL = f"{MODELS}/lightning-study.json"
SOURCE = "lightning.json"


def _path_length(points):
  return sum(dist(a,b) for a,b in pairwise(points))


def _downlead(stations,key,earthing,settings):
  point = stations[key]["point_m"]
  terminal = f"C-{key}"
  bond = earthing["steel_terminals"][terminal]
  earth_end = bond["points_m"][-1]
  x,y,z = point
  if x!=earth_end[0]:
    exterior = [settings["west_x_m"] if earth_end[0]<x else settings["east_x_m"],y,z]
  else:
    exterior = [x,settings["south_y_m"] if earth_end[1]<y else settings["north_y_m"],z]
  test = [*exterior[:2],settings["test_joint_z_m"]]
  if not earth_end[2]<test[2]<z:
    raise ValueError(f"{key}: 断接卡标高须介于柱脚连接和屋面引出之间。")
  return {"from":key,"to":terminal,"points_m":[point,exterior,test,[*exterior[:2],earth_end[2]],earth_end],
          "test_joint_m":test,"earthing_link":f"BOND-{terminal}"}


def _continuity(roof_net,earthing,roof_config,settings,obstacles,issues):
  section = roof_config["ribbon_width_m"]*roof_config["ribbon_thickness_m"]*1e6
  rho = settings["copper_rho_ohm_mm2_m"]
  perimeter = roof_net["perimeter"]
  points = roof_net["stations"]
  conductors = {}
  bonds = {}
  for name,row in roof_net["links"].items():
    bonds[name] = Bond(row["from"],row["to"],conductor_resistance_mohm(row["length_m"],section,rho))
  for key in perimeter:
    name = f"DL-{key}"
    row = _downlead(points,key,earthing,settings)
    shape = [span(a,b,roof_config["ribbon_width_m"]) for a,b in pairwise(row["points_m"])]
    terminal = row["to"]
    plinth = earthing["steel_terminals"][terminal]["pad"]
    allowed_at_terminal = {f"{terminal}/Plate",f"{plinth}/Plinth"}
    near = sorted((min(_distance(s,volume) for s in (shape[:-1] if tag in allowed_at_terminal else shape)),tag)
                  for tag,volume in obstacles.items())
    clearance,controller = near[0]
    if clearance+1e-9 < settings["minimum_clearance_m"]:
      issue(issues,"DOWNLEAD_CLEARANCE",[name,controller],f"引下线实体与邻近构件最小定位净距 {clearance:.3f} m。")
    row["length_m"] = _path_length(row["points_m"])
    row["minimum_clearance_m"],row["closest_object"] = clearance,controller
    row["resistance_mohm"] = conductor_resistance_mohm(row["length_m"],section,rho)+2*settings["joint_mohm"]
    conductors[name] = row
    bonds[name] = Bond(key,row["to"],row["resistance_mohm"])
  for key,row in earthing["links"].items():
    if row["kind"] in ("ring","steel"):
      bonds[key] = Bond(row["from"],row["to"],row["resistance_mohm"])
  for key,row in earthing["rods"].items():
    bonds[f"TERM-{key}"] = Bond(row["ring_station"],key,settings["joint_mohm"])
  unavailable = set(settings["unavailable"])|set(earthing["assumptions"]["fault"]["unavailable_pe"])
  # Prior PE failures apply only where the existing conductor participates in this graph.
  unknown = set(settings["unavailable"])-bonds.keys()
  if unknown:
    raise ValueError(f"防雷输入停用的导体不存在：{sorted(unknown)}")
  unavailable &= bonds.keys()
  for key in sorted(unavailable & conductors.keys()):
    issue(issues,"DOWNLEAD_UNAVAILABLE",[key],"计划安装的引下线不可用。")
  network = BondNetwork({node for row in bonds.values() for node in (row.start,row.end)},bonds,set(earthing["rods"]))
  active = [key for key in perimeter if f"DL-{key}" not in unavailable]
  edges = {}
  if len(active)>=2:
    for a,b in pairwise([*active,active[0]]):
      i,j = perimeter.index(a),perimeter.index(b)
      walk = perimeter[i:j+1] if i<j else perimeter[i:]+perimeter[:j+1]
      edges[f"{a}:{b}"] = _path_length([points[key]["point_m"][:2] for key in walk])
  if len(active)<2 or max(edges.values(),default=0)>settings["maximum_perimeter_pitch_m"]:
    issue(issues,"DOWNLEAD_PITCH",active,"周边有效引下线间距或数量超过候选设计限值。")
  terminals = {row["node"] for row in roof_net["masts"].values()}
  limit = settings["maximum_path_mohm"]
  cases = {name:network.evaluate(terminals,unavailable|additional,limit)
           for name,additional in [("normal",set()),*((f"without-{key}",{key}) for key in sorted(conductors)
                                                    if key not in unavailable)]}
  for key,row in cases.items():
    if errors:=row["disconnected"]+row["over_budget"]:
      issue(issues,"DOWNLEAD_PATH",[key,*errors],"候选接闪杆至接地极的金属路径不能满足连续性。")
  return {"downleads":conductors,"perimeter_pitch_m":edges,"path_limit_mohm":limit,"cases":cases}


def _world_bounds(prim,cache):
  box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
  return Space(tuple(box.GetMin()),tuple(box.GetMax()))


def _surge(layout,topology,earthing,stage,settings,issues):
  cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),[UsdGeom.Tokens.default_])
  unavailable = set(earthing["assumptions"]["fault"]["unavailable_pe"])
  supply = number(settings["candidate_supply_ll_v"],"SPD nominal supply")
  factor = number(settings["maximum_operating_factor"],"SPD maximum operating factor")
  inductance = number(settings["effective_lead_inductance_uh_per_m"],"SPD equivalent lead inductance")
  length_limit = number(settings["max_total_lead_m"],"SPD total lead limit")
  if factor<1:
    raise ValueError("SPD maximum operating voltage factor must include the nominal voltage.")
  result = {}
  for side,train in sorted(topology["trains"].items()):
    host = f"PowerPath{side}"
    placement = layout["objects"][host]
    geom = stage.GetPrimAtPath(placement["prim_path"]+"/Asset/Geometry")
    cabinet = _world_bounds(geom.GetChild("Envelope"),cache)
    components = {prim.GetName():_world_bounds(prim,cache) for prim in geom.GetChildren()
                  if prim.GetName()!="Envelope"}
    lug = earthing["bars"][f"MEB-{side}"]["port_m"]
    for name,config in sorted(settings["stages"].items()):
      tag = f"SPD-{side}-{name.upper()}"
      local = config["bus"]
      net_id = f"{host}/{local}"
      if net_id not in train["nodes"] or local not in components:
        raise ValueError(f"{tag} 无法追溯到配电拓扑和柜内实体母线。")
      source = components[local]
      x,_,z = source.center_m
      port = [x,source.maximum_m[1],z]
      phase_len,pe_len = (number(length,tag+" lead") for length in settings["local_lead_m"][name])
      device = [x,port[1]+phase_len,z]
      bar = [x,device[1]+pe_len,z]
      body = Space((x-.07,device[1]-.035,z-.09),(x+.07,device[1]+.035,z+.09))
      local_points = [port,device,bar]
      upper = cabinet.maximum_m[2]-.32
      rack_bond = [bar,[x,bar[1],upper],[lug[0],bar[1],upper],
                   [lug[0],lug[1]+.11,upper],[lug[0],lug[1]+.11,lug[2]],lug]
      envelopes = [body]
      envelopes.extend(span(a,b,.020) for a,b in pairwise([port,device,bar,*rack_bond[1:]]))
      other = [key for key,volume in sorted(components.items()) if key!=local and any(e.intersects(volume) for e in envelopes)]
      if other or any(not cabinet.contains_space(e) for e in envelopes):
        issue(issues,"SPD_COORDINATION",[tag,*other],"SPD 本体或接线离开安装柜，或与现有柜内器件相交。")
      local_length = _path_length(local_points)
      phase_voltage = supply*factor/sqrt(3)
      lead_voltage = local_length*inductance*number(config["di_dt_ka_us"],tag+" surge front")
      total = number(config["up_kv"],tag+" protection level")+lead_voltage
      electrode_link = f"LUG-MEB-{side}"
      checks = {"class":config["class"]==("T1+T2" if name=="transfer" else "T2"),
                "continuous_voltage":number(config["uc_v"],tag+" continuous voltage")>=phase_voltage,
                "impulse_duty":number(config["impulse_ka"],tag+" impulse rating")>=number(
                  config["min_impulse_ka"],tag+" minimum impulse rating"),
                "lead_length":local_length<=length_limit+1e-9,
                "combined_protection":total<=number(config["withstand_kv"],tag+" equipment withstand"),
                "equipotential_path":not unavailable & {electrode_link,f"TIE-MEB-{side}"}}
      if failed:=[key for key,value in checks.items() if not value]:
        issue(issues,"SPD_DUTY",[tag,*failed],"SPD 候选安装或电气保护限值未通过。")
      result[tag] = {**config,"side":side,"stage":name,"bus_id":net_id,"bus_prim":str(geom.GetChild(local).GetPath()),
                     "local_leads_m":local_points,"local_length_m":local_length,"body":bounds(body),
                     "equipotential_bond_m":rack_bond,"equipotential_length_m":_path_length(rack_bond),
                     "earthing_link":electrode_link,"phase_max_v":phase_voltage,
                     "lead_voltage_kv":lead_voltage,"installed_protection_kv":total,"checks":checks}
  return result


def build_lightning(context):
  config = load_config(context,SOURCE)
  if config is None:
    return missing_input(context,source_path(context,SOURCE),"lightning-study")
  if config.get("state")!="assumed" or not config.get("basis"):
    raise ValueError("防雷计算输入应声明候选设计依据。")
  inputs = (ROOF_SYSTEM,EARTHING,EARTHING_STUDY,ROOF,POWER,WATER,TERRAIN,TOPOLOGY,f"{MODELS}/layout.json")
  network,earthing,_,roof,power,water,terrain,topology,layout = (staged_json(context,key) for key in inputs)
  issues = list(network["issues"])
  obstacles = _opaque(context,staged_json(context,ARCH),staged_json(context,ACCESS))
  for key,row in roof["drainage"].items():
    settings = roof["assumptions"]["drainage"]
    width,depth = settings["gutter_width_m"],settings["gutter_depth_m"]
    a,*_,b = row["gutter_invert_m"]
    lo,hi = sorted((a[0],b[0]))
    y = a[1]
    obstacles[f"GUTTER-{key}"] = Space((lo,y-width/2,min(a[2],b[2])),(hi,y+width/2,max(a[2],b[2])+depth))
  # Enclosure facades are deliberate downlead support hosts. Existing services and
  # restrictions remain separate geometry, including pipes, railings and metal frames.
  walls = staged_json(context,ARCH)["walls"]
  clear_of = {key:shape for key,shape in obstacles.items() if key not in walls}
  paths = _continuity(network,earthing,network["geometry_settings"],config["downs"],clear_of,issues)
  coverage = evaluate_coverage(roof,power,water,terrain,network["masts"],config["coverage"],issues)
  stage = Usd.Stage.Open(str(context.build_path/LAYOUT_SCENE))
  if stage is None:
    raise ValueError("缺少设备实体场景，无法定位 SPD 母线。")
  spds = _surge(layout,topology,earthing,stage,config["surge"],issues)
  report = model_report(context,"lightning-study",{
    "source_digest":file_digest(source_path(context,SOURCE)),
    "input_digests":{key:file_digest(context.build_path/key) for key in (*inputs,LAYOUT_SCENE)},
    "continuity":paths,"coverage":coverage,"surge_protection":spds,"issues":issues,
    "scope":"Candidate main hall and defined elevated power/FWS service volumes. "
            "Surge screen covers the low-voltage transfer and outgoing buses, the local bonding reference and "
            "bounded lead voltage. Transferred potential, SPD coordination curves, equipment withstand, site risk, "
            "touch and step voltage, external utility and signal interfaces remain subject to engineering review.",
  })
  wires = _schedule(context,"aidc-lightning-conductors.csv",("id","from","to","length_m","minimum_clearance_m","earthing_link"),
    ((key,row["from"],row["to"],row["length_m"],row.get("minimum_clearance_m"),row.get("earthing_link"))
     for key,row in sorted((network["links"]|paths["downleads"]).items())))
  surge = _schedule(context,"aidc-lightning-spds.csv",("id","bus","class","impulse_ka","local_m","installed_kv","withstand_kv"),
    ((key,row["bus_id"],row["class"],row["impulse_ka"],row["local_length_m"],row["installed_protection_kv"],row["withstand_kv"])
     for key,row in sorted(spds.items())))
  protected = _schedule(context,"aidc-lightning-coverage.csv",("id","tiles","minimum_margin_m","unproved","unproved_one_mast_removed"),
    ((key,row["tiles"],row["minimum_margin_m"],row["not_certified"],row["not_certified_one_mast_out"])
     for key,row in sorted(coverage["targets"].items())))
  return TargetResult(report.status,(*report.artifacts,wires,surge,protected),report.diagnostics)


def build_lightning_check(context):
  study = staged_json(context,MODEL)
  return model_report(context,"lightning-check",{
    "data_digest":file_digest(context.build_path/MODEL),"protected_targets":len(study["coverage"]["targets"]),
    "issues":study["issues"],
  },study["issues"])
