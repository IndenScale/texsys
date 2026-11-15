"""Produce structural bracing plan, calibrated elevations and pad demand sheet."""

from itertools import pairwise
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .fws_buffer_views import _canvas
from .fws_transient_views import _text_at
from .lateral_bracing import MODEL
from .lateral_study import STUDY
from .plans import _add, _axes, _dimension, _line, _rect, _text
from .spatial_design import artifact, staged_json
from .structure import MODEL as FRAME

_STYLE = """
.hall{fill:#f7f9fa;stroke:#647584;stroke-width:.10}
.beam{fill:#dfe6eb;stroke:#455667;stroke-width:.06}
.pad{fill:#e4e7e5;stroke:#4e6265;stroke-width:.074}
.brace-foot{fill:none;stroke:#657381;stroke-width:.075;stroke-dasharray:.22 .17}
.x-brace{fill:none;stroke:#975023;stroke-width:.19}
.y-brace{fill:none;stroke:#285d80;stroke-width:.19}
.node{fill:white;stroke:#5f6d77;stroke-width:.065}
.datum{fill:none;stroke:#71848e;stroke-width:.045;stroke-dasharray:.24 .13}
"""


def _plan(model, frame):
  (x0, x1), (y0, y1) = model["plan_m"]
  root = _canvas((x0-4.73, y0-5.4, x1+3.43, y1+4.), "主机房抗侧力支撑定位")
  SubElement(root, "style").text = _STYLE
  _rect(root, (x0, y0, x1, y1), "hall")
  _axes(root, frame, (x0, y0, x1, y1))
  utilized = {key for row in model["bays"].values() for key in row["columns"]}
  for key, member in frame["members"].items():
    x, y, _ = member["start_m"]
    if member["kind"] == "column":
      section = frame["settings"]["sections_m"]["column"]
      w, d = section["width"]/2, section["depth"]/2
      _rect(root, (x-w, y-d, x+w, y+d), "beam", data_column=key)
      if key in utilized:
        pad = frame["members"][member["pad"]]["parts"][0]
        a, b, _ = pad["origin"]
        w, d, _ = pad["size"]
        _rect(root, (a, b, a+w, b+d), "brace-foot", data_pad=member["pad"])
  for name, row in model["bays"].items():
    a, b = row["start_m"], row["end_m"]
    x, y = ((start+end)/2 for start, end in zip(a, b, strict=True))
    role = row["axis"] + "-brace"
    _line(root, *a, *b, role).set("data-bay", name)
    for u, v in (a, b):
      _add(root, "circle", cx=u, cy=-v, r=.19, **{"class": "node"})
    if row["axis"] == "x":
      _text(root, x, y + .72, name, .52)
    else:
      _text(root, x+.40, y+.18, name, .52, "start")
  _line(root, x0, y1+2.05, x0+1.35, y1+2.05, "x-brace")
  _text(root, x0+1.6, y1+1.87, "X 向支撑", .49, "start")
  _line(root, x0+6.4, y1+2.05, x0+7.75, y1+2.05, "y-brace")
  _text(root, x0+8.0, y1+1.87, "Y 向支撑", .49, "start")
  _text(root, x0+16.0, y1+1.87, "虚线为受侧力柱基础轮廓", .49, "start")
  _add(root, "path", d=f"M{x1+3.0:g},{-y1-1.65:g} l-.4,1.12 .4,-.18 .4,.18 z", fill="#293845")
  _text(root, x1+3.0, y1+1.9, "N", .58)
  return root


def _elevation(root, model, frame, line, axis, dx, dy):
  alongside, across = ("x", "y") if axis == 0 else ("y", "x")
  stations = frame[alongside]
  offset = dx-min(stations.values())
  slab = next(row["start_m"][2] for row in frame["members"].values() if row["kind"] == "column")
  roof = model["roof_beam_top_m"]
  x0, x1 = dx, max(stations.values())+offset
  _text(root, x0, dy+roof+1.07, f"{line} 轴支撑立面", .62, "start")
  _line(root, x0-.8, dy+slab, x1+.75, dy+slab, "datum")
  for name, along in stations.items():
    x = along+offset
    c = frame["members"][f"C-{name}-{line}" if axis == 0 else f"C-{line}-{name}"]
    pad, plinth = frame["members"][c["pad"]]["parts"]
    width = pad["size"][axis]
    z = pad["origin"][2]
    _rect(root, (x-width/2, dy+z, x+width/2, dy+z+pad["size"][2]), "pad", data_pad=c["pad"])
    pier = plinth["size"][axis]/2
    _rect(root, (x-pier, dy+plinth["origin"][2], x+pier, dy+slab), "pad")
    section = frame["settings"]["sections_m"]["column"]
    column = section["width" if axis == 0 else "depth"]/2
    _rect(root, (x-column, dy+slab, x+column, dy+c["end_m"][2]), "beam")
    _text(root, x, dy+z-.51, name, .48)
  section = frame["settings"]["sections_m"]["girder" if axis == 0 else "tie"]
  _rect(root, (x0, dy+roof-section["depth"], x1, dy+roof), "beam")
  _text(root, x1+.73, dy+roof-.14, f"{roof:+.2f}", .42, "start")
  _text(root, x1+.73, dy+slab-.12, f"{slab:+.2f}", .42, "start")
  for start, end in pairwise(stations.values()):
    _dimension(root, offset+start, offset+end, dy-2.7, False)
  mark = frame[across][line]
  for name, row in model["bays"].items():
    if row["axis"] != alongside or row["start_m"][1-axis] != mark:
      continue
    for identity, rod in row["rods"].items():
      a, b = rod["points_m"]
      _line(root, a[axis]+offset, a[2]+dy, b[axis]+offset, b[2]+dy, alongside+"-brace").set("data-rod", identity)
      for point in (a, b):
        _add(root, "circle", cx=point[axis]+offset, cy=-point[2]-dy, r=.13, **{"class": "node"})
    _text(root, offset+(row["start_m"][axis]+row["end_m"][axis])/2, dy+roof-.29, name, .42)


