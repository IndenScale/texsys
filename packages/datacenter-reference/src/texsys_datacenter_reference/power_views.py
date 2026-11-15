"""Render the expanded train graph as one-line diagrams with traced links."""

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from texsys.targets import TargetResult

from .electrical import MODEL
from .plans import _add
from .spatial_design import artifact, staged_json


_STYLE = """
text{font-family:Arial,'PingFang SC',sans-serif;fill:#172732}
.wire{stroke:#243947;stroke-width:1;fill:none}
.dc{stroke:#8a4d22;stroke-dasharray:2 1.5}
.device{fill:#f4f7f9;stroke:#283b48;stroke-width:.72}
.bus{fill:#374e5d}
.alternate{fill:#eef3f5}
.terminal{fill:white}
"""


def _name(identity):
  return identity.split("/")[-1]


def _label(root, x, y, text, size=3.25, anchor="middle"):
  _add(root, "text", x=x, y=y, text_anchor=anchor, font_size=size).text = text


def _device(root, identity, x, y, width, caption, style="", title=None, height=10):
  box = _add(root, "rect", x=x-width/2, y=y-height/2, width=width, height=height,
             data_instance=identity, **{"class": f"device {style}"})
  SubElement(box, "title").text = identity
  _label(root, x, y-(.2 if title else -1.1), caption, 3.15)
  if title:
    _label(root, x, y+3.15, title, 2.5)


def _bus(root, identity, x, y, width):
  _add(root, "rect", x=x-width/2, y=y-1, width=width, height=2, data_instance=identity, **{"class": "bus"})


def _breaker(root, identity, x, y):
  body = _add(root, "rect", x=x-1.5, y=y-1.5, width=3, height=3, data_protection_link=identity,
              **{"class": "device terminal"})
  SubElement(body, "title").text = "断路器 " + identity
  _add(root, "line", x1=x-.85, y1=y+.85, x2=x+.85, y2=y-.85, **{"class": "wire"})


