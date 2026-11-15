"""Draw facility A/B banks, local motor feeds and the governing electrical cases."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .cw_network import MODEL as CW
from .facility_power import MODEL
from .facility_power_study import STUDY
from .fws_buffer_views import _canvas
from .fws_transient_views import _text_at
from .plans import _add, _line, _rect, _text, _xy
from .power_routing import LAYOUT
from .site_grading import MODEL as TERRAIN
from .site_storm import terrain_profile
from .spatial_design import MODELS, artifact, staged_json

_STYLE = """
.asset-context{fill:#e8ebec;stroke:#69767d;stroke-width:.095}
.transfer{fill:#e6dece;stroke:#78654e;stroke-width:.09}
.a{stroke:#975123;fill:#edd0ad;stroke-width:.10}
.b{stroke:#225984;fill:#c9dfef;stroke-width:.10}
.pump{stroke:#28806b;fill:#c8e4d9;stroke-width:.078}
.tower{stroke:#695777;fill:#e4d8ed;stroke-width:.083}
.command{stroke:#7a5778;fill:#eee1ec;stroke-width:.085}
.water-context{fill:none;stroke:#9baeb0;stroke-width:.090;stroke-dasharray:.21 .18}
.ground{stroke:#576f52;fill:none;stroke-width:.036}
.datum{stroke:#8799a1;fill:none;stroke-width:.031;stroke-dasharray:.12 .07}
.cutmark{stroke:#4e5668;stroke-width:.092;stroke-dasharray:.43 .24}
.leader{stroke:#735c47;fill:none;stroke-width:.06}
.detail .a,.detail .b{stroke-width:.031}
.detail .asset-context{stroke-width:.032}
"""


def _root(rect, title):
  root = _canvas(rect, title)
  SubElement(root, "style").text = _STYLE
  return root


def _role(key):
  if key.startswith(("A-", "B-")):
    return key[0].lower()
  if key.startswith("CMD-"):
    return "command"
  return "tower" if "Tower" in key else "pump"


def _assets(root, objects, picked):
  for key in picked:
    row = objects[key]
    x0, y0, x1, y1 = _xy(row)
    _rect(root, (x0, y0, x1, y1), "asset-context" if key not in {"AuxFeed", "ControlPanel"} else "transfer",
          data_equipment=key)
    if key not in {"AuxFeed", "ControlPanel"}:
      _text(root, (x0+x1)/2, (y0+y1)/2, key, .43)


def _cabinet_callouts(root, objects, high):
  for key, direction in (("AuxFeed", 1), ("ControlPanel", -1)):
    x0, y0, x1, y1 = _xy(objects[key])
    x = x1 if direction > 0 else x0
    y = (y0+y1)/2
    elbow = x+.4*direction
    end = elbow+1.4*direction
    top = high+(.87 if direction > 0 else 1.58)
    _add(root, "polyline", points=f"{x:.4f},{-y:.4f} {elbow:.4f},{-y:.4f} {elbow:.4f},{-top:.4f} {end:.4f},{-top:.4f}",
         **{"class": "leader"}, data_cabinet=key)
    _text(root, end+.1*direction, top-.13, key, .44, "start" if direction > 0 else "end")


def _footprint(root, name, row):
  a, b = row["points_m"]
  role = _role(name)
  attrs = {"data_route": name, "data_sdl_links": " ".join(row["links"])}
  if a[:2] == b[:2]:
    _add(root, "circle", cx=a[0], cy=-a[1], r=.095, **{"class": role}, **attrs)
    return
  _rect(root, _xy(row["envelope"]), role, **attrs)


def _plan(context, model, objects, *, underground):
  if underground:
    picked = sorted({row[side].split(".")[0] for row in model["circuits"].values()
                     for side in ("from", "to") if row["kind"] in {"plant", "aux", "control"}})
    title = "动力区 A/B 供电排管平面"
  else:
    picked = sorted({row[side].split(".")[0] for row in model["circuits"].values()
                     for side in ("from", "to") if row["kind"] in {"pump", "tower"}})
    title = "冷源区泵塔就地动力支路平面"
  corners = [_xy(objects[name]) for name in picked]
  x0, y0 = min(a for a, _, _, _ in corners), min(b for _, b, _, _ in corners)
  x1, y1 = max(c for _, _, c, _ in corners), max(d for _, _, _, d in corners)
  root = _root((x0-1.5, y0-3.6, x1+1.8, y1+2.4), title)
  grid = staged_json(context, f"{MODELS}/grid.json")
  for name, x in grid["x"].items():
    if x0-.8 <= x <= x1+.4:
      _line(root, x, y0-2.84, x, y1+.47, "grid").set("data-axis", name)
      _add(root, "circle", cx=x, cy=-(y0-3.0), r=.28, **{"class": "axis"})
      _text(root, x, y0-3.12, name, .35)
  if not underground:
    water = staged_json(context, CW)
    for key, row in water["routes"].items():
      _add(root, "polyline", points=" ".join(f"{x:.6f},{-y:.6f}" for x, y, _ in row["points_m"]),
           **{"class": "water-context"}, data_water_link=key)
  _assets(root, objects, picked)
  for name, row in model["segments"].items():
    if underground == (not name.startswith("LOCAL-")):
      _footprint(root, name, row)
  if underground:
    _cabinet_callouts(root, objects, y1)
    for side, y in (("A", y0-2.01), ("B", y0-2.75)):
      route = model["assumptions"]["trains"][side]
      _text(root, x0+.09, y, f"{side} 路排管中心 EL {route['trunk_z_m']:+.2f}", .47, "start")
    first = min(name for name in picked if name.startswith("CoolContainer"))
    station = objects[first]["ports_m"]["power_a"][0]
    _line(root, station, y0-1.66, station, y0-.32, "cutmark").set("data-section", first)
    _text(root, station+.29, y0-1.74, "1", .47)
  else:
    setout = model["assumptions"]["routes"]
    _text(root, x0+.35, y1+1.12, f"冷却泵 EL +{setout['pump_carrier_z_m']:.2f}   冷却塔 EL +{setout['tower_carrier_z_m']:.2f}",
          .45, "start")
  return root


def _section(context, model, objects, plant):
  port_x, port_y, _ = objects[plant]["ports_m"]["power_a"]
  a, b = port_y-2.24, port_y+3.7
  root = _root((a-.4, -2.05, b+2., 3.42), f"{plant} 入线排管与转换柜横剖")
  root.set("class", "detail")
  grade = staged_json(context, TERRAIN)
  _rect(root, (port_y, 0, objects[plant]["maximum_m"][1], objects[plant]["maximum_m"][2]), "asset-context", data_plant=plant)
  ground = terrain_profile(grade, (port_x, a, 0), (port_x, b, 0))
  _add(root, "polyline", points=" ".join(f"{a+offset:.6f},{-z:.6f}" for offset, z, _ in ground),
       **{"class": "ground"}, data_surface="formation")
  _line(root, a, 0, b, 0, "datum")
  sides = {side: route for side, route in model["assumptions"]["trains"].items()}
  for side, specs in sides.items():
    key = next(k for k, row in model["segments"].items() if k.startswith(f"{side}-BANK") and
               row["envelope"]["minimum_m"][0]-1e-6 <= port_x <= row["envelope"]["maximum_m"][0]+1e-6)
    item = model["segments"][key]["envelope"]
    _, y0, z0 = item["minimum_m"]
    _, y1, z1 = item["maximum_m"]
    _rect(root, (y0, z0, y1, z1), side.lower(), data_bank=key)
    _text(root, a+.05, specs["trunk_z_m"]+.04, f"{side} EL {specs['trunk_z_m']:+.2f}", .135, "start")
  for key, row in model["segments"].items():
    if not key.startswith(f"A-{plant}-"):
      continue
    _, y0, z0 = row["envelope"]["minimum_m"]
    _, y1, z1 = row["envelope"]["maximum_m"]
    _rect(root, (y0, z0, y1, z1), "a", data_route=key)
  _text(root, port_y+.35, 2.42, f"{plant} 转换柜接入", .176, "start")
  _text(root, port_y+.31, .26, "A 路沿正面接口引入", .136, "start")
  _text(root, port_y+.31, -.52, f"横剖 X = {port_x:.3f} m", .133, "start")
  _text(root, port_y+.31, -.80, "B 路入线沿相邻 X 站位", .133, "start")
  return root


def _matrix(study):
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 292 202"})
  SubElement(root, "title").text = "冷源动力供电连续性"
  SubElement(root, "style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#253642}
.panel{stroke:#4c6372;fill:#f7f9f9;stroke-width:.55}
.grid{stroke:#aebbc0;stroke-width:.35}
.good{fill:#dbeadd;stroke:#527662;stroke-width:.25}
.off{fill:#f2dfcc;stroke:#8b6741;stroke-width:.25}
"""
  states = study["states"]
  cases = [
    ("仅 A 路", next(row for row in states.values() if row["conditions"] == "train-only" and row["operating_train"] == "A")),
    ("仅 B 路", next(row for row in states.values() if row["conditions"] == "train-only" and row["operating_train"] == "B")),
    ("1 号冷源柜退出", next(row for row in states.values() if row["conditions"] == "cell-transfer-off"
                           and row["fws_case"].endswith("CoolContainer1"))),
    ("2 号冷源柜退出", next(row for row in states.values() if row["conditions"] == "cell-transfer-off"
                           and row["fws_case"].endswith("CoolContainer2"))),
    ("3 号冷源柜退出", next(row for row in states.values() if row["conditions"] == "cell-transfer-off"
                           and row["fws_case"].endswith("CoolContainer3"))),
    ("共用控制退出", next(row for row in states.values() if row["conditions"] == "common-aux-off")),
  ]
  cells = list(study["cell_ratings"])
  _text_at(root, 15, 17, "冷源供电工况矩阵", 6, "start")
  _text_at(root, 15, 28, "各冷源柜执行开路转换，冷却泵和塔风机随本列供电。", 4, "start")
  _add(root, "rect", x=15, y=45, width=262, height=97, **{"class": "panel"})
  for label, x in (("工况", 22), ("冷源 1", 83), ("冷源 2", 114), ("冷源 3", 145), ("冷源耗电 kW", 182), ("单列总输入 kW", 231)):
    _text_at(root, x, 53, label, 3.7, "start")
  for index, (title, row) in enumerate(cases):
    y = 65+13.0*index
    _add(root, "line", x1=15, x2=277, y1=y-7, y2=y-7, **{"class": "grid"})
    _text_at(root, 22, y+2.5, title, 4, "start")
    for j, cell in enumerate(cells):
      active = cell in row["running_cells"]
      x = 83+31*j
      _add(root, "rect", x=x, y=y-3.1, width=21, height=7, data_cell=cell, **{"class": "good" if active else "off"})
      value = row["running_cells"][cell]["demand_kw"] if active else None
      _text_at(root, x+10.5, y+2, f"{value:.0f}" if active else "停用", 3.7)
    _text_at(root, 204, y+2, f"{row['plant_kw']:.1f}", 3.9)
    _text_at(root, 253, y+2, f"{row['single_train_input_kw']:.1f}", 3.9)
  faults = study["fault_cases"]
  largest = max(row["current_max_ka"] for row in faults.values())
  drop = max(row["maximum_total_drop_pct"] for row in study["ratings"].values())
  _text_at(root, 16, 157, f"{len(faults)} 项三相保护工况    最大短路电流 {largest:.2f} kA    最大电压降 {drop:.2f}%", 4.2, "start")
  _text_at(root, 16, 170, "共用控制故障仍需独立黑启动与恢复试验。", 4, "start")
  return root


def build_facility_power_view(context):
  mode = context.scope["mode"]
  model = staged_json(context, MODEL)
  layout = staged_json(context, LAYOUT)["objects"]
  if mode in ("entry-plan", "cells-plan"):
    view = _plan(context, model, layout, underground=mode == "entry-plan")
  elif mode == "section":
    view = _section(context, model, layout, context.scope["plant"])
  elif mode == "matrix":
    view = _matrix(staged_json(context, STUDY))
  else:
    raise ValueError(f"Unknown facility power view {mode}.")
  output = context.build_path/context.output
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(view)
  output.write_text(tostring(view, encoding="unicode")+"\n", encoding="utf-8")
  return TargetResult("pass", (artifact(context, "facility-power-view", output),))
