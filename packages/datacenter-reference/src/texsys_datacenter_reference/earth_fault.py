"""Check continuity, a coupled rod resistance screen and TN-S fault duty."""

from math import asinh, dist, log, pi, sqrt

import numpy as np

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_electrical.bonding import Bond, BondNetwork
from texsys_electrical.protection import Breaker, _duty

from .earthing import MODEL, issue
from .facility_power import MODEL as FACILITY
from .facility_power_study import STUDY as FACILITY_STUDY
from .lighting_distribution import MODEL as LIGHTING
from .power_installation import STUDY as CRITICAL_STUDY
from .roof import _schedule
from .spatial_design import MODELS, model_report, number, staged_json

STUDY = f"{MODELS}/earth-fault-study.json"


def _continuity(model, unavailable, issues):
  links = model["links"]
  bonds = {name:Bond(row["from"],row["to"],row["resistance_mohm"]) for name,row in links.items()}
  refs = {row["source"] for row in model["bars"].values()}
  terminals = set(model["steel_terminals"])|set(model["bars"])|{row["ring_station"] for row in model["rods"].values()}
  network = BondNetwork({node for b in bonds.values() for node in (b.start,b.end)},bonds,refs)
  limit = model["assumptions"]["metal"]["main_bond_path_limit_mohm"]
  inactive = unavailable & bonds.keys()
  normal = network.evaluate(terminals,inactive,limit)
  without = {key:network.evaluate(terminals,inactive|{key},limit)
             for key,row in links.items() if row["kind"]=="ring" and key not in inactive}
  for key,result in [("normal",normal),*without.items()]:
    disconnected = result["disconnected"]+result["over_budget"]
    if disconnected:
      issue(issues,"BOND_CONTINUITY",[key,*disconnected],"接地等电位网开路或导通电阻超限。")
  # Source cabinet and MEB are required to retain their direct listed connection.
  for key in sorted(inactive):
    if links[key]["kind"] in ("bar","source"):
      issue(issues,"SOURCE_BOND_OPEN",[key],"源侧主保护连接不可用。")
  return {"normal":normal,"one_ring_segment_open":without,"limit_mohm":limit}


def _electrodes(rods, settings, issues):
  names = sorted(rods)
  if len(names)<2:
    raise ValueError("至少需要两根接地极才能校核单极退出情形。")
  radius = settings["diameter_m"]/2
  length = settings["length_m"]
  base = settings["rho_ohm_m"]/(2*pi*length)
  diagonal = base*(log(4*length/radius)-1)
  matrix = np.array([[diagonal if a==b else base*asinh(length/dist(rods[a]["top_m"],rods[b]["top_m"]))
                      for b in names] for a in names])
  np.linalg.cholesky(matrix)
  result = {}
  for omitted in [None,*names]:
    kept = [i for i,name in enumerate(names) if name!=omitted]
    sub = matrix[np.ix_(kept,kept)]
    potential = np.ones(len(kept))
    currents = np.linalg.solve(sub,potential)
    residual = np.linalg.norm(sub@currents-potential,ord=np.inf)
    resistance = 1/float(sum(currents))
    key = "all-connected" if omitted is None else f"without-{omitted}"
    passed = bool(min(currents)>0 and residual<1e-10 and resistance<=settings["max_screening_ohm"])
    result[key] = {"resistance_ohm":resistance,"potential_residual_v":float(residual),
                   "currents_a_per_v":{names[i]:float(current) for i,current in zip(kept,currents,strict=True)},
                   "status":"pass" if passed else "fail"}
    if not passed:
      issue(issues,"ELECTRODE_SCREENING",[key],"联合接地极等电位求解或单极退出时的电阻筛查未通过。")
  return {"electrode_order":names,"matrix_ohm":matrix.tolist(),"single_rod_ohm":diagonal,"cases":result,
          "limit_ohm":settings["max_screening_ohm"],
          "method":"Uniform-soil slender-rod screening. Off-diagonal mutual terms use an equal-length line-source approximation."}


def _breaker(phase_kv, data):
  return Breaker(phase_kv,data["interrupt_ka"],tuple(map(tuple,data["curve"])))


def _phase(case):
  return sum(complex(row["r"],row["x"]) for row in case["path_impedance_ohm"].values())


