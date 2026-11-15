"""Render the physical telecom routes and logical terminations."""

from itertools import pairwise
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .architecture import MODEL as ARCHITECTURE
from .plans import _STYLE, _add, _axes, _dimension, _line, _north_and_scale, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json
from .telecom import MODEL, PLANES
from .telecom_coordination import COORDINATION


_COLORS = {"A": "#64488e", "B": "#267257"}


def _plan(context, model, plane):
  architecture = staged_json(context, ARCHITECTURE)
  layout = staged_json(context, f"{MODELS}/layout.json")
  grid = staged_json(context, f"{MODELS}/grid.json")
  coordination = staged_json(context, COORDINATION)
  x0, y0, x1, y1 = _xy(architecture["hall"])
  cabinets = [_xy(row["cabinet"]) for row in model["planes"].values()]
  low, high = min(x0, *[row[0] for row in cabinets]), max(x1, *[row[2] for row in cabinets])
  svg = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "data-coordinate-unit": "m", "data-units-per-meter": "1",
                        "data-plane": plane, "data-frame": layout["frame"],
                        "viewBox": f"{low-6:g} {-y1-5:g} {high-low+11:g} {y1-y0+13:g}"})
  SubElement(svg, "title").text = f"主机房弱电桥架 {plane} 路"
  color = _COLORS[plane]
  SubElement(svg, "style").text = _STYLE + f"""
.duct{{fill:none;stroke:{color};stroke-width:.16}}
.port{{fill:white;stroke:{color};stroke-width:.095}}
.wall{{fill:#34434c}}
.cabinet{{fill:#e6eef2;stroke:{color};stroke-width:.10}}
.pod{{fill:#e9eef1;stroke:#6a7983;stroke-width:.066}}
.opening{{fill:white;stroke:#a34b36;stroke-width:.082}}
.leader{{fill:none;stroke:#a34b36;stroke-width:.042}}
"""
  _rect(svg, (x0, y0, x1, y1), "zone")
  _axes(svg, grid, (x0, y0, x1, y1), dimensions=False)
  for a, b in pairwise(sorted(x for x in grid["x"].values() if x0 <= x <= x1)):
    _dimension(svg, a, b, y0-2, vertical=False)
  for a, b in pairwise(sorted(y for y in grid["y"].values() if y0 <= y <= y1)):
    _dimension(svg, a, b, low-4.0, vertical=True)
  _north_and_scale(svg, (x0, y0, x1, y1))
  for key, record in architecture["walls"].items():
    if not key.startswith("W-over-"):
      _rect(svg, _xy(record), "wall", data_wall=key)
  assembly = model["planes"][plane]
  for row in assembly["cables"].values():
    name = row["pod"]
    a, b, c, d = _xy(layout["objects"][name])
    _rect(svg, (a, b, c, d), "pod", data_instance=name)
    _text(svg, (a+c)/2, b+.84 if plane == "B" else d-.72, name, .50)
  cabinet = assembly["cabinet"]
  a, b, c, d = _xy(cabinet)
  _rect(svg, (a, b, c, d), "cabinet", data_cabinet=cabinet["id"])
  _text(svg, (a+c)/2, b-.40, cabinet["id"], .56)
  for key, record in assembly["runs"].items():
    if record["axis"] != 2:
      start, end = record["points_m"]
      node = _line(svg, start[0], start[1], end[0], end[1], "duct")
      node.set("data-tray", key)
      SubElement(node, "title").text = f"{key} · {len(record['cables'])} 条缆"
  for key, record in assembly["cables"].items():
    x, y, _ = record["panel_point_m"]
    node = _add(svg, "circle", cx=x, cy=-y, r=.145, data_cable=key, **{"class": "port"})
    SubElement(node, "title").text = record["panel"]
  for index, (key, row) in enumerate(((key, row) for key, row in coordination["wall_openings"].items() if row["plane"] == plane), 1):
    x, y, z = row["center_m"]
    dx = -.95 if plane == "A" else .95
    dy = -1.12 if plane == "B" and row["wall"] != "W-DIVIDE" else 1.36
    label_x, label_y = x + dx, y+dy
    _rect(svg, (x-.18, y-.18, x+.18, y+.18), "opening", data_opening=key)
    _line(svg, x, y, label_x, label_y-.21, "leader")
    width, height = [round(value*1000) for value in row["opening_size_m"]]
    _text(svg, label_x, label_y, f"{plane}{index}  {width}×{height} / +{z:.2f}", .44, "end" if plane == "A" else "start")
  _text(svg, (x0+x1)/2, y1+.92, f"{plane} 路桥架中心标高 +{assembly['tier_m']:.2f} m", .70)
  return svg


