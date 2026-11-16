"""Resize the standby yard against published 400 V alternator and fuel boundaries."""

from itertools import combinations, pairwise
from math import dist, hypot, pi, sqrt

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_hvac_dynamics.hydraulics import pump_head_m

from .electrical import MODEL as POWER
from .facility_power_study import STUDY as FACILITY
from .fuel_study import STUDY as FUEL_STUDY, _gravity_capacity, _loss
from .fuel_system import LAYOUT, MODEL as FUEL, _area
from .roof import _schedule
from .site_grading import MODEL as GRADING, SITE, design_source
from .site_storm import MODEL as STORM
from .site_storm_study import STUDY as RAIN
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json, vector
from .wet_utilities import MODEL as WET

SOURCE = "generator-yard.json"
MODEL = f"{MODELS}/generator-yard.json"

TRAINS = ("A", "B")


def _box(origin, size):
  return {"minimum_m": origin, "maximum_m": [a+b for a,b in zip(origin,size,strict=True)]}


def _xy(box):
  return [*box["minimum_m"][:2],*box["maximum_m"][:2]]


def _within(a, b, offset=0):
  return all(b[i]+offset<=a[i] and a[i+2]<=b[i+2]-offset for i in range(2))


def _separation(a,b):
  return hypot(*(max(a[i]-b[i+2],b[i]-a[i+2],0) for i in range(2)))


def _issues(checks):
  return [{"rule":f"AIDC_GENERATOR_YARD_{code}","objects":row["objects"],
           "status":"fail","message":row["failure_message"]} for code,row in checks.items() if row["status"]=="fail"]


def _check(checks, code, condition, message, *objects, **evidence):
  status = {"status":"pass"} if condition else {"status":"fail","failure_message":message}
  checks[code] = {**status,"objects":objects,**evidence}


def _power(spec, ref, topology, facility, legacy, checks):
  voltage = number(ref["voltage_v"],"发电机电压")
  gross = number(ref["gross_kw"],"发电机额定电功率")
  kva = number(ref["gross_kva"],"发电机额定视在功率")
  fan = number(ref["radiator_kw"],"散热器风机")
  pf,fan_pf = (number(spec[key],key) for key in ("facility_pf","radiator_pf"))
  derate, sensitivity = (number(spec[key],key,zero=True) for key in ("site_derate_fraction","sensitivity_derate_fraction"))
  if not 0<pf<=1 or not 0<fan_pf<=1 or not 0<=derate<1 or not 0<sensitivity<1 or gross>kva:
    raise ValueError("功率因数、额定值和折减比例超出允许范围。")
  if voltage!=400 or ref["frequency_hz"]!=50:
    raise ValueError("本候选的发电、转换与负载接口为 400 V、50 Hz。")
  feeder = number(spec["generator_feeder_ampacity_a"],"发电机馈线电流")
  transfer = number(spec["facility_transfer_ampacity_a"],"设施转换电流")
  rated_a = kva*1000/(sqrt(3)*voltage)
  cases, limits = {}, {}
  for side in TRAINS:
    train = topology["trains"][side]
    equipment = sorted(train["genset_feeders"])
    if len(equipment)!=2 or set(equipment)!=set(legacy[side]):
      raise ValueError(f"{side} 列应有两台可替代机组，且燃油隔离工况应逐台关联。")
    old_ats = train["nodes"][train["ats"]]["parameters"]["rated_a"]["value"]
    limits[side] = {"ats":train["ats"],"original_ats_a":old_ats,"generator_feeder_ampacity_a":feeder,
                    "facility_transfer_ampacity_a":transfer,"nameplate_a":rated_a}
    matching = {key:row["single_train_input_kw"] for key,row in facility["states"].items() if row["operating_train"]==side}
    state = max(matching,key=matching.get)
    for offline in equipment:
      demand = max(legacy[side][offline]["demand_kw"],matching[state])
      reactive = demand*sqrt(1/pf**2-1)
      combined_kva = hypot(demand+fan,reactive+fan*sqrt(1/fan_pf**2-1))
      generator_a = combined_kva*1000/(sqrt(3)*voltage)
      transfer_a = hypot(demand,reactive)*1000/(sqrt(3)*voltage)
      margin = gross*(1-derate)-fan-demand
      pass_ = margin>=0 and combined_kva<=kva*(1-derate) and feeder>=rated_a and transfer>=transfer_a
      cases[offline] = {"train":side,"running":next(k for k in equipment if k!=offline),
                        "governing_facility_state":state,"calculated_facility_kw":matching[state],
                        "facility_budget_kw":legacy[side][offline]["demand_kw"],"demand_kw":demand,
                        "gross_demand_kva":combined_kva,"generator_a":generator_a,"transfer_a":transfer_a,
                        "active_margin_kw":margin,"maximum_power_derate_fraction":1-(demand+fan)/gross,
                        "sensitivity_derate_fraction":sensitivity,"sensitivity_margin_kw":gross*(1-sensitivity)-fan-demand,
                        "sensitivity_status":"pass" if gross*(1-sensitivity)>=demand+fan and combined_kva<=kva*(1-sensitivity) else "fail",
                        "status":"pass" if pass_ else "fail"}
      _check(checks,f"POWER_{offline}",pass_,"单台机组运行时，发电有功、视在功率或候选载流量不足。",
             offline,train["ats"],rated_a=rated_a,site_derate_fraction=derate)
  return cases,limits


