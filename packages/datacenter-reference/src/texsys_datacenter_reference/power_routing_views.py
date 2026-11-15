"""Project critical feeder installation plans and a coordinated penetration section."""

from xml.etree.ElementTree import SubElement, indent, tostring

from texsys.targets import TargetResult

from .architecture import MODEL as ARCHITECTURE
from .fws_buffer_views import _canvas
from .plans import _add, _line, _rect, _text, _xy
from .power_routing import LAYOUT, MODEL
from .spatial_design import MODELS, artifact, staged_json

_STYLE = """
.hall{fill:#faf9f5;stroke:#4b5357;stroke-width:.14}
.pod{fill:#e8eef1;stroke:#677982;stroke-width:.075}
.source{fill:#e9e4da;stroke:#6a5b4f;stroke-width:.09}
.roof{fill:none;stroke:#60676c;stroke-width:.035;stroke-dasharray:.13 .075}
.main-a,.branch-a,.drop-a,.bus-a{stroke:#91511b;fill:#e9bc86;stroke-width:.10}
.main-b,.branch-b,.drop-b,.bus-b{stroke:#205579;fill:#b0cce1;stroke-width:.10}
.bus-a,.bus-b{stroke-width:.077}
.section .drop-a,.section .drop-b,.section .bus-a,.section .bus-b,.section .pod{stroke-width:.024}
.sectionsleeve{stroke:#222b30;fill:white;stroke-width:.018}
.cut{stroke:#526870;stroke-width:.07;stroke-dasharray:.32 .22}
"""

def _plan(context, side, train):
  architecture = staged_json(context, ARCHITECTURE)
  layout = staged_json(context, LAYOUT)
  grid = staged_json(context, f"{MODELS}/grid.json")
  objects = layout["objects"]
  busways = train["busways"]
  roof = _xy(architecture["hall"])
  pods = sorted({name for row in busways.values() for name in row["terminal_paths"]})
  source, = {row["from"].split(".")[0] for row in busways.values()}
  left, bottom, right, top = roof
  center, _, _, source_end = _xy(objects[source])
  spine = min(point[0] for row in train["segments"].values() for point in row["points_m"])
  svg = _canvas((min(spine-2.4, left-4.6), bottom-2.0, right+1.2, source_end+1.35), f"{side} 路主机房屋面配电布置")
  SubElement(svg, "style").text = _STYLE
  _rect(svg, roof, "hall", data_zone="IT-HALL")
  for key, x in grid["x"].items():
    if left <= x <= right:
      _line(svg, x, bottom-1.12, x, top+.52, "grid").set("data-axis", key)
      _add(svg, "circle", cx=x, cy=-(bottom-1.18), r=.32, **{"class": "axis"})
      _text(svg, x, bottom-1.30, key, .34)
  for key, y in grid["y"].items():
    if bottom <= y <= top:
      _line(svg, left-1.13, y, right+.37, y, "grid").set("data-axis", key)
      _add(svg, "circle", cx=left-1.2, cy=-y, r=.32, **{"class": "axis"})
      _text(svg, left-1.2, y-.12, key, .34)
  for name in pods:
    x0, y0, x1, y1 = _xy(objects[name])
    _rect(svg, (x0, y0, x1, y1), "pod", data_pod=name)
    _text(svg, x0+.27, (y0+y1)/2-.12, name, .43, "start")
  _rect(svg, _xy(objects[source]), "source", data_asset=source)
  _text(svg, center+3, source_end-.55, source, .45)
  for key, row in train["segments"].items():
    suffix = side.lower()
    if row["points_m"][0][:2] == row["points_m"][1][:2]:
      x, y, _ = row["points_m"][0]
      _add(svg, "circle", cx=x, cy=-y, r=.135, data_route=key, data_sdl_link=row["links"][0],
           **{"class": f"drop-{suffix}"})
    else:
      _rect(svg, _xy(row["envelope"]), f"{row['kind']}-{suffix}", data_route=key, data_sdl_links=" ".join(row["links"]))
  for bus, row in busways.items():
    x, y, _, _ = _xy(objects[bus])
    _rect(svg, _xy(objects[bus]), f"bus-{side.lower()}", data_busway=bus, data_sdl_link=row["link"])
    text_offset = -.53 if side == "A" else .32
    _text(svg, x+.23, y+text_offset, f"{bus}   EL +{row['entry_m'][2]:.3f}", .38, "start")
  station = objects["IT01"]["ports_m"]["power_a"][0]
  for y in (bottom-.38, top+.42):
    _line(svg, station-.35, y, station+.35, y, "cut")
    _text(svg, station+.49, y-.12, "1", .39, "start")
  return svg


