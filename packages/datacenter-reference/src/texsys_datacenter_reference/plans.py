"""Project the staged site, datum, placement and routing models into plans."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from .spatial_design import DISCIPLINES, MODELS, artifact, staged_json
from texsys.targets import TargetResult


_STYLE = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#192028}
.boundary{fill:none;stroke:#18232a;stroke-width:.17}
.setback{fill:none;stroke:#646e76;stroke-width:.09;stroke-dasharray:.55 .3}
.grid{stroke:#707d87;stroke-width:.045;stroke-dasharray:.8 .33 .17 .33}
.axis{fill:white;stroke:#35434e;stroke-width:.08}
.dimension{stroke:#34444f;fill:none;stroke-width:.06}
.zone{fill:#f5f7f8;stroke:#506575;stroke-width:.10;stroke-dasharray:.34 .24}
.architecture{fill:#dce3e8;stroke:#303941}
.structure{fill:none;stroke:#192028;stroke-dasharray:.48 .2}
.it{fill:#e4eef3;stroke:#263b4a}
.electrical{fill:#ece7d9;stroke:#524633}
.mechanical{fill:#e3ede9;stroke:#345046}
.asset{stroke-width:.10}
.supply{fill:none;stroke:#15546f;stroke-width:.16;stroke-linejoin:round}
.return{fill:none;stroke:#973d2a;stroke-width:.16;stroke-dasharray:.55 .3;stroke-linejoin:round}
"""


def _add(parent, tag, **attributes):
  return SubElement(parent, tag, {key.replace("_", "-"): f"{value:.6f}".rstrip("0").rstrip(".")
                                  if isinstance(value, float) else str(value)
                                  for key, value in attributes.items()})


def _line(parent, x0, y0, x1, y1, cls):
  return _add(parent, "line", x1=x0, y1=-y0, x2=x1, y2=-y1, **{"class": cls})


def _rect(parent, bounds, cls, **attributes):
  x0, y0, x1, y1 = bounds
  return _add(parent, "rect", x=x0, y=-y1, width=x1 - x0, height=y1 - y0, **{"class": cls}, **attributes)


def _text(parent, x, y, text, size, anchor="middle"):
  _add(parent, "text", x=x, y=-y, font_size=size, text_anchor=anchor).text = text


def _xy(record):
  x0, y0, _ = record["minimum_m"]
  x1, y1, _ = record["maximum_m"]
  return x0, y0, x1, y1


def _dimension(parent, a, b, station, vertical):
  def point(along, across):
    return (across, along) if vertical else (along, across)
  _line(parent, *point(a, station), *point(b, station), "dimension")
  for v in (a, b):
    _line(parent, *point(v - .22, station - .22), *point(v + .22, station + .22), "dimension")
  # Dimension numerals express model millimeters regardless of paper scale.
  if vertical:
    x, y = point((a + b) / 2, station - .35)
    node = _add(parent, "text", x=x, y=-y, text_anchor="middle", font_size=.84,
                transform=f"rotate(-90 {x:g} {-y:g})")
    node.text = f"{(b-a)*1000:.0f}"
  else:
    _text(parent, (a + b) / 2, station + .35, f"{(b-a)*1000:.0f}", .84)


def _axes(parent, grid, bounds, *, dimensions=True):
  x0, y0, x1, y1 = bounds
  for axis, positions in (("x", grid["x"]), ("y", grid["y"])):
    is_x = axis == "x"
    low, high = (x0, x1) if is_x else (y0, y1)
    visible = sorted(((key, val) for key, val in positions.items() if low <= val <= high),
                     key=lambda item: item[1])
    for key, val in visible:
      x, y = (val, y0 - 4) if is_x else (x0 - 4, val)
      end = (val, y1 + 1) if is_x else (x1 + 1, val)
      line = _line(parent, x, y, *end, "grid")
      line.set("data-axis", key)
      _add(parent, "circle", cx=x, cy=-y, r=.66, **{"class": "axis"})
      _text(parent, x, y - .27, key, .78)
    if dimensions:
      for (_, a), (_, b) in zip(visible, visible[1:]):
        _dimension(parent, a, b, y0 - 2 if is_x else x0 - 2, vertical=not is_x)


def _north_and_scale(parent, bounds):
  x0, y0, x1, y1 = bounds
  x, y = x1 + 3, y1
  _add(parent, "path", d=f"M{x:g},{-y-2:g} l-.75,2.4 .75,-.45 .75,.45 z", fill="#18232a")
  _text(parent, x, y + 2.4, "N", .96)
  for i in range(2):
    _add(parent, "rect", x=x0+i*2, y=-y0+5.5, width=2, height=.3,
         fill="#18232a" if i == 0 else "white", stroke="#18232a", stroke_width=.08)
  for i in range(3):
    _text(parent, x0+i*2, y0-7, str(i*2) + (" m" if i == 2 else ""), .84)


