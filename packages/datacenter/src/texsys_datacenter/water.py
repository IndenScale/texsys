"""Bind heat-rejection duty to cooling makeup, treatment and outfall capacity."""

from dataclasses import asdict
import math
from pathlib import Path

from texsys.formal import _load_json
from texsys_hvac_dynamics.tower_water import TowerWater
from texsys_plumbing.makeup import MakeupNetwork, Treatment, WaterSource

from .fws import BuildingWater
from .unit import _identifiers, _number


def evaluate_water(source: Path, building: dict, unit: dict) -> dict:
  config = _load_json((source / "water.study.json").read_text(encoding="utf-8"))
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Water study needs schema_version 1 and an explicit assumed basis.")
  towers = {name: TowerWater(**rating) for name, rating in sorted(config["heat_rejection"].items())}
  if not towers:
    raise ValueError("Water study needs named heat-rejection trains.")
  wet = [tower.makeup_tds_limit_mg_l for tower in towers.values() if tower.wet_fraction > 0]
  quality_limit = min([_number(config["product_tds_limit_mg_l"], "product_tds_limit_mg_l"), *wet])
  sources = {name: WaterSource(**rating) for name, rating in sorted(config["sources"].items())}
  treatments = {name: Treatment(**{key: value for key, value in rating.items() if key != "power_feed"})
                for name, rating in sorted(config["treatments"].items())}
  power_feeds = {name: rating["power_feed"] for name, rating in config["treatments"].items()}
  tank = config["treated_storage"]
  dimensions = tuple(_number(value, "treated_storage.envelope_m") for value in tank["envelope_m"])
  if len(dimensions) != 3 or tank["usable_m3"] > math.prod(dimensions):
    raise ValueError("Usable makeup storage must fit its declared volume envelope.")
  network = MakeupNetwork(sources, treatments, storage_m3=tank["usable_m3"], initial_m3=tank["initial_m3"],
                          initial_tds_mg_l=tank["initial_tds_mg_l"], product_limit_mg_l=quality_limit)
  definition = _load_json((source / "building.study.json").read_text(encoding="utf-8"))
  facility = BuildingWater(source / "fws.study.json", definition["shared_heat_rejection"],
                           building["floor_zones"], building["levels_m"], unit)

  results = {}
  for name, case in sorted(config["cases"].items()):
    if case["expected"] not in {"pass", "fail"}:
      raise ValueError(f"Invalid water expectation in {name}.")
    intervals, rejection_by_start = [], {}
    for interval in case["intervals"]:
      key = interval["building_scenario"]
      upstream = building["scenarios"][key]
      flows = upstream["fws"]["delivered_kw_by_train"]
      if towers.keys() != flows.keys() or set(power_feeds.values()) - upstream["shared_power"].keys():
        raise ValueError("Makeup trains and treatment power must bind to the building's existing equipment.")
      balance = {train: tower.balance(flows[train]) for train, tower in towers.items()}
      unavailable_treatments = _identifiers(interval.get("unavailable_treatments", []), "unavailable_treatments")
      unpowered = {train for train, feed in power_feeds.items() if upstream["shared_power"][feed]["available_kw"] <= 0}
      intervals.append({
        "start_h": interval["start_h"], "end_h": interval["end_h"],
        "makeup_m3_h": sum(item["makeup_m3_h"] for item in balance.values()),
        "blowdown_m3_h": sum(item["blowdown_m3_h"] for item in balance.values()),
        "outfall_m3_h": interval["outfall_m3_h"],
        "unavailable_sources": _identifiers(interval.get("unavailable_sources", []), "unavailable_sources"),
        "unavailable_treatments": unavailable_treatments | unpowered,
        "tds_overrides_mg_l": interval.get("tds_overrides_mg_l", {}),
      })
      rejection_by_start[interval["start_h"]] = {
        "building_scenario": key, "upstream_status": upstream["status"], "requested_by_train": balance,
        "unpowered_treatments": sorted(unpowered),
      }
    rows = network.simulate(intervals)
    for row in rows:
      data = rejection_by_start[max(start for start in rejection_by_start if start <= row["start_h"])]
      fraction = row["wet_duty_fraction"]
      by_train = data["requested_by_train"]
      # Only the wet duty needs water. The declared dry share remains available.
      supported = {train: load["cooling_kw"] * (1 - towers[train].wet_fraction * (1 - fraction))
                   for train, load in by_train.items()}
      upstream = building["scenarios"][data["building_scenario"]]
      allocation = upstream["fws"]
      baseline = sum(allocation["delivered_kw_by_terminal"].values())
      if fraction < 1 - 1e-8 and baseline > 0:
        phase = building["phases"][upstream["phase"]]
        demand = dict.fromkeys(phase["levels"], phase["heat_budget_kw"]["liquid_it"] / len(phase["levels"]))
        source_cap = {train: min(cap, upstream["fws_source_kw"][train]) for train, cap in supported.items()}
        allocation = facility.evaluate(definition["scenarios"][data["building_scenario"]], demand,
                                       source_cap, upstream["fws_terminal_kw"])
      row.update({
        **data, "water_supported_cooling_kw_by_train": supported,
        "delivered_after_water_kw_by_level": allocation["delivered_kw_by_terminal"],
        "shortage_after_water_kw_by_level": allocation["shortage_kw_by_terminal"],
        "cooling_shortage_kw": max(0., baseline - sum(allocation["delivered_kw_by_terminal"].values())),
      })
    first_limited = next((row["start_h"] for row in rows if row["cooling_shortage_kw"] > 1e-6), None)
    empty = 0. if tank["initial_m3"] == 0 else next((row["end_h"] for row in rows if row["tank_end_m3"] <= 1e-8), None)
    totals = {key.removesuffix("_h"): sum(row[key] * (row["end_h"] - row["start_h"]) for row in rows)
              for key in ("raw_m3_h", "produced_m3_h", "makeup_used_m3_h", "treatment_reject_m3_h", "blowdown_m3_h")}
    totals["cooling_shortage_kwh"] = sum(row["cooling_shortage_kw"] * (row["end_h"] - row["start_h"]) for row in rows)
    results[name] = {"status": "pass" if first_limited is None else "fail", "expected": case["expected"],
                     "first_limited_h": first_limited, "first_empty_h": empty,
                     "tank_end_m3": rows[-1]["tank_end_m3"], "totals": totals, "intervals": rows}
  if not results:
    raise ValueError("Water study needs explicit operating cases.")
  return {
    "schema_version": 1, "state": config["state"], "basis": config["basis"], "formal_review": "pending",
    "status": "pass" if all(case["status"] == case["expected"] for case in results.values()) else "fail",
    "treated_storage": tank, "product_tds_limit_mg_l": quality_limit,
    "heat_rejection": {name: asdict(tower) for name, tower in towers.items()},
    "sources": {name: asdict(rating) for name, rating in sources.items()},
    "treatments": config["treatments"], "cases": results,
    "pending": ["project-water-and-outfall-capacity", "complete-water-chemistry-and-treatment-validation",
                "weather-dependent-heat-rejection", "storage-piping-and-equipment-selection",
                "water-limited-thermal-transients"],
  }