def _elevations(model, frame):
  (x0, x1), (y0, y1) = model["plan_m"]
  width = max(x1-x0+4.5, 2*(y1-y0)+7.)
  lines = {axis: sorted({row["axes"][0][1-axis] for row in model["bays"].values()
                         if row["axis"] == "xy"[axis]}, key=frame["yx"[axis]].get) for axis in (0, 1)}
  transverse_rows = (len(lines[1])+1)//2
  row_pitch = 10.16
  top = (len(lines[0])+transverse_rows-1)*row_pitch+7.85
  root = _canvas((-1.7, -3.3, width, top), "主机房纵横向交叉支撑立面")
  SubElement(root, "style").text = _STYLE
  for index, line in enumerate(lines[0]):
    _elevation(root, model, frame, line, 0, 0., (index+transverse_rows)*row_pitch)
  for index, line in enumerate(lines[1]):
    _elevation(root, model, frame, line, 1, (index%2)*(y1-y0+4.), (index//2)*row_pitch)
  diameter = model["assumptions"]["rod_diameter_m"]*1000
  _text(root, width-.2, top-2.01, f"配对拉杆 Ø{diameter:.0f}   上节点 EL {model['upper_joint_m']:+.2f}   "
        f"下节点 EL {model['lower_joint_m']:+.2f}", .45, "end")
  _text(root, width-.2, top-2.88, "拉杆错开，连接构造见专项复核。", .43, "end")
  return root


def _schedule(study):
  cases = study["cases"]
  pads = next(iter(cases.values()))["foundations"] if cases else {}
  notes_y = 67+9.1*len(pads)
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 320 {notes_y+41:g}"})
  SubElement(root, "title").text = "支撑及独立基础控制作用"
  SubElement(root, "style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#23353e}
.header{fill:#e7edf1;stroke:#576d79;stroke-width:.32}
.rule{stroke:#b2c0c5;stroke-width:.30}
"""
  _text_at(root, 12, 14, "支撑柱及独立基础控制作用", 6.2, "start")
  _text_at(root, 12, 25, f"候选风与地震作用共 {study['intended_cases']} 组，"
           f"有效重力 {study['effective_weight_kn']:.1f} kN。", 3.8, "start")
  if not cases:
    _text_at(root, 12, 47, "当前布置不能形成稳定传力体系。见 lateral-check.json。", 4.4, "start")
    return root
  _add(root, "rect", x=12, y=37, width=296, height=9.5, **{"class": "header"})
  for x, label in ((19, "基础"), (60, "控制工况"), (142, "正压力 kN"), (189, "水平力 kN"),
                   (236, "边缘压力 kPa"), (280, "接触面积率")):
    _text_at(root, x, 43.4, label, 4, "start")
  for index, pad in enumerate(pads):
    key, case = max(cases.items(), key=lambda item: item[1]["foundations"][pad]["peak_soil_kpa"])
    result = case["foundations"][pad]
    xvalues = [result["pad_normal_kn"], sum(h*h for h in result["shear_kn"])**.5, result["peak_soil_kpa"]]
    y = 54+9.1*index
    _add(root, "line", x1=12, x2=308, y1=y+3.3, y2=y+3.3, **{"class": "rule"})
    leaf = _add(root, "g", data_pad=pad, data_case=key)
    _text_at(leaf, 19, y, pad, 3.9, "start")
    _text_at(leaf, 60, y, key, 3.4, "start")
    for x, value in zip((160, 209, 257), xvalues, strict=True):
      _text_at(leaf, x, y, f"{value:.1f}", 3.9, "end")
    _text_at(leaf, 300, y, f"{result['contact_fraction']:.1%}", 3.9, "end")
  limit = max(row["tension_kn"] for case in cases.values() for row in case["rods"].values())
  drift = max(case["drift_ratio"] for case in cases.values() if case["drift_limit_ratio"] is not None)
  _text_at(root, 12, notes_y, f"已计算工况中最大拉杆轴力 {limit:.2f} kN，最大控制层间位移角 {drift:.5f}。", 4, "start")
  _text_at(root, 12, notes_y+12, "逐工况的轴力、底部弯矩、接触面积和抗滑验算见配套计算表。", 3.9, "start")
  _text_at(root, 12, notes_y+24, "表内每行按该基础的最大边缘压力选取。正式地勘、连接和基础配筋待核。", 3.9, "start")
  return root


def build_lateral_view(context):
  mode = context.scope["view"]
  if mode == "schedule":
    root = _schedule(staged_json(context, STUDY))
  else:
    model, frame = (staged_json(context, path) for path in (MODEL, FRAME))
    root = {"plan": _plan, "elevations": _elevations}[mode](model, frame)
  output = context.build_path / context.output
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode")+"\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "lateral-view", output),))
