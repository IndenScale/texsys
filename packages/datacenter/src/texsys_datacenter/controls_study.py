"""Bind a confirmed changeover to routed water circuits and rack thermal response."""

from dataclasses import asdict
from heapq import heappop, heappush
from itertools import count, pairwise

from texsys.formal import _load_json, resolve_sdl_imports
from texsys.library import library_entries
from texsys.sdl_catalog import read_sdl_catalog
from texsys_controls.changeover import ConfirmedChangeover, Observation
from texsys_hvac_dynamics.ride_through import RoomRideThrough, ThermalInterval, ThermalNode

from .tcs import _read
from .tcs_study import _evaluate_cases
from .unit import _identifiers, _number


def _bindings(root, source, controller):
  file = "controls.sdl.json"
  catalog = read_sdl_catalog(source, root)
  if catalog.diagnostics:
    raise ValueError("Cooling controls require a valid locked SDL catalog.")
  documents = catalog.documents
  design = documents[file]
  system = design["components"][design["root"]]
  aliases, errors = resolve_sdl_imports(documents, library_entries(set(documents)), file)
  if errors:
    raise ValueError(f"Unresolved control module imports: {errors}.")
  item = system["instances"][controller]
  entry, definition = aliases[item["component"]]
  manifest = catalog.modules[entry]
  if not entry.startswith("@controls/"):
    raise ValueError("The changeover controller must be a locked controls module.")
  declared = documents[entry]["components"][definition]["behavior"]
  [asset] = [record["path"] for record in manifest["assets"].values() if record["kind"] == "behavior"]
  contract = _load_json((catalog.files[entry].parent / asset).read_text(encoding="utf-8"))
  if declared["reference"] != asset or contract["implementation"] != "texsys_controls.changeover.ConfirmedChangeover":
    raise ValueError("The installed control module must declare the confirmed changeover implementation.")
  bindings = {**declared["parameters"], **item.get("parameters", {})}
  if {key: record["unit"] for key, record in bindings.items()} != contract["parameters"]:
    raise ValueError("Control behavior parameters must match the module's declared units.")
  timeouts = {f"{key}_s": _number(record["value"], key) for key, record in bindings.items()
              if record["state"] in ("assumed", "known")}
  if timeouts.keys() != {"isolation_timeout_s", "proof_timeout_s"}:
    raise ValueError("Both controller timeouts must be resolved.")

  connections = [(edge["from"], edge["to"]) for edge in system["connections"]]

  def target(port):
    [endpoint] = [end for start, end in connections if start == f"{controller}.{port}"]
    return endpoint.partition(".")[0]

  def observation(port):
    [endpoint] = [start for start, end in connections if end == f"{controller}.{port}"]
    return endpoint

  valves = {target(f"close_{leg}"): observation(f"{leg}_closed") for leg in ("supply", "return")}
  if len(valves) != 2 or any(point != f"{valve}.feedback" for valve, point in valves.items()):
    raise ValueError("Each commanded isolation valve needs its own feedback connection.")
  return {"entry": entry, "module_version": manifest["version"], "instance": controller,
          "fault_point": observation("fault"), "proof_point": observation("backup_proof"),
          "enable_target": target("enable_backup"), "isolation": valves, **timeouts}


def _hydraulic_states(source, unit, building, fws, zones, installation, cases):
  model = unit["tcs_model"]
  room, rack = installation["room"], installation["rack"]
  routes = model["routes"]
  valves = installation["valves"]
  leak = installation["leaking_segment"]
  active = routes[leak]["cdu"]
  backup = installation["backup_cdu"]
  if active == backup or len(valves) != 2 or leak not in {record["segment"] for record in valves.values()}:
    raise ValueError("Isolate both legs of the leaking circuit and use an independent backup.")
  isolated = {record["segment"] for record in valves.values()}
  if isolated != {f"{active}/{leg}/drop-{rack}" for leg in ("supply", "return")}:
    raise ValueError("Isolation valves must bind to the routed supply and return at this rack.")
  standby = {f"{backup}/{leg}/drop-{rack}" for leg in ("supply", "return")}
  if standby - routes.keys():
    raise ValueError("The standby command requires installed TCS supply and return at the rack.")
  definitions = {}
  for scenario in sorted({case["building_scenario"] for case in cases.values()}):
    for phase, unavailable in (("before", standby), ("interlocked", {leak} | standby),
                               ("isolated", isolated | standby), ("transferred", isolated)):
      changes = {room: {"unavailable_segments": sorted(unavailable)}} if unavailable else {}
      definitions[f"{scenario}/{phase}"] = {"building_scenario": scenario, "local_changes": changes, "expected": "pass"}
  profiles, evaluated = _evaluate_cases(source, {"cases": definitions}, model, unit, building, fws, zones)
  result = {}
  for key, record in evaluated.items():
    profile = record["profiles_by_room"][room]
    result[key] = {"state_id": key, "room": room, "whole_building_delivered_kw": record["total_delivered_kw"],
                   "fws_dispatch": record["dispatch"]["fws_delivered_kw_by_train"],
                   "dispatch_mode": record["dispatch"]["mode"], **profiles[profile]}
  return result


