"""Check prescribed simultaneous loads against the routed FWS network."""

import csv
import math

from scipy.optimize import brentq

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


def _tree(model, leg, blocked, source):
  edges = {key: pipe for key, pipe in model["segments"].items() if pipe["leg"] == leg and key != blocked}
  adjacency = {node: [] for node in model["nodes"] if node[0] == leg[0].upper()}
  for key, pipe in edges.items():
    first, last = pipe["from"], pipe["to"]
    adjacency[first].append((last, key, 1))
    adjacency[last].append((first, key, -1))
  order = [source]
  parents = {source: None}
  for node in order:
    for neighbour, key, sign in adjacency[node]:
      if neighbour not in parents:
        parents[neighbour] = (node, key, sign)
        order.append(neighbour)
  tree_edges = {parents[node][1] for node in order[1:]}
  chords = [key for key, pipe in edges.items() if key not in tree_edges and pipe["from"] in parents]
  if len(chords) > 1:
    raise ValueError(f"{leg}: the declared FWS model supports at most one independent circuit per leg.")
  return order, parents, chords


def _tree_flows(order, parents, injection):
  remaining = {node: injection.get(node, 0.) for node in order}
  flows = {}
  for node in reversed(order[1:]):
    parent, key, sign = parents[node]
    flows[key] = -sign*remaining[node]
    remaining[parent] += remaining[node]
  return flows


def _port_node(leg, name):
  return f"{leg[0].upper()}-{name}-PORT"


