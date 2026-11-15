"""Draw grading, stormwater alignments, detention and performance from staged data."""

from math import ceil
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .architecture import MODEL as ARCHITECTURE
from .fws_buffer_views import _canvas
from .fws_transient_views import _text_at
from .plans import _add, _line, _rect, _text, _xy
from .site_grading import MODEL as GRADING, elevation
from .site_storm import DETENTION_IN, DETENTION_OUT, MODEL, OUTFALL, terrain_profile
from .site_storm_study import STUDY
from .spatial_design import MODELS, artifact, staged_json
from .wet_utilities import MODEL as WET

_STYLE = """
.parcel{fill:none;stroke:#324852;stroke-width:.23}
.context{fill:#e3e4de;stroke:#53626c;stroke-width:.105}
.yard-equipment{fill:#f3f1eb;stroke:#6b767a;stroke-width:.095}
.pavement{fill:#e7edef;stroke:none}
.landscape{fill:#eef3e5;stroke:none}
.roof-fill{fill:#e7e5df;stroke:none}
.oil-fill{fill:#f1e6d4;stroke:none}
.zone0{fill:#e1edf2;stroke:none}.zone1{fill:#edf0e0;stroke:none}
.zone2{fill:#e8eee8;stroke:none}.zone3{fill:#e1e5ef;stroke:none}
.contour{fill:none;stroke:#63796e;stroke-width:.080}
.low-contour{fill:none;stroke:#2c6075;stroke-width:.14}
.net{fill:none;stroke:#145575;stroke-width:.24}
.branch{fill:none;stroke:#248785;stroke-width:.15}
.wet{fill:none;stroke:#8c735a;stroke-width:.12;stroke-dasharray:.43 .26}
.bund{fill:none;stroke:#98632b;stroke-width:.18;stroke-dasharray:.60 .26}
.inlet{fill:white;stroke:#135e7a;stroke-width:.18}
.storm-tank{fill:#dceaf1;stroke:#325b71;stroke-width:.15}
.spot{fill:#273b45;stroke:none}.outline-point{fill:#fff;stroke:#2c5768;stroke-width:.12}
.terrain{fill:none;stroke:#54765c;stroke-width:.22}
.baseline{fill:none;stroke:#93a0a2;stroke-width:.15;stroke-dasharray:.65 .30}
.inverse{fill:none;stroke:#215b79;stroke-width:.19}
.pipe-top{fill:none;stroke:#5592ab;stroke-width:.13}
.manhole{fill:none;stroke:#314f63;stroke-width:.13}
.hgl{fill:none;stroke:#257fa5;stroke-width:.055;stroke-dasharray:.27 .13}
.reservoir{fill:#eff4f6;stroke:#405361;stroke-width:.040}
.throttle{fill:#b9d7e1;stroke:#236180;stroke-width:.032}
.section-ground{fill:none;stroke:#486f50;stroke-width:.035}
.tank-detail .inverse{stroke-width:.035}
"""


def _base(rect, title):
  root = _canvas(rect, title)
  SubElement(root, "style").text = _STYLE
  return root


def _polylines(svg, points, style, **attrs):
  return _add(svg, "polyline", points=" ".join(f"{x:.6f},{-y:.6f}" for x, y in points),
              **{"class": style}, **attrs)


def _point(svg, point, radius, style, **attrs):
  x, y = point[:2]
  return _add(svg, "circle", cx=x, cy=-y, r=radius, **{"class": style}, **attrs)


