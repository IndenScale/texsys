"""Project supply, exhaust and rack environment onto calibrated pod drawings."""

from xml.etree.ElementTree import SubElement, indent, tostring

from texsys.targets import TargetResult

from .air_routing import MODEL as AIR
from .air_study import MODEL as STUDY
from .plans import _add, _line, _rect, _text, _xy
from .pod_layout import MODEL as PODS
from .pod_views import _base, _dim, _symbol
from .spatial_design import artifact, staged_json


_STYLE = """
.supply-duct{fill:#a7ccd9;stroke:#255b79;stroke-width:.024}
.return-face{fill:#e0b588;stroke:#8c4f23;stroke-width:.022}
.exhaust{fill:#f1d7b7;stroke:#8c4f23;stroke-width:.018}
.cool-flow{stroke:#235e83;fill:none;stroke-width:.027}
.hot-flow{stroke:#90501d;fill:none;stroke-width:.024}
.safe{fill:#d5e8f0;stroke:#426a82;stroke-width:.026}
.warm{fill:#f3e0b1;stroke:#8a652e;stroke-width:.026}
.fail{fill:#efd0cb;stroke:#903f35;stroke-width:.030}
"""

_CASES = {
  "normal": "正常工况",
  "electrical-train-off:A": "A 列退出，B 列运行",
  "electrical-train-off:B": "B 列退出，A 列运行",
}


def _sheet(region, label):
  svg = _base(region, label)
  SubElement(svg, "style").text = _STYLE
  return svg


def _arrow(svg, x, y, dx, dy, style, *, size=0.11):
  _line(svg, x - dx * size * 2.5, y - dy * size * 2.5, x, y, style)
  for sign in [-1, 1]:
    _line(svg, x, y, x - size * (dx + sign * dy * 0.48), y - size * (dy - sign * dx * 0.48), style)


def _frame(pod, layout, title):
  length, width, _ = layout["size_m"]
  svg = _sheet((-0.12, -0.67, length + 0.49, width + 0.92), title)
  _rect(svg, (0, 0, length, width), "aisle", data_host=pod)
  for key, row in layout["local_shell"].items():
    if key not in {"ROOF", "D-N-H", "D-S-H"}:
      _rect(svg, _xy(row), "shell", data_shell=key)
  return svg


def _section_station(layout):
  item = layout["local_equipment"]["Rack03"]
  return item["minimum_m"][0] + item["dimensions_m"][0] / 2


def _plan(pod, layout, air):
  length, width, _ = layout["size_m"]
  svg = _frame(pod, layout, f"{pod} 精密空调送回风布置")
  for key, item in layout["local_equipment"].items():
    a, b, c, d = _xy(item)
    _rect(svg, (a, b, c, d), _symbol(item), data_equipment=key)
    if key.startswith("Rack"):
      _text(svg, (a + c) / 2, (b + d) / 2 - 0.03, key.replace("Rack", "R"), 0.105)
  for key, shaft in air["racks"].items():
    _rect(svg, _xy(shaft), "exhaust", data_rear_riser=key)
  for key, unit in air["units"].items():
    boundary = _add(svg, "g", data_air_unit=key)
    _rect(boundary, _xy(unit["elbow"]), "supply-duct")
    _rect(boundary, _xy(unit["return_face"]), "return-face")
    x, y, _ = unit["tip_m"]
    direction = 1 if y < width / 2 else -1
    _arrow(boundary, x, y + 0.26 * direction, 0, direction, "cool-flow", size=0.095)
    _, lower_y, _, upper_y = _xy(layout["local_equipment"][key])
    _text(boundary, x, lower_y + 0.16 if direction > 0 else upper_y - 0.20, key, 0.106)
  sec = _section_station(layout)
  for y in (-0.12, width + 0.12):
    _line(svg, sec - 0.09, y, sec + 0.09, y, "cutting")
    _text(svg, sec + 0.15, y - 0.04, "1", 0.115)
  _text(svg, 0, width + 0.53, f"{pod}  送风短管、空调回风口与机柜后腔", 0.161, "start")
  for index, (key, label) in enumerate(
    (("supply-duct", "送风短管"), ("return-face", "空调回风口"), ("exhaust", "机柜排风后腔"))
  ):
    x = 0.11 + index * 2.16
    _rect(svg, (x, -0.38, x + 0.32, -0.28), key)
    _text(svg, x + 0.43, -0.37, label, 0.112, "start")
  return svg


