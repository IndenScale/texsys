"""Drive room thermal ride-through from standby switching and FWS allocations."""

from dataclasses import asdict
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

from texsys.formal import _load_json
from texsys_hvac_dynamics.ride_through import RoomRideThrough, ThermalInterval, ThermalNode

from .fws import BuildingWater
from .unit import _number


def _json(source, name):
  return _load_json((source / f"{name}.study.json").read_text(encoding="utf-8"))


def _with_pull_down(water, water_case, snapshot, reserve_fraction):
  initial = snapshot["fws"]
  base = initial["delivered_kw_by_terminal"]
  base_units = initial.get("delivered_kw_by_unit_train")
  if not any(base.values()) or reserve_fraction == 0:
    return initial, 0.0

  def dispatch(fraction):
    requested = {level: amount * (1 + fraction) for level, amount in base.items()}
    requested_units = ({unit: sum(trains.values()) * (1 + fraction) for unit, trains in base_units.items()}
                       if base_units is not None else None)
    return water.evaluate(water_case, requested, snapshot["fws_source_kw"], snapshot["fws_terminal_kw"],
                          demand_kw_by_unit=requested_units)

  result = dispatch(reserve_fraction)
  if not result["shortage_kw_by_terminal"]:
    return result, reserve_fraction
  # Add headroom uniformly, preserving each floor's original allocation.
  low, high, best = 0., reserve_fraction, initial
  for _ in range(13):
    middle = (low + high) / 2
    candidate = dispatch(middle)
    if candidate["shortage_kw_by_terminal"]:
      high = middle
    else:
      low, best = middle, candidate
  return best, low


def _fraction(start, delay, ramp, time):
  if start is None:
    return 1.
  return min(1., max(0., (time - start - delay) / ramp))


def _frames(level, intervals, unit, trains, count, profiles, unit_id=None):
  started = {}
  fws_started = {}
  frames = []
  air_specs = unit["air_units"]
  cdus = unit["cdus"]
  loads = unit["power_model"]["demand_kw"]
  racks = unit["power_model"]["it_loads"]
  for index, (snapshot, water) in enumerate(intervals):
    start, end = snapshot["start_s"], snapshot["end_s"]
    active = snapshot["active_cooling_by_level"][level]
    available = set(active["active_air_units"]) | set(active["circulating_cdus"])
    started = {name: started.get(name, start if index else None) for name in sorted(available)}
    fws_started = {train: fws_started.get(train, start if index else None)
                   for train, kw in snapshot["fws_source_kw"].items() if kw > 0}
    it_kw = sum(loads[name] for name in racks if f"{level}/{name}" in snapshot["service"]["paths"])
    cuts = {start, end}
    for name, since in started.items():
      if since is None:
        continue
      delay, ramp = profiles["air" if name in air_specs else "cdu"]
      cuts.update(edge for edge in (since + delay, since + delay + ramp) if start < edge < end)
    delay, ramp = profiles["fws"]
    for since in fws_started.values():
      if since is not None:
        cuts.update(edge for edge in (since + delay, since + delay + ramp) if start < edge < end)

    def readiness(time):
      ready = {name: _fraction(since, *profiles["air" if name in air_specs else "cdu"], time)
               for name, since in started.items()}
      air = min(sum(air_specs[name][key] * ready[name] for name in active["active_air_units"])
                for key in ("capacity_kw", "airflow_limited_kw"))
      liquid = 0.
      for train, plants in trains.items():
        members = [name for name in active["active_cdus"] if cdus[name]["plant"] in plants]
        flow = (water["delivered_kw_by_unit_train"][unit_id][train] if unit_id is not None
                else water["delivered_kw_by_path"][train].get(level, 0.) / count)
        if flow > 0:
          weights = {name: cdus[name]["available_kw"] for name in members}
          cdu_fraction = sum(weights[name] * ready[name] for name in members) / sum(weights.values())
          fws_fraction = _fraction(fws_started[train], *profiles["fws"], time)
          liquid += flow * min(cdu_fraction, fws_fraction)
      circulation = max((ready[name] for name in active["circulating_cdus"]), default=0.)
      return air, liquid, circulation

    for low, high in pairwise(sorted(cuts)):
      ramps = tuple(zip(readiness(low), readiness(high), strict=True))
      frames.append(ThermalInterval(low, high, it_kw, *ramps))
  return tuple(frames)


