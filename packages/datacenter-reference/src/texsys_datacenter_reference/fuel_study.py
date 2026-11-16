"""Evaluate isolated fuel trains, transfer hydraulics and retained spills."""

from itertools import pairwise
import json
from math import acos, hypot, pi, sqrt

from scipy.optimize import brentq

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_hvac_dynamics.hydraulics import pipe_loss, pump_head_m

from .electrical import MODEL as POWER
from .facility_power_study import STUDY as FACILITY
from .fuel_system import MODEL, issue
from .roof import _schedule
from .site_storm_study import STUDY as STORM
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json

STUDY = f"{MODELS}/fuel-study.json"
CONCEPT = "datacenter-reference/design-checks.json"


def _loss(row, flow, fluid, viscosity):
  pipe = row["pipe"]
  return pipe_loss(flow,diameter=pipe["inside_m"],length=row["length_m"],roughness=fluid["roughness_m"],
                   minor_loss=pipe["minor_k"]+.8*row["fittings"],density=fluid["density_kg_m3"],viscosity=viscosity)


def _gravity_capacity(row, fluid):
  grades = [(a[2]-b[2])/hypot(a[0]-b[0],a[1]-b[1]) for a,b in pairwise(row["points_m"]) if a[:2]!=b[:2]]
  grade = min(grades)
  r = row["pipe"]["inside_m"]/2
  angle = 2*acos(-.5)
  area = r*r/2*(angle+sqrt(3)*.5)
  wetted_radius = area/(r*angle)
  return grade,3600*area*wetted_radius**(2/3)*sqrt(max(grade,0))/fluid["overflow_manning_n"]


def _hydraulic_states(tank,skid,routes,side,ends,pump_count,settings,cabinet,issues):
  rated = cabinet["pressure_design_lph"]/3_600_000
  trade = settings["pump_shutoff_m"]-settings["pump_head_at_design_m"]
  if rated<=0 or trade<=0:
    raise ValueError("转输泵曲线应随流量下降并涵盖标称点。")
  maximum = sqrt(settings["pump_shutoff_m"]/trade)*rated
  rho_g = settings["density_kg_m3"]*settings["gravity_m_s2"]
  result = {}
  for name,viscosity,filter_kpa in (("warm",settings["warm_viscosity_pa_s"],settings["filter_clean_kpa"]),
                                  ("cold-dirty",settings["cold_viscosity_pa_s"],settings["filter_loaded_kpa"])):
    for end in ends:
      tags = (f"SU-{side}",f"TR-{side}",f"HDR-{side}",f"fill-{end}")
      inlet = routes[tags[-1]]["points_m"][-1][2]
      static = (inlet-tank["minimum_surface_m"])*rho_g
      def calculate(n):
        def residual(flow):
          head = pump_head_m(flow/n,settings["pump_shutoff_m"],trade/(rated*rated),1)
          dynamic = sum(_loss(routes[key],flow,settings,viscosity) for key in tags)
          return rho_g*head-static-filter_kpa*1000-dynamic
        pressure_margin = residual(rated)/1000
        flow = brentq(residual,0,n*maximum,xtol=1e-13) if residual(0)>0 else 0.
        npsh = (settings["available_air_abs_kpa"]-settings["vapor_kpa"])*1000/rho_g+(
          tank["minimum_surface_m"]-skid["ports_m"]["in"][2])-_loss(routes[tags[0]],flow,settings,viscosity)/rho_g
        return {"flow_m3_h":flow*3600,"npsh_available_m":npsh,"pressure_margin_at_design_kpa":pressure_margin,
                "energy_residual_pa":residual(flow)}
      single,double = calculate(1),calculate(2)
      passed = (pump_count>=1 and single["flow_m3_h"]*1000>=cabinet["pressure_design_lph"] and
                min(single["npsh_available_m"],double["npsh_available_m"])>=settings["required_npsh_m"]+settings["npsh_margin_m"])
      key = f"{side}:{name}:{end}"
      if not passed:
        issue(issues,"TRANSFER_DUTY",[key,*tags],"转输流量、失效冗余或泵进口余量不满足候选要求。")
      result[key] = {"routes":tags,"static_head_m":static/rho_g,"pumps_available":pump_count,"single":single,
                    "both_running":double,"status":"pass" if passed else "fail"}
  return result


