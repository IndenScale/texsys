"""Check prescribed simultaneous loads against the routed FWS network."""

import csv
import math

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_hvac_dynamics.facility_hydraulics import EquipmentLoss, PipeHydraulics
from texsys_hvac_dynamics.hydraulics import pipe_loss, pump_head_m

from .checks import load_design
from .fws_network import LEGS, MODEL
from .spatial_design import artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


_G = 9.80665


def _rated(instance, key, unit):
  record = instance.parameters.get(key, {})
  if record.get("unit") != unit:
    raise ValueError(f"{instance.name}: {key} needs a rating in {unit}.")
  return number(record.get("value"), f"{instance.name}.{key}")


def _equipment(design, model):
  plants, terminals = {}, {}
  for name in model["ports"]["plants"]:
    assembly = design.instances[name]
    supply, = assembly.specification["boundary"]["fws_supply"]
    chiller, port = supply.split(".")
    unit = assembly.children[chiller]
    if unit.family != "water-cooled-chiller" or port != "chw_supply":
      raise ValueError(f"{name}: the plant supply must come from its declared chiller.")
    pumps = {}
    edges = assembly.definition["connections"]
    for endpoint in sorted(assembly.specification["boundary"]["fws_return"]):
      pump, port = endpoint.split(".")
      item = assembly.children[pump]
      if item.family != "circulation-pump" or port != "in":
        raise ValueError(f"{name}: return boundary must feed the declared parallel pumps.")
      outgoing, = [edge for edge in edges if edge["from"] == f"{pump}.out"]
      header = outgoing["to"].split(".")[0]
      if not any(edge["from"].split(".")[0] == header and edge["to"] == f"{chiller}.chw_return" for edge in edges):
        raise ValueError(f"{name}/{pump} does not serve the chiller return path.")
      pumps[f"{name}/{pump}"] = {"rated_flow_m3_s": _rated(item, "flow_m3h", "m3/h")/3600,
                                  "rated_head_m": _rated(item, "head_m", "m"),
                                  "rated_power_kw": _rated(item, "power_kw", "kW")}
    if len(pumps) < 2:
      raise ValueError(f"{name} must bind distinct duty and standby pumps.")
    plants[name] = {"rating_kw": _rated(unit, "cooling_kw", "kW"),
                    "chiller": f"{name}/{chiller}", "pumps": pumps,
                    "chiller_design_flow_m3_s": _rated(unit, "chw_flow_m3h", "m3/h")/3600}

  for name in model["ports"]["terminals"]:
    assembly = design.instances[name]
    sides = {}
    for leg, port in (("supply", "facility_in"), ("return", "facility_out")):
      sides[leg] = set()
      for endpoint in assembly.specification["boundary"][f"fws_{leg}"]:
        child, interface = endpoint.split(".")
        if interface != port or assembly.children[child].family != "coolant-distribution-unit":
          raise ValueError(f"{name}: {leg} must connect the facility sides of its CDUs.")
        sides[leg].add(child)
    if len(sides["supply"]) < 2 or sides["return"] != sides["supply"]:
      raise ValueError(f"{name}: expected paired facility paths to two independent CDUs.")
    terminals[name] = {"it_kw": _rated(assembly, "it_load_kw", "kW"),
                       "cdu_kw": {f"{name}/{child}": _rated(assembly.children[child], "nameplate_capacity", "kW")
                                  for child in sorted(sides["supply"])}}
  return plants, terminals


def _cases(plants, settings):
  order = list(settings["duty_order"])
  count = settings["duty_count"]
  if set(order) != plants.keys() or len(order) != len(plants) or type(count) is not int or not 1 <= count < len(plants):
    raise ValueError("Declare a preferred order for every plant, with at least one standby plant.")
  locked = set(settings.get("unavailable_plants", []))
  disabled = set(settings.get("unavailable_pumps", []))
  known_pumps = {pump for plant in plants.values() for pump in plant["pumps"]}
  if locked - plants.keys() or disabled - known_pumps:
    raise ValueError("Unavailable equipment must identify an SDL plant or pump.")
  cases = {"normal": {"off_plants": locked, "off_pumps": disabled, "order": order}}
  for name in order:
    cases[f"plant-off:{name}"] = {"off_plants": locked | {name}, "off_pumps": disabled, "order": order}
    preferred = [name, *(other for other in order if other != name)]
    for pump in plants[name]["pumps"]:
      cases[f"pump-off:{pump}"] = {"off_plants": locked, "off_pumps": disabled | {pump}, "order": preferred}
  return count, cases


def _pipe(segment):
  spec = segment["hydraulic_section"]
  return PipeHydraulics(segment["length_m"], spec["inside_m"], spec["roughness_m"],
                        segment["minor_k"], spec["max_velocity_m_s"])


