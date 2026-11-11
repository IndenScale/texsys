import json
from pathlib import Path

from cli_process import ProjectCliRunner

from test_hvac_bom import copied_project
from texsys.cli import app
from texsys_hvac_bom.compare import compare_documents

runner = ProjectCliRunner()


def entry(identity, design_ids, quantity=1, capacity=100, zone="a"):
  return {
    "entry_id": identity,
    "design_instance_ids": design_ids,
    "family": "chiller",
    "quantity": quantity,
    "unit": "ea",
    "selection": {"status": "approved", "model": "generic-100kw"},
    "specifications": {"capacity_kw": {"value": capacity, "status": "known", "basis": "test"}},
    "space": {"zone": zone},
  }


def document(profile, entries, history=()):
  return {
    "schema_version": 1,
    "target": "hvac:equipment-bom",
    "profile": profile,
    "scope": {"include": "all"},
    "source_snapshots": [{"id": f"{profile}:snapshot", "digest": "sha256:source"}],
    "entries": list(entries),
    "history": list(history),
  }


def compare(first, second):
  return compare_documents(first, second, "sha256:first", "sha256:candidate")


def test_same_profile_comparison_classifies_all_controlled_difference_types() -> None:
  baseline = document(
    "as-built",
    [entry("asset-a", ["design-a"]), entry("asset-removed", ["design-removed"])],
  )
  candidate = document(
    "as-built",
    [entry("asset-a", ["design-b"], quantity=2, capacity=110, zone="b"), entry("asset-added", ["design-added"])],
  )

  result = compare(baseline, candidate)

  assert result["status"] == "pass"
  assert {item["type"] for item in result["differences"]} == {
    "quantity-change",
    "configuration-change",
    "location-change",
    "mapping-change",
    "removed",
    "added",
  }
  assert result["summary"]["difference_count"] == 6
  assert all(item["review_status"] == "unreviewed" for item in result["differences"])
  assert all(item["evidence"]["baseline_digest"] == "sha256:first" for item in result["differences"])


def test_cross_profile_comparison_matches_shared_design_identity() -> None:
  baseline = document("engineering", [entry("design-a", ["design-a"])])
  candidate = document("as-purchased", [entry("po-a", ["design-a"])])

  result = compare(baseline, candidate)

  assert result["differences"] == []
  assert result["summary"]["has_differences"] is False


def test_replacement_is_not_double_counted_as_removed_and_added() -> None:
  baseline = document("as-built", [entry("asset-old", ["design-a"])])
  candidate = document(
    "as-maintained",
    [entry("asset-new", ["design-a"])],
    history=[
      {
        "relation": "replaces",
        "physical_asset_id": "asset-new",
        "replaces_physical_asset_id": "asset-old",
      }
    ],
  )

  result = compare(baseline, candidate)

  assert [item["type"] for item in result["differences"]] == ["replacement"]


def test_many_to_many_comparison_does_not_invent_quantity_allocation() -> None:
  baseline = document("engineering", [entry("design-a", ["design-a"])])
  candidate = document(
    "as-purchased",
    [entry("po-a", ["design-a"]), entry("po-b", ["design-a"])],
  )

  result = compare(baseline, candidate)

  assert [item["type"] for item in result["differences"]] == ["mapping-change"]
  difference = result["differences"][0]
  assert difference["baseline"] == ["design-a"]
  assert difference["candidate"] == ["po-a", "po-b"]
  assert "not summed" in difference["aggregation"]


def test_comparison_target_emits_deterministic_artifact_without_failing_on_difference(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  baseline = document("as-built", [entry("asset-a", ["design-a"])])
  candidate = document("as-built", [entry("asset-a", ["design-a"], quantity=2)])
  for name, value in (("baseline", baseline), ("candidate", candidate)):
    path = root / "assets" / "compare" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  scope = {"baseline": "assets/compare/baseline.json", "candidate": "assets/compare/candidate.json"}

  arguments = [
    "build",
    "--target",
    "hvac:equipment-bom-diff",
    "--scope",
    json.dumps(scope),
    "-p",
    str(root),
  ]
  first = runner.invoke(app, arguments)

  assert first.exit_code == 0, first.output
  path = root / "build" / "hvac" / "bom-diff.json"
  result = json.loads(path.read_text())
  assert result["summary"]["counts_by_type"] == {"quantity-change": 1}
  first_bytes = path.read_bytes()
  assert runner.invoke(app, arguments).exit_code == 0
  assert path.read_bytes() == first_bytes


def test_comparison_with_missing_fixed_input_is_indeterminate(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  scope = {"baseline": "assets/missing.json", "candidate": "assets/also-missing.json"}

  result = runner.invoke(
    app,
    ["build", "--target", "hvac:equipment-bom-diff", "--scope", json.dumps(scope), "-p", str(root)],
  )

  assert result.exit_code == 3, result.output
  report = json.loads((root / "build" / "build.json").read_text())
  assert report["targets"][0]["open_items"][0]["id"] == "comparison-input-open"


def test_neutral_comparison_target_uses_generic_output_namespace(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  for name in ("baseline", "candidate"):
    path = root / "assets" / "compare" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document("engineering", [entry("meter-a", ["meter-a"])])))

  result = runner.invoke(
    app,
    [
      "build",
      "--target",
      "bom:diff",
      "--scope",
      json.dumps({"baseline": "assets/compare/baseline.json", "candidate": "assets/compare/candidate.json"}),
      "-p",
      str(root),
    ],
  )

  assert result.exit_code == 0, result.output
  comparison = json.loads((root / "build" / "bom" / "bom-diff.json").read_text())
  assert comparison["target"] == "bom:diff"
  assert comparison["summary"]["has_differences"] is False
