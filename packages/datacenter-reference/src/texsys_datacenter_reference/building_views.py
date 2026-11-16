"""Project the compiled building and egress data into calibrated AIDC sheets."""

from itertools import pairwise
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .architecture import MODEL as ARCHITECTURE
from .fire_egress import MODEL as EGRESS
from .plans import _STYLE, _add, _axes, _dimension, _line, _north_and_scale, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json


_BUILDING_STYLE = """
.shell{fill:#384759;stroke:#19242c;stroke-width:.065}
.deck{fill:#a6b0b9;stroke:#33424c;stroke-width:.06}
.land{fill:#edf2ef;stroke:#687b74;stroke-width:.08}
.leaf{fill:none;stroke:#375649;stroke-width:.09}
.blocked{fill:none;stroke:#a63f31;stroke-width:.13}
.fire{fill:#3c454d;stroke:#783d35;stroke-width:.08}
.fc-s{fill:#f5f4ed}.fc-n{fill:#eef4f5}
.aisle{fill:#a6ccaf;fill-opacity:.34}
.route{fill:none;stroke:#27714a;stroke-width:.11}
.access{fill:white;stroke:#27714a;stroke-width:.08}
.pod{fill:#e2e8ed;stroke:#657581;stroke-width:.09}
.level-line{fill:none;stroke:#556d78;stroke-width:.05;stroke-dasharray:.29 .13}
"""


def _drawing(box, title, *, section=False):
  x0, y0, x1, y1 = box
  left, right, bottom, top = (1.6, 3.1, 1.0, 1.15) if section else (6, 5, 8, 5)
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "data-coordinate-unit": "m",
                         "data-units-per-meter": "1",
                         "viewBox": f"{x0-left:g} {-y1-top:g} {x1-x0+left+right:g} {y1-y0+bottom+top:g}"})
  SubElement(root, "title").text = title
  SubElement(root, "style").text = _STYLE + _BUILDING_STYLE
  return root


def _door_leaves(door):
  x = door["landing_stations_m"][0][0]
  outward = -1 if door["side"] == "west" else 1
  length = door["clear_width_m"]/door["leaf_count"]
  for y,direction in ((door["minimum_m"][1],1),(door["maximum_m"][1],-1))[:door["leaf_count"]]:
    yield (x,y),length,outward,direction


def _doors(root, architecture):
  for tag, door in architecture["doors"].items():
    x, y, _ = door["inside_m"]
    _, y_min, _ = door["minimum_m"]
    _, y_max, _ = door["maximum_m"]
    handed = -1 if door["side"] == "west" else 1
    edge, stop, bottom, end = door["landing_stations_m"]
    half = door["ramp_width_m"]/2
    for a, b in ((edge, stop), (stop, bottom), (bottom, end)):
      _rect(root, (min(a[0], b[0]), y-half, max(a[0], b[0]), y+half), "land", data_ramp=tag)
    _text(root, (stop[0]+bottom[0])/2, y + half + .30, f"{100*door['ramp_slope']:.2f}%", .44)
    if door["available"]:
      for (jamb,hinge),leaf,outward,toward in _door_leaves(door):
        leaf_tip = jamb+outward*leaf
        _line(root, jamb, hinge, leaf_tip, hinge, "leaf")
        sweep = int(outward != toward)
        _add(root, "path", d=f"M{leaf_tip:g},{-hinge:g} A{leaf:g},{leaf:g} 0 0 {sweep} {jamb:g},{-(hinge+toward*leaf):g}",
             data_door=tag, **{"class": "leaf"})
    else:
      _line(root, edge[0], y_min, edge[0], y_max, "blocked").set("data-door", tag)
    _text(root, x-handed*.60, y + 1.02, tag, .53, "start" if handed < 0 else "end")


def _floor_plan(context, architecture, *, egress=False):
  grid = staged_json(context, f"{MODELS}/grid.json")
  layout = staged_json(context, f"{MODELS}/layout.json")
  x0, y0, x1, y1 = _xy(architecture["hall"])
  extents = [door["discharge_m"][0] for door in architecture["doors"].values()]
  root = _drawing((min(extents), y0, max(extents), y1), "主机房防火分区及疏散" if egress else "主机房建筑平面")
  root.set("data-frame", layout["frame"])
  for identity, room in architecture["rooms"].items():
    _rect(root, _xy(room), "fc-s" if identity == "FC-S" else "fc-n", data_compartment=identity)
  _axes(root, grid, (x0, y0, x1, y1), dimensions=False)
  for a, b in pairwise(sorted(x for x in grid["x"].values() if x0 <= x <= x1)):
    _dimension(root, a, b, y0-2, vertical=False)
  for a, b in pairwise(sorted(y for y in grid["y"].values() if y0 <= y <= y1)):
    _dimension(root, a, b, min(extents)-1.95, vertical=True)
  _north_and_scale(root, (x0, y0, x1, y1))
  for identity, row in architecture["walls"].items():
    if identity.startswith("W-over-"):
      continue
    _rect(root, _xy(row), "fire" if identity == "W-DIVIDE" else "shell", data_wall=identity)
  for identity, item in layout["objects"].items():
    if item["discipline"] != "it" or item["zone"] != architecture["zone"]:
      continue
    a, b, c, d = _xy(item)
    _rect(root, (a, b, c, d), "pod", data_instance=identity)
    _text(root, (a+c)/2, (b+d)/2-.16, identity, .55)
  _doors(root, architecture)
  if egress:
    _fire_routes(root, architecture, staged_json(context, EGRESS))
  else:
    cut = architecture["rooms"]["FC-S"]["aisle_y_m"]
    station = min(extents)-.54
    _text(root, station-.40, cut-.18, "1", .53)
    _line(root, station, cut, min(extents), cut, "level-line").set("data-section-direction", "D-SW")
    _text(root, x0, y1+.77, f"L01  {architecture['hall']['minimum_m'][2]:+.3f}", .70, "start")
    _text(root, (x0+x1)/2, y1+.77, f"{architecture['partition_axis']} 轴防火分隔", .68)
  return root


