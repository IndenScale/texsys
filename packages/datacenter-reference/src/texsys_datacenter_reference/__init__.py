"""Data center reference design library: components, design checks and BOM."""

from texsys.evidence import write_json
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys.targets import Artifact, TargetDefinition, TargetResult

from .air_routing import build_air_coordination, build_air_routing, build_air_scene
from .air_study import build_air_check, build_air_study
from .air_views import build_air_view
from .architecture import build_architecture
from .bom import project_reference_bom
from .building_views import build_building_view
from .checks import evaluate_design_checks
from .coordination import build_building_coordination, build_coordinated_scene, build_fws_network_coordination
from .cw_network import build_cw_coordination, build_cw_network
from .cw_study import build_cw_check, build_cw_study
from .cw_views import build_cw_view
from .electrical import build_power_study, build_power_topology
from .fire_egress import build_egress_check, build_egress_model
from .fire_systems import build_fire_systems
from .fire_systems_coordination import build_fire_systems_coordination
from .fire_systems_study import build_fire_systems_study
from .fire_systems_views import build_fire_systems_view
from .fws_buffer_views import build_buffer_view
from .fws_design import build_hydraulics, build_routes
from .fws_network import build_network
from .fws_network_study import build_network_study
from .fws_network_views import build_network_view
from .fws_thermal_parameters import build_thermal_parameters
from .fws_transient import build_transient, build_transient_check
from .fws_transient_views import build_transient_view
from .plans import build_plan
from .pod_layout import build_pod_coordination, build_pod_layout, build_pod_scene
from .pod_views import build_pod_view
from .power_views import build_power_view
from .review import build_review_package
from .spatial_design import build_grid, build_layout, build_levels, build_site, spatial_inputs
from .structure import build_gravity, build_structure
from .structure_views import build_structure_view
from .tcs_routing import build_tcs_coordination, build_tcs_routing
from .tcs_study import build_tcs_check, build_tcs_study
from .tcs_views import build_tcs_view
from .telecom import build_telecom
from .telecom_coordination import build_telecom_coordination
from .telecom_study import build_telecom_study
from .telecom_views import build_telecom_view
from .wet_utilities import build_wet_coordination, build_wet_utilities
from .wet_utility_study import build_wet_check, build_wet_study
from .wet_utility_views import build_wet_view


