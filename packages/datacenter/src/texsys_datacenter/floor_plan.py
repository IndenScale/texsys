"""Project compiled architectural room footprints into floor plan sheets."""

from xml.etree.ElementTree import Element, SubElement, tostring

from texsys.formal import _load_json
from texsys.targets import Artifact, BuildContext, TargetResult


def _sheets(building, scope):
  model = building.get("architecture")
  if model is None:
    raise ValueError("Floor plans require compiled architectural rooms.")
  if (len(scope) != 1 or not scope.keys() <= {"room", "level", "work_package"}
      or any(not isinstance(value, str) or not value for value in scope.values())):
    raise ValueError("Floor plan scope requires one room, level or work_package identifier.")
  [(kind, selected)] = scope.items()
  rooms = model["rooms"]
  levels = building["levels_m"]
  if kind == "room" and selected in rooms:
    members = [selected]
  elif kind == "level" and selected in levels:
    members = [name for name, room in rooms.items() if room["minimum_m"][2] == levels[selected]]
  elif kind == "work_package" and selected in model["work_packages"]:
    members = model["work_packages"][selected]
  else:
    raise ValueError(f"Unknown floor plan {kind}: {selected}.")
  sheets = {
    level: {name: rooms[name] for name in members if rooms[name]["minimum_m"][2] == elevation}
    for level, elevation in sorted(levels.items(), key=lambda item: item[1])
  }
  sheets = {level: records for level, records in sheets.items() if records}
  if not sheets or sum(map(len, sheets.values())) != len(members):
    raise ValueError(f"No complete set of floor plan rooms for {selected}.")
  return sheets


def _svg(building, sheets, scope):
  width, depth = building["floor_m"]
  left, bottom = 0, 0
  if "room" in scope:
    [room] = next(iter(sheets.values())).values()
    left, bottom, _ = room["minimum_m"]
    right, top, _ = room["maximum_m"]
    width, depth = right - left, top - bottom
  sheet_height = depth + 6
  svg = Element("svg", {"xmlns": "http://www.w3.org/2000/svg",
                        "viewBox": f"{left - 2:g} {-bottom - depth - 4:g} {width + 4:g} {len(sheets) * sheet_height:g}",
                        "data-frame": building["frame"], "data-coordinate-unit": "m",
                        "font-family": "sans-serif", "role": "img"})
  SubElement(svg, "title").text = f"{next(iter(scope.values()))} · 平面布置"
  SubElement(svg, "desc").text = "概念建筑房间投影，XY 坐标单位为米，各楼层独立成图。"
  for index, (level, rooms) in enumerate(sheets.items()):
    sheet = SubElement(svg, "g", {"data-level-id": level, "transform": f"translate(0 {index * sheet_height:g})"})
    SubElement(sheet, "text", {"x": f"{left:g}", "y": f"{-bottom - depth - 1:g}", "font-size": "1.4"}).text = (
      f"{level} · {building['levels_m'][level]:g} m"
    )
    SubElement(sheet, "rect", {"x": f"{left:g}", "y": f"{-bottom - depth:g}", "width": f"{width:g}",
                               "height": f"{depth:g}", "fill": "#fff", "stroke": "#617282", "stroke-width": ".08"})

    def area(identity):
      x, y, _ = rooms[identity]["minimum_m"]
      right, top, _ = rooms[identity]["maximum_m"]
      return (right - x) * (top - y)

    for identity in sorted(rooms, key=lambda identity: (-area(identity), identity)):
      room = rooms[identity]
      x, y, _ = room["minimum_m"]
      right, top, _ = room["maximum_m"]
      group = SubElement(sheet, "g", {"data-room-id": identity})
      SubElement(group, "title").text = identity
      SubElement(group, "rect", {"x": f"{x:g}", "y": f"{-top:g}", "width": f"{right - x:g}",
                                 "height": f"{top - y:g}", "fill": "#e8f1f6", "stroke": "#446781", "stroke-width": ".06"})
      SubElement(group, "text", {"x": f"{(x + right) / 2:g}", "y": f"{-(y + top) / 2:g}", "font-size": ".8",
                                 "text-anchor": "middle", "fill": "#193b50"}).text = identity.split("/", 1)[-1]
  return tostring(svg, encoding="utf-8", xml_declaration=True) + b"\n"


def build_floor_plan(context: BuildContext) -> TargetResult:
  directory = context.build_path / "datacenter"
  building = _load_json((directory / "building-study.json").read_text(encoding="utf-8"))
  sheets = _sheets(building, context.scope)
  output = context.build_path / (context.output or "datacenter/floor-plan.svg")
  if output.suffix != ".svg":
    raise ValueError("A floor plan output must use the .svg extension.")
  output.parent.mkdir(parents=True, exist_ok=True)
  output.write_bytes(_svg(building, sheets, context.scope))
  return TargetResult("pass", (Artifact("floor-plan", output.relative_to(context.root).as_posix()),),
                      details={"frame": building["frame"], "coordinate_unit": "m", "fidelity": "concept",
                               "sheets": {level: sorted(rooms) for level, rooms in sheets.items()}})
