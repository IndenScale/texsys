"""HVAC Target BOM package."""

from pathlib import Path

from texsys.evidence import write_json
from texsys.formal import Diagnostic
from texsys.packages import TexsysPackage
from texsys.targets import Artifact, BuildContext, TargetDefinition, TargetResult
from texsys_hvac_equipment.domain import domain_path

from texsys_hvac_bom.bom import PROFILES, build_bom, load_document
from texsys_hvac_bom.compare import build_comparison


class HvacBomPackage(TexsysPackage):
  name = "hvac-bom"
  version = "0.1.0"

  def lint(self, root: Path) -> tuple[Diagnostic, ...]:
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
    artifacts = tuple(
      Artifact(identifier, path.relative_to(context.root).as_posix())
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
