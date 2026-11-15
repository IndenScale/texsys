"""Draw calibrated roof plans, sections and details from the compiled assembly."""

from xml.etree.ElementTree import SubElement, indent, tostring

from texsys.targets import TargetResult

from .fws_buffer_views import _canvas
from .plans import _add, _line, _rect, _text
from .roof import LAYERS, MODEL, roof_surfaces
from .spatial_design import MODELS, artifact, staged_json

_STYLE = """
.roof-field{fill:#f6f5f0;stroke:#33434c;stroke-width:.10}
.plan-bay{fill:none;stroke:#697279;stroke-width:.06;stroke-dasharray:.26 .16}
.ridge{stroke:#34464f;stroke-width:.14;stroke-dasharray:.47 .26}
.slope{stroke:#4d6b76;stroke-width:.075}
.purlin{fill:#566877;stroke:none}
.gutter{fill:#dcebf1;stroke:#37637b;stroke-width:.07}
.flashing-water{fill:#a2cbd0;stroke:#2e666c;stroke-width:.065}
.flashing-power{fill:#edc99a;stroke:#795020;stroke-width:.065}
.clear{fill:#fff;stroke:#303e43;stroke-width:.039}
.roof-line{stroke:#415b69;stroke-width:.045;stroke-dasharray:.26 .13}
.section-outline{stroke:#253841;stroke-width:.027;fill:#f7f7f4}
.deck{fill:#879fae;stroke:#32434b;stroke-width:.011}
.insulation{fill:#eddfb9;stroke:#6b5e49;stroke-width:.010}
.membrane{fill:#516c74;stroke:#27424a;stroke-width:.012}
.detail-flashing{fill:#a3cacc;stroke:#216268;stroke-width:.009}
.detail-pipe{stroke:#407584;fill:#e0eef2;stroke-width:.010}
.detail .deck,.detail .insulation,.detail .detail-pipe,.detail .detail-flashing{stroke-width:.004}
.detail .membrane{stroke-width:.002}
.detail-datum{stroke:#415b69;stroke-width:.0035;stroke-dasharray:.04 .025}
.detail-dimension{stroke:#34444f;stroke-width:.0035}
.drainage-zone{fill:#e9f1f3;stroke:#73919c;stroke-width:.050}
"""

_TITLES = {"envelope": "主机房屋面分区及预留", "secondary": "主机房屋面檩条定位", "rain": "屋面雨水分区与排水接口"}


def _arrow(root, x, y, dx, dy):
  _line(root, x, y, x+dx, y+dy, "slope")
  length = (dx*dx+dy*dy)**.5
  u, v = dx/length, dy/length
  for hand in (-1, 1):
    _line(root, x+dx, y+dy, x+dx-.37*u+hand*.19*v, y+dy-.37*v-hand*.19*u, "slope")


def _axes(root, roof):
  x0, y0, x1, y1 = roof["outline_xy_m"]
  for axis, x in roof["grid"]["x"].items():
    _line(root, x, y0-1.08, x, y1+.52, "grid").set("data-axis", axis)
    _add(root, "circle", cx=x, cy=-(y0-1.12), r=.34, **{"class": "axis"})
    _text(root, x, y0-1.24, axis, .35)
  for axis, y in roof["grid"]["y"].items():
    _line(root, x0-1.16, y, x1+.75, y, "grid").set("data-axis", axis)
    _add(root, "circle", cx=x0-1.18, cy=-y, r=.34, **{"class": "axis"})
    _text(root, x0-1.18, y-.12, axis, .35)