def _exercise(binding, installation, timing, case, backup_flow, proof_minimum):
  controller = ConfirmedChangeover(**{name: binding[name] for name in (
    "fault_point", "isolation", "enable_target", "proof_point", "isolation_timeout_s", "proof_timeout_s",
  )})
  fault_at = _number(case["fault_at_s"], "fault_at_s", inclusive=True)
  duration = _number(case["duration_s"], "duration_s")
  if fault_at >= duration:
    raise ValueError("The leak must occur during the study window.")
  detection = _number(case.get("detection_delay_s", timing["detection_delay_s"]), "detection delay")
  proof_delay = _number(timing["proof_delay_s"], "flow proof delay")
  detector = case.get("detector", "normal")
  proof_sensor = case.get("flow_switch", "normal")
  stuck = _identifiers(case.get("stuck_valves", []), "stuck_valves")
  stale = _identifiers(case.get("stale_feedback", []), "stale_feedback")
  if (detector not in ("normal", "silent", "bad") or proof_sensor not in ("normal", "silent", "bad")
      or (stuck | stale) - binding["isolation"].keys() or stuck & stale):
    raise ValueError("Invalid or overlapping sensor and actuator fault injections.")

  queue, serial = [], count()
  closed, physical = set(), [{"at_s": fault_at, "kind": "leak", "segment": installation["leaking_segment"]}]

  def schedule(at, event, data):
    if at <= duration:
      heappush(queue, (at, next(serial), event, data))

  if detector != "silent":
    at = fault_at + detection
    schedule(at, "observation", Observation(binding["fault_point"], True, at, "bad" if detector == "bad" else "good"))

  handled = 0
  while queue:
    now, _, event, data = heappop(queue)
    controller.advance(now)
    if event == "valve-travel":
      closed.add(data)
      physical.append({"at_s": now, "kind": "valve-closed", "target": data})
      sample = fault_at if data in stale else now
      controller.receive(now, Observation(binding["isolation"][data], True, sample, "stale" if data in stale else "good"))
    elif event == "flow-proof":
      if controller.state == "starting":
        quality = "bad" if proof_sensor == "bad" else "good"
        physical.append({"at_s": now, "kind": "backup-flow-probe", "flow_kg_s": backup_flow,
                         "minimum_kg_s": proof_minimum})
        controller.receive(now, Observation(binding["proof_point"], backup_flow >= proof_minimum, now, quality))
    else:
      controller.receive(now, data)

    for record in controller.trace[handled:]:
      if record["kind"] == "command" and record["action"] == "close":
        valve = record["target"]
        if valve not in stuck:
          travel = _number(installation["valves"][valve]["travel_s"], "valve travel")
          schedule(now + travel, "valve-travel", valve)
      if record["kind"] == "command" and record["action"] == "enable":
        if closed != binding["isolation"].keys():
          raise ValueError("The controller issued a backup command before physical isolation.")
        if proof_sensor != "silent":
          schedule(now + proof_delay, "flow-proof", None)
      if record["kind"] == "backup-proved" and "proof_loss_after_s" in case:
        at = now + _number(case["proof_loss_after_s"], "flow proof loss delay")
        schedule(at, "observation", Observation(binding["proof_point"], False, at))
    handled = len(controller.trace)
  controller.advance(duration)
  lockout = next((event for event in controller.trace if event["kind"] == "lockout"), None)
  proved = next((event["at_s"] for event in controller.trace if event["kind"] == "backup-proved"), None)
  isolation_proved = next((event["at_s"] for event in controller.trace
                           if event["kind"] == "command" and event["action"] == "enable"), None)
  return {"trace": controller.trace, "physical_events": physical, "final_state": controller.state,
          "isolation_proved_at_s": isolation_proved, "proved_at_s": proved,
          "lockout": lockout, "fault_at_s": fault_at, "duration_s": duration}


def _thermal_frames(hydraulic, rack, it, air, required, sequence, ramp):
  base = hydraulic["before"]["delivered_kw_by_terminal"][rack]
  hold = hydraulic["interlocked"]["delivered_kw_by_terminal"][rack]
  isolated = hydraulic["isolated"]["delivered_kw_by_terminal"][rack]
  restored = hydraulic["transferred"]["delivered_kw_by_terminal"][rack]
  fault, end, proved = (sequence[key] for key in ("fault_at_s", "duration_s", "proved_at_s"))
  closed = sequence["isolation_proved_at_s"]
  stop = sequence["lockout"]["at_s"] if sequence["lockout"] and proved is not None else end
  cuts = {0., fault, end}
  if closed is not None:
    cuts.add(closed)
  if proved is not None:
    cuts.update((proved, stop, min(proved + ramp, stop)))
  intervals = []
  for a, b in pairwise(sorted(cuts)):
    if b <= fault:
      liquid = (base, base)
    elif proved is not None and a >= proved and b <= stop:
      liquid = tuple(isolated + (restored - isolated) * min(1., (t - proved) / ramp) for t in (a, b))
    elif closed is not None and a >= closed:
      liquid = (isolated, isolated)
    else:
      liquid = (hold, hold)
    circulation = tuple(min(1., kw / required) for kw in liquid)
    intervals.append(ThermalInterval(a, b, it, (air, air), liquid, circulation))
  return tuple(intervals)


