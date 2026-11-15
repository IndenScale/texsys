"""Authoring aid: generate container-unit and construction composite modules.

Run from the repository root:
  uv run --locked python packages/datacenter-reference/tools/generate_units.py

Outputs are committed source; re-run after editing the unit definitions.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "library" / "modules"
SOURCE = "Texsys 数据中心参考设计库概念假设（公开产品水平脱敏）"


def port(direction: str, kind: str, maximum: int = 1) -> dict:
  return {"direction": direction, "kind": kind, "max_connections": maximum}


def binding(value: float, unit: str) -> dict:
  return {"value": value, "unit": unit, "state": "assumed", "source": SOURCE}


def conn(index: int, a: str, b: str) -> dict:
  return {"from": a, "id": f"link-{index:03d}", "to": b}


# ---------------------------------------------------------------- it-container

def it_container() -> dict:
  components = {
    "PowerFeed": {"ports": {
      "in": port("in", "ac-power"),
      **{f"out_{i}": port("out", "ac-power") for i in range(1, 6)},
    }},
    "RackPdu": {"ports": {
      "in": port("in", "ac-power"),
      **{f"out_{i}": port("out", "ac-power") for i in range(1, 9)},
    }},
    "TcsSupplyManifold": {"ports": {
      "in": port("in", "tcs"),
      **{f"out_{i}": port("out", "tcs") for i in range(1, 9)},
    }},
    "TcsReturnManifold": {"ports": {
      **{f"in_{i}": port("in", "tcs") for i in range(1, 9)},
      "out": port("out", "tcs"),
    }},
    "FirePanel": {"ports": {
      "power_a": port("in", "ac-power"),
      "power_b": port("in", "ac-power"),
      "alarm": port("out", "signal"),
      "agent": port("out", "fire-agent"),
    }},
  }
  instances = {
    "FeedA": {"component": "PowerFeed"},
    "FeedB": {"component": "PowerFeed"},
    "PduA": {"component": "RackPdu"},
    "PduB": {"component": "RackPdu"},
    "CduA": {"component": "CDU", "parameters": {
      "nameplate_capacity": binding(120, "kW"), "electrical_input": binding(8, "kW")}},
    "CduB": {"component": "CDU", "parameters": {
      "nameplate_capacity": binding(120, "kW"), "electrical_input": binding(8, "kW")}},
    "PaA1": {"component": "PrecisionAir", "parameters": {
      "sensible_capacity": binding(30, "kW"), "supply_airflow": binding(2.6, "m3/s"),
      "electrical_input": binding(3, "kW")}},
    "PaA2": {"component": "PrecisionAir", "parameters": {
      "sensible_capacity": binding(30, "kW"), "supply_airflow": binding(2.6, "m3/s"),
      "electrical_input": binding(3, "kW")}},
    "PaB1": {"component": "PrecisionAir", "parameters": {
      "sensible_capacity": binding(30, "kW"), "supply_airflow": binding(2.6, "m3/s"),
      "electrical_input": binding(3, "kW")}},
    "PaB2": {"component": "PrecisionAir", "parameters": {
      "sensible_capacity": binding(30, "kW"), "supply_airflow": binding(2.6, "m3/s"),
      "electrical_input": binding(3, "kW")}},
    "SupplyA": {"component": "TcsSupplyManifold"},
    "ReturnA": {"component": "TcsReturnManifold"},
    "SupplyB": {"component": "TcsSupplyManifold"},
    "ReturnB": {"component": "TcsReturnManifold"},
    "FirePanel1": {"component": "FirePanel"},
  }
  for i in range(1, 9):
    instances[f"Rack{i:02d}"] = {"component": "Rack"}

  connections: list[dict] = []
  power_a = [("FeedA.out_1", "PduA.in"), ("FeedA.out_2", "CduA.power"),
             ("FeedA.out_3", "PaA1.power"), ("FeedA.out_4", "PaA2.power"),
             ("FeedA.out_5", "FirePanel1.power_a")]
  power_b = [("FeedB.out_1", "PduB.in"), ("FeedB.out_2", "CduB.power"),
             ("FeedB.out_3", "PaB1.power"), ("FeedB.out_4", "PaB2.power"),
             ("FeedB.out_5", "FirePanel1.power_b")]
  for a, b in power_a + power_b:
    connections.append(conn(len(connections) + 1, a, b))
  for i in range(1, 9):
    connections.append(conn(len(connections) + 1, f"PduA.out_{i}", f"Rack{i:02d}.power_a"))
    connections.append(conn(len(connections) + 1, f"PduB.out_{i}", f"Rack{i:02d}.power_b"))
  for path, cdu, supply, ret in (("a", "CduA", "SupplyA", "ReturnA"),
                                  ("b", "CduB", "SupplyB", "ReturnB")):
    connections.append(conn(len(connections) + 1, f"{cdu}.tcs_supply", f"{supply}.in"))
    for i in range(1, 9):
      connections.append(conn(len(connections) + 1, f"{supply}.out_{i}", f"Rack{i:02d}.tcs_in_{path}"))
      connections.append(conn(len(connections) + 1, f"Rack{i:02d}.tcs_out_{path}", f"{ret}.in_{i}"))
    connections.append(conn(len(connections) + 1, f"{ret}.out", f"{cdu}.tcs_return"))
  for units, racks, path in ((("PaA1", "PaA2"), range(1, 9), "a"),
                              (("PaB1", "PaB2"), range(1, 9), "b")):
    for unit, group in zip(units, (list(racks)[:4], list(racks)[4:])):
      for i in group:
        connections.append(conn(len(connections) + 1, f"{unit}.supply", f"Rack{i:02d}.air_in_{path}"))
        connections.append(conn(len(connections) + 1, f"Rack{i:02d}.air_out_{path}", f"{unit}.return"))

  def net_of(c: dict) -> str:
    f, t = c["from"], c["to"]
    if f.startswith(("FeedA.", "PduA.")) or t.endswith(".power_a"):
      return "PowerA"
    if f.startswith(("FeedB.", "PduB.")) or t.endswith(".power_b"):
      return "PowerB"
    if any(x in f or x in t for x in ("CduA.", "SupplyA.", "ReturnA.")) \
        or f.endswith(".tcs_out_a") or t.endswith(".tcs_in_a"):
      return "TcsA"
    if any(x in f or x in t for x in ("CduB.", "SupplyB.", "ReturnB.")) \
        or f.endswith(".tcs_out_b") or t.endswith(".tcs_in_b"):
      return "TcsB"
    if f.startswith(("PaA1.", "PaA2.")) or t.endswith(".air_in_a") or f.endswith(".air_out_a"):
      return "AirA"
    return "AirB"

  net_kinds = {"PowerA": "ac-power", "PowerB": "ac-power", "TcsA": "tcs", "TcsB": "tcs",
               "AirA": "room-air", "AirB": "room-air"}
  nets = {name: {"kind": kind, "connections": []} for name, kind in net_kinds.items()}
  for c in connections:
    nets[net_of(c)]["connections"].append(c["id"])
  layout = {}
  for i in range(1, 9):
    row, col = divmod(i - 1, 4)
    layout[f"Rack{i:02d}"] = [0.2 + col * 0.85, 0.15 + row * 1.25, 0]
  layout.update({"CduA": [4.0, 0.15, 0], "CduB": [4.0, 1.35, 0],
                 "PaA1": [4.9, 0.15, 0], "PaA2": [4.9, 0.75, 0],
                 "PaB1": [4.9, 1.35, 0], "PaB2": [4.9, 1.95, 0],
                 "PduA": [5.6, 0.15, 0], "PduB": [5.6, 1.35, 0],
                 "FirePanel1": [5.6, 0.75, 0],
                 "FeedA": [5.6, 0.45, 1.8], "FeedB": [5.6, 1.05, 1.8],
                 "SupplyA": [3.7, 0.15, 2.3], "ReturnA": [3.7, 0.45, 2.3],
                 "SupplyB": [3.7, 1.35, 2.3], "ReturnB": [3.7, 1.65, 2.3]})
  return {
    "title": "IT 方舱",
    "envelope": [6.0, 2.4, 2.9],
    "color": [0.3, 0.38, 0.45],
    "imports": {
      "Rack": "@hvac-equipment/library/modules/liquid-cooled-rack/liquid-cooled-rack.sdl.json",
      "CDU": "@hvac-equipment/library/modules/coolant-distribution-unit/coolant-distribution-unit.sdl.json",
      "PrecisionAir": "@hvac-equipment/library/modules/precision-air-unit/precision-air-unit.sdl.json",
    },
    "components": components,
    "ports": {
      "power_a": port("in", "ac-power"),
      "power_b": port("in", "ac-power"),
      "fws_supply": port("in", "fws", 2),
      "fws_return": port("out", "fws", 2),
      "alarm": port("out", "signal"),
    },
    "instances": instances,
    "connections": connections,
    "nets": nets,
    "boundary": {
      "power_a": ["FeedA.in"], "power_b": ["FeedB.in"],
      "fws_supply": ["CduA.facility_in", "CduB.facility_in"],
      "fws_return": ["CduA.facility_out", "CduB.facility_out"],
      "alarm": ["FirePanel1.alarm"],
    },
    "layout": layout,
    "parameters": {
      "it_load_kw": binding(120, "kW"),
      "rack_count": binding(8, "台"),
      "liquid_fraction": binding(0.75, None),
      "fire_resistance_hours": binding(1.0, "h"),
    },
    "properties": {
      "mass_kg": 12000,
      "fire": {"class": "方舱围护不燃，舱内气体灭火", "fire_resistance_hours": 1.0,
               "suppression": "全氟己酮（概念）", "detection": "极早期烟感"},
      "weathering": {"installation": "indoor", "ip": "IP54"},
      "racks_per_container": 8,
      "rack_load_kw": 15,
    },
    "model": "containerized-liquid-cooled-it-module",
    "method": "8 台 15 kW 液冷机柜，液侧 75% 由 2N CDU 承担，风侧 25% 由 2N 精密空调承担；A/B 双路配电。",
    "limitations": "概念脱敏；实际气流组织、漏液检测与消防联动由项目校核。",
  }


# ------------------------------------------------------------- power-container

def power_container() -> dict:
  components = {
    "TransferBus": {"ports": {
      "in": port("in", "ac-power"),
      "critical": port("out", "ac-power"),
      "facility": port("out", "ac-power", 4),
    }},
    "UpsBus": {"ports": {
      "in": port("in", "ac-power"),
      **{f"out_{i}": port("out", "ac-power") for i in range(1, 6)},
    }},
    "OutputBus": {"ports": {
      **{f"in_{i}": port("in", "ac-power") for i in range(1, 6)},
      "out": port("out", "ac-power", 8),
    }},
  }
  instances = {
    "Transformer": {"component": "Transformer"},
    "Switchboard": {"component": "Switchboard", "parameters": {
      "rated_a": binding(5000, "A"), "breaking_ka": binding(85, "kA")}},
    "Ats": {"component": "ATS", "parameters": {"rated_a": binding(5000, "A")}},
    "TransferBus1": {"component": "TransferBus"},
    "UpsBus1": {"component": "UpsBus"},
    "OutputBus1": {"component": "OutputBus"},
  }
  for i in range(1, 6):
    instances[f"Ups{i}"] = {"component": "UpsModule"}
    instances[f"Battery{i}"] = {"component": "Battery"}
  pairs = [
    ("Transformer.secondary", "Switchboard.in"),
    ("Switchboard.out_1", "Ats.source_a"),
    ("Ats.out", "TransferBus1.in"),
    ("TransferBus1.critical", "UpsBus1.in"),
  ]
  for i in range(1, 6):
    pairs.append((f"UpsBus1.out_{i}", f"Ups{i}.ac_in"))
    pairs.append((f"Ups{i}.ac_out", f"OutputBus1.in_{i}"))
    pairs.append((f"Battery{i}.dc", f"Ups{i}.battery"))
  connections = [conn(i + 1, a, b) for i, (a, b) in enumerate(pairs)]

  def is_dc(c: dict) -> bool:
    return c["from"].startswith("Battery")

  nets = {
    "AcPath": {"kind": "ac-power", "connections": [c["id"] for c in connections if not is_dc(c)]},
    "DcPath": {"kind": "dc-power", "connections": [c["id"] for c in connections if is_dc(c)]},
  }
  layout = {"Transformer": [0.2, 0.2, 0], "Switchboard": [2.2, 0.2, 0],
            "Ats": [3.4, 0.2, 0], "TransferBus1": [2.6, 0.5, 1.8],
            "UpsBus1": [3.4, 1.3, 1.8], "OutputBus1": [5.4, 0.8, 1.8]}
  for i in range(1, 6):
    layout[f"Ups{i}"] = [3.9 + (i - 1) * 0.35, 0.2, 0]
    layout[f"Battery{i}"] = [0.2 + (i - 1) * 0.9, 1.3, 0]
  return {
    "title": "配电方舱",
    "envelope": [6.0, 2.4, 2.9],
    "color": [0.4, 0.42, 0.48],
    "imports": {
      "Transformer": "library/modules/dry-type-transformer/dry-type-transformer.sdl.json",
      "Switchboard": "library/modules/lv-switchboard/lv-switchboard.sdl.json",
      "ATS": "library/modules/ats/ats.sdl.json",
      "UpsModule": "library/modules/ups-module/ups-module.sdl.json",
      "Battery": "library/modules/battery-cabinet/battery-cabinet.sdl.json",
    },
    "components": components,
    "ports": {
      "utility_in": port("in", "ac-power"),
      "genset_in": port("in", "ac-power"),
      "out": port("out", "ac-power", 8),
      "facility_out": port("out", "ac-power", 4),
    },
    "instances": instances,
    "connections": connections,
    "nets": nets,
    "boundary": {
      "utility_in": ["Transformer.primary"],
      "genset_in": ["Ats.source_b"],
      "out": ["OutputBus1.out"],
      "facility_out": ["TransferBus1.facility"],
    },
    "layout": layout,
    "parameters": {
      "rated_kva": binding(2500, "kVA"),
      "ups_modules": binding(5, "台"),
      "battery_energy_kwh": binding(600, "kWh"),
      "battery_autonomy_min": binding(15, "min"),
      "fire_resistance_hours": binding(1.0, "h"),
    },
    "properties": {
      "mass_kg": 16000,
      "fire": {"class": "方舱围护不燃", "fire_resistance_hours": 1.0},
      "weathering": {"installation": "indoor", "ip": "IP54"},
    },
    "model": "containerized-power-path",
    "method": "ATS 后的转换母线分出设施负荷与 2500 kVA UPS 两条支路。每台 UPS 配 120 kWh 电池柜。",
    "limitations": "概念脱敏；实际保护选择性、电池通风与维护旁路由项目校核。",
  }


# ------------------------------------------------------------ cooling-container

def cooling_container() -> dict:
  components = {
    "PowerFeed": {"ports": {
      "in_a": port("in", "ac-power"), "in_b": port("in", "ac-power"),
      "out_1": port("out", "ac-power"), "out_2": port("out", "ac-power"),
      "out_3": port("out", "ac-power"),
    }},
    "ChwReturnHeader": {"ports": {
      "in_1": port("in", "chw"), "in_2": port("in", "chw"), "out": port("out", "chw"),
    }},
  }
  instances = {
    "Chiller": {"component": "Chiller"},
    "Pump1": {"component": "Pump"},
    "Pump2": {"component": "Pump"},
    "Feed": {"component": "PowerFeed"},
    "Header": {"component": "ChwReturnHeader"},
  }
  pairs = [("Feed.out_1", "Chiller.power"), ("Feed.out_2", "Pump1.power"),
           ("Feed.out_3", "Pump2.power"), ("Pump1.out", "Header.in_1"),
           ("Pump2.out", "Header.in_2"), ("Header.out", "Chiller.chw_return")]
  connections = [conn(i + 1, a, b) for i, (a, b) in enumerate(pairs)]
  return {
    "title": "冷却方舱",
    "envelope": [6.0, 2.4, 2.9],
    "color": [0.28, 0.42, 0.5],
    "imports": {
      "Chiller": "library/modules/water-cooled-chiller/water-cooled-chiller.sdl.json",
      "Pump": "library/modules/circulation-pump/circulation-pump.sdl.json",
    },
    "components": components,
    "ports": {
      "power_a": port("in", "ac-power"),
      "power_b": port("in", "ac-power"),
      "fws_return": port("in", "fws", 3),
      "fws_supply": port("out", "fws", 3),
      "cw_in": port("in", "cw"),
      "cw_out": port("out", "cw"),
    },
    "instances": instances,
    "connections": connections,
    "nets": {
      "ChwPath": {"kind": "chw", "connections": [
        c["id"] for c in connections if c["from"].startswith(("Pump", "Header"))
        or c["to"].startswith("Header")]},
      "PowerA": {"kind": "ac-power", "connections": [
        c["id"] for c in connections if c["from"] in ("Feed.out_1", "Feed.out_2")]},
      "PowerB": {"kind": "ac-power", "connections": [
        c["id"] for c in connections if c["from"] == "Feed.out_3"]},
    },
    "boundary": {
      "power_a": ["Feed.in_a"],
      "power_b": ["Feed.in_b"],
      "fws_return": ["Pump1.in", "Pump2.in"],
      "fws_supply": ["Chiller.chw_supply"],
      "cw_in": ["Chiller.cw_in"],
      "cw_out": ["Chiller.cw_out"],
    },
    "layout": {"Chiller": [0.2, 0.3, 0], "Pump1": [4.5, 0.3, 0],
               "Pump2": [4.5, 1.3, 0], "Feed": [5.5, 0.9, 1.8],
               "Header": [4.0, 0.8, 0.5]},
    "parameters": {
      "cooling_kw": binding(1200, "kW"),
      "cop": binding(5.5, None),
      "standby_pumps": binding(1, "台"),
      "fire_resistance_hours": binding(1.0, "h"),
    },
    "properties": {
      "mass_kg": 11000,
      "fire": {"class": "方舱围护不燃", "fire_resistance_hours": 1.0},
      "weathering": {"installation": "outdoor", "ip": "IP54"},
    },
    "model": "containerized-chilled-water-plant",
    "method": "1200 kW 水冷机组 + 2 台冷冻泵（1 用 1 备）；对外接口为设施水 FWS（与 CDU 设施侧同回路），A/B 双路供电，冷却水接室外冷却塔。",
    "limitations": "概念脱敏；实际水力平衡、防冻与群控由项目校核。",
  }


# ---------------------------------------------------------- generator-container

def generator_container() -> dict:
  components = {
    "GenBus": {"ports": {"in_1": port("in", "ac-power"), "in_2": port("in", "ac-power"),
                         "out": port("out", "ac-power")}},
    "FuelHeader": {"ports": {"in": port("in", "fuel"), "out_1": port("out", "fuel"),
                             "out_2": port("out", "fuel")}},
    "ControlSplit": {"ports": {"in": port("in", "signal"), "out_1": port("out", "signal"),
                               "out_2": port("out", "signal")}},
  }
  instances = {
    "Genset1": {"component": "Genset"},
    "Genset2": {"component": "Genset"},
    "GenBus1": {"component": "GenBus"},
    "FuelHeader1": {"component": "FuelHeader"},
    "ControlSplit1": {"component": "ControlSplit"},
  }
  pairs = [("Genset1.ac_out", "GenBus1.in_1"), ("Genset2.ac_out", "GenBus1.in_2"),
           ("FuelHeader1.out_1", "Genset1.fuel_in"), ("FuelHeader1.out_2", "Genset2.fuel_in"),
           ("ControlSplit1.out_1", "Genset1.control"), ("ControlSplit1.out_2", "Genset2.control")]
  connections = [conn(i + 1, a, b) for i, (a, b) in enumerate(pairs)]
  return {
    "title": "发电机方舱",
    "envelope": [12.0, 2.4, 2.9],
    "color": [0.33, 0.45, 0.36],
    "imports": {
      "Genset": "library/modules/diesel-generator/diesel-generator.sdl.json",
    },
    "components": components,
    "ports": {
      "out": port("out", "ac-power"),
      "fuel_in": port("in", "fuel"),
      "control": port("in", "signal"),
      "exhaust": port("out", "exhaust", 2),
    },
    "instances": instances,
    "connections": connections,
    "nets": {
      "AcOutput": {"kind": "ac-power", "connections": [
        c["id"] for c in connections if "Genset" in c["from"] and "ac_out" in c["from"]]},
      "Fuel": {"kind": "fuel", "connections": [
        c["id"] for c in connections if "Fuel" in c["from"]]},
      "Control": {"kind": "signal", "connections": [
        c["id"] for c in connections if "Control" in c["from"]]},
    },
    "boundary": {
      "out": ["GenBus1.out"],
      "fuel_in": ["FuelHeader1.in"],
      "control": ["ControlSplit1.in"],
      "exhaust": ["Genset1.exhaust", "Genset2.exhaust"],
    },
    "layout": {"Genset1": [0.2, 0.2, 0], "Genset2": [6.2, 0.2, 0],
               "GenBus1": [0.2, 1.8, 1.8], "FuelHeader1": [5.9, 1.8, 0.5],
               "ControlSplit1": [11.5, 1.8, 1.5]},
    "parameters": {
      "prime_kw_total": binding(6000, "kW"),
      "genset_count": binding(2, "台"),
      "fuel_storage_l": binding(9000, "L"),
      "fuel_storage_hours": binding(12, "h"),
      "fire_resistance_hours": binding(1.0, "h"),
    },
    "properties": {
      "mass_kg": 40000,
      "fire": {"class": "方舱围护不燃，储油区防火堤", "fire_resistance_hours": 1.0},
      "weathering": {"installation": "outdoor", "ip": "IP23", "noise_dba_at_1m": 75},
    },
    "model": "containerized-standby-power",
    "method": "2×3000 kW 机组 N+1 并机输出；日用油箱与埋地罐合计 9000 L，满负荷约 15 h（GB 50174 8.1.15）。",
    "limitations": "概念脱敏；实际并机同步、排烟与储油消防由项目校核。",
  }


# -------------------------------------------------------------- constructions

def steel_frame_bay() -> dict:
  instances = {f"Column{i}": {"component": "SteelSupport"} for i in range(1, 5)}
  instances.update({"Beam1": {"component": "SteelSupport"}, "Beam2": {"component": "SteelSupport"}})
  pairs = [("Column1.bearing_b", "Beam1.bearing_a"), ("Column2.bearing_b", "Beam1.bearing_b"),
           ("Column3.bearing_b", "Beam2.bearing_a"), ("Column4.bearing_b", "Beam2.bearing_b")]
  connections = [conn(i + 1, a, b) for i, (a, b) in enumerate(pairs)]
  return {
    "title": "钢框架标准跨",
    "envelope": [8.0, 8.0, 4.5],
    "color": [0.5, 0.5, 0.55],
    "imports": {"SteelSupport": "@structure/library/modules/steel-support/steel-support.sdl.json"},
    "components": {},
    "ports": {f"bearing_{i}": port("inout", "structural-bearing") for i in range(1, 5)},
    "instances": instances,
    "connections": connections,
    "nets": {"Frame": {"kind": "structural-bearing", "connections": [c["id"] for c in connections]}},
    "boundary": {f"bearing_{i}": [f"Column{i}.bearing_a"] for i in range(1, 5)},
    "layout": {"Column1": [0, 0, 0], "Column2": [7.6, 0, 0],
               "Column3": [0, 7.6, 0], "Column4": [7.6, 7.6, 0],
               "Beam1": [0, 0, 4.1], "Beam2": [0, 7.6, 4.1]},
    "parameters": {
      "bay_span_m": binding(8, "m"),
      "clear_height_m": binding(4.5, "m"),
      "fire_resistance_hours": binding(2.0, "h"),
      "fire_resistance_grade": binding(2, "级"),
    },
    "properties": {
      "mass_kg": 6500,
      "fire": {"class": "钢构件防火涂料 2.0 h（概念）", "fire_resistance_hours": 2.0,
               "fire_resistance_grade": 2},
      "weathering": {"installation": "indoor", "ip": None},
      "steel_grade": "Q355B（概念）",
    },
    "model": "single-bay-steel-frame",
    "method": "8 m 跨标准框架，四柱两梁；耐火等级二级对应 GB 50174 13.2.1。",
    "limitations": "概念脱敏；实际截面、节点与抗震由结构专业校核。",
  }


def fire_partition_wall() -> dict:
  return {
    "title": "防火隔墙",
    "envelope": [6.0, 0.2, 4.5],
    "color": [0.65, 0.6, 0.55],
    "imports": {"WallPanel": "@architecture/library/modules/wall-panel/wall-panel.sdl.json"},
    "components": {},
    "ports": {},
    "instances": {"Panel": {"component": "WallPanel"}},
    "connections": [],
    "nets": {},
    "boundary": {},
    "layout": {"Panel": [0, 0, 0]},
    "parameters": {
      "fire_resistance_hours": binding(2.0, "h"),
      "thickness_mm": binding(200, "mm"),
    },
    "properties": {
      "mass_kg": 2600,
      "fire": {"class": "A 级不燃", "fire_resistance_hours": 2.0},
      "weathering": {"installation": "indoor", "ip": None},
      "material": "蒸压加气混凝土板（概念）",
    },
    "model": "fire-rated-partition",
    "method": "主机房与辅助区分隔，耐火极限 ≥ 2.0 h（GB 50174 13.2.4）。",
    "limitations": "概念脱敏；实际接缝封堵与管线穿越由项目校核。",
  }


UNITS = {
  "it-container": it_container(),
  "power-container": power_container(),
  "cooling-container": cooling_container(),
  "generator-container": generator_container(),
  "steel-frame-bay": steel_frame_bay(),
  "fire-partition-wall": fire_partition_wall(),
}


def root_name(name: str) -> str:
  return "".join(part.capitalize() for part in name.split("-"))


def build_sdl(name: str, unit: dict) -> dict:
  root = root_name(name)
  composite = {
    "ports": unit["ports"],
    "instances": unit["instances"],
    "connections": unit["connections"],
    "nets": unit["nets"],
    "behavior": {
      "reference": "assets/behavior.json",
      "state": "assumed",
      "source": SOURCE,
      "parameters": {key: value for key, value in unit["parameters"].items()
                     if value.get("unit") is not None},
    },
  }
  return {
    "schema_version": 2,
    "root": root,
    "imports": unit["imports"],
    "components": {**unit["components"], root: composite},
  }


def build_geometry(name: str, unit: dict) -> str:
  ex, ey, ez = unit["envelope"]
  r, g, b = unit["color"]
  lines = [
    '#usda 1.0', '(', '    defaultPrim = "Module"', '    metersPerUnit = 1', '    upAxis = "Z"', ')',
    '', 'def Xform "Module"', '{', '    def Scope "Geometry"', '    {',
    '        def Cube "Envelope"', '        {',
    f'            color3f[] primvars:displayColor = [({r}, {g}, {b})]',
    '            double size = 1',
    f'            double3 xformOp:scale = ({ex}, {ey}, {ez})',
    f'            double3 xformOp:translate = ({ex / 2}, {ey / 2}, {ez / 2})',
    '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
    '        }',
  ]
  for inst, pos in unit["layout"].items():
    lines += [
      f'        def Cube "{inst}"', '        {',
      '            color3f[] primvars:displayColor = [(0.55, 0.58, 0.62)]',
      '            double size = 1',
      '            double3 xformOp:scale = (0.6, 0.6, 0.6)',
      f'            double3 xformOp:translate = ({pos[0] + 0.3}, {pos[1] + 0.3}, {pos[2] + 0.3})',
      '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
      '        }',
    ]
  lines += ['    }', '', '    def Scope "Ports"', '    {']
  n = len(unit["ports"])
  for i, (pname, p) in enumerate(unit["ports"].items()):
    x = ex * (i + 1) / (n + 1) if n else ex / 2
    lines += [
      f'        def Xform "{pname}"', '        {',
      '            custom double3 texsys:normal = (0, -1, 0)',
      f'            double3 xformOp:translate = ({x:.3f}, 0, {ez / 2})',
      '            uniform token[] xformOpOrder = ["xformOp:translate"]',
      '        }',
    ]
  lines += ['    }', '}', '']
  return "\n".join(lines)


def build_symbol(name: str, unit: dict) -> str:
  parts = [
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="140" viewBox="0 0 200 140">',
    '  <rect x="20" y="20" width="160" height="90" fill="none" stroke="#333" stroke-width="2"/>',
    f'  <text x="100" y="45" font-size="11" text-anchor="middle" fill="#333">{unit["title"]}</text>',
  ]
  n = len(unit["ports"])
  for i, pname in enumerate(unit["ports"]):
    x = 20 + (i + 1) * 160 / (n + 1) if n else 100
    parts.append(f'  <circle id="port-{pname}" cx="{x:.1f}" cy="110" r="4" fill="#0a7"/>')
    parts.append(f'  <text x="{x:.1f}" y="126" font-size="7" text-anchor="middle">{pname}</text>')
  parts.append('</svg>')
  return "\n".join(parts) + "\n"


def build_specification(name: str, unit: dict) -> dict:
  ex, ey, ez = unit["envelope"]
  n = len(unit["ports"])
  return {
    "schema_version": 1,
    "state": "assumed",
    "basis": SOURCE,
    "envelope_m": unit["envelope"],
    "frame": {"origin": "module-minimum-corner", "units": "m", "up_axis": "Z"},
    "interfaces": {
      pname: {"position_m": [ex * (i + 1) / (n + 1) if n else ex / 2, 0, ez / 2],
              "normal": [0, -1, 0]}
      for i, pname in enumerate(unit["ports"])
    },
    "boundary": unit["boundary"],
    "instance_layout_m": unit["layout"],
    "properties": {
      "geometry_parts": {
        "Envelope": {"origin_m": [0, 0, 0], "size_m": unit["envelope"], "color": unit["color"]}
      },
      **unit["properties"],
    },
  }


def build_behavior(name: str, unit: dict) -> dict:
  return {
    "schema_version": 1,
    "state": "assumed",
    "model": unit["model"],
    "parameters": {key: value["unit"] for key, value in unit["parameters"].items()
                   if value.get("unit") is not None},
    "method": unit["method"],
    "limitations": unit["limitations"],
  }


def build_module_toml(name: str, unit: dict) -> str:
  lines = ["schema_version = 1", f'name = "{unit["title"]}"', 'version = "0.1.0"',
           f'entry = "{name}.sdl.json"', ""]
  for kind, path, fidelity in (
    ("geometry", "assets/geometry.usda", "方舱包络、内部布置与接口位姿"),
    ("symbol", "assets/symbol.svg", "功能符号与端口锚点"),
    ("specification", "assets/specification.json", "坐标、边界映射、防火与耐候属性"),
    ("behavior", "assets/behavior.json", unit["model"]),
  ):
    lines += [f"[assets.{kind}]", f'kind = "{kind}"', f'path = "{path}"',
              f'source = "{SOURCE}"', f'fidelity = "{fidelity}"', ""]
  for pname in unit["ports"]:
    lines += [f"[port_bindings.{pname}]", f'geometry = "/Module/Ports/{pname}"',
              f'symbol = "port-{pname}"', ""]
  return "\n".join(lines)


def main() -> None:
  for name, unit in UNITS.items():
    directory = ROOT / name
    assets = directory / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.sdl.json").write_text(
      json.dumps(build_sdl(name, unit), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "module.toml").write_text(build_module_toml(name, unit), encoding="utf-8")
    (assets / "geometry.usda").write_text(build_geometry(name, unit), encoding="utf-8")
    (assets / "symbol.svg").write_text(build_symbol(name, unit), encoding="utf-8")
    (assets / "specification.json").write_text(
      json.dumps(build_specification(name, unit), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (assets / "behavior.json").write_text(
      json.dumps(build_behavior(name, unit), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {name}: {len(unit['connections'])} connections, {len(unit['instances'])} instances")


if __name__ == "__main__":
  main()
