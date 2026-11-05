import json
from pathlib import Path

import pytest

from test_hvac_bom import acceptance_equipment, build, copied_project, specification, write_v2


def item(quantity=1, capacity=100, space=None, status="active", effective_at=None, replaces=None):
  result = {
    "family": "chiller",
    "quantity": quantity,
    "unit": "ea",
    "selection": {"status": "approved", "model": "generic-100kw"},
    "specifications": {
      "capacity_kw": specification(capacity),
      "heat_rejection_kw": specification(9),
    },
    "status": status,
  }
  if space is not None:
    result["space"] = {"zone": space}
  if effective_at is not None:
    result["effective_at"] = effective_at
  if replaces is not None:
    result["replaces"] = replaces
  return result


def mapping(design, external, status="confirmed"):
  return {
    "design_instance_id": design,
    "external_item_id": external,
    "relation": "implemented-by",
    "status": status,
    "basis": f"evidence for {external}",
  }


def write_snapshot(root: Path, profile, items, mappings, name="snapshot.json"):
  path = root / "assets" / "snapshots" / name
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    json.dumps(
      {
        "schema_version": 1,
        "profile": profile,
        "source": {
          "system": "reviewed-test-fixture",
          "record_version": "record-1",
          "effective_at": "2026-11-05T00:00:00Z",
        },
        "items": items,
        "mappings": mappings,
      },
      indent=2,
      sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
  )
  return f"assets/snapshots/{name}"


def purchased_snapshot(root: Path):
  return write_snapshot(
    root,
    "as-purchased",
    {"po-40": item(2, 40), "po-100": item(1, 100)},
    [mapping("chiller-40-group", "po-40"), mapping("chiller-100", "po-100")],
    "purchased.json",
  )


def test_as_purchased_passes_with_fixed_snapshot_and_keeps_configuration_difference(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())
  snapshot = purchased_snapshot(root)

  result = build(root, "as-purchased", {"snapshot": snapshot})

  assert result.exit_code == 0, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert bom["evidence"]["status"] == "pass"
  assert {entry["procurement_item_id"] for entry in bom["entries"]} == {"po-40", "po-100"}
  assert bom["evidence"]["metrics"]["quantity_by_unit"] == {"ea": 3.0}
  assert len(bom["source_snapshots"]) == 2
  assert bom["source_snapshots"][1]["record_version"] == "record-1"
  assert len(bom["mappings"]) == 2
  assert {difference["type"] for difference in bom["differences"]} == {"configuration-change"}
  assert all(difference["review_status"] == "unreviewed" for difference in bom["differences"])
  assert (root / "build" / "hvac" / "bom-check.json").is_file()


def test_long_lead_procurement_scope_does_not_promote_other_design_items(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())
  snapshot = purchased_snapshot(root)

  result = build(
    root,
    "as-purchased",
    {"identities": ["chiller-40-group"], "snapshot": snapshot},
  )

  assert result.exit_code == 0, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert bom["scope"]["identities"] == ["chiller-40-group"]
  assert [entry["entry_id"] for entry in bom["entries"]] == ["po-40"]
  assert bom["evidence"]["metrics"]["quantity_by_unit"] == {"ea": 2.0}
  assert [item["design_instance_id"] for item in bom["mappings"]] == ["chiller-40-group"]


def test_scoped_design_without_external_mapping_is_indeterminate(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())
  snapshot = write_snapshot(
    root,
    "as-purchased",
    {"po-40": item(2, 40)},
    [mapping("chiller-40-group", "po-40")],
  )

  result = build(root, "as-purchased", {"snapshot": snapshot})

  assert result.exit_code == 3, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert "missing-external-mapping:chiller-100" in {item["id"] for item in bom["open_items"]}


def test_one_procurement_package_mapping_two_design_roles_is_counted_once(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())
  snapshot = write_snapshot(
    root,
    "as-purchased",
    {"package-1": item(1, 140)},
    [
      mapping("chiller-40-group", "package-1"),
      mapping("chiller-100", "package-1"),
    ],
  )

  result = build(root, "as-purchased", {"snapshot": snapshot})

  assert result.exit_code == 0, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert len(bom["entries"]) == 1
  assert bom["entries"][0]["design_instance_ids"] == ["chiller-100", "chiller-40-group"]
  assert bom["evidence"]["metrics"]["quantity_by_unit"] == {"ea": 1.0}
  groups = bom["views"]["by-family-spec"]["groups"]
  assert len(groups) == 1
  assert groups[0]["quantity"] == 1.0
  assert groups[0]["design_instance_ids"] == ["chiller-100", "chiller-40-group"]