def _section(context, trains, pod):
  layout = staged_json(context, LAYOUT)
  architecture = staged_json(context, ARCHITECTURE)
  hall_z = architecture["hall"]["maximum_m"][2]
  host = layout["objects"][pod]
  _, y0, z0 = host["minimum_m"]
  _, y1, z1 = host["maximum_m"]
  svg = _canvas((-.5, 0, 10.8, 9.2), f"{pod} A/B 馈电入舱横剖面")
  svg.set("class", "section")
  SubElement(svg, "style").text = _STYLE
  width = y1-y0
  _rect(svg, (0, z0, width, z1), "pod", data_pod=pod)
  _line(svg, -.33, hall_z, width+.55, hall_z, "roof").set("data_interface", "building-roof")
  _line(svg, -.33, z0, width+.55, z0, "roof")
  _text(svg, width+.7, hall_z-.075, f"主机房屋面 EL +{hall_z:.3f}", .13, "start")
  _text(svg, width+.7, z1-.075, f"方舱顶板 EL +{z1:.3f}", .13, "start")
  _text(svg, width+.7, z0-.075, f"方舱底板 EL +{z0:.3f}", .13, "start")
  sleeves = staged_json(context, f"{MODELS}/power-coordination.json")["penetrations"]
  for side, train in trains.items():
    bus, = (name for name, row in train["busways"].items() if pod in row["terminal_paths"])
    tap = train["busways"][bus]["terminal_paths"][pod]
    record = train["segments"][tap["segment"]]
    jacket = layout["objects"][bus]
    _, uy, lz = jacket["minimum_m"]
    _, vy, uz = jacket["maximum_m"]
    suffix = side.lower()
    _rect(svg, (uy-y0, lz, vy-y0, uz), f"bus-{suffix}", data_busway=bus)
    _, sy, sz = record["envelope"]["minimum_m"]
    _, ty, tz = record["envelope"]["maximum_m"]
    _rect(svg, (sy-y0, sz, ty-y0, tz), f"drop-{suffix}", data_route=tap["segment"], data_sdl_link=tap["link"])
    sleeve = sleeves[tap["segment"]]
    sleeve_width = sleeve["width_m"]
    for field in ("building_roof_m", "pod_roof_m"):
      _, y, z = sleeve[field]
      _rect(svg, (y-y0-sleeve_width/2, z-.043, y-y0+sleeve_width/2, z+.043), "sectionsleeve",
            data_penetration=tap["segment"], data_host=field)
    _text(svg, (uy+vy)/2-y0, uz+.15, f"{bus} 底 +{lz:.3f}", .146, "start" if side == "A" else "middle")
    _text(svg, (sy+ty)/2-y0+.13, (z1+hall_z)/2, f"{side} 路 {tap['link']}", .12, "start")
  station = host["ports_m"]["power_a"][0]
  _text(svg, 0, 8.65, f"1–1  横剖面  X = {station:.3f} m", .18, "start")
  sleeve_mm = round(1000*sleeve["width_m"])
  _text(svg, width+.7, 3.06, f"A/B 各预留 {sleeve_mm} × {sleeve_mm} 套管洞口", .13, "start")
  _text(svg, width+.7, 2.81, "洞口按编号及 X / Y / Z 坐标对照定位表", .13, "start")
  return svg


def build_power_routing_view(context):
  routes = staged_json(context, MODEL)["trains"]
  mode = context.scope["kind"]
  if mode == "plan":
    side = context.scope["train"]
    svg = _plan(context, side, routes[side])
  elif mode == "section":
    svg = _section(context, routes, context.scope["pod"])
  else:
    raise ValueError(f"Unknown power routing view {mode}.")
  file = context.build_path / context.output
  file.parent.mkdir(parents=True, exist_ok=True)
  indent(svg)
  file.write_text(tostring(svg, encoding="unicode")+"\n", encoding="utf-8")
  return TargetResult("pass", (artifact(context, "power-route-view", file),))
