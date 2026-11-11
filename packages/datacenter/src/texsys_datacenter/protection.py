"""Evaluate protection at selected states of the existing standby topology."""

from dataclasses import asdict
from itertools import pairwise
from pathlib import Path

from texsys.formal import _load_json
from texsys_electrical import trace_power
from texsys_electrical.protection import Breaker, CircuitSection, FaultSource, Impedance, SourceCondition, check_radial_fault

from .standby import _phase_network
from .unit import _number


def _json(source: Path, name: str) -> dict:
  return _load_json((source / f"{name}.study.json").read_text(encoding="utf-8"))


def _identity(node):
  return node.split("/", 1)[-1]


def _source(record, voltage):
  return FaultSource(voltage, *(
    SourceCondition(Impedance(**record[limit]["impedance"]), record[limit]["voltage_factor"],
                    record[limit].get("current_limit_ka"))
    for limit in ("minimum", "maximum")
  ), sustain_s=record.get("sustain_s"))


def _models(config, standby_config, network):
  ac_nodes = {_identity(node) for node in network.capacities_kw.keys() | network.demand_kw.keys()}
  battery_nodes = standby_config["unit_batteries"].keys()
  voltage = {node: _number(value, f"{node}.voltage_kv") for node, value in config["voltage_kv"].items()}
  if voltage.keys() != ac_nodes - battery_nodes:
    raise ValueError("Protection voltage data must cover the existing AC topology.")
  external = standby_config["external_sources"]
  if config["sources"].keys() != external.keys():
    raise ValueError("Protection fault envelopes must cover the external sources.")
  converter_nodes = config["converters"].keys()
  battery_boundaries = {item["connect_to"] for item in standby_config["unit_batteries"].values()}
  if (battery_boundaries - converter_nodes
      or converter_nodes - standby_config["unit_ratings_kva"].keys()):
    raise ValueError("Protection must declare an inverter boundary for every battery-backed AC output.")
  sources = {node: _source(record, voltage[node]) for node, record in config["sources"].items()}
  converters = {node: _source(record, voltage[node]) for node, record in config["converters"].items()}
  for node, converter in converters.items():
    if (converter.sustain_s is None or converter.minimum.current_limit_ka is None
        or converter.maximum.current_limit_ka is None):
      raise ValueError(f"Converter {node} needs declared current limits and their sustain duration.")

  breakers = {name: Breaker(**record) for name, record in config["breakers"].items()}
  types = {name: Impedance(**record) for name, record in config["impedances"].items()}
  connections = {(_identity(a), _identity(b)) for a, b in network.links}
  sections = {}
  for record in config["sections"]:
    a, b = record["from"], record["to"]
    if (a, b) not in connections or (a, b) in sections or a in battery_nodes or b in converter_nodes:
      raise ValueError(f"Invalid or repeated AC protection section {a} -> {b}.")
    breaker_id = record.get("breaker")
    breaker = breakers[breaker_id] if breaker_id is not None else None
    if breaker is not None and breaker.voltage_kv not in (voltage[a], voltage[b]):
      raise ValueError(f"Breaker {breaker_id} voltage does not match section {a} -> {b}.")
    # Impedance data are supplied on the section's downstream voltage basis.
    sections[a, b] = (types[record["impedance"]], breaker_id, breaker)
  return voltage, sources, converters, sections


def evaluate_protection(source: Path, building: dict, unit: dict, standby: dict) -> dict:
  config = _json(source, "protection")
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Protection study requires schema_version 1 and an explicit assumed basis.")
  standby_config = _json(source, "standby")
  feeds = _json(source, "building")["shared_power"]
  networks, results = {}, {}
  models = None
  for name, case in sorted(config["cases"].items()):
    if case["expected"] not in {"pass", "fail", "deenergized"}:
      raise ValueError(f"Invalid protection expectation in {name}.")
    snapshot = standby["scenarios"][case["standby_scenario"]]
    phase = snapshot["phase"]
    if phase not in networks:
      networks[phase] = _phase_network(standby_config, feeds, unit, building["phases"][phase])
    network = networks[phase]
    if models is None:
      models = _models(config, standby_config, network)
    voltage, sources, converters, sections = models

    time = _number(case["at_s"], f"{name}.at_s", inclusive=True)
    interval = next((item for item in snapshot["intervals"] if item["start_s"] <= time < item["end_s"]), None)
    if interval is None:
      raise ValueError(f"Protection case {name} must lie within a standby interval.")
    fault = config["faults"][case["fault"]]
    node = fault["node"]
    if node not in network.capacities_kw.keys() | network.demand_kw.keys() or _identity(node) not in voltage:
      raise ValueError(f"Protection fault node {node} is outside the phase's AC topology.")
    if _identity(node) in converters:
      raise ValueError("A fault at an inverter boundary needs a separately identified AC terminal.")
    resolved = trace_power(network.links, network.sources, set(network.capacities_kw), set(network.demand_kw),
                           set(interval["unavailable"]))
    energized = resolved.get(node)
    evidence = {"node": node, "standby_scenario": case["standby_scenario"], "at_s": time,
                "interval_s": [interval["start_s"], interval["end_s"]], "expected": case["expected"]}
    if energized is None:
      results[name] = {**evidence, "status": "deenergized", "energized_path": []}
      continue
    active_source, model = energized[0], None
    circuit = energized
    for index, candidate in reversed(tuple(enumerate(energized))):
      if _identity(candidate) in converters:
        active_source = candidate
        model = converters[_identity(candidate)]
        circuit = energized[index:]
        break
    if model is None:
      model = sources[active_source]
    selected, breaker_ids = [], {}
    for a, b in pairwise(circuit):
      edge = _identity(a), _identity(b)
      if edge not in sections:
        raise ValueError(f"Missing fault-path impedance data for {a} -> {b} in {name}.")
      impedance, breaker_id, breaker = sections[edge]
      identity = f"{a} -> {b}"
      selected.append(CircuitSection(identity, voltage[edge[1]], impedance, breaker))
      if breaker_id is not None:
        breaker_ids[identity] = breaker_id
    result = check_radial_fault(model, voltage[_identity(node)], tuple(selected),
                                primary_max_clear_s=fault["primary_max_clear_s"],
                                backup_max_clear_s=fault.get("backup_max_clear_s"), grading_s=fault["grading_s"])
    for identity, duty in result["devices"].items():
      duty["breaker"] = breaker_ids[identity]
    results[name] = {**evidence, **result, "energized_path": energized, "contributing_path": circuit,
                     "fault_source": active_source,
                     "fault_source_basis": asdict(model),
                     "fault_source_mode": "inverter" if _identity(active_source) in converters else "voltage-source"}
  if not results:
    raise ValueError("Protection study needs explicit fault cases.")
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"],
    "status": "pass" if all(case["status"] == case["expected"] for case in results.values()) else "fail",
    "scope": "representative-unit-radial-three-phase",
    "formal_review": "pending",
    "pending": ["project-source-and-device-evidence", "multi-source-and-motor-contribution",
                "unbalanced-earth-and-arc-faults", "peak-thermal-and-assembly-withstand",
                "manufacturer-tested-selectivity", "post-trip-sequence-and-load-recovery"],
    "cases": results,
  }
