"""Bind timed electrical transfer to unit loads and building cooling."""

from dataclasses import asdict
from pathlib import Path

from texsys.formal import _load_json
from texsys_electrical.standby import ACLoad, PowerRating, StandbyPower, SwitchState

from .fws import BuildingWater
from .unit import _identifiers, _number, evaluate_unit


def _phase_network(config: dict, feeds: dict, unit: dict, phase: dict) -> StandbyPower:
  model = unit["power_model"]
  external = config["external_sources"]
  if any("/" in level for level in phase["levels"]):
    raise ValueError("Standby level identities cannot contain a path separator.")
  if (not external or external.keys() & feeds.keys()
      or any(not name.strip() or "/" in name for name in external.keys() | feeds.keys())):
    raise ValueError("External power sources and shared feeds need distinct unqualified identities.")
  ratings = {name: PowerRating(record["kw"], record["kva"]) for name, record in external.items()}
  energy = {name: record["usable_kwh"] for name, record in external.items() if "usable_kwh" in record}
  if config["feed_ratings_kva"].keys() != feeds.keys():
    raise ValueError("Apparent power ratings must cover the building's shared feeds.")
  ratings.update({name: PowerRating(record["capacity_kw"], config["feed_ratings_kva"][name])
                  for name, record in feeds.items()})
  links = []
  for start, end in config["upstream_links"]:
    if start not in external or end not in feeds:
      raise ValueError(f"Invalid external source-to-feed connection: {start} -> {end}.")
    links.append((start, end))
  if {end for _, end in links} != feeds.keys():
    raise ValueError("Each shared feed needs an upstream power source.")

  kva, pf = config["unit_ratings_kva"], config["unit_load_power_factor"]
  if kva.keys() != model["ratings_kw"].keys() or pf.keys() != model["demand_kw"].keys():
    raise ValueError("Standby ratings and power factors must cover the unit's declared distribution and loads.")
  batteries = config["unit_batteries"]
  if (batteries.keys() & (kva.keys() | pf.keys())
      or any(not name.strip() or "/" in name for name in batteries)):
    raise ValueError("Battery identities must be separate from the unit's SDL power nodes.")
  if any(record["connect_to"] not in kva or record["connect_to"] in unit["power_sources"]
         for record in batteries.values()):
    raise ValueError("Each battery must feed a declared downstream unit distribution node.")
  loads, sources = {}, set(external)
  count = phase["unit_count"] // len(phase["levels"])
  for level in phase["levels"]:
    for name, kw in model["ratings_kw"].items():
      ratings[f"{level}/{name}"] = PowerRating(kw * count, _number(kva[name], f"{name}.kva") * count)
    for name, kw in model["demand_kw"].items():
      loads[f"{level}/{name}"] = ACLoad(kw * count, pf[name])
    links.extend((name, f"{level}/{record['unit_source']}") for name, record in feeds.items())
    links.extend((f"{level}/{start}", f"{level}/{end}") for start, end in model["links"])
    for name, record in batteries.items():
      node = f"{level}/{name}"
      ratings[node] = PowerRating(_number(record["kw"], f"{name}.kw") * count,
                                  _number(record["kva"], f"{name}.kva") * count)
      energy[node] = _number(record["usable_kwh"], f"{name}.usable_kwh") * count
      sources.add(node)
      # Normal upstream paths retain priority over the local energy source.
      links.append((node, f"{level}/{record['connect_to']}"))
  return StandbyPower(tuple(links), sources, ratings, loads, energy)


def _longest_interruption(intervals: list[dict], affected: str) -> dict[str, float]:
  started, longest = {}, {}
  for interval in intervals:
    missing = set(interval[affected])
    started = {key: started.get(key, interval["start_s"]) for key in missing}
    for key, since in started.items():
      longest[key] = max(longest.get(key, 0), interval["end_s"] - since)
  return dict(sorted(longest.items()))


def _powered_by_level(paths, levels) -> dict[str, set[str]]:
  powered = {level: set() for level in levels}
  for node in paths:
    level, local = node.split("/", 1)
    powered[level].add(local)
  return powered


