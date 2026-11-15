"""Couple cooling dispatch to routed facility power and radial protection."""

from math import sqrt

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_electrical.protection import Breaker, CircuitSection, Impedance, check_radial_fault

from .cw_study import MODEL as CW
from .electrical import MODEL as TOPOLOGY, _quantity, _setting, _source
from .facility_power import MODEL, issue
from .roof import _schedule
from .spatial_design import MODELS, model_report, number, staged_json

STUDY = f"{MODELS}/facility-power-study.json"
UPSTREAM = f"{MODELS}/power-study.json"
FWS = f"{MODELS}/fws-network-study.json"


def _number(config, key):
  return number(config[key], key)


def _dispatch(topology, fws, cw, settings):
  train = topology["trains"]["A"]
  cells = {}
  for name, devices in train["cell_feeds"].items():
    source = cw["equipment"][name]
    if set(devices) != {source["pump"], source["tower"]}:
      raise ValueError(f"{name} power branches do not match the installed condenser-water cell.")
    plant = fws["plants"][name]
    installed = {key: row["rated_power_kw"] for key, row in plant["pumps"].items()}
    electrical_kw = plant["rating_kw"]/cw["parameters"]["cop"]
    pump = source["pump"]
    tower = source["tower"]
    tower_kw = _quantity(train["nodes"][tower]["parameters"], "fan_kw", "kW")
    cells[name] = {
      "pump": pump, "tower": tower, "tower_kw": tower_kw,
      "cw_motor_rated_kw": source["pump_rated_kw"], "fws_motor_ratings_kw": installed,
      "chiller_rated_input_kw": electrical_kw,
      "transfer_rated_kw": electrical_kw+max(installed.values())+source["pump_rated_kw"]+tower_kw+settings["fixed_cell_kw"],
    }
  return cells


def _states(cells, topology, fws, cw, installed, settings, auxiliary, command, diagnostics):
  result = {}
  auxiliary_kw = topology["facility"]["auxiliary_kw"]
  cooling_budget = topology["facility"]["cooling_plant_kw"]
  for side, normal in installed["trains"].items():
    # Land every cooling cell onto one available train. Never parallel the two installed sources.
    cases = [("train-only", "normal"), *(("cell-transfer-off", f"plant-off:{name}") for name in cells)]
    cases.extend(("motor-or-water-contingency", key) for key in cw["cases"].keys() if key.startswith(
      ("cw-pump-off:", "cw-tower-off:", "pump-off:", "main-isolated:")
    ))
    cases.append(("common-aux-off", "normal"))
    for condition, case in cases:
      water = cw["cases"][case]
      name = f"{side}:{condition}:{case}"
      water_case = fws["cases"][water["fws_dispatch"]]
      duties = {}
      for cell, load in water["duty"].items():
        select = cells[cell]
        cw_kw = cw["hydraulics"][cell]["estimated_motor_kw"]
        fan = select["tower_kw"]
        chw_kw = water_case["duty"][cell]["power_kw"]
        duties[cell] = {"compressor_kw": load["compressor_kw"], "cold_water_pump_kw": chw_kw,
                        "condenser_pump_kw": cw_kw, "tower_fan_kw": fan, "controls_kw": settings["fixed_cell_kw"],
                        "demand_kw": load["compressor_kw"]+chw_kw+cw_kw+fan+settings["fixed_cell_kw"],
                        "water_duty_kw": load["evaporator_kw"]}
      combination_kw = sum(row["demand_kw"] for row in duties.values())
      common_available = condition != "common-aux-off"
      total_kw = normal["critical_kw"]/installed["assumptions"]["ups_efficiency"] + combination_kw + (
        auxiliary_kw if common_available else 0
      )
      if water["status"] != "pass" or water_case["status"] != "pass" or len(duties) < 2:
        issue(diagnostics, "COOLING_CONTINUITY", [name], "Cooling dispatch does not meet the credited contingency.")
      if combination_kw > cooling_budget or total_kw > topology["facility_budget_kw"]:
        issue(diagnostics, "FACILITY_BUDGET", [name], "Cooling inputs exceed the declared single-train electrical budget.")
      result[name] = {"operating_train": side, "conditions": condition, "fws_case": water["fws_dispatch"],
                      "cw_case": case, "running_cells": duties, "plant_kw": combination_kw,
                      "single_train_input_kw": total_kw, "all_cooling_kw": sum(row["water_duty_kw"] for row in duties.values()),
                      "auxiliary_kw": auxiliary_kw if common_available else 0,
                      "common_command_available": common_available,
                      "generator_restart_credited": False,
                      "unavailable_facility_ids": [auxiliary, command] if not common_available else []}
  return result


