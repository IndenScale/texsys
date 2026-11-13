"""Project compiled room and layout zone footprints into floor plan sheets."""

from xml.etree.ElementTree import Element, SubElement, tostring

from texsys.formal import _load_json
from texsys.targets import Artifact, BuildContext, TargetResult


def _sheets(building, scope):
  keys = set(scope)
  if (keys not in ({"room"}, {"level"}, {"work_package"}, {"level", "zone"})
      or any(not isinstance(value, str) or not value for value in scope.values())):
    raise ValueError("Floor plan scope requires a room, level, work_package, or a level and zone together.")
  levels = building["levels_m"]
  if "zone" in scope:
    level, zone = scope["level"], scope["zone"]
    if level not in levels:
      raise ValueError(f"Unknown floor plan level: {level}.")
    zones = building["floor_zones"]
    if zone not in zones:
      raise ValueError(f"Unknown floor plan zone: {zone}.")
    record = zones[zone]
    return {level: {f"{level}/{zone}": (*record["origin_m"], *record["size_m"])}}
  model = building.get("architecture")
  if model is None:
    raise ValueError("Floor plans require compiled architectural rooms.")
  [(kind, selected)] = scope.items()
  rooms = model["rooms"]
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
  return {level: {name: _room_footprint(room) for name, room in records.items()}
          for level, records in sheets.items()}


def _room_footprint(room):
  x, y, _ = room["minimum_m"]
  right, top, _ = room["maximum_m"]
  return x, y, right - x, top - y


def _svg(building, sheets, scope):
  width, depth = building["floor_m"]
  left, bottom = 0, 0
  zone_view = "zone" in scope
  title = next(iter(scope.values()))
  if "room" in scope or zone_view:
    [(title, (left, bottom, width, depth))] = next(iter(sheets.values())).items()
  sheet_height = depth + 6
  svg = Element("svg", {"xmlns": "http://www.w3.org/2000/svg",
                        "viewBox": f"{left - 2:g} {-bottom - depth - 4:g} {width + 4:g} {len(sheets) * sheet_height:g}",
                        "data-frame": building["frame"], "data-coordinate-unit": "m",
                        "font-family": "sans-serif", "role": "img"})
  SubElement(svg, "title").text = f"{title} · 平面布置"
  SubElement(svg, "desc").text = (
    "概念楼层布局区域投影，XY 坐标单位为米。" if zone_view
    else "概念建筑房间投影，XY 坐标单位为米，各楼层独立成图。"
  )
  for index, (level, footprints) in enumerate(sheets.items()):
    sheet = SubElement(svg, "g", {"data-level-id": level, "transform": f"translate(0 {index * sheet_height:g})"})
    SubElement(sheet, "text", {"x": f"{left:g}", "y": f"{-bottom - depth - 1:g}", "font-size": "1.4"}).text = (
      f"{level} · {building['levels_m'][level]:g} m"
    )
    SubElement(sheet, "rect", {"x": f"{left:g}", "y": f"{-bottom - depth:g}", "width": f"{width:g}",
                               "height": f"{depth:g}", "fill": "#fff", "stroke": "#617282", "stroke-width": ".08"})

    def drawing_order(identity):
      _, _, dx, dy = footprints[identity]
      return -dx * dy, identity

    for identity in sorted(footprints, key=drawing_order):
      x, y, dx, dy = footprints[identity]
      group = SubElement(sheet, "g", {"data-zone-id" if zone_view else "data-room-id": identity})
      SubElement(group, "title").text = identity
      SubElement(group, "rect", {"x": f"{x:g}", "y": f"{-y - dy:g}", "width": f"{dx:g}",
                                 "height": f"{dy:g}", "fill": "#e8f1f6", "stroke": "#446781", "stroke-width": ".06"})
      SubElement(group, "text", {"x": f"{x + dx / 2:g}", "y": f"{-y - dy / 2:g}", "font-size": ".8",
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
                               "sheets": {level: sorted(footprints) for level, footprints in sheets.items()}})