def _svg_text(root, x, y, label, size=4.3):
  _add(root, "text", x=x, y=y, text_anchor="middle", font_size=size).text = label


def _diagram(model):
  svg = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 308 215"})
  SubElement(svg, "title").text = "双路光纤系统与机柜端接"
  SubElement(svg, "style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#263644}
.a{stroke:#64488e;stroke-width:.85;fill:none}.b{stroke:#267257;stroke-width:.85;fill:none}
.unit{fill:#f2f5f7;stroke:#417081;stroke-width:.52}
.rack{fill:#f0f3f5;stroke:#3b505c;stroke-width:.59}
"""
  pods = [row["pod"] for row in model["planes"]["A"]["cables"].values()]
  if len(pods) > 16:
    raise ValueError("The telecom system sheet has room for up to sixteen pods.")
  wires = {plane: {row["pod"]: key for key, row in model["planes"][plane]["cables"].items()} for plane in PLANES}
  _svg_text(svg, 154, 12, "双路光纤系统与机柜端接", 6.5)
  for plane, x, aisle in (("A", 66, 10), ("B", 242, 298)):
    label = model["planes"][plane]["cabinet"]["id"]
    _add(svg, "rect", x=x-37, y=22, width=74, height=16, data_cabinet=label, **{"class": "unit"})
    _svg_text(svg, x, 32, f"{label}  室外汇聚柜")
    _add(svg, "path", d=f"M{x},38 V47 H{aisle} V141", **{"class": plane.lower()})
  for row, offset in enumerate(range(0, len(pods), 4)):
    y = 65+23*row
    _add(svg, "path", d=f"M10,{y-7} H256", **{"class": "a"})
    _add(svg, "path", d=f"M298,{y+7} H52", **{"class": "b"})
    for col, name in enumerate(pods[offset:offset+4]):
      x = 53+67*col
      _add(svg, "rect", x=x-17, y=y-4.6, width=34, height=9.2, data_instance=name, **{"class": "unit"})
      _svg_text(svg, x, y+1.4, name)
      for plane, dx, direction in (("A", 1, -1), ("B", -1, 1)):
        _add(svg, "line", x1=x+dx, y1=y+7*direction, x2=x+dx, y2=y+4.6*direction,
             data_cable=wires[plane][name], **{"class": plane.lower()})

  pod = pods[0]
  terms = [row for row in model["allocation"].values() if row["rack"].split("/")[0] == pod and row["plane"] == "A"]
  for x, plane in ((51, "A"), (257, "B")):
    _add(svg, "rect", x=x-33, y=170, width=66, height=16, **{"class": "unit"})
    _svg_text(svg, x, 180, model["planes"][plane]["cables"][wires[plane][pod]]["panel"])
  _add(svg, "rect", x=109, y=168, width=90, height=20, **{"class": "rack"})
  first, last = terms[0]["rack"].split("/")[-1], terms[-1]["rack"].split("/")[-1]
  _svg_text(svg, 154, 180, f"{pod} · {first}–{last}")
  for a, b, side in ((84, 109, "A"), (199, 224, "B")):
    _add(svg, "line", x1=a, y1=178, x2=b, y2=178, **{"class": side.lower()})
  _svg_text(svg, 154, 202, f"单缆 {model['cable']['fiber_count']} 芯 · 端口及纤芯编号见端接总表", 4.4)
  return svg


def build_telecom_view(context):
  scope = dict(context.scope)
  model = staged_json(context, MODEL)
  if scope.keys() == {"plane"} and scope["plane"] in PLANES:
    root = _plan(context, model, scope["plane"])
  elif scope == {"view": "system"}:
    root = _diagram(model)
  else:
    raise ValueError("Select a telecom service plane or the system diagram.")
  output = context.build_path / context.output
  if output.suffix != ".svg":
    raise ValueError("Telecom drawings require a named SVG destination.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "telecom-view-svg", output),))
