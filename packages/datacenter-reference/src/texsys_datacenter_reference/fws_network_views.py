"""Render installation and schematic drawings from the routed FWS model."""

from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .fws_network import LEGS, MODEL
from .plans import _STYLE, _add, _axes, _line, _north_and_scale, _rect, _text
from .spatial_design import MODELS, artifact, staged_json


_WATER_STYLE = """
.fws-supply{fill:none;stroke:#0a577c;stroke-width:.11}
.fws-return{fill:none;stroke:#8a3e2d;stroke-width:.11;stroke-dasharray:.38 .21}
.sleeve-supply{fill:white;stroke:#0a577c;stroke-width:.065}
.sleeve-return{fill:white;stroke:#8a3e2d;stroke-width:.065}
.outline{fill:#e9eef1;stroke:#334855;stroke-width:.09}
.datum{fill:none;stroke:#465864;stroke-width:.065;stroke-dasharray:.4 .2}
"""


def _calibrated(bounds, title, *, padding):
  xmin, ymin, xmax, ymax = bounds
  left, bottom, right, top = padding
  svg = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "data-coordinate-unit": "m",
                        "data-units-per-meter": "1",
                        "viewBox": f"{xmin-left:g} {-ymax-top:g} {xmax-xmin+left+right:g} {ymax-ymin+top+bottom:g}"})
  SubElement(svg, "title").text = title
  SubElement(svg, "style").text = _STYLE + _WATER_STYLE
  return svg


def _pipe_line(root, key, record, *, vertical=1):
  a, b = record["points_m"]
  line = _line(root, a[0], a[vertical], b[0], b[vertical], "fws-" + record["leg"])
  line.set("data-pipe", key)
  SubElement(line, "title").text = key
  return line


def _extent(row):
  x, y, _ = row["minimum_m"]
  xmax, ymax, _ = row["maximum_m"]
  return x, y, xmax, ymax


def _overlay(context, model, *, roof):
  site = staged_json(context, f"{MODELS}/site.json")
  layout = staged_json(context, f"{MODELS}/layout.json")
  levels = staged_json(context, f"{MODELS}/levels.json")
  grid = staged_json(context, f"{MODELS}/grid.json")
  room = _extent(levels["zones"][model["terminal_zone"]])
  bounds = room if roof else site["boundary_m"]
  title = "屋面设施水预留" if roof else "设施水总平面"
  svg = _calibrated(bounds, title, padding=(6, 8, 5, 5))
  svg.set("data-frame", site["frame"])
  if not roof:
    _rect(svg, site["boundary_m"], "boundary")
    _rect(svg, site["buildable_m"], "setback")
  _rect(svg, room, "zone")
  _axes(svg, grid, bounds)
  _north_and_scale(svg, bounds)
  if roof:
    for name in model["ports"]["terminals"]:
      x, y, xmax, ymax = _extent(layout["objects"][name])
      _rect(svg, (x, y, xmax, ymax), "outline", data_instance=name)
      _text(svg, (x+xmax)/2, (y+ymax)/2, name, .52)
    for name, record in model["penetrations"].items():
      x, y, _ = record["point_m"]
      leg = model["segments"][record["segment"]]["leg"]
      half = record["square_width_m"]/2
      _rect(svg, (x-half, y-half, x+half, y+half), "sleeve-" + leg, data_reservation=name)
      _text(svg, x-.09, y-.25 if leg == "supply" else y+.27, name.split("-")[0][2:] + leg[0].upper(), .30)
  else:
    for name in sorted(model["ports"]["plants"] | model["ports"]["terminals"]):
      x, y, xmax, ymax = _extent(layout["objects"][name])
      _rect(svg, (x, y, xmax, ymax), "outline", data_instance=name)
      _text(svg, (x+xmax)/2, (y+ymax)/2, name, .60)
    for key, record in model["segments"].items():
      _pipe_line(svg, key, record)
    for leg, root in model["roots"].items():
      x, y, z = model["nodes"][root]
      _text(svg, x+.25, y - .44 if leg == "supply" else y + .54, f"{leg[0].upper()} +{z:.2f} m", .67, "start")
  return svg


def _section(context, model, terminal):
  layout = staged_json(context, f"{MODELS}/layout.json")
  plant = layout["objects"][terminal]
  left, right = plant["minimum_m"][0], plant["maximum_m"][0]
  bottom, top = plant["minimum_m"][2], plant["maximum_m"][2]
  paths = model["paths"]["terminals"][terminal]
  selected = {key: model["segments"][key] for route in paths.values() for key in route[-3:]}
  roof = next(record["point_m"][2] for identity, record in model["penetrations"].items()
              if identity.startswith(terminal + "-"))
  pipe_top = max(point[2] for row in selected.values() for point in row["points_m"])
  bounds = left, bottom, right, pipe_top
  svg = _calibrated(bounds, f"{terminal} 设施水接驳正视投影", padding=(2, 1.35, 2.25, 1.2))
  SubElement(svg, "style").text = """
.fws-supply,.fws-return{stroke-width:.027}
.outline,.datum,.dimension,.sleeve-supply,.sleeve-return{stroke-width:.02}
.datum,.fws-return{stroke-dasharray:.10 .05}
"""
  _rect(svg, (left, bottom, right, top), "outline", data_instance=terminal)
  _text(svg, (left+right)/2, (top+bottom)/2, terminal, .24)
  for label, height in (("方舱落位", bottom), ("屋面控制", roof)):
    _line(svg, left-.2, height, right+.35, height, "datum")
    _text(svg, right+.42, height+.03, f"{label} {height:+.3f}", .155, "start")
  for key, record in selected.items():
    a, b = record["points_m"]
    # Only draw real x-z projections. End-on pipes appear as points at their ports.
    if a[0] != b[0] or a[2] != b[2]:
      clipped = dict(record, points_m=[(max(left-.6, x), y, z) for x, y, z in (a, b)])
      _pipe_line(svg, key, clipped, vertical=2)
  for leg in LEGS:
    opening = model["penetrations"][f"{terminal}-{leg}-ROOF"]
    x, _, z = opening["point_m"]
    h = opening["square_width_m"]/2
    _rect(svg, (x-h, z-.09, x+h, z+.09), "sleeve-" + leg, data_pipe=opening["segment"])
    _text(svg, x, z+.27 if leg == "supply" else z+.64,
          f"{leg[0].upper()} {round(2*h*1000)}", .155)
  y = bottom-.67
  _line(svg, left, y, right, y, "dimension")
  for x in (left, right):
    _line(svg, x-.05, y-.05, x+.05, y+.05, "dimension")
  _text(svg, (left+right)/2, y+.09, f"{(right-left)*1000:.0f} mm", .17)
  return svg


