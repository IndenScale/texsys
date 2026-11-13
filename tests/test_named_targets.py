from dataclasses import replace
import json
from pathlib import Path
import shutil
from xml.etree import ElementTree

import pytest
from pxr import Usd

from project_process import build_project
from texsys.dependencies import lock_project
from texsys.project import ProjectError
from texsys.targets import TargetDefinition, resolve_targets

REPO = Path(__file__).parents[1]
SVG = {"svg": "http://www.w3.org/2000/svg"}


def copy_project(tmp_path, example):
  root = tmp_path / example
  shutil.copytree(REPO / "examples" / example, root, ignore=shutil.ignore_patterns("build"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(root)
  return root


def test_named_targets_resolve_independent_scopes_and_keep_registered_dependencies():
  dependency = TargetDefinition("example:model", "example", "1", lambda context: None)
  generator = replace(dependency, id="example:plan", dependencies=(dependency.id,), named_output=True,
                      profiles=("draft", "review"), default_profile="draft")

  class Package:
    def targets(self):
      return (dependency, generator)

  manifest = {"layout": {"build": "artifacts"}, "build": {"default_targets": ["room", "level"], "targets": {
    "room": {"using": generator.id, "scope": {"room": "101"}, "output": "artifacts/room.svg"},
    "level": {"using": generator.id, "scope": {"level": "L01"}, "output": "artifacts/floor.svg", "profile": "review"},
  }}}
  selection = resolve_targets((Package(),), manifest)
  assert [item.id for item in selection.resolved] == [dependency.id, "room", "level"]
  assert resolve_targets((Package(),), manifest, ("level",)).named_outputs == {
    "room.svg": "room", "floor.svg": "level",
  }
  room, level = selection.resolved[1:]
  assert room.definition == level.definition == generator
  assert (room.scope, room.output, room.profile) == ({"room": "101"}, "room.svg", "draft")
  assert (level.scope, level.output, level.profile) == ({"level": "L01"}, "floor.svg", "review")
  assert room.dependencies == level.dependencies == (dependency.id,)

  entry = manifest["build"]["targets"]["room"]
  for output in ("artifacts/../outside.svg", "src/room.svg", "artifacts/runs/x.svg", "artifacts/Studio/x.svg",
                 "artifacts/.cache/x.svg"):
    entry["output"] = output
    with pytest.raises(ProjectError, match="output"):
      resolve_targets((Package(),), manifest)
  entry["output"] = "artifacts/FLOOR.svg"
  with pytest.raises(ProjectError, match="overlaps"):
    resolve_targets((Package(),), manifest)


def test_named_target_dependencies_extend_the_generator_and_reject_invalid_graphs():
  model = TargetDefinition("example:model", "example", "1", lambda context: None)
  drawing = replace(model, id="example:drawing", named_output=True)
  sheet = replace(drawing, id="example:sheet", dependencies=(model.id,))

  class Package:
    def targets(self):
      return (model, drawing, sheet)

  entries = {
    "view": {"using": drawing.id, "output": "build/view.svg"},
    "sheet": {"using": sheet.id, "output": "build/sheet.svg",
              "dependencies": ["view", model.id, "view"]},
  }
  manifest = {"build": {"default_targets": ["sheet"], "targets": entries}}
  selection = resolve_targets((Package(),), manifest)
  assert [target.id for target in selection.resolved] == [model.id, "view", "sheet"]
  assert selection.resolved[-1].dependencies == (model.id, "view")
  assert selection.resolved[0].resolution_source == "dependency of sheet"

  entries["sheet"]["dependencies"] = []
  assert resolve_targets((Package(),), manifest).resolved[-1].dependencies == (model.id,)
  for invalid in ("view", [None], [""], {"view": True}):
    entries["sheet"]["dependencies"] = invalid
    with pytest.raises(ProjectError, match="dependencies must be an array"):
      resolve_targets((Package(),), manifest)
  entries["sheet"]["dependencies"] = ["missing"]
  with pytest.raises(ProjectError, match="Unknown build target: missing"):
    resolve_targets((Package(),), manifest)
  entries["sheet"]["dependencies"] = ["view"]
  entries["view"]["dependencies"] = ["sheet"]
  with pytest.raises(ProjectError, match="dependency cycle: sheet -> view -> sheet"):
    resolve_targets((Package(),), manifest)


def test_floor_plans_project_declared_room_level_and_cross_level_work_package(tmp_path):
  root = copy_project(tmp_path, "datacenter-hybrid-unit")
  zone_target = "plan-zone-l01-unit-01-01"
  targets = ["plan-room-cooling", "plan-level-01", "plan-lot-mep-01", zone_target, "building-scene"]
  result = build_project(root, targets=targets)
  assert result.status == "pass"
  report = json.loads(result.report.read_text())
  assert [item["id"] for item in report["targets"]][-len(targets):] == targets
  scene = Usd.Stage.Open(str(root / "build/scenes/building.usda"))
  assert scene.GetDefaultPrim().GetChild("L01").IsValid()
  assert (root / "build/scenes/building.json").is_file()
  assert (root / "build/datacenter/building-study.json").is_file()

  def read(path, attribute="data-room-id"):
    svg = ElementTree.parse(root / "build/drawings" / path).getroot()
    sheets = {}
    for sheet in svg.findall("svg:g", SVG):
      sheets[sheet.attrib["data-level-id"]] = {
        shape.attrib[attribute]: shape.find("svg:rect", SVG).attrib for shape in sheet.findall("svg:g", SVG)
      }
    return sheets

  room_path = "rooms/L01/cooling.svg"
  zone_path = "zones/L01/unit-01-01.svg"
  lot_path = "packages/MEP-01.svg"
  room = read(room_path)
  assert {level: set(rooms) for level, rooms in room.items()} == {"L01": {"L01/Cooling"}}
  assert {key: float(room["L01"]["L01/Cooling"][key]) for key in ("x", "y", "width", "height")} == {
    "x": 50, "y": -42, "width": 8, "height": 18,
  }
  floor = read("plans/L01.svg")
  assert set(floor) == {"L01"}
  assert set(floor["L01"]) == {"L01/Hall", "L01/Services", "L01/Cooling", "L01/Electrical", "L01/Liquid-Collection"}
  lot = read(lot_path)
  assert {level: set(rooms) for level, rooms in lot.items()} == {
    "L01": {"L01/Cooling", "L01/Electrical"}, "L02": {"L02/Cooling"},
  }
  zone = read(zone_path, "data-zone-id")
  assert {level: set(zones) for level, zones in zone.items()} == {"L01": {"L01/Unit-01-01"}}
  assert {key: float(zone["L01"]["L01/Unit-01-01"][key]) for key in ("x", "y", "width", "height")} == {
    "x": 4, "y": -12, "width": 8, "height": 8,
  }

  repeat = build_project(root, targets=targets)
  assert [item["execution"] for item in json.loads(repeat.report.read_text())["targets"]][-len(targets):] == ["cached"] * len(targets)

  source = root / "src/building.study.json"
  data = json.loads(source.read_text())
  data["layout"]["reserves"]["Cooling"]["size_m"][0] = 7.5
  data["layout"]["origin_m"][0] = 4.5
  source.write_text(json.dumps(data))
  updated = build_project(root, targets=targets)
  assert updated.status == "pass"
  assert read(room_path)["L01"]["L01/Cooling"]["width"] == "7.5"
  assert read(lot_path)["L02"]["L02/Cooling"]["width"] == "7.5"
  assert read(zone_path, "data-zone-id")["L01"]["L01/Unit-01-01"]["x"] == "4.5"
  stable_file = root / "build/drawings" / room_path
  published = stable_file.read_bytes()
  rejected = build_project(root, targets=[targets[0]], scope={"room": "L01/missing"})
  assert rejected.status == "error"
  assert stable_file.read_bytes() == published
  assert "Unknown floor plan room" in rejected.report.read_text()

  different_level = build_project(root, targets=["datacenter:floor-plan"], scope={"level": "L02", "zone": "Unit-01-01"})
  assert different_level.status == "pass"
  svg = ElementTree.parse(root / "build/datacenter/floor-plan.svg").getroot()
  assert svg.find("svg:g", SVG).attrib["data-level-id"] == "L02"
  assert svg.find("svg:g/svg:g", SVG).attrib["data-zone-id"] == "L02/Unit-01-01"
  assert svg.find("svg:g/svg:text", SVG).text == "L02 · 7 m"

  zone_file = root / "build/drawings" / zone_path
  previous_zone = zone_file.read_bytes()
  for scope, message in (
    ({"zone": "Unit-01-01"}, "a level and zone together"),
    ({"level": "missing", "zone": "Unit-01-01"}, "Unknown floor plan level"),
    ({"level": "L01", "zone": "missing"}, "Unknown floor plan zone"),
  ):
    rejected = build_project(root, targets=[zone_target], scope=scope)
    assert rejected.status == "error"
    assert message in rejected.report.read_text()
    assert zone_file.read_bytes() == previous_zone


def test_schematic_named_target_reuses_registered_generator_in_another_project(tmp_path):
  root = copy_project(tmp_path, "hvac-system-schematic")
  result = build_project(root, targets=["system-schematic"])
  assert result.status == "pass"
  assert {path.relative_to(root).as_posix() for path in result.items} == {
    "build/drawings/schematics/system.json", "build/drawings/schematics/system.svg", "build/drawings/schematics/system.html",
    "build/drawings/schematics/system.drawio", "build/drawings/schematics/system-check.json",
  }
  diagram = json.loads((root / "build/drawings/schematics/system.json").read_text())
  assert diagram["nodes"] and diagram["source_map"]
