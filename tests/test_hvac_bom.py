import json
from pathlib import Path
import shutil

import pytest
from typer.testing import CliRunner

from texsys.cli import app

runner = CliRunner()
ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "hvac-cold-station"
PACKAGES = ROOT / "packages"


def copied_project(tmp_path: Path) -> Path:
  target = tmp_path / "hvac-bom-project"
  shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("build"))
  (target / "build").mkdir()
  manifest = target / "texsys.toml"
  manifest.write_text(
    manifest.read_text(encoding="utf-8").replace('"../../packages/', f'"{PACKAGES}/'),
    encoding="utf-8",
  )
  assert runner.invoke(app, ["lock", "-p", str(target)]).exit_code == 0
  assert runner.invoke(app, ["sync", "-p", str(target)]).exit_code == 0
  return target


def specification(value, status="known"):
  result = {"status": status, "basis": "test-planning-basis"}
  if status != "pending":
    result["value"] = value
  return result


def write_v2(root: Path, equipment: dict) -> None:
  (root / "src" / "hvac.json").write_text(
    json.dumps({"schema_version": 2, "equipment": equipment}, indent=2, sort_keys=True) + "\n"
  )
  sdl = {
    "schema_version": 1,
    "root": "ColdStation",
    "components": {
      "ColdStation": {
        "instances": {identity: {"component": "Equipment"} for identity in equipment}
      },
      "Equipment": {},
    },
  }
  (root / "src" / "system.sdl.json").write_text(
    json.dumps(sdl, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )


def acceptance_equipment():
  return {
    "chiller-40-group": {
      "family": "chiller",
      "quantity": 2,
      "unit": "ea",
      "selection": {"status": "unselected"},
      "specifications": {"capacity_kw": specification(40, "assumed")},
    },
    "chiller-100": {
      "family": "chiller",
      "quantity": 1,
      "unit": "ea",
      "selection": {"status": "unselected"},
      "specifications": {"capacity_kw": specification(100)},
    },
  }


def build(root: Path, profile="feasibility", scope=None):
  arguments = ["build", "--target", "hvac:equipment-bom", "--profile", profile, "-p", str(root)]
  if scope is not None:
    arguments[5:5] = ["--scope", json.dumps(scope)]
  return runner.invoke(app, arguments)


def test_feasibility_bom_closes_without_selected_models_and_is_deterministic(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())

  first = build(root)

  assert first.exit_code == 0, first.output
  bom_path = root / "build" / "hvac" / "bom.json"
  bom = json.loads(bom_path.read_text())
  assert bom["target"] == "hvac:equipment-bom"
  assert bom["profile"] == "feasibility"
  assert bom["evidence"]["status"] == "pass"
  assert bom["evidence"]["metrics"]["quantity_by_unit"] == {"ea": 3.0}
  groups = bom["views"]["by-family-spec"]["groups"]
  by_capacity = {group["specifications"]["capacity_kw"]["value"]: group for group in groups}
  assert by_capacity[40.0]["total_capacity_kw"] == 80.0
  assert by_capacity[40.0]["capacity_status"] == "assumed"
  assert by_capacity[100.0]["total_capacity_kw"] == 100.0
  assert by_capacity[100.0]["capacity_status"] == "known"
  assert all(entry["selection"]["status"] == "unselected" for entry in bom["entries"])
  assert all(entry["item_basis"]["source_snapshot"] == "design:src/hvac.json" for entry in bom["entries"])
  assert bom["source_snapshots"][0]["digest"].startswith("sha256:")

  first_bytes = bom_path.read_bytes()
  second = build(root)
  assert second.exit_code == 0, second.output
  assert bom_path.read_bytes() == first_bytes


@pytest.mark.parametrize("profile", ["as-purchased", "as-built", "as-maintained"])
def test_external_fact_profile_cannot_pass_without_snapshot(tmp_path: Path, profile: str) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())

  result = build(root, profile)

  assert result.exit_code == 3, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert bom["evidence"]["status"] == "indeterminate"
  assert bom["entries"] == []
  assert bom["open_items"][0]["id"] == f"missing-{profile}-snapshot"
  report = json.loads((root / "build" / "build.json").read_text())
  assert all(artifact["partial"] for artifact in report["artifacts"])


def test_engineering_profile_requires_instance_level_entries(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())

  result = build(root, "engineering")

  assert result.exit_code == 3, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert "ungrouped-instance-required:chiller-40-group" in {
    item["id"] for item in bom["open_items"]
  }


def test_same_candidate_model_with_different_configuration_is_not_merged(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  equipment = acceptance_equipment()
  for identity, capacity in (("chiller-a", 40), ("chiller-b", 50)):
    equipment[identity] = {
      "family": "chiller",
      "quantity": 1,
      "unit": "ea",
      "selection": {"status": "candidate", "model": "generic-100kw"},
      "specifications": {"capacity_kw": specification(capacity)},
    }
  write_v2(root, equipment)

  result = build(root)

  assert result.exit_code == 0, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  groups = bom["views"]["by-family-spec"]["groups"]
  assert len(groups) == 4
  capacities = {group["specifications"]["capacity_kw"]["value"] for group in groups}
  assert capacities == {40.0, 50.0, 100.0}


def test_scope_limits_delivery_without_changing_source_identities(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())

  result = build(root, scope={"identities": ["chiller-100"]})

  assert result.exit_code == 0, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert bom["scope"] == {"identities": ["chiller-100"]}
  assert [entry["design_instance_id"] for entry in bom["entries"]] == ["chiller-100"]
  assert bom["evidence"]["metrics"]["quantity_by_unit"] == {"ea": 1.0}


def test_pending_value_is_rejected_instead_of_becoming_unknown_by_null(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  equipment = acceptance_equipment()
  equipment["chiller-100"]["specifications"]["capacity_kw"] = {
    "status": "pending",
    "basis": "awaiting-load-review",
    "value": 100,
  }
  write_v2(root, equipment)

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 1
  diagnostics = json.loads(result.output)["diagnostics"]
  assert any(item["rule"] == "B001" for item in diagnostics)