def _machine(config,runs,day,key,consumption,issues):
  fluid,cab = config["hydraulics"],config["cabinet"]
  rho_g = fluid["density_kg_m3"]*fluid["gravity_m_s2"]
  supply = cab["engine_supply_factor"]*consumption
  if supply<consumption:
    raise ValueError("发动机循环供油量应至少等于燃烧消耗。")
  suction,return_ = runs[f"feed-{day['engine']}"],runs[f"return-{day['engine']}"]
  feed_loss = _loss(suction,supply/3_600_000,fluid,fluid["cold_viscosity_pa_s"])
  low_p = (rho_g*(day["levels_m"]["low"]-day["engine_supply_m"][2])-feed_loss)/1000
  upper_p = rho_g*(day["levels_m"]["overflow"]-day["engine_supply_m"][2])/1000
  return_elevation = max(row[2] for row in return_["points_m"])
  back_pressure = (rho_g*(return_elevation-day["engine_return_m"][2])+
                   _loss(return_,(supply-consumption)/3_600_000,fluid,fluid["cold_viscosity_pa_s"]))/1000
  pass_window = low_p>=cab["engine_feed_min_kpa"] and upper_p<=cab["engine_feed_max_kpa"] and back_pressure<=cab["engine_return_max_kpa"]
  if not pass_window:
    issue(issues,"ENGINE_PRESSURE",[key,day["engine"]],"机组候选供油压头或回油背压不满足边界。")
  return {"estimated_consumption_lph":consumption,"circulation_lph":supply,"engine_return_lph":supply-consumption,
          "minimum_supply_kpa":low_p,"maximum_supply_kpa":upper_p,"maximum_return_kpa":back_pressure,
          "status":"pass" if pass_window else "fail"}


def _cycle(side,ends,consumption,cabinet,pump_count,issues):
  time_step = .5
  total_s = 1500
  reserve = {key:day["area_m2"]*1000*(day["levels_m"]["start"]-day["levels_m"]["low"]) for key,day in ends.items()}
  levels = {key:day["levels_m"]["start"] for key,day in ends.items()}
  admitting = dict.fromkeys(ends,True)
  minimum = dict(levels)
  drawn,returned,stored = 0.,0.,0.
  worst = []
  for i in range(int(total_s/time_step)+1):
    t = i*time_step
    if i%60==0:
      worst.append({"elapsed_s":t,"levels_m":dict(levels)})
    if i*time_step >= total_s:
      continue
    fill_count = sum(admitting.values())
    q = cabinet["pressure_design_lph"]/3600/fill_count if (
      pump_count>0 and fill_count and t>=cabinet["changeover_s"]) else 0.
    used = sum(consumption.values())/3600*time_step
    drawn += used
    for key,day in ends.items():
      area_l_m = day["area_m2"]*1000
      fill = q*time_step if admitting[key] else 0.
      returned += fill
      delta = fill-consumption[key]*time_step/3600
      stored += delta
      levels[key] += delta/area_l_m
      minimum[key] = min(minimum[key],levels[key])
      if levels[key]>=day["levels_m"]["stop"]:
        admitting[key] = False
      if levels[key]<=day["levels_m"]["start"]:
        admitting[key] = True
  passed = (abs(stored-returned+drawn)<1e-7 and all(minimum[key]>=row["levels_m"]["low"] for key,row in ends.items()))
  if not passed:
    issue(issues,"DAY_TANK_CHANGEOVER",[side,*ends],"转输泵切换或两台并行起动期间，日用箱跌至低液位。")
  return {"initial_reserve_l":reserve,"minimum_levels_m":minimum,"burned_l":drawn,"delivered_l":returned,
          "net_day_change_l":stored,"balance_residual_l":stored-returned+drawn,"time_series":worst,"status":"pass" if passed else "fail"}