def evaluate_ride_through(source: Path, building: dict, unit: dict, standby: dict) -> dict:
  config = _json(source, "ride-through")
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Ride-through study needs schema_version 1 and an explicit assumed basis.")
  heat = unit["heat_budget_kw"]
  model = RoomRideThrough({name: ThermalNode(**record) for name, record in config["nodes"].items()},
                          air_it_kw=heat["air_it"], liquid_it_kw=heat["liquid_it"], other_air_kw=heat["other_room"],
                          **config["transfer"])
  max_step = _number(config["max_step_s"], "max_step_s")
  reserve = _number(config["liquid_pull_down_fraction"], "liquid_pull_down_fraction", inclusive=True)
  if config["restart"].keys() != {"air", "cdu", "fws"}:
    raise ValueError("Ride-through needs air, CDU and shared FWS restart profiles.")
  profiles = {key: (_number(record["delay_s"], f"{key}.delay_s", inclusive=True),
                    _number(record["ramp_s"], f"{key}.ramp_s")) for key, record in config["restart"].items()}
  standby_config = _json(source, "standby")
  rejection = _json(source, "building")["shared_heat_rejection"]
  water = BuildingWater(source / "fws.study.json", rejection, building["floor_zones"], building["levels_m"], unit)
  trains = {name: set(group["unit_plants"]) for name, group in sorted(rejection.items())}
  results, cache = {}, {}
  for name, case in sorted(config["cases"].items()):
    if case["expected"] not in {"pass", "fail"}:
      raise ValueError(f"Invalid thermal ride-through expectation in {name}.")
    key = case["standby_scenario"]
    upstream = standby["scenarios"][key]
    water_case = standby_config["scenarios"][key].get("water", {})
    phase = building["phases"][upstream["phase"]]
    levels = phase["levels"]
    count = phase["unit_count"] // len(levels)
    intervals, pull_down = [], []
    for snapshot in upstream["intervals"]:
      allocation, fraction = _with_pull_down(water, water_case, snapshot, reserve)
      intervals.append((snapshot, allocation))
      pull_down.append({"start_s": snapshot["start_s"], "end_s": snapshot["end_s"],
                        "achieved_fraction": fraction, "fws_ceiling_kw_by_level": allocation["delivered_kw_by_terminal"]})
    by_level, by_unit_group, affected_units = {}, {}, {}
    detailed = all("delivered_kw_by_unit_train" in allocation for _, allocation in intervals)
    unit_ids = [name for name, zone in building["floor_zones"].items() if zone["role"] == "unit"]

    def simulate(frames):
      if frames not in cache:
        cache[frames] = model.simulate(frames, max_step)
      return {**cache[frames], "thermal_intervals": [asdict(frame) for frame in frames]}

    for level in levels:
      if not detailed:
        by_level[level] = simulate(_frames(level, intervals, unit, trains, count, profiles))
        continue
      groups = defaultdict(list)
      for unit_name in unit_ids:
        identity = f"{level}/{unit_name}"
        frames = _frames(level, intervals, unit, trains, count, profiles, unit_id=identity)
        groups[frames].append(identity)
      group_results = []
      for frames, members in groups.items():
        result = simulate(frames)
        group_results.append({"units": members, "result": result})
        exceeded = [node for node, item in result["nodes"].items() if item["seconds_above"] > 1e-8]
        if exceeded:
          affected_units.update(dict.fromkeys(members, exceeded))
      by_unit_group[level] = group_results
      representative = max(group_results, key=lambda group: max(
        (node["seconds_above"], node["peak_c"]) for node in group["result"]["nodes"].values()
      ))
      by_level[level] = {**representative["result"], "representative_unit": representative["units"][0]}
    affected = {level: [node for node, detail in result["nodes"].items() if detail["seconds_above"] > 1e-8]
                for level, result in by_level.items()}
    affected = {level: nodes for level, nodes in affected.items() if nodes}
    for identity, nodes in affected_units.items():
      level = identity.split("/", 1)[0]
      affected[level] = sorted(set(affected.get(level, ())) | set(nodes))
    results[name] = {"standby_scenario": key, "phase": upstream["phase"], "duration_s": upstream["duration_s"],
                     "status": "fail" if affected else "pass", "expected": case["expected"],
                     "affected_levels": affected, "fws_pull_down": pull_down, "levels": by_level}
    if detailed:
      results[name].update({"affected_units": affected_units, "unit_groups": by_unit_group})
  if not results:
    raise ValueError("Ride-through study needs explicit scenarios.")
  return {"schema_version": 1, "state": config["state"], "basis": config["basis"], "formal_review": "pending",
          "status": "pass" if all(case["status"] == case["expected"] for case in results.values()) else "fail",
          "scope": "representative-hybrid-unit-with-local-branch-groups", "design_nodes": config["nodes"],
          "restart": config["restart"], "cases": results}