def _solve_leg(model, leg, blocked, plant_flows, terminal_flows, fluid):
  root = model["roots"][leg]
  source = _port_node(leg, next(iter(plant_flows)))
  order, parents, chords = _tree(model, leg, blocked, source)
  unserved = [name for name in terminal_flows if _port_node(leg, name) not in parents]
  disconnected = [name for name in plant_flows if _port_node(leg, name) not in parents]
  if unserved or disconnected:
    return {"unserved": unserved, "disconnected": disconnected}
  direction = 1 if leg == "supply" else -1
  injections = {_port_node(leg, name): direction*flow for name, flow in plant_flows.items()}
  injections.update({_port_node(leg, name): -direction*flow for name, flow in terminal_flows.items()})
  routed = {key: _pipe(item) for key, item in model["segments"].items() if item["leg"] == leg}

  def loss(key, flow):
    pipe = routed[key]
    return pipe_loss(flow, diameter=pipe.diameter_m, length=pipe.length_m, roughness=pipe.roughness_m,
                     minor_loss=pipe.minor_loss_coefficient, density=fluid["density_kg_m3"],
                     viscosity=fluid["viscosity_pa_s"])/1000

  flows = dict.fromkeys(routed, 0.) | _tree_flows(order, parents, injections)
  if chords:
    chord, = chords
    edge = model["segments"][chord]
    cycle = {key: sign for key, sign in _tree_flows(order, parents, {edge["to"]: 1., edge["from"]: -1.}).items() if sign}
    cycle[chord] = 1.

    def pressure_error(circulation):
      return sum(sign*loss(key, flows[key] + sign*circulation) for key, sign in cycle.items())

    # Each tree flow is bounded by the total injection. At the interval ends
    # every circulating edge therefore has the same pressure-loss direction.
    total = sum(plant_flows.values())
    circulation = brentq(pressure_error, -total, total, xtol=1e-13)
    for key, sign in cycle.items():
      flows[key] += sign*circulation
  losses = {key: loss(key, flow) for key, flow in flows.items()}
  pressures = {source: 0.}
  mass = dict.fromkeys(order, 0.) | injections
  error = 0.
  for node in order[1:]:
    parent, key, sign = parents[node]
    pressures[node] = pressures[parent] - sign*losses[key]
  for key, flow in flows.items():
    edge = model["segments"][key]
    a, b = edge["from"], edge["to"]
    if key == blocked or a not in pressures:
      continue
    mass[a] -= flow
    mass[b] += flow
    error = max(error, abs(pressures[a] - pressures[b] - losses[key]))
  continuity = max(map(abs, mass.values()))
  if continuity > 1e-10 or error > 1e-7:
    raise ValueError(f"{leg}: FWS conservation residuals exceed tolerance ({continuity:g} m³/s, {error:g} kPa).")
  reference = pressures.get(root, 0.)
  pipes = {}
  for key, pipe in routed.items():
    velocity = abs(flows[key])/pipe.area_m2
    pipes[key] = {"flow_m3_s": flows[key], "loss_kpa": losses[key], "velocity_m_s": velocity,
                  "velocity_limit_m_s": pipe.max_velocity_m_s, "isolated": key == blocked,
                  "pass": velocity <= pipe.max_velocity_m_s}
  return {"unserved": [], "disconnected": [], "pipes": pipes, "continuity_m3_s": continuity,
          "pressure_residual_kpa": error, "pressure_kpa": {node: value-reference for node, value in pressures.items()}}


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
  blocked = state.get("isolated_segment")
  valve_pair = model["segments"][blocked].get("valves", []) if blocked else []
  scenario = {"unavailable_plants": sorted(state["off_plants"]), "unavailable_pumps": sorted(state["off_pumps"]),
              "isolated_segment": blocked, "closed_valves": valve_pair}
  pumps = {}
  for name in state["order"]:
    if name not in state["off_plants"]:
      available = [pump for pump in plants[name]["pumps"] if pump not in state["off_pumps"]]
      if available:
        pumps[name] = available[0]
    if len(pumps) == count:
      break
  if not pumps:
    return {**scenario, "status": "fail", "checks": {"dispatch": False}, "demand_kw": total, "duty": {},
            "unserved_liquid_kw": total}
  plant_flows = {name: total/len(pumps)/heat_per_flow for name in pumps}
  outlet_flows = {key: heat/heat_per_flow for key, heat in needs.items()}
  legs = {leg: _solve_leg(model, leg, blocked, plant_flows, outlet_flows, fluid) for leg in LEGS}
  unserved = sorted({name for entry in legs.values() for name in entry["unserved"]})
  disconnected = sorted({name for entry in legs.values() for name in entry["disconnected"]})
  if unserved or disconnected:
    return {**scenario, "status": "fail", "checks": {"connected-duty": False, "valve-pair": len(valve_pair) == 2},
            "demand_kw": total, "duty": {}, "unserved_terminals": unserved, "disconnected_plants": disconnected,
            "unserved_liquid_kw": sum(needs[name] for name in unserved)}
  results = legs["supply"]["pipes"] | legs["return"]["pipes"]

  def pressure_offset(name):
    return (legs["supply"]["pressure_kpa"][_port_node("supply", name)]
            - legs["return"]["pressure_kpa"][_port_node("return", name)])

  cdu_loss = EquipmentLoss(**config["cdu_loss"])
  outlet = {}
  for key, flow in outlet_flows.items():
    hydraulic = -pressure_offset(key)
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
    feed_loss = pressure_offset(name)
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
  if blocked:
    checks["valve-pair"] = len(valve_pair) == 2
  passed = all(checks.values())
  return {**scenario, "status": "pass" if passed else "fail", "checks": checks, "demand_kw": total,
          "requested_heat_kw": sum(entry["heat_kw"] for entry in duty.values()), "fluid": fluid,
          "unserved_liquid_kw": 0. if passed else None,
          "continuity_max_m3_s": max(entry["continuity_m3_s"] for entry in legs.values()),
          "loop_residual_max_kpa": max(entry["pressure_residual_kpa"] for entry in legs.values()),
          "common_header_kpa": header_head, "critical_terminal": governing,
          "node_pressure_relative_to_header_kpa": legs["supply"]["pressure_kpa"] | legs["return"]["pressure_kpa"],
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
  isolate = config.get("require_common_main_isolation", False)
  if type(isolate) is not bool:
    raise ValueError("Declare whether common main isolation is required as a boolean.")
  if isolate:
    normal = required_cases["normal"]
    required_cases.update({f"main-isolated:{key}": {**normal, "isolated_segment": key}
                           for key, segment in model["segments"].items() if segment["type"] in {"header", "spine"}})
  cases = {key: _case(model, plants, terminals, config, count, scenario)
           for key, scenario in required_cases.items()}
  issues = []
  for case, result in cases.items():
    if result["status"] != "pass":
      failures = [key for key, passed in result["checks"].items() if not passed]
      identities = [entry for section in ("pipes", "terminals", "duty")
                    for entry, check in result.get(section, {}).items() if not check["pass"]]
      identities.extend(result.get("unserved_terminals", []))
      identities.extend(result.get("disconnected_plants", []))
      issues.append({"rule": "AIDC_FWS_NETWORK_DUTY", "status": "fail", "objects": [case, *identities],
                     "message": f"{case}: FWS engineering checks failed: {', '.join(failures)}."})
  exposure = {}
  for pipe, segment in model["segments"].items():
    if segment["kind"] in {"distribution", "bypass"}:
      leg = segment["leg"]
      source = _port_node(leg, next(iter(plants)))
      _, connected, _ = _tree(model, leg, pipe, source)
      affected = [name for name in terminals if _port_node(leg, name) not in connected]
      exposure[pipe] = {"terminals": affected,
                        "unserved_liquid_kw": sum(terminals[name]["it_kw"]*config["liquid_fraction"] for name in affected)}
  result = model_report(context, "fws-network-study", {
    "route_digest": file_digest(context.build_path / MODEL), "basis": config["basis"],
    "coverage": "Quasi-steady fixed flows, signed loop equilibrium and individual common-main isolation.",
    "common_main_isolation_required": isolate,
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
  outputs = [artifact(context, "fws-pump-duty", schedule)]
  if isolate:
    matrix = schedule.with_name("aidc-fws-isolation-cases.csv")
    with matrix.open("w", encoding="utf-8", newline="") as stream:
      table = csv.writer(stream)
      table.writerow(("case", "isolated_segment", "closed_valves", "unserved_terminals", "unserved_liquid_kw",
                      "required_header_kpa", "max_pipe_velocity_m_s", "status"))
      for case, record in cases.items():
        if record["isolated_segment"]:
          table.writerow((case, record["isolated_segment"], " ".join(record["closed_valves"]),
                          " ".join(record.get("unserved_terminals", [])), record["unserved_liquid_kw"],
                          round(record["common_header_kpa"], 4) if "common_header_kpa" in record else "",
                          round(max(pipe["velocity_m_s"] for pipe in record["pipes"].values()), 4) if "pipes" in record else "",
                          record["status"]))
    outputs.append(artifact(context, "fws-isolation-cases", matrix))
  return TargetResult(result.status, (*result.artifacts, *outputs), result.diagnostics)
