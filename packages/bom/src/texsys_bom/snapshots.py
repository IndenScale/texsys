"""Fixed external-fact snapshots and design/procurement/asset mappings."""

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from texsys.evidence import file_digest
from texsys_bom.contracts import canonical_digest, validate_item


@dataclass
class ExternalProjection:
  status: str
  snapshot: dict[str, Any] | None = None
  entries: list[dict[str, Any]] = field(default_factory=list)
  raw_entries: list[dict[str, Any]] = field(default_factory=list)
  entry_ids: list[str] = field(default_factory=list)
  mappings: list[dict[str, Any]] = field(default_factory=list)
  open_items: list[dict[str, Any]] = field(default_factory=list)
  diagnostics: list[dict[str, Any]] = field(default_factory=list)
  differences: list[dict[str, Any]] = field(default_factory=list)
  history: list[dict[str, Any]] = field(default_factory=list)


def _load(
  root: Path,
  profile: str,
  scope: dict[str, Any],
  item_validator: Callable[[str, dict[str, Any]], None] | None,
) -> tuple[dict[str, Any] | None, Path | None, str | None]:
  name = scope.get("snapshot")
  if name is None:
    return None, None, f"{profile} requires scope.snapshot to reference a fixed external evidence snapshot."
  if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
    raise ValueError("scope.snapshot must be a safe project-relative path.")
  path = (root / name).resolve()
  path.relative_to((root / "assets").resolve())
  if not path.is_file():
    return None, path, f"External evidence snapshot is missing: {name}"
  try:
    document = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise ValueError(f"Cannot read external evidence snapshot: {error}") from error
  if not isinstance(document, dict) or set(document) != {
    "schema_version",
    "profile",
    "source",
    "items",
    "mappings",
  }:
    raise ValueError("External snapshot has missing or unknown top-level fields.")
  if document["schema_version"] != 1 or document["profile"] != profile:
    raise ValueError(f"External snapshot profile must be {profile}.")
  source = document["source"]
  if not isinstance(source, dict) or set(source) != {"system", "record_version", "effective_at"}:
    raise ValueError("External snapshot source requires system, record_version and effective_at.")
  if not all(isinstance(value, str) and value for value in source.values()):
    raise ValueError("External snapshot source fields must be non-empty strings.")
  if not isinstance(document["items"], dict) or not document["items"]:
    raise ValueError("External snapshot items must be a non-empty object.")
  for identity, item in document["items"].items():
    validate_item(identity, item, external=True)
    if item_validator is not None:
      item_validator(identity, item)
  if not isinstance(document["mappings"], list):
    raise ValueError("External snapshot mappings must be an array.")
  return document, path, None


def _review(item: dict[str, Any]) -> dict[str, Any]:
  return item.get("difference_review", {"status": "unreviewed", "basis": "not reviewed"})


