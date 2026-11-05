"""External-process Target BOM checker used through the hook protocol."""

import json
import os
from pathlib import Path
from typing import Any

from texsys.evidence import write_json


def check_document(
  bom: dict[str, Any], *, view_name: str = "by-family-spec"
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
  diagnostics: list[dict[str, Any]] = []
  open_items: list[dict[str, Any]] = []
  if bom.get("evidence", {}).get("status") != "pass":
    open_items.extend(bom.get("open_items", []))
    return "indeterminate", diagnostics, open_items

  entries = bom.get("entries", [])
  groups = bom.get("views", {}).get(view_name, {}).get("groups", [])
  entry_ids = sorted(item["entry_id"] for item in entries)
  grouped_ids = sorted(identity for group in groups for identity in group["entry_ids"])
  if entry_ids != grouped_ids:
    diagnostics.append(
      {
        "rule": "BOM101",
        "status": "fail",
        "message": "Aggregated view does not preserve the authoritative entry identities.",
      }
    )
  entry_quantity = sum(float(item["quantity"]) for item in entries)
  grouped_quantity = sum(float(group["quantity"]) for group in groups)
  if entry_quantity != grouped_quantity:
    diagnostics.append(
      {
        "rule": "BOM102",
        "status": "fail",
        "message": "Aggregated quantity differs from the authoritative entries.",
      }
    )
  for entry in entries:
    if entry["quantity"] <= 0 or not entry.get("item_basis", {}).get("source_snapshot"):
      diagnostics.append(
        {
          "rule": "BOM103",
          "status": "fail",
          "message": f"Entry lacks quantity or basis: {entry['entry_id']}",
        }
      )
  return ("fail" if diagnostics else "pass"), diagnostics, open_items


def run_checker(checker_id: str, *, view_name: str = "by-family-spec") -> None:
  context_path = Path(os.environ["TEXSYS_HOOK_CONTEXT"])
  result_path = Path(os.environ["TEXSYS_HOOK_RESULT"])
  context = json.loads(context_path.read_text(encoding="utf-8"))
  outputs = [Path(item) for item in context["outputs"]]
  status = "error"
  diagnostics: list[dict[str, Any]] = []
  open_items: list[dict[str, Any]] = []
  try:
    bom_path = Path(context["inputs"][0]["path"])
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    status, diagnostics, open_items = check_document(bom, view_name=view_name)
    report = {
      "schema_version": 1,
      "checker": checker_id,
      "target": context["target"],
      "profile": context["profile"],
      "status": status,
      "diagnostics": diagnostics,
      "open_items": open_items,
    }
    write_json(outputs[0], report)
  except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
    status = "error"
    diagnostics = [{"rule": "BOM100", "status": "error", "message": str(error)}]

  root = Path(context["project_root"])
  result = {
    "status": status,
    "diagnostics": diagnostics,
    "outputs": [path.relative_to(root).as_posix() for path in outputs],
    "open_items": open_items,
  }
  write_json(result_path, result)
  if status == "error":
    raise SystemExit(2)
  if status == "fail":
    raise SystemExit(1)
  if status == "indeterminate":
    raise SystemExit(3)


def main() -> None:
  run_checker("bom-check/0.1.0")


if __name__ == "__main__":
  main()
