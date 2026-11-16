"""Evaluate routed charging duty, candidate fault pickup and the emergency command states."""

from collections import defaultdict
from math import hypot, pi, sqrt

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .emergency_lighting import MODEL as LIGHTING
from .emergency_lighting_study import STUDY as LIGHT_STUDY
from .lighting_distribution import MODEL, issue
from .roof import _schedule
from .spatial_design import MODELS, model_report, number, staged_json

STUDY = f"{MODELS}/lighting-distribution-study.json"
_GROUPS = frozenset(("A","B"))


def _cable(row):
  names=("min_ohm_per_km","max_ohm_per_km","x_ohm_per_km","rated_a","adjusted_ampacity_a",
         "magnetic_multiple","breaking_ka","od_m")
  values={key:number(row[key],f"conductor {key}") for key in names}
  if values["min_ohm_per_km"]>values["max_ohm_per_km"]:
    raise ValueError("Cold conductor impedance exceeds the hot conductor impedance.")
  return values


def _loop(cable, distance, hot):
  return (2*distance*cable["max_ohm_per_km" if hot else "min_ohm_per_km"]/1000,
          2*distance*cable["x_ohm_per_km"]/1000)


def _voltage_drop(amps, loop, pf):
  return amps*(loop[0]*pf+loop[1]*sqrt(1-pf*pf))


def _fault(voltage, source, *conductors):
  return voltage/hypot(*(sum(terms) for terms in zip(source,*conductors,strict=True)))


def _protection(name, device, load, minimum, maximum, issues):
  result={"ampacity":load<=device["rated_a"]<=device["adjusted_ampacity_a"],
          "magnetic_pickup":minimum>=device["rated_a"]*device["magnetic_multiple"],
          "interrupting_capacity":maximum<=1000*device["breaking_ka"]}
  for key,passed in result.items():
    if not passed:
      issue(issues,"PROTECTION",[name,key],f"{name} does not meet the candidate {key} criterion.")
  return result


def _fill(model,feeder,branch,electrical,issues):
  maximum=number(electrical["max_duct_fill"],"maximum allowable duct fill")
  if maximum>=1:
    raise ValueError("The maximum duct fill fraction must be less than one.")
  totals=defaultdict(float)
  result={}
  for key,run in model["carriers"].items():
    area=0.
    for circuit in run["circuits"]:
      od=(number(electrical["signal_od_m"],"signal cable OD") if run["service"]=="signal"
          else feeder["od_m"] if circuit=="EL-FEED" else branch["od_m"])
      area+=pi*od*od/4
      totals[circuit]+=run["length_m"]
    usable=run["outside_m"]-0.008
    if usable<=0:
      raise ValueError("Declared conduit does not provide positive interior area.")
    result[key]=area/(pi*usable**2/4)
    if result[key]>maximum:
      issue(issues,"FILL",[key],f"{key} conductor fill {result[key]:.3f} exceeds the candidate maximum.")
  return result,dict(totals)


