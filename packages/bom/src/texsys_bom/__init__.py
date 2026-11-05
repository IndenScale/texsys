"""Domain-neutral BOM kernel and generic comparison target."""

from texsys.packages import TexsysPackage
from texsys.targets import TargetDefinition
from texsys_bom.contracts import BomContract
from texsys_bom.projection import project_bom


class BomPackage(TexsysPackage):
  name = "bom"
  version = "0.1.0"

  def targets(self) -> tuple[TargetDefinition, ...]:
    from texsys_bom.compare import build_comparison

    return (
      TargetDefinition(
        id="bom:diff",
        package_name=self.name,
        package_version=self.version,
        builder=build_comparison,
      ),
    )


package = BomPackage()

__all__ = ["BomContract", "package", "project_bom"]