def _equipment(spec, ref, yard, topology, checks):
  origin = spec["origins_m"]
  expected = {key for train in topology["trains"].values() for key in train["genset_feeders"]}
  if set(origin)!=expected:
    raise ValueError("候选定位表须逐台匹配备用供电拓扑。")
  lx,ly,lz = vector(spec["overall_reservation_m"],3,"机组布置包络")
  sx,sy,sz = vector(ref["indicative_skid_m"],3,"厂商表列机座")
  rx,ry,rz = vector(spec["radiator_reservation_m"],3,"散热器限界")
  if min(lx,ly,lz,sx,sy,sz,rx,ry,rz)<=0:
    raise ValueError("机组、机座和散热器的空间尺寸须为正。")
  edge = number(spec["pad_edge_m"],"基础扩边")
  base = spec["pad_z_m"]
  thick = number(spec["pad_thickness_m"],"基础厚度")
  mass = number(spec["wet_design_kg"],"拟定设备湿重")
  limit = number(spec["bearing_limit_kpa"],"暂定承载限值")
  minimum = number(spec["minimum_access_m"],"布置净距")
  yard_bounds = yard["extent_m"]
  equipment = {}
  for key,coords in sorted(origin.items()):
    x,y = vector(coords,2,key)
    reservation = _box([x,y,base],[lx,ly,lz])
    skid = _box([x+.35,y+(ly-sy)/2,base],[sx,sy,sz])
    radiator = _box([x+lx-.25-rx,y+(ly-ry)/2,base],[rx,ry,rz])
    pad = _box([x-edge,y-edge,base-thick],[lx+2*edge,ly+2*edge,thick])
    area = (lx+2*edge)*(ly+2*edge)
    pressure = mass*9.80665/1000/area+thick*number(spec["concrete_kn_m3"],"混凝土重度")
    fits = _within(_xy(reservation),yard_bounds,minimum) and _within(_xy(skid),_xy(reservation))
    fits = (fits and _within(_xy(radiator),_xy(reservation)) and skid["maximum_m"][0]<=radiator["minimum_m"][0]
            and max(sz,rz)<=lz and pressure<=limit and mass>=ref["indicative_dry_kg"] and base>=yard["bund_crest_m"])
    _check(checks,f"PLACEMENT_{key}",fits,"保留区、机座或散热器发生冲突，或静压、基础顶高不足。",key,
           mean_pressure_kpa=pressure,bearing_limit_kpa=limit)
    equipment[key] = {"reservation":reservation,"indicative_skid":skid,"radiator_reservation":radiator,
                      "pad":pad,"design_wet_kg":mass,"mean_bearing_kpa":pressure}
  for (a,one),(b,two) in combinations(equipment.items(),2):
    gap = _separation(_xy(one["pad"]),_xy(two["pad"]))
    _check(checks,f"ACCESS_{a}_{b}",gap>=minimum,"机组基础边之间的检修安装净距不足。",a,b,clearance_m=gap)
  return equipment


