"""Controlled, deterministic comparison of two Target BOM snapshots."""

from collections import Counter
import json
from pathlib import Path
from typing import Any

from texsys.evidence import file_digest, write_json
from texsys.targets import Artifact, BuildContext, TargetResult


def _difference(
  kind: str,
  baseline_id: str | None,
  candidate_id: str | None,
  baseline_digest: str,
  candidate_digest: str,
  **details: Any,
) -> dict[str, Any]:
  return {
    "type": kind,
    "baseline_entry_id": baseline_id,
    "candidate_entry_id": candidate_id,
    "reason": "Observed difference between two fixed Target BOM inputs; no approval is inferred.",
    "review_status": "unreviewed",
    "evidence": {
      "baseline_digest": baseline_digest,
      "candidate_digest": candidate_digest,
    },
    **details,
  }


def compare_documents(
  baseline: dict[str, Any],
  candidate: dict[str, Any],
  baseline_digest: str,
  candidate_digest: str,
) -> dict[str, Any]:
  """Compare identities and configurations without merging either authority."""

  first_entries = baseline.get("entries", [])
  second_entries = candidate.get("entries", [])
  first_by_id = {item["entry_id"]: item for item in first_entries}
  second_by_id = {item["entry_id"]: item for item in second_entries}
  if len(first_by_id) != len(first_entries) or len(second_by_id) != len(second_entries):
    raise ValueError("Target BOM entry_id values must be unique.")

  pairs: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
  paired_first: set[str] = set()
  paired_second: set[str] = set()
  replacements = {
    (item.get("replaces_physical_asset_id"), item.get("physical_asset_id"))
    for item in candidate.get("history", [])
    if item.get("relation") == "replaces"
  }
  mapping_only: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
  if baseline.get("profile") == candidate.get("profile"):
    for identity in sorted(set(first_by_id) & set(second_by_id)):
      pairs.append((first_by_id[identity], second_by_id[identity], False))
      paired_first.add(identity)
      paired_second.add(identity)
  else:
    first_by_design: dict[str, list[dict[str, Any]]] = {}
    second_by_design: dict[str, list[dict[str, Any]]] = {}
    for item in first_entries:
      for design_id in item.get("design_instance_ids", []):
        first_by_design.setdefault(design_id, []).append(item)
    for item in second_entries:
      for design_id in item.get("design_instance_ids", []):
        second_by_design.setdefault(design_id, []).append(item)
    for design_id in sorted(set(first_by_design) & set(second_by_design)):
      first = first_by_design[design_id]
      second = second_by_design[design_id]
      if len(first) == 1 and len(second) == 1:
        if (first[0]["entry_id"], second[0]["entry_id"]) not in replacements:
          pairs.append((first[0], second[0], False))
          paired_first.add(first[0]["entry_id"])
          paired_second.add(second[0]["entry_id"])
      else:
        paired_first.update(item["entry_id"] for item in first)
        paired_second.update(item["entry_id"] for item in second)
        mapping_only.append((design_id, first, second))

  for old_id, new_id in sorted(replacements):
    if old_id in first_by_id and new_id in second_by_id:
      if old_id not in paired_first and new_id not in paired_second:
        pairs.append((first_by_id[old_id], second_by_id[new_id], True))
        paired_first.add(old_id)
        paired_second.add(new_id)

  differences: list[dict[str, Any]] = []
  for design_id, first, second in mapping_only:
    differences.append(
      _difference(
        "mapping-change",
        None,
        None,
        baseline_digest,
        candidate_digest,
        design_instance_ids=[design_id],
        baseline=[item["entry_id"] for item in first],
        candidate=[item["entry_id"] for item in second],
        aggregation="Many-to-many quantities are not summed without an explicit allocation contract.",
      )
    )
  for first, second, replacement in pairs:
    first_id = first["entry_id"]
    second_id = second["entry_id"]
    if replacement:
      differences.append(
        _difference(
          "replacement",
          first_id,
          second_id,
          baseline_digest,
          candidate_digest,
          design_instance_ids=sorted(
            set(first.get("design_instance_ids", [])) | set(second.get("design_instance_ids", []))
          ),
        )
      )
    if first["quantity"] != second["quantity"] or first["unit"] != second["unit"]:
      differences.append(
        _difference(
          "quantity-change",
          first_id,
          second_id,
          baseline_digest,
          candidate_digest,
          baseline={"quantity": first["quantity"], "unit": first["unit"]},
          candidate={"quantity": second["quantity"], "unit": second["unit"]},
        )
      )
    first_configuration = {
      "family": first["family"],
      "selection": first["selection"],
      "specifications": first["specifications"],
    }
    second_configuration = {
      "family": second["family"],
      "selection": second["selection"],
      "specifications": second["specifications"],
    }
    if first_configuration != second_configuration:
      differences.append(
        _difference(
          "configuration-change",
          first_id,
          second_id,
          baseline_digest,
          candidate_digest,
          baseline=first_configuration,
          candidate=second_configuration,
        )
      )
    if first.get("space") != second.get("space"):
      differences.append(
        _difference(
          "location-change",
          first_id,
          second_id,
          baseline_digest,
          candidate_digest,
          baseline=first.get("space"),
          candidate=second.get("space"),
        )
      )
    if set(first.get("design_instance_ids", [])) != set(second.get("design_instance_ids", [])):
      differences.append(
        _difference(
          "mapping-change",
          first_id,
          second_id,
          baseline_digest,
          candidate_digest,
          baseline=sorted(first.get("design_instance_ids", [])),
          candidate=sorted(second.get("design_instance_ids", [])),
        )
      )

  for identity in sorted(set(first_by_id) - paired_first):
    differences.append(
      _difference(
        "removed",
        identity,
        None,
        baseline_digest,
        candidate_digest,
        design_instance_ids=first_by_id[identity].get("design_instance_ids", []),
      )
    )
  for identity in sorted(set(second_by_id) - paired_second):
    differences.append(
      _difference(
        "added",
        None,
        identity,
        baseline_digest,
        candidate_digest,
        design_instance_ids=second_by_id[identity].get("design_instance_ids", []),
      )
    )

  counts = Counter(item["type"] for item in differences)
  return {
    "schema_version": 1,
    "target": "bom:diff",
    "status": "pass",
    "baseline": {
      "target": baseline.get("target"),
      "profile": baseline.get("profile"),
      "scope": baseline.get("scope"),
      "digest": baseline_digest,
      "source_snapshots": baseline.get("source_snapshots", []),
    },
    "candidate": {
      "target": candidate.get("target"),
      "profile": candidate.get("profile"),
      "scope": candidate.get("scope"),
      "digest": candidate_digest,
      "source_snapshots": candidate.get("source_snapshots", []),
    },
    "differences": differences,
    "summary": {
      "difference_count": len(differences),
      "counts_by_type": {name: counts[name] for name in sorted(counts)},
      "has_differences": bool(differences),
      "review": "Differences are observations, not automatic errors or approvals.",
    },
  }


