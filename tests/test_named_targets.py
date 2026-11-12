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


def test_floor_plans_project_declared_room_level_and_cross_level_work_package(tmp_path):
  root = copy_project(tmp_path, "datacenter-hybrid-unit")
  targets = ["plan-room-cooling", "plan-level-01", "plan-lot-mep-01", "building-scene"]
  result = build_project(root, targets=targets)
  assert result.status == "pass"
  report = json.loads(result.report.read_text())
  assert [item["id"] for item in report["targets"]][-len(targets):] == targets
  scene = Usd.Stage.Open(str(root / "build/scenes/building.usda"))
  assert scene.GetDefaultPrim().GetChild("L01").IsValid()
  assert (root / "build/scenes/building.json").is_file()
  assert (root / "build/datacenter/building-study.json").is_file()

  def read(path):
    svg = ElementTree.parse(root / "build/plans" / path).getroot()
    sheets = {}
    for sheet in svg.findall("svg:g", SVG):
      sheets[sheet.attrib["data-level-id"]] = {
        room.attrib["data-room-id"]: room.find("svg:rect", SVG).attrib for room in sheet.findall("svg:g", SVG)
      }
    return sheets

  room = read("room-l01-cooling.svg")
  assert {level: set(rooms) for level, rooms in room.items()} == {"L01": {"L01/Cooling"}}
  assert {key: float(room["L01"]["L01/Cooling"][key]) for key in ("x", "y", "width", "height")} == {
    "x": 50, "y": -42, "width": 8, "height": 18,
  }
  floor = read("level-01.svg")
  assert set(floor) == {"L01"}
  assert set(floor["L01"]) == {"L01/Hall", "L01/Services", "L01/Cooling", "L01/Electrical", "L01/Liquid-Collection"}
  lot = read("lot-mep-01.svg")
  assert {level: set(rooms) for level, rooms in lot.items()} == {
    "L01": {"L01/Cooling", "L01/Electrical"}, "L02": {"L02/Cooling"},
  }

  repeat = build_project(root, targets=targets)
  assert [item["execution"] for item in json.loads(repeat.report.read_text())["targets"]][-len(targets):] == ["cached"] * len(targets)

  source = root / "src/building.study.json"
  data = json.loads(source.read_text())
  data["layout"]["reserves"]["Cooling"]["size_m"][0] = 7.5
  source.write_text(json.dumps(data))
  updated = build_project(root, targets=targets)
  assert updated.status == "pass"
  assert read("room-l01-cooling.svg")["L01"]["L01/Cooling"]["width"] == "7.5"
  assert read("lot-mep-01.svg")["L02"]["L02/Cooling"]["width"] == "7.5"
  stable_file = root / "build/plans/room-l01-cooling.svg"
  published = stable_file.read_bytes()
  rejected = build_project(root, targets=[targets[0]], scope={"room": "L01/missing"})
  assert rejected.status == "error"
  assert stable_file.read_bytes() == published
  assert "Unknown floor plan room" in rejected.report.read_text()


def test_schematic_named_target_reuses_registered_generator_in_another_project(tmp_path):
  root = copy_project(tmp_path, "hvac-system-schematic")
  result = build_project(root, targets=["system-schematic"])
  assert result.status == "pass"
  assert {path.relative_to(root).as_posix() for path in result.items} == {
    "build/diagrams/system.json", "build/diagrams/system.svg", "build/diagrams/system.html",
    "build/diagrams/system.drawio", "build/diagrams/system-check.json",
  }
  diagram = json.loads((root / "build/diagrams/system.json").read_text())
  assert diagram["nodes"] and diagram["source_map"]