def _fuel_endurance(side,topology,facility,tank,day,requirements,issues):
  train = topology["trains"][side]
  values = {}
  for key,row in train["nodes"].items():
    if key.startswith(f"GenContainer{side}/Genset"):
      params = row["parameters"]
      values[key] = {"prime_kw":params["prime_kw"]["value"],"consumption_lph":params["fuel_consumption_lph"]["value"]}
  if {row["engine"] for row in day.values()}!=values.keys() or len(values)<2:
    raise ValueError(f"{side} 的日用箱必须追溯到每一台安装机组。")
  demand = max(topology["facility_budget_kw"],max(row["single_train_input_kw"] for row in facility["states"].values()
                                                if row["operating_train"]==side))
  result = {}
  for offline in sorted(values):
    remaining = sorted(((row["prime_kw"],name,row["consumption_lph"]) for name,row in values.items() if name!=offline),reverse=True)
    el,used,on = 0.,0.,[]
    for kw,name,lph in remaining:
      if el>=demand:
        break
      el += kw
      used += lph
      on.append(name)
    hours = tank["usable_l"]/used if used>0 else 0
    passed = el>=demand and hours>=requirements
    if not passed:
      issue(issues,"INDEPENDENT_RESERVE",[side,offline],"仅使用当列储罐，有一台机组退出时无法满足独立负荷与续航。")
    result[offline] = {"engines_running":on,"running_kw":el,"demand_kw":demand,"rated_consumption_lph":used,
                       "local_usable_l":tank["usable_l"],"endurance_h":hours,"required_h":requirements,
                       "status":"pass" if passed else "fail"}
  return values,result


def _retention(model,storm,maximum_flow,settings,issues):
  yard = model["oil_yard"]
  roles = model["runs"]
  by_side = {}
  for side in ("A","B"):
    days = model["day_tanks"]
    spill = model["tanks"][f"TK-{side}"]["credible_leak_l"]
    spill += sum(row["gross_l"] for key,row in days.items() if f"-{side}" in key)
    line_inventory = sum(pi*row["pipe"]["inside_m"]**2/4*row["length_m"]
                         for row in roles.values() if row["train"]==side)
    spill += 1000*(line_inventory+maximum_flow[side]*settings["line_isolation_s"]/3600)
    by_side[side] = {"spill_m3":spill/1000,"routed_pipe_inventory_l":line_inventory*1000}
  release = max(row["spill_m3"] for row in by_side.values())
  if release>yard["credible_spill_m3"]:
    issue(issues,"SPILL_BUDGET",["oil-yard"],"燃油设备计算事故液量超过已声明的油区控制事故量。")
  cases = {}
  for label,row in sorted(storm["cases"].items()):
    value = row["oil_yard"]
    basis = max(yard["credible_spill_m3"],release)
    demand = settings["retained_before_storm_m3"]+value["rain_m3"]+basis
    free = value["capacity_m3"]-demand
    status = "pass" if free>=settings["minimum_remaining_storage_m3"] else "fail"
    if status=="fail":
      issue(issues,"SEALED_RETENTION",[label,"OIL-SEALED"],"候选事故与降雨共同作用时，密闭收集池剩余容量不足。")
    cases[label] = {"rain_m3":value["rain_m3"],"prior_volume_m3":settings["retained_before_storm_m3"],"estimated_spill_m3":release,"basis_spill_m3":basis,
                    "demand_m3":demand,"working_capacity_m3":value["capacity_m3"],"free_storage_m3":free,"status":status}
  return {"spill_envelope_by_train":by_side,"declared_spill_budget_m3":yard["credible_spill_m3"],"cases":cases}


