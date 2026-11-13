"""Boundary checks for self-contained SVG and component typography."""

from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from texsys_sheet.compiler import _lines, _svg, _text, compile_sheet


SVG = "http://www.w3.org/2000/svg"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def test_wrapped_character_must_fit_the_text_column():
  # The narrow Latin character fits; the following CJK character must not be
  # accepted merely because it moves to a new line.
  with pytest.raises(ValueError, match="narrower|overflow|fit"):
    _lines("a中", 0.7, 1)


def test_component_label_cannot_emit_invalid_xml_text():
  root = ET.Element("svg")
  with pytest.raises(ValueError, match="control|XML|character"):
    _text(root, "项目\x00名称", (0, 0, 100, 10), {}, "title.project.label")


def test_plain_text_indentation_and_internal_spaces_survive_svg_rendering():
  source = SimpleNamespace(components=[(
    {"id": "notes", "component": "sheet:notes", "box": [5, 5, 100, 40], "binding": "notes"},
    {"schema_version": 1, "kind": "text"},
  )], records={})
  document = {"template": {"paper_mm": [120, 60]},
              "metadata": {"drawing_number": "N-01", "title": "说明"},
              "texts": {"notes": "    1.  分项说明"}, "views": {}, "source": "assets/notes.json"}
  svg, _ = compile_sheet(source, document)
  root = ET.fromstring(svg)
  text = root.find(f".//{{{SVG}}}text")
  assert text.text == "    1.  分项说明"
  # SVG's default whitespace handling collapses these spaces unless explicitly
  # preserved; textLength alone would stretch the collapsed string instead.
  assert root.get(XML_SPACE) == "preserve" or text.get(XML_SPACE) == "preserve"


def test_svg_validation_allows_local_references_but_rejects_external_and_invalid_geometry():
  valid = f'''<svg xmlns="{SVG}" viewBox="-10 0 200 100">
    <defs><linearGradient id="shade"><stop offset="0" stop-color="black"/></linearGradient></defs>
    <rect width="100" height="100" fill="url(#shade)"/>
  </svg>'''.encode()
  assert _svg(valid, "view.svg") == (-10, 0, 200, 100)
  for fragment in (
    '<image href="https://example.test/external.svg"/>',
    '<rect style="fill:url(https://example.test/fill.svg#shade)"/>',
    '<text onclick="alert(1)">invalid</text>',
  ):
    with pytest.raises(ValueError, match="external|event"):
      _svg(f'<svg xmlns="{SVG}" viewBox="0 0 10 10">{fragment}</svg>'.encode(), "view.svg")
  for viewbox in ("0 0 0 10", "0 0 -1 10", "0 0 NaN 10", "0 0 inf 10"):
    with pytest.raises(ValueError, match="viewBox"):
      _svg(f'<svg xmlns="{SVG}" viewBox="{viewbox}"/>'.encode(), "view.svg")


def test_svg_entity_declarations_cannot_bypass_validation_using_utf16():
  svg = f'''<?xml version="1.0" encoding="UTF-16"?>
<!DOCTYPE svg [<!ENTITY caption "external declaration">]>
<svg xmlns="{SVG}" viewBox="0 0 100 100"><text>&caption;</text></svg>'''.encode("utf-16")
  with pytest.raises(ValueError, match="UTF-8|encoding|declaration|entit"):
    _svg(svg, "view.svg")