def _supply(model,photometry,electrical,issues):
  feed,branch=(_cable(electrical[key]) for key in ("feeder","branch"))
  voltage=number(electrical["phase_voltage_v"],"phase-to-neutral voltage")
  pf=number(electrical["normal_pf"],"charging power factor")
  if pf>1:
    raise ValueError("Power factor cannot exceed one.")
  cold=tuple(number(electrical[f"source_min_{k}_ohm"],"minimum supply impedance") for k in ("r","x"))
  hot=tuple(number(electrical[f"source_max_{k}_ohm"],"maximum supply impedance") for k in ("r","x"))
  if any(a>b for a,b in zip(cold,hot,strict=True)):
    raise ValueError("Maximum supply impedance cannot be below its minimum.")
  groups=photometry["charge_branches"]
  assignment=electrical["phase_by_branch"]
  if set(assignment)!=set(groups) or set(assignment.values())!={"L1","L2","L3"}:
    raise ValueError("All eight charge branches require a phase assignment across L1/L2/L3.")
  phase=dict.fromkeys(("L1","L2","L3"),0.)
  phase["L1"]+=number(electrical["cabinet_controls_va"],"cabinet control burden")/voltage
  for key,row in groups.items():
    phase[assignment[key]]+=row["charge_w"]/voltage/pf
    if key.endswith("-A") and assignment[key]==assignment[key[:-1]+"B"]:
      issue(issues,"PHASE_GROUP",[key,key[:-1]+"B"],f"{key} shares its phase with the adjacent charging group.")
  duty=sum(row["charge_w"] for row in groups.values())
  connected_kw=(duty+electrical["cabinet_controls_va"])/1000+number(
                electrical["simultaneous_aux_other_kw"],"coincident auxiliary demand",zero=True)
  if connected_kw>model["supply"]["auxiliary_budget_kw"]:
    issue(issues,"AUX_BUDGET",[model["supply"]["port"]],
          f"Coincident demand {connected_kw:.3f} kW exceeds the installed auxiliary budget.")
  fills,quantities=_fill(model,feed,branch,electrical,issues)
  support={"phase_a":phase,"connected_charge_w":duty,"coincident_aux_kw":connected_kw,
           "maximum_fill":max(fills.values(),default=0.),"fill_by_run":fills,"cable_length_by_circuit_m":quantities}
  if "EL-FEED" not in model["routes"]:
    return support,{}
  fl=model["routes"]["EL-FEED"]["length_m"]
  feed_hot,feed_cold=_loop(feed,fl,True),_loop(feed,fl,False)
  source_max=_fault(voltage*1.05,cold)
  board_max=_fault(voltage*1.05,cold,feed_cold)
  board_min=_fault(voltage*.90,hot,feed_hot)
  feed_drop=_voltage_drop(max(phase.values()),feed_hot,pf)
  support.update({"feeder_length_m":fl,"board_min_fault_a":board_min,"supply_max_fault_a":source_max,
                  "board_max_fault_a":board_max,"feeder_drop_v":feed_drop,
                  "protection":_protection("EL-FEED",feed,max(phase.values()),board_min,source_max,issues)})
  branches={}
  for key,row in sorted(groups.items()):
    loads={}
    current=row["charge_w"]/voltage/pf
    for fixture in sorted(row["fixtures"]):
      if fixture not in model["routes"]:
        continue
      route=model["routes"][fixture]
      impedance=_loop(branch,route["length_m"],True)
      minimum=_fault(voltage*.90,hot,feed_hot,impedance)
      drop_pct=100*(feed_drop+_voltage_drop(current,impedance,pf))/voltage
      loads[fixture]={"length_m":route["length_m"],"maximum_drop_pct":drop_pct,"minimum_fault_a":minimum}
    if len(loads)!=len(row["fixtures"]):
      issue(issues,"TERMINATION",[key],f"{key}: at least one declared luminaire lacks a routed termination.")
    if not loads:
      continue
    critical=min(loads,key=lambda name:loads[name]["minimum_fault_a"])
    minimum=loads[critical]["minimum_fault_a"]
    drop=max(value["maximum_drop_pct"] for value in loads.values())
    if drop>number(electrical["max_voltage_drop_pct"],"maximum permissible voltage drop"):
      issue(issues,"VOLTAGE_DROP",[key,critical],f"{key} drop {drop:.3f}% exceeds the project allowance.")
    branches[key]={"phase":assignment[key],"charge_w":row["charge_w"],"load_a":current,"points":loads,
                   "maximum_drop_pct":drop,"minimum_fault_a":minimum,"critical_fixture":critical,
                   "installed_cable_m":quantities.get(key,0.),
                   "protection":_protection(key,branch,current,minimum,board_max,issues)}
  return support,branches


def _supervision(routes,settings,issues):
  result={}
  for key,row in sorted(routes.items()):
    if row["service"]!="signal":
      continue
    total=2*row["length_m"]*number(settings["signal_pair_ohm_per_km"],"signal loop resistance per kilometre")/1000
    result[key]={"length_m":row["length_m"],"loop_ohm":total}
    if total>number(settings["signal_loop_max_ohm"],"maximum signal pair loop resistance"):
      issue(issues,"SIGNAL_LOOP",[key],f"{key} loop resistance {total:.3f} Ω exceeds the candidate input limit.")
  if len(result)!=2:
    issue(issues,"SIGNAL_LOOP",sorted(result),"Both compartment fire-command links need routed and supervised terminals.")
  return result


def _optics(photometry, available):
  field={_GROUPS:"intact_lx",frozenset("A"):"off_B_lx",frozenset("B"):"off_A_lx"}.get(available)
  return {key:{"minimum_lx":row[field]["value_lx"] if field else 0.,
               "required_lx":row["minimum_required_lx"]} for key,row in photometry["zones"].items()}


