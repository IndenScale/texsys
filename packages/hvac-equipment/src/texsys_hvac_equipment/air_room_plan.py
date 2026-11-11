"""Draw the air room's dimensioned plan from the same inputs as its USD scene."""

from html import escape
from itertools import pairwise


def write_plan(room, path, width, length, grid):
  scale = min(69, 850 / width, 625 / length)
  left, top = 99, 160
  right, bottom = left + width * scale, top + length * scale
  page_width, page_height = right + 320, bottom + 104
  layout = room.layout
  body = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {page_width:g} {page_height:g}">']
  body.append("""
<style>
  text {font-family: "PingFang SC","Noto Sans CJK SC","Microsoft Yahei",sans-serif;fill:#1c2e43}
  .muted{fill:#516579} .light{fill:#f4f9fc} .badge{font-size:11px;font-weight:600}
  .axis{stroke:#48759a;stroke-width:1;stroke-dasharray:5 5} .dim{stroke:#526f88;stroke-width:1}
</style>
<defs>
  <pattern id="grille" width="6" height="6" patternUnits="userSpaceOnUse">
    <path d="M0 1H6" stroke="#3595ad" stroke-width="1.5"/>
  </pattern>
  <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
    <path d="M0 0L6 3L0 6Z" fill="#b35435"/>
  </marker>
</defs>""")

  def rect(x, y, w, h, fill, extra=""):
    body.append(f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" fill="{fill}" {extra}/>')

  def text(x, y, value, size=13, extra=""):
    body.append(f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" {extra}>{escape(str(value))}</text>')

  def line(x1, y1, x2, y2, extra=""):
    body.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" {extra}/>')

  def circle(x, y, label):
    body.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="11" fill="#f6fafc" stroke="#48759a"/>')
    text(x, y + 4, label, 11, 'text-anchor="middle"')

  def xy(x, y):
    return left + x * scale, bottom - y * scale

  def plan_rect(bounds, fill, extra=""):
    x0, y0, x1, y1 = bounds
    x, y = xy(x0, y1)
    rect(x, y, (x1 - x0) * scale, (y1 - y0) * scale, fill, extra)

  def box_rect(box, fill, extra=""):
    return plan_rect((*box.origin[:2], *box.maximum[:2]), fill, extra)

  rect(0, 0, page_width, page_height, "#f3f7fa")
  text(32, 31, "TEXSYS  /  HVAC DESIGN EXAMPLE", 12, 'letter-spacing="2.1" font-weight="600"')
  text(32, 67, "风冷机房 · 设备与送风平面", 27, 'font-weight="650"')
  text(33, 92, f"{layout['frame']['id']}  /  {width:g} × {length:g} m  /  完成面 {layout['raised_floor']['finished_elevation_m']:+.2f} m", 13, 'class="muted"')
  rect(left, top, width * scale, length * scale, "#fbfcfd")
  for key in ("HotNorth", "HotSouth"):
    plan_rect(layout["zones"][key]["bounds_m"], "#f8e1d6")
  plan_rect(layout["zones"]["ColdAisle"]["bounds_m"], "#d9eff4")
  pitch = layout["raised_floor"]["pitch_m"]
  for i in range(1, round(width / pitch)):
    x, _ = xy(i * pitch, 0)
    line(x, top, x, bottom, 'stroke="#dce5ec" stroke-width=".7"')
  for i in range(1, round(length / pitch)):
    _, y = xy(0, i * pitch)
    line(left, y, right, y, 'stroke="#dce5ec" stroke-width=".7"')
  for box in room.grilles:
    box_rect(box, "url(#grille)", 'stroke="#5eafbd" stroke-width=".5"')

  for key, value in grid.x.items():
    x, _ = xy(value, 0)
    line(x, top - 17, x, bottom + 30, 'class="axis"')
    circle(x, bottom + 43, key)
  for key, value in grid.y.items():
    _, y = xy(0, value)
    line(left - 32, y, right + 12, y, 'class="axis"')
    circle(left - 44, y, key)

  for column in (box for owner, box in room.objects["Steel"] if owner.startswith("COL-") and box.identity == "BasePlate"):
    box_rect(column, "#3a5372", 'stroke="#203b58"')
  for name, box in room.equipment.items():
    x, y = xy(box.origin[0] + box.size[0] / 2, box.origin[1] + box.size[1] / 2)
    is_cooling = name in layout["cooling_units"]
    box_rect(box, "#277c88" if is_cooling else "#293d54", 'stroke="#eff7fc" stroke-width="1"')
    text(x, y + 4, name, 9, 'text-anchor="middle" class="light" font-weight="600"')
    if is_cooling:
      text(x, y + 20, "↓ 下送风", 9, 'text-anchor="middle" class="light"')
  for row in layout["rack_rows"]:
    for name in row["members"]:
      box = room.equipment[name]
      offset = 0 if row["front"] == "+Y" else box.size[1]
      x, y = xy(box.origin[0] + box.size[0] / 2, box.origin[1] + offset)
      direction = 1 if row["front"] == "+Y" else -1
      line(x, y + direction * 3, x, y + direction * 20, 'stroke="#b35435" marker-end="url(#arrow)"')
  for key, label in (("ColdAisle", "冷通道  /  地板送风"), ("HotNorth", "热通道  /  上部回风"),
                     ("HotSouth", "热通道  /  上部回风")):
    x0, y0, x1, y1 = layout["zones"][key]["bounds_m"]
    x, y = xy((x0 + x1) / 2, (y0 + y1) / 2)
    rect(x - 70, y - 12, 140, 23, "#f8fbfd", 'rx="3" opacity=".94"')
    text(x, y + 4, label, 12, 'text-anchor="middle"')

  for first, second in (((left, top), (left, bottom)), ((left, top), (right, top)), ((left, bottom), (right, bottom))):
    line(*first, *second, 'stroke="#29415b" stroke-width="2"')
  door_start, door_stop = layout["door"]["opening_y_m"]
  _, low = xy(width, door_start)
  _, high = xy(width, door_stop)
  line(right, top, right, high, 'stroke="#29415b" stroke-width="2"')
  line(right, low, right, bottom, 'stroke="#29415b" stroke-width="2"')
  line(right, low, right + 20, low, 'stroke="#526f88" stroke-dasharray="3 3"')
  text(right + 12, (low + high) / 2 + 4, "入口", 11)

  line(left, top - 42, right, top - 42, 'class="dim"')
  text((left + right) / 2, top - 46, f"{width:g} m", 12, 'text-anchor="middle"')
  line(left, top - 19, right, top - 19, 'class="dim"')
  for value in grid.x.values():
    x, _ = xy(value, 0)
    line(x, top - 45, x, top - 15, 'class="dim"')
  for start, stop in pairwise(grid.x.values()):
    x, _ = xy((start + stop) / 2, 0)
    text(x, top - 23, f"{stop - start:g} m", 11, 'text-anchor="middle"')
  text(26, (top + bottom) / 2, f"{length:g} m", 11,
       f'text-anchor="middle" transform="rotate(-90 26 {(top + bottom) / 2:.3f})"')

  sidebar = right + 62
  text(sidebar, top + 5, "系统组成", 16, 'font-weight="650"')
  counts = [
    ("#293d54", f"{sum(len(row['members']) for row in layout['rack_rows'])} 台标准化机柜"),
    ("#277c88", f"{len(layout['cooling_units'])} 台下送风冷却单元"),
    ("#3a5372", "钢柱及轴网"),
    ("#d9eff4", f"冷通道 / {len(room.grilles)} 块送风板"),
    ("#f8e1d6", "热通道 / 回风"),
  ]
  for index, (color, label) in enumerate(counts):
    y = top + 37 + index * 30
    rect(sidebar, y - 12, 16, 16, color, 'rx="2"')
    text(sidebar + 24, y, label)
  text(sidebar, top + 220, "气流路径", 16, 'font-weight="650"')
  for index, label in enumerate(("冷却单元 ↓ 静压箱", "送风板 ↑ 冷通道", "机柜前进后出 → 热通道", "机房上部 → 单元回风")):
    text(sidebar, top + 250 + index * 25, label, 13, 'class="muted"')
  text(sidebar, top + 380, "空间口径", 16, 'font-weight="650"')
  rack = layout["rack"]
  for index, label in enumerate((
    f"{rack['rack_units']}U · {rack['mounting_width_in']} 英寸安装体系",
    f"{' × '.join(f'{n:g}' for n in rack['envelope_m'])} m 机柜外包络",
    f"{pitch * 1000:g} × {pitch * 1000:g} mm 地板模数",
    "方案假设  /  轴网与位置以米计",
  )):
    text(sidebar, top + 409 + index * 23, label, 12, 'class="muted"')
  text(left, bottom + 77, "HVAC  /  AIR-COOLED ROOM", 11, 'letter-spacing="1.5" class="muted"')
  body.append("</svg>")
  path.write_text("\n".join(body) + "\n", encoding="utf-8")