def _load_input(root: Path, name: Any, label: str) -> tuple[dict[str, Any], str]:
  if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
    raise ValueError(f"{label} must be a safe project-relative Target BOM path.")
  path = (root / name).resolve()
  path.relative_to(root)
  if not path.is_file():
    raise FileNotFoundError(f"{label} Target BOM is missing: {name}")
  document = json.loads(path.read_text(encoding="utf-8"))
  if (
    not isinstance(document, dict)
    or document.get("schema_version") != 1
    or not isinstance(document.get("target"), str)
    or not isinstance(document.get("entries"), list)
  ):
    raise ValueError(f"{label} is not a Target BOM document.")
  return document, file_digest(path)


def build_comparison(context: BuildContext, *, output_directory: str = "bom") -> TargetResult:
  try:
    baseline, baseline_digest = _load_input(context.root, context.scope.get("baseline"), "baseline")
    candidate, candidate_digest = _load_input(context.root, context.scope.get("candidate"), "candidate")
    payload = compare_documents(baseline, candidate, baseline_digest, candidate_digest)
  except (OSError, ValueError, json.JSONDecodeError) as error:
    return TargetResult(
      "indeterminate",
      diagnostics=({"rule": "B010", "status": "indeterminate", "message": str(error)},),
      open_items=({"id": "comparison-input-open", "impact": str(error)},),
    )
  payload["target"] = context.target
  path = context.build_path / output_directory / "bom-diff.json"
  write_json(path, payload)
  return TargetResult(
    "pass",
    artifacts=(Artifact("bom-diff", path.relative_to(context.root).as_posix()),),
    details={"summary": payload["summary"]},
  )
