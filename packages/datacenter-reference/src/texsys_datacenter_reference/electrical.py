"""Resolve the AIDC power topology and check the declared duty and fault cases."""

import csv
import math

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_electrical.protection import Breaker, CircuitSection, FaultSource, Impedance, SourceCondition, check_radial_fault

from .checks import load_design
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


MODEL = f"{MODELS}/power-topology.json"


def _endpoint(endpoint, instances, expanded):
  node, port = endpoint.split(".", 1)
  instance = instances[node]
  kind = instance.ports[port]["kind"]
  if node in expanded:
    boundary, = instance.specification["boundary"][port]
    leaf, sub_port = boundary.split(".", 1)
    if instance.children[leaf].ports[sub_port]["kind"] != kind:
      raise ValueError(f"Boundary kind does not match at {endpoint}.")
    return f"{node}/{leaf}.{sub_port}", kind
  return endpoint, kind


def _edges(instances, connections, document, *, owner="", expanded=()):
  edges = []
  prefix = owner + "/" if owner else ""
  for connection in connections:
    start, first = _endpoint(connection["from"], instances, expanded)
    end, second = _endpoint(connection["to"], instances, expanded)
    if first != second:
      raise ValueError(f"Incompatible SDL link {prefix}{connection['id']}.")
    if first in {"ac-power", "dc-power"}:
      edges.append({"id": prefix + connection["id"], "from": prefix + start, "to": prefix + end, "kind": first,
                    "physical_from": prefix + connection["from"], "physical_to": prefix + connection["to"],
                    "source": document, "source_link": connection["id"]})
  return edges


def _node(endpoint):
  return endpoint.split(".", 1)[0]


def _link(edges, a, b):
  entry = [edge for edge in edges if _node(edge["from"]) == a and _node(edge["to"]) == b]
  if len(entry) != 1:
    raise ValueError(f"Expected one identified power link from {a} to {b}, found {len(entry)}.")
  return entry[0]["id"]


def _quantity(params, key, unit, *, zero=False):
  value = params.get(key, {})
  if value.get("unit") != unit:
    raise ValueError(f"{key} must be declared in {unit}.")
  return number(value.get("value"), key, zero=zero)