def _flows(model, pump_flows, outlet_flows):
  flows = dict.fromkeys(model["segments"], 0.)
  for role, demand in (("plants", pump_flows), ("terminals", outlet_flows)):
    for name, flow in demand.items():
      for leg in LEGS:
        for key in model["paths"][role][name][leg]:
          flows[key] += flow
  residuals = dict.fromkeys(model["nodes"], 0.)
  for key, flow in flows.items():
    pipe = model["segments"][key]
    residuals[pipe["from"]] -= flow
    residuals[pipe["to"]] += flow
  for name, flow in pump_flows.items():
    residuals[f"S-{name}-PORT"] += flow
    residuals[f"R-{name}-PORT"] -= flow
  for name, flow in outlet_flows.items():
    residuals[f"S-{name}-PORT"] -= flow
    residuals[f"R-{name}-PORT"] += flow
  error = max(map(abs, residuals.values()))
  if error > 1e-10:
    raise ValueError("An FWS node does not satisfy simultaneous flow conservation.")
  return flows, error


def _case(model, plants, terminals, config, count, state):
  fluid = {key: number(config[key], key) for key in ("density_kg_m3", "viscosity_pa_s", "cp_kj_kg_k", "delta_t_k")}
  density = fluid["density_kg_m3"]
  heat_per_flow = density * fluid["cp_kj_kg_k"] * fluid["delta_t_k"]
  fraction = number(config["liquid_fraction"], "facility liquid fraction")
  if fraction > 1:
    raise ValueError("Liquid share must not exceed the declared IT load.")
  efficiency = number(config["pump_efficiency"], "pump efficiency")
  minimum_speed = number(config["min_speed_ratio"], "minimum pump speed")
  derating = number(config["chiller_capacity_factor"], "chiller capacity factor")
  if max(efficiency, minimum_speed, derating) > 1:
    raise ValueError("Declared efficiency, speed ratio and capacity factor must not exceed one.")
  reserve = number(config["head_reserve_kpa"], "required pressure reserve", zero=True)
  valve_limit = number(config["balance_max_valve_drop_kpa"], "balancing valve pressure limit")
  shutoff_ratio = number(config["pump_shutoff_ratio"], "pump shutoff to rated head ratio")
  if shutoff_ratio <= 1:
    raise ValueError("Assumed shutoff head must exceed the rated head.")
  needs = {key: record["it_kw"]*fraction for key, record in terminals.items()}
  total = sum(needs.values())
  pumps = {}
  for name in state["order"]:
    if name not in state["off_plants"]:
      available = [pump for pump in plants[name]["pumps"] if pump not in state["off_pumps"]]
      if available:
        pumps[name] = available[0]
    if len(pumps) == count:
      break
  if not pumps:
    return {"status": "fail", "checks": {"dispatch": False}, "demand_kw": total, "duty": {},
            "unavailable_plants": sorted(state["off_plants"]), "unavailable_pumps": sorted(state["off_pumps"])}
  plant_flows = {name: total/len(pumps)/heat_per_flow for name in pumps}
  outlet_flows = {key: heat/heat_per_flow for key, heat in needs.items()}
  flows, continuity = _flows(model, plant_flows, outlet_flows)
  pipes = {key: _pipe(item) for key, item in model["segments"].items()}
  head, results = {}, {}
  for key, pipe in pipes.items():
    flow = flows[key]
    loss = pipe_loss(flow, diameter=pipe.diameter_m, length=pipe.length_m, roughness=pipe.roughness_m,
                     minor_loss=pipe.minor_loss_coefficient, density=density, viscosity=fluid["viscosity_pa_s"])/1000
    velocity = flow / pipe.area_m2
    head[key] = loss
    results[key] = {"flow_m3_s": flow, "loss_kpa": loss, "velocity_m_s": velocity,
                    "velocity_limit_m_s": pipe.max_velocity_m_s, "pass": velocity <= pipe.max_velocity_m_s}

  def route_loss(role, name):
    return sum(head[key] for leg in LEGS for key in model["paths"][role][name][leg])

  cdu_loss = EquipmentLoss(**config["cdu_loss"])
  outlet = {}
  for key, flow in outlet_flows.items():
    hydraulic = route_loss("terminals", key)
    equipment = cdu_loss.pressure_pa(flow)/1000
    outlet[key] = {"heat_kw": needs[key], "flow_m3_s": flow, "pipe_kpa": hydraulic,
                   "cdu_kpa": equipment, "required_kpa": hydraulic + equipment,
                   "single_cdu_capacity_kw": min(terminals[key]["cdu_kw"].values())}
  governing = max(outlet, key=lambda key: outlet[key]["required_kpa"])
  header_head = outlet[governing]["required_kpa"] + reserve
  for record in outlet.values():
    record["balancing_kpa"] = header_head - record["required_kpa"]
    record["pass"] = record["heat_kw"] <= record["single_cdu_capacity_kw"] and record["balancing_kpa"] <= valve_limit
  duty = {}
  for name, pump in pumps.items():
    plant, rating = plants[name], plants[name]["pumps"][pump]
    flow = plant_flows[name]
    chiller_loss = EquipmentLoss(plant["chiller_design_flow_m3_s"],
                                 number(config["chiller_rated_drop_kpa"], "chiller pressure loss", zero=True))
    equipment_loss = chiller_loss.pressure_pa(flow)/1000
    feed_loss = route_loss("plants", name)
    required = header_head + feed_loss + equipment_loss
    shutoff = shutoff_ratio*rating["rated_head_m"]
    curve = (shutoff - rating["rated_head_m"]) / rating["rated_flow_m3_s"]**2
    speed = math.sqrt((required*1000/(density*_G) + curve*flow**2)/shutoff)
    max_head = pump_head_m(flow, shutoff, curve, 1)*density*_G/1000
    power_kw = flow*required/efficiency
    heat = flow*heat_per_flow
    checks = {
      "speed": minimum_speed <= speed <= 1,
      "pump-flow": flow <= rating["rated_flow_m3_s"]*speed,
      "motor": power_kw <= rating["rated_power_kw"],
      "chiller-flow": flow <= plant["chiller_design_flow_m3_s"],
      "cooling-rating": heat <= plant["rating_kw"]*derating,
    }
    duty[name] = {"pump": pump, "flow_m3_s": flow, "heat_kw": heat, "pipe_kpa": feed_loss,
                  "chiller_kpa": equipment_loss, "required_kpa": required, "available_kpa": max_head,
                  "speed_ratio": speed, "power_kw": power_kw, "checks": checks, "pass": all(checks.values())}
  checks = {"dispatch": len(pumps) == count, "pipe-velocity": all(x["pass"] for x in results.values()),
            "terminal-duty": all(x["pass"] for x in outlet.values()), "plant-duty": all(x["pass"] for x in duty.values())}
  return {"status": "pass" if all(checks.values()) else "fail", "checks": checks, "demand_kw": total,
          "unavailable_plants": sorted(state["off_plants"]), "unavailable_pumps": sorted(state["off_pumps"]),
          "requested_heat_kw": sum(entry["heat_kw"] for entry in duty.values()), "fluid": fluid,
          "continuity_max_m3_s": continuity, "common_header_kpa": header_head, "critical_terminal": governing,
          "duty": duty, "pipes": results, "terminals": outlet}


