"""Compile independent paper components, content and staged views into SVG.

All sheet coordinates and text heights are millimetres. Source views retain
their own coordinate systems; fit is explicitly not an engineering scale.
"""

import base64
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import unicodedata
import xml.etree.ElementTree as ET

from texsys.project import inspect_project

SVG = "http://www.w3.org/2000/svg"
FONT = "Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif"
ASSETS = Path(__file__).parent / "assets"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")


def _xml_text(value, label):
  if any(not (ord(c) in {9, 10, 13} or 0x20 <= ord(c) <= 0xD7FF or
              0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
    raise ValueError(f"{label}: text contains an invalid XML character.")
  return value


def _object(value, allowed, label, required=()):
  if not isinstance(value, dict) or value.keys() - set(allowed) or set(required) - value.keys():
    raise ValueError(f"{label}: expected fields {', '.join(allowed)}; required {', '.join(required)}.")
  return value


def _version(value, label):
  if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
    raise ValueError(f"{label}: schema_version must be 1.")


def _number(value, label, *, positive=True):
  if type(value) not in {int, float} or not math.isfinite(value) or (value <= 0 if positive else value < 0):
    raise ValueError(f"{label}: expected a finite {'positive' if positive else 'nonnegative'} number.")
  return value


def _box(value, label):
  if not isinstance(value, list) or len(value) != 4:
    raise ValueError(f"{label}: box must be [x, y, width, height] in mm.")
  return tuple(_number(n, label, positive=i > 1) for i, n in enumerate(value))


def _inside(box, width, height, label):
  x, y, w, h = box
  if x + w > width + 1e-7 or y + h > height + 1e-7:
    raise ValueError(f"{label}: component extends outside its paper/cell boundary.")


def _overlap(a, b):
  return a[0] < b[0]+b[2]-1e-7 and b[0] < a[0]+a[2]-1e-7 and a[1] < b[1]+b[3]-1e-7 and b[1] < a[1]+a[3]-1e-7


def _canonical(value):
  if (not isinstance(value, str) or not value or "\\" in value or "\0" in value
      or PurePosixPath(value).is_absolute() or any(p in {"", ".", ".."} for p in value.split("/"))):
    raise ValueError(f"Expected a canonical project-relative path: {value!r}.")
  return value


def _digest(data):
  return "sha256:" + hashlib.sha256(data).hexdigest()


class SheetSource:
  def __init__(self, context):
    self.context = context
    self.info = inspect_project(context.root)
    self.records = {}
    self.project_paths = set()
    self.components = []

  def read(self, reference, role, *, package=False):
    if package and isinstance(reference, str) and reference.startswith("sheet:"):
      name = reference.removeprefix("sheet:")
      if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError(f"Invalid sheet component reference: {reference!r}.")
      path = ASSETS / f"{name}.json"
    else:
      _canonical(reference)
      path = self.context.root / reference
      if not any(path.is_relative_to(self.context.root / directory) for directory in (self.info.source, self.info.assets)):
        raise ValueError(f"Sheet source must be inside project source/assets: {reference}.")
      self.project_paths.add(reference)
    if path.resolve() != path or not path.is_file():
      raise ValueError(f"Missing or symbolic-link sheet source: {reference}.")
    data = path.read_bytes()
    self.records[(reference, role)] = {"path": reference, "digest": _digest(data), "role": role}
    return data.decode("utf-8")

  def json(self, reference, role, *, package=False):
    try:
      return json.loads(self.read(reference, role, package=package))
    except (json.JSONDecodeError, UnicodeError) as error:
      raise ValueError(f"{reference}: invalid UTF-8 JSON: {error}") from error

  def load(self):
    scope = _object(dict(self.context.scope), ("source",), "sheet scope", ("source",))
    doc = self.json(scope["source"], "sheet")
    _object(doc, ("schema_version", "template", "metadata", "texts", "views"), scope["source"],
            ("schema_version", "template", "metadata"))
    _version(doc, scope["source"])
    template = self.json(doc["template"], "template", package=True)
    _object(template, ("schema_version", "paper_mm", "components"), "template", ("schema_version", "paper_mm", "components"))
    _version(template, "template")
    paper = template["paper_mm"]
    if not isinstance(paper, list) or len(paper) != 2 or any(_number(n, "paper_mm") > 2000 for n in paper):
      raise ValueError("paper_mm: expected width and height, each at most 2000 mm.")
    if not isinstance(template["components"], list) or not template["components"]:
      raise ValueError("Template must contain components.")
    identifiers = set()
    occupied = []
    bindings = {"texts": set(), "views": set()}
    for item in template["components"]:
      _object(item, ("id", "component", "box", "binding"), "template component", ("id", "component", "box"))
      identifier = item["id"]
      if not isinstance(identifier, str) or not NAME.fullmatch(identifier) or identifier in identifiers:
        raise ValueError(f"Invalid/duplicate component identity: {identifier!r}.")
      identifiers.add(identifier)
      definition = self.json(item["component"], "component", package=True)
      if not isinstance(definition, dict):
        raise ValueError(f"{item['component']}: component must be an object.")
      _version(definition, item["component"])
      kind = definition.get("kind")
      if kind not in {"frame", "title-block", "text", "view"}:
        raise ValueError(f"{identifier}: unknown component kind {kind!r}.")
      box = _box(item["box"], identifier)
      _inside(box, *paper, identifier)
      if kind != "frame":
        for other, previous in occupied:
          if _overlap(box, previous):
            raise ValueError(f"Overlapping sheet components: {identifier}, {other}.")
        occupied.append((identifier, box))
      if kind in {"text", "view"}:
        binding = item.get("binding")
        if not isinstance(binding, str) or not NAME.fullmatch(binding):
          raise ValueError(f"{identifier}: requires a named content binding.")
        bindings["texts" if kind == "text" else "views"].add(binding)
      elif "binding" in item:
        raise ValueError(f"{identifier}: {kind} does not take a binding.")
      self.components.append((item, definition))
    metadata = self.json(doc["metadata"], "metadata")
    if not isinstance(metadata, dict) or not metadata or any(
      not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", k)
      or not isinstance(v, str) or not v.strip() for k, v in metadata.items()
    ):
      raise ValueError("Sheet metadata must contain non-empty text fields.")
    for name, value in metadata.items():
      _xml_text(value, name)
    for required in ("project", "title", "drawing_number", "revision", "stage", "scale", "sheet"):
      if required not in metadata:
        raise ValueError(f"Missing sheet metadata field: {required}.")
    for kind in ("texts", "views"):
      entries = doc.get(kind, {})
      if not isinstance(entries, dict) or set(entries) != bindings[kind]:
        raise ValueError(f"{kind}: bindings must exactly match the template: {sorted(bindings[kind])}.")
    texts = {name: self.read(path, "text") for name, path in doc.get("texts", {}).items()}
    for name, text in texts.items():
      if not text.strip():
        raise ValueError(f"Empty text source for binding {name}.")
      _substitute(text, metadata, name)
    views = doc.get("views", {})
    for name, view in views.items():
      _object(view, ("path", "scale"), f"view {name}", ("path", "scale"))
      _canonical(view["path"])
      path = PurePosixPath(view["path"])
      if not path.is_relative_to(PurePosixPath(self.info.build)) or path.suffix != ".svg":
        raise ValueError(f"{name}: view must reference an SVG build artifact.")
      if view["scale"] != "fit":
        raise ValueError(f"{name}: only fit is supported; no engineering scale is inferred.")
    allowed_scales = {"NTS", "示意，不按比例"} if views else {"不适用", "NTS", "示意，不按比例"}
    if metadata["scale"] not in allowed_scales:
      raise ValueError("metadata.scale must declare NTS / 示意，不按比例 (or 不适用 on a text-only sheet).")
    return {"template": template, "metadata": metadata, "texts": texts, "views": views,
            "source": scope["source"]}

  def view(self, reference):
    relative = PurePosixPath(reference).relative_to(PurePosixPath(self.info.build))
    path = self.context.build_path / relative
    # Only the runner's verified dependency snapshot is visible to this target.
    # Never fall back to a published file left by an older build.
    if path.resolve() != path or not path.is_file():
      raise ValueError(f"View is not staged by a declared target dependency: {reference}.")
    data = path.read_bytes()
    viewbox = _svg(data, reference)
    self.records[(reference, "view")] = {"path": reference, "digest": _digest(data), "role": "view"}
    return data, viewbox


def _substitute(text, metadata, label):
  _xml_text(text, label)
  def replace(match):
    name = match.group(1)
    if name not in metadata:
      raise ValueError(f"{label}: missing text field {name}.")
    return metadata[name]
  result = PLACEHOLDER.sub(replace, text)
  if "{{" in result or "}}" in result:
    raise ValueError(f"{label}: invalid/unresolved text field; expected {{{{field}}}}.")
  if any(unicodedata.category(c) == "Cc" and c not in "\n\t\r" for c in result):
    raise ValueError(f"{label}: unsupported control character in text.")
  return result.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)


def _svg(data, label):
  # Embedded images are inert and have a separate CSS/ID scope. Reject active
  # and external content as it would break the self-contained artifact contract.
  try:
    text = data.decode("utf-8-sig")
  except UnicodeError as error:
    raise ValueError(f"{label}: SVG must be UTF-8.") from error
  declaration = re.match(r"\s*<\?xml\s+[^?]*encoding=['\"]([^'\"]+)['\"]", text, re.I)
  if declaration and declaration[1].lower() not in {"utf-8", "utf8"}:
    raise ValueError(f"{label}: SVG must declare UTF-8 encoding.")
  if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
    raise ValueError(f"{label}: SVG document/entity declarations are unsupported.")
  try:
    root = ET.fromstring(text)
  except ET.ParseError as error:
    raise ValueError(f"{label}: invalid SVG: {error}") from error
  if root.tag != f"{{{SVG}}}svg":
    raise ValueError(f"{label}: expected an SVG root in the SVG namespace.")
  values = re.split(r"[\s,]+", root.get("viewBox", "").strip())
  try:
    box = tuple(float(v) for v in values)
  except ValueError as error:
    raise ValueError(f"{label}: SVG requires a finite viewBox.") from error
  if len(box) != 4 or not all(math.isfinite(v) for v in box) or min(box[2:]) <= 0:
    raise ValueError(f"{label}: SVG requires a finite positive viewBox.")
  for element in root.iter():
    tag = element.tag.split("}")[-1]
    if tag in {"script", "foreignObject", "animate", "animateTransform", "animateMotion", "set"}:
      raise ValueError(f"{label}: active SVG content is unsupported: {tag}.")
    values = [element.text or ""] if tag == "style" else []
    for key, value in element.attrib.items():
      local = key.split("}")[-1]
      if local.lower().startswith("on"):
        raise ValueError(f"{label}: SVG event handlers are unsupported.")
      if local in {"href", "src"} and not value.startswith("#"):
        raise ValueError(f"{label}: SVG external/image resource is unsupported: {local}.")
      if local == "base":
        raise ValueError(f"{label}: SVG base URLs are unsupported.")
      values.append(value)
    for value in values:
      if "@import" in value.lower() or "\\" in value:
        raise ValueError(f"{label}: SVG imported/escaped CSS is unsupported.")
      for url in re.findall(r"url\s*\((.*?)\)", value, re.I | re.S):
        if not url.strip(" \t\r\n\"'").startswith("#"):
          raise ValueError(f"{label}: SVG external CSS resource is unsupported.")
  return box


def _n(value):
  return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _element(parent, tag, **attributes):
  return ET.SubElement(parent, tag, {name.replace("_", "-"): str(value) for name, value in attributes.items()})


def _advance(char):
  if unicodedata.combining(char):
    return 0
  if unicodedata.east_asian_width(char) in {"W", "F"}:
    return 1
  if char in "MW@%":
    return 0.9
  return 0.62


def _lines(text, width, size):
  lines = []
  for paragraph in text.rstrip().split("\n"):
    current = ""
    for char in paragraph:
      if _advance(char) * size > width + 1e-7:
        raise ValueError("Text area is narrower than a single character.")
      if sum(_advance(c) for c in current + char) * size > width + 1e-7:
        if not current:
          raise ValueError("Text area is narrower than a single character.")
        split = current.rfind(" ")
        if split > 0:
          lines.append(current[:split])
          current = current[split+1:] + char
        else:
          lines.append(current)
          current = char
      else:
        current += char
    lines.append(current.rstrip())
  return lines


def _text(parent, text, box, definition, label, *, field=False):
  _xml_text(text, label)
  x, y, width, height = box
  size = _number(definition.get("font_size_mm", 3.5), f"{label}.font_size_mm")
  line_height = _number(definition.get("line_height_mm", 5), f"{label}.line_height_mm")
  padding = _number(definition.get("padding_mm", 2), f"{label}.padding_mm", positive=False)
  if line_height < size * 1.15 or width <= 2*padding or height <= 2*padding:
    raise ValueError(f"{label}: text needs positive inner space and line height >= 1.15 × font size.")
  lines = _lines(text, width-2*padding, size)
  if len(lines)*line_height > height-2*padding+1e-7:
    raise ValueError(f"Text overflow in {label}: {len(lines)} lines need {_n(len(lines)*line_height)} mm; available {_n(height-2*padding)} mm.")
  for i, line in enumerate(lines):
    if not line:
      continue
    attributes = {"x": _n(x+padding), "y": _n(y+padding+i*line_height+size),
                  "font_size": _n(size), "fill": "#18232a", "stroke": "none"}
    # Fixed advances make the horizontal contract independent of browser font
    # metrics; host glyph shape still requires review. No silent truncation.
    node = _element(parent, "text", **attributes)
    node.set("textLength", _n(sum(_advance(c) for c in line)*size))
    node.set("lengthAdjust", "spacingAndGlyphs")
    if field:
      node.set("data-field", label)
    node.text = line
  return {"line_count": len(lines), "font_size_mm": size, "line_height_mm": line_height,
          "text": text, "box_mm": list(box)}


def compile_sheet(source, document):
  paper = document["template"]["paper_mm"]
  metadata = document["metadata"]
  root = ET.Element("svg", {"xmlns": SVG, "width": f"{_n(paper[0])}mm", "height": f"{_n(paper[1])}mm",
                             "viewBox": f"0 0 {_n(paper[0])} {_n(paper[1])}", "font-family": FONT,
                             "data-drawing-number": metadata["drawing_number"], "data-unit": "mm", "xml:space": "preserve"})
  _element(root, "title").text = f"{metadata['drawing_number']} · {metadata['title']}"
  _element(root, "desc").text = "工程图页草稿；排版检查通过不代表工程接受或正式签章。"
  _element(root, "rect", width=_n(paper[0]), height=_n(paper[1]), fill="white")
  elements = []
  for item, definition in source.components:
    kind, identifier = definition["kind"], item["id"]
    x, y, width, height = box = _box(item["box"], identifier)
    group = _element(root, "g", id=identifier, data_component=item["component"], data_kind=kind)
    record = {"id": identifier, "component": item["component"], "kind": kind, "box_mm": list(box)}
    common = ("schema_version", "kind")
    if kind == "frame":
      _object(definition, (*common, "line_width_mm"), identifier)
      line = _number(definition.get("line_width_mm", 0.5), "line_width_mm")
      _element(group, "rect", x=_n(x), y=_n(y), width=_n(width), height=_n(height), fill="none", stroke="#18232a", stroke_width=_n(line))
    elif kind == "text":
      _object(definition, (*common, "font_size_mm", "line_height_mm", "padding_mm"), identifier)
      name = item["binding"]
      text = _substitute(document["texts"][name], metadata, name)
      record.update(binding=name, **_text(group, text, box, definition, identifier))
    elif kind == "title-block":
      _object(definition, (*common, "cells", "line_width_mm", "font_size_mm", "label_size_mm"), identifier, ("cells",))
      cells = definition["cells"]
      if not isinstance(cells, list) or not cells:
        raise ValueError(f"{identifier}: title block requires cells.")
      occupied = []
      fields = set()
      for cell in cells:
        _object(cell, ("box", "field", "label"), identifier, ("box", "field", "label"))
        local = _box(cell["box"], identifier)
        _inside(local, width, height, identifier)
        if any(_overlap(local, other) for other in occupied):
          raise ValueError(f"{identifier}: overlapping title-block cells.")
        occupied.append(local)
        name = cell["field"]
        if not isinstance(name, str) or name not in metadata or name in fields:
          raise ValueError(f"{identifier}: missing/duplicate title-block field {name!r}.")
        if not isinstance(cell["label"], str) or not cell["label"].strip():
          raise ValueError(f"{identifier}: title-block cell needs a label.")
        fields.add(name)
        cx, cy, cw, ch = local
        line = _number(definition.get("line_width_mm", 0.25), "line_width_mm")
        _element(group, "rect", x=_n(x+cx), y=_n(y+cy), width=_n(cw), height=_n(ch), fill="none", stroke="#18232a", stroke_width=_n(line))
        label_size = _number(definition.get("label_size_mm", 1.8), "label_size_mm")
        label_height = label_size*1.25+1
        _text(group, cell["label"], (x+cx, y+cy, cw, label_height),
              {"font_size_mm": label_size, "line_height_mm": label_size*1.25, "padding_mm": 0.5}, f"{identifier}.{name}.label")
        size = _number(definition.get("font_size_mm", 2.8), "font_size_mm")
        _text(group, _substitute(metadata[name], metadata, name), (x+cx, y+cy+label_height, cw, ch-label_height),
              {"font_size_mm": size, "line_height_mm": size*1.25, "padding_mm": 0.5}, name, field=True)
      record["fields"] = sorted(fields)
    else:
      _object(definition, (*common, "padding_mm"), identifier)
      name = item["binding"]
      view = document["views"][name]
      data, viewbox = source.view(view["path"])
      padding = _number(definition.get("padding_mm", 1), "padding_mm", positive=False)
      if min(width, height) <= 2*padding:
        raise ValueError(f"{identifier}: view has no inner space.")
      image = _element(group, "image", x=_n(x+padding), y=_n(y+padding), width=_n(width-2*padding), height=_n(height-2*padding))
      image.set("preserveAspectRatio", "xMidYMid meet")
      image.set("href", "data:image/svg+xml;base64,"+base64.b64encode(data).decode("ascii"))
      record.update(binding=name, source=view["path"], source_viewbox=list(viewbox), scale_mode="fit", engineering_scale=None)
    elements.append(record)
  report = {"schema_version": 1, "deliverable": "sheet", "source": document["source"], "paper_mm": paper,
            "metadata": metadata, "composition": "valid", "engineering_acceptance": "not-evaluated",
            "font": {"family": FONT, "sizing": "fixed-advance-mm", "embedded": False},
            "sources": sorted(source.records.values(), key=lambda item: (item["role"], item["path"])),
            "components": elements,
            "limits": ["Views use fit, not engineering scale; existing view text scales with the view.",
                       "Host font glyphs require visual review; fonts are not embedded.",
                       "Composition checks do not validate engineering prose, signatures or construction readiness.",
                       "Text is UTF-8 plain text with explicit line breaks and metadata fields; no rich text or automatic pagination."]}
  _element(root, "metadata").text = json.dumps({"source": document["source"], "sources": report["sources"]}, ensure_ascii=False, sort_keys=True)
  ET.indent(root)
  return ET.tostring(root, encoding="unicode") + "\n", report