def _response_cases(settings,photometry,issues):
  supervise,release,pickup,limit=(number(settings[key],f"control timing {key}",zero=True) for key in
      ("supervision_s","contactor_release_s","fixture_pickup_s","max_emergency_start_s"))
  stuck=frozenset(settings["forced_welded_groups"])
  if not stuck<=_GROUPS:
    raise ValueError("Forced contact welds must identify lamp group A or B.")
  cases={}
  challenges=(
    ("fire-n","FACP-FC-N",True,()),("fire-s","FACP-FC-S",True,()),
    ("open-supervision","open",True,()),("short-supervision","short",True,()),
    ("normal-feeder-loss","normal",False,()),("alarm-with-A-contact-weld","FACP-FC-N",True,("A",)),
    ("alarm-with-B-contact-weld","FACP-FC-S",True,("B",)),("manual-test","manual",True,()),
  )
  for name,command,mains,weld in challenges:
    available=_GROUPS if not mains else _GROUPS-(stuck|frozenset(weld))
    latency=pickup if not mains else supervise+release+pickup
    optical=_optics(photometry,available)
    passed=latency<=limit and all(row["minimum_lx"]>=row["required_lx"] for row in optical.values())
    if not passed:
      issue(issues,"EMERGENCY_RESPONSE",[name],f"{name} leaves insufficient modeled emergency lighting or exceeds the response time.")
    cases[name]={"command":command,"normal_supply":mains,"welded_groups":sorted(stuck|frozenset(weld)),
                 "energized_battery_groups":sorted(available),"pickup_s":latency,"zones":optical,"status":"pass" if passed else "fail"}
  return cases


def _reset_timeline():
  history=[]
  events=(
    (0,"normal",False,True), (10,"fire",False,True), (20,"fire",True,True),
    (40,"normal",False,True), (50,"open",True,True), (60,"normal",True,False),
    (70,"normal",True,True),
  )
  lock=False
  for at, status, reset, mains in events:
    if status!="normal":
      lock=True
    elif reset and mains:
      lock=False
    history.append({"elapsed_s":at,"supervision":status,"reset":reset,"normal_supply":mains,
                    "alarm_latched":lock,"charge_command":mains and not lock})
  return history


def build_lighting_distribution_study(context):
  model,photometry,lights=(staged_json(context,key) for key in (MODEL,LIGHT_STUDY,LIGHTING))
  config=model["assumptions"]
  issues=list(model["issues"])
  power,branches=_supply(model,photometry,config["electrical"],issues)
  signals=_supervision(model["routes"],config["electrical"],issues)
  cases=_response_cases(config["controls"],photometry,issues)
  timeline=_reset_timeline()
  result=model_report(context,"lighting-distribution-study",{
    "input_digests":{key:file_digest(context.build_path/key) for key in (MODEL,LIGHT_STUDY,LIGHTING)},
    "normal_distribution":power,"branches":branches,"signal_loops":signals,"emergency_cases":cases,
    "reset_timeline":timeline,"issues":issues,"fixtures":len(lights["fixtures"]),
    "coverage":"Candidate 230 V phase-to-neutral charging circuits, conservative full-branch voltage drop and "
               "end-point magnetic pickup. Independently stored lamp energy supports the declared loss scenarios. "
               "The isolated fire interface assumes supervised relay contacts and local lamp dropout response. "
               "Actual device certification, discrimination, grounding loop measurements and field tests require verification.",
  })
  circuit_table=_schedule(context,"aidc-lighting-charging-circuits.csv",
    ("branch","phase","lamps","charge_w","duty_a","installed_cable_m","max_drop_percent","min_ln_fault_a","critical_fixture"),
    ((key,row["phase"],len(row["points"]),row["charge_w"],row["load_a"],row["installed_cable_m"],row["maximum_drop_pct"],
      row["minimum_fault_a"],row["critical_fixture"]) for key,row in branches.items()))
  operation=_schedule(context,"aidc-lighting-command-tests.csv",
    ("case","command","mains","battery_groups","pickup_s","zone","minimum_lux","required_lux","status"),
    ((key,row["command"],row["normal_supply"]," ".join(row["energized_battery_groups"]),row["pickup_s"],
      zone,value["minimum_lx"],value["required_lx"],row["status"]) for key,row in cases.items()
     for zone,value in row["zones"].items()))
  return TargetResult(result.status,(*result.artifacts,circuit_table,operation),result.diagnostics)


def build_lighting_distribution_check(context):
  report=staged_json(context,STUDY)
  return model_report(context,"lighting-distribution-check",{
    "study_digest":file_digest(context.build_path/STUDY),"issues":report["issues"],
    "fixtures":report["fixtures"],"cases":len(report["emergency_cases"]),
  },report["issues"])