def _amps(kw, base_voltage, pf):
  return kw / (sqrt(3)*base_voltage*pf)


def _drop(current, impedance, pf):
  return sqrt(3)*current*(impedance["r_ohm"]*pf+impedance["x_ohm"]*sqrt(1-pf*pf))


def _ratings(model, cells, states, topology, upstream, settings, auxiliary, command, issues):
  pf = _number(settings, "plant_power_factor")
  if pf > 1:
    raise ValueError("Power factor must not exceed one.")
  command_kw = _quantity(topology["trains"]["A"]["nodes"][command]["parameters"], "input_kw", "kW")
  auxiliary_cap = _quantity(topology["trains"]["A"]["nodes"][auxiliary]["parameters"], "rated_a", "A")
  command_budget = topology["facility"]["auxiliary_kw"]
  reports = {}
  volts = {side: data["voltage_kv"] for side, data in upstream["trains"].items()}
  if len(set(volts.values())) != 1:
    raise ValueError("Facility transfer inputs need the same voltage.")
  voltage = next(iter(volts.values()))
  for key, row in model["circuits"].items():
    name, kind, train = row["terminal"], row["kind"], row.get("train")
    cable = model["assumptions"]["cables"][kind]
    factor = _number(cable, "derate")
    if factor > 1:
      raise ValueError("Cable derating factor must not exceed one.")
    current_limit = _number(cable, "ampacity_a")*factor*cable["parallel"]
    if kind == "plant":
      kw = cells[name]["transfer_rated_kw"]
      demand = max(s["running_cells"].get(name, {}).get("demand_kw", 0) for s in states.values())
      current_limit = min(current_limit, _number(settings, "transfer_rating_a"))
    elif kind == "aux":
      kw = demand = command_budget
      current_limit = min(current_limit, auxiliary_cap)
    elif kind == "control":
      kw = demand = command_kw
    else:
      cell = cells[row["cell"]]
      kw = cell["cw_motor_rated_kw"] if kind == "pump" else cell["tower_kw"]
      demand = max(s["running_cells"].get(row["cell"], {}).get(f"condenser_{kind}_kw" if kind == "pump" else "tower_fan_kw", 0)
                   for s in states.values())
    amps = _amps(kw, voltage, pf)
    drop = _drop(amps, row["impedance_ohm"], pf)
    if current_limit < amps:
      issue(issues, "AMPACITY", [key, name], f"{key} needs {amps:.2f} A, with {current_limit:.2f} A installed.")
    reports[key] = {"kind": kind, "train": train, "terminal": name, "rated_demand_kw": kw, "maximum_run_kw": demand,
                    "rated_current_a": amps, "installed_ampacity_a": current_limit,
                    "branch_drop_v": drop, "maximum_total_drop_pct": 0.}
  for side, train in topology["trains"].items():
    for facility, incoming in train["facility_feeds"].items():
      last = train["cell_feeds"].get(facility, train["shared_feeds"])
      first = reports[incoming]
      for link in last.values():
        target = reports[link]
        drop_pct = (first["branch_drop_v"]+target["branch_drop_v"])/(voltage*10)
        if drop_pct > _number(settings, "maximum_voltage_drop_pct"):
          issue(issues, "VOLTAGE_DROP", [incoming, link], f"End-to-end voltage drop {drop_pct:.3f}% exceeds the design limit.")
        target["maximum_total_drop_pct"] = max(target["maximum_total_drop_pct"], drop_pct)
        first["maximum_total_drop_pct"] = max(first["maximum_total_drop_pct"], drop_pct)
  return reports


def _upstream_sections(source_case, upstream, train, voltage):
  controls = {train["utility_route"][1]: "main", train["utility_route"][-1]: "transfer",
              **dict.fromkeys(train["genset_feeders"].values(), "generator")}
  result = []
  for key in source_case["path"]:
    impedance = source_case["path_impedance_ohm"][key]
    setting = controls.get(key)
    rating = _quantity(train["nodes"][train["switchboard"]]["parameters"], "breaking_ka", "kA") if setting == "main" else None
    breaker = _setting(upstream["assumptions"], setting, voltage, interrupt=rating) if setting else None
    result.append(CircuitSection(key, voltage, Impedance(impedance["r"], impedance["x"]), breaker))
  return tuple(result)