def _train(design, side, spec):
  utility, generator, skid = (spec[k] for k in ("utility", "generator", "power_container"))
  local = {name: design.instances[name] for name in (generator, skid)}
  if (design.instances[utility].family != "utility-feed"
      or local[generator].family != "generator-container" or local[skid].family != "power-container"):
    raise ValueError(f"Train {side} must name its actual utility, generator and power container.")
  net = design.nets[spec["net"]]
  auxiliary = design.nets[spec["shared_net"]]
  if net["kind"] != "ac-power" or auxiliary["kind"] != "ac-power":
    raise ValueError("Train networks must be declared AC nets.")
  ids = set(net["connections"] + auxiliary["connections"])
  edges = _edges(design.instances, (c for c in design.connections if c["id"] in ids), "system.sdl.json", expanded=local)
  if {edge["id"] for edge in edges} != ids:
    raise ValueError(f"Power net {side} does not resolve all declared AC links.")
  for name, instance in local.items():
    edges.extend(_edges(instance.children, instance.definition["connections"], instance.document, owner=name))
  topology = {name: instance for name, instance in design.instances.items() if name not in local}
  topology.update({f"{owner}/{name}": instance for owner, unit in local.items() for name, instance in unit.children.items()})
  identities = {_node(ref) for edge in edges for ref in (edge["from"], edge["to"])}
  nodes = {name: {"family": topology[name].family, "parameters": topology[name].parameters} for name in identities}

  def by_family(family):
    return sorted(name for name in nodes if nodes[name]["family"] == family)

  transformer, = by_family("dry-type-transformer")
  switchboard, = by_family("lv-switchboard")
  ats, = by_family("ats")
  transfer, = by_family("TransferBus")
  ups_in, = by_family("UpsBus")
  ups_out, = by_family("OutputBus")
  gen_bus, = by_family("GenBus")
  utility_route = [_link(edges, a, b) for a, b in zip((utility, transformer, switchboard, ats),
                                                      (transformer, switchboard, ats, transfer), strict=True)]
  standby_route = [_link(edges, gen_bus, ats), utility_route[-1]]
  _link(edges, transfer, ups_in)
  gensets = by_family("diesel-generator")
  if len(gensets) < 2:
    raise ValueError(f"Train {side} needs a duty and a standby generator.")
  genset_feeders = {name: _link(edges, name, gen_bus) for name in gensets}
  inverters = {}
  batteries = by_family("battery-cabinet")
  for name in by_family("ups-module"):
    incoming = [edge for edge in edges if _node(edge["to"]) == name and edge["kind"] == "dc-power"]
    battery_edge, = incoming
    battery = _node(battery_edge["from"])
    if battery not in batteries:
      raise ValueError(f"Expected a battery cabinet at {name}.")
    inverters[name] = {"battery": battery, "links": [_link(edges, ups_in, name), _link(edges, name, ups_out),
                                                     battery_edge["id"]]}
  if not inverters or {item["battery"] for item in inverters.values()} != set(batteries):
    raise ValueError(f"Train {side} is missing its paired inverters and batteries.")
  terminals = sorted(name for name, instance in design.instances.items() if instance.family == "it-container")
  feeders, served = {}, set()
  for bus in by_family("busway"):
    terminals_on_bus = {}
    feed = _link(edges, ups_out, bus)
    for edge in edges:
      if _node(edge["from"]) != bus:
        continue
      destination, port = edge["to"].split(".", 1)
      if destination not in terminals or destination in served or port != f"power_{side.lower()}":
        raise ValueError(f"Expected a unique IT terminal on train {side} at {edge['to']}.")
      served.add(destination)
      terminals_on_bus[destination] = edge["id"]
    if not terminals_on_bus:
      raise ValueError(f"Busway {bus} does not feed any IT terminals.")
    feeders[bus] = {"feed": feed, "terminals": terminals_on_bus}
  if not served or served != set(terminals):
    raise ValueError(f"Train {side} does not cover all IT terminals: {sorted(set(terminals)-served)}.")
  actual_ups_edges = {edge["id"] for edge in edges if _node(edge["from"]) == ups_out}
  if actual_ups_edges != {record["feed"] for record in feeders.values()}:
    raise ValueError(f"Train {side} UPS output must feed only the critical IT busways.")
  facility_families = {"cooling-container", "facility-aux-transfer"}
  facilities = sorted(name for name in nodes if nodes[name]["family"] in facility_families)
  expected = {name for name, instance in design.instances.items() if instance.family in facility_families}
  if set(facilities) != expected:
    raise ValueError(f"Train {side} must include every facility branch.")
  facility_feeds = {name: _link(edges, transfer, name) for name in facilities}
  shared, = by_family("facility-aux-transfer")
  shared_feeds = {_node(edge["to"]): edge["id"] for edge in edges if _node(edge["from"]) == shared}
  if set(shared_feeds) != set(by_family("generator-command-panel")) or len(shared_feeds) != 1:
    raise ValueError("The common standby command must be the auxiliary board's unique modeled consumer.")
  cell_feeds = {}
  for name in by_family("cooling-container"):
    links = {edge["from"].split(".")[1]: edge for edge in edges if _node(edge["from"]) == name}
    targets = {"pump_power_out": "condenser-water-pump", "tower_power_out": "cooling-tower"}
    if links.keys() != targets.keys() or any(nodes[_node(links[port]["to"])]["family"] != family
                                            for port, family in targets.items()):
      raise ValueError(f"{name} needs two local outgoing feeds to its condenser-water equipment.")
    cell_feeds[name] = {_node(edge["to"]): edge["id"] for edge in links.values()}
  served = [item for outputs in cell_feeds.values() for item in outputs]
  if len(set(served)) != len(served) or set(served) != set(by_family("condenser-water-pump")+by_family("cooling-tower")):
    raise ValueError("All cooling cells must own their condenser pump and tower without a common feed.")
  for terminal in terminals:
    it = design.instances[terminal]
    destination, = it.specification["boundary"][f"power_{side.lower()}"]
    feed = _node(destination)
    equipment = {_node(edge["to"]) for edge in it.definition["connections"]
                 if _node(edge["from"]) == feed}
    ancillary = {name: _quantity(it.children[name].parameters, "electrical_input", "kW") for name in equipment
                 if it.children[name].family in {"precision-air-unit", "coolant-distribution-unit"}}
    if not ancillary:
      raise ValueError(f"Terminal {terminal} is missing its cooling duty for train {side}.")
    nodes[terminal]["ancillary_kw"] = ancillary

  return {"nodes": nodes, "edges": edges, "utility": utility, "generator_bus": gen_bus, "transformer": transformer,
          "switchboard": switchboard, "ats": ats, "transfer": transfer, "ups_in": ups_in, "ups_out": ups_out,
          "utility_route": utility_route, "standby_route": standby_route, "inverters": inverters,
          "genset_feeders": genset_feeders, "feeders": feeders, "facility_feeds": facility_feeds,
          "shared_bus": shared, "shared_feeds": shared_feeds, "cell_feeds": cell_feeds}