def _case(config, runs, path, phase, source, breaker, unavailable, maximum_phase=None):
  fault, metal = config["fault"], config["metal"]
  kv = fault["voltage_v"]/1000
  profile = fault["source_profiles"][source]
  hot = sum(runs[key]["hot_mohm"] for key in path)
  cold = sum(runs[key]["cold_mohm"] for key in path)
  x = sum(runs[key]["length_m"] for key in path)*metal["reactance_ohm_per_km"]/1000
  hot += metal["terminal_contact_mohm"]*(len(path)+1)
  cold += metal["terminal_contact_mohm"]*(len(path)+1)
  pe_min_fault,pe_max_fault = complex(hot/1000,x),complex(cold/1000,x)
  z_max = phase+pe_min_fault+complex(profile["max_r_ohm"],profile["max_x_ohm"])
  z_min = (phase if maximum_phase is None else maximum_phase)+pe_max_fault+complex(profile["min_r_ohm"],profile["min_x_ohm"])
  low = min(kv*fault["minimum_voltage_factor"]/abs(z_max),profile.get("min_limit_ka",float("inf")))
  high = min(kv*fault["maximum_voltage_factor"]/abs(z_min),profile.get("max_limit_ka",float("inf")))
  open_pe = sorted(unavailable & set(path))
  if isinstance(breaker,Breaker):
    duty = _duty(breaker,kv,low,high)
  else:
    pickup = breaker["rated_a"]*breaker["magnetic_multiple"]/1000
    duty = {"curve_covered":low>=pickup,"pickup_pass":low>=pickup,"interrupt_ka":breaker["breaking_ka"],
            "interrupt_pass":high<=breaker["breaking_ka"],"latest_clear_s":fault["lighting_magnetic_s"] if low>=pickup else None}
  clear = duty["latest_clear_s"]
  min_section = min(runs[key]["section_mm2"] for key in path)
  section_required = high*1000*sqrt(clear)/metal["adiabatic_k_a_sqrt_s_mm2"] if clear is not None else None
  failed = [key for key,status in (
    ("metallic-path",not open_pe),("current-envelope",0<low<=high),
    ("pickup-and-clearing",duty["pickup_pass"] and clear is not None and clear<=fault["clearing_limit_s"]),
    ("curve-valid-range",duty["curve_covered"]),("interrupting-duty",duty["interrupt_pass"]),
    ("source-sustain",clear is not None and clear<=profile.get("hold_s",float("inf"))),
    ("adiabatic-section",section_required is not None and section_required<=min_section),
  ) if not status]
  return {"mode":source,"pe":path,"unavailable_pe":open_pe,
          "envelope_applicability":"intact-path-only" if open_pe else "available-path",
          "minimum_ka":low,"maximum_ka":high,
          "hot_loop_ohm":{"r":z_max.real,"x":z_max.imag},"cold_loop_ohm":{"r":z_min.real,"x":z_min.imag},
          "pe_hot_mohm":hot,"pe_cold_mohm":cold,"pe_reactance_ohm":x,
          "duty":duty,"minimum_installed_section_mm2":min_section,"required_section_mm2":section_required,
          "failed":failed,"status":"fail" if failed else "pass"}


def _faults(model, critical, facility, installed, lighting, unavailable):
  config,runs,paths = (model[key] for key in ("assumptions","pe_runs","paths"))
  kv = config["fault"]["voltage_v"]/1000
  breakers = critical["assumptions"]["breakers"]
  result = {}
  for side, terminals in sorted(paths["critical"].items()):
    for terminal, pe in sorted(terminals.items()):
      breaker = _breaker(kv,breakers["feeder" if terminal.startswith("Busway") else "terminal"])
      for mode in ("inverter","battery"):
        key = f"{mode}:{terminal}"
        phase_case = critical["trains"][side]["fault_cases"][key]
        result[f"IT:{side}:{key}"] = _case(config,runs,pe,_phase(phase_case),mode,breaker,unavailable)
  switches = installed["assumptions"]["study"]["switches"]
  for key, reference in sorted(facility["fault_cases"].items()):
    side, source, terminal = key.split(":",1)[0], reference["source"], reference["primary"]
    circuit = installed["circuits"][terminal]
    pe = [paths["facility"][link] for link in sorted(reference["path_impedance_ohm"]) if link in paths["facility"]]
    mode = "utility" if source.startswith("utility:") else "generator"
    kind = "feed" if circuit["kind"] in ("aux","plant") else "command" if circuit["kind"]=="control" else "motor"
    result[f"FAC:{key}"] = _case(config,runs,pe,_phase(reference),mode,_breaker(kv,switches[kind]),unavailable)
    result[f"FAC:{key}"]["train"] = side
  electrical = lighting["assumptions"]["electrical"]
  aux_bonds = [name for name,row in installed["circuits"].items() if row["kind"]=="aux"]
  for terminal, branch in sorted(paths["lighting"].items()):
    for aux in aux_bonds:
      case_key = f"EL:{aux}:{terminal}"
      # Lighting equivalent source is referred at AuxFeed. The upstream PE must still be present.
      pe = [*branch]
      hot,cold = [sum(complex(electrical["feeder" if key=="PE-EL-FEED" else "branch"][f"{limit}_ohm_per_km"],
                           electrical["feeder" if key=="PE-EL-FEED" else "branch"]["x_ohm_per_km"])
                        *runs[key]["length_m"]/1000 for key in pe) for limit in ("max","min")]
      value = _case(config,runs,pe,hot,"lighting",electrical["feeder" if terminal=="EL-MDB" else "branch"],unavailable,cold)
      value["upstream_pe"] = paths["facility"][aux]
      if value["upstream_pe"] in unavailable:
        value["unavailable_pe"].append(value["upstream_pe"])
        value["envelope_applicability"] = "intact-path-only"
        value["failed"].append("upstream-metallic-path")
        value["status"] = "fail"
      result[case_key] = value
  return result


