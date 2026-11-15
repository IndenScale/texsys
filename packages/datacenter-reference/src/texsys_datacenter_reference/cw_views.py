"""Project CW routing, the section cut and the computed operating diagram."""

from itertools import pairwise
from xml.etree.ElementTree import SubElement, indent, tostring

from texsys.targets import TargetResult

from .cw_network import MODEL, ROLES
from .cw_study import MODEL as STUDY
from .fws_buffer_views import _canvas
from .plans import _add, _line, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json


_STYLE = """
.hot,.discharge,.return{fill:none;stroke-width:.077;stroke-linejoin:round}
.hot,.discharge{stroke:#9d402d}
.return{stroke:#1d6283;stroke-dasharray:.31 .13}
.flange{fill:white;stroke:#293e4c;stroke-width:.065}
.cut{fill:none;stroke:#42515a;stroke-width:.045;stroke-dasharray:.52 .18 .10 .18}
.grade{fill:none;stroke:#42515a;stroke-width:.065}
.direction{stroke-dasharray:none;stroke-width:.075}
"""
_LABELS = {"hot": "冷凝器热水", "discharge": "泵出口至塔", "return": "冷水回冷凝器"}


def _arrow(svg, x, y, dx, dy, role, size=.20):
  _add(svg, "path", d=f"M{x-dx*size-dy*size*.63:g},{-y+dy*size-dx*size*.63:g} "
                     f"L{x:g},{-y:g} L{x-dx*size+dy*size*.63:g},{-y+dy*size+dx*size*.63:g}",
       **{"class": f"{role} direction"})


def _routes(model, plant):
  return [model["routes"][model["cells"][plant]["routes"][role]] for role in ROLES]


def _section_run(model, plant):
  discharge = _routes(model, plant)[1]
  crossing = max(pairwise(discharge["points_m"]), key=lambda pair: abs(pair[1][0]-pair[0][0]))
  (a, y, z), (b, _, _) = crossing
  if a == b:
    raise ValueError(f"{plant}: the section requires an east-west pump discharge offset.")
  return min(a, b), max(a, b), y, z


def _plan(context, model):
  grid = staged_json(context, f"{MODELS}/grid.json")
  layout = staged_json(context, f"{MODELS}/layout.json")["objects"]
  names = [name for plant, cell in model["cells"].items() for name in (plant, cell["pump"], cell["tower"])]
  points = [p for route in model["routes"].values() for p in route["points_m"]]
  assets = {name: _xy(layout[name]) for name in names}
  a = min(min(p[0] for p in points), min(row[0] for row in assets.values()))
  b = min(min(p[1] for p in points), min(row[1] for row in assets.values()))
  c = max(max(p[0] for p in points), max(row[2] for row in assets.values()))
  d = max(max(p[1] for p in points), max(row[3] for row in assets.values()))
  svg = _canvas((a-1.3, b-1.65, c+1.2, d+1.65), "冷源区冷却水接管平面")
  SubElement(svg, "style").text = _STYLE
  for key, x in grid["x"].items():
    if a-.85 <= x <= c+.4:
      _line(svg, x, b-.69, x, d+.7, "grid").set("data-axis", key)
      _add(svg, "circle", cx=x, cy=-(b-.72), r=.24, **{"class": "axis"})
      _text(svg, x, b-.81, key, .26)
  for key, y in grid["y"].items():
    if b <= y <= d:
      _line(svg, a-.73, y, c+.39, y, "grid").set("data-axis", key)
      _add(svg, "circle", cx=a-.74, cy=-y, r=.24, **{"class": "axis"})
      _text(svg, a-.74, y-.09, key, .26)
  pumps = {cell["pump"] for cell in model["cells"].values()}
  for name, bbox in assets.items():
    x0, y0, x1, y1 = bbox
    _rect(svg, bbox, "equipment", data_instance=name)
    if name in pumps:
      _text(svg, x1+.08, y1+.25, name, .28, "start")
    else:
      _text(svg, (x0+x1)/2, (y0+y1)/2, name, .30)
  for key, row in model["routes"].items():
    role, points = row["role"], row["points_m"]
    group = _add(svg, "g", data_connection=key, data_cell=row["plant"])
    for first, last in pairwise(points):
      a0, a1 = first[:2], last[:2]
      if a0 != a1:
        _line(group, *a0, *a1, role)
      distance = abs(a1[0]-a0[0])+abs(a1[1]-a0[1])
      if distance >= 1.0:
        x, y = ((u+v)/2 for u, v in zip(a0, a1, strict=True))
        _arrow(group, x, y, (a1[0]-a0[0])/distance, (a1[1]-a0[1])/distance, role)
    for endpoint, (x, y, _) in zip((row["from"], row["to"]), (points[0], points[-1]), strict=True):
      _add(group, "circle", cx=x, cy=-y, r=.09, **{"class": "flange"}, data_port=endpoint)
    # Put the tags at the longest projected leg before associating the same IDs in the schedule.
    first, last = max(pairwise(points), key=lambda pair: abs(pair[1][0]-pair[0][0])+abs(pair[1][1]-pair[0][1]))
    if role == "discharge":
      x = min(first[0], last[0])+abs(last[0]-first[0])*.34
      _text(group, x, first[1]+.20, key, .24)
    elif role == "hot":
      _text(group, first[0]-.17, (first[1]+last[1])/2, key, .24, "end")
    else:
      y = min(first[1], last[1])+abs(last[1]-first[1])*.26
      _text(group, first[0]+.18, y, key, .24, "start")
  start, end, station, _ = _section_run(model, next(iter(model["cells"])))
  for x, other in ((start-.18, start-.90), (end+.18, end+.75)):
    _line(svg, other, station, x, station, "cut")
    _line(svg, x, station, x, station+.46, "cut")
    _text(svg, x, station+.69, "1", .32)
  _text(svg, c+.45, d+1.02, "N", .34)
  _arrow(svg, c+.45, d+.79, 0, 1, "grade", .49)
  _line(svg, c+.45, d+.23, c+.45, d+.72, "grade")
  _text(svg, (a+c)/2, d+.77, f"{len(model['cells'])} 列独立闭式回路    {model['pipe']['designation']}", .39)
  for i, (role, label) in enumerate(_LABELS.items()):
    x = a+4+i*6
    _line(svg, x, b-1.15, x+.62, b-1.15, role)
    _text(svg, x+.78, b-1.24, label, .29, "start")
  return svg


