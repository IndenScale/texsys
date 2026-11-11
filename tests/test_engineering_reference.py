"""Frozen report declarations constrain arithmetic without becoming field acceptance."""

import json
from pathlib import Path
import shutil

import pytest

from cli_process import ProjectCliRunner
from texsys.dependencies import lock_project
from texsys.engineering import (
  accept_engineering,
  apply_engineering_change,
  inspect_engineering,
  plan_engineering_change,
  run_engineering,
)
from texsys.evidence import file_digest, write_json
from texsys.project import ProjectError, initialize_project


REPO = Path(__file__).parents[1]
ADAPTER = "hvac:reference-capacity"
STUDY = "src/reference-capacity.study.json"
SNAPSHOT = "assets/reference/cold-station-acceptance.json"


def _binding(value, unit="1"):
  return {"value": value, "unit": unit, "state": "assumed", "source": "Explicit concept assumption for this review."}


@pytest.fixture
def reference(tmp_path):
  root = tmp_path / "reference"
  initialize_project(root)
  added = ProjectCliRunner().invoke(None, ["add", REPO / "packages/hvac-checks", "-p", root])
  assert added.exit_code == 0, added.output
  values = {"chiller_count": (2, "1"), "per_chiller_capacity_kw": (75, "kW"),
            "air_load_count": (7, "1"), "per_air_load_kw": (15, "kW"),
            "liquid_load_count": (2, "1"), "per_liquid_load_kw": (45, "kW"),
            "reported_total_capacity_kw": (150, "kW")}
  snapshot = {"schema_version": 1, "id": "sanitized-acceptance-report", "sources": [
    {"id": "acceptance", "sha256": "ab" * 32, "label": "Sanitized report declaration",
     "original_path": "/unavailable/original-acceptance.pdf"}],
    "configuration": {key: {"value": value, "unit": unit, "state": "assumed", "source_id": "acceptance",
                             "location": "Report, configuration table", "meaning": f"Declared {key}, not measured performance"}
                      for key, (value, unit) in values.items()},
    "unresolved": ["Actual performance and simultaneous operation have not been confirmed."],
    "professional_confirmation": {"status": "unconfirmed"}}
  write_json(root / SNAPSHOT, snapshot)
  write_json(root / STUDY, {"schema_version": 1, "reference": {"path": SNAPSHOT, "digest": file_digest(root / SNAPSHOT)},
                           "assumptions": {"air_load_fraction": _binding(.6), "liquid_load_fraction": _binding(.6),
                                           "available_chillers": _binding(2)},
                           "requirements": {"min_margin_kw": _binding(0, "kW")},
                           "scope": "Assume the declared capacities share one thermal boundary; no field validation."})
  return root


def _read(root, path):
  return json.loads((root / path).read_text(encoding="utf-8"))


def _rebind_snapshot(root, snapshot):
  write_json(root / SNAPSHOT, snapshot)
  study = _read(root, STUDY)
  study["reference"]["digest"] = file_digest(root / SNAPSHOT)
  write_json(root / STUDY, study)


def _change(root, values):
  state = inspect_engineering(root, ADAPTER)
  request = {"adapter": ADAPTER, "revision": state["revision"],
             "changes": [{"input_id": key, "value": value} for key, value in values.items()]}
  plan = plan_engineering_change(root, request)
  assert plan["status"] == "pass" and plan["impacts"][0]["evidence"]["facts"]
  assert apply_engineering_change(root, request, plan["plan"])["applied"]


def _check(run, identity):
  return next(item for item in run["result"]["checks"] if item["id"] == f"reference:{identity}")


def _reject_acceptance(root, run):
  with pytest.raises(ProjectError, match="passing"):
    accept_engineering(root, ADAPTER, run["run_id"], "概念参考审查测试员", "声明资料与条件算术不能替代现场验证。")