def _protected_paths(topology, model, upstream, settings, issues):
  result = {}

  def local(link, name, voltage):
    data = settings["switches"][name]
    device = Breaker(voltage, _number(data, "interrupt_ka"), tuple(tuple(row) for row in data["curve"]))
    return CircuitSection(link, voltage, Impedance(**model["circuits"][link]["impedance_ohm"]), device)

  for side, train in topology["trains"].items():
    power = upstream["trains"][side]
    volts = power["voltage_kv"]
    for name, case in power["fault_cases"].items():
      if not name.endswith(":transfer"):
        continue
      group = "utility" if name.startswith("utility:") else "generator"
      source = _source(upstream["assumptions"], group, case["source_voltage_kv"])
      leading = _upstream_sections(case, upstream, train, volts)
      duty = {}
      for terminal, link in train["facility_feeds"].items():
        feed = (*leading, local(link, "feed", volts))
        duty[link] = feed
        for output in train["cell_feeds"].get(terminal, train["shared_feeds"]).values():
          duty[output] = (*feed, local(output, "command" if terminal == train["shared_bus"] else "motor", volts))
      for key, path in duty.items():
        row = check_radial_fault(source, volts, path, primary_max_clear_s=_number(settings, "primary_max_s"),
                                 backup_max_clear_s=upstream["assumptions"]["clearing"]["backup_s"],
                                 grading_s=_number(settings, "grading_min_s"))
        identity = f"{side}:{name}:{key}"
        if row["status"] != "pass":
          issue(issues, "PROTECTION", [identity],
                "Protection failed: " + ", ".join(key for key, passed in row["checks"].items() if not passed))
        result[identity] = {"feed_train": side, "source": name, "location": model["circuits"][key]["terminal"], **row}
  return result


def build_facility_power_study(context):
  model, topology, upstream, fws, cw = [staged_json(context, path) for path in (MODEL, TOPOLOGY, UPSTREAM, FWS, CW)]
  settings = model["assumptions"]["study"]
  train = topology["trains"]["A"]
  auxiliary = train["shared_bus"]
  command, = train["shared_feeds"]
  issues = []
  dispatch = _dispatch(topology, fws, cw, settings)
  states = _states(dispatch, topology, fws, cw, upstream, settings, auxiliary, command, issues)
  ratings = _ratings(model, dispatch, states, topology, upstream, settings, auxiliary, command, issues)
  selected = _protected_paths(topology, model, upstream, settings, issues)
  report = model_report(context, "facility-power-study", {
    "input_digests": {path: file_digest(context.build_path / path) for path in (MODEL, TOPOLOGY, UPSTREAM, FWS, CW)},
    "cell_ratings": dispatch, "states": states, "ratings": ratings, "fault_cases": selected,
    "issues": issues, "checks": "fail" if issues else "pass",
    "coverage": "400 V AC positive-sequence study of open transition cells and coincident maximum installed duty. "
                "Transfer and load interruption dynamics, arc flash, ground faults and actual protective-device "
                "coordination need manufacturer and commissioning checks. The common generator command remains a "
                "recorded dependency. Its failure receives no black-start credit.",
  })
  ampacity = _schedule(context, "aidc-facility-power-duty.csv",
                       ("link", "kind", "load_kw", "peak_run_kw", "required_a", "ampacity_a", "end_to_end_drop_pct"),
                       ((key, value["kind"], value["rated_demand_kw"], value["maximum_run_kw"],
                         value["rated_current_a"], value["installed_ampacity_a"], value["maximum_total_drop_pct"])
                        for key, value in ratings.items()))
  faults = _schedule(context, "aidc-facility-power-faults.csv",
                     ("case", "location", "current_min_ka", "current_max_ka", "primary", "backup", "status"),
                     ((key, row["location"], row["current_min_ka"], row["current_max_ka"], row["primary"], row["backup"],
                       row["status"]) for key, row in selected.items()))
  return TargetResult(report.status, (*report.artifacts, ampacity, faults), report.diagnostics)


def build_facility_power_check(context):
  study = staged_json(context, STUDY)
  return model_report(context, "facility-power-check", {
    "study_digest": file_digest(context.build_path / STUDY), "issues": study["issues"],
  }, study["issues"])