def _plan(roof, mode, storm=None):
  x0, y0, x1, y1 = roof["outline_xy_m"]
  ridge = roof["section"]["ridge_y_m"]
  x_mid = (x0+x1)/2
  svg = _canvas((x0-2.2, y0-2.5, x1+2.2, y1+2.9), _TITLES[mode])
  SubElement(svg, "style").text = _STYLE
  _rect(svg, roof["outline_xy_m"], "roof-field", data_assembly="hall-roof")
  if mode == "rain":
    for key, row in storm["outlets"].items():
      bounds = row["catchment_xy_m"]
      _rect(svg, bounds, "drainage-zone", data_catchment=key)
      x = (bounds[0]+bounds[2])/2
      y = (bounds[1]+bounds[3])/2
      _text(svg, x, y+.20, f"{key}  {row['catchment_m2']:.1f} m²", .43)
      _text(svg, x, y-.52, f"{row['design_flow_l_s']:.2f} L/s", .37)
  else:
    for key, row in roof["panels"].items():
      rect = row["extent_xy_m"]
      _rect(svg, rect, "plan-bay", data_panel=key)
      if mode == "envelope":
        _text(svg, (rect[0]+rect[2])/2, (rect[1]+rect[3])/2-.75, key, .40)
  _axes(svg, roof)
  _line(svg, x0, ridge, x1, ridge, "ridge").set("data_ridge", "roof-ridge")
  for key, row in roof["openings"].items():
    if mode == "rain":
      continue
    style = "flashing-water" if row["service"] == "FWS" else "flashing-power"
    _rect(svg, row["curb_xy_m"], style, data_opening=key, data_run=row["run"])
    _rect(svg, row["hole_xy_m"], "clear", data_clear_opening=key)
  if mode == "secondary":
    for key, row in roof["purlins"].items():
      a, b, _ = row["minimum_m"]
      c, d, _ = row["maximum_m"]
      _rect(svg, (a, b, c, d), "purlin", data_purlin=key, data_supports=" ".join(row["supports"]))
    for key, station in roof["grid"]["y"].items():
      _line(svg, x0, station, x1, station, "ridge").set("data_support_axis", key)
    _text(svg, x0+.35, y1+1.22, "檩条沿南北跨，三排轴线处支承于主体钢梁", .38, "start")
  else:
    for x in (x_mid-8, x_mid+8):
      for sign in (-1, 1):
        y = ridge+sign*1.23
        _arrow(svg, x, y, 0, sign*1.35)
        _text(svg, x+.33, y+sign*.67, f"{100*roof['section']['slope']:.1f}%", .36, "start")
    for key, row in roof["drainage"].items():
      a, b, c = row["gutter_invert_m"]
      half = roof["assumptions"]["drainage"]["gutter_width_m"]/2
      _rect(svg, (min(a[0], c[0]), a[1]-half, max(a[0], c[0]), a[1]+half), "gutter", data_gutter=key)
      _add(svg, "circle", cx=b[0], cy=-b[1], r=.21, data_leader=key, **{"class": "gutter"})
      if mode == "rain":
        _arrow(svg, (a[0]+b[0])/2, a[1], 1.1 if b[0]>a[0] else -1.1, 0)
        _text(svg, b[0], b[1]+.65, key, .32)
    _text(svg, x_mid+.25, ridge+.32, f"屋脊 EL +{roof_surfaces(roof, ridge)[-1]:.3f}", .36, "start")
  return svg


def _poly(svg, points, css, **data):
  return _add(svg, "polygon", points=" ".join(f"{y:.6f},{-z:.6f}" for y, z in points), **{"class": css}, **data)


def _layers(svg, roof, stations, *, hole=None):
  for left, right in stations:
    low, high = [roof_surfaces(roof, point) for point in (left, right)]
    for index, name in enumerate(LAYERS):
      _poly(svg, ((left, low[index]), (right, high[index]), (right, high[index+1]), (left, low[index+1])),
            name, data_layer=name, **({"data-opening-context": hole} if hole else {}))


