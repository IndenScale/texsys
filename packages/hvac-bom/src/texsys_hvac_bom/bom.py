"""HVAC source adapter and view composed with the neutral BOM kernel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from texsys.evidence import file_digest
from texsys_bom.contracts import PROFILES, select_scope, validate_item
from texsys_bom.contracts import BomContract
from texsys_bom.projection import project_bom
from texsys_hvac_equipment.domain import domain_path, equipment_families, load_system
from texsys_hvac_bom.rules import HVAC_CONTRACT
from texsys_hvac_dynamics import has_physics
from texsys_hvac_dynamics.domain import PhysicsUnresolved, compile_model


def _validate_v2(document: Any) -> dict[str, Any]:
  if not isinstance(document, dict) or set(document) != {"schema_version", "equipment"}:
    raise ValueError("HVAC schema v2 requires exactly schema_version and equipment.")
  raw_equipment = document["equipment"]
  if not isinstance(raw_equipment, dict) or not raw_equipment:
    raise ValueError("equipment must be a non-empty object.")
  families = equipment_families()
  for identity, item in raw_equipment.items():
    if not identity or not isinstance(item, dict):
      raise ValueError("Each equipment identity must contain an object.")
    validate_item(identity, item)
    if item["family"] not in families:
      raise ValueError(f"Unsupported equipment family: {identity} ({item['family']}).")
    selection = item["selection"]
    if selection["status"] != "unselected" and selection.get("model") not in families[item["family"]]["models"]:
      raise ValueError(f"Unsupported selected model for equipment: {identity}")
  return document


def load_document(root: Path) -> dict[str, Any] | None:
  path = domain_path(root)
  if not path.is_file():
    return None
  try:
    document = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise ValueError(f"Cannot read src/hvac.json: {error}") from error
  if not isinstance(document, dict) or document.get("schema_version") not in (1, 2):
    raise ValueError("src/hvac.json requires schema_version 1 or 2.")
  if document["schema_version"] == 2:
    validated = _validate_v2(document)
    try:
      sdl = json.loads((root / "src" / "system.sdl.json").read_text(encoding="utf-8"))
      sdl_identities = set(sdl["components"][sdl["root"]]["instances"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
      raise ValueError("Cannot resolve BOM identities from src/system.sdl.json.") from error
    if sdl_identities != set(validated["equipment"]):
      raise ValueError("HVAC v2 equipment identities must exactly match SDL root instances.")
    return validated
  return document


def _v1_entries(root: Path) -> list[dict[str, Any]]:
  system = load_system(root)
  if system is None:
    return []
  entries = []
  for item in system.equipment:
    basis = f"equipment-family:{item.family}/{item.model}"
    entries.append(
      {
        "family": item.family,
        "quantity": 1.0,
        "unit": "ea",
        "selection": {"status": "candidate", "model": item.model},
        "specifications": {
          "capacity_kw": {"value": item.capacity_kw, "status": "known", "basis": basis},
          "heat_rejection_kw": {
            "value": item.heat_rejection_kw,
            "status": "known",
            "basis": basis,
          },
        },
      }
    )
  return entries


def _entries(root: Path, document: dict[str, Any]) -> list[dict[str, Any]]:
  if document["schema_version"] == 1:
    return _v1_entries(root)
  entries = []
  families = equipment_families()
  for identity in sorted(document["equipment"]):
    item = dict(document["equipment"][identity])
    selection = item["selection"]
    if selection["status"] != "unselected":
      performance = families[item["family"]]["models"][selection["model"]]
      specifications = dict(item["specifications"])
      for name in ("capacity_kw", "heat_rejection_kw"):
        if name not in specifications:
          specifications[name] = {
            "value": performance[name],
            "status": "known",
            "basis": f"equipment-family:{item['family']}/{selection['model']}",
          }
      item["specifications"] = specifications
    entries.append(item)
  return entries


def _markdown(profile: str, status: str, groups: list[dict[str, Any]]) -> str:
  lines = [
    "# HVAC Target BOM",
    "",
    f"Profile: `{profile}`  ",
    f"Status: `{status}`",
    "",
    "| Family | Selection | Model | Capacity (kW) | Parameter status | Quantity | Unit | Design identities |",
    "| --- | --- | --- | ---: | --- | ---: | --- | --- |",
  ]
  for group in groups:
    capacity = group["specifications"].get("capacity_kw", {})
    capacity_value = capacity.get("value")
    capacity_text = "pending" if capacity_value is None else f"{capacity_value:g}"
    lines.append(
      f"| {group['family']} | {group['selection']['status']} | "
      f"{group['selection'].get('model', '—')} | {capacity_text} | "
      f"{group['capacity_status']} | {group['quantity']:g} | {group['unit']} | "
      f"{', '.join(group['design_instance_ids'])} |"
    )
  return "\n".join(lines) + "\n"


def build_bom(context, write_artifact) -> TargetResultData:
  try:
    physical = compile_model(context.root) if has_physics(context.root) else None
  except PhysicsUnresolved as error:
    return TargetResultData("indeterminate", open_items=[{"id": "unresolved-physical-input", "impact": str(error)}])
  if physical is not None:
    if domain_path(context.root).exists():
      raise ValueError("A physical SDL model and a separate HVAC equipment ledger cannot both own the equipment facts.")
    identities = [c.identity for c in physical.components if c.kind != "junction"]
    raw_entries = [{"family": c.kind, "quantity": 1, "unit": "ea", "selection": {"status": "unselected"},
                    "specifications": {name: {"value": binding.value, "unit": binding.unit,
                                      "status": binding.state, "basis": binding.source} for name, binding in c.parameters.items()}}
                   for c in physical.components if c.kind != "junction"]
    source_snapshot = {"id": "design:physical-model", "kind": "design", "path": "src/system.sdl.json",
                       "digest": physical.snapshot_digest, "record_version": physical.snapshot_digest,
                       "applicability": "SDL physical instances and their versioned parameter bindings"}
    families = {c.kind for c in physical.components}
    def validate_physical_external(identity, item):
      if item["family"] not in families:
        raise ValueError(f"External item {identity} has no family in this physical design.")
      if any("unit" not in spec for spec in item["specifications"].values()):
        raise ValueError(f"External item {identity} requires explicit specification units.")
    # compile_model validates the family-specific constitutive requirements;
    # pipes and valves must not be forced to invent a cooling capacity.
    contract = BomContract(backend="texsys-hvac-dynamics-bom/1", validate_external_item=validate_physical_external)
  else:
    document = load_document(context.root)
    if document is None:
      return TargetResultData("indeterminate", open_items=[{"id": "missing-hvac-input", "impact": "No HVAC equipment ledger or physical SDL model is available."}])
    raw_entries = _entries(context.root, document)
    identities = sorted(document["equipment"])
    source_path = domain_path(context.root)
    source_digest = file_digest(source_path)
    source_snapshot = {"id": "design:src/hvac.json", "kind": "design", "path": "src/hvac.json",
                       "digest": source_digest, "record_version": source_digest,
                       "applicability": "authoritative design input"}
    contract = HVAC_CONTRACT
  try:
    selected_ids, entries = select_scope(raw_entries, identities, dict(context.scope))
  except ValueError as error:
    return TargetResultData("indeterminate", open_items=[{"id": "invalid-scope", "impact": str(error)}])

  projection = project_bom(
    root=context.root,
    target=context.target,
    profile=context.profile,
    scope=dict(context.scope),
    source_snapshot=source_snapshot,
    all_design_ids=identities,
    selected_design_ids=selected_ids,
    design_entries=entries,
    contract=contract,
  )
  bom_path = context.build_path / "hvac" / "bom.json"
  view_path = context.build_path / "hvac" / "bom.md"
  if physical is not None:
    projection.document["model_digest"] = physical.snapshot_digest
    projection.document["model_sources"] = [{"path": p, "digest": d} for p, d in physical.source_digests]
  write_artifact(bom_path, projection.document)
  view_path.parent.mkdir(parents=True, exist_ok=True)
  if physical is not None:
    lines = ["# 暖通系统 BOM", "", f"配置：{context.profile}；检查：{projection.status}", "",
             "| 类型 | 实例 | 数量 | 规格摘要 | 选型 |", "| --- | --- | ---: | --- | --- |"]
    for group in projection.groups:
      summary = "; ".join(f"{name}={spec.get('value', spec['status'])} {spec.get('unit', '')}" for name, spec in group["specifications"].items())
      lines.append(f"| {group['family']} | {', '.join(group['design_instance_ids'])} | {group['quantity']:g} {group['unit']} | {summary} | {group['selection']['status']} |")
    content = "\n".join(lines) + "\n"
  else:
    content = _markdown(context.profile, projection.status, projection.groups)
  view_path.write_text(content, encoding="utf-8")
  return TargetResultData(
    projection.status,
    artifacts=(("bom-data", bom_path), ("bom-view", view_path)),
    diagnostics=projection.diagnostics,
    open_items=projection.open_items,
    details=projection.details,
  )


class TargetResultData:
  """Transport between the HVAC adapter and its target registration."""

  def __init__(self, status, artifacts=(), diagnostics=(), open_items=(), details=None):
    self.status = status
    self.artifacts = artifacts
    self.diagnostics = diagnostics
    self.open_items = open_items
    self.details = details or {}


__all__ = ["PROFILES", "TargetResultData", "build_bom", "load_document"]