def _section(pod, layout, air):
  _, width, height = layout["size_m"]
  x = _section_station(layout)
  svg = _sheet((-0.59, -0.56, width + 0.80, height + 0.86), f"{pod} 机柜后腔排风剖面")
  for key, item in layout["local_shell"].items():
    (lo, y, z), (hi, Y, Z) = item["minimum_m"], item["maximum_m"]
    if lo < x < hi:
      _rect(svg, (y, z, Y, Z), "shell", data_shell=key)
  lo, hi = layout["aisle"]["minimum_m"], layout["aisle"]["maximum_m"]
  _rect(svg, (lo[1], 0, hi[1], hi[2]), "aisle", data_access="headroom")
  for key, shaft in air["racks"].items():
    (left, y, z), (right, Y, Z) = shaft["minimum_m"], shaft["maximum_m"]
    if not left < x < right:
      continue
    cabinet = layout["local_equipment"][key]
    (_, y0, z0), (_, y1, z1) = cabinet["minimum_m"], cabinet["maximum_m"]
    _rect(svg, (y0, z0, y1, z1), "rack", data_equipment=key)
    _rect(svg, (y, z, Y, Z), "exhaust", data_rear_riser=key)
    direction = 1 if y0 < width / 2 else -1
    face = y1 if direction > 0 else y0
    _arrow(svg, face, 1.23, -direction, 0, "cool-flow", size=0.10)
    _arrow(svg, (y + Y) / 2, Z + 0.15, 0, 1, "hot-flow", size=0.075)
    _text(svg, (y0 + y1) / 2, 0.96, key, 0.132)
    tag_y = 0.12 if direction > 0 else width - 0.12
    _text(svg, tag_y, -0.30, f"后腔净宽 {shaft['gap_m'] * 1000:.0f} mm", 0.119, "start" if direction > 0 else "end")
  _text(svg, width / 2, 1.42, "冷通道", 0.15)
  _dim(svg, lo[1], hi[1], 0.31, label=f"{(hi[1] - lo[1]) * 1000:.0f} 净")
  _text(svg, width / 2, 2.60, "共用回风空间", 0.137)
  _text(svg, -0.44, height + 0.58, f"1—1  {pod}  机柜送回风横剖面", 0.167, "start")
  _text(svg, -0.44, height + 0.32, f"剖切位置 X {x:.2f} m    舱内基准 ±0.000", 0.116, "start")
  return svg


def _environment(pod, layout, study, case_name):
  _, width, _ = layout["size_m"]
  case = study["cases"][case_name]["pods"][pod]
  svg = _frame(pod, layout, f"{pod} 机柜进风温度")
  for key, shape in layout["local_equipment"].items():
    a, b, c, d = _xy(shape)
    row = case["racks"].get(key)
    if row:
      inlet = row["inlet_c"]
      passed = all(row["checks"].values())
      style = "fail" if not passed else "warm" if inlet > 25 else "safe"
      _rect(svg, (a, b, c, d), style, data_rack=key, data_temperature="none" if inlet is None else inlet)
      _text(svg, (a + c) / 2, (b + d) / 2 + 0.06, key.replace("Rack", "R"), 0.115)
      _text(svg, (a + c) / 2, (b + d) / 2 - 0.11, f"{inlet:.2f} °C" if inlet is not None else "未定", 0.117)
    elif key in case["active_units"]:
      _rect(svg, (a, b, c, d), "cdu", data_running=key)
      _text(svg, (a + c) / 2, (b + d) / 2, key, 0.105)
  temperatures = [row["inlet_c"] for row in case["racks"].values() if row["inlet_c"] is not None]
  status = "满足" if case["status"] == "pass" else "不满足"
  peak = f"最高 {max(temperatures):.2f} °C" if temperatures else "温度待定"
  _text(svg, 0, width + 0.53, f"{pod}   {_CASES.get(case_name, case_name)}", 0.166, "start")
  _text(svg, 0, width + 0.25, f"机柜进风 {status}　·　{peak}", 0.131, "start")
  _text(
    svg,
    0.11,
    -0.38,
    f"合计送风 {case['supplied_m3_s']:.2f} m³/s　·　舱内显热 {case['sensible_load_kw']:.1f} kW",
    0.125,
    "start",
  )
  return svg


def build_air_view(context):
  layout = staged_json(context, PODS)
  air = staged_json(context, AIR)
  name = context.scope.get("pod", air["typical"])
  view = context.scope["view"]
  pod = layout["pods"][name]
  if view == "plan":
    svg = _plan(name, pod, air["pods"][name])
  elif view == "section":
    svg = _section(name, pod, air["pods"][name])
  elif view == "environment":
    svg = _environment(name, pod, staged_json(context, STUDY), context.scope["case"])
  else:
    raise ValueError(f"Unknown air view {view}.")
  destination = context.build_path / context.output
  destination.parent.mkdir(parents=True, exist_ok=True)
  indent(svg)
  destination.write_text(tostring(svg, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult("pass", (artifact(context, f"air-{view}-view", destination),))