def _draw_assets(parent, objects, scope, zones):
  discipline = scope.get("discipline")
  for name, record in sorted(objects.items(), key=lambda item: item[1]["discipline"] != "structure"):
    if record["zone"] not in zones or discipline and record["discipline"] != discipline:
      continue
    bounds = _xy(record)
    layer = record["discipline"]
    _rect(parent, bounds, f"asset {layer}", data_instance=name, data_semantic_ref=record["semantic_ref"],
          data_discipline=layer, data_zone=record["zone"])
    x0, y0, x1, y1 = bounds
    if name.startswith("Floor") or not discipline and (layer == "structure" or name.startswith("Wall")):
      continue
    if name.startswith("Busway"):
      if discipline:
        _text(parent, x0 + .30, y0 + .18, name, .57, "start")
    elif name.startswith("CwPump"):
      if discipline:
        _text(parent, (x0+x1)/2, y0 - .65, name, .71)
    elif name.startswith("Wall"):
      _text(parent, (x0+x1)/2, y0 - .8, name, .71)
    elif layer == "structure":
      _text(parent, x0 + .46, y1 - .84, name, .73, "start")
    else:
      _text(parent, (x0+x1)/2, (y0+y1)/2 - .23, name, .7)


def build_plan(context):
  scope = context.scope
  if (scope.keys() - {"zone", "level", "discipline"} or ("zone" in scope and "level" in scope)
      or ("discipline" in scope and scope["discipline"] not in DISCIPLINES)):
    raise ValueError("Plan scope accepts one zone or level, and optionally one drawing discipline.")
  mode = context.target.split(":")[1]
  site = staged_json(context, f"{MODELS}/site.json")
  grid = {} if mode == "site-plan" else staged_json(context, f"{MODELS}/grid.json")
  levels = {} if mode in {"site-plan", "grid-plan"} else staged_json(context, f"{MODELS}/levels.json")
  layout = staged_json(context, f"{MODELS}/layout.json") if mode in {"plan", "fws-plan"} else {}
  zones = levels.get("zones", {})
  if "zone" in scope:
    identity = scope["zone"]
    zones = {identity: zones[identity]}
  elif "level" in scope:
    if scope["level"] not in levels.get("levels_m", {}):
      raise ValueError(f"Unknown floor datum: {scope['level']}.")
    zones = {name: zone for name, zone in zones.items() if zone["level"] == scope["level"]}
    if not zones:
      raise ValueError(f"No zones at datum {scope['level']}.")
  if mode == "fws-plan" and scope:
    raise ValueError("The FWS sheet covers the entire declared loop.")
  x0, y0, x1, y1 = site["boundary_m"]
  if "zone" in scope or "level" in scope:
    bounds = [_xy(record) for record in zones.values()]
    x0, y0, x1, y1 = min(p[0] for p in bounds), min(p[1] for p in bounds), max(p[2] for p in bounds), max(p[3] for p in bounds)
  bounds = x0, y0, x1, y1
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "data-frame": site["frame"],
                         "viewBox": f"{x0-6:g} {-y1-5:g} {x1-x0+11:g} {y1-y0+13:g}",
                         "data-coordinate-unit": "m", "data-units-per-meter": "1", "role": "img"})
  SubElement(root, "title").text = context.instance_id or mode
  SubElement(root, "desc").text = "AIDC 合成方案。局部坐标以米计，尺寸标注以毫米计。"
  SubElement(root, "style").text = _STYLE
  _rect(root, site["boundary_m"], "boundary", data_parcel=site["parcel"])
  _rect(root, site["buildable_m"], "setback")
  for name, zone in zones.items():
    box = _xy(zone)
    _rect(root, box, "zone", data_zone=name, data_elevation_m=zone["minimum_m"][2])
    _text(root, box[0] + .5, box[3] + .72, f"{zone['name']}  {zone['minimum_m'][2]:+.3f} m", .96, "start")
  if grid:
    _axes(root, grid, bounds)
  else:
    _dimension(root, x0, x1, y0 - 2, vertical=False)
    _dimension(root, y0, y1, x0 - 2, vertical=True)
  _north_and_scale(root, bounds)
  _draw_assets(root, layout.get("objects", {}), scope, zones)
  if mode == "fws-plan":
    routes = staged_json(context, f"{MODELS}/fws-routing.json")["routes"]
    for leg, route in routes.items():
      _add(root, "polyline", points=" ".join(f"{x:g},{-y:g}" for x, y, _ in route["points_m"]),
           data_leg=leg, data_length_m=route["length_m"], **{"class": leg})
      x, y = route["header_xy_m"]
      label = "供水" if leg == "supply" else "回水"
      _text(root, x + .32, y + .42, f"{label} +{route['tier_m']:.2f} m", .84, "start")
  output = context.build_path / (context.output or f"drawings/views/{mode}.svg")
  if output.suffix != ".svg":
    raise ValueError("A plan output needs the SVG extension.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "plan-svg", output),))