def build_network_study(context):
  path = source_path(context, "fws-network-study.json")
  if not path.is_file():
    return missing_input(context, path, "fws-network-study")
  config = read_versioned(path)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("FWS water study needs an explicit assumed engineering basis.")
  model = staged_json(context, MODEL)
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "fws-network-study", {}, design.diagnostics)
  plants, terminals = _equipment(design, model)
  count, required_cases = _cases(plants, config)
  cases = {key: _case(model, plants, terminals, config, count, scenario)
           for key, scenario in required_cases.items()}
  issues = []
  for case, result in cases.items():
    if result["status"] != "pass":
      failures = [key for key, passed in result["checks"].items() if not passed]
      identities = [entry for section in ("pipes", "terminals", "duty")
                    for entry, check in result.get(section, {}).items() if not check["pass"]]
      issues.append({"rule": "AIDC_FWS_NETWORK_DUTY", "status": "fail", "objects": [case, *identities],
                     "message": f"{case}: FWS engineering checks failed: {', '.join(failures)}."})
  # Radial downstream geometry gives a single route for each terminal in each leg.
  # Isolation screening is separate from the declared source/pump duty acceptance cases.
  exposure = {}
  for pipe, segment in model["segments"].items():
    if segment["kind"] == "distribution":
      affected = [name for name, paths in model["paths"]["terminals"].items() if pipe in paths[segment["leg"]]]
      exposure[pipe] = {"terminals": affected,
                        "unserved_liquid_kw": sum(terminals[name]["it_kw"]*config["liquid_fraction"] for name in affected)}
  result = model_report(context, "fws-network-study", {
    "route_digest": file_digest(context.build_path / MODEL), "basis": config["basis"],
    "coverage": "Closed loop with controlled equal plant shares and fixed terminal flow setpoints.",
    "plants": plants, "terminals": terminals, "cases": cases,
    "distribution_isolation_exposure": exposure,
  }, issues)
  schedule = context.build_path / "schedules/aidc-fws-pump-duty.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("case", "plant", "pump", "flow_l_s", "heat_kw", "pressure_required_kpa",
                    "pressure_at_full_speed_kpa", "speed_ratio", "power_kw", "case_status"))
    for case, record in cases.items():
      for plant, duty in record["duty"].items():
        table.writerow((case, plant, duty["pump"], round(1000*duty["flow_m3_s"], 4), round(duty["heat_kw"], 4),
                        round(duty["required_kpa"], 4), round(duty["available_kpa"], 4), round(duty["speed_ratio"], 4),
                        round(duty["power_kw"], 4), record["status"]))
  return TargetResult(result.status, (*result.artifacts, artifact(context, "fws-pump-duty", schedule)),
                      result.diagnostics)
