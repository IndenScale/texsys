"""Design-check references against the reference design example.

Positive: the assembled design satisfies every declared GB 50174 requirement.
Negative: single-input changes invalidate the corresponding check; missing
design basis leaves the target indeterminate instead of crashing.
"""

import json
from pathlib import Path

import pytest

from texsys.dependencies import lock_project
from texsys.targets import BuildContext
from texsys_datacenter_reference import package
from texsys_datacenter_reference.checks import evaluate_design_checks

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "datacenter-reference-design"


@pytest.fixture(scope="module")
def example_source():
  if not (EXAMPLE / "src" / "system.sdl.json").is_file():
    pytest.skip("reference design example not present")
  return EXAMPLE / "src"


def _copy_project(example_source, tmp_path):
  packages = EXAMPLE.parents[1] / "packages"
  manifest = (EXAMPLE / "texsys.toml").read_text(encoding="utf-8")
  (tmp_path / "texsys.toml").write_text(manifest.replace("../../packages/", f"{packages}/"), encoding="utf-8")
  source = tmp_path / "src"
  source.mkdir()
  (tmp_path / "assets").mkdir()
  for path in example_source.iterdir():
    (source / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
  lock_project(tmp_path)
  return source


def _edit_system(source, update):
  path = source / "system.sdl.json"
  document = json.loads(path.read_text(encoding="utf-8"))
  update(document["components"][document["root"]])
  path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def test_reference_design_passes_all_checks(example_source):
  report = evaluate_design_checks(example_source, EXAMPLE)
  assert report["assembly_diagnostics"] == []
  failed = [c["id"] for c in report["checks"] if c["status"] != "pass"]
  assert failed == [], failed
  assert report["status"] == "pass"
  assert {c["clause"] for c in report["checks"]} >= {"3.2.1", "8.1.7", "13.2.4"}


def test_lower_wall_fire_rating_fails_separation(example_source, tmp_path):
  source = _copy_project(example_source, tmp_path)

  def lower_wall(root):
    wall = next(name for name, inst in root["instances"].items()
                if inst["component"] == "FirePartitionWall")
    root["instances"][wall]["parameters"] = {
      "fire_resistance_hours": {"value": 1.0, "unit": "h", "state": "assumed",
                                "source": "反例：隔墙耐火极限降为 1.0 h"}}

  _edit_system(source, lower_wall)
  report = evaluate_design_checks(source, tmp_path)
  assert report["assembly_diagnostics"] == []
  separation = next(c for c in report["checks"] if c["id"] == "GB-13.2.4")
  assert separation["status"] == "fail"
  assert 1.0 in separation["evidence"]["wall_hours"]
  assert report["status"] == "fail"


def test_worse_fire_resistance_grade_fails_building_check(example_source, tmp_path):
  source = _copy_project(example_source, tmp_path)

  def worsen_grade(root):
    frame = next(name for name, inst in root["instances"].items()
                 if inst["component"] == "SteelFrameBay")
    root["instances"][frame]["parameters"] = {
      "fire_resistance_grade": {"value": 3, "unit": "级", "state": "assumed",
                                "source": "反例：框架耐火等级降为三级"}}

  _edit_system(source, worsen_grade)
  report = evaluate_design_checks(source, tmp_path)
  assert report["assembly_diagnostics"] == []
  building = next(c for c in report["checks"] if c["id"] == "GB-13.2.1")
  assert building["status"] == "fail"
  assert 3.0 in building["evidence"]["fire_resistance_grades"]
  assert report["status"] == "fail"


def test_remove_b_path_fails_redundancy(example_source, tmp_path):
  source = _copy_project(example_source, tmp_path)

  def remove_b(root):
    removed = {c["id"] for c in root["connections"] if c["to"].endswith(".power_b")}
    root["connections"] = [c for c in root["connections"] if c["id"] not in removed]
    for net in root.get("nets", {}).values():
      net["connections"] = [i for i in net["connections"] if i not in removed]

  _edit_system(source, remove_b)
  report = evaluate_design_checks(source, tmp_path)
  assert report["assembly_diagnostics"] == []
  redundancy = next(c for c in report["checks"] if c["id"] == "GB-3.2.1")
  assert redundancy["status"] == "fail"
  assert redundancy["evidence"]["dual_connected"] is False
  assert report["status"] == "fail"


def test_missing_design_basis_is_indeterminate(example_source, tmp_path):
  source = _copy_project(example_source, tmp_path)
  (source / "design-basis.json").unlink()
  context = BuildContext(root=tmp_path, target="dcr:design-checks", profile="concept",
                         scope={}, build_path=tmp_path / "build", scratch_path=tmp_path / "scratch")
  result = package.build_checks(context)
  assert result.status == "indeterminate"
  assert any(item["id"] == "missing-design-basis.json" for item in result.open_items)
