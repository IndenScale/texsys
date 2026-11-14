"""WBS/BOM projection from the assembled reference design.

Items use the domain-neutral BOM contract; specifications carry the conceptual
(assumed) parameter state and never claim procurement evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from texsys_bom.contracts import validate_item

from .checks import Design, Instance, load_design

UNITS = {
  "it-container": "套", "power-container": "套", "cooling-container": "套",
  "generator-container": "套", "steel-frame-bay": "跨", "fire-partition-wall": "道",
  "raised-access-floor": "块", "dry-type-transformer": "台", "lv-switchboard": "台",
  "ats": "台", "ups-module": "台", "battery-cabinet": "台", "busway": "段",
  "rack-pdu": "台", "diesel-generator": "台", "water-cooled-chiller": "台",
  "cooling-tower": "台", "circulation-pump": "台", "condenser-water-pump": "台",
  "utility-feed": "路",
  "liquid-cooled-rack": "台", "coolant-distribution-unit": "台", "precision-air-unit": "台",
  "steel-support": "根", "wall-panel": "块",
}


def _specifications(instance: Instance) -> dict[str, Any]:
  specifications: dict[str, Any] = {}
  for name, binding in instance.parameters.items():
    if not isinstance(binding, dict) or "value" not in binding:
      continue
    specification: dict[str, Any] = {
      "value": binding["value"],
      "status": binding.get("state", "assumed"),
      "basis": binding.get("source", "Texsys 数据中心参考设计库概念假设"),
    }
    if binding.get("unit"):
      specification["unit"] = binding["unit"]
    specifications[name] = specification
  return specifications


def _items(design: Design, prefix: str, instances: dict[str, Instance],
           zones: dict[str, str], wbs: dict[str, Any]) -> list[dict[str, Any]]:
  items: list[dict[str, Any]] = []
  for name, instance in instances.items():
    identity = f"{prefix}{name}"
    if instance.children:
      items.extend(_items(design, f"{identity}/", instance.children, zones, wbs))
      continue
    family = instance.family
    item = {
      "family": family,
      "quantity": 1,
      "unit": UNITS.get(family, "件"),
      "selection": {"status": "candidate", "model": f"datacenter-reference/{family}"},
      "specifications": _specifications(instance),
    }
    top = identity.split("/", 1)[0]
    zone = zones.get(top)
    if zone:
      item["space"] = {"zone": zone}
    validate_item(identity, item)
    items.append({"id": identity, "wbs": wbs.get("assignment", {}).get(top, "WBS-00"), **item})
  return items


def project_reference_bom(source: Path, root: Path) -> dict[str, Any]:
  design = load_design(source, root)
  zones = {name: zone for zone, members in design.placement.get("zones", {}).items()
           for name in members}
  wbs_path = source / "wbs.json"
  wbs = json.loads(wbs_path.read_text(encoding="utf-8")) if wbs_path.is_file() else {}
  items = _items(design, "", design.instances, zones, wbs)

  groups: dict[str, dict[str, Any]] = {}
  for item in items:
    key = item["family"]
    group = groups.setdefault(key, {"family": key, "unit": item["unit"], "quantity": 0,
                                    "selection": item["selection"], "instances": []})
    group["quantity"] += item["quantity"]
    group["instances"].append(item["id"])

  by_wbs: dict[str, list[str]] = {}
  for item in items:
    by_wbs.setdefault(item["wbs"], []).append(item["id"])

  return {
    "schema_version": 1,
    "status": "pass",
    "profile": "feasibility",
    "items": items,
    "groups": sorted(groups.values(), key=lambda g: g["family"]),
    "wbs": {node: {"title": wbs.get("nodes", {}).get(node, ""), "items": members}
            for node, members in sorted(by_wbs.items())},
    "fidelity": "concept",
    "boundary": "选型状态为 candidate；参数为脱敏概念假设，不构成采购或工程接受依据。",
  }
