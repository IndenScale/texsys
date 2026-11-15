"""Authoring aid: generate device-family module files from the spec table.

Run from the repository root:
  uv run --locked python packages/datacenter-reference/tools/generate_devices.py

Outputs are committed source; re-run after editing SPECS to refresh assets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "library" / "modules"
SOURCE = "Texsys 数据中心参考设计库概念假设（公开产品水平脱敏）"

# name: (中文名, envelope_m, color, ports, parameters, properties, model, method, limitations)
# ports: name -> (direction, kind, position_m, normal)
SPECS: dict[str, dict] = {
  "facility-aux-transfer": {
    "title": "辅助负荷转换柜",
    "envelope": [.72, 1., 2.1],
    "color": [.49, .51, .55],
    "ports": {
      "in_a": ("in", "ac-power", [.14, 0, 1.0], [0, -1, 0]),
      "in_b": ("in", "ac-power", [.58, 0, 1.0], [0, -1, 0]),
      "out_4": ("out", "ac-power", [.36, 1., 1.25], [0, 1, 0]),
    },
    "parameters": {"rated_a": (125, "A")},
    "properties": {"weathering": {"installation": "outdoor", "ip": "IP54"}},
    "model": "open-transition-auxiliary-switchboard",
    "method": "两路交流输入经开路转换供共用辅助负荷。",
    "limitations": "共用转换柜故障影响其下游，黑启动另行设计。",
  },
  "generator-command-panel": {
    "title": "发电机启动控制柜",
    "envelope": [.72, .60, 1.65],
    "color": [.48, .56, .58],
    "ports": {
      "power": ("in", "ac-power", [.36, 0, 1.25], [0, -1, 0]),
      "out_1": ("out", "signal", [.20, .60, 1.30], [0, 1, 0]),
      "out_2": ("out", "signal", [.52, .60, 1.30], [0, 1, 0]),
    },
    "parameters": {"input_kw": (1.5, "kW")},
    "properties": {"weathering": {"installation": "outdoor", "ip": "IP54"}},
    "model": "common-generator-command",
    "method": "接收辅助电源并提供两路启动指令。",
    "limitations": "指令互锁、控制电源电池、双路自治及黑启动试验待深化。",
  },
  "dry-type-transformer": {
    "title": "干式变压器",
    "envelope": [1.8, 1.2, 2.0],
    "color": [0.35, 0.4, 0.45],
    "ports": {
      "primary": ("in", "ac-power", [0.4, 0.6, 2.0], [0, 0, 1]),
      "secondary": ("out", "ac-power", [1.4, 0.6, 2.0], [0, 0, 1]),
    },
    "parameters": {
      "rated_kva": (3150, "kVA"),
      "primary_kv": (10, "kV"),
      "secondary_kv": (0.4, "kV"),
      "impedance_pct": (6, "%"),
      "no_load_loss_kw": (3.2, "kW"),
      "load_loss_kw": (22, "kW"),
    },
    "properties": {
      "mass_kg": 4200,
      "fire": {"class": "不燃（环氧浇注干式）", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP31"},
      "cooling": "AN/AF",
    },
    "model": "ideal-two-winding-transformer",
    "method": "额定容量与阻抗用于供电容量和短路筛查；损耗按空载与负载分量线性叠加。",
    "limitations": "概念参数取自公开产品水平脱敏；实际温升、谐波降容与保护配合由项目校核。",
  },
  "lv-switchboard": {
    "title": "低压开关柜",
    "envelope": [1.0, 1.2, 2.2],
    "color": [0.45, 0.48, 0.52],
    "ports": {
      "in": ("in", "ac-power", [0.5, 0.1, 2.2], [0, -1, 0]),
      "out_1": ("out", "ac-power", [0.2, 1.1, 2.2], [0, 1, 0]),
      "out_2": ("out", "ac-power", [0.4, 1.1, 2.2], [0, 1, 0]),
      "out_3": ("out", "ac-power", [0.6, 1.1, 2.2], [0, 1, 0]),
      "out_4": ("out", "ac-power", [0.8, 1.1, 2.2], [0, 1, 0]),
    },
    "parameters": {
      "rated_a": (4000, "A"),
      "breaking_ka": (65, "kA"),
    },
    "properties": {
      "mass_kg": 800,
      "fire": {"class": "金属封闭不燃", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP41"},
    },
    "model": "radial-feeder-switchboard",
    "method": "进线一路，馈线四路；额定电流与分断能力用于容量和短路筛查。",
    "limitations": "概念脱敏；实际母线温升、动热稳定与选择性配合由项目校核。",
  },
  "ats": {
    "title": "自动转换开关",
    "envelope": [0.8, 0.6, 2.0],
    "color": [0.5, 0.45, 0.4],
    "ports": {
      "source_a": ("in", "ac-power", [0.2, 0.1, 2.0], [0, -1, 0]),
      "source_b": ("in", "ac-power", [0.6, 0.1, 2.0], [0, -1, 0]),
      "out": ("out", "ac-power", [0.4, 0.5, 2.0], [0, 1, 0]),
    },
    "parameters": {
      "rated_a": (4000, "A"),
      "transfer_time_s": (10, "s"),
    },
    "properties": {
      "mass_kg": 350,
      "fire": {"class": "金属封闭不燃", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP31"},
    },
    "model": "two-source-transfer-switch",
    "method": "常用/备用两路输入一路输出；切换时间用于供电连续性筛查。",
    "limitations": "概念脱敏；实际 PC/CB 级选型、旁路隔离与发电机启动联锁由项目校核。",
  },
  "ups-module": {
    "title": "模块化 UPS 功率模块",
    "envelope": [0.6, 0.8, 2.0],
    "color": [0.2, 0.3, 0.5],
    "ports": {
      "ac_in": ("in", "ac-power", [0.15, 0.1, 2.0], [0, -1, 0]),
      "ac_out": ("out", "ac-power", [0.45, 0.1, 2.0], [0, -1, 0]),
      "battery": ("in", "dc-power", [0.3, 0.7, 2.0], [0, 1, 0]),
    },
    "parameters": {
      "rated_kva": (500, "kVA"),
      "efficiency": (0.96, None),
      "overload_125_pct_min": (10, "min"),
    },
    "properties": {
      "mass_kg": 300,
      "fire": {"class": "金属外壳不燃", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP20"},
    },
    "model": "double-conversion-ups-module",
    "method": "在线双变换；额定 kVA 与效率用于容量和热负荷计算，电池端口接电池柜。",
    "limitations": "概念脱敏；实际并机环流、电池管理与切换瞬态由项目校核。",
  },
  "battery-cabinet": {
    "title": "电池柜",
    "envelope": [0.8, 0.8, 2.0],
    "color": [0.25, 0.35, 0.3],
    "ports": {
      "dc": ("out", "dc-power", [0.4, 0.4, 2.0], [0, 0, 1]),
    },
    "parameters": {
      "energy_kwh": (120, "kWh"),
      "voltage_v": (480, "V"),
      "autonomy_min": (15, "min"),
    },
    "properties": {
      "mass_kg": 1200,
      "fire": {"class": "磷酸铁锂，柜体阻燃 V-0", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP20"},
      "chemistry": "LFP",
    },
    "model": "battery-string",
    "method": "能量与电压用于后备时间筛查；15 min 对应 A 级 UPS 电池后备概念值。",
    "limitations": "概念脱敏；实际热失控防护、通风与消防联动由项目校核。",
  },
  "busway": {
    "title": "密集型母线槽",
    "envelope": [0.2, 0.15, 3.0],
    "color": [0.55, 0.5, 0.35],
    "ports": {
      "in": ("in", "ac-power", [0.1, 0.075, 0.0], [0, 0, -1]),
      "tap_1": ("out", "ac-power", [0.1, 0.075, 0.75], [0, 0, 1]),
      "tap_2": ("out", "ac-power", [0.1, 0.075, 1.5], [0, 0, 1]),
      "tap_3": ("out", "ac-power", [0.1, 0.075, 2.25], [0, 0, 1]),
      "tap_4": ("out", "ac-power", [0.1, 0.075, 3.0], [0, 0, 1]),
    },
    "parameters": {
      "rated_a": (800, "A"),
      "segment_length_m": (3, "m"),
    },
    "properties": {
      "mass_kg": 45,
      "fire": {"class": "金属外壳不燃", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP54"},
    },
    "model": "busway-segment-with-taps",
    "method": "一段母线四个插接口；额定电流用于机柜列配电容量筛查。",
    "limitations": "概念脱敏；实际温升、短路动稳定与插接箱保护由项目校核。",
  },
  "rack-pdu": {
    "title": "机柜 PDU",
    "envelope": [0.1, 0.1, 1.6],
    "color": [0.15, 0.15, 0.2],
    "ports": {
      "in": ("in", "ac-power", [0.05, 0.05, 1.6], [0, 0, 1]),
      "out_1": ("out", "ac-power", [0.05, 0.0, 0.2], [0, -1, 0]),
      "out_2": ("out", "ac-power", [0.05, 0.0, 0.4], [0, -1, 0]),
      "out_3": ("out", "ac-power", [0.05, 0.0, 0.6], [0, -1, 0]),
      "out_4": ("out", "ac-power", [0.05, 0.0, 0.8], [0, -1, 0]),
      "out_5": ("out", "ac-power", [0.05, 0.0, 1.0], [0, -1, 0]),
      "out_6": ("out", "ac-power", [0.05, 0.0, 1.2], [0, -1, 0]),
      "out_7": ("out", "ac-power", [0.05, 0.0, 1.4], [0, -1, 0]),
      "out_8": ("out", "ac-power", [0.05, 0.0, 1.55], [0, -1, 0]),
    },
    "parameters": {
      "rated_a": (63, "A"),
      "outlets": (24, None),
    },
    "properties": {
      "mass_kg": 8,
      "fire": {"class": "阻燃 V-0", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP20"},
    },
    "model": "metered-rack-pdu",
    "method": "一路输入八路输出（示意）；额定电流用于逐柜配电容量筛查。",
    "limitations": "概念脱敏；实际支路计量、谐波与漏电保护由项目校核。",
  },
  "diesel-generator": {
    "title": "柴油发电机组",
    "envelope": [6.0, 2.4, 2.6],
    "color": [0.3, 0.45, 0.35],
    "ports": {
      "ac_out": ("out", "ac-power", [5.8, 1.2, 1.0], [1, 0, 0]),
      "fuel_in": ("in", "fuel", [0.2, 1.2, 0.5], [-1, 0, 0]),
      "exhaust": ("out", "exhaust", [3.0, 1.2, 2.6], [0, 0, 1]),
      "control": ("in", "signal", [0.2, 0.3, 1.5], [-1, 0, 0]),
    },
    "parameters": {
      "prime_kw": (3000, "kW"),
      "standby_kw": (3300, "kW"),
      "fuel_consumption_lph": (620, "L/h"),
      "voltage_v": (400, "V"),
    },
    "properties": {
      "mass_kg": 18000,
      "fire": {"class": "集装箱不燃围护", "fire_resistance_hours": None},
      "weathering": {"installation": "outdoor-container", "ip": "IP23", "noise_dba_at_1m": 75},
    },
    "model": "containerized-diesel-genset",
    "method": "常用/备用功率与油耗用于备用容量和储油时长筛查。",
    "limitations": "概念脱敏；实际瞬态响应、并机与排放由项目校核。",
  },
  "water-cooled-chiller": {
    "title": "水冷冷水机组",
    "envelope": [4.0, 1.8, 2.2],
    "color": [0.25, 0.4, 0.55],
    "ports": {
      "chw_return": ("in", "chw", [0.3, 0.2, 1.0], [-1, 0, 0]),
      "chw_supply": ("out", "chw", [0.3, 1.6, 1.0], [-1, 0, 0]),
      "cw_in": ("in", "cw", [3.7, 0.2, 1.0], [1, 0, 0]),
      "cw_out": ("out", "cw", [3.7, 1.6, 1.0], [1, 0, 0]),
      "power": ("in", "ac-power", [2.0, 0.9, 2.2], [0, 0, 1]),
    },
    "parameters": {
      "cooling_kw": (1200, "kW"),
      "cop": (5.5, None),
      "chw_flow_m3h": (207, "m3/h"),
      "cw_flow_m3h": (260, "m3/h"),
    },
    "properties": {
      "mass_kg": 8500,
      "fire": {"class": "不燃", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP54"},
      "refrigerant": "R134a（概念）",
    },
    "model": "steady-water-cooled-chiller",
    "method": "额定制冷量与 COP 用于冷源容量和排热计算；冷冻水 7/12 °C，冷却水 32/37 °C。",
    "limitations": "概念脱敏；实际部分负荷性能、喘振与防冻由项目校核。",
  },
  "cooling-tower": {
    "title": "闭式冷却塔",
    "envelope": [3.0, 3.0, 3.5],
    "color": [0.4, 0.5, 0.45],
    "ports": {
      "cw_in": ("in", "cw", [0, 1.5, 3.0], [-1, 0, 0]),
      "cw_out": ("out", "cw", [3, 1.5, 0.5], [1, 0, 0]),
      "makeup": ("in", "makeup-water", [1.5, 3, 0.5], [0, 1, 0]),
      "fan_power": ("in", "ac-power", [1.5, 2.8, 3.5], [0, 0, 1]),
      "blowdown": ("out", "process-drain", [2.2, 3, .25], [0, 1, 0]),
    },
    "parameters": {
      "heat_rejection_kw": (1500, "kW"),
      "approach_c": (4, "degC"),
      "fan_kw": (30, "kW"),
    },
    "properties": {
      "mass_kg": 3500,
      "fire": {"class": "FRP 阻燃", "fire_resistance_hours": None},
      "weathering": {"installation": "outdoor", "ip": "IP55", "wind_resistance": "12 级（概念）"},
    },
    "model": "induced-draft-cooling-tower",
    "method": "排热量与逼近度用于冷却水温和冷源连续性筛查。",
    "limitations": "概念脱敏；实际飘水、结冰与噪声由项目校核。",
  },
  "circulation-pump": {
    "title": "端吸循环泵",
    "envelope": [1.2, 0.5, 0.8],
    "color": [0.3, 0.35, 0.5],
    "ports": {
      "in": ("in", "chw", [0.1, 0.25, 0.4], [-1, 0, 0]),
      "out": ("out", "chw", [1.1, 0.25, 0.4], [1, 0, 0]),
      "power": ("in", "ac-power", [0.6, 0.25, 0.8], [0, 0, 1]),
    },
    "parameters": {
      "flow_m3h": (210, "m3/h"),
      "head_m": (25, "m"),
      "power_kw": (22, "kW"),
      "efficiency": (0.78, None),
    },
    "properties": {
      "mass_kg": 450,
      "fire": {"class": "不燃", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": "IP55"},
    },
    "model": "centrifugal-pump-fixed-speed",
    "method": "额定流量与扬程用于水力筛查；效率用于泵功率估算。",
    "limitations": "概念脱敏；实际汽蚀余量、并联工况与变频由项目校核。",
  },
  "condenser-water-pump": {
    "title": "冷却水循环泵",
    "envelope": [1.2, 0.5, 0.8],
    "color": [0.35, 0.4, 0.45],
    "ports": {
      "in": ("in", "cw", [0, 0.25, 0.4], [-1, 0, 0]),
      "out": ("out", "cw", [1.2, 0.25, 0.4], [1, 0, 0]),
      "power": ("in", "ac-power", [0.85, 0.25, 0.8], [0, 0, 1]),
    },
    "parameters": {
      "flow_m3h": (260, "m3/h"),
      "head_m": (25, "m"),
      "power_kw": (22, "kW"),
      "efficiency": (0.78, None),
    },
    "properties": {
      "mass_kg": 480,
      "fire": {"class": "不燃", "fire_resistance_hours": None},
      "weathering": {"installation": "outdoor", "ip": "IP55"},
    },
    "model": "centrifugal-pump-fixed-speed",
    "method": "额定流量与扬程用于冷却水水力筛查；效率用于泵功率估算。",
    "limitations": "概念脱敏；实际汽蚀余量、并联工况与变频由项目校核。",
  },
  "raised-access-floor": {
    "title": "防静电活动地板",
    "envelope": [0.6, 0.6, 0.04],
    "color": [0.6, 0.6, 0.62],
    "ports": {},
    "parameters": {
      "panel_size_mm": (600, "mm"),
      "pedestal_height_m": (0.4, "m"),
      "concentrated_load_kn": (12, "kN"),
      "surface_resistance_ohm_log10": (8, "log10(Ω)"),
    },
    "properties": {
      "mass_kg": 35,
      "fire": {"class": "A 级不燃", "fire_resistance_hours": None},
      "weathering": {"installation": "indoor", "ip": None},
    },
    "model": "steel-cementitious-access-floor-panel",
    "method": "集中荷载与表面电阻用于承载和静电筛查（GB 50174 8.3）。",
    "limitations": "概念脱敏；实际支架抗震、接地连续性与耐磨由项目校核。",
  },
}


def usda_geometry(name: str, spec: dict) -> str:
  ex, ey, ez = spec["envelope"]
  r, g, b = spec["color"]
  lines = [
    '#usda 1.0',
    '(',
    '    defaultPrim = "Module"',
    '    metersPerUnit = 1',
    '    upAxis = "Z"',
    ')',
    '',
    'def Xform "Module"',
    '{',
    '    def Scope "Geometry"',
    '    {',
    '        def Cube "Envelope"',
    '        {',
    f'            color3f[] primvars:displayColor = [({r}, {g}, {b})]',
    '            double size = 1',
    f'            double3 xformOp:scale = ({ex}, {ey}, {ez})',
    f'            double3 xformOp:translate = ({ex / 2}, {ey / 2}, {ez / 2})',
    '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
    '        }',
    '    }',
    '',
    '    def Scope "Ports"',
    '    {',
  ]
  for port, (_, _, pos, normal) in spec["ports"].items():
    lines += [
      f'        def Xform "{port}"',
      '        {',
      f'            custom double3 texsys:normal = ({normal[0]}, {normal[1]}, {normal[2]})',
      f'            double3 xformOp:translate = ({pos[0]}, {pos[1]}, {pos[2]})',
      '            uniform token[] xformOpOrder = ["xformOp:translate"]',
      '        }',
    ]
  lines += ['    }', '}', '']
  return "\n".join(lines)


def symbol_svg(name: str, spec: dict) -> str:
  ex, ey, _ = spec["envelope"]
  width, height = 160, 120
  parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
    '  <rect x="20" y="20" width="120" height="80" fill="none" stroke="#333" stroke-width="2"/>',
    f'  <text x="80" y="65" font-size="10" text-anchor="middle" fill="#333">{spec["title"]}</text>',
  ]
  n = len(spec["ports"])
  for i, port in enumerate(spec["ports"]):
    x = 20 + (i + 1) * 120 / (n + 1) if n else 80
    parts.append(f'  <circle id="port-{port}" cx="{x:.1f}" cy="100" r="4" fill="#0a7"/>')
    parts.append(f'  <text x="{x:.1f}" y="114" font-size="6" text-anchor="middle">{port}</text>')
  parts.append('</svg>')
  return "\n".join(parts) + "\n"


def specification(name: str, spec: dict) -> dict:
  return {
    "schema_version": 1,
    "state": "assumed",
    "basis": SOURCE,
    "envelope_m": spec["envelope"],
    "frame": {"origin": "module-minimum-corner", "units": "m", "up_axis": "Z"},
    "interfaces": {
      port: {"position_m": pos, "normal": normal}
      for port, (_, _, pos, normal) in spec["ports"].items()
    },
    "properties": {
      "geometry_parts": {
        "Envelope": {"origin_m": [0, 0, 0], "size_m": spec["envelope"], "color": spec["color"]}
      },
      **spec["properties"],
    },
  }


def behavior(name: str, spec: dict) -> dict:
  return {
    "schema_version": 1,
    "state": "assumed",
    "model": spec["model"],
    "parameters": {key: unit for key, (_, unit) in spec["parameters"].items()},
    "method": spec["method"],
    "limitations": spec["limitations"],
  }


def sdl(name: str, spec: dict) -> dict:
  root = "".join(part.capitalize() for part in name.split("-"))
  return {
    "schema_version": 1,
    "root": root,
    "components": {
      root: {
        "ports": {
          port: {"direction": direction, "kind": kind, "max_connections": 1}
          for port, (direction, kind, _, _) in spec["ports"].items()
        },
        "behavior": {
          "reference": "assets/behavior.json",
          "state": "assumed",
          "source": SOURCE,
          "parameters": {
            key: {"value": value, "unit": unit, "state": "assumed", "source": SOURCE}
            for key, (value, unit) in spec["parameters"].items()
            if unit is not None
          },
        },
      }
    },
  }


def module_toml(name: str, spec: dict) -> str:
  lines = [
    "schema_version = 1",
    f'name = "{spec["title"]}"',
    'version = "0.1.0"',
    f'entry = "{name}.sdl.json"',
    "",
  ]
  for kind, path, fidelity in (
    ("geometry", "assets/geometry.usda", "设备包络与接口位姿"),
    ("symbol", "assets/symbol.svg", "功能符号与端口锚点"),
    ("specification", "assets/specification.json", "坐标、接口、防火与耐候属性"),
    ("behavior", "assets/behavior.json", spec["model"]),
  ):
    lines += [
      f"[assets.{kind}]",
      f'kind = "{kind}"',
      f'path = "{path}"',
      f'source = "{SOURCE}"',
      f'fidelity = "{fidelity}"',
      "",
    ]
  for port in spec["ports"]:
    lines += [
      f"[port_bindings.{port}]",
      f'geometry = "/Module/Ports/{port}"',
      f'symbol = "port-{port}"',
      "",
    ]
  return "\n".join(lines)


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("modules", nargs="*", metavar="module")
  args = parser.parse_args()
  for name in args.modules or SPECS:
    spec = SPECS[name]
    directory = ROOT / name
    assets = directory / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.sdl.json").write_text(
      json.dumps(sdl(name, spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "module.toml").write_text(module_toml(name, spec), encoding="utf-8")
    (assets / "geometry.usda").write_text(usda_geometry(name, spec), encoding="utf-8")
    (assets / "symbol.svg").write_text(symbol_svg(name, spec), encoding="utf-8")
    (assets / "specification.json").write_text(
      json.dumps(specification(name, spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (assets / "behavior.json").write_text(
      json.dumps(behavior(name, spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {name}")


if __name__ == "__main__":
  main()