def build_power_topology(context):
  path = source_path(context, "electrical.json")
  if not path.is_file():
    return missing_input(context, path, "power-topology")
  spec = read_versioned(path)
  if set(spec["trains"]) != {"A", "B"}:
    raise ValueError("The project must declare and identify both A and B electrical trains.")
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "power-topology", {}, design.diagnostics)
  trains = {side: _train(design, side, record) for side, record in sorted(spec["trains"].items())}
  for key in ("utility", "ats", "transfer", "ups_out", "generator_bus"):
    if trains["A"][key] == trains["B"][key]:
      raise ValueError(f"A and B share {key}: {trains['A'][key]}.")
  if set(trains["A"]["feeders"]) & set(trains["B"]["feeders"]):
    raise ValueError("A and B share a critical busway.")
  return model_report(context, "power-topology", {"trains": trains, "facility": design.placement["facility_load_breakdown"],
                                                 "facility_budget_kw": design.placement["facility_load_kw"]})


def _setting(config, key, voltage, *, interrupt=None):
  record = config["breakers"][key]
  rating = number(record["interrupt_ka"], f"{key}.interrupt_ka") if interrupt is None else interrupt
  return Breaker(voltage, rating, tuple(tuple(row) for row in record["curve"]))


def _source(config, key, voltage, base_current=None):
  record = config["sources"][key]

  def bound(label):
    data = record[label]
    limit = data.get("current_multiplier")
    if limit is not None and base_current is None:
      raise ValueError(f"{key}: source has a current limit without a nameplate current.")
    return SourceCondition(Impedance(**data["impedance_ohm"]), data["voltage_factor"],
                           number(limit, "fault current multiplier") * base_current if limit is not None else None)

  return FaultSource(voltage, bound("minimum"), bound("maximum"), sustain_s=record.get("sustain_s"))