class DatacenterReferencePackage(TexsysPackage):
  name = "datacenter-reference"
  version = "0.15.0"

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
      TargetDefinition("dcr:architecture", self.name, self.version, build_architecture, dependencies=("dcr:layout",),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "architecture.json",
                                                     "design-basis.json", "placement.study.json")),
      TargetDefinition("dcr:egress-model", self.name, self.version, build_egress_model,
                       dependencies=("dcr:architecture", "dcr:structure-model", "dcr:pod-layout"), input_patterns=()),
      TargetDefinition("dcr:fire-egress", self.name, self.version, build_egress_check, dependencies=("dcr:egress-model",),
                       input_patterns=spatial_inputs("design-basis.json")),
      TargetDefinition("dcr:fire-systems", self.name, self.version, build_fire_systems, dependencies=("dcr:architecture",),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "fire-systems.json",
                                                     "design-basis.json", "placement.study.json")),
      TargetDefinition("dcr:fire-systems-study", self.name, self.version, build_fire_systems_study,
                       dependencies=("dcr:fire-systems",), input_patterns=spatial_inputs("fire-systems-study.json")),
      TargetDefinition("dcr:fire-systems-coordination", self.name, self.version, build_fire_systems_coordination,
                       dependencies=("dcr:fire-systems", "dcr:telecom-coordination"), input_patterns=()),
      TargetDefinition("dcr:fire-systems-view", self.name, self.version, build_fire_systems_view,
                       dependencies=("dcr:fire-systems",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:fire-effect-view", self.name, self.version, build_fire_systems_view,
                       dependencies=("dcr:fire-systems-study",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:pod-layout", self.name, self.version, build_pod_layout, dependencies=("dcr:layout",),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "pod-layout.json",
                                                     "design-basis.json", "placement.study.json")),
      TargetDefinition("dcr:pod-scene", self.name, self.version, build_pod_scene,
                       dependencies=("dcr:pod-layout",), input_patterns=()),
      TargetDefinition("dcr:pod-view", self.name, self.version, build_pod_view,
                       dependencies=("dcr:pod-layout",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:pod-coordination", self.name, self.version, build_pod_coordination,
                       dependencies=("dcr:pod-layout", "dcr:wet-utility-coordination"), input_patterns=()),
      TargetDefinition("dcr:architecture-view", self.name, self.version, build_building_view,
                       dependencies=("dcr:architecture",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:egress-view", self.name, self.version, build_building_view,
                       dependencies=("dcr:egress-model",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:building-coordination", self.name, self.version, build_building_coordination,
                       dependencies=("dcr:architecture", "dcr:fws-network-coordination"), input_patterns=()),
      TargetDefinition("dcr:telecom", self.name, self.version, build_telecom, dependencies=("dcr:architecture",),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "telecom.json",
                                                     "design-basis.json", "placement.study.json")),
      TargetDefinition("dcr:telecom-study", self.name, self.version, build_telecom_study, dependencies=("dcr:telecom",),
                       input_patterns=spatial_inputs("telecom-study.json")),
      TargetDefinition("dcr:telecom-coordination", self.name, self.version, build_telecom_coordination,
                       dependencies=("dcr:building-coordination", "dcr:telecom"), input_patterns=()),
      TargetDefinition("dcr:telecom-plan", self.name, self.version, build_telecom_view,
                       dependencies=("dcr:telecom-coordination",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:telecom-diagram", self.name, self.version, build_telecom_view,
                       dependencies=("dcr:telecom",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:cw-network", self.name, self.version, build_cw_network, dependencies=("dcr:layout",),
                       input_patterns=spatial_inputs("system.sdl.json", "cw-network.json")),
      TargetDefinition("dcr:cw-coordination", self.name, self.version, build_cw_coordination,
                       dependencies=("dcr:cw-network", "dcr:fire-systems-coordination"), input_patterns=()),
      TargetDefinition("dcr:cw-view", self.name, self.version, build_cw_view,
                       dependencies=("dcr:cw-network",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:cw-diagram", self.name, self.version, build_cw_view,
                       dependencies=("dcr:cw-study",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:cw-study", self.name, self.version, build_cw_study,
                       dependencies=("dcr:cw-network", "dcr:fws-network-study"),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "cw-study.json")),
      TargetDefinition("dcr:cw-check", self.name, self.version, build_cw_check,
                       dependencies=("dcr:cw-study",), input_patterns=()),
      TargetDefinition("dcr:wet-utilities", self.name, self.version, build_wet_utilities,
                       dependencies=("dcr:cw-network",), input_patterns=spatial_inputs("system.sdl.json", "wet-utilities.json")),
      TargetDefinition("dcr:wet-utility-coordination", self.name, self.version, build_wet_coordination,
                       dependencies=("dcr:wet-utilities", "dcr:cw-coordination"), input_patterns=()),
      TargetDefinition("dcr:wet-utility-study", self.name, self.version, build_wet_study,
                       dependencies=("dcr:wet-utilities", "dcr:cw-study"), input_patterns=spatial_inputs("wet-utility-study.json")),
      TargetDefinition("dcr:wet-utility-check", self.name, self.version, build_wet_check,
                       dependencies=("dcr:wet-utility-study",), input_patterns=()),
      TargetDefinition("dcr:wet-utility-view", self.name, self.version, build_wet_view,
                       dependencies=("dcr:wet-utilities",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:wet-utility-diagram", self.name, self.version, build_wet_view,
                       dependencies=("dcr:wet-utility-study",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:fws-routing", self.name, self.version, build_routes, dependencies=("dcr:layout",),
                       input_patterns=spatial_inputs("system.sdl.json", "fws-routing.json")),
      TargetDefinition("dcr:fws-hydraulics", self.name, self.version, build_hydraulics,
                       dependencies=("dcr:fws-routing",), input_patterns=spatial_inputs("fws-hydraulics.json")),
      TargetDefinition("dcr:fws-network", self.name, self.version, build_network, dependencies=("dcr:layout",),
                       input_patterns=spatial_inputs("system.sdl.json", "fws-network.json")),
      TargetDefinition("dcr:fws-network-study", self.name, self.version, build_network_study,
                       dependencies=("dcr:fws-network",),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "design-basis.json",
                                                     "placement.study.json", "fws-network-study.json")),
      TargetDefinition("dcr:fws-thermal-parameters", self.name, self.version, build_thermal_parameters,
                       dependencies=("dcr:fws-network-study",), input_patterns=spatial_inputs("fws-thermal-parameters.json")),
      TargetDefinition("dcr:fws-transient", self.name, self.version, build_transient,
                       dependencies=("dcr:fws-thermal-parameters",), input_patterns=spatial_inputs("fws-transient.json")),
      TargetDefinition("dcr:fws-transient-check", self.name, self.version, build_transient_check,
                       dependencies=("dcr:fws-transient",), input_patterns=()),
      TargetDefinition("dcr:fws-transient-view", self.name, self.version, build_transient_view,
                       dependencies=("dcr:fws-transient", "dcr:architecture"), input_patterns=(), named_output=True),
      TargetDefinition("dcr:fws-network-coordination", self.name, self.version, build_fws_network_coordination,
                       dependencies=("dcr:structure-model", "dcr:fws-network"), input_patterns=()),
      TargetDefinition("dcr:fws-network-view", self.name, self.version, build_network_view,
                       dependencies=("dcr:fws-network",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:fws-buffer-view", self.name, self.version, build_buffer_view,
                       dependencies=("dcr:fws-network",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:tcs-routing", self.name, self.version, build_tcs_routing, dependencies=("dcr:pod-layout",),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "design-basis.json",
                                                     "placement.study.json", "tcs-routing.json")),
      TargetDefinition("dcr:tcs-coordination", self.name, self.version, build_tcs_coordination,
                       dependencies=("dcr:tcs-routing", "dcr:pod-coordination", "dcr:pod-scene"), input_patterns=()),
      TargetDefinition("dcr:tcs-study", self.name, self.version, build_tcs_study,
                       dependencies=("dcr:tcs-routing", "dcr:fws-transient", "dcr:fws-network-study"),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "design-basis.json",
                                                     "placement.study.json", "tcs-study.json")),
      TargetDefinition("dcr:tcs-check", self.name, self.version, build_tcs_check,
                       dependencies=("dcr:tcs-study",), input_patterns=()),
      TargetDefinition("dcr:tcs-layout-view", self.name, self.version, build_tcs_view,
                       dependencies=("dcr:tcs-routing",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:tcs-study-view", self.name, self.version, build_tcs_view,
                       dependencies=("dcr:tcs-study",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:air-routing", self.name, self.version, build_air_routing, dependencies=("dcr:pod-layout",),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "design-basis.json",
                                                     "placement.study.json", "air-routing.json")),
      TargetDefinition("dcr:air-coordination", self.name, self.version, build_air_coordination,
                       dependencies=("dcr:air-routing", "dcr:tcs-routing", "dcr:fire-systems"), input_patterns=()),
      TargetDefinition("dcr:air-scene", self.name, self.version, build_air_scene,
                       dependencies=("dcr:air-coordination", "dcr:tcs-coordination"), input_patterns=()),
      TargetDefinition("dcr:air-study", self.name, self.version, build_air_study,
                       dependencies=("dcr:air-routing", "dcr:tcs-study"),
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "design-basis.json",
                                                     "placement.study.json", "air-study.json")),
      TargetDefinition("dcr:air-check", self.name, self.version, build_air_check,
                       dependencies=("dcr:air-study",), input_patterns=()),
      TargetDefinition("dcr:air-layout-view", self.name, self.version, build_air_view,
                       dependencies=("dcr:air-routing",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:air-study-view", self.name, self.version, build_air_view,
                       dependencies=("dcr:air-study",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:structure-model", self.name, self.version, build_structure, dependencies=("dcr:layout",),
                       input_patterns=spatial_inputs("structure.json")),
      TargetDefinition("dcr:structure-gravity", self.name, self.version, build_gravity,
                       dependencies=("dcr:structure-model",), input_patterns=spatial_inputs("structure-loads.json")),
      TargetDefinition("dcr:structure-view", self.name, self.version, build_structure_view,
                       dependencies=("dcr:structure-model",), input_patterns=(), named_output=True),
      TargetDefinition("dcr:coordination-model", self.name, self.version, build_coordinated_scene,
                       dependencies=("dcr:structure-model", "dcr:fws-routing"), input_patterns=()),
      TargetDefinition("dcr:power-topology", self.name, self.version, build_power_topology,
                       input_patterns=spatial_inputs("*.sdl.json", "*/module.toml", "design-basis.json",
                                                     "placement.study.json", "electrical.json")),
      TargetDefinition("dcr:power-study", self.name, self.version, build_power_study,
                       dependencies=("dcr:power-topology",), input_patterns=spatial_inputs("electrical-study.json")),
      TargetDefinition("dcr:power-view", self.name, self.version, build_power_view,
                       dependencies=("dcr:power-topology",), input_patterns=(), named_output=True),
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
