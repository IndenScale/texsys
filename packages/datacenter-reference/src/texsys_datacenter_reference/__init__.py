"""Data center reference design library: components, design checks and BOM."""

from texsys.evidence import write_json
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys.targets import Artifact, TargetDefinition, TargetResult

from .bom import project_reference_bom
from .checks import evaluate_design_checks


class DatacenterReferencePackage(TexsysPackage):
  name = "datacenter-reference"
  version = "0.1.0"

  def targets(self) -> tuple[TargetDefinition, ...]:
    return (
      TargetDefinition("dcr:design-checks", self.name, self.version, self.build_checks,
                       profiles=("concept",), default_profile="concept"),
      TargetDefinition("dcr:bom", self.name, self.version, self.build_bom,
                       profiles=("concept",), default_profile="concept"),
    )

  def build_checks(self, context) -> TargetResult:
    project = inspect_project(context.root)
    source = context.root / project.source
    missing = [name for name in ("system.sdl.json", "design-basis.json")
               if not (source / name).is_file()]
    if missing:
      return TargetResult("indeterminate", open_items=tuple(
        {"id": f"missing-{name}", "impact": f"Design checks require src/{name}."}
        for name in missing))
    report = evaluate_design_checks(source, context.root)
    path = context.build_path / "datacenter-reference/design-checks.json"
    write_json(path, report)
    diagnostics = tuple({"check": c["id"], "message": c["message"]}
                        for c in report["checks"] if c["status"] != "pass")
    return TargetResult(report["status"],
                        (Artifact("design-checks", path.relative_to(context.root).as_posix()),),
                        details={"fidelity": "concept", "grade": report.get("grade")},
                        diagnostics=diagnostics)

  def build_bom(self, context) -> TargetResult:
    project = inspect_project(context.root)
    report = project_reference_bom(context.root / project.source, context.root)
    path = context.build_path / "datacenter-reference/bom.json"
    write_json(path, report)
    return TargetResult(report["status"],
                        (Artifact("bom", path.relative_to(context.root).as_posix()),),
                        details={"fidelity": "concept", "items": len(report["items"])})


package = DatacenterReferencePackage()

__all__ = ["package"]
