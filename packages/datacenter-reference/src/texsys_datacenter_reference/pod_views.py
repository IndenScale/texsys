"""Plot construction placement and access from the compiled IT pod model."""

from xml.etree.ElementTree import SubElement, indent, tostring

from texsys.targets import TargetResult

from .fws_buffer_views import _canvas
from .plans import _line, _rect, _text, _xy
from .pod_layout import MODEL
from .spatial_design import artifact, staged_json


_STYLE = """
.shell{fill:#cdd7db;stroke:#253443;stroke-width:.026}
.rack{fill:#dbebf0;stroke:#3c687a;stroke-width:.027}
.cdu{fill:#dcecea;stroke:#427b6e;stroke-width:.027}
.air{fill:#ece8d9;stroke:#786949;stroke-width:.027}
.control{fill:#e6e9ed;stroke:#4e5a64;stroke-width:.023}
.aisle{fill:#edf2f3;stroke:#79979c;stroke-width:.023;stroke-dasharray:.10 .067}
.opening{fill:#f4f7f6;stroke:#64878e;stroke-width:.025;stroke-dasharray:.085 .065}
.cutting{fill:none;stroke:#596970;stroke-width:.023;stroke-dasharray:.17 .065 .025 .065}
.dim{fill:none;stroke:#485962;stroke-width:.020}
"""
_FAMILIES = {"liquid-cooled-rack": "rack", "coolant-distribution-unit": "cdu", "precision-air-unit": "air"}


def _symbol(row):
  return _FAMILIES.get(row["family"], "control")


def _base(bounds, title):
  svg = _canvas(bounds, title)
  SubElement(svg, "style").text = _STYLE
  return svg


def _dim(svg, a, b, at, *, vertical=False, label=None):
  points = ((at, a, at, b) if vertical else (a, at, b, at))
  _line(svg, *points, "dim")
  for end in (a, b):
    x, y = (at, end) if vertical else (end, at)
    _line(svg, x-.038, y-.039, x+.038, y+.039, "dim")
  x, y = (at+.07, (a+b)/2) if vertical else ((a+b)/2, at+.065)
  _text(svg, x, y, label or f"{(b-a)*1000:.0f}", .119, "start" if vertical else "middle")


def _plan(pod, row):
  length, width, _ = row["size_m"]
  svg = _base((-.12, -.68, length+.50, width+.84), f"{pod} 方舱设备及检修通道")
  _rect(svg, (0, 0, length, width), "aisle", data_envelope=pod)
  _rect(svg, _xy(row["aisle"]), "aisle", data_access="working-aisle")
  for key, item in row["doors"].items():
    access = _xy(item["access"])
    _rect(svg, access, "opening", data_door=key)
    x, y, _ = item["exterior_m"]
    _text(svg, x, -.20 if y == 0 else width+.19, f"{key}  ·  {item['clear_width_m']*1000:.0f}", .125)
  for key, part in row["local_shell"].items():
    if key not in {"ROOF", "D-N-H", "D-S-H"}:
      _rect(svg, _xy(part), "shell", data_shell=key)
  for key, part in row["local_equipment"].items():
    a, b, c, d = _xy(part)
    _rect(svg, (a, b, c, d), _symbol(part), data_equipment=key)
    if key.startswith("Feed"):
      _text(svg, (a+c)/2, d+.067, key, .082)
    else:
      _text(svg, (a+c)/2, (b+d)/2-.037, key.replace("Rack", "R"), .119 if len(key) < 8 else .090)
  x = row["local_equipment"]["Rack01"]["minimum_m"][0]+row["local_equipment"]["Rack01"]["dimensions_m"][0]/2
  for y in (-.11, width+.12):
    _line(svg, x-.09, y, x+.09, y, "cutting")
    _text(svg, x+.15, y-.034, "1", .115)
  _line(svg, x, -.08, x, width+.09, "cutting")
  _dim(svg, 0, length, -.47)
  _dim(svg, 0, width, length+.12, vertical=True)
  _, left, _, right = _xy(row["aisle"])
  _dim(svg, left, right, 2.77, vertical=True, label=f"{(right-left)*1000:.0f} 净")
  _text(svg, length*.59, width+.53, f"{pod} 双排机柜及服务设备平面", .175)
  _text(svg, length-.02, width+.23, "舱体 +Y ↑", .159, "end")
  return svg


def _section(pod, row):
  x = row["local_equipment"]["Rack01"]["minimum_m"][0]+row["local_equipment"]["Rack01"]["dimensions_m"][0]/2
  _, width, height = row["size_m"]
  svg = _base((-.47, -.50, width+.82, height+.81), f"{pod} 检修通道横剖面")
  for key, part in row["local_shell"].items():
    (a, y0, z0), (b, y1, z1) = part["minimum_m"], part["maximum_m"]
    if a <= x <= b:
      _rect(svg, (y0, z0, y1, z1), "shell", data_shell=key)
  for key, part in row["local_equipment"].items():
    (a, y0, z0), (b, y1, z1) = part["minimum_m"], part["maximum_m"]
    if a <= x <= b:
      _rect(svg, (y0, z0, y1, z1), _symbol(part), data_equipment=key)
      _text(svg, (y0+y1)/2, (z0+z1)/2, key, .132)
  _, a, headroom = row["aisle"]["maximum_m"]
  _, b, _ = row["aisle"]["minimum_m"]
  _rect(svg, (b, 0, a, headroom), "aisle", data_access="headroom")
  _line(svg, -.25, 0., width+.29, 0., "dim")
  _text(svg, (a+b)/2, 1.62, "检修通道", .138)
  _text(svg, (a+b)/2, 1.30, f"净空 ≥ {headroom*1000:.0f}", .113)
  _dim(svg, b, a, .25, label=f"{(a-b)*1000:.0f} 净")
  _dim(svg, 0, width, -.29)
  _dim(svg, 0, height, width+.33, vertical=True)
  _text(svg, .05, height+.49, f"1—1  {pod}  机柜与检修通道剖面", .185, "start")
  _text(svg, .05, height+.23, f"方舱局部 X {x:.2f} m   ·   地板基准 ±0.000", .122, "start")
  return svg


def build_pod_view(context):
  model = staged_json(context, MODEL)
  name = context.scope.get("pod", model["typical"])
  row = model["pods"][name]
  views = {"plan": _plan, "section": _section}
  view = context.scope["view"]
  svg = views[view](name, row)
  output = context.build_path/context.output
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(svg)
  output.write_text(tostring(svg, encoding="unicode")+"\n", encoding="utf-8")
  return TargetResult("pass", (artifact(context, f"pod-{view}-view", output),))