def _fire_routes(root, architecture, report):
  x0, _, x1, _ = _xy(architecture["hall"])
  half = report["limits"]["minimum_clear_width_m"]/2
  for identity, room in architecture["rooms"].items():
    y = room["aisle_y_m"]
    _rect(root, (x0, y-half, x1, y+half), "aisle", data_room_route=identity)
    _, _, _, top = _xy(room)
    label = f"{identity} · {room['floor_area_m2']:.1f} m²"
    _text(root, (x0+x1)/2, top-.66, label, .60)
  for name, record in report["routes"].items():
    # The common aisle is drawn once. Each pod shows its approach and nearer usable exit.
    candidates = sorted(record["paths"], key=lambda row: (not row["pass"], row["inside_to_exit_m"], row["exit"]))
    path = candidates[0]
    a, b, c = path["points_m"][:3]
    cls = "route" if path["pass"] else "blocked"
    _line(root, *a[:2], *b[:2], cls).set("data-egress-origin", name)
    sign = 1 if c[0] > b[0] else -1
    _add(root, "polyline", points=f"{b[0]:g},{-b[1]:g} {b[0]+sign*.70:g},{-b[1]:g} {b[0]+sign*.43:g},{-b[1]-.16:g}",
         data_exit=path["exit"], **{"class": cls})
    _add(root, "circle", cx=a[0], cy=-a[1], r=.12, **{"class": "access"})
  for tag, exit_record in report["exits"].items():
    point = exit_record["points_m"][-1]
    _add(root, "circle", cx=point[0], cy=-point[1], r=.22, data_discharge=tag,
         **{"class": "access" if exit_record["pass"] else "blocked"})


def _exit_section(architecture, tag):
  door = architecture["doors"][tag]
  if door["side"] != "west":
    raise ValueError("This section is oriented through a west exit.")
  inside, _, floor = door["inside_m"]
  grade = architecture["yard_m"]
  outside_x = door["minimum_m"][0]
  extent = door["discharge_m"][0]
  roof = architecture["hall"]["maximum_m"][2]
  head = door["maximum_m"][2]
  root = _drawing((extent-.4, grade-.7, inside+4, roof), f"{tag} 出口剖面", section=True)
  SubElement(root, "style").text = ".route{stroke-width:.040}.shell,.deck{stroke-width:.028}"
  slab_bottom = architecture["slab"]["minimum_m"][2]
  _rect(root, (inside, slab_bottom, inside+4, floor), "deck", data_section_slab="L01")
  _rect(root, (outside_x, door["threshold"]["minimum_m"][2], inside, floor), "deck", data_threshold=tag)
  _rect(root, (outside_x, head, inside, roof), "shell", data_door_head=tag)
  path = door["landing_stations_m"]
  _add(root, "polyline", points=" ".join(f"{x:g},{-z:g}" for x, z in path), data_ramp=tag, **{"class": "route"})
  for text, z in (("场坪", grade), ("L01", floor), ("门洞上沿", head), ("RF", roof)):
    _line(root, inside+4, z, inside+4.52, z, "level-line")
    _text(root, inside+4.54, z+.06, f"{text} {z:+.3f}", .20, "start")
  run = abs(path[1][0]-path[2][0])
  for a, b in ((path[0], path[1]), (path[1], path[2]), (path[2], path[3])):
    x0, x1 = sorted((a[0], b[0]))
    _line(root, x0, grade-.33, x1, grade-.33, "dimension")
    for x in (x0, x1):
      _line(root, x-.042, grade-.372, x+.042, grade-.288, "dimension")
    _text(root, (x0+x1)/2, grade-.21, f"{(x1-x0)*1000:.0f}", .19)
  _text(root, (path[1][0]+path[2][0])/2, (floor+grade)/2+.24, f"{(floor-grade)/run*100:.2f}%", .22)
  _text(root, outside_x-.04, head-.35, tag, .22, "end")
  return root


def build_building_view(context):
  architecture = staged_json(context, ARCHITECTURE)
  scope = dict(context.scope)
  if scope == {"view": "floor"}:
    root = _floor_plan(context, architecture)
  elif scope == {"view": "fire"}:
    root = _floor_plan(context, architecture, egress=True)
  elif scope.keys() == {"view", "door"} and scope["view"] == "exit-section":
    root = _exit_section(architecture, scope["door"])
  else:
    raise ValueError("Choose the building floor plan, fire plan or a named exit section.")
  output = context.build_path / context.output
  if output.suffix != ".svg":
    raise ValueError("A building view requires an SVG destination.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "building-view-svg", output),))
