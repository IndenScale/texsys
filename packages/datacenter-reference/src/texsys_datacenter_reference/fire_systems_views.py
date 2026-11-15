"""Compose sheets from the fire routes, pod geometry and sequence evidence."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .architecture import MODEL as ARCH_MODEL
from .fire_systems import MODEL
from .plans import _STYLE, _add, _axes, _line, _north_and_scale, _rect, _text, _xy
from .spatial_design import MODELS, artifact, staged_json


_FIRE_STYLE = """
.fire-wall{fill:#3a4652}
.fire-pod{fill:#ecf0f3;stroke:#6c7985;stroke-width:.064}
.ring{fill:none;stroke:#a53829;stroke-width:.092}
.device{fill:white;stroke:#a53829;stroke-width:.076}
.manual{fill:#a53829;stroke:white;stroke-width:.048}
.io{fill:white;stroke:#695294;stroke-width:.074}
.coverage{fill:none;stroke:#bc8477;stroke-dasharray:.21 .18;stroke-width:.048}
.removed{fill:#fff2ee;stroke:#ab3227;stroke-width:.091;stroke-dasharray:.18 .10}
"""


def _svg(title, viewbox, *, metric=True):
  attrs = {"xmlns": "http://www.w3.org/2000/svg", "viewBox": viewbox}
  if metric:
    attrs.update({"data-coordinate-unit": "m", "data-units-per-meter": "1"})
  root = Element("svg", attrs)
  SubElement(root, "title").text = title
  SubElement(root, "style").text = _STYLE + _FIRE_STYLE
  return root


def _alarm(context, model):
  arch = staged_json(context, ARCH_MODEL)
  grid = staged_json(context, f"{MODELS}/grid.json")
  bounds = _xy(arch["hall"])
  x0, y0, x1, y1 = bounds
  root = _svg("主机房火灾自动报警平面", f"{x0-6:g} {-y1-5:g} {x1-x0+11:g} {y1-y0+13:g}")
  _rect(root, bounds, "zone")
  _axes(root, grid, bounds)
  _north_and_scale(root, bounds)
  for tag, wall in arch["walls"].items():
    if not tag.startswith("W-over-"):
      _rect(root, _xy(wall), "fire-wall", data_wall=tag)
  for name, pod in model["pods"].items():
    a, b, c, d = _xy(pod["host_bounds"])
    _rect(root, (a, b, c, d), "fire-pod", data_pod=name)
    _text(root, (a+c)/2, b+.44, name, .43)
  for key, loop in model["rings"].items():
    for tag, cell in loop["cells"].items():
      a, b, c, d = cell["bounds_m"]
      cls = "coverage" if tag in loop["devices"] else "removed"
      _rect(root, (a+.035, b+.035, c-.035, d-.035), cls, data_detection_cell=tag)
    for name, row in loop["runs"].items():
      a, b = row["points_m"]
      if a[:2] != b[:2]:
        _line(root, *a[:2], *b[:2], "ring").set("data-alarm-run", name)
    for tag, row in loop["devices"].items():
      x, y, _ = row["point_m"]
      kind, address = row["kind"], row["address"]
      if kind in {"hall-smoke", "pod-interface"}:
        node = _add(root, "circle", cx=x, cy=-y, r=.125, data_fire_device=tag,
                    **{"class": "device" if kind == "hall-smoke" else "io"})
        if kind == "hall-smoke":
          _text(root, x-.08, y+.31, f"{address:02}", .32, "end")
      else:
        half = .18 if kind == "panel" else .11
        node = _rect(root, (x-half, y-half, x+half, y+half), "manual" if kind == "manual-call" else "device",
                     data_fire_device=tag)
        anchor = "end" if x < (x0+x1)/2 else "start"
        dx = -.41 if anchor == "end" else .39
        caption = tag if kind == "panel" else f"{tag.split('-')[0]} {address:02}"
        _text(root, x+dx, y-.09, caption, .40, anchor)
      SubElement(node, "title").text = tag
    room = arch["rooms"][key]
    _text(root, (x0+x1)/2, room["maximum_m"][1]-.71, f"{key} 报警回路", .52)
  _text(root, (x0+x1)/2, y1+.82, "主机房报警环线与舱内火警监视接口", .70)
  return root


def _pod(model, name):
  pod = model["pods"][name]
  x0, y0, z0 = pod["host_bounds"]["minimum_m"]
  x1, y1, z1 = pod["host_bounds"]["maximum_m"]
  length, depth, height = x1-x0, y1-y0, z1-z0
  gap = depth + 1.08
  root = _svg(f"{name} 舱内灭火设备及支管布置", f"-.8 {-gap-height-.78:g} {length+1.55:g} {gap+height+1.62:g}")
  SubElement(root, "style").text = ".ring{stroke-width:.023}.fire-pod,.device,.manual{stroke-width:.021}.dimension{stroke-width:.018}"
  for offset, upper in ((0, depth), (gap, height)):
    _rect(root, (0, offset, length, offset+upper), "fire-pod", data_pod=name)
  _text(root, length/2, depth+.34, "平面", .17)
  _text(root, length/2, gap+height+.30, "沿长边投影", .17)
  cab = pod["cabinet"]
  for axis, offset, origin in ((1, 0, y0), (2, gap, z0)):
    a, b = cab["minimum_m"], cab["maximum_m"]
    _rect(root, (a[0]-x0, a[axis]-origin+offset, b[0]-x0, b[axis]-origin+offset), "manual", data_cabinet=pod["controller"])
    _text(root, b[0]-x0+.12, (a[axis]+b[axis])/2-origin+offset, f"AG-{name}", .14, "start")
    for tag, row in pod["pipes"].items():
      a, b = row["points_m"]
      if (a[0], a[axis]) != (b[0], b[axis]):
        _line(root, a[0]-x0, a[axis]-origin+offset, b[0]-x0, b[axis]-origin+offset, "ring").set("data-agent-run", tag)
    for label, row in pod["nozzles"].items():
      x, y = row[0]-x0, row[axis]-origin+offset
      _add(root, "circle", cx=x, cy=-y, r=.097, data_nozzle=label, **{"class": "device"})
      _text(root, x, y-.26, label, .14)
    for label, row in pod["detectors"].items():
      x, y = row[0]-x0, row[axis]-origin+offset
      _rect(root, (x-.067, y-.06, x+.067, y+.06), "device", data_detector=label)
      _text(root, x+.15, y-.05, label, .13, "start")
  _line(root, 0, -.42, length, -.42, "dimension")
  for x in (0, length):
    _line(root, x-.04, -.46, x+.04, -.38, "dimension")
  _text(root, length/2, -.59, f"{1000*length:.0f} mm", .15)
  for offset, size in ((0, depth), (gap, height)):
    _line(root, -.34, offset, -.34, offset+size, "dimension")
    for y in (offset, offset+size):
      _line(root, -.38, y-.04, -.30, y+.04, "dimension")
    mid_y = -(offset+size/2)
    _add(root, "text", x=-.46, y=mid_y, font_size=.15, text_anchor="middle",
         transform=f"rotate(-90 -.46 {mid_y:g})").text = f"{1000*size:.0f}"
  return root


def _effect(context):
  report = staged_json(context, f"{MODELS}/fire-systems-study.json")["cause_effect"]
  root = _svg("舱内火警与灭火联动", "0 0 304 212", metric=False)
  SubElement(root, "style").text = """
text{fill:#23323c}.block{fill:#f2f5f6;stroke:#48626c;stroke-width:.64}
.guard{fill:#f8efdf;stroke:#a56f31;stroke-width:.64}
.edge{fill:none;stroke:#46575e;stroke-width:.7}.prohibit{fill:#fbede9;stroke:#a34b36;stroke-width:.68}
"""

  def block(x, y, width, height, strings, style="block"):
    _add(root, "rect", x=x-width/2, y=y, width=width, height=height, **{"class": style})
    for index, value in enumerate(strings):
      _add(root, "text", x=x, y=y+height/2+(index+.5-len(strings)/2)*6+1.4,
           text_anchor="middle", font_size=4.3).text = value

  def edge(x, y, y2):
    _add(root, "path", d=f"M{x},{y} V{y2} m-1.9,-2.6 l1.9,2.6 1.9,-2.6", **{"class": "edge"})

  _add(root, "text", x=152, y=13, text_anchor="middle", font_size=6.3).text = "方舱气体灭火联动许可"
  block(104, 29, 112, 17, ["舱内两只探测器确认／本地手动请求"])
  block(241, 29, 86, 17, ["主机房报警只报送状态"])
  edge(104, 46, 58)
  block(104, 58, 112, 22, ["发出舱内预警", "请求停风与关闭风阀"])
  edge(104, 80, 94)
  feedback_names = {"fan_stopped": "风机已停", "damper_closed": "阀已关", "actuator_healthy": "执行回路正常"}
  first = next(iter(report["controller_bindings"].values()))
  feedback = "、".join(feedback_names[key] for key in first["feedbacks"])
  block(104, 94, 112, 32, [f"预警后延时 {report['delay_s']:g} s", feedback, "停止输入未动作"], "guard")
  edge(104, 126, 149)
  block(104, 149, 112, 25, ["联动模拟允许喷放", "许可按本地回路独立判定"])
  block(241, 103, 86, 25, ["监视线断开时报故障", "舱内联动独立判定"], "prohibit")
  _add(root, "text", x=152, y=199, text_anchor="middle", font_size=4.4).text = "见联动工况表和各方舱控制器接口。"
  return root


def build_fire_systems_view(context):
  scope = dict(context.scope)
  if scope == {"view": "cause-effect"}:
    root = _effect(context)
  else:
    model = staged_json(context, MODEL)
    if scope == {"view": "alarm"}:
      root = _alarm(context, model)
    elif scope.keys() == {"view", "pod"} and scope["view"] == "pod":
      root = _pod(model, scope["pod"])
    else:
      raise ValueError("Select the alarm plan, local pod or cause-effect sheet.")
  output = context.build_path / context.output
  if output.suffix != ".svg":
    raise ValueError("Fire system sheets require an SVG view destination.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "fire-systems-view", output),))