def _run(points, pipe):
  points = [p for i,p in enumerate(points) if not i or p!=points[i-1]]
  return {"points_m":points,"length_m":sum(dist(a,b) for a,b in pairwise(points)),
          "fittings":max(0,len(points)-2),"pipe":pipe}


def _fuel(cfg, ref, base, power, equipment, yard, checks):
  profile = base["family_assumptions"]
  fluid,cab = profile["hydraulics"],profile["cabinet"]
  rho_g = fluid["density_kg_m3"]*fluid["gravity_m_s2"]
  dz = cfg["minimum_oil_depth_m"]
  upper = cfg["maximum_oil_depth_m"]
  radius = number(cfg["inner_diameter_m"],"储罐内径")/2
  length = number(cfg["inner_length_m"],"储罐内长")
  jacket = number(cfg["jacket_m"],"储罐保护层")
  if not 0<dz<upper<2*radius:
    raise ValueError("储罐运行液位超出内径。")
  burn = number(ref["rated_fuel_lph"],"额定保守耗油率")
  circulation = number(ref["maximum_fuel_circulation_lpm"],"机组最大总循环流量")*60
  if circulation<burn:
    raise ValueError("燃油总循环流量小于燃烧量。")
  usable = (_area(radius,upper)-_area(radius,dz))*length*1000
  full = pi*radius**2*length*1000
  trip_lph = number(cab["pressure_design_lph"],"单泵设计转输量")
  freeboard = number(cfg["day_tank_freeboard_m"],"日用箱溢流以上净高")
  size = tuple(cab["day_size_m"])
  if not 0<freeboard<size[2]:
    raise ValueError("日用箱净高应大于溢流以上净高。")
  transfer = profile["pipes"]["transfer"]
  suction = profile["pipes"]["suction"]
  cold = fluid["cold_viscosity_pa_s"]
  pump_ids = {f"PB-{side}{n}" for side in TRAINS for n in (1,2)}
  unavailable = set(cfg["unavailable_pumps"])
  if unavailable-pump_ids:
    raise ValueError(f"未找到转输泵 {sorted(unavailable-pump_ids)}。")
  tanks, routes = {}, {}
  for side in TRAINS:
    row = cfg["tanks"][side]
    x,y = vector(row["center_xy_m"],2,f"{side} 储罐")
    px,py = vector(row["pump_xy_m"],2,f"{side} 泵撬")
    shell_z = cfg["tank_bottom_m"]+radius+jacket
    body = _box([x-length/2-jacket,y-radius-jacket,cfg["tank_bottom_m"]],
                [length+2*jacket,2*(radius+jacket),2*(radius+jacket)])
    pad = _box([x-length/2-jacket-.13,y-radius-jacket-.13,yard["formation_m"]-.22],
               [length+2*jacket+.26,2*(radius+jacket)+.26,.22])
    saddles = [_box([x+offset-.17,y-.62,yard["formation_m"]],[.34,1.24,cfg["tank_bottom_m"]-yard["formation_m"]+.18])
               for offset in (-length*.28,length*.28)]
    pump_z = 0.42
    pump = _box([px-.32,py-.24,yard["formation_m"]+.05],[.64,.48,.57])
    pumps = {f"PB-{side}{n}":_box([px-.27+(n-1)*.29,py-.18,yard["formation_m"]+.11],[.25,.36,.48]) for n in (1,2)}
    available = sorted(pumps.keys()-unavailable)
    tank_end = body["minimum_m"][0]
    supply = [[tank_end,y,pump_z],[px,y,pump_z],[px,py,pump_z]]
    routes[f"SU-{side}"] = _run(supply,suction)
    days = {}
    for offline,case in power.items():
      if case["train"]!=side:
        continue
      unit = case["running"]
      a,b,c,d = _xy(equipment[unit]["reservation"])
      center_y = (d+size[1]/2+.12) if side=="A" else (b-size[1]/2-.12)
      center_x = a+1.35
      bottom = 1.12
      day = _box([center_x-size[0]/2,center_y-size[1]/2,bottom],size)
      inlet_z = bottom+size[2]
      days[unit] = day
      by = row["transfer_y_m"]
      line = [[px,py,pump_z],[px,py,.24],[px,by,.24],[center_x,by,.24],
              [center_x,center_y,.24],[center_x,center_y,inlet_z]]
      key = "TR-"+unit
      routes[key] = _run(line,transfer)
      # Offsets place provisional ports on the outside of the skid and day-tank wall.
      leg = 1 if side=="A" else -1
      skid = equipment[unit]["indicative_skid"]
      skid_face_y = skid["maximum_m"][1] if side=="A" else skid["minimum_m"][1]
      day_face_y = center_y-leg*size[1]/2
      day_out,engine_in = [center_x-.23,day_face_y,bottom+.08],[center_x-.23,skid_face_y,.81]
      day_return,engine_out = [center_x+.23,day_face_y,bottom+.55],[center_x+.23,skid_face_y,.85]
      routes[f"FEED-{unit}"] = _run([day_out,[day_out[0],day_out[1],engine_in[2]],engine_in],profile["pipes"]["engine_supply"])
      routes[f"RET-{unit}"] = _run([engine_out,[engine_out[0],day_return[1],engine_out[2]],day_return],
                                  profile["pipes"]["engine_return"])
      feed_loss = _loss(routes[f"FEED-{unit}"],circulation/3_600_000,fluid,cold)
      return_loss = _loss(routes[f"RET-{unit}"],(circulation-burn)/3_600_000,fluid,cold)
      minimum_feed_kpa = (rho_g*(bottom+cab["day_low_depth_m"]-engine_in[2])-feed_loss)/1000
      maximum_backpressure_kpa = (rho_g*(day_return[2]-engine_out[2])+return_loss)/1000
      conservative_lift = max(0,-minimum_feed_kpa*1000/rho_g)
      engine_pass = (conservative_lift<=ref["maximum_fuel_lift_m"]
                     and maximum_backpressure_kpa<=cab["engine_return_max_kpa"])
      _check(checks,f"ENGINE_PORTS_{unit}",engine_pass,"供回油候选接口的冷态吸程或回油压力越界。",unit,
             candidate_return_limit_kpa=cab["engine_return_max_kpa"],maximum_backpressure_kpa=maximum_backpressure_kpa)
      flow = trip_lph/3_600_000
      low = cfg["tank_bottom_m"]+jacket+dz
      static = (inlet_z-low)*rho_g
      head = pump_head_m(flow,fluid["pump_shutoff_m"],
                         (fluid["pump_shutoff_m"]-fluid["pump_head_at_design_m"])/(flow*flow),1)
      suction_loss = _loss(routes[f"SU-{side}"],flow,fluid,cold)
      transfer_margin = (head*rho_g-static-suction_loss-_loss(routes[key],flow,fluid,cold))/1000-fluid["filter_loaded_kpa"]
      npsh = (fluid["available_air_abs_kpa"]-fluid["vapor_kpa"])*1000/rho_g+low-pump_z-suction_loss/rho_g
      hours = usable/burn
      required = case["required_h"]
      # Return from the engine to its own day tank does not consume transfer capacity.
      day_usable = size[0]*size[1]*(cab["day_start_depth_m"]-cab["day_low_depth_m"])*1000
      hold_l = burn*cab["changeover_s"]/3600
      pass_ = (bool(available) and hours>=required and transfer_margin>=0 and trip_lph>burn and day_usable>=hold_l
               and npsh>=fluid["required_npsh_m"]+fluid["npsh_margin_m"] and _within(_xy(day),yard["extent_m"]) and engine_pass)
      _check(checks,f"FUEL_{unit}",pass_,"分列油量、单泵压头、泵进口余量或切换缓冲不足。",unit,f"TK-{side}",
             transfer_margin_kpa=transfer_margin)
      case.update({"endurance_h":hours,"usable_l":usable,"rated_burn_lph":burn,"gross_circulation_lph":circulation,
                   "net_return_lph":circulation-burn,"transfer_margin_kpa":transfer_margin,"npsh_available_m":npsh,
                   "minimum_feed_kpa":minimum_feed_kpa,"maximum_backpressure_kpa":maximum_backpressure_kpa,
                   "day_buffer_l":day_usable,"pump_changeover_consumed_l":hold_l,"available_pumps":available,
                   "fuel_status":"pass" if pass_ else "fail"})
    _check(checks,f"STORAGE_{side}",_within(_xy(body),yard["extent_m"]) and _separation(_xy(body),_xy(pump))>=.20,
           "储罐或泵撬越出围堰或彼此净距不足。",f"TK-{side}",f"PB-{side}")
    tanks[side] = {"body":body,"pad":pad,"saddles":saddles,
                   "centerline_m":[[tank_end,y,shell_z],[body["maximum_m"][0],y,shell_z]],
                   "centerline_diameter_m":2*(radius+jacket),"nominal_l":full,"usable_l":usable,
                   "working_high_l":_area(radius,upper)*length*1000,"minimum_surface_m":cfg["tank_bottom_m"]+jacket+dz,
                   "maximum_surface_m":cfg["tank_bottom_m"]+jacket+upper,"pump":pump,"pumps":pumps,"day_tanks":days}
  return tanks,routes


