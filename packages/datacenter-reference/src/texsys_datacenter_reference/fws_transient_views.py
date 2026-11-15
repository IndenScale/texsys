"""Draw equipment-specific switchover curves and a calibrated temperature plan."""

import math
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .fws_transient import MODEL
from .plans import _STYLE, _add, _axes, _north_and_scale, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json


_COLORS = ("#1e5a81", "#31754d", "#625587")


def _text_at(root, x, y, text, size=4, anchor="middle"):
  _add(root, "text", x=x, y=y, text_anchor=anchor, font_size=size).text = text


def _chart(model):
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 310 215"})
  SubElement(root, "title").text = "单冷源退出后设施水入口温升"
  SubElement(root, "style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#263746}
.grid{stroke:#c8d2da;stroke-width:.28}.frame{fill:none;stroke:#4a5a66;stroke-width:.54}
.limit{stroke:#a23c31;stroke-width:.76;stroke-dasharray:3 1.4}
.period{fill:#e9edf1;opacity:.72}.curve{fill:none;stroke-width:1.05}
"""
  settings = model["settings"]
  limit, minimum, duration = (settings[key] for key in ("terminal_inlet_limit_c", "supply_setpoint_c", "duration_s"))
  cases = list(model["scenarios"].values())
  highest = max(limit, *(case["terminals"][case["governing_terminal"]]["comparison_peak_c"] for case in cases))
  lowest = min(minimum, *(min(case["trace"]["cdu_inlet_c"][case["governing_terminal"]]) for case in cases))
  bottom_temp = math.floor((lowest+1e-8)/2)*2
  upper_temp = math.ceil((highest+.5)/2)*2
  low, top, right, bottom = 31, 30, 287, 141

  def x(t):
    return low+t/duration*(right-low)

  def y(t):
    return bottom-(t-bottom_temp)/(upper_temp-bottom_temp)*(bottom-top)

  for degrees in range(bottom_temp, upper_temp+1, max(2, 2*math.ceil((upper_temp-bottom_temp)/16))):
    station = y(degrees)
    _add(root, "line", x1=low, y1=station, x2=right, y2=station, **{"class": "grid"})
    _text_at(root, low-4, station+1.2, str(degrees), 3.7, "end")
  for i in range(7):
    tick = duration*i/6
    _add(root, "line", x1=x(tick), y1=top, x2=x(tick), y2=bottom, **{"class": "grid"})
    _text_at(root, x(tick), bottom+7, f"{tick:g}", 3.6)
  delay, ramp = settings["standby_delay_s"], settings["standby_ramp_s"]
  _add(root, "rect", x=x(0), y=top, width=x(delay+ramp)-x(0), height=bottom-top, **{"class": "period"})
  _add(root, "rect", x=low, y=top, width=right-low, height=bottom-top, **{"class": "frame"})
  _add(root, "line", x1=low, y1=y(limit), x2=right, y2=y(limit), data_limit_c=limit, **{"class": "limit"})
  _text_at(root, right-2, y(limit)-2, f"入口温度预算 {limit:g} °C", 3.7, "end")
  _text_at(root, low, top-5, "CDU 设施水入口温度 / °C", 4, "start")
  _text_at(root, (low+right)/2, bottom+14, "冷源退出后时间 / s", 3.9)
  _text_at(root, 155, 14, "单冷源退出后的设施水入口温升", 6.3)
  for index, case in enumerate(cases):
    name = case["governing_terminal"]
    samples = case["trace"]
    row = case["terminals"][name]
    color = _COLORS[index % len(_COLORS)]
    coords = " ".join(f"{x(t):.3f},{y(value):.3f}" for t, value in
                      zip(samples["time_s"], samples["cdu_inlet_c"][name], strict=True))
    _add(root, "polyline", points=coords, data_outage=case["unavailable_plant"], data_terminal=name,
         stroke=color, **{"class": "curve"})
    ly = 176+index*9
    _add(root, "line", x1=36, y1=ly-1.2, x2=48, y2=ly-1.2, stroke=color, **{"class": "curve"})
    _text_at(root, 53, ly, f"{case['unavailable_plant']} 退出 · {name} · {row['comparison_peak_c']:.2f} °C", 4.4, "start")
  buffer_l = settings["plant_outlet_buffer_m3"]*1000
  _text_at(root, 36, 203, f"浅灰时段为待机启动及升载  {delay:g} + {ramp:g} s · 候选缓冲 {buffer_l:g} L / 台", 4.1, "start")
  return root


def _plan(context, model):
  architecture = staged_json(context, f"{MODELS}/architecture.json")
  grid = staged_json(context, f"{MODELS}/grid.json")
  layout = staged_json(context, f"{MODELS}/layout.json")
  x0, y0, x1, y1 = bounds = _xy(architecture["hall"])
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "data-coordinate-unit": "m", "data-units-per-meter": "1",
                         "viewBox": f"{x0-6:g} {-y1-5:g} {x1-x0+11:g} {y1-y0+13:g}"})
  SubElement(root, "title").text = "逐舱设施水入口温度校核"
  SubElement(root, "style").text = _STYLE + """
.partition{fill:#3f4853}.pod-pass{fill:#e5eff6;stroke:#5b7482;stroke-width:.075}
.pod-limit{fill:#f9ecd8;stroke:#97734d;stroke-width:.075}
.pod-fail{fill:#fae9e5;stroke:#aa4535;stroke-width:.085}
.critical{fill:none;stroke:#304e62;stroke-width:.145}
"""
  _rect(root, bounds, "zone")
  _axes(root, grid, bounds)
  _north_and_scale(root, bounds)
  _rect(root, _xy(architecture["walls"]["W-DIVIDE"]), "partition")
  cases = model["scenarios"]
  names = next(iter(cases.values()))["terminals"]
  values = {name: max(case["terminals"][name]["comparison_peak_c"] for case in cases.values()) for name in names}
  critical = max(values, key=values.__getitem__)
  limit = model["settings"]["terminal_inlet_limit_c"]
  for name, peak in values.items():
    box = _xy(layout["objects"][name])
    lo, bottom, hi, top = box
    classification = "pod-fail" if peak > limit else "pod-limit" if limit-peak < 1 else "pod-pass"
    _rect(root, box, classification, data_pod=name, data_peak_c=f"{peak:.6f}")
    if name == critical:
      _rect(root, box, "critical", data_governing=name)
    _text(root, (lo+hi)/2, (bottom+top)/2+.26, name, .52)
    _text(root, (lo+hi)/2, (bottom+top)/2-.54, f"{peak:.2f} °C", .62)
  _text(root, (x0+x1)/2, y1+.94, f"两种停机工况峰值 · 水温限值 {limit:g} °C", .71)
  return root


def build_transient_view(context):
  model = staged_json(context, MODEL)
  scope = dict(context.scope)
  if scope == {"view": "curves"}:
    root = _chart(model)
  elif scope == {"view": "plan"}:
    root = _plan(context, model)
  else:
    raise ValueError("Select the switchover curves or calibrated pod temperature plan.")
  output = context.build_path / context.output
  if output.suffix != ".svg":
    raise ValueError("FWS thermal sheets require an SVG view destination.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "fws-transient-view", output),))
