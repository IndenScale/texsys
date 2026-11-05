"""HVAC-owned rules composed into the neutral BOM kernel."""

from typing import Any

from texsys_bom.contracts import BomContract
from texsys_hvac_equipment.domain import equipment_families


def validate_external_item(identity: str, item: dict[str, Any]) -> None:
  families = equipment_families()
  if item["family"] not in families:
    raise ValueError(f"External item {identity} has an unsupported HVAC family.")
  selection = item["selection"]
  if selection["status"] != "unselected" and selection.get("model") not in families[item["family"]]["models"]:
    raise ValueError(f"External item {identity} has an unsupported HVAC model.")


def decorate_capacity_group(group: dict[str, Any]) -> None:
  capacity = group["specifications"].get("capacity_kw")
  if capacity is not None and "value" in capacity:
    group["total_capacity_kw"] = group["quantity"] * capacity["value"]
    group["capacity_status"] = capacity["status"]
  else:
    group["capacity_status"] = "pending"


HVAC_CONTRACT = BomContract(
  backend="texsys-hvac-bom/0.1.0",
  required_engineering_specifications=("capacity_kw", "heat_rejection_kw"),
  validate_external_item=validate_external_item,
  decorate_group=decorate_capacity_group,
)
