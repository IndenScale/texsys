"""Equipment-family package, USD BRep projection and installation events."""

from importlib import resources
from pathlib import Path
from typing import Any

from texsys.evidence import write_json
from texsys.geometry import (
  AnalyticBoxKernel,
  GeometryEventSimulator,
  GeometryStimulus,
  InstallationEvent,
)
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys.targets import Artifact, BuildContext, TargetDefinition, TargetResult

from texsys_hvac_equipment.domain import load_system


class HvacEquipmentPackage(TexsysPackage):
  name = "hvac-equipment"
  version = "0.1.0"

  def targets(self) -> tuple[TargetDefinition, ...]:
    return (
      TargetDefinition(
        id="hvac:equipment-layout",
        package_name=self.name,
        package_version=self.version,
        builder=self.build_layout,
        profiles=("engineering",),
        default_profile="engineering",
      ),
    )

  def build_layout(self, context: BuildContext) -> TargetResult:
    paths = self.build(context.root)
    if not paths:
      return TargetResult(
        "indeterminate",
        open_items=({"id": "missing-hvac-input", "impact": "No HVAC layout was generated."},),
      )
    identifiers = ("equipment-data", "layout-scene", "brep-asset")
    return TargetResult(artifacts=tuple(Artifact(key, path) for key, path in zip(identifiers, paths)))

  def build(self, root: Path) -> tuple[str, ...]:
    system = load_system(root)
    if system is None:
      return ()
    project = inspect_project(root)
    if project is None:
      raise ValueError("Project is not initialized.")
    kernel = AnalyticBoxKernel()
    solids = tuple(
      kernel.create_box(item.identity, item.origin, item.size) for item in system.equipment
    )
    scene = root / project.build / "usd" / "layout.usda"
    asset = Path(str(resources.files("texsys").joinpath("assets/unit_brep_cube.usda")))
    kernel.write_usd(solids, scene, asset)
    equipment = root / project.build / "hvac" / "equipment.json"
    write_json(
      equipment,
      {
        "schema_version": 1,
        "equipment": [item.as_dict() for item in system.equipment],
        "geometry_kernel": "AnalyticBoxKernel",
        "brep_representation": "USD BrepArray reference with analytic box operations",
      },
    )
    return tuple(
      path.relative_to(root).as_posix()
      for path in (equipment, scene, scene.parent / "assets" / "unit_brep_cube.usda")
    )

  def simulate(self, root: Path, kind: str) -> dict[str, Any] | None:
    system = load_system(root)
    if system is None or kind != "event":
      return None
    kernel = AnalyticBoxKernel()
    solids = tuple(
      kernel.create_box(item.identity, item.origin, item.size) for item in system.equipment
    )
    events = tuple(InstallationEvent("install", identity) for identity in system.installation_order)
    solution = GeometryEventSimulator(kernel).solve(
      GeometryStimulus(solids, events, system.clearance_m)
    )
    actual = float(len(solution.collisions))
    if solution.status == "error":
      status = "error"
    elif solution.status != "solved":
      status = "indeterminate"
    else:
      status = "pass" if actual == 0 else "fail"
    return {
      "name": "installation-events",
      "kind": "event",
      "status": status,
      "package": {"name": self.name, "version": self.version},
      "solver": GeometryEventSimulator.version,
      "geometry_kernel": type(kernel).__name__,
      "solution": solution.as_dict(),
      "assertions": [
        {
          "metric": "collision_count",
          "operator": "==",
          "expected": 0.0,
          "actual": actual,
          "status": "pass" if actual == 0 else "fail",
          "description": "Installation envelopes and clearance must remain collision-free.",
        }
      ],
    }


package = HvacEquipmentPackage()

__all__ = ["load_system", "package"]