def _label(root, x, y, label, size=4):
  _add(root, "text", x=x, y=y, font_size=size, text_anchor="middle").text = label


def _schematic(model):
  svg = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 309 215"})
  SubElement(svg, "title").text = "设施水共有总管系统图"
  SubElement(svg, "style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#203441}
.s{stroke:#155478;fill:none;stroke-width:1}
.r{stroke:#913c2b;fill:none;stroke-width:1;stroke-dasharray:2.7 1.5}
.equipment{stroke:#34444f;fill:#f4f7f8;stroke-width:.75}
"""
  _label(svg, 154.5, 12, "三冷源共用设施水总管系统", 6.8)

  def equipment(name, x, y, w, height):
    _add(svg, "rect", x=x-w/2, y=y-height/2, width=w, height=height,
         data_instance=name, **{"class": "equipment"})
    _label(svg, x, y+1.4, name)

  def pipe(edge, x, lo, hi, leg, *, from_equipment):
    supply = leg == "supply"
    towards_bottom = supply == from_equipment
    start, end = (lo, hi) if towards_bottom else (hi, lo)
    cls = "s" if supply else "r"
    _add(svg, "line", x1=x, y1=start, x2=x, y2=end, data_sdl_link=edge["id"], **{"class": cls})
    middle = (lo + hi)/2
    sign = 1 if towards_bottom else -1
    _add(svg, "polyline", points=f"{x-2:g},{middle-sign*2:g} {x:g},{middle:g} {x+2:g},{middle-sign*2:g}",
         **{"class": cls})

  plants = list(model["ports"]["plants"].items())
  rows = defaultdict(list)
  for name, paths in model["paths"]["terminals"].items():
    roots = tuple(next(key for key in paths[leg] if model["segments"][key]["type"] == "row") for leg in LEGS)
    rows[roots].append(name)
  if len(plants) > 3 or len(rows) > 4 or any(len(members) > 4 for members in rows.values()):
    raise ValueError("This A3 schematic may show up to 3 plants and four rows of four terminals.")
  for i, (name, records) in enumerate(plants):
    x = 61 + i*92
    equipment(name, x, 32, 68, 15)
    pipe(records["supply"], x-6, 39.5, 58, "supply", from_equipment=True)
    pipe(records["return"], x+6, 39.5, 67, "return", from_equipment=True)
  _add(svg, "line", x1=9, y1=58, x2=300, y2=58, **{"class": "s"})
  _add(svg, "line", x1=9, y1=67, x2=300, y2=67, **{"class": "r"})
  for col, names in enumerate(sorted(rows.values(), key=min)):
    x = 42.5 + 75*col
    for offset, cls in ((-27, "s"), (27, "r")):
      _add(svg, "line", x1=x+offset, y1=58 if cls == "s" else 67,
           x2=x+offset, y2=104+22*(len(names)-1), **{"class": cls})
    for row, name in enumerate(sorted(names)):
      records = model["ports"]["terminals"][name]
      y = 104 + 22*row
      equipment(name, x, y, 35, 11)
      for leg, start, stop in (("supply", x-27, x-17.5), ("return", x+17.5, x+27)):
        _add(svg, "line", x1=start, y1=y, x2=stop, y2=y, data_sdl_link=records[leg]["id"],
             **{"class": "s" if leg == "supply" else "r"})
  _label(svg, 154.5, 187, "蓝实线为供水，红虚线为回水", 4.6)
  _label(svg, 154.5, 197, "公用管段及接口编号详见管段表", 4.3)
  return svg


def build_network_view(context):
  model = staged_json(context, MODEL)
  scope = dict(context.scope)
  view = scope.get("view")
  if set(scope) == {"view"} and view in ("site", "roof"):
    svg = _overlay(context, model, roof=view == "roof")
  elif scope == {"view": "schematic"}:
    svg = _schematic(model)
  elif view == "section" and set(scope) == {"view", "terminal"}:
    svg = _section(context, model, scope["terminal"])
  else:
    raise ValueError("Select the FWS site, roof, schematic or a named terminal section.")
  output = context.build_path / context.output
  if output.suffix != ".svg":
    raise ValueError("Facility water views require a named SVG output.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(svg)
  output.write_text(tostring(svg, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "fws-network-view", output),))