def _section(roof):
  _, y0, _, y1 = roof["outline_xy_m"]
  ridge = roof["section"]["ridge_y_m"]
  base = roof["section"]["steel_top_m"]
  svg = _canvas((y0-2.95, -.25, y1+3.3, 6.9), "主机房屋面横剖面")
  SubElement(svg, "style").text = _STYLE
  _layers(svg, roof, [(y0, ridge), (ridge, y1)])
  girder = roof["section"]["primary_girder_m"]
  _rect(svg, (min(roof["grid"]["y"].values()), base, max(roof["grid"]["y"].values()),
              roof["section"]["deck_base_m"]), "section-outline", data_section="purlin")
  for key, y in roof["grid"]["y"].items():
    half = girder["width"]/2
    web = girder["web"]/2
    bottom, flange = base-girder["depth"], girder["flange"]
    _rect(svg, (y-half, bottom, y+half, bottom+flange), "deck", data_girder_axis=key)
    _rect(svg, (y-web, bottom+flange, y+web, base-flange), "deck")
    _rect(svg, (y-half, base-flange, y+half, base), "deck")
    _line(svg, y, .2, y, 6.2, "grid").set("data-axis", key)
    _text(svg, y, 6.36, key, .28)
  _line(svg, y0-.55, base, y1+.55, base, "roof-line")
  _text(svg, ridge, base-.65, f"钢梁顶 EL +{base:.3f}", .25)
  _text(svg, ridge, roof_surfaces(roof, ridge)[-1]+.25, f"屋面最高点 EL +{roof_surfaces(roof, ridge)[-1]:.3f}", .26)
  for key, row in roof["drainage"].items():
    if not key.endswith("-W"):
      continue
    _, y, z = row["leader_top_m"]
    diameter = roof["assumptions"]["drainage"]["leader_outside_diameter_m"]
    _rect(svg, (y-diameter/2, row["handoff_m"][2], y+diameter/2, z), "gutter", data_leader=key)
    half = roof["assumptions"]["drainage"]["gutter_width_m"]/2
    _rect(svg, (y-half, z, y+half, z+roof["assumptions"]["drainage"]["gutter_depth_m"]), "gutter", data_gutter=key)
    _text(svg, y, .08, key, .24)
  return svg


def _detail(roof, name):
  row = roof["openings"][name]
  _, y, z = row["point_m"]
  _, lo, _, hi = row["hole_xy_m"]
  _, outer_lo, _, outer_hi = row["curb_xy_m"]
  left, right = y-.48, y+.85
  svg = _canvas((left-.1, z-.48, right+.85, z+.48), f"{name} 屋面预留剖面")
  svg.set("class", "detail")
  SubElement(svg, "style").text = _STYLE
  _layers(svg, roof, [(left, lo), (hi, right)], hole=name)
  route = row["service_envelope"]
  _rect(svg, (route["minimum_m"][1], max(z-.39, route["minimum_m"][2]),
              route["maximum_m"][1], min(z+.193, route["maximum_m"][2])), "detail-pipe", data_run=row["run"])
  for a, b in ((outer_lo, lo), (hi, outer_hi)):
    lower_a, lower_b = [roof_surfaces(roof, point)[-1]-.012 for point in (a, b)]
    _poly(svg, ((a, lower_a), (b, lower_b), (b, row["curb_top_m"]), (a, row["curb_top_m"])),
          "detail-flashing", data_flashing=name)
  deck = roof["section"]["deck_base_m"]
  _line(svg, left-.04, deck, right+.42, deck, "detail-datum")
  _line(svg, lo, z+.23, hi, z+.23, "detail-dimension")
  for cut in (lo, hi):
    _line(svg, cut-.010, z+.219, cut+.010, z+.241, "detail-dimension")
  _text(svg, y, z+.272, f"净宽 {1000*row['width_m']:.0f}", .027)
  _text(svg, right+.02, row["curb_top_m"]+.02, f"上翻顶 EL +{row['curb_top_m']:.3f}", .025, "start")
  _text(svg, right+.02, deck-.06, f"压型钢承板底 EL +{deck:.3f}", .025, "start")
  _text(svg, left, z+.36, f"{name}    {row['compartment']}    {row['run']}", .032, "start")
  return svg


def build_roof_view(context):
  roof = staged_json(context, MODEL)
  mode = context.scope["view"]
  if mode == "rain":
    svg = _plan(roof, mode, staged_json(context, f"{MODELS}/roof-drainage.json"))
  elif mode in ("envelope", "secondary"):
    svg = _plan(roof, mode)
  elif mode == "section":
    svg = _section(roof)
  elif mode == "detail":
    svg = _detail(roof, context.scope["opening"])
  else:
    raise ValueError(f"Unknown roof view {mode}.")
  output = context.build_path / context.output
  output.parent.mkdir(parents=True, exist_ok=True)
  indent(svg)
  output.write_text(tostring(svg, encoding="unicode")+"\n", encoding="utf-8")
  return TargetResult("pass", (artifact(context, "roof-view", output),))