def _power_study(side, train, project, config, busways):
  nodes = train["nodes"]

  def get(node, key, unit):
    return _quantity(nodes[node]["parameters"], key, unit)

  tx = train["transformer"]
  voltage = get(tx, "secondary_kv", "kV")
  kva = get(tx, "rated_kva", "kVA")
  pf = number(config["power_factor"], "load power factor")
  efficiency = number(config["ups_efficiency"], "UPS efficiency")
  if max(pf, efficiency) > 1:
    raise ValueError("Power factor and efficiency must not exceed 1.")
  amps_per_kw = 1 / (math.sqrt(3) * voltage * pf)
  ups_kva = sum(get(name, "rated_kva", "kVA") for name in train["inverters"])
  battery_kwh = sum(get(record["battery"], "energy_kwh", "kWh") for record in train["inverters"].values())
  terminal_kw = {name: get(name, "it_load_kw", "kW") + sum(nodes[name]["ancillary_kw"].values())
                 for record in train["feeders"].values() for name in record["terminals"]}
  critical_kw = sum(terminal_kw.values())
  facility_kw = sum(number(project["facility"][key], key, zero=True) for key in ("cooling_plant_kw", "auxiliary_kw"))
  input_kw = critical_kw/efficiency + facility_kw
  generator_ratings = {name: get(name, "prime_kw", "kW") for name in train["genset_feeders"]}
  if any(not math.isclose(get(name, "voltage_v", "V")/1000, voltage) for name in generator_ratings):
    raise ValueError(f"Train {side} generator and transformer secondary voltages disagree.")
  autonomy = battery_kwh*60/critical_kw
  limits = {
    "ups": (critical_kw, ups_kva*pf, "kW"),
    "autonomy": (number(config["min_battery_minutes"], "minimum battery autonomy"), autonomy, "min"),
    "transformer": (input_kw, kva*pf, "kW"),
    "declared-facility-budget": (input_kw, number(project["facility_budget_kw"], "facility budget"), "kW"),
    **{name: (input_kw, rating, "kW") for name, rating in generator_ratings.items()},
    **{name: (input_kw*amps_per_kw, get(name, "rated_a", "A"), "A") for name in (train["switchboard"], train["ats"])},
    **{name: (sum(terminal_kw[t] for t in record["terminals"]) * amps_per_kw, get(name, "rated_a", "A"), "A")
       for name, record in train["feeders"].items()},
  }
  duty = {key: {"required": demand, "available": limit, "unit": unit, "pass": demand <= limit}
          for key, (demand, limit, unit) in limits.items()}

  utility = _source(config, "utility", get(tx, "primary_kv", "kV"))
  generator = _source(config, "generator", voltage)
  inverter_current = ups_kva / (1000*math.sqrt(3)*voltage)
  inverter_sources = {mode: _source(config, mode, voltage, inverter_current) for mode in ("inverter", "battery")}
  impedances = {key: Impedance(**item) for key, item in config["sections_ohm"].items()}
  x_r = number(config["transformer_x_over_r"], "transformer X/R")
  transformer_z = get(tx, "impedance_pct", "%") / 100 * voltage**2 / (kva/1000)
  r = transformer_z / math.hypot(1, x_r)
  main_interrupt = get(train["switchboard"], "breaking_ka", "kA")
  utility_in, transformer_link, main_link, transfer_link = train["utility_route"]
  main = _setting(config, "main", voltage, interrupt=main_interrupt)
  transfer_cb = _setting(config, "transfer", voltage)
  mains = (CircuitSection(utility_in, utility.voltage_kv, impedances["mv_in"]),
           CircuitSection(transformer_link, voltage, Impedance(r, r*x_r), main))
  settings = config["clearing"]
  primary = number(settings["primary_s"], "primary clearing limit")
  backup = number(settings["backup_s"], "backup clearing limit")
  grading = number(settings["grading_s"], "grading margin")
  cases = {}

  def solve(key, source, path, location, *, redundant=False, requires_backup=False):
    response = check_radial_fault(source, voltage, path, primary_max_clear_s=primary,
                                  backup_max_clear_s=backup if requires_backup else None, grading_s=grading)
    cases[key] = {"location": location, "independent_alternate_train": redundant,
                  "source_voltage_kv": source.voltage_kv, "path": [s.identity for s in path], **response}

  solve("utility:main", utility, mains, train["switchboard"])
  transfer = CircuitSection(transfer_link, voltage, impedances["transfer"], transfer_cb)
  solve("utility:transfer", utility, (*mains, CircuitSection(main_link, voltage, impedances["connection"]), transfer),
        train["transfer"], requires_backup=True)
  for name, first in train["genset_feeders"].items():
    gen_section = CircuitSection(first, voltage, impedances["generator"], _setting(config, "generator", voltage))
    gen_link = CircuitSection(train["standby_route"][0], voltage, impedances["connection"])
    solve(f"{name}:transfer", generator, (gen_section, gen_link, transfer), train["transfer"], requires_backup=True)
  for mode, source in inverter_sources.items():
    for name, record in train["feeders"].items():
      path = busways[name]
      feeder = CircuitSection(record["feed"], voltage, Impedance(**path["impedance"]), _setting(config, "feeder", voltage))
      solve(f"{mode}:{name}", source, (feeder,), name, redundant=True)
      for terminal, tap in record["terminals"].items():
        impedance = Impedance(**path["terminal_paths"][terminal]["impedance"])
        downstream = CircuitSection(tap, voltage, impedance, _setting(config, "terminal", voltage))
        solve(f"{mode}:{terminal}", source, (feeder, downstream), terminal, redundant=True, requires_backup=True)
  return {"critical_kw": critical_kw, "facility_without_ups_kw": facility_kw, "input_kw": input_kw,
          "terminal_kw": terminal_kw, "rated_ups_kva": ups_kva, "battery_kwh": battery_kwh, "autonomy_min": autonomy,
          "voltage_kv": voltage, "duty": duty, "fault_cases": cases}


