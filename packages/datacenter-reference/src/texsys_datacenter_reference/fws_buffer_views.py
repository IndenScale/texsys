"""Project full-scale buffer and tie-in geometry into cold plant sheets."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .fws_network import MODEL
from .plans import _STYLE, _add, _line, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json


_BUFFER_STYLE = """
.storage{fill:#e8f0f5;stroke:#315c75;stroke-width:.07}
.equipment{fill:#e9edf0;stroke:#4a5964;stroke-width:.074}
.clearance{fill:#e8f0f4;fill-opacity:.5;stroke:#66848c;stroke-width:.06;stroke-dasharray:.14 .10}
.foot{fill:#c7cdd2;stroke:#465763;stroke-width:.06}
.branch-s{fill:none;stroke:#1d6283;stroke-width:.105}
.branch-r{fill:none;stroke:#a04636;stroke-width:.081;stroke-dasharray:.19 .12}
.water{fill:none;stroke:#4c7e97;stroke-width:.054;stroke-dasharray:.11 .08}
.level{fill:none;stroke:#344956;stroke-width:.048}
"""


def _canvas(bounds, title):
  x0, y0, x1, y1 = bounds
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "data-coordinate-unit": "m", "data-units-per-meter": "1",
                         "viewBox": f"{x0:g} {-y1:g} {x1-x0:g} {y1-y0:g}"})
  SubElement(root, "title").text = title
  SubElement(root, "style").text = _STYLE + _BUFFER_STYLE
  return root


def _role(row):
  return "branch-s" if row["leg"] == "supply" else "branch-r"


def _plan(context, network):
  layout = staged_json(context, f"{MODELS}/layout.json")
  grid = staged_json(context, f"{MODELS}/grid.json")
  buffers = network["buffers"]
  assets = {row["plant"]: layout["objects"][row["plant"]] for row in buffers.values()}
  xmin = min(row["minimum_m"][0] for row in assets.values())
  xmax = max(row["maximum_m"][0] for row in assets.values())
  south = min(network["segments"][row["lead_out"]]["points_m"][-1][1] for row in buffers.values())
  north = max(row["maximum_m"][1] for row in assets.values())
  a, b, c, d = xmin-1.95, south-2.50, xmax+2.05, north+2.05
  root = _canvas((a, b, c, d), "冷源区设施水缓冲及接管")
  for key, x in grid["x"].items():
    if xmin <= x <= xmax:
      _line(root, x, south-1.7, x, north+1.2, "grid").set("data-axis", key)
      _add(root, "circle", cx=x, cy=-(south-1.7), r=.29, **{"class": "axis"})
      _text(root, x, south-1.8, key, .31)
  for key, y in grid["y"].items():
    if south <= y <= north:
      _line(root, xmin-1.1, y, xmax+1, y, "grid").set("data-axis", key)
      _add(root, "circle", cx=xmin-1.1, cy=-y, r=.29, **{"class": "axis"})
      _text(root, xmin-1.1, y-.10, key, .31)
  for name, row in assets.items():
    x0, y0, x1, y1 = _xy(row)
    _rect(root, (x0, y0, x1, y1), "equipment", data_plant=name)
    _text(root, (x0+x1)/2, (y0+y1)/2, name, .39)
  for segment, row in network["segments"].items():
    if row["kind"] != "plant":
      continue
    first, last = row["points_m"]
    if first[:2] != last[:2]:
      _line(root, max(a+.4, first[0]), first[1], max(a+.4, last[0]), last[1], _role(row)).set("data-pipe", segment)
  for key, tank in buffers.items():
    x, y, _ = tank["center_m"]
    _rect(root, _xy(tank["foundation"]), "foot", data_pad=key)
    _rect(root, _xy(tank["service_clearance"]), "clearance", data_service=key)
    _add(root, "circle", cx=x, cy=-y, r=tank["outside_diameter_m"]/2, data_buffer=key, **{"class": "storage"})
    _text(root, x, south-.66, key, .34)
    _text(root, x, south-1.06, f"{tank['working_volume_m3']*1000:.1f} L", .30)
  _text(root, xmax+1.0, north+1.29, "N", .42)
  _add(root, "path", d=f"M{xmax+1:g},{-north-1.08:g} l-.31,.68 .31,-.12 .31,.12 z", fill="#18232a")
  _text(root, (xmin+xmax)/2, north+1.17, "冷源方舱出水 → 串联缓冲 → 设施水总管", .47)
  return root


def _section(context, network, identifier):
  tank = network["buffers"][identifier]
  entering, leaving = (network["segments"][tank[key]] for key in ("lead_in", "lead_out"))
  connection = tank["points_m"][0][2]
  _, south, base = tank["foundation"]["minimum_m"]
  port_y = entering["points_m"][0][1]
  layout = staged_json(context, f"{MODELS}/layout.json")
  plant = layout["objects"][tank["plant"]]
  _, plant_y, plant_bottom = plant["minimum_m"]
  _, plant_end, plant_top = plant["maximum_m"]
  elbow_y = leaving["points_m"][1][1]
  rise, = [row for row in network["segments"].values() if row["leg"] == "supply" and row["from"] == leaving["to"]]
  pipe_z = rise["points_m"][1][2]
  root = _canvas((elbow_y-1.65, base-1.15, plant_end+1., pipe_z+1.00), f"{identifier} 供水接管剖面")
  SubElement(root, "style").text = ".branch-s{stroke-width:.043}.storage,.equipment,.foot{stroke-width:.029}.level{stroke-width:.023}"
  _line(root, elbow_y-.84, base, plant_end+.35, base, "level")
  _rect(root, (plant_y, plant_bottom, plant_end, plant_top), "equipment", data_plant=tank["plant"])
  _text(root, (plant_y+plant_end)/2, (plant_top+plant_bottom)/2+.55, tank["plant"], .17)
  for key in ("foundation", "pedestal", "body"):
    _, y0, z0 = tank[key]["minimum_m"]
    _, y1, z1 = tank[key]["maximum_m"]
    _rect(root, (y0, z0, y1, z1), "storage" if key == "body" else "foot", data_buffer_part=key)
  for record in (entering, leaving, rise):
    first, last = record["points_m"]
    _line(root, first[1], first[2], last[1], last[2], "branch-s")
  _, y0, _ = tank["body"]["minimum_m"]
  _, y1, _ = tank["body"]["maximum_m"]
  _line(root, y0, tank["waterline_m"], y1, tank["waterline_m"], "water")
  _text(root, (y0+y1)/2, tank["waterline_m"]+.52, f"{identifier} · {1000*tank['working_volume_m3']:.1f} L", .18)
  _text(root, elbow_y-.12, pipe_z+.23, f"供水总管 +{pipe_z:.2f}", .17, "start")
  _text(root, port_y+.12, connection+.05, f"管口 +{connection:.2f}", .16, "start")
  _text(root, elbow_y-.16, tank["waterline_m"]+.21, f"液位 +{tank['waterline_m']:.3f}", .16, "end")
  _line(root, elbow_y+.14, tank["waterline_m"], y0-.05, tank["waterline_m"], "water")
  _text(root, south, base-.36, "冷源区场坪", .16, "start")
  return root


def build_buffer_view(context):
  network = staged_json(context, MODEL)
  if not network.get("buffers"):
    raise ValueError("The cold plant views require routed storage geometry.")
  scope = dict(context.scope)
  if scope == {"view": "plan"}:
    root = _plan(context, network)
  elif scope.keys() == {"view", "buffer"} and scope["view"] == "section":
    root = _section(context, network, scope["buffer"])
  else:
    raise ValueError("Choose the buffer plant plan or a named connection section.")
  target = context.build_path / context.output
  if target.suffix != ".svg":
    raise ValueError("Buffer drawings require an SVG view destination.")
  target.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  target.write_text(tostring(root, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "fws-buffer-view", target),))
