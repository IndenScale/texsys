"""Draw the roof load path, rod elevations and governing demands."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .fws_buffer_views import _canvas
from .fws_transient_views import _text_at
from .lateral_bracing import MODEL as BRACES
from .lateral_study import STUDY
from .plans import _add, _axes, _line, _rect, _text
from .roof import MODEL as ROOF
from .roof_transfer import MODEL
from .spatial_design import artifact, staged_json

_STYLE = """
.edge{stroke:#657987;fill:none;stroke-width:.10}
.upper{stroke:#964b21;stroke-width:.12;fill:none}
.lower{stroke:#2a6187;stroke-width:.12;stroke-dasharray:.35 .16;fill:none}
.power-port{fill:#e8cf99;stroke:#6a5840;stroke-width:.050}
.water-port{fill:#b8d9d9;stroke:#3a6568;stroke-width:.050}
.support{fill:white;stroke:#374b58;stroke-width:.066}
.deck{fill:#dadfe1;stroke:#596974;stroke-width:.012}
.beam{fill:#d4dde1;stroke:#495e6a;stroke-width:.012}
.reserve{fill:#f5efe5;fill-opacity:.92;stroke:#917758;stroke-width:.011;stroke-dasharray:.045 .028}
.axis-detail{fill:none;stroke:#677982;stroke-width:.009;stroke-dasharray:.039 .023}
.detail-upper{stroke:#964b21;stroke-width:.018;fill:#dca47a}
.detail-lower{stroke:#2a6187;stroke-width:.018;fill:#a6c9dd}
.node-pin{fill:white;stroke:#384d5b;stroke-width:.011}
"""


def _plan(model, roof, bracing):
  points = model["nodes"]
  x0, x1 = (fn(x for x, _ in points.values()) for fn in (min, max))
  y0, y1 = (fn(y for _, y in points.values()) for fn in (min, max))
  root = _canvas((x0-4.73, y0-5.4, x1+3.43, y1+4.), "主机房屋面水平支撑与集力平面")
  SubElement(root, "style").text = _STYLE
  _axes(root, roof["grid"], (x0, y0, x1, y1))
  for key, row in model["collectors"].items():
    a, b = [points[node] for node in row["nodes"]]
    _line(root, *a, *b, "edge").set("data-collector", key)
  for key, row in model["ties"].items():
    a, b = row["points_m"]
    _line(root, *a[:2], *b[:2], row["role"]).set("data-rod", key)
  for key, row in roof["openings"].items():
    x, y, _ = row["point_m"]
    _add(root, "circle", cx=x, cy=-y, r=.115, data_roof_opening=key,
         **{"class": "power-port" if row["service"] == "power" else "water-port"})
  supported = {column for row in bracing["bays"].values() for column in row["columns"]}
  for key in sorted(supported):
    x, y = points[key]
    _rect(root, (x-.17, y-.17, x+.17, y+.17), "support", data_column=key)
  for key, row in model["panels"].items():
    southwest, southeast, northeast, northwest = [points[node] for node in row["nodes"]]
    x = (southwest[0]+southeast[0])/2
    y = (northwest[1]+northeast[1])/2+1.18
    _text(root, x, y, key.removeprefix("RF-"), .54)
  for i, (role, label) in enumerate((("upper", "上层拉杆"), ("lower", "下层拉杆"), ("edge", "轴线集力梁"))):
    entry = x0+i*8.50
    _line(root, entry, y1+2.3, entry+1.15, y1+2.3, role)
    _text(root, entry+1.35, y1+2.12, label, .49, "start")
  _text(root, x0, y1+3.14, "方框为纵横向支撑接入柱。小圆为机电屋面穿越。", .48, "start")
  return root


def _detail(model, roof):
  top = model["collector_elevation_m"]
  lower, upper = (model["levels_m"][key] for key in ("lower", "upper"))
  roof_bottom = roof["section"]["deck_base_m"]
  root = _canvas((-.6, top-.57, 4.57, roof_bottom+1.02), "屋面支撑节点与双层错位剖面")
  SubElement(root, "style").text = _STYLE
  _text(root, -.27, roof_bottom+.65, "集力梁端高度投影", .097, "start")
  _text(root, 2.45, roof_bottom+.65, "双层拉杆交叉剖面", .097, "start")
  depth = model["collector_depth_m"]
  _rect(root, (-.23, top-depth/2, .22, top+depth/2), "beam")
  thickness = roof["section"]["layers"]["deck"]["eave_thickness_m"]
  _rect(root, (-.30, roof_bottom, 1.72, roof_bottom+thickness), "deck")
  _line(root, 0., top-.27, 0., roof_bottom+.33, "axis-detail")
  _text(root, -.21, roof_bottom+.32, "轴线", .076, "start")
  _line(root, -.28, top, 1.75, top, "axis-detail")
  diameter = model["assumptions"]["diameter_m"]
  _text(root, .54, roof_bottom+.28, f"钢承板底 EL {roof_bottom:+.2f}", .084, "start")
  setback = model["assumptions"]["joint_setback_m"]
  for name, elevation in (("upper", upper), ("lower", lower)):
    _rect(root, (.02, elevation-.026, setback, elevation+.024), "reserve", data_joint_space=name)
    _line(root, setback, elevation, 1.67, elevation, "detail-"+name)
    _add(root, "circle", cx=setback, cy=-elevation, r=.021, **{"class": "node-pin"})
    _add(root, "circle", cx=2.93, cy=-elevation, r=diameter/2, **{"class": "detail-"+name})
    _line(root, 2.93+diameter/2, elevation, 3.73, elevation, "axis-detail")
    _text(root, 3.78, elevation-.017, f"{elevation:+.2f}", .060, "start")
  clearance = upper-lower-diameter
  _text(root, 2.45, roof_bottom+.28, f"净距 {clearance*1000:.0f} mm   杆径 Ø{diameter*1000:.0f}", .084, "start")
  offset = model["assumptions"]["plan_offset_m"]["lower"]
  _text(root, -.18, top-.37, f"端部预留 {setback*1000:.0f} mm   下层平面偏置 {offset*1000:.0f} mm", .077, "start")
  _text(root, 2.45, top-.37, "端板与拉杆接头由节点专项确定。", .077, "start")
  return root


def _table(study, model):
  cases = study["cases"]
  size = max(190, 100+len(model["panels"])*10)
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 300 {size}"})
  SubElement(root, "title").text = "屋面支撑索力与节点控制需求"
  SubElement(root, "style").text = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#253642}
.header{fill:#e7edf1;stroke:#637684;stroke-width:.38}
.rule{stroke:#aabac0;stroke-width:.32}
"""
  _text_at(root, 14, 14, "屋面水平传力控制值", 6.0, "start")
  _text_at(root, 14, 25, f"候选风与静力替代地震 {study['intended_cases']} 组。"
           "杆件总拉力含初始预张拉。", 3.9, "start")
  if not cases:
    _text_at(root, 14, 48, "当前布置未形成稳定传力体系，见侧向校核诊断。", 4.4, "start")
    return root
  _add(root, "rect", x=14, y=37, width=272, height=10, **{"class": "header"})
  for x, label in ((20, "屋面板跨"), (79, "上层最小 kN"), (128, "上层最大 kN"),
                   (181, "下层最小 kN"), (231, "下层最大 kN")):
    _text_at(root, x, 44.2, label, 3.9, "start")
  for idx, panel in enumerate(model["panels"]):
    controls = []
    for role in ("upper", "lower"):
      key = next(name for name, row in model["ties"].items() if row["panel"] == panel and row["role"] == role)
      demand = [row["roof_members"][key]["total_kn"] for row in cases.values()]
      controls.extend((min(demand), max(demand)))
    y = 56+9*idx
    _text_at(root, 20, y, panel, 3.9, "start")
    for x, value in zip((92, 141, 194, 244), controls, strict=True):
      _text_at(root, x, y, f"{value:.2f}", 3.9)
    _add(root, "line", x1=14, x2=286, y1=y+3.9, y2=y+3.9, **{"class": "rule"})
  lead_case, lead_node, lead = max(((case, node, row["moment_kn_m"]) for case, state in cases.items()
                                  for node, row in state["roof_joints"].items()), key=lambda row: row[2])
  end = 62+9*len(model["panels"])
  _text_at(root, 14, end, f"最大节点偏心力矩包络 {lead:.2f} kN·m，节点 {lead_node}。", 4.0, "start")
  _text_at(root, 14, end+11, f"控制工况 {lead_case}。完整杆力、位移及节点需求见计算表。", 3.8, "start")
  _text_at(root, 14, end+22, "预张拉实测、节点板、螺栓与防松构造仍待专项复核。", 3.8, "start")
  return root


def build_roof_transfer_view(context):
  model = staged_json(context, MODEL)
  mode = context.scope["view"]
  if mode == "plan":
    root = _plan(model, staged_json(context, ROOF), staged_json(context, BRACES))
  elif mode == "detail":
    root = _detail(model, staged_json(context, ROOF))
  elif mode == "schedule":
    root = _table(staged_json(context, STUDY), model)
  else:
    raise ValueError(f"Unknown roof transfer view {mode}.")
  path = context.build_path/context.output
  path.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  path.write_text(tostring(root, encoding="unicode")+"\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "roof-transfer-view", path),))
