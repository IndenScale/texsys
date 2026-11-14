"""Design-check references against the reference design example.

Positive: the assembled design satisfies every declared GB 50174 requirement.
Negative: single-input changes invalidate the corresponding check.
"""

import json
from pathlib import Path

import pytest

from texsys.dependencies import lock_project
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


def test_reference_design_passes_all_checks(example_source):
  report = evaluate_design_checks(example_source, EXAMPLE)
  assert report["assembly_diagnostics"] == []
  failed = [c["id"] for c in report["checks"] if c["status"] != "pass"]
  assert failed == [], failed
  assert report["status"] == "pass"
  assert {c["clause"] for c in report["checks"]} >= {"3.2.1", "8.1.7", "13.2.2"}


def test_lower_wall_fire_rating_fails_separation(example_source, tmp_path):
  source = _copy_project(example_source, tmp_path)
  document = json.loads((source / "system.sdl.json").read_text(encoding="utf-8"))
  root = document["components"][document["root"]]
  wall = next(name for name, inst in root["instances"].items()
              if inst["component"] == "FirePartitionWall")
  root["instances"][wall]["parameters"] = {
    "fire_resistance_hours": {"value": 1.0, "unit": "h", "state": "assumed",
                              "source": "反例：隔墙耐火极限降为 1.0 h"}}
  (source / "system.sdl.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
  report = evaluate_design_checks(source, tmp_path)
  assert report["assembly_diagnostics"] == []
  separation = next(c for c in report["checks"] if c["id"] == "GB-13.2.2")
  assert separation["status"] == "fail"
  assert 1.0 in separation["evidence"]["wall_hours"]
  assert report["status"] == "fail"


def test_remove_b_path_fails_redundancy(example_source, tmp_path):
  source = _copy_project(example_source, tmp_path)
  document = json.loads((source / "system.sdl.json").read_text(encoding="utf-8"))
  root = document["components"][document["root"]]
  removed = {c["id"] for c in root["connections"] if c["to"].endswith(".power_b")}
  root["connections"] = [c for c in root["connections"] if c["id"] not in removed]
  for net in root.get("nets", {}).values():
    net["connections"] = [i for i in net["connections"] if i not in removed]
  (source / "system.sdl.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
  report = evaluate_design_checks(source, tmp_path)
  assert report["assembly_diagnostics"] == []
  redundancy = next(c for c in report["checks"] if c["id"] == "GB-3.2.1")
  assert redundancy["status"] == "fail"
  assert redundancy["evidence"]["dual_connected"] is False
  assert report["status"] == "fail"
