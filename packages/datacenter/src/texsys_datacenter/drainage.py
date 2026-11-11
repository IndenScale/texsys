"""Coordinate leak interlock outcomes with collection and drainage capacity."""

from pathlib import Path

from texsys.formal import _load_json
from texsys_plumbing import CollectionNetwork, Release

from .unit import _identifiers, _number


def evaluate_drainage(source: Path, building: dict) -> dict:
  config = _load_json((source / "drainage.study.json").read_text(encoding="utf-8"))
  if (type(config.get("schema_version")) is not int or config["schema_version"] != 1
      or config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip()):
    raise ValueError("Drainage study requires schema_version 1 and an explicit assumed basis.")
  receivers = config["receivers"]
  network = CollectionNetwork(config["segments"], {name: data["usable_l"] for name, data in receivers.items()})
  for name, record in receivers.items():
    zone = building["floor_zones"].get(record["reserve"], {})
    if record["level"] not in building["levels_m"] or zone.get("role") != "liquid-collection":
      raise ValueError(f"Receiver {name} needs a collection reserve on a declared level.")

  results = {}
  for name, case in sorted(config["scenarios"].items()):
    if case["expected"] not in ("pass", "fail"):
      raise ValueError(f"Invalid drainage expectation for {name}.")
    upstream = building["scenarios"][case["building_scenario"]]
    interlocks = upstream["fws"]["interlocks"]
    releases = {}
    for leak, action in interlocks.items():
      detail = config["leak_releases"][leak]
      isolated = _number(detail["isolated_release_s"], f"{leak}.isolated_release_s")
      unisolated = _number(detail["unisolated_release_s"], f"{leak}.unisolated_release_s")
      if unisolated < isolated:
        raise ValueError(f"Unisolated duration cannot be shorter for {leak}.")
      releases[leak] = Release(detail["inlet"], detail["flow_l_s"],
                               isolated if action["isolation_confirmed"] else unisolated)
    unavailable = _identifiers(case.get("unavailable_drain_segments", []), "unavailable_drain_segments")
    budget = network.evaluate(releases, unavailable)
    checks = {"drain-connected": not budget["disconnected_releases"],
              "drain-capacity": not budget["conveyance_excess_l_s"],
              "usable-storage": not budget["storage_excess_l"]}
    results[name] = {**budget, "status": "pass" if all(checks.values()) else "fail", "expected": case["expected"],
                     "checks": checks, "building_scenario": case["building_scenario"],
                     "released_l": {leak: release.flow_l_s * release.duration_s for leak, release in releases.items()}}
  if not results:
    raise ValueError("Drainage study needs explicit scenarios.")
  return {"schema_version": 1, "state": config["state"], "basis": config["basis"], "scenarios": results}