def _civil(yard, site, grade, storm, wet, rain, fuel, tanks, equipment, routes, layout, checks):
  parcel = site["boundary_m"]
  x0,y0,x1,y1 = yard["extent_m"]
  a,b,c,d = yard["basin_inner_extent_m"]
  ex = [a-.20,b-.20,c+.20,d+.20]
  x,y = yard["collector_xy_m"]
  bottom,ceiling,depth = (yard[key] for key in ("basin_bottom_m","basin_ceiling_m","basin_working_depth_m"))
  slope = yard["formation_m"]-yard["collector_rim_m"]
  # Each planar facet falls from its outside edge to the common receiver.
  grades = [slope/value for value in (x-x0,x1-x,y-y0,y1-y)] if x0<x<x1 and y0<y<y1 else []
  geometry = (_within(yard["extent_m"],parcel) and _within(ex,parcel) and b<y<d and x<a
              and min(c-a,d-b)>0 and 0<depth<ceiling-bottom and ceiling<yard["collector_rim_m"]
              and yard["bund_crest_m"]>yard["formation_m"] and grades and min(grades)>=yard["minimum_surface_slope"])
  _check(checks,"FORMATION",geometry,"候选围堰汇流坡度、池体净高或宗地边界不满足。","YARD","OIL-SEALED")
  start,end = yard["collector_pipe_invert_m"],yard["basin_pipe_invert_m"]
  diameter = number(yard["collector_pipe_inside_m"],"事故池连接管内径")
  if not x<a:
    raise ValueError("收集井须在事故池进管西侧。")
  # The loss-of-outfall case retains all runoff. The working pool level stays below the inlet invert.
  collector = {"points_m":[[x,y,start+diameter/2],[a,y,end+diameter/2]],"pipe":{"inside_m":diameter}}
  collector_grade,collector_m3h = _gravity_capacity(collector,{"overflow_manning_n":0.013})
  max_rainfall = max(row["intensity_mm_h"] for row in rain["cases"].values())
  peak_inflow = (x1-x0)*(y1-y0)*max_rainfall/1000
  _check(checks,"COLLECTOR",collector_grade>=.005 and collector_m3h>=peak_inflow and start+diameter<yard["collector_rim_m"]
         and bottom+depth<end<ceiling,"事故液连接管坡度、过水能力或重力出流标高不足。","OIL-I","OIL-SEALED",
         capacity_m3_h=collector_m3h,peak_rain_m3_h=peak_inflow)
  clearance = number(yard["utility_clearance_m"],"地下管线净距")
  ground_conflicts = []
  closest = None
  for model in (storm,wet):
    for name,row in model["runs"].items():
      r = row["pipe"]["outside_m"]/2
      for p,q in pairwise(row["points_m"]):
        section = [min(p[i],q[i])-r for i in range(2)]+[max(p[i],q[i])+r for i in range(2)]
        gap = _separation(ex,section)
        closest = min(closest,gap) if closest is not None else gap
        if gap<clearance:
          ground_conflicts.append(name)
  _check(checks,"EXCAVATION",not ground_conflicts,"新事故液池外墙到既有雨水或工艺排水净距不足。",
         "OIL-SEALED",*sorted(set(ground_conflicts)),nearest_plan_clearance_m=closest)
  asset_boxes = {k:_xy(v["reservation"]) for k,v in equipment.items()}
  for side,row in tanks.items():
    asset_boxes.update({f"TK-{side}":_xy(row["body"]),f"PB-{side}":_xy(row["pump"]),
                        **{f"DT-{key}":_xy(box) for key,box in row["day_tanks"].items()}})
  asset_boxes["OIL-SEALED"] = ex
  for (key,first),(other,second) in combinations(asset_boxes.items(),2):
    if _separation(first,second)<=0:
      _check(checks,f"CLASH_{key}_{other}",False,"发电备选方案设备包络或池体占地重叠。",key,other)
  for tag,row in layout["objects"].items():
    if "/" in tag or tag in ("GenContainerA","GenContainerB"):
      continue
    p,q = row["minimum_m"],row["maximum_m"]
    if p[2]>yard["bund_crest_m"] or q[2]<-.2:
      continue
    if _separation(yard["extent_m"],_xy(row))<=0 or _separation(ex,_xy(row))<=0:
      _check(checks,f"EXISTING_{tag}",False,"候选围堰或池体与保留场地对象重叠。",tag)
  settings = fuel["family_assumptions"]["containment"]
  fluid = fuel["family_assumptions"]["hydraulics"]
  duty = fuel["family_assumptions"]["cabinet"]["pressure_design_lph"]
  open_end_lph = 2*duty*sqrt(fluid["pump_shutoff_m"]/(fluid["pump_shutoff_m"]-fluid["pump_head_at_design_m"]))
  post_trip_l = open_end_lph*settings["line_isolation_s"]/3600
  day_gross = sum(row["gross_l"] for row in fuel["day_tanks"].values())/2
  inventory = {}
  for side,row in tanks.items():
    pipe_l = sum(pi*obj["pipe"]["inside_m"]**2/4*obj["length_m"]*1000 for key,obj in routes.items()
                 if key==f"SU-{side}" or f"-GenContainer{side}/" in key)
    inventory[side] = (row["working_high_l"]+day_gross+pipe_l+post_trip_l)/1000
  prior = settings["retained_before_storm_m3"]
  reserve = settings["minimum_remaining_storage_m3"]
  spill = max(inventory.values())
  area = (x1-x0)*(y1-y0)
  capacity = (c-a)*(d-b)*depth
  rain_cases = {}
  for key,case in sorted(rain["cases"].items()):
    depth_m = case["oil_yard"]["rain_m3"]/grade["oil_yard"]["plan_area_m2"]
    inflow = depth_m*area
    remaining = capacity-prior-spill-inflow
    rain_cases[key] = {"area_m2":area,"rain_m3":inflow,"spill_m3":spill,"prior_m3":prior,
                       "capacity_m3":capacity,"remaining_m3":remaining,"original_capacity_m3":case["oil_yard"]["capacity_m3"]}
    _check(checks,f"RETENTION_{key}",remaining>=reserve,"候选密闭池无法同时容纳事故液、既存液量和降雨。",
           "OIL-SEALED",key,free_m3=remaining)
  return rain_cases,min(grades,default=0),inventory,collector