def _section(context, model, plant):
  a, b, station, level = _section_run(model, plant)
  grade = staged_json(context, f"{MODELS}/layout.json")["objects"][plant]["origin_m"][2]
  svg = _canvas((a-.33, grade-.72, b+.33, max(grade+4.1, level+.8)), f"{plant} 冷却水管廊横剖面")
  SubElement(svg, "style").text = _STYLE + ".hot,.discharge,.return{stroke-width:.035}.grade{stroke-width:.035}"
  _text(svg, a, max(grade+3.64, level+.64), f"1—1   {plant}   Y = {station:.2f} m", .16, "start")
  _text(svg, a, grade+.14, f"地坪 +{grade:.2f}", .15, "start")
  _line(svg, a-.21, grade, b+.21, grade, "grade")
  radius = model["pipe"]["envelope_m"]/2
  for row in _routes(model, plant):
    role = row["role"]
    points = row["points_m"]
    group = _add(svg, "g", data_connection=row["connection"])
    for (x0, y0, z0), (x1, y1, z1) in pairwise(points):
      if y0 == y1 == station and x0 != x1:
        _rect(group, (min(x0, x1), z0-radius, max(x0, x1), z0+radius), role)
        _text(group, (x0+x1)/2, z0+.23, f"{_LABELS[role]} +{z0:.2f}", .15)
      if min(y0, y1) < station < max(y0, y1):
        _add(group, "circle", cx=x0, cy=-z1, r=radius, **{"class": role})
        _text(group, x0+.24, z1+.03, f"{_LABELS[role]} +{z1:.2f}", .14, "start")
        _line(group, x0, grade, x0, grade-.10, "grade")
        _text(group, x0, grade-.27, f"X {x0:.2f}", .12)
  _text(svg, (a+b)/2, grade-.45, f"外包络 φ{model['pipe']['envelope_m']*1000:.0f} mm（含保温）", .15)
  return svg


def _diagram(model, study):
  svg = _canvas((0, 0, 29, 15.2), "冷却水系统及设计运行工况")
  SubElement(svg, "style").text = _STYLE
  normal = study["cases"]["normal"]["duty"]
  for i, (plant, cell) in enumerate(model["cells"].items()):
    y = 12.8-i*4.45
    group = _add(svg, "g", data_cell=plant)
    _rect(group, (1, y-1.02, 7.1, y+.71), "equipment")
    _text(group, 4.05, y+.08, f"{plant}  冷凝器", .34)
    _add(group, "circle", cx=13.1, cy=-y, r=.46, **{"class": "flange"})
    _arrow(group, 13.34, y, 1, 0, "hot", .32)
    _text(group, 13.1, y+.79, cell["pump"], .34)
    _rect(group, (19, y-1.02, 24.8, y+.71), "equipment")
    _text(group, 21.9, y+.08, f"{cell['tower']}  闭式塔", .34)
    for role, x0, x1 in (("hot", 7.1, 12.64), ("discharge", 13.56, 19)):
      _line(group, x0, y, x1, y, role).set("data_connection", cell["routes"][role])
      _arrow(group, x0+(x1-x0)*.63, y, 1, 0, role, .24)
    _add(group, "polyline", points=f"21.9,{-y+1.02:g} 21.9,{-y+1.72:g} 4.05,{-y+1.72:g} 4.05,{-y+1.02:g}",
         data_connection=cell["routes"]["return"], **{"class": "return"})
    _arrow(group, 10, y-1.72, -1, 0, "return", .27)
    row = normal.get(plant)
    if row:
      h = study["hydraulics"][plant]
      _text(group, 9.9, y+.31, f"{row['hot_c']:.1f} °C", .29)
      _text(group, 16, y+.31, f"{h['flow_m3_s']*3600:.0f} m³/h", .29)
      _text(group, 25.45, y+.09, f"{row['rejection_kw']:.0f} kW 排热", .32, "start")
      _text(group, 13.1, y-1.32, f"{row['cold_c']:.1f} °C  ·  算得扬程 {h['required_head_m']:.1f} m", .30)
    else:
      _text(group, 25.45, y+.09, "备用", .32, "start")
  _text(svg, 1, .51, f"按正常工况注记   {len(study['cases'])} 项稳态工况见冷却水计算表", .34, "start")
  return svg


def build_cw_view(context):
  model = staged_json(context, MODEL)
  view = context.scope["view"]
  if view == "plan":
    svg = _plan(context, model)
  elif view == "section":
    svg = _section(context, model, context.scope["plant"])
  elif view == "diagram":
    svg = _diagram(model, staged_json(context, STUDY))
  else:
    raise ValueError(f"Unknown cooling-water view {view}.")
  output = context.build_path / context.output
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(svg)
  output.write_text(tostring(svg, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult("pass", (artifact(context, f"cw-{view}-view", output),))
