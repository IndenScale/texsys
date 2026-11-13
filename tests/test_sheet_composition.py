"""Exercise independently maintained sheet sources through the real build chain."""

import base64
import json
from pathlib import Path
import shutil
from xml.etree import ElementTree as ET

from project_process import build_project
from texsys.dependencies import lock_project, sync_project
from texsys.evidence import file_digest
from texsys.project import initialize_project
from texsys.registry import publish_package


REPO = Path(__file__).parents[1]
SVG = {"svg": "http://www.w3.org/2000/svg"}
SHEETS = Path("build/drawings/sheets")


def read_json(path):
  return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
  path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_project(tmp_path, example):
  root = tmp_path / example
  shutil.copytree(REPO / "examples" / example, root, ignore=shutil.ignore_patterns("build"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(root)
  return root


def run(root, *targets):
  result = build_project(root, targets=list(targets))
  report = read_json(result.report)
  return result, {item["id"]: item for item in report["targets"]}


def sheet_text(path):
  return "".join("".join(node.itertext()) for node in ET.parse(path).findall(".//svg:text", SVG))


def assert_embedded_view(sheet_path, source_path):
  svg = ET.parse(sheet_path).getroot()
  assert svg.attrib["width"] == "420mm"
  assert svg.attrib["height"] == "297mm"
  assert svg.attrib["viewBox"] == "0 0 420 297"
  images = svg.findall(".//svg:image", SVG)
  assert len(images) == 1
  href = images[0].attrib.get("href", images[0].attrib.get("{http://www.w3.org/1999/xlink}href"))
  assert href.startswith("data:image/svg+xml;base64,")
  # Internal SVG IDs/styles remain in their own document, with no external URL.
  assert base64.b64decode(href.split(",", 1)[1]) == source_path.read_bytes()


def test_cold_station_sheets_track_sources_and_only_recompose_changed_text_and_template(tmp_path):
  root = copy_project(tmp_path, "hvac-dynamic-station")
  targets = ("drawing-system", "drawing-notes")
  result, records = run(root, *targets)
  assert result.status == "pass", result.report.read_text()
  assert records["drawing-system"]["dependencies"] == ["system-schematic"]
  assert records["drawing-notes"]["dependencies"] == []
  system = root / SHEETS / "system.svg"
  notes = root / SHEETS / "general-notes.svg"
  upstream = root / "build/drawings/schematics/system.svg"
  assert_embedded_view(system, upstream)
  assert "冷站概念工程示例" in sheet_text(system)
  assert "HVAC-S-001" in sheet_text(system)
  assert "HVAC-N-001" in sheet_text(notes)
  assert not ET.parse(notes).getroot().findall(".//svg:image", SVG)
  evidence = read_json(root / SHEETS / "system-sheet.json")
  assert evidence["metadata"]["drawing_number"] == "HVAC-S-001"
  sources = {item["path"]: item["digest"] for item in evidence["sources"]}
  for relative in ("assets/sheets/system.sheet.json", "assets/sheets/title.json",
                   "assets/sheets/general-notes.txt", "assets/sheets/footer.txt",
                   "build/drawings/schematics/system.svg"):
    assert sources[relative] == file_digest(root / relative)
  baseline = {path: path.read_bytes() for path in (system, notes, upstream)}

  repeated, records = run(root, *targets)
  assert repeated.status == "pass"
  assert all(record["execution"] == "cached" for record in records.values())
  assert {path: path.read_bytes() for path in (system, notes, upstream)} == baseline

  text = root / "assets/sheets/general-notes.txt"
  text.write_text(text.read_text() + "\n6. 设备就位前复核检修净距。\n", encoding="utf-8")
  changed, records = run(root, *targets)
  assert changed.status == "pass", changed.report.read_text()
  assert records["system-schematic"]["execution"] == "cached"
  assert all(records[target]["execution"] == "executed" for target in targets)
  assert upstream.read_bytes() == baseline[upstream]
  assert "设备就位前复核检修净距" in sheet_text(system)
  assert "设备就位前复核检修净距" in sheet_text(notes)

  # A project can maintain its layout separately without forking the renderer.
  template_path = root / "assets/sheets/layout.json"
  template = read_json(REPO / "packages/sheet/src/texsys_sheet/assets/a3-landscape.json")
  frame_path = root / "assets/sheets/frame.json"
  frame = read_json(REPO / "packages/sheet/src/texsys_sheet/assets/frame.json")
  write_json(frame_path, frame)
  next(item for item in template["components"] if item["id"] == "frame")["component"] = "assets/sheets/frame.json"
  write_json(template_path, template)
  source_path = root / "assets/sheets/system.sheet.json"
  source = read_json(source_path)
  source["template"] = "assets/sheets/layout.json"
  write_json(source_path, source)
  local, records = run(root, *targets)
  assert local.status == "pass", local.report.read_text()
  before_layout = system.read_bytes()
  view = next(item for item in template["components"] if item.get("binding") == "main")
  view["box"][2] -= 1
  write_json(template_path, template)
  changed, records = run(root, *targets)
  assert changed.status == "pass", changed.report.read_text()
  assert records["system-schematic"]["execution"] == "cached"
  assert records["drawing-notes"]["execution"] == "cached"
  assert records["drawing-system"]["execution"] == "executed"
  assert system.read_bytes() != before_layout
  assert upstream.read_bytes() == baseline[upstream]

  frame["line_width_mm"] = 0.7
  write_json(frame_path, frame)
  changed, records = run(root, *targets)
  assert changed.status == "pass", changed.report.read_text()
  assert records["system-schematic"]["execution"] == records["drawing-notes"]["execution"] == "cached"
  assert records["drawing-system"]["execution"] == "executed"
  border = ET.parse(system).find("svg:g[@id='frame']/svg:rect", SVG)
  assert border.attrib["stroke-width"] == "0.7"


def test_sheet_missing_content_unbound_fields_and_overflow_keep_last_published_page(tmp_path):
  root = copy_project(tmp_path, "hvac-dynamic-station")
  result, _ = run(root, "drawing-notes")
  assert result.status == "pass", result.report.read_text()
  page = root / SHEETS / "general-notes.svg"
  evidence = root / SHEETS / "general-notes-sheet.json"
  previous = (page.read_bytes(), evidence.read_bytes())
  text = root / "assets/sheets/general-notes.txt"
  original = text.read_text()
  failures = [(None, "Missing or symbolic-link sheet source"),
              ("{{missing_project_field}}", "missing text field missing_project_field"),
              ("工程说明文字\n" * 1000, "Text overflow")]
  for content, message in failures:
    if content is None:
      text.unlink()
    else:
      text.write_text(content, encoding="utf-8")
    rejected, records = run(root, "drawing-notes")
    assert rejected.status == "error", rejected.report.read_text()
    assert any(message in item["message"] for item in records["drawing-notes"]["diagnostics"])
    assert (page.read_bytes(), evidence.read_bytes()) == previous
    text.write_text(original, encoding="utf-8")
  recovered, _ = run(root, "drawing-notes")
  assert recovered.status == "pass"
  assert (page.read_bytes(), evidence.read_bytes()) == previous


def test_sheet_does_not_consume_an_old_public_view_without_its_build_dependency(tmp_path):
  root = copy_project(tmp_path, "hvac-dynamic-station")
  result, _ = run(root, "drawing-system")
  assert result.status == "pass", result.report.read_text()
  page = root / SHEETS / "system.svg"
  previous = page.read_bytes()
  assert (root / "build/drawings/schematics/system.svg").is_file()
  manifest = root / "texsys.toml"
  text = manifest.read_text()
  assert 'dependencies = ["system-schematic"]' in text
  manifest.write_text(text.replace('dependencies = ["system-schematic"]', "dependencies = []"))
  rejected, records = run(root, "drawing-system")
  assert rejected.status == "error", rejected.report.read_text()
  assert set(records) == {"drawing-system"}
  assert any("not staged by a declared target dependency" in item["message"]
             for item in records["drawing-system"]["diagnostics"])
  assert page.read_bytes() == previous


def test_same_sheet_components_accept_an_independent_air_room_view_and_content(tmp_path):
  root = copy_project(tmp_path, "hvac-air-cooled-room")
  result, records = run(root, "drawing-room")
  assert result.status == "pass", result.report.read_text()
  assert records["drawing-room"]["dependencies"] == ["hvac:air-room"]
  page = root / SHEETS / "room.svg"
  assert_embedded_view(page, root / "build/hvac/air-room/floor-plan.svg")
  text = sheet_text(page)
  assert "风冷机房概念工程示例" in text
  assert "HVAC-P-001" in text
  assert "冷站概念工程示例" not in text
  evidence = read_json(root / SHEETS / "room-sheet.json")
  assert evidence["metadata"]["scale"] == "示意，不按比例"


def test_published_sheet_components_execute_after_author_removal_in_two_projects(tmp_path, monkeypatch):
  monkeypatch.setenv("TEXSYS_CACHE_DIR", str(tmp_path / "cache"))
  author = tmp_path / "author"
  shutil.copytree(REPO / "packages/sheet", author, ignore=shutil.ignore_patterns("build", "__pycache__"))
  registry = tmp_path / "registry"
  publish_package(author, registry)
  shutil.rmtree(author)
  pages = []
  for name, project_name in (("project-a", "项目甲"), ("project-b", "项目乙")):
    root = tmp_path / name
    initialize_project(root)
    shutil.copytree(REPO / "examples/hvac-dynamic-station/assets/sheets", root / "assets/sheets")
    title = root / "assets/sheets/notes-title.json"
    metadata = read_json(title)
    metadata["project"] = project_name
    write_json(title, metadata)
    with (root / "texsys.toml").open("a") as stream:
      stream.write('\n[build]\ndefault_targets = ["notes"]\n')
      stream.write('[build.targets.notes]\nusing = "sheet:compose"\n')
      stream.write('scope = { source = "assets/sheets/notes.sheet.json" }\noutput = "build/notes.svg"\n')
      stream.write(f'\n[dependencies]\nsheet = {{ registry = {json.dumps(str(registry))}, version = "0.1.0" }}\n')
    lock_project(root)
    sync_project(root)
    result, _ = run(root, "notes")
    assert result.status == "pass", result.report.read_text()
    text = sheet_text(root / "build/notes.svg")
    assert project_name in text
    pages.append(text)
  assert "项目乙" not in pages[0]
  assert "项目甲" not in pages[1]
