"""Issue the parcel utility plan, drainage profile and water-balance diagram."""

from itertools import pairwise
from xml.etree.ElementTree import SubElement, indent, tostring

from texsys.targets import TargetResult

from .fws_buffer_views import _canvas
from .plans import _add, _line, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json
from .wet_utilities import MODEL
from .wet_utility_study import MODEL as STUDY


_STYLE = """
.city,.makeup,.drain{fill:none;stroke-width:.11;stroke-linejoin:round}
.city{stroke:#17618a}
.makeup{stroke:#24787a;stroke-dasharray:.58 .2}
.drain{stroke:#865425;stroke-dasharray:.85 .22 .14 .22}
.datum{fill:none;stroke:#384752;stroke-width:.085}
.chamber{fill:#fff;stroke:#865425;stroke-width:.10}
.cut{fill:#e3ecef;stroke:#4b6b76;stroke-width:.085}
.legend{fill:#495863}
.crossing{fill:#fff;stroke:#314856;stroke-width:.075}
"""


def _base(bounds, title):
  root = _canvas(bounds, title)
  SubElement(root, "style").text = _STYLE
  return root


def _plan(context, model):
  layout = staged_json(context, f"{MODELS}/layout.json")["objects"]
  site = staged_json(context, f"{MODELS}/site.json")
  points = [point for row in model["runs"].values() for point in row["points_m"]]
  xmin, ymin = min(p[0] for p in points)-3.4, min(layout[t]["minimum_m"][1] for t in model["towers"])-3
  xmax, ymax = max(p[0] for p in points)+5.2, max(p[1] for p in points)+3.8
  svg = _base((xmin, ymin, xmax, ymax), "场区冷却塔补水及排污管网平面")
  _, _, east, north = model["parcel_m"]
  _line(svg, xmin+.5, north, east, north, "boundary")
  _line(svg, east, north, east, ymin+.7, "boundary")
  _, _, east_setback, north_setback = site["buildable_m"]
  _line(svg, xmin+.5, north_setback, east_setback, north_setback, "setback")
  _line(svg, east_setback, north_setback, east_setback, ymin+.7, "setback")
  _text(svg, east-7, north+.6, "宗地北界", .59)
  _text(svg, east_setback-1.55, north_setback+.52, "退界", .53, "end")

  for name in model["towers"]:
    x0, y0, x1, y1 = _xy(layout[name])
    _rect(svg, (x0, y0, x1, y1), "equipment", data_instance=name)
    _text(svg, (x0+x1)/2, y0+.72, name, .55)
  tank = model["tank"]
  x0, y0, x1, y1 = _xy(tank)
  _rect(svg, (x0, y0, x1, y1), "cut", data_structure=tank["id"])
  _text(svg, x1+.62, (y0+y1)/2, "TK-01 断流水箱／补水泵", .64, "start")
  for key, row in model["runs"].items():
    group = _add(svg, "g", data_run=key)
    service = row["service"]
    for a, b in pairwise(row["points_m"]):
      if a[:2] != b[:2]:
        _line(group, *a[:2], *b[:2], service)
    if key.startswith("DR-M"):
      a, b = row["points_m"]
      _text(group, (a[0]+b[0])/2, a[1]+.54, key, .52)
  for name, row in model["receivers"].items():
    x0, y0, x1, y1 = _xy(row)
    _rect(svg, (x0, y0, x1, y1), "chamber", data_receptor=name)
  city = model["runs"]["MU-CITY"]["points_m"][0]
  _add(svg, "circle", cx=city[0], cy=-city[1], r=.25, **{"class": "crossing"}, data_boundary="city")
  _text(svg, city[0]+1, city[1]+1.05, f"市政来水交接  X {city[0]:g} / Y {city[1]:g}", .61, "start")
  x, y, _ = model["outfall"]["point_m"]
  for name, row in model["junctions"].items():
    _rect(svg, _xy(row), "chamber", data_chamber=name)
  _text(svg, x-1.35, y+.71, "MH-EAST 排污交接", .62, "end")
  _text(svg, xmax-2.05, ymax-1.26, "N", .67)
  _line(svg, xmax-2.05, ymax-3.36, xmax-2.05, ymax-1.91, "datum")
  _add(svg, "path", d=f"M{xmax-2.05:g},{-ymax+1.88:g} l-.47,1.1 .47,-.22 .47,.22 z", fill="#344956")
  for i, (key, label) in enumerate((("city", "市政来水"), ("makeup", "加压补水"), ("drain", "工艺排污"))):
    x = xmin+3+i*10.5
    _line(svg, x, ymin+1.10, x+1.4, ymin+1.10, key)
    _text(svg, x+1.83, ymin+.86, label, .58, "start")
  return svg


