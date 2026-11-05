"""Domain-neutral BOM data contracts and explicit composition points."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from typing import Any

PROFILES = ("feasibility", "engineering", "as-purchased", "as-built", "as-maintained")
PARAMETER_STATUSES = {"known", "assumed", "pending"}
SELECTION_STATUSES = {"unselected", "candidate", "approved"}

EntryValidator = Callable[[str, dict[str, Any]], None]
GroupDecorator = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class BomContract:
  """Composable discipline rules; the neutral kernel never imports their owners."""

  backend: str
  required_engineering_specifications: tuple[str, ...] = ()
  validate_external_item: EntryValidator | None = None
  decorate_group: GroupDecorator | None = None
  view_name: str = "by-family-spec"


def canonical_digest(value: Any) -> str:
  content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
  return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def finite_number(value: Any, label: str, *, positive: bool = False) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ValueError(f"{label} must be a finite number.")
  result = float(value)
  if not (-float("inf") < result < float("inf")) or (positive and result <= 0):
    raise ValueError(f"{label} must be {'positive' if positive else 'finite'}.")
  return result


def parameter_status(specifications: dict[str, Any], name: str) -> str:
  specification = specifications.get(name)
  return "pending" if specification is None else specification["status"]


def validate_item(identity: str, item: Any, *, external: bool = False) -> None:
  """Validate neutral item structure without interpreting discipline fields."""

  required = {"family", "quantity", "unit", "selection", "specifications"}
  allowed = required | {"space"}
  if external:
    allowed |= {"status", "effective_at", "replaces", "design_change", "difference_review"}
  if not identity or not isinstance(item, dict) or not required <= set(item) or set(item) - allowed:
    raise ValueError(f"BOM item {identity} has missing or unknown fields.")
  if not isinstance(item["family"], str) or not item["family"]:
    raise ValueError(f"BOM item {identity} requires a non-empty family reference.")
  finite_number(item["quantity"], f"item.{identity}.quantity", positive=True)
  if not isinstance(item["unit"], str) or not item["unit"]:
    raise ValueError(f"item.{identity}.unit must be a non-empty string.")

  selection = item["selection"]
  if (
    not isinstance(selection, dict)
    or set(selection) - {"status", "model"}
    or selection.get("status") not in SELECTION_STATUSES
  ):
    raise ValueError(f"BOM item {identity} has an invalid selection.")
  if selection["status"] == "unselected":
    if "model" in selection:
      raise ValueError(f"Unselected BOM item {identity} cannot declare a model.")
  elif not isinstance(selection.get("model"), str) or not selection["model"]:
    raise ValueError(f"Selected BOM item {identity} requires a model reference.")

  specifications = item["specifications"]
  if not isinstance(specifications, dict):
    raise ValueError(f"BOM item {identity} has invalid specifications.")
  for name, specification in specifications.items():
    if not name or not isinstance(specification, dict):
      raise ValueError(f"Invalid specification for BOM item: {identity}")
    if set(specification) - {"value", "status", "basis", "unit"} or not {"status", "basis"} <= set(specification):
      raise ValueError(f"Specification {identity}.{name} requires status and basis.")
    if "unit" in specification and (not isinstance(specification["unit"], str) or not specification["unit"]):
      raise ValueError(f"Specification {identity}.{name} has an invalid unit.")
    if specification["status"] not in PARAMETER_STATUSES:
      raise ValueError(f"Specification {identity}.{name} has an invalid status.")
    if not isinstance(specification["basis"], str) or not specification["basis"]:
      raise ValueError(f"Specification {identity}.{name} requires a basis.")
    if specification["status"] == "pending":
      if "value" in specification:
        raise ValueError(f"Pending specification {identity}.{name} cannot have a value.")
    else:
      if "value" not in specification:
        raise ValueError(f"Specification {identity}.{name} requires a value.")
      finite_number(specification["value"], f"item.{identity}.specifications.{name}.value")

  if "space" in item and (
    not isinstance(item["space"], dict)
    or not all(isinstance(key, str) and isinstance(value, str) for key, value in item["space"].items())
  ):
    raise ValueError(f"BOM item {identity} has invalid space data.")
  if not external:
    return
  if item.get("status", "active") not in ("active", "retired"):
    raise ValueError(f"External item {identity} has an invalid lifecycle status.")
  for name in ("effective_at", "replaces"):
    if name in item and (not isinstance(item[name], str) or not item[name]):
      raise ValueError(f"External item {identity}.{name} must be a non-empty string.")
  for name in ("design_change", "difference_review"):
    if name in item and (
      not isinstance(item[name], dict)
      or item[name].get("status") not in ("approved", "unreviewed", "pending")
      or not isinstance(item[name].get("basis"), str)
      or not item[name]["basis"]
    ):
      raise ValueError(f"External item {identity}.{name} is invalid.")


def select_scope(
  entries: list[dict[str, Any]], identities: list[str], scope: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]]]:
  if set(scope) - {"include", "identities", "snapshot"}:
    raise ValueError("BOM scope supports only include, identities and snapshot.")
  if "identities" in scope:
    requested = scope["identities"]
    if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
      raise ValueError("BOM scope.identities must be an array of strings.")
    missing = sorted(set(requested) - set(identities))
    if missing:
      raise ValueError(f"BOM scope contains unknown design identities: {', '.join(missing)}")
    selected = [item for item in requested if item in identities]
  elif scope.get("include", "all") == "all":
    selected = identities
  else:
    raise ValueError("BOM scope.include currently supports only 'all'.")
  positions = {identity: index for index, identity in enumerate(identities)}
  return selected, [entries[positions[item]] for item in selected]