def _terrain_layer(svg, terrain, *, catchments):
  xy = terrain["points_xy_m"]
  proposed = sum(terrain["surface"]["proposed_m"], [])
  names = {name: f"zone{i}" for i, name in enumerate(sorted(terrain["catchments"]))}
  styles = {"roof": "roof-fill", "oil": "oil-fill", "hardstand": "pavement", "landscape": "landscape"}
  for row in terrain["triangles"]:
    vertices = row["vertices"]
    kind = row["surface"]
    color = names[row["catchment"]] if catchments and row["catchment"] else styles[kind]
    shape = " ".join(f"{xy[index][0]:.6f},{-xy[index][1]:.6f}" for index in vertices)
    _add(svg, "polygon", points=shape, **{"class": color}, data_catchment=row["catchment"] or kind)
    if catchments or kind == "roof":
      continue
    a, b, c = vertices
    for cm in range(-10, 41, 10):
      level = cm/100
      chord = []
      for i, j in ((a, b), (b, c), (c, a)):
        h0, h1 = proposed[i], proposed[j]
        if min(h0, h1) < level <= max(h0, h1):
          t = (level-h0)/(h1-h0)
          chord.append(tuple(p+(q-p)*t for p, q in zip(xy[i], xy[j], strict=True)))
      if len(chord) == 2:
        _line(svg, *chord[0], *chord[1], "low-contour" if cm == 0 else "contour").set("data-level", str(level))


def _survey_points(svg, terrain):
  for key, row in terrain["hardstands"].items():
    a, b, c, d = row["extent_m"]
    _rect(svg, (a, b, c, d), "context", data_platform=key)
    _text(svg, (a+c)/2, (b+d)/2, f"{key} EL {row['grade_m']:+.2f}", .68)
  for key, row in terrain["landing_controls"].items():
    x, y = row["end_xy_m"]
    _point(svg, (x, y), .28, "spot", data_landing=key)
    _text(svg, x+.56, y+.62, f"{row['grade_m']:+.2f}", .57, "start")
  xx, yy = [terrain["surface"][name] for name in ("x_m", "y_m")]
  for x in (xx[0], 20, 60, xx[-1]):
    for y in (yy[0], 14, 56):
      if not yy[0] <= y <= yy[-1] or not xx[0] <= x <= xx[-1]:
        continue
      z = elevation(terrain, x, y)
      _point(svg, (x, y), .21, "spot")
      _text(svg, x-.45 if x == xx[-1] else x+.4, y+.6, f"{z:+.2f}", .58, "end" if x == xx[-1] else "start")


def _plan(context, grade, storm=None):
  a, b, c, d = grade["extent_m"]
  root = _base((a-4, b-4, c+5, d+4), "场区雨水排水总图" if storm else "场区竖向设计及土方平面")
  _terrain_layer(root, grade, catchments=storm is not None)
  _rect(root, grade["extent_m"], "parcel", data_parcel=grade["parcel"])
  hall = staged_json(context, ARCHITECTURE)["hall"]
  _rect(root, _xy(hall), "context", data_hall="IT-HALL")
  _text(root, (hall["minimum_m"][0]+hall["maximum_m"][0])/2, (hall["minimum_m"][1]+hall["maximum_m"][1])/2,
        "主机房屋面汇水", .83)
  oil = grade["oil_yard"]
  _rect(root, oil["extent_m"], "bund", data_partition="oil")
  _point(root, oil["collector_xy_m"], .36, "inlet", data_oil_inlet="OIL-I")
  _text(root, oil["extent_m"][0]+.8, oil["extent_m"][3]-1.1, "密闭收集区", .7, "start")
  if not storm:
    _survey_points(root, grade)
    work = grade["earthwork"]
    _text(root, 2., -2.15, f"场地挖方 {work['cut_m3']:.1f} m³    填方 {work['fill_m3']:.1f} m³", .73, "start")
  else:
    layout = staged_json(context, f"{MODELS}/layout.json")
    for key, row in layout["objects"].items():
      if row["zone"] in {"POWER", "COOLING", "STANDBY"} and row["discipline"] in {"electrical", "mechanical"}:
        _rect(root, _xy(row), "yard-equipment", data_equipment=key)
    wet = staged_json(context, WET)
    for name, row in wet["runs"].items():
      coords = [p[:2] for p in row["points_m"] if p[2] < 0]
      if len(set(map(tuple, coords))) > 1:
        _polylines(root, coords, "wet", data_separate_service=name)
    for key, row in storm["roof_shoes"].items():
      _polylines(root, [p[:2] for p in row["points_m"]], "branch", data_downpipe=key)
    for name, row in storm["runs"].items():
      first, last = row["points_m"]
      _line(root, *first[:2], *last[:2], "net").set("data-run", name)
      x, y = ((a+b)/2 for a, b in zip(first[:2], last[:2], strict=True))
      if row["length_m"] > 8:
        _text(root, x+.64 if first[0] == last[0] else x, y+.68, f"{name} {row['pipe']['size']}", .58,
              "start" if first[0] == last[0] else "middle")
    for key, row in storm["nodes"].items():
      x, y, _ = row["point_m"]
      _point(root, (x, y), .32, "inlet", data_node=key)
      if key in storm["roof_shoes"]:
        _text(root, x+.55, y-1.26, key, .58, "start")
      elif key == OUTFALL:
        _text(root, x-.25, y+1.2, "东界雨水交接", .69, "end")
    _rect(root, storm["detention"]["extent_m"], "storm-tank", data_tank="DETENTION")
    t = storm["detention"]["extent_m"]
    _text(root, (t[0]+t[2])/2, (t[1]+t[3])/2+1.1, "调蓄池", .7)
    _text(root, 2., -2.15, "纵断线沿北侧最远屋面落水至东界排水口", .72, "start")
  _line(root, c-6.5, d-5.8, c-6.5, d-2.1, "net")
  _line(root, c-7.2, d-3.4, c-6.5, d-2.1, "net")
  _line(root, c-5.8, d-3.4, c-6.5, d-2.1, "net")
  _text(root, c-6.5, d-1.0, "北", .72)
  return root