def project_external(
  root: Path,
  profile: str,
  scope: dict[str, Any],
  all_design_ids: list[str],
  selected_design_ids: list[str],
  design_entries: list[dict[str, Any]],
  *,
  item_validator: Callable[[str, dict[str, Any]], None] | None = None,
) -> ExternalProjection:
  try:
    document, path, missing = _load(root, profile, scope, item_validator)
  except ValueError as error:
    return ExternalProjection(
      "fail", diagnostics=[{"rule": "B002", "status": "fail", "message": str(error)}]
    )
  if document is None:
    return ExternalProjection(
      "indeterminate",
      open_items=[{"id": f"missing-{profile}-snapshot", "impact": missing}],
    )

  relative_path = path.relative_to(root).as_posix()
  digest = file_digest(path)
  source = document["source"]
  snapshot_id = f"{profile}:{relative_path}"
  snapshot = {
    "id": snapshot_id,
    "kind": profile,
    "path": relative_path,
    "digest": digest,
    "source_system": source["system"],
    "record_version": source["record_version"],
    "effective_at": source["effective_at"],
  }
  items = document["items"]
  raw_mappings = document["mappings"]
  mappings_by_design: dict[str, list[dict[str, Any]]] = {}
  mappings_by_item: dict[str, list[dict[str, Any]]] = {}
  seen_mappings: set[tuple[str, str]] = set()
  normalized_mappings: list[dict[str, Any]] = []
  try:
    for mapping in raw_mappings:
      required = {"design_instance_id", "external_item_id", "relation", "status", "basis"}
      if not isinstance(mapping, dict) or set(mapping) != required:
        raise ValueError("Each external mapping requires exactly the declared mapping fields.")
      design_id = mapping["design_instance_id"]
      external_id = mapping["external_item_id"]
      if design_id not in all_design_ids or external_id not in items:
        raise ValueError(f"External mapping references an unknown object: {design_id}/{external_id}")
      if mapping["relation"] != "implemented-by" or mapping["status"] not in ("confirmed", "pending"):
        raise ValueError("External mapping has an invalid relation or status.")
      if not isinstance(mapping["basis"], str) or not mapping["basis"]:
        raise ValueError("External mapping requires a basis.")
      pair = (design_id, external_id)
      if pair in seen_mappings:
        raise ValueError(f"Duplicate external mapping: {design_id}/{external_id}")
      seen_mappings.add(pair)
      normalized = {**mapping, "record_version": source["record_version"]}
      normalized_mappings.append(normalized)
      mappings_by_design.setdefault(design_id, []).append(normalized)
      mappings_by_item.setdefault(external_id, []).append(normalized)
  except ValueError as error:
    return ExternalProjection(
      "fail",
      snapshot=snapshot,
      diagnostics=[{"rule": "B003", "status": "fail", "message": str(error)}],
    )

  selected_set = set(selected_design_ids)
  scoped_mappings = [item for item in normalized_mappings if item["design_instance_id"] in selected_set]
  external_ids = sorted({item["external_item_id"] for item in scoped_mappings})
  open_items: list[dict[str, Any]] = []
  differences: list[dict[str, Any]] = []
  design_by_id = dict(zip(selected_design_ids, design_entries))
  for design_id in selected_design_ids:
    if design_id not in mappings_by_design:
      open_items.append(
        {
          "id": f"missing-external-mapping:{design_id}",
          "item": design_id,
          "impact": f"No {profile} item is mapped to this scoped design object.",
        }
      )
  for mapping in scoped_mappings:
    if mapping["status"] == "pending":
      open_items.append(
        {
          "id": f"pending-external-mapping:{mapping['design_instance_id']}:{mapping['external_item_id']}",
          "item": mapping["design_instance_id"],
          "impact": mapping["basis"],
        }
      )

  rows: list[dict[str, Any]] = []
  raw_entries: list[dict[str, Any]] = []
  identity_key = "procurement_item_id" if profile == "as-purchased" else "physical_asset_id"
  for external_id in external_ids:
    item = items[external_id]
    if item.get("status", "active") != "active":
      continue
    mapped = [item["design_instance_id"] for item in mappings_by_item[external_id] if item["design_instance_id"] in selected_set]
    mapped = sorted(set(mapped))
    if not mapped:
      continue
    if item["selection"]["status"] != "approved":
      open_items.append(
        {"id": f"selection-not-approved:{external_id}", "item": external_id, "impact": "External fact items require approved selection."}
      )
    if not item["specifications"]:
      open_items.append(
        {"id": f"missing-external-configuration:{external_id}", "item": external_id, "impact": "External configuration is missing."}
      )
    for name, specification in item["specifications"].items():
      if specification["status"] != "known":
        open_items.append(
          {"id": f"unconfirmed-external-spec:{external_id}:{name}", "item": external_id, "impact": "External fact specifications must be known."}
        )
    if profile in ("as-built", "as-maintained") and "space" not in item:
      open_items.append(
        {"id": f"missing-installed-location:{external_id}", "item": external_id, "impact": "Installed assets require a location."}
      )
    if profile == "as-maintained" and "effective_at" not in item:
      open_items.append(
        {"id": f"missing-maintenance-effective-time:{external_id}", "item": external_id, "impact": "Maintenance items require effective_at."}
      )

    if len(mapped) == 1 and len(mappings_by_design[mapped[0]]) == 1:
      design = design_by_id[mapped[0]]
      review = _review(item)
      if item["quantity"] != design["quantity"] or item["unit"] != design["unit"]:
        differences.append(
          {
            "type": "quantity-change",
            "design_instance_id": mapped[0],
            "external_item_id": external_id,
            "design": {"quantity": design["quantity"], "unit": design["unit"]},
            "external": {"quantity": item["quantity"], "unit": item["unit"]},
            "reason": review["basis"],
            "review_status": review["status"],
          }
        )
      if item["specifications"] != design["specifications"] or item["selection"] != design["selection"]:
        differences.append(
          {
            "type": "configuration-change",
            "design_instance_id": mapped[0],
            "external_item_id": external_id,
            "reason": review["basis"],
            "review_status": review["status"],
          }
        )
      if item.get("space") != design.get("space") and "space" in item:
        differences.append(
          {
            "type": "location-change",
            "design_instance_id": mapped[0],
            "external_item_id": external_id,
            "design": design.get("space"),
            "external": item["space"],
            "reason": review["basis"],
            "review_status": review["status"],
          }
        )

    parameter_status = {name: value["status"] for name, value in sorted(item["specifications"].items())}
    basis = {
      "type": profile,
      "source_snapshot": snapshot_id,
      "object_id": external_id,
      "content_digest": canonical_digest(item),
      "parameter_status": parameter_status,
      "selection_status": item["selection"]["status"],
      "record_version": source["record_version"],
      "effective_at": item.get("effective_at", source["effective_at"]),
      "applicability": scope,
      "unresolved_items": [],
    }
    public_item = {
      key: value
      for key, value in item.items()
      if key not in ("design_change", "difference_review", "replaces")
    }
    row = {
      "entry_id": external_id,
      identity_key: external_id,
      "design_instance_ids": mapped,
      **public_item,
      "item_basis": basis,
    }
    rows.append(row)
    raw_entries.append({**public_item, "_entry_id": external_id, "_design_instance_ids": mapped})

  history: list[dict[str, Any]] = []
  if profile == "as-maintained":
    for external_id, item in sorted(items.items()):
      if item.get("status") == "retired":
        history.append({"physical_asset_id": external_id, **item})
    for row in rows:
      item = items[row["entry_id"]]
      replaced = item.get("replaces")
      if replaced is None:
        continue
      if replaced not in items or items[replaced].get("status") != "retired":
        return ExternalProjection(
          "fail",
          snapshot=snapshot,
          diagnostics=[{"rule": "B004", "status": "fail", "message": f"Replacement must reference a retired asset: {row['entry_id']}"}],
        )
      previous = items[replaced]
      history.append(
        {
          "relation": "replaces",
          "physical_asset_id": row["entry_id"],
          "replaces_physical_asset_id": replaced,
          "effective_at": item["effective_at"],
        }
      )
      differences.append(
        {
          "type": "replacement",
          "external_item_id": row["entry_id"],
          "replaces_physical_asset_id": replaced,
          "reason": item.get("design_change", {}).get("basis", "maintenance replacement"),
          "review_status": item.get("design_change", {}).get("status", "unreviewed"),
        }
      )
      changed = item["family"] != previous["family"] or item["specifications"] != previous["specifications"]
      if changed and item.get("design_change", {}).get("status") != "approved":
        open_items.append(
          {
            "id": f"design-change-required:{row['entry_id']}",
            "item": row["entry_id"],
            "impact": "Replacement changes family, capacity, interface or configuration and requires design review.",
          }
        )

  for row in rows:
    row["item_basis"]["unresolved_items"] = [
      item["id"] for item in open_items if item.get("item") == row["entry_id"]
    ]
  status = "indeterminate" if open_items else "pass"
  return ExternalProjection(
    status,
    snapshot,
    rows,
    raw_entries,
    [row["entry_id"] for row in rows],
    scoped_mappings,
    open_items,
    [],
    differences,
    history,
  )