def build_generator_yard(context):
  source = source_path(context,SOURCE)
  if not source.is_file():
    return missing_input(context,source,"generator-yard")
  cfg = design_source(source)
  paths = (POWER,FACILITY,FUEL,FUEL_STUDY,SITE,GRADING,STORM,WET,RAIN,LAYOUT)
  topology,facility,fuel,legacy,site,grade,storm,wet,rain,layout = (staged_json(context,key) for key in paths)
  ref,yard = cfg["reference"],cfg["yard"]
  checks = {}
  original = legacy["independent_storage"]
  cases,limits = _power(cfg["electrical"],ref,topology,facility,original,checks)
  old_host = {}
  skid_length,skid_width,_ = ref["indicative_skid_m"]
  for side in TRAINS:
    host = layout["objects"][f"GenContainer{side}"]
    x,y,u,v = _xy(host)
    old_host[side] = {"host":f"GenContainer{side}","available_m":[u-x,v-y],
                      "two_in_line_minimum_m":2*skid_length,"two_side_by_side_minimum_m":2*skid_width,
                      "fits_axis_aligned_skids":((u-x>=2*skid_length and v-y>=skid_width)
                                                or (u-x>=skid_length and v-y>=2*skid_width))}
  for key,row in cases.items():
    previous = original[row["train"]][key]
    row["required_h"] = previous["required_h"]
    row["original_storage_h_on_same_duty"] = previous["local_usable_l"]/ref["rated_fuel_lph"]
  equipment = _equipment(cfg["equipment"],ref,yard,topology,checks)
  tanks,routes = _fuel(cfg["fuel"],ref,fuel,cases,equipment,yard,checks)
  rain_cases,grade_min,spill,collector = _civil(yard,site,grade,storm,wet,rain,fuel,tanks,equipment,routes,layout,checks)
  result = model_report(context,"generator-yard",{
    "basis":cfg["basis"],"selection_status":"conditional","frame":site["frame"],
    "input_digests":{key:file_digest(context.build_path/key) for key in paths},"source_digest":file_digest(source),
    "reference":ref,"yard":yard,"parcel_m":site["boundary_m"],"minimum_formation_grade":grade_min,
    "equipment":equipment,"tanks":tanks,"routes":routes,"power_and_fuel_cases":cases,"ratings":limits,
    "rain_cases":rain_cases,"spill_inventory_m3":spill,"collector":collector,"checks":checks,
    "original_yard_m":grade["oil_yard"]["extent_m"],"original_host_fit":old_host,
    "professional_hold_points":[
      "400 V 设备与散热器总装图、完整降容曲线、实测场地条件、并机和阶跃负载验收。",
      "额定馈线与 ATS 成套选型、短路分断能力、继保配合及接地。",
      "机组供回油界限、并发转输与溢流、通气、排烟背压、事故池施工和机械联锁。",
      "基础锚固和振动、池体抗浮、油区消防间距和雨水含油处理的专业审查。"
    ],
  })
  rows = [(key,row["running"],row["demand_kw"],row["active_margin_kw"],row["generator_a"],row["endurance_h"],
           row["transfer_margin_kpa"],row["status"],row["fuel_status"]) for key,row in sorted(cases.items())]
  schedule = _schedule(context,"aidc-generator-yard-operating-cases.csv",
                       ("unavailable","running","demand_kw","margin_kw","generator_a","endurance_h",
                        "transfer_margin_kpa","power","fuel"),rows)
  return TargetResult(result.status,(*result.artifacts,schedule),result.diagnostics)


def build_generator_yard_check(context):
  model = staged_json(context,MODEL)
  issues = _issues(model["checks"])
  return model_report(context,"generator-yard-check",{
    "source_digest":model["source_digest"],"model_digest":file_digest(context.build_path/MODEL),
    "selection_status":model["selection_status"],"issues":issues,
  },issues)