def _profile(terrain, storm, start):
  positions = []
  cursor = 0.
  path = storm["catchment_paths"][start]
  last, = (key for key, row in storm["runs"].items() if row["from_node"] == DETENTION_OUT)
  full = [*path, "DETENTION", last]
  for key in full:
    if key == "DETENTION":
      a, b = (storm["nodes"][tag]["point_m"] for tag in (DETENTION_IN, DETENTION_OUT))
      length = b[0]-a[0]
    else:
      row = storm["runs"][key]
      a, b = row["points_m"]
      length = row["surface_profile_m"][-1][0]
    positions.append((cursor, cursor+length, key, a, b))
    cursor += length
  factor = 6
  root = _base((-4.8, -43., cursor+4.8, 8.5), "北侧最远支路至雨水交接点纵断面")
  for n in range(-5, 1):
    line = _line(root, 0, factor*n, cursor, factor*n, "baseline")
    line.set("data-profile-elevation", str(n))
    _text(root, -.40, factor*n-.35, f"{n:+.1f}", .75, "end")
  for offset, finish, key, a, b in positions:
    grade = terrain_profile(terrain, a, b)
    _polylines(root, [(offset+x, z*factor) for x, z, _ in grade], "terrain")
    if key == "DETENTION":
      tank = storm["detention"]
      width = tank["extent_m"][2]-tank["extent_m"][0]
      _rect(root, (offset, tank["bottom_m"]*factor, offset+width, tank["ceiling_m"]*factor), "storm-tank",
            data_profile_tank=key)
      _text(root, offset+width/2, (tank["bottom_m"]+tank["ceiling_m"])/2*factor, "调蓄池", .76)
    else:
      row = storm["runs"][key]
      d = row["pipe"]["inside_m"]
      lo, hi = row["invert_m"]
      _line(root, offset, lo*factor, finish, hi*factor, "inverse").set("data-pipe-invert", key)
      _line(root, offset, (lo+d)*factor, finish, (hi+d)*factor, "pipe-top")
      if finish-offset >= 6:
        _text(root, (offset+finish)/2, (lo+hi)/2*factor-1.7, row["pipe"]["size"], .70)
  for offset, _, key, a, _ in positions:
    if key == "DETENTION":
      name = DETENTION_IN
      floor = storm["nodes"][name]["point_m"][2]
    else:
      name = storm["runs"][key]["from_node"]
      floor = storm["runs"][key]["invert_m"][0]
    rim = elevation(terrain, *a[:2])
    _line(root, offset, floor*factor, offset, rim*factor +.8, "manhole").set("data-station", name)
    _text(root, offset, 2.0, name, .73)
    _text(root, offset, -32.1, f"{offset:.1f}", .76)
    _text(root, offset, -34.6, f"{rim:+.3f}", .76)
    _text(root, offset, -37.1, f"{floor:+.3f}", .76)
  outfall = storm["nodes"][OUTFALL]["point_m"]
  _line(root, cursor, outfall[2]*factor, cursor, .8, "manhole").set("data_station", OUTFALL)
  _text(root, cursor, 2.0, "东界", .76)
  _text(root, cursor, -32.1, f"{cursor:.1f}", .76)
  _text(root, cursor, -34.6, f"{elevation(terrain, *outfall[:2]):+.3f}", .76)
  _text(root, cursor, -37.1, f"{outfall[2]:+.3f}", .76)
  for y, label in ((-32.1, "桩距"), (-34.6, "地面"), (-37.1, "管底")):
    _line(root, -4.2, y+.57, cursor+2.1, y+.57, "baseline")
    _text(root, -2.0, y, label, .74, "end")
  _text(root, 0, -40., "沿线桩距、设计地面和管底标高 / m", .82, "start")
  _text(root, 0, 6., "北西落水经北侧总管、调蓄池至东界雨水出界口", .90, "start")
  return root