def test_as_built_uses_physical_asset_ids_and_installed_locations(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())
  snapshot = write_snapshot(
    root,
    "as-built",
    {
      "asset-40": item(2, 40, space="plant-room"),
      "asset-100": item(1, 100, space="plant-room"),
    },
    [mapping("chiller-40-group", "asset-40"), mapping("chiller-100", "asset-100")],
    "built.json",
  )

  result = build(root, "as-built", {"snapshot": snapshot})

  assert result.exit_code == 0, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert {entry["physical_asset_id"] for entry in bom["entries"]} == {"asset-40", "asset-100"}
  assert all(entry["space"]["zone"] == "plant-room" for entry in bom["entries"])
  assert {difference["type"] for difference in bom["differences"]} == {
    "configuration-change",
    "location-change",
  }


def test_as_built_without_installed_location_is_indeterminate(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())
  snapshot = write_snapshot(
    root,
    "as-built",
    {"asset-40": item(2, 40), "asset-100": item(1, 100)},
    [mapping("chiller-40-group", "asset-40"), mapping("chiller-100", "asset-100")],
  )

  result = build(root, "as-built", {"snapshot": snapshot})

  assert result.exit_code == 3, result.output
  assert "missing-installed-location" in (root / "build" / "hvac" / "bom.json").read_text()


def maintained_snapshot(root: Path, changed=False, approved=False):
  new = item(1, 110 if changed else 100, space="plant-room", effective_at="2026-11-06T00:00:00Z", replaces="asset-old")
  if approved:
    new["design_change"] = {"status": "approved", "basis": "design-review-42"}
  return write_snapshot(
    root,
    "as-maintained",
    {
      "asset-old": item(1, 100, space="plant-room", status="retired", effective_at="2026-10-01T00:00:00Z"),
      "asset-new": new,
    },
    [mapping("chiller-100", "asset-new")],
    "maintained.json",
  )


def maintained_design():
  return {
    "chiller-100": {
      "family": "chiller",
      "quantity": 1,
      "unit": "ea",
      "selection": {"status": "approved", "model": "generic-100kw"},
      "specifications": {
        "capacity_kw": specification(100),
        "heat_rejection_kw": specification(9),
      },
      "space": {"zone": "plant-room"},
    }
  }


def test_maintenance_replacement_preserves_history_and_current_asset(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, maintained_design())
  snapshot = maintained_snapshot(root)

  result = build(root, "as-maintained", {"snapshot": snapshot})

  assert result.exit_code == 0, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert [entry["physical_asset_id"] for entry in bom["entries"]] == ["asset-new"]
  assert any(item.get("physical_asset_id") == "asset-old" for item in bom["history"])
  assert any(item.get("relation") == "replaces" for item in bom["history"])
  assert bom["differences"][0]["type"] == "replacement"


def test_capacity_changing_replacement_requires_design_review(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, maintained_design())
  snapshot = maintained_snapshot(root, changed=True)

  result = build(root, "as-maintained", {"snapshot": snapshot})

  assert result.exit_code == 3, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert "design-change-required:asset-new" in {item["id"] for item in bom["open_items"]}


def test_reviewed_capacity_changing_replacement_can_close(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, maintained_design())
  snapshot = maintained_snapshot(root, changed=True, approved=True)

  result = build(root, "as-maintained", {"snapshot": snapshot})

  assert result.exit_code == 0, result.output
  bom = json.loads((root / "build" / "hvac" / "bom.json").read_text())
  assert bom["evidence"]["status"] == "pass"


def test_unknown_mapping_reference_is_deterministic_fail(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  write_v2(root, acceptance_equipment())
  snapshot = write_snapshot(
    root,
    "as-purchased",
    {"po-40": item(2, 40)},
    [mapping("unknown-design", "po-40")],
  )

  result = build(root, "as-purchased", {"snapshot": snapshot})

  assert result.exit_code == 1, result.output
  report = json.loads((root / "build" / "build.json").read_text())
  assert report["targets"][0]["diagnostics"][0]["rule"] == "B003"
