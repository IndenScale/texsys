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
.valve{fill:white;stroke:#253845;stroke-width:.08}
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


def _overlay(context, model, view):
  site = staged_json(context, f"{MODELS}/site.json")
  layout = staged_json(context, f"{MODELS}/layout.json")
  levels = staged_json(context, f"{MODELS}/levels.json")
  grid = staged_json(context, f"{MODELS}/grid.json")
  room = _extent(levels["zones"][model["terminal_zone"]])
  isolated = view == "isolation"
  roof = view == "roof"
  points = [point for pipe in model["segments"].values() for point in pipe["points_m"]]
  xs, ys, _ = zip(*points, strict=True)
  bounds = (min(xs), min(ys), max(xs), max(ys)+3) if isolated else (room if roof else site["boundary_m"])
  title = {"roof": "屋面设施水预留", "site": "设施水总平面", "isolation": "设施水环路分段隔离"}[view]
  svg = _calibrated(bounds, title, padding=(6, 8, 5, 5))
  svg.set("data-frame", site["frame"])
  if view == "site":
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
      if not isolated or record.get("valves"):
        line = _pipe_line(svg, key, record)
        if record["kind"] == "bypass":
          line.set("data-bypass", record["leg"])
    if isolated:
      _isolation_tags(svg, model)
    else:
      for leg, root in model["roots"].items():
        x, y, z = model["nodes"][root]
        _text(svg, x+.25, y - .44 if leg == "supply" else y + .54, f"{leg[0].upper()} +{z:.2f} m", .67, "start")
  return svg


def _isolation_tags(svg, model):
  for identity, valve in model["valves"].items():
    x, y, _ = valve["point_m"]
    marker = _add(svg, "path", d=f"M{x-.22:g},{-y:g} l.22,-.22 .22,.22 -.22,.22 z", data_valve=identity,
                  **{"class": "valve"})
    SubElement(marker, "title").text = f"{identity} · 常开"
  for key, pipe in model["segments"].items():
    if not pipe.get("valves"):
      continue
    a, b = pipe["points_m"]
    x, y = (a[0]+b[0])/2, (a[1]+b[1])/2
    sign = -1 if pipe["leg"] == "supply" else 1
    if a[0] == b[0]:
      x += sign*.42
      _text(svg, x, y+.12, key, .50, "end" if sign < 0 else "start")
    else:
      side = -sign if pipe["kind"] == "bypass" and key == model["bypass"][pipe["leg"]][-2] else sign
      _text(svg, x, y + (.44 if side > 0 else -.76), key, .50)


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
  SubElement(svg, "title").text = "设施水供回水环路系统图"
  SubElement(svg, "style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#203441}
.s{stroke:#155478;fill:none;stroke-width:1}
.r{stroke:#913c2b;fill:none;stroke-width:1;stroke-dasharray:2.7 1.5}
.equipment{stroke:#34444f;fill:#f4f7f8;stroke-width:.75}
.dot-s{fill:#155478}.dot-r{fill:#913c2b}
"""
  _label(svg, 154.5, 12, "设施水供回水系统", 6.8)

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
    equipment(name, x, 31, 68, 15)
    pipe(records["supply"], x-6, 38.5, 53, "supply", from_equipment=True)
    pipe(records["return"], x+6, 38.5, 62, "return", from_equipment=True)
    for station, height, cls in ((x-6, 53, "s"), (x+6, 62, "r")):
      _add(svg, "circle", cx=station, cy=height, r=1.25, **{"class": "dot-" + cls})

  sorted_rows = sorted(rows.values(), key=min, reverse=True)
  bottom = 86+28*(len(sorted_rows)-1)
  for leg, cls, west, east, top, offset in (("supply", "s", 10, 299, 53, -8), ("return", "r", 21, 289, 62, 8)):
    end = bottom+offset
    closure = end+19
    _add(svg, "path", d=f"M{west},{end} V{top} H{east}", **{"class": cls})
    if model["bypass"][leg]:
      _add(svg, "path", d=f"M{east},{top} V{closure} H{west} V{end}",
           data_bypass=leg, **{"class": cls})
  for row, names in enumerate(sorted_rows):
    y = 86 + 28*row
    for x, offset, tail, cls in ((10, -8, -6, "s"), (21, 8, 6, "r")):
      _add(svg, "line", x1=x, y1=y+offset, x2=57+65*(len(names)-1)+tail, y2=y+offset, **{"class": cls})
      _add(svg, "circle", cx=x, cy=y+offset, r=1.2, **{"class": "dot-" + cls})
    for col, name in enumerate(sorted(names)):
      records = model["ports"]["terminals"][name]
      x = 57 + 65*col
      equipment(name, x, y, 37, 9)
      for leg, dx, station, edge, cls in (("supply", -6, -8, -4.5, "s"), ("return", 6, 8, 4.5, "r")):
        _add(svg, "line", x1=x+dx, y1=y+station, x2=x+dx, y2=y+edge, data_sdl_link=records[leg]["id"],
             **{"class": cls})
        _add(svg, "circle", cx=x+dx, cy=y+station, r=1.12, **{"class": "dot-" + cls})
  _label(svg, 154.5, 210, "蓝实线为供水，红虚线为回水。共有干管阀位见分段隔离图。", 4.4)
  return svg


def build_network_view(context):
  model = staged_json(context, MODEL)
  scope = dict(context.scope)
  view = scope.get("view")
  if set(scope) == {"view"} and view in {"site", "roof", "isolation"}:
    svg = _overlay(context, model, view)
  elif scope == {"view": "schematic"}:
    svg = _schematic(model)
  elif view == "section" and set(scope) == {"view", "terminal"}:
    svg = _section(context, model, scope["terminal"])
  else:
    raise ValueError("Select the FWS site, roof, isolation plan, schematic or a named terminal section.")
  output = context.build_path / context.output
  if output.suffix != ".svg":
    raise ValueError("Facility water views require a named SVG output.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(svg)
  output.write_text(tostring(svg, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "fws-network-view", output),))
