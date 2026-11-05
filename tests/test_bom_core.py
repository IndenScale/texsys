import json
from pathlib import Path
import tomllib

import texsys_bom
from texsys_bom.checker import check_document
from texsys_bom.compare import compare_documents
from texsys_bom.contracts import BomContract
from texsys_bom.projection import project_bom


ROOT = Path(__file__).parents[1]


def specification(value=1.0, status="known"):
  return {"value": value, "status": status, "basis": "neutral-test-fixture"}


def design_entry(identity="meter-a", family="meter", model="concept-meter"):
  return {
    "family": family,
    "quantity": 1.0,
    "unit": "ea",
    "selection": {"status": "approved", "model": model},
    "specifications": {"accuracy_c": specification(0.5)},
  }


def source_snapshot():
  return {
    "id": "design:src/neutral.json",
    "kind": "design",
    "path": "src/neutral.json",
    "digest": "sha256:neutral-source",
    "record_version": "neutral-source-1",
    "applicability": "authoritative neutral test input",
  }


def project(root, *, profile="feasibility", scope=None, contract=None, entry=None):
  entry = entry or design_entry()
  return project_bom(
    root=root,
    target="test:neutral-bom",
    profile=profile,
    scope=scope or {"include": "all"},
    source_snapshot=source_snapshot(),
    all_design_ids=["meter-a"],
    selected_design_ids=["meter-a"],
    design_entries=[entry],
    contract=contract or BomContract(backend="neutral-test/1.0"),
  )


def test_neutral_projection_and_checker_do_not_need_hvac_semantics(tmp_path: Path) -> None:
  result = project(tmp_path)

  assert result.status == "pass"
  assert result.document["entries"][0]["family"] == "meter"
  assert result.groups[0]["quantity"] == 1.0
  assert "total_capacity_kw" not in result.groups[0]
  assert check_document(result.document)[0] == "pass"


def test_discipline_requirements_are_composed_without_overriding_core(tmp_path: Path) -> None:
  contract = BomContract(
    backend="neutral-test/1.0",
    required_engineering_specifications=("insulation_resistance_ohm",),
  )

  result = project(tmp_path, profile="engineering", contract=contract)

  assert result.status == "indeterminate"
  assert "engineering-spec-required:meter-a:insulation_resistance_ohm" in {
    item["id"] for item in result.open_items
  }


def test_neutral_external_snapshot_accepts_non_hvac_family_and_model(tmp_path: Path) -> None:
  snapshot = {
    "schema_version": 1,
    "profile": "as-purchased",
    "source": {
      "system": "neutral-test-system",
      "record_version": "po-1",
      "effective_at": "2026-11-05T00:00:00Z",
    },
    "items": {"po-meter": design_entry()},
    "mappings": [
      {
        "design_instance_id": "meter-a",
        "external_item_id": "po-meter",
        "relation": "implemented-by",
        "status": "confirmed",
        "basis": "neutral-purchase-record",
      }
    ],
  }
  path = tmp_path / "assets" / "snapshot.json"
  path.parent.mkdir()
  path.write_text(json.dumps(snapshot), encoding="utf-8")

  result = project(
    tmp_path,
    profile="as-purchased",
    scope={"snapshot": "assets/snapshot.json"},
  )

  assert result.status == "pass"
  assert result.document["entries"][0]["procurement_item_id"] == "po-meter"
  assert result.document["entries"][0]["family"] == "meter"


def test_neutral_comparison_and_package_dependencies_are_discipline_independent(tmp_path: Path) -> None:
  baseline = project(tmp_path).document
  changed = design_entry()
  changed["quantity"] = 2.0
  candidate = project(tmp_path, entry=changed).document

  result = compare_documents(baseline, candidate, "sha256:baseline", "sha256:candidate")

  assert result["target"] == "bom:diff"
  assert [item["type"] for item in result["differences"]] == ["quantity-change"]
  package_manifest = tomllib.loads((ROOT / "packages" / "bom" / "pyproject.toml").read_text())
  assert package_manifest["project"]["dependencies"] == ["texsys"]
  source_root = Path(texsys_bom.__file__).parent
  assert all("texsys_hvac" not in path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))
  assert {target.id for target in texsys_bom.package.targets()} == {"bom:diff"}