def test_public_reference_lifecycle_never_promotes_declarations_to_engineering_acceptance(reference):
  root = reference
  state = inspect_engineering(root, ADAPTER)
  assert {item["id"] for item in state["inputs"]} == {
    "reference.air-load-fraction", "reference.liquid-load-fraction", "reference.available-chillers"}
  assert all("design" not in item and item["object_ref"].startswith("reference:") for item in state["inputs"])
  baseline = run_engineering(root, ADAPTER)
  assert baseline["execution"] == "completed" and baseline["current"]
  result = baseline["result"]
  assert result["assessment"] == "indeterminate" and result["arithmetic_execution"] == "completed"
  assert result["solver_execution"] == "not-run" and result["analysis_method"] == "0D conditional capacity arithmetic"
  assert _check(baseline, "conditional-load")["actual"] == 117
  assert _check(baseline, "available-capacity")["actual"] == 150
  assert _check(baseline, "capacity-margin")["actual"] == 33
  assert _check(baseline, "reported-total")["status"] == "pass"
  assert _check(baseline, "field-verification")["status"] == "indeterminate"
  assert len(result["extraction"]) == 4 and not result["source_content_verified"]
  assert all(item["source"]["path"] == SNAPSHOT and item["source"]["digest"] == file_digest(root / SNAPSHOT)
             for item in result["extraction"])
  assert result["reference_snapshot"]["content"]["sources"][0]["original_path"] == "/unavailable/original-acceptance.pdf"
  assert result["study_scope"].startswith("Assume")
  evidence = _check(baseline, "conditional-load")["evidence"]
  assert evidence["facts"][0]["source_sha256"] == "sha256:" + "ab" * 32
  assert evidence["facts"][0]["pointer"].startswith("/configuration/")
  assert evidence["snapshot"]["digest"] == file_digest(root / SNAPSHOT)
  assert all("design" not in item for item in result["checks"] + result["extraction"])
  _reject_acceptance(root, baseline)
  _change(root, {"reference.air-load-fraction": 1, "reference.liquid-load-fraction": 1})
  assert inspect_engineering(root, ADAPTER)["latest"]["freshness"] == "inputs-changed"
  failed = run_engineering(root, ADAPTER)
  assert failed["result"]["assessment"] == "fail" and failed["execution"] == "completed"
  assert _check(failed, "conditional-load")["actual"] == 195
  assert _check(failed, "capacity-margin")["actual"] == -45
  assert _check(failed, "capacity-margin")["status"] == "fail"
  _reject_acceptance(root, failed)
  _change(root, {"reference.air-load-fraction": .6, "reference.liquid-load-fraction": .6})
  repaired = run_engineering(root, ADAPTER)
  assert repaired["result"]["assessment"] == "indeterminate" and repaired["acceptance"] == "unreviewed"
  assert _check(repaired, "capacity-margin")["actual"] == 33
  _reject_acceptance(root, repaired)
  assert not list((root / "reviews/engineering").glob("*.json"))


def test_digest_mismatch_cannot_silently_use_new_facts_and_rebinding_still_checks_source_consistency(reference):
  root = reference
  old = run_engineering(root, ADAPTER)
  snapshot = _read(root, SNAPSHOT)
  snapshot["configuration"]["per_chiller_capacity_kw"]["value"] = 76
  write_json(root / SNAPSHOT, snapshot)
  state = inspect_engineering(root, ADAPTER)
  assert state["inputs"] == [] and state["latest"]["freshness"] == "inputs-changed"
  invalid = run_engineering(root, ADAPTER)
  assert invalid["execution"] == "completed" and invalid["result"]["assessment"] == "indeterminate"
  assert invalid["result"]["arithmetic_execution"] == "not-run" and invalid["result"]["extraction"] == []
  assert invalid["result"]["diagnostics"][0]["code"] == "REFERENCE_DIGEST_MISMATCH"
  assert "摘要不匹配" in _check(invalid, "snapshot")["message"]
  _reject_acceptance(root, invalid)
  _rebind_snapshot(root, snapshot)
  changed = run_engineering(root, ADAPTER)
  assert changed["result"]["assessment"] == "fail"
  assert _check(changed, "reported-total")["actual"] == 152 and _check(changed, "reported-total")["limit"] == 150
  assert _check(changed, "reported-total")["status"] == "fail"
  history = inspect_engineering(root, ADAPTER)["runs"]
  assert next(item for item in history if item["run_id"] == old["run_id"])["freshness"] == "inputs-changed"


@pytest.mark.parametrize("case", ["missing-fact", "unknown-fact", "wrong-unit", "unknown-source-id", "bad-source-hash",
                                  "missing-location", "no-sources", "boolean-count", "fractional-count"])
def test_invalid_reference_facts_never_produce_precise_capacity_numbers(reference, case):
  snapshot = _read(reference, SNAPSHOT)
  fact = snapshot["configuration"]["chiller_count"]
  if case == "missing-fact":
    del snapshot["configuration"]["chiller_count"]
  elif case == "unknown-fact":
    fact["state"] = "unknown"
    del fact["value"]
  elif case == "wrong-unit":
    fact["unit"] = "kW"
  elif case == "unknown-source-id":
    fact["source_id"] = "not-in-snapshot"
  elif case == "bad-source-hash":
    snapshot["sources"][0]["sha256"] = "unverified"
  elif case == "missing-location":
    del fact["location"]
  elif case == "no-sources":
    snapshot["sources"] = []
  else:
    fact["value"] = True if case == "boolean-count" else 2.5
  _rebind_snapshot(reference, snapshot)
  result = run_engineering(reference, ADAPTER)["result"]
  assert result["assessment"] == "indeterminate" and result["arithmetic_execution"] == "not-run"
  assert result["solver_execution"] == "not-run" and result["extraction"] == []
  assert result["diagnostics"] and all("actual" not in check for check in result["checks"])