def _diagram(root, train, side):
  edges = {item["id"]: item for item in train["edges"]}
  drawn = set()

  def wire(identity, *points):
    edge = edges[identity]
    if identity in drawn:
      raise ValueError(f"Drawn electrical link is repeated: {identity}.")
    drawn.add(identity)
    _add(root, "polyline", points=" ".join(f"{x:g},{y:g}" for x, y in points),
         data_link=identity, data_from=edge["from"], data_to=edge["to"],
         **{"class": "wire dc" if edge["kind"] == "dc-power" else "wire"})

  def param(identity, name):
    return train["nodes"][identity]["parameters"][name]["value"]

  utility, transformer, switchboard, ats, transfer = (
    train[key] for key in ("utility", "transformer", "switchboard", "ats", "transfer")
  )
  _label(root, 6, 7, f"{side} 路供配电单线图", 5.5, "start")
  _device(root, utility, 42, 19, 57, f"{utility}  市电", title=f"{param(transformer,'primary_kv'):g} kV")
  _device(root, transformer, 42, 37, 57, "干式变压器",
          title=f"{param(transformer,'rated_kva'):g} kVA  ·  {param(transformer,'secondary_kv'):g} kV")
  _device(root, switchboard, 42, 55, 57, "低压进线开关柜",
          title=f"{param(switchboard,'rated_a'):g} A  ·  {param(switchboard,'breaking_ka'):g} kA")
  _device(root, ats, 105, 68, 53, "ATS  市电 / 发电互锁",
          title=f"{param(ats,'rated_a'):g} A")
  _device(root, transfer, 105, 86, 53, "转换母线", title="分接设施与 UPS 负荷")
  a, b, c, d = train["utility_route"]
  wire(a, (42, 24), (42, 32))
  wire(b, (42, 42), (42, 50))
  wire(c, (42, 60), (42, 68), (78.5, 68))
  wire(d, (105, 73), (105, 81))
  _breaker(root, b, 42, 46)
  _breaker(root, d, 105, 77)

  gensets = list(train["genset_feeders"].items())
  if len(gensets) > 3:
    raise ValueError("The one-line sheet fits up to three generators per train.")
  for index, (name, feeder) in enumerate(gensets):
    x = 158 + 52 * index
    _device(root, name, x, 19, 44, f"{_name(name)}  发电机", title=f"{param(name,'prime_kw'):g} kW")
    wire(feeder, (x, 24), (x, 37))
    _breaker(root, feeder, x, 30)
  _bus(root, train["generator_bus"], 210, 37, 128)
  _label(root, 209, 43, "机组汇流  一用一备")
  wire(train["standby_route"][0], (153, 38), (153, 68), (131.5, 68))

  # Feeders on the downstream side of the transfer bus split into two voltage domains.
  _bus(root, train["ups_in"], 103, 106, 179)
  _label(root, 7, 102, "UPS 输入母线", anchor="start")
  ups_feed, = (edge["id"] for edge in edges.values() if edge["from"].split(".")[0] == transfer
               and edge["to"].split(".")[0] == train["ups_in"])
  wire(ups_feed, (105, 91), (105, 105))
  for idx, (name, record) in enumerate(train["inverters"].items()):
    if idx >= 5:
      raise ValueError("The one-line sheet fits up to five parallel UPS modules.")
    x = 28 + 36 * idx
    battery = record["battery"]
    _device(root, name, x, 128, 21, _name(name), title=f"{param(name,'rated_kva'):g} kVA")
    _device(root, battery, x+13, 117, 15, f"{param(battery,'energy_kwh'):g} kWh", "alternate", height=6.5)
    feed, output, dc = record["links"]
    wire(feed, (x, 107), (x, 123))
    wire(dc, (x + 13, 120.25), (x + 13, 128), (x + 10.5, 128))
    wire(output, (x, 133), (x, 147))
  _bus(root, train["ups_out"], 103, 148, 179)
  _label(root, 7, 144, "UPS 输出", anchor="start")
  for index, (bus, record) in enumerate(train["feeders"].items()):
    if index >= 4 or len(record["terminals"]) > 4:
      raise ValueError("The one-line sheet fits up to four busways with four IT taps each.")
    x = 33 + 46 * index
    _device(root, bus, x, 165, 35, bus, title=f"{param(bus,'rated_a'):g} A")
    wire(record["feed"], (x, 149), (x, 160))
    _breaker(root, record["feed"], x, 154.5)
    stack = list(record["terminals"].items())
    _add(root, "polyline", points=f"{x:g},170 {x:g},173 {x-15:g},173 {x-15:g},{180+9*(len(stack)-1):g}",
         **{"class": "wire"})
    for branch_index, (terminal, link) in enumerate(stack):
      y = 180 + 9 * branch_index
      _device(root, terminal, x+3, y, 20, terminal, "terminal", height=6.2)
      wire(link, (x-15, y), (x-7, y))
      _breaker(root, link, x-11, y)

  for index, (name, link) in enumerate(train["facility_feeds"].items()):
    if index >= 4:
      raise ValueError("The one-line sheet fits up to four non-UPS facility branches.")
    y = 103 + 16*index
    _device(root, name, 237, y, 43, name, "alternate", height=7)
    wire(link, (131.5, 86), (199, 86), (199, y), (215.5, y))
  _label(root, 237, 94, "设施负荷  ·  ATS 后直供")
  shared = train["shared_bus"]
  gy = 103 + 16*list(train["facility_feeds"]).index(shared)
  for index, (name, link) in enumerate(train["shared_feeds"].items()):
    if index >= 8:
      raise ValueError("The one-line sheet fits up to eight common auxiliary taps.")
    y = 151 + 9*index
    _device(root, name, 275, y, 31, name, "terminal", height=6.4)
    wire(link, (258.5, gy), (294, gy), (294, y), (290.5, y))
  _label(root, 237, 164, "共用辅助负荷", anchor="middle")
  _label(root, 8, 215, "棕色虚线为直流电池支路，方框斜线为参与校核的断路器。", anchor="start")
  if drawn != edges.keys():
    raise ValueError(f"The {side} one-line omitted power connections: {sorted(edges.keys()-drawn)}.")


def build_power_view(context):
  if set(context.scope) != {"train"} or context.scope["train"] not in {"A", "B"}:
    raise ValueError("The power drawing scope must select train A or B.")
  side = context.scope["train"]
  root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 301 220", "data-train": side})
  SubElement(root, "title").text = f"AIDC {side} 路供配电单线图"
  SubElement(root, "style").text = _STYLE
  _diagram(root, staged_json(context, MODEL)["trains"][side], side)
  output = context.build_path / (context.output or f"drawings/views/power-one-line-{side.lower()}.svg")
  if output.suffix != ".svg":
    raise ValueError("One-line drawing output must use the SVG extension.")
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(root)
  output.write_text(tostring(root, encoding="unicode") + "\n", encoding="utf-8")
  return TargetResult(artifacts=(artifact(context, "one-line-diagram", output),))