def _tank_section(terrain, storm, study):
  tank = storm["detention"]
  x0, _, x1, _ = tank["extent_m"]
  inlet = storm["nodes"][DETENTION_IN]["point_m"]
  outlet = storm["nodes"][DETENTION_OUT]["point_m"]
  bottom, roof = tank["bottom_m"], tank["ceiling_m"]
  root = _base((x0-1.9, bottom-.85, x1+3.8, 1.1), "调蓄池及限流井纵剖面")
  _rect(root, (x0, bottom, x1, roof), "reservoir", data_tank="DETENTION")
  root.set("class", "tank-detail")
  control = max(case["detention"]["peak_water_level_m"] for case in study["cases"].values())
  _line(root, x0, control, x1, control, "hgl").set("data_stage", "peak-water-level")
  _text(root, (x0+x1)/2, control+.22, f"控制工况峰值水位 {control:+.3f} m", .176)
  _text(root, (x0+x1)/2, (roof+control)/2, f"有效调蓄 {tank['working_capacity_m3']:.1f} m³", .19)
  profile_end = [x1+2.8, inlet[1], 0]
  profile_start = [x0-1.3, inlet[1], 0]
  profile = terrain_profile(terrain, profile_start, profile_end)
  _polylines(root, [(profile_start[0]+s, z) for s, z, _ in profile], "section-ground")
  out = tank["orifice_points_m"][0]
  _line(root, x0-1.25, inlet[2], x0, inlet[2], "inverse")
  _text(root, x0-1.2, inlet[2]+.31, "雨水入池", .171, "start")
  r = tank["outlet_diameter_m"]/2
  _rect(root, (x1, out[2]-r, outlet[0], out[2]+r), "throttle", data_orifice="D-OUT")
  _line(root, outlet[0], outlet[2], x1+2.5, outlet[2]-.034, "inverse")
  _text(root, x1+.24, out[2]+.31, f"限流孔 Ø{2*r*1000:.0f}", .166, "start")
  _text(root, x1+.25, outlet[2]-.36, f"出口管底 {outlet[2]:+.3f}", .166, "start")
  _text(root, x0+.39, bottom-.29, f"池底 EL {bottom:+.3f}", .176, "start")
  _text(root, x0+.39, roof+.25, f"顶板 EL {roof:+.3f}", .176, "start")
  return root


