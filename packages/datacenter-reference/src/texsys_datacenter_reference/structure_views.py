"""Draw calibrated plans and a grid section directly from the frame model."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .plans import _STYLE, _axes, _dimension, _line, _north_and_scale, _rect, _text
from .spatial_design import artifact, staged_json
from .structure import MODEL


_MEMBERS = """
.column{fill:#192028;stroke:#192028;stroke-width:.05}
.girder{fill:#c7d5df;stroke:#192028;stroke-width:.08}
.tie{fill:#e2e9ed;stroke:#35434e;stroke-width:.075}
.foundation{fill:#e5e4df;stroke:#333b3e;stroke-width:.08}
.plinth{fill:#fff;stroke:#333b3e;stroke-width:.07}
"""


def _part_bounds(part, axes):
  a, b = axes
  x, y = part["origin"][a], part["origin"][b]
  return x, y, x+part["size"][a], y+part["size"][b]


def _plan(root, model, bounds, view):
  _axes(root, model, bounds)
  _north_and_scale(root, bounds)
  for identity, member in model["members"].items():
    kind = member["kind"]
    x, y, _ = member["start_m"]
    x1, y1, _ = member["end_m"]
    if view == "footings" and kind == "foundation":
      pad, plinth = member["parts"]
      _rect(root, _part_bounds(pad, (0, 1)), kind, data_member=identity)
      _rect(root, _part_bounds(plinth, (0, 1)), "plinth", data_member=identity)
      label_x = x + pad["size"][0]/2 + .29
      _text(root, label_x, y + .37, identity, .59, "start")
      _text(root, label_x, y - .51, "×".join(f"{value*1000:.0f}" for value in pad["size"]), .47, "start")
    elif view == "frame" and kind != "foundation":
      for part in member["parts"]:
        _rect(root, _part_bounds(part, (0, 1)), kind, data_member=identity)
      if kind == "girder":
        _text(root, (x+x1)/2, y + .59, identity, .58)
      elif kind == "tie":
        _text(root, x + .28, (y+y1)/2 + .12, identity, .57, "start")
      else:
        _text(root, x + .40, y - .82, identity, .57, "start")


def _section(root, model, bounds, line):
  xmin, low, xmax, high = bounds
  mark = model["y"][line]
  for axis, x in model["x"].items():
    _line(root, x, low - .8, x, high + 1.1, "grid")
    _text(root, x, high + 1.23, axis, .65)
  for a, b in zip(list(model["x"].values())[:-1], list(model["x"].values())[1:], strict=True):
    _dimension(root, a, b, low - 1.0, vertical=False)
  for identity, member in model["members"].items():
    kind = member["kind"]
    x, centerline, z = member["start_m"]
    parts = member["parts"]
    if kind == "column" and centerline == mark:
      # Project one full flange and the base plate to show the column outline on the cut axis.
      parts = [parts[0], parts[-1]]
    for part in parts:
      y = part["origin"][1]
      if (kind == "column" and centerline == mark) or y <= mark <= y + part["size"][1]:
        cls = "plinth" if part["identity"] == "Plinth" else kind
        _rect(root, _part_bounds(part, (0, 2)), cls, data_member=identity)
    if centerline == mark and kind in {"column", "foundation"}:
      _text(root, x + .45, z + .40, identity, .60, "start")
  base = next(member["start_m"][2] for member in model["members"].values() if member["kind"] == "column")
  for label, depth in (("屋面梁顶", high), ("柱脚", base), ("基础底", low)):
    _line(root, xmin - 2.5, depth, xmax + 1.2, depth, "grid")
    _text(root, xmin - 2.6, depth + .20, f"{label} {depth:+.3f}", .57, "end")
  _text(root, (xmin+xmax)/2, high + 2.1, f"{line} 轴纵剖面", .83)


def build_structure_view(context):
  model = staged_json(context, MODEL)
  view = context.scope.get("view")
  xmin, xmax = min(model["x"].values()), max(model["x"].values())
  if view in {"frame", "footings"} and set(context.scope) == {"view"}:
    ymin, ymax = min(model["y"].values()), max(model["y"].values())
  elif view == "section" and set(context.scope) == {"view", "axis"}:
    ymin = min(member["start_m"][2] for member in model["members"].values())
    ymax = max(member["end_m"][2] for member in model["members"].values())
  else:
    raise ValueError("Structure drawing scope must select frame, footings or a named-axis section.")
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "data-frame": model["frame"],
                         "data-coordinate-unit": "m", "data-units-per-meter": "1",
                         "viewBox": f"{xmin-7:g} {-ymax-4:g} {xmax-xmin+13:g} {ymax-ymin+12:g}"})
  SubElement(root, "title").text = {"frame": "屋面钢梁与柱", "footings": "独立基础定位",
                                   "section": "主机房结构剖面"}[view]
  SubElement(root, "style").text = _STYLE + _MEMBERS
  bounds = xmin, ymin, xmax, ymax
  if view == "section":
    _section(root, model, bounds, context.scope["axis"])
  else:
    _plan(root, model, bounds, view)
  output = context.build_path / (context.output or f"drawings/views/structure-{view}.svg")
  if output.suffix != ".svg":
    raise ValueError("Structure drawing output must use the SVG extension.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "structure-view", output),))