def build_power_study(context):
  path = source_path(context, "electrical-study.json")
  if not path.is_file():
    return missing_input(context, path, "power-study")
  config = read_versioned(path)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("Electrical duty and fault assumptions must declare their engineering basis.")
  topology = staged_json(context, MODEL)
  routing_model = f"{MODELS}/power-routing.json"
  routing = staged_json(context, routing_model)
  trains = {side: _power_study(side, train, topology, config, routing["trains"][side]["busways"])
            for side, train in topology["trains"].items()}
  diagnostics = []
  for side, report in trains.items():
    for element, result in report["duty"].items():
      if not result["pass"]:
        diagnostics.append({"rule": "AIDC_ELECTRICAL_DUTY", "status": "fail", "objects": [side, element],
                            "message": f"{side}/{element}: {result['required']:.2f} exceeds {result['available']:.2f} "
                                       f"{result['unit']}."})
    for case, response in report["fault_cases"].items():
      if response["status"] != "pass":
        diagnostics.append({"rule": "AIDC_ELECTRICAL_FAULT", "status": "fail", "objects": [side, case],
                            "message": f"{side}/{case}: " + ", ".join(k for k, passed in response["checks"].items()
                                                                      if not passed)})
  result = model_report(context, "power-study", {"topology_digest": file_digest(context.build_path / MODEL),
                                                "routing_digest": file_digest(context.build_path / routing_model),
                                                "assumptions": config, "trains": trains}, diagnostics)
  schedule = context.build_path / "schedules/aidc-power-protection.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(("train", "case", "fault_point", "i_min_ka", "i_max_ka", "primary_link", "backup_link",
                     "primary_max_clear_s", "backup_max_clear_s", "grading_min_s", "status"))
    for side, report in trains.items():
      for case, response in sorted(report["fault_cases"].items()):
        primary, backup = response["primary"], response["backup"]
        grading = [entry["margin_s"] for entry in response["grading"].values() if entry["margin_s"] is not None]
        writer.writerow((side, case, response["location"], round(response["current_min_ka"], 6),
                         round(response["current_max_ka"], 6), primary, backup,
                         response["devices"].get(primary, {}).get("latest_clear_s"),
                         response["devices"].get(backup, {}).get("latest_clear_s"),
                         min(grading, default=None), response["status"]))
  return TargetResult(result.status, (*result.artifacts, artifact(context, "protection-schedule", schedule)),
                      result.diagnostics)
