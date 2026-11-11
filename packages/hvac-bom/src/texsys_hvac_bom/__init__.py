"""HVAC Target BOM package."""

import json
from pathlib import Path

from texsys.evidence import write_json
from texsys.formal import Diagnostic
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys_hvac_dynamics.inputs import physical_projection_inputs
from texsys.targets import Artifact, BuildContext, TargetDefinition, TargetResult
from texsys_hvac_equipment.domain import domain_path

from texsys_hvac_bom.bom import PROFILES, build_bom, load_document
from texsys_hvac_bom.compare import build_comparison


class HvacBomPackage(TexsysPackage):
  name = "hvac-bom"
  version = "0.1.0"

  def check(self, root: Path) -> tuple[Diagnostic, ...]:
    if not domain_path(root).is_file():
      return ()
    try:
      load_document(root)
    except ValueError as error:
      return (Diagnostic("B001", "fail", "src/hvac.json", str(error)),)
    return ()

  def targets(self) -> tuple[TargetDefinition, ...]:
    return (
      TargetDefinition(
        id="hvac:equipment-bom",
        package_name=self.name,
        package_version=self.version,
        builder=self.build_target,
        profiles=PROFILES,
        default_profile="feasibility",
        default_scope={"include": "all"},
        input_patterns=physical_projection_inputs,
      ),
      TargetDefinition(
        id="hvac:equipment-bom-diff",
        package_name=self.name,
        package_version=self.version,
        builder=build_comparison,
      ),
    )

  def build_target(self, context: BuildContext) -> TargetResult:
    result = build_bom(context, write_json)
    info = inspect_project(context.root)
    sdl_path = Path(info.source) / "system.sdl.json"
    sdl = json.loads((context.root / sdl_path).read_text()) if (context.root / sdl_path).is_file() else None
    root = sdl["root"] if sdl else info.name
    source_map = ({f"{root}.{name}": {"file": sdl_path.as_posix(), "path": f"components.{root}.instances.{name}"}
                   for name in sdl["components"][root].get("instances", {})} if sdl else {})
    if domain_path(context.root).is_file():
      source_map.update({name: {"file": "src/hvac.json", "path": f"equipment.{name}"}
                         for name in load_document(context.root)["equipment"]})
    external = context.profile in {"as-purchased", "as-built", "as-maintained"}
    incomplete = external and (any(item["id"].startswith(("missing-", "pending-external-mapping")) for item in result.open_items)
                               or any(item.get("rule") in {"B002", "B003"} for item in result.diagnostics))
    artifacts = tuple(
      Artifact(identifier, path.relative_to(context.root).as_posix(), complete=not incomplete,
               preview={"kind": "bom", "title": "设备清单", "root": root, "source_map": source_map} if identifier == "bom-data" else None)
      for identifier, path in result.artifacts
    )
    return TargetResult(
      result.status,
      artifacts,
      diagnostics=tuple(result.diagnostics),
      open_items=tuple(result.open_items),
      details=result.details,
    )


package = HvacBomPackage()
