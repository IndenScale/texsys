"""Canonical HVAC input formatting and deterministic human-readable projections."""

import difflib
import json
from pathlib import Path

from texsys.evidence import write_json
from texsys.formal import FormatResult
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys.targets import Artifact, BuildContext, TargetDefinition, TargetResult
from texsys_hvac_equipment.domain import domain_path, load_system


class HvacFormatterPackage(TexsysPackage):
  name = "hvac-formatter"
  version = "0.1.0"

  def targets(self) -> tuple[TargetDefinition, ...]:
    return (
      TargetDefinition(
        id="hvac:equipment-projection",
        package_name=self.name,
        package_version=self.version,
        builder=self.build_projection,
        profiles=("engineering",),
        default_profile="engineering",
      ),
    )

  def build_projection(self, context: BuildContext) -> TargetResult:
    paths = self.build(context.root)
    if not paths:
      return TargetResult(
        "indeterminate",
        open_items=({"id": "missing-hvac-input", "impact": "No HVAC projection was generated."},),
      )
    identifiers = ("equipment-table", "connection-topology")
    return TargetResult(artifacts=tuple(Artifact(key, path) for key, path in zip(identifiers, paths)))

  def format(self, root: Path, check: bool = False, diff: bool = False) -> FormatResult | None:
    path = domain_path(root)
    if not path.is_file():
      return None
    original = path.read_text(encoding="utf-8")
    formatted = json.dumps(json.loads(original), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if original == formatted:
      return FormatResult((), (), "")
    difference = ""
    if diff:
      difference = "".join(
        difflib.unified_diff(
          original.splitlines(keepends=True),
          formatted.splitlines(keepends=True),
          fromfile="a/src/hvac.json",
          tofile="b/src/hvac.json",
        )
      )
    if not check and not diff:
      path.write_text(formatted, encoding="utf-8")
    return FormatResult(("src/hvac.json",), (), difference)

  def build(self, root: Path) -> tuple[str, ...]:
    system = load_system(root)
    if system is None:
      return ()
    project = inspect_project(root)
    if project is None:
      raise ValueError("Project is not initialized.")
    lines = [
      "# HVAC equipment projection",
      "",
      "| Identity | Family | Concept model | Capacity (kW) | Heat rejection (kW) |",
      "| --- | --- | --- | ---: | ---: |",
    ]
    for item in system.equipment:
      lines.append(
        f"| {item.identity} | {item.family} | {item.model} | "
        f"{item.capacity_kw:g} | {item.heat_rejection_kw:g} |"
      )
    table = root / project.build / "hvac" / "equipment.md"
    table.parent.mkdir(parents=True, exist_ok=True)
    table.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sdl = json.loads((root / "src" / "system.sdl.json").read_text(encoding="utf-8"))
    topology = root / project.build / "hvac" / "topology.json"
    write_json(
      topology,
      {
        "schema_version": 1,
        "root": sdl["root"],
        "connections": sdl["components"][sdl["root"]].get("connections", []),
      },
    )
    return tuple(path.relative_to(root).as_posix() for path in (table, topology))


package = HvacFormatterPackage()
