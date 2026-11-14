"""Authoring aid: assemble the reference design system SDL from the library.

Run from the repository root:
  uv run --locked python examples/datacenter-reference-design/tools/assemble.py

Output src/system.sdl.json is committed source; re-run after editing the layout.
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"
LIB = "@datacenter-reference/library/modules"
CONTAINERS = 16
COOLING = 3


def port(direction: str, kind: str, maximum: int = 1) -> dict:
  return {"direction": direction, "kind": kind, "max_connections": maximum}


def main() -> None:
  imports = {
    "ItContainer": f"{LIB}/it-container/it-container.sdl.json",
    "PowerContainer": f"{LIB}/power-container/power-container.sdl.json",
    "CoolingContainer": f"{LIB}/cooling-container/cooling-container.sdl.json",
    "GeneratorContainer": f"{LIB}/generator-container/generator-container.sdl.json",
    "SteelFrameBay": f"{LIB}/steel-frame-bay/steel-frame-bay.sdl.json",
    "FirePartitionWall": f"{LIB}/fire-partition-wall/fire-partition-wall.sdl.json",
    "RaisedAccessFloor": f"{LIB}/raised-access-floor/raised-access-floor.sdl.json",
    "Busway": f"{LIB}/busway/busway.sdl.json",
    "CoolingTower": f"{LIB}/cooling-tower/cooling-tower.sdl.json",
    "CondenserWaterPump": f"{LIB}/condenser-water-pump/condenser-water-pump.sdl.json",
  }
  components: dict = {
    "utility-feed": {"ports": {"supply": port("out", "ac-power")}},
    "FwsSupplyHeader": {"ports": {
      **{f"in_{i}": port("in", "fws") for i in range(1, COOLING + 1)},
      **{f"out_{i}": port("out", "fws") for i in range(1, CONTAINERS + 1)},
    }},
    "FwsReturnHeader": {"ports": {
      **{f"in_{i}": port("in", "fws") for i in range(1, CONTAINERS + 1)},
      **{f"out_{i}": port("out", "fws") for i in range(1, COOLING + 1)},
    }},
    "MakeupWaterSource": {"ports": {"supply": port("out", "makeup-water", COOLING)}},
    "ControlPanel": {"ports": {
      "power": port("in", "ac-power"),
      "out_1": port("out", "signal"), "out_2": port("out", "signal"),
    }},
    "AuxFeed": {"ports": {
      "in_a": port("in", "ac-power"), "in_b": port("in", "ac-power"),
      **{f"out_{i}": port("out", "ac-power") for i in range(1, COOLING + 2)},
    }},
  }
  instances: dict = {
    "UtilityA": {"component": "utility-feed"},
    "UtilityB": {"component": "utility-feed"},
    "PowerPathA": {"component": "PowerContainer"},
    "PowerPathB": {"component": "PowerContainer"},
    "GenContainerA": {"component": "GeneratorContainer"},
    "GenContainerB": {"component": "GeneratorContainer"},
    "FwsSupplyHeader": {"component": "FwsSupplyHeader"},
    "FwsReturnHeader": {"component": "FwsReturnHeader"},
    "MakeupWaterSource": {"component": "MakeupWaterSource"},
    "ControlPanel": {"component": "ControlPanel"},
    "AuxFeed": {"component": "AuxFeed"},
  }
  for i in range(1, CONTAINERS + 1):
    instances[f"IT{i:02d}"] = {"component": "ItContainer"}
    instances[f"Floor{i:02d}"] = {"component": "RaisedAccessFloor"}
  for i in range(1, 5):
    instances[f"BuswayA{i}"] = {"component": "Busway"}
    instances[f"BuswayB{i}"] = {"component": "Busway"}
  for i in range(1, COOLING + 1):
    instances[f"CoolContainer{i}"] = {"component": "CoolingContainer"}
    instances[f"CwPump{i}"] = {"component": "CondenserWaterPump"}
    instances[f"Tower{i}"] = {"component": "CoolingTower"}
  for i in range(1, 9):
    instances[f"Frame{i:02d}"] = {"component": "SteelFrameBay"}
  for i in range(1, 5):
    instances[f"Wall{i:02d}"] = {"component": "FirePartitionWall"}

  connections: list[dict] = []
  sides: list[str] = []

  def conn(a: str, b: str, side: str) -> None:
    connections.append({"from": a, "id": f"link-{len(connections) + 1:03d}", "to": b})
    sides.append(side)

  conn("UtilityA.supply", "PowerPathA.utility_in", "A")
  conn("UtilityB.supply", "PowerPathB.utility_in", "B")
  conn("GenContainerA.out", "PowerPathA.genset_in", "A")
  conn("GenContainerB.out", "PowerPathB.genset_in", "B")
  for path in ("A", "B"):
    for i in range(1, 5):
      conn(f"PowerPath{path}.out", f"Busway{path}{i}.in", path)
    conn(f"PowerPath{path}.out", "AuxFeed.in_a" if path == "A" else "AuxFeed.in_b", path)
    for i in range(1, COOLING + 1):
      conn(f"PowerPath{path}.out", f"CoolContainer{i}.power_a" if path == "A"
           else f"CoolContainer{i}.power_b", path)
  for i in range(1, CONTAINERS + 1):
    group, tap = divmod(i - 1, 4)
    conn(f"BuswayA{group + 1}.tap_{tap + 1}", f"IT{i:02d}.power_a", "A")
    conn(f"BuswayB{group + 1}.tap_{tap + 1}", f"IT{i:02d}.power_b", "B")
  for i in range(1, COOLING + 1):
    conn(f"CoolContainer{i}.fws_supply", f"FwsSupplyHeader.in_{i}", "fws")
    conn(f"FwsReturnHeader.out_{i}", f"CoolContainer{i}.fws_return", "fws")
    conn(f"CoolContainer{i}.cw_out", f"CwPump{i}.in", "cw")
    conn(f"CwPump{i}.out", f"Tower{i}.cw_in", "cw")
    conn(f"Tower{i}.cw_out", f"CoolContainer{i}.cw_in", "cw")
    conn(f"AuxFeed.out_{i}", f"Tower{i}.fan_power", "aux")
    conn("MakeupWaterSource.supply", f"Tower{i}.makeup", "makeup")
  conn(f"AuxFeed.out_{COOLING + 1}", "ControlPanel.power", "aux")
  for i in range(1, CONTAINERS + 1):
    conn(f"FwsSupplyHeader.out_{i}", f"IT{i:02d}.fws_supply", "fws")
    conn(f"IT{i:02d}.fws_return", f"FwsReturnHeader.in_{i}", "fws")
  conn("ControlPanel.out_1", "GenContainerA.control", "signal")
  conn("ControlPanel.out_2", "GenContainerB.control", "signal")

  nets: dict = {}
  for connection, side in zip(connections, sides):
    net = {"A": "PowerA", "B": "PowerB", "fws": "Fws", "cw": "Cw",
           "aux": "AuxPower", "makeup": "Makeup", "signal": "Control"}[side]
    nets.setdefault(net, {"kind": {
      "PowerA": "ac-power", "PowerB": "ac-power", "AuxPower": "ac-power",
      "Fws": "fws", "Cw": "cw", "Makeup": "makeup-water", "Control": "signal",
    }[net], "connections": []})["connections"].append(connection["id"])

  document = {
    "schema_version": 2,
    "root": "ReferenceDesign",
    "imports": imports,
    "components": {
      **components,
      "ReferenceDesign": {"instances": instances, "connections": connections, "nets": nets},
    },
  }
  (SOURCE / "system.sdl.json").write_text(
    json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  print(f"assembled {len(instances)} instances, {len(connections)} connections, {len(nets)} nets")


if __name__ == "__main__":
  main()