def evaluate_standby(root: Path, source: Path, building: dict, unit: dict) -> dict:
  config = _load_json((source / "standby.study.json").read_text(encoding="utf-8"))
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Standby study requires schema_version 1 and an explicit assumed basis.")
  definition = _load_json((source / "building.study.json").read_text(encoding="utf-8"))
  feeds, rejection = definition["shared_power"], definition["shared_heat_rejection"]
  water = BuildingWater(source / "fws.study.json", rejection, building["floor_zones"], building["levels_m"], unit)
  budgets = config["max_interruption_s"]
  metrics = {"it": "unserved_it", "air": "air_shortage_kw_by_level", "liquid": "liquid_shortage_kw_by_level"}
  if budgets.keys() != metrics.keys():
    raise ValueError("Standby interruption limits must cover IT, air and liquid service.")
  for service, value in budgets.items():
    _number(value, f"max_interruption_s.{service}", inclusive=True)

  networks, cases, signatures = {}, {}, {}
  it_loads = set(unit["power_model"]["it_loads"])
  for name, case in sorted(config["scenarios"].items()):
    if case["expected"] not in ("pass", "fail"):
      raise ValueError(f"Invalid standby expectation for {name}.")
    phase_id = case["phase"]
    phase = building["phases"][phase_id]
    if phase_id not in networks:
      networks[phase_id] = _phase_network(config, feeds, unit, phase)
    states = tuple(SwitchState(item["at_s"], frozenset(_identifiers(item["unavailable"], f"{name}.unavailable")))
                   for item in case["states"])
    timeline = networks[phase_id].simulate(case["duration_s"], states)
    cases[name] = timeline
    for interval in timeline:
      for nodes in _powered_by_level(interval.service.paths, phase["levels"]).values():
        signature = frozenset(nodes)
        if signature not in signatures:
          signatures[signature] = str(len(signatures))
  if not cases:
    raise ValueError("Standby study needs explicit scenarios.")
  thermal = evaluate_unit(root, source, powered_by_case={key: set(nodes) for nodes, key in signatures.items()})

  results = {}
  for name, timeline in cases.items():
    case = config["scenarios"][name]
    phase = building["phases"][case["phase"]]
    levels = phase["levels"]
    count = phase["unit_count"] // len(levels)
    heat = unit["heat_budget_kw"]
    intervals = []
    for interval in timeline:
      snapshot = asdict(interval)
      level_power = _powered_by_level(interval.service.paths, levels)
      available = {level: thermal[signatures[frozenset(nodes)]] for level, nodes in level_power.items()}
      source_kw = {train: group["capacity_kw"] if group["power_feed"] in interval.service.energized else 0
                   for train, group in rejection.items()}
      terminal_kw = {
        train: {level: count * sum(available[level]["liquid_available_by_plant_kw"][plant]
                                   for plant in group["unit_plants"]) for level in levels}
        for train, group in rejection.items()
      }
      fws = water.evaluate(case.get("water", {}), dict.fromkeys(levels, heat["liquid_it"] * count),
                           source_kw, terminal_kw)
      air_kw = {level: count * report["air_available_kw"] for level, report in available.items()}
      intervals.append({
        **snapshot,
        "unserved_it": [node for node in interval.service.unserved
                        if node.split("/", 1)[1] in it_loads],
        "air_available_kw_by_level": air_kw,
        "active_cooling_by_level": {level: {key: report[key] for key in
                                           ("active_air_units", "active_cdus", "circulating_cdus")}
                                    for level, report in available.items()},
        "air_shortage_kw_by_level": {level: count * heat["total_air"] - kw for level, kw in air_kw.items()
                                     if count * heat["total_air"] - kw > 1e-6},
        "liquid_shortage_kw_by_level": fws["shortage_kw_by_terminal"],
        **({"liquid_shortage_kw_by_unit": fws["shortage_kw_by_unit"]} if "shortage_kw_by_unit" in fws else {}),
        "fws": fws,
        "fws_source_kw": source_kw, "fws_terminal_kw": terminal_kw,
      })
    interruptions = {key: _longest_interruption(intervals, metric) for key, metric in metrics.items()}
    checks = {f"{key}-interruption": max(values.values(), default=0) <= budgets[key] + 1e-8
              for key, values in interruptions.items()}
    unit_interruptions = None
    if all("liquid_shortage_kw_by_unit" in interval for interval in intervals):
      unit_interruptions = _longest_interruption(intervals, "liquid_shortage_kw_by_unit")
      checks["liquid-interruption"] = max(unit_interruptions.values(), default=0) <= budgets["liquid"] + 1e-8
    checks["active-power-capacity"] = not any(interval.service.overloaded_kw for interval in timeline)
    checks["apparent-power-capacity"] = not any(interval.overloaded_kva for interval in timeline)
    checks["leak-isolation"] = all(action["isolation_confirmed"] for interval in intervals
                                  for action in interval["fws"]["interlocks"].values())
    results[name] = {
      "status": "pass" if all(checks.values()) else "fail", "expected": case["expected"], "phase": case["phase"],
      "duration_s": case["duration_s"], "checks": checks, "longest_interruption_s": interruptions,
      "intervals": intervals,
      **({"longest_liquid_interruption_s_by_unit": unit_interruptions} if unit_interruptions is not None else {}),
    }
  return {"schema_version": 1, "state": config["state"], "basis": config["basis"],
          "max_interruption_s": budgets, "scenarios": results}