def build_earth_fault_study(context):
  inputs = (MODEL,CRITICAL_STUDY,FACILITY_STUDY,FACILITY,LIGHTING)
  model,critical,facility,installed,lighting = (staged_json(context,key) for key in inputs)
  issues = list(model["issues"])
  unavailable = set(model["assumptions"]["fault"]["unavailable_pe"])
  unknown = unavailable - model["links"].keys() - model["pe_runs"].keys()
  if unknown:
    raise ValueError(f"声明了未知的保护导体：{sorted(unknown)}")
  bonds = _continuity(model,unavailable,issues)
  electrodes = _electrodes(model["rods"],model["assumptions"]["electrodes"],issues)
  config = model["assumptions"]["fault"]
  for key in ("voltage_v","clearing_limit_s","lighting_magnetic_s"):
    number(config[key],key)
  if not 0<number(config["minimum_voltage_factor"],"minimum voltage factor")<=1<=number(
      config["maximum_voltage_factor"],"maximum voltage factor"):
    raise ValueError("相电压包络应围绕额定值。")
  for name,source in config["source_profiles"].items():
    for key in ("r_ohm","x_ohm"):
      if number(source[f"min_{key}"],f"{name} {key} minimum",zero=True)>number(
          source[f"max_{key}"],f"{name} {key} maximum",zero=True):
        raise ValueError(f"{name}: 故障电源阻抗上下界无效。")
    if "hold_s" in source:
      number(source["hold_s"],f"{name} sustain duration")
    if "min_limit_ka" in source or "max_limit_ka" in source:
      if number(source["min_limit_ka"],f"{name} minimum current limit")>number(
          source["max_limit_ka"],f"{name} maximum current limit"):
        raise ValueError(f"{name}: 限流上下界无效。")
  cases = _faults(model,critical,facility,installed,lighting,unavailable)
  for key,row in cases.items():
    if row["failed"]:
      issue(issues,"FAULT_DUTY",[key,*row["failed"]],"相壳故障回路未满足参考设计保护判据。")
  report = model_report(context,"earth-fault-study",{
    "input_digests":{key:file_digest(context.build_path/key) for key in inputs},
    "continuity":bonds,"electrodes":electrodes,"cases":cases,"issues":issues,
    "coverage":"Independent TN-S candidate source equivalents include source-side return impedance. "
               "Run impedances come from routed feeder and branch lengths. Electrode resistance is not credited to fault clearing. "
               "Inverter mode and battery mode are evaluated separately. The per-device phase-to-PE screen evaluates primary clearing, "
               "breaking capacity, and a conservative whole-time adiabatic bound. Certified earth-fault trip curves, earth-fault grading, "
               "source grounding and transfer pole schedule, touch/step voltage and site measurement remain for professional verification.",
  })
  table = _schedule(context,"aidc-earth-fault-cases.csv",
    ("case","mode","minimum_ka","maximum_ka","latest_s","required_mm2","installed_mm2","status","applicability","pe"),
    ((key,row["mode"],row["minimum_ka"],row["maximum_ka"],row["duty"]["latest_clear_s"],row["required_section_mm2"],
      row["minimum_installed_section_mm2"],row["status"],row["envelope_applicability"]," ".join(row["pe"])) for key,row in sorted(cases.items())))
  rods = _schedule(context,"aidc-earth-electrode-cases.csv",("case","resistance_ohm","limit_ohm","status"),
    ((key,row["resistance_ohm"],electrodes["limit_ohm"],row["status"]) for key,row in sorted(electrodes["cases"].items())))
  return TargetResult(report.status,(*report.artifacts,table,rods),report.diagnostics)


def build_earth_fault_check(context):
  study = staged_json(context,STUDY)
  return model_report(context,"earth-fault-check",{
    "study_digest":file_digest(context.build_path/STUDY),"cases":len(study["cases"]),"issues":study["issues"],
  },study["issues"])