def build_fuel_study(context):
  bases = (MODEL,POWER,FACILITY,STORM,CONCEPT)
  model,topology,facility,storm,concept = (staged_json(context,key) for key in bases)
  cfg = model["family_assumptions"]
  source = source_path(context,"design-basis.json")
  if not source.is_file():
    return missing_input(context,source,"fuel-study")
  design = json.loads(source.read_text(encoding="utf-8"))
  requirement, = [row for row in design["requirements"] if row["id"]=="GB-8.1.15"]
  hours = number(requirement["parameters"]["min_fuel_hours"],"最低续航")
  allocation, = [row["evidence"]["groups"] for row in concept["checks"] if row["id"]=="GB-8.1.15"]
  settings,cab = cfg["hydraulics"],cfg["cabinet"]
  issues = list(model["issues"])
  runs,pumps,day_tanks = (model[key] for key in ("runs","skids","day_tanks"))
  unknown = set(settings["unavailable_pumps"])-{name for row in pumps.values() for name in row["pumps"]}
  if unknown:
    raise ValueError(f"未找到指定的燃油转输泵：{sorted(unknown)}")
  storage,hydraulic,days,cycle,overflow,maximum,limits = {},{},{},{},{},{},{}
  for side in ("A","B"):
    skid = pumps[f"PB-{side}"]
    tank = model["tanks"][f"TK-{side}"]
    nominal = allocation[f"GenContainer{side}"]["nominal_l"]
    if tank["nominal_l"]<nominal:
      issue(issues,"STORAGE_RECONCILIATION",[side,f"TK-{side}"],"布置提取的储罐净名义容量小于设计分配。")
    ends = {key:row for key,row in day_tanks.items() if row["host"]==f"GenContainer{side}"}
    engines,storage[side] = _fuel_endurance(side,topology,facility,tank,ends,hours,issues)
    n = len(skid["pumps"].keys()-set(settings["unavailable_pumps"]))
    values = _hydraulic_states(tank,skid,runs,side,ends,n,settings,cab,issues)
    hydraulic.update(values)
    consumption = {end:engines[row["engine"]]["consumption_lph"] for end,row in ends.items()}
    days.update({end:_machine(cfg,runs,row,end,consumption[end],issues) for end,row in ends.items()})
    cycle[side] = _cycle(side,ends,consumption,cab,n,issues)
    maximum[side] = max(row["both_running"]["flow_m3_h"] for row in values.values())
    stopping = maximum[side]*1000*cab["high_high_stop_s"]/3600
    reserve = min(row["area_m2"]*1000*(row["levels_m"]["overflow"]-row["levels_m"]["high_high"])
                  for row in ends.values())
    status = "pass" if stopping<=reserve else "fail"
    if status=="fail":
      issue(issues,"HIGH_HIGH_STOP",[side,*ends],"高高液位停泵延时超过溢流前的有效容积。")
    limits[side] = {"two_pump_coast_l":stopping,"minimum_storage_to_overflow_l":reserve,"status":status}
    for key in (f"OV-{side}",f"OVH-{side}",*(f"overflow-{end}" for end in ends)):
      grade,capacity = _gravity_capacity(runs[key],settings)
      status = "pass" if grade>=settings["minimum_overflow_grade"] and capacity>=maximum[side] else "fail"
      if status=="fail":
        issue(issues,"OVERFLOW_CAPACITY",[side,key],"日用箱的被动溢流无法承接双泵误启动时的最大计算流量。")
      overflow[key] = {"minimum_grade":grade,"capacity_m3_h_at_75_percent_depth":capacity,
                       "maximum_two_pump_flow_m3_h":maximum[side],"status":status}
  containment = _retention(model,storm,maximum,cfg["containment"],issues)
  report = model_report(context,"fuel-study",{
    "input_digests":{key:file_digest(context.build_path/key) for key in bases},"basis_digest":file_digest(source),
    "independent_storage":storage,"transfer":hydraulic,"engine_ports":days,"changeover":cycle,
    "overflow":overflow,"high_high_stop":limits,"containment":containment,"issues":issues,
    "scope":"Two separate storage paths. Transfers evaluated as incompressible steady hydraulic bounds, "
            "with clean and cold-loaded filters, pump trip, day-tank switching and two-pump overflow. "
            "The tanks use declared cylinder and cuboid interiors. Vent discharge, installed switch wiring, actual engine skids, "
            "return fuel temperature and vendor suction/backpressure limits require professional confirmation.",
  })
  schedule = _schedule(context,"aidc-fuel-operating-cases.csv",("family","case","unit_1","value_1","unit_2","value_2","status"),
    [("storage",mode,"h",row["endurance_h"],"L",row["local_usable_l"],row["status"])
     for modes in storage.values() for mode,row in sorted(modes.items())]+
    [("transfer",key,"m3/h",row["single"]["flow_m3_h"],"m NPSH",row["single"]["npsh_available_m"],row["status"])
     for key,row in sorted(hydraulic.items())]+
    [("engine",key,"kPa minimum feed",row["minimum_supply_kpa"],"kPa return",row["maximum_return_kpa"],row["status"])
     for key,row in sorted(days.items())]+
    [("overflow",key,"m3/h capacity",row["capacity_m3_h_at_75_percent_depth"],"m3/h dual",row["maximum_two_pump_flow_m3_h"],row["status"])
     for key,row in sorted(overflow.items())]+
    [("changeover",key,"L consumed",row["burned_l"],"L balance residual",row["balance_residual_l"],row["status"])
     for key,row in sorted(cycle.items())]+
    [("retention",key,"m3 demand",row["demand_m3"],"m3 spare",row["free_storage_m3"],row["status"])
     for key,row in sorted(containment["cases"].items())])
  return TargetResult(report.status,(*report.artifacts,schedule),report.diagnostics)


def build_fuel_check(context):
  study = staged_json(context,STUDY)
  return model_report(context,"fuel-check",{
    "study_digest":file_digest(context.build_path/STUDY),"issues":study["issues"],
  },study["issues"])