def evaluate_controls(root, source, unit, building, fws):
  config = _read(source, "controls")
  if config["schema_version"] != 1 or config["state"] != "assumed" or not config["basis"].strip():
    raise ValueError("Controls study needs schema version 1 and an assumed basis.")
  installation = config["installation"]
  binding = _bindings(root, source, installation["controller"])
  if binding["isolation"].keys() != installation["valves"].keys():
    raise ValueError("Each SDL isolation actuator must be bound to one actual valve.")
  zones = {key: zone for key, zone in building["floor_zones"].items() if zone["role"] == "unit"}
  cases = config["cases"]
  if not cases:
    raise ValueError("Cooling controls need explicit failure and transfer scenarios.")
  states = _hydraulic_states(source, unit, building, fws, zones, installation, cases)
  rack = installation["rack"]
  it = unit["power_model"]["demand_kw"][rack]
  share = it / unit["heat_budget_kw"]["it"]
  rack_heat = unit["rack_heat_kw"][rack]
  transfer = _read(source, "ride-through")
  nodes = {name: ThermalNode(**{**record, "capacity_kj_k": record["capacity_kj_k"] * share})
           for name, record in transfer["nodes"].items()}
  gains = {key: value * share if key.endswith("kw_k") else value for key, value in transfer["transfer"].items()}
  thermal = RoomRideThrough(nodes, air_it_kw=rack_heat["air"], liquid_it_kw=rack_heat["liquid"],
                            other_air_kw=unit["heat_budget_kw"]["other_room"] * share, **gains)
  ramp = _number(config["timing"]["restart_ramp_s"], "backup ramp")
  backup = installation["backup_cdu"]
  loop = unit["tcs_model"]["loops"][backup]
  minimum = _number(installation["proof_minimum_fraction"], "minimum proof fraction")
  if minimum > 1:
    raise ValueError("Minimum proof fraction must be at most one.")
  proof = minimum * rack_heat["liquid"] / (loop["specific_heat_kj_kgk"] * (loop["return_c"] - loop["supply_c"]))
  results, transient_cache = {}, {}
  for name, case in sorted(cases.items()):
    upstream = case["building_scenario"]
    hydraulic = {phase: states[f"{upstream}/{phase}"] for phase in ("before", "interlocked", "isolated", "transferred")}
    if hydraulic["before"]["delivered_kw_by_terminal"][rack] + 1e-6 < rack_heat["liquid"]:
      raise ValueError(f"{name}: the target rack needs full design cooling before the injected fault.")
    circuit = hydraulic["transferred"]["circuits_by_rack"].get(rack, {}).get(backup, {})
    sequence = _exercise(binding, installation, config["timing"], case, circuit.get("flow_kg_s", 0.), proof)
    local = unit["scenarios"][building["scenarios"][upstream]["unit_scenario"]]
    if rack not in local["power"]["paths"]:
      raise ValueError(f"{name}: this rack leak study requires IT power during the fault.")
    frames = _thermal_frames(hydraulic, rack, it, local["air_available_kw"] * share, rack_heat["liquid"], sequence, ramp)
    if frames not in transient_cache:
      transient_cache[frames] = thermal.simulate(frames, transfer["max_step_s"])
    response = transient_cache[frames]
    exceeds = [node for node, details in response["nodes"].items() if details["seconds_above"] > 1e-8]
    ready_at = sequence["proved_at_s"] + ramp if sequence["proved_at_s"] is not None else None
    if ready_at is not None and (ready_at > sequence["duration_s"]
                                 or (sequence["lockout"] and ready_at >= sequence["lockout"]["at_s"])):
      ready_at = None
    recovered = (sequence["final_state"] == "proved" and ready_at is not None
                 and hydraulic["transferred"]["delivered_kw_by_terminal"][rack] + 1e-6 >= rack_heat["liquid"])
    expected = case["expected"]
    if expected not in ("pass", "fail"):
      raise ValueError(f"{name}: an explicit pass or fail expectation is required.")
    results[name] = {"expected": expected, "status": "pass" if recovered and not exceeds else "fail",
                     "building_scenario": upstream, "rack": f"{installation['room']}/{rack}",
                     "hydraulic_states": {phase: item["state_id"] for phase, item in hydraulic.items()},
                     "sequence": sequence, "thermal": response, "thermal_intervals": [asdict(frame) for frame in frames],
                     "exceeded_nodes": exceeds, "backup_ready_at_s": ready_at, "retained_backup_at_end": recovered}
  mismatches = [name for name, case in results.items() if case["status"] != case["expected"]]
  return {"schema_version": 1, "state": "assumed", "basis": config["basis"], "status": "fail" if mismatches else "pass",
          "binding": binding, "installation": installation, "cases": results, "hydraulic_states": states,
          "mismatched_cases": mismatches,
          "pending": ["commissioning-evidence", "whole-room-transient-flow-and-mixing", "bms-protocol-and-point-mapping"]}
