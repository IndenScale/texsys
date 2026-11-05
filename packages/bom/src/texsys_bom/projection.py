"""Composable Target BOM projection independent of any discipline package."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from texsys_bom.contracts import BomContract, canonical_digest, parameter_status
from texsys_bom.snapshots import project_external


@dataclass
class ProjectionResult:
  status: str
  document: dict[str, Any]
  groups: list[dict[str, Any]]
  diagnostics: list[dict[str, Any]]
  open_items: list[dict[str, Any]]
  details: dict[str, Any]


def _design_profile_result(
  profile: str,
  identities: list[str],
  entries: list[dict[str, Any]],
  required_engineering_specifications: tuple[str, ...],
) -> tuple[str, list[dict[str, Any]]]:
  open_items: list[dict[str, Any]] = []
  indeterminate = False
  for identity, item in zip(identities, entries):
    specifications = item["specifications"]
    if not specifications:
      indeterminate = True
      open_items.append(
        {"id": f"missing-functional-spec:{identity}", "item": identity, "impact": "No functional specification is available."}
      )
    for name, specification in sorted(specifications.items()):
      if specification["status"] == "pending":
        open_items.append(
          {"id": f"pending-spec:{identity}:{name}", "item": identity, "impact": f"{name} remains pending."}
        )
    if profile != "engineering":
      continue
    if item["quantity"] != 1:
      indeterminate = True
      open_items.append(
        {"id": f"ungrouped-instance-required:{identity}", "item": identity, "impact": "Engineering BOM requires instance-level quantity 1."}
      )
    for name in required_engineering_specifications:
      if parameter_status(specifications, name) == "pending":
        indeterminate = True
        open_items.append(
          {"id": f"engineering-spec-required:{identity}:{name}", "item": identity, "impact": f"Engineering BOM requires {name}."}
        )
    if item["selection"]["status"] != "approved":
      open_items.append(
        {"id": f"selection-open:{identity}", "item": identity, "impact": "Selection is not approved; this is not procurement evidence."}
      )
  return ("indeterminate" if indeterminate else "pass"), open_items


def aggregate(entries: list[dict[str, Any]], identities: list[str]) -> list[dict[str, Any]]:
  """Aggregate identical neutral configurations without adding domain metrics."""

  groups: dict[str, dict[str, Any]] = {}
  for identity, item in zip(identities, entries):
    key_data = {
      "family": item["family"],
      "specifications": item["specifications"],
      "selection": item["selection"],
      "unit": item["unit"],
    }
    key = canonical_digest(key_data)
    if key not in groups:
      groups[key] = {
        "family": item["family"],
        "selection": item["selection"],
        "specifications": item["specifications"],
        "unit": item["unit"],
        "quantity": 0.0,
        "entry_ids": [],
        "design_instance_ids": [],
      }
    group = groups[key]
    group["quantity"] += item["quantity"]
    group["entry_ids"].append(item.get("_entry_id", identity))
    group["design_instance_ids"].extend(item.get("_design_instance_ids", [identity]))

  result = []
  for group in groups.values():
    group["entry_ids"].sort()
    group["design_instance_ids"] = sorted(set(group["design_instance_ids"]))
    result.append(group)
  return sorted(
    result,
    key=lambda item: (
      item["family"],
      item["selection"].get("model", ""),
      canonical_digest(item["specifications"]),
      item["unit"],
    ),
  )


def project_bom(
  *,
  root: Path,
  target: str,
  profile: str,
  scope: dict[str, Any],
  source_snapshot: dict[str, Any],
  all_design_ids: list[str],
  selected_design_ids: list[str],
  design_entries: list[dict[str, Any]],
  contract: BomContract,
) -> ProjectionResult:
  """Project design or fixed external facts through an explicit rule contract."""

  if profile not in ("feasibility", "engineering", "as-purchased", "as-built", "as-maintained"):
    raise ValueError(f"Unsupported BOM profile: {profile}")
  source_snapshots = [source_snapshot]
  diagnostics: list[dict[str, Any]] = []
  mappings: list[dict[str, Any]] = []
  differences: list[dict[str, Any]] = []
  history: list[dict[str, Any]] = []
  entries = design_entries

  if profile in ("as-purchased", "as-built", "as-maintained"):
    projection = project_external(
      root,
      profile,
      scope,
      all_design_ids,
      selected_design_ids,
      design_entries,
      item_validator=contract.validate_external_item,
    )
    status = projection.status
    open_items = projection.open_items
    diagnostics = projection.diagnostics
    rows = projection.entries
    entries = projection.raw_entries
    entry_ids = projection.entry_ids
    mappings = projection.mappings
    differences = projection.differences
    history = projection.history
    if projection.snapshot is not None:
      source_snapshots.append(projection.snapshot)
  else:
    status, open_items = _design_profile_result(
      profile,
      selected_design_ids,
      entries,
      contract.required_engineering_specifications,
    )
    entry_ids = selected_design_ids
    rows = []
    for identity, item in zip(selected_design_ids, entries):
      parameter_states = {name: value["status"] for name, value in sorted(item["specifications"].items())}
      basis = {
        "type": "design",
        "source_snapshot": source_snapshot["id"],
        "object_id": identity,
        "content_digest": canonical_digest(item),
        "parameter_status": parameter_states,
        "selection_status": item["selection"]["status"],
        "record_version": source_snapshot["record_version"],
        "applicability": dict(scope),
        "unresolved_items": [open_item["id"] for open_item in open_items if open_item.get("item") == identity],
      }
      rows.append(
        {
          "entry_id": identity,
          "design_instance_id": identity,
          "design_instance_ids": [identity],
          **item,
          "item_basis": basis,
        }
      )

  groups = aggregate(entries, entry_ids)
  if contract.decorate_group is not None:
    for group in groups:
      contract.decorate_group(group)
  metrics = {
    "entry_count": len(rows),
    "quantity_by_unit": {
      unit: sum(item["quantity"] for item in entries if item["unit"] == unit)
      for unit in sorted({item["unit"] for item in entries})
    },
    "open_item_count": len(open_items),
    "difference_count": len(differences),
    "identity_deduplication": "entry_id with design identity union",
  }
  payload = {
    "schema_version": 1,
    "target": target,
    "profile": profile,
    "profile_contract_version": 1,
    "scope": dict(scope),
    "source_snapshots": source_snapshots,
    "entries": rows,
    "mappings": mappings,
    "differences": differences,
    "history": history,
    "open_items": open_items,
    "views": {
      contract.view_name: {
        "aggregation_key": ["family", "selection", "specifications", "unit"],
        "unit_policy": "canonical field units; no implicit conversion",
        "groups": groups,
      }
    },
    "evidence": {
      "status": status,
      "backend": contract.backend,
      "metrics": metrics,
      "applicability": "The status applies only to this target, profile and scope.",
    },
  }
  return ProjectionResult(
    status,
    payload,
    groups,
    diagnostics,
    open_items,
    {"metrics": metrics, "view": contract.view_name},
  )