@pytest.mark.parametrize("case", ["unknown-assumption", "no-assumption-source", "fractional-available", "excess-available",
                                  "excess-fraction", "weakened-requirement", "outside-assets", "absolute-path", "malformed-digest"])
def test_invalid_study_bindings_or_source_paths_stop_arithmetic(reference, case):
  study = _read(reference, STUDY)
  if case == "unknown-assumption":
    study["assumptions"]["air_load_fraction"]["state"] = "unknown"
    del study["assumptions"]["air_load_fraction"]["value"]
  elif case == "no-assumption-source":
    del study["assumptions"]["air_load_fraction"]["source"]
  elif case in {"fractional-available", "excess-available"}:
    study["assumptions"]["available_chillers"]["value"] = 1.5 if case == "fractional-available" else 3
  elif case == "excess-fraction":
    study["assumptions"]["air_load_fraction"]["value"] = 1.1
  elif case == "weakened-requirement":
    study["requirements"]["min_margin_kw"]["value"] = -1
  elif case == "malformed-digest":
    study["reference"]["digest"] = "sha256:missing"
  else:
    study["reference"]["path"] = STUDY if case == "outside-assets" else str(reference / SNAPSHOT)
  write_json(reference / STUDY, study)
  assert inspect_engineering(reference, ADAPTER)["inputs"] == []
  run = run_engineering(reference, ADAPTER)
  assert run["result"]["assessment"] == "indeterminate" and run["result"]["arithmetic_execution"] == "not-run"
  assert run["result"]["extraction"] == []
  _reject_acceptance(reference, run)


def test_numeric_bounds_zero_availability_and_stricter_requirement(reference):
  state = inspect_engineering(reference, ADAPTER)
  before = (reference / STUDY).read_bytes()
  with pytest.raises(ProjectError, match="非负整数"):
    plan_engineering_change(reference, {"adapter": ADAPTER, "revision": state["revision"],
                            "changes": [{"input_id": "reference.available-chillers", "value": 1.5}]})
  assert (reference / STUDY).read_bytes() == before
  for identity, value in (("reference.available-chillers", 3), ("reference.air-load-fraction", 1.01)):
    with pytest.raises(ProjectError, match="bounds"):
      plan_engineering_change(reference, {"adapter": ADAPTER, "revision": state["revision"],
                              "changes": [{"input_id": identity, "value": value}]})
  _change(reference, {"reference.available-chillers": 0})
  zero = run_engineering(reference, ADAPTER)
  assert zero["result"]["assessment"] == "fail" and zero["result"]["arithmetic_execution"] == "completed"
  assert _check(zero, "available-capacity")["actual"] == 0 and _check(zero, "capacity-margin")["actual"] == -117
  _change(reference, {"reference.available-chillers": 2})
  study = _read(reference, STUDY)
  study["requirements"]["min_margin_kw"]["value"] = 34
  write_json(reference / STUDY, study)
  stricter = run_engineering(reference, ADAPTER)
  assert stricter["result"]["assessment"] == "fail"
  assert _check(stricter, "capacity-margin")["actual"] == 33 and _check(stricter, "capacity-margin")["limit"] == 34


def test_source_author_claim_cannot_upgrade_field_verification(reference):
  snapshot = _read(reference, SNAPSHOT)
  snapshot["professional_confirmation"] = {"status": "verified", "performance": "pass"}
  snapshot["unresolved"] = []
  for fact in snapshot["configuration"].values():
    fact["state"] = "known"
  _rebind_snapshot(reference, snapshot)
  result = run_engineering(reference, ADAPTER)
  assert result["result"]["assessment"] == "indeterminate" and not result["result"]["source_content_verified"]
  assert _check(result, "field-verification")["status"] == "indeterminate"
  _reject_acceptance(reference, result)


def test_repository_reference_fixture_is_reproducible_without_original_documents(tmp_path):
  root = tmp_path / "cold-station"
  shutil.copytree(REPO / "examples/hvac-cold-station", root,
                  ignore=shutil.ignore_patterns("build", "reviews", ".texsys-write.lock"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(root)
  generic = (root / "src/hvac.json").read_bytes(), (root / "src/system.sdl.json").read_bytes()
  run = run_engineering(root, ADAPTER)
  assert run["result"]["assessment"] == "indeterminate" and run["result"]["arithmetic_execution"] == "completed"
  assert _check(run, "conditional-load")["actual"] == 117 and _check(run, "capacity-margin")["actual"] == 33
  assert ((root / "src/hvac.json").read_bytes(), (root / "src/system.sdl.json").read_bytes()) == generic