def _profile(model):
  rows = [row for key, row in model["runs"].items() if key.startswith("DR-M")]
  first, last = rows[0]["points_m"][0][0], rows[-1]["points_m"][-1][0]
  zmin = model["outfall"]["receiving_invert_m"]
  zscale = 10
  svg = _base((first-5, (zmin-.47)*zscale, last+5.5, .84*zscale), "工艺排污总管纵断面")
  _line(svg, first-2, 0, last+1.7, 0, "datum")
  _text(svg, first+.75, 1.02, "场坪  ±0.000", .61, "start")
  for key, row in model["runs"].items():
    if not key.startswith("DR-M"):
      continue
    first_point, last_point = row["points_m"]
    x0, x1 = first_point[0], last_point[0]
    a, b = row["invert_m"]
    outer = (row["pipe"]["inside_m"]+row["pipe"]["outside_m"])/2
    group = _add(svg, "g", data_run=key)
    _line(group, x0, a*zscale, x1, b*zscale, "drain")
    _line(group, x0, (a+outer)*zscale, x1, (b+outer)*zscale, "drain")
    _line(group, x0, -1.05, x0, a*zscale-.3, "setback")
    _text(group, x0+.43, a*zscale-1.21, f"{a:+.3f}", .60, "start")
    _text(group, (x0+x1)/2, ((a+b)/2+outer)*zscale+1.33,
          f"{row['pipe']['size']}   i={100*row['slope']:.2f}%   L={x1-x0:.1f}m", .53)
    tower = row["from_node"].removeprefix("DR-J-")
    _text(group, x0, -2.39, tower, .53)
  _, _, at = model["outfall"]["point_m"]
  _line(svg, last, .65, last, zmin*zscale, "datum")
  _text(svg, last+.28, at*zscale-.10, f"{at:+.3f}", .59, "start")
  difference = at-zmin
  _text(svg, last+.38, zmin*zscale-1.22, f"{zmin:+.3f}", .58, "start")
  _text(svg, last-1.32, zmin*zscale-1.39, f"可用高差 {difference*1000:.0f} mm", .54, "end")
  _text(svg, last, 1.17, "MH-EAST", .59)
  _text(svg, first-.5, 5.6, "工艺排污总管  W—E", .83, "start")
  _text(svg, first-.5, 3.68, "水平 1:250  垂直 1:25  标高单位 m", .61, "start")
  return svg


def _diagram(model, study):
  row = study["cases"]["normal"]
  count = len(model["towers"])
  svg = _base((0, 0, 29, 17.9), "补水、储备及排污系统")
  nodes = (
    (1.2, 12.6, 5.4, 14.9, "市政给水", "city"),
    (7.8, 12.6, 12.9, 14.9, "TK-01 断流水箱", "tank"),
    (15., 12.6, 19.8, 14.9, "加压补水", "booster"),
    (22.4, 12.6, 27.75, 14.9, f"闭式塔喷淋 × {count}", "towers"),
    (22.4, 6.9, 27.75, 9.2, "自由出流接水器", "receptors"),
    (13.65, 6.9, 19.8, 9.2, "MH-EAST 交接", "outfall"),
  )
  for a, b, c, d, text, key in nodes:
    _rect(svg, (a, b, c, d), "cut", data_node=key)
    _text(svg, (a+c)/2, (b+d)/2-.08, text, .45)
  for x0, x1, service in ((5.4, 7.8, "city"), (12.9, 15, "makeup"), (19.8, 22.4, "makeup")):
    _line(svg, x0, 13.72, x1, 13.72, service)
    _add(svg, "path", d=f"M{x1-.56:g},-13.94 L{x1-.18:g},-13.72 {x1-.56:g},-13.50 z", fill="#386778")
  _line(svg, 25.0, 12.6, 25.0, 10.45, "drain")
  _line(svg, 25.0, 10.05, 25.0, 9.2, "drain")
  _line(svg, 22.4, 8.05, 19.8, 8.05, "drain")
  _add(svg, "path", d="M20.28,-8.24 L19.92,-8.05 20.28,-7.86 z", fill="#865425")
  _text(svg, 20.94, 14.22, f"{row['makeup_m3_h']:.2f} m³/h", .41)
  _text(svg, 23.49, 11.16, f"排污 {row['blowdown_m3_h']:.2f} m³/h", .39, "end")
  _text(svg, 1.25, 10.83, f"来水 {study['parameters']['city_tds_mg_l']:.0f} mg/L TDS", .45, "start")
  _text(svg, 1.25, 9.66, f"有效储量 {model['tank']['usable_volume_m3']:.2f} m³", .45, "start")
  _text(svg, 1.25, 8.51, f"停水 {study['parameters']['required_city_outage_hours']:.2f} h 后余 "
        f"{row['outage_end_remaining_m3']:.2f} m³", .45, "start")
  recharge = row["refill_hours"]
  recharge_label = f"{recharge:.2f} h" if recharge is not None else "无法恢复"
  _text(svg, 1.25, 7.35, f"恢复水位 {recharge_label}", .45, "start")
  _text(svg, 1.25, 4.4, f"正常工况注记  ·  计算复核 {len(study['cases'])} 个冷却水工况", .56, "start")
  _text(svg, 1.25, 3.25, "喷淋侧排污通过接水器与工艺排水系统间接连接。", .44, "start")
  return svg


def build_wet_view(context):
  model = staged_json(context, MODEL)
  view = context.scope["view"]
  if view == "plan":
    svg = _plan(context, model)
  elif view == "profile":
    svg = _profile(model)
  elif view == "diagram":
    svg = _diagram(model, staged_json(context, STUDY))
  else:
    raise ValueError(f"Unknown utility view: {view}.")
  output = context.build_path/context.output
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(svg)
  output.write_text(tostring(svg, encoding="unicode")+"\n", encoding="utf-8")
  return TargetResult("pass", (artifact(context, f"wet-utility-{view}-view", output),))
