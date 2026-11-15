"""Data center reference design library: components, design checks and BOM."""

from texsys.evidence import write_json
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys.targets import Artifact, TargetDefinition, TargetResult

from .bom import project_reference_bom
from .checks import evaluate_design_checks
from .fws_design import build_hydraulics, build_routes
from .plans import build_plan
from .review import build_review_package
from .spatial_design import build_grid, build_layout, build_levels, build_site, spatial_inputs


class DatacenterReferencePackage(TexsysPackage):
  name = "datacenter-reference"
  version = "0.2.0"

  def targets(self) -> tuple[TargetDefinition, ...]:
    return (
      TargetDefinition("dcr:design-checks", self.name, self.version, self.build_checks,
                       profiles=("concept",), default_profile="concept",
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "design-basis.json",
                                                     "placement.study.json")),
      TargetDefinition("dcr:bom", self.name, self.version, self.build_bom,
                       profiles=("concept",), default_profile="concept",
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "design-basis.json",
                                                     "placement.study.json", "wbs.json")),
      TargetDefinition("dcr:site", self.name, self.version, build_site, input_patterns=spatial_inputs("site.json")),
      TargetDefinition("dcr:grid", self.name, self.version, build_grid, dependencies=("dcr:site",),
                       input_patterns=spatial_inputs("grid.json")),
      TargetDefinition("dcr:levels", self.name, self.version, build_levels, dependencies=("dcr:grid",),
                       input_patterns=spatial_inputs("levels.json")),
      TargetDefinition("dcr:layout", self.name, self.version, build_layout, dependencies=("dcr:levels",),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml",
                                                     "coordination.json", "placement.study.json")),
      TargetDefinition("dcr:fws-routing", self.name, self.version, build_routes, dependencies=("dcr:layout",),
                       input_patterns=spatial_inputs("system.sdl.json", "fws-routing.json")),
      TargetDefinition("dcr:fws-hydraulics", self.name, self.version, build_hydraulics,
                       dependencies=("dcr:fws-routing",), input_patterns=spatial_inputs("fws-hydraulics.json")),
      TargetDefinition("dcr:site-plan", self.name, self.version, build_plan, dependencies=("dcr:site",),
                       input_patterns=(), named_output=True),
      TargetDefinition("dcr:grid-plan", self.name, self.version, build_plan, dependencies=("dcr:grid",),
                       input_patterns=(), named_output=True),
      TargetDefinition("dcr:zoning-plan", self.name, self.version, build_plan, dependencies=("dcr:levels",),
                       input_patterns=(), named_output=True),
      TargetDefinition("dcr:plan", self.name, self.version, build_plan, dependencies=("dcr:layout",),
                       input_patterns=(), named_output=True),
      TargetDefinition("dcr:fws-plan", self.name, self.version, build_plan, dependencies=("dcr:fws-routing",),
                       input_patterns=(), named_output=True),
      TargetDefinition("dcr:review", self.name, self.version, build_review_package, named_output=True),
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