def _chart(storm, study):
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 290 190"})
  SubElement(root, "title").text = "雨水调蓄水量与出界流量"
  SubElement(root, "style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#263746}
.grid{stroke:#bccbd2;stroke-width:.25}.limit{stroke:#a3492d;stroke-width:.55;stroke-dasharray:2 1.4}
.curve{fill:none;stroke-width:1.05}
"""
  cases = study["cases"]
  horizons = min(180, ceil((max(row["duration_min"] for row in cases.values())+90)/30)*30)
  steps = (("volume_m3", "蓄水量 / m³", storm["detention"]["working_capacity_m3"]),
           ("outflow_l_s", "出界流量 / L/s", storm["assumptions"]["outfall"]["permitted_l_s"]))
  color = ("#316984", "#5a7244", "#8a6046")
  for index, (field, title, limit) in enumerate(steps):
    left, right = 26, 276
    top, bottom = 13+index*83, 76+index*83
    ceiling = ceil(max(limit, *(step[field] for row in cases.values() for step in row["detention"]["timeline"]))/10)*10
    def point(minutes, value):
      return left+(right-left)*minutes/horizons, bottom-(bottom-top)*value/ceiling
    _text_at(root, left, top-4, title, 4, "start")
    for tick in range(5):
      mark = ceiling*tick/4
      _, row = point(0, mark)
      _add(root, "line", x1=left, x2=right, y1=row, y2=row, **{"class": "grid"})
      _text_at(root, left-3, row+1.15, f"{mark:g}", 3.1, "end")
    _, mark = point(0, limit)
    _add(root, "line", x1=left, x2=right, y1=mark, y2=mark, **{"class": "limit"})
    _text_at(root, right-1, mark+4.3, f"限值 {limit:g}", 3.3, "end")
    for case_index, (key, row) in enumerate(cases.items()):
      ink = color[case_index % len(color)]
      trace = [point(s["time_s"]/60, s[field]) for s in row["detention"]["timeline"] if s["time_s"]/60 <= horizons]
      _add(root, "polyline", points=" ".join(f"{x:.3f},{y:.3f}" for x, y in trace), stroke=ink, data_case=key,
           **{"class": "curve"})
    for tick in range(0, horizons+1, 15):
      x, _ = point(tick, 0)
      _text_at(root, x, bottom+4.8, str(tick), 3.1)
  _text_at(root, 276, 171, "时间 / min", 3.5, "end")
  labels = {"design": "设计降雨", "longer": "长历时", "shorter": "短历时"}
  for index, (key, row) in enumerate(cases.items()):
    x, y = 28+86*(index % 3), 175+9*(index//3)
    _add(root, "line", x1=x, x2=x+8, y1=y, y2=y, stroke=color[index % len(color)], **{"class": "curve"})
    _text_at(root, x+10, y+1, f"{labels.get(key, key)}  {row['duration_min']:g} min", 3.8, "start")
  root.set("viewBox", f"0 0 290 {190+9*((len(cases)-1)//3)}")
  return root


def build_site_storm_view(context):
  mode = context.scope["view"]
  grade = staged_json(context, GRADING)
  if mode == "grading":
    root = _plan(context, grade)
  else:
    storm = staged_json(context, MODEL)
    if mode == "storm":
      root = _plan(context, grade, storm)
    elif mode == "profile":
      root = _profile(grade, storm, context.scope["start"])
    elif mode == "detail":
      root = _tank_section(grade, storm, staged_json(context, STUDY))
    elif mode == "hydrograph":
      root = _chart(storm, staged_json(context, STUDY))
    else:
      raise ValueError(f"Unknown site storm view {mode}.")
  output = context.build_path / context.output
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode")+"\n", encoding="utf-8")
  return TargetResult("pass", (artifact(context, "site-storm-view", output),))
