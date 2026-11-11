"""Data center concept study targets."""

from texsys.evidence import write_json
from texsys.formal import _load_json
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys.targets import Artifact, TargetDefinition, TargetResult

from .air_distribution import evaluate_air_distribution, write_air_distribution_usd
from .air_environment import evaluate_air_environment, write_air_environment_usd
from .architecture_layout import evaluate_architecture_layout
from .building import evaluate_building, write_building_usd
from .controls_study import evaluate_controls
from .cooling_equipment import evaluate_cooling_equipment
from .diaphragm import evaluate_diaphragm, write_diaphragm_usd
from .drainage import evaluate_drainage
from .egress import evaluate_building_egress, write_egress_usd
from .fire import evaluate_fire, write_fire_usd
from .floor import evaluate_floor, write_floor_assembly
from .foundation import evaluate_foundations, write_foundations_usd
from .foundation_ties import evaluate_foundation_ties
from .frame import evaluate_main_frame, write_frame_usd
from .fws import BuildingWater
from .fws_branches import evaluate_fws_branches, write_fws_branches_usd
from .grounding import evaluate_grounding, write_grounding_usd
from .lateral import evaluate_lateral, write_lateral_usd
from .protection import evaluate_protection
from .ride_through import evaluate_ride_through
from .seismic import evaluate_seismic_envelope
from .slab_openings import evaluate_slab_openings, write_slab_openings_usd
from .standby import evaluate_standby
from .tcs_study import evaluate_tcs
from .unit import evaluate_unit
from .water import evaluate_water


class DatacenterPackage(TexsysPackage):
  name = "datacenter"
  version = "0.1.0"

  def engineering_adapters(self):
    from .engineering import FwsChangeAdapter
    return (FwsChangeAdapter(),)

  def targets(self):
    return (
      TargetDefinition("datacenter:unit-study", self.name, self.version, self.build_unit,
                       profiles=("concept", "evaluation"), default_profile="concept"),
      TargetDefinition("datacenter:floor-study", self.name, self.version, self.build_floor,
                       profiles=("concept", "evaluation"), default_profile="concept",
                       dependencies=("datacenter:unit-study",)),
      TargetDefinition("datacenter:building-study", self.name, self.version, self.build_building,
                       profiles=("concept", "evaluation"), default_profile="concept",
                       dependencies=("datacenter:unit-study", "datacenter:floor-study")),
      TargetDefinition("datacenter:air-environment", self.name, self.version, self.build_air_environment,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:air-distribution",)),
      TargetDefinition("datacenter:air-distribution", self.name, self.version, self.build_air_distribution,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:cooling-equipment",)),
      TargetDefinition("datacenter:frame-study", self.name, self.version, self.build_frame,
                       profiles=("concept", "evaluation"), default_profile="concept",
                       dependencies=("datacenter:building-study",)),
      TargetDefinition("datacenter:drainage-study", self.name, self.version, self.build_drainage,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:building-study",)),
      TargetDefinition("datacenter:standby-study", self.name, self.version, self.build_standby,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:building-study",)),
      TargetDefinition("datacenter:fire-study", self.name, self.version, self.build_fire,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:building-study",)),
      TargetDefinition("datacenter:egress-study", self.name, self.version, self.build_egress,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:fire-study",)),
      TargetDefinition("datacenter:architecture-study", self.name, self.version, self.build_architecture,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:egress-study",)),
      TargetDefinition("datacenter:lateral-study", self.name, self.version, self.build_lateral,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:frame-study", "datacenter:egress-study")),
      TargetDefinition("datacenter:diaphragm-study", self.name, self.version, self.build_diaphragm,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:lateral-study", "datacenter:slab-openings")),
      TargetDefinition("datacenter:slab-openings", self.name, self.version, self.build_slab_openings,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:frame-study", "datacenter:fire-study", "datacenter:fws-branches")),
      TargetDefinition("datacenter:fws-branches", self.name, self.version, self.build_fws_branches,
                       profiles=("concept", "evaluation"), default_profile="concept",
                       dependencies=("datacenter:frame-study",)),
      TargetDefinition("datacenter:cooling-equipment", self.name, self.version, self.build_cooling_equipment,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:fws-branches",)),
      TargetDefinition("datacenter:tcs-study", self.name, self.version, self.build_tcs,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:cooling-equipment",)),
      TargetDefinition("datacenter:controls-study", self.name, self.version, self.build_controls,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:tcs-study",)),
      TargetDefinition("datacenter:foundation-study", self.name, self.version, self.build_foundations,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:lateral-study",)),
      TargetDefinition("datacenter:foundation-ties", self.name, self.version, self.build_foundation_ties,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:foundation-study",)),
      TargetDefinition("datacenter:seismic-envelope", self.name, self.version, self.build_seismic_envelope,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:diaphragm-study", "datacenter:foundation-ties")),
      TargetDefinition("datacenter:protection-study", self.name, self.version, self.build_protection,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:standby-study",)),
      TargetDefinition("datacenter:grounding-study", self.name, self.version, self.build_grounding,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:building-study",)),
      TargetDefinition("datacenter:ride-through-study", self.name, self.version, self.build_ride_through,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:standby-study",)),
      TargetDefinition("datacenter:water-study", self.name, self.version, self.build_water,
                       profiles=("concept",), default_profile="concept",
                       dependencies=("datacenter:building-study",)),
    )

  def build_unit(self, context):
    project = inspect_project(context.root)
    try:
      report = evaluate_unit(context.root, context.root / project.source)
    except (KeyError, AttributeError) as error:
      raise ValueError(f"Malformed unit study input: {error}.") from error
    mismatches = [name for name, case in report["scenarios"].items() if case["status"] != case["expected"]]
    report["status"] = "fail" if mismatches else "pass"
    path = context.build_path / "datacenter/unit-study.json"
    write_json(path, report)
    return TargetResult("pass" if context.profile == "evaluation" else report["status"],
                        (Artifact("unit-study", path.relative_to(context.root).as_posix()),),
                        details={"verification": report["status"], "fidelity": "concept"},
                        diagnostics=tuple({"scenario": name, "message": "Scenario differs from its expectation."}
                                          for name in mismatches))

  def build_floor(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    usd = directory / "deck-slab.usda"
    unit = _load_json((directory / "unit-study.json").read_text(encoding="utf-8"))
    try:
      report = evaluate_floor(context.root / project.source, usd, root=context.root, unit_report=unit)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed floor study input: {error}.") from error
    screening = report["structural_screening"]
    mismatches = tuple({"scenario": name, "message": "Structural screening differs from its expectation."}
                       for name, case in (screening["cases"].items() if screening is not None else ())
                       if case["status"] != case["expected"])
    report["status"] = "fail" if report["plan_clashes"] or mismatches else "pass"
    path = directory / "floor-study.json"
    write_json(path, report)
    artifacts = [
      Artifact("floor-study", path.relative_to(context.root).as_posix()),
      Artifact("deck-slab", usd.relative_to(context.root).as_posix()),
    ]
    assembly = directory / "floor-assembly.usda"
    if write_floor_assembly(report, usd, assembly):
      artifacts.append(Artifact("floor-assembly", assembly.relative_to(context.root).as_posix()))
    return TargetResult("pass" if context.profile == "evaluation" else report["status"], tuple(artifacts),
                        details={"verification": report["status"], "fidelity": "concept"},
                        diagnostics=(*mismatches, *({"objects": pair, "message": "Floor coordination overlap."}
                                                    for pair in report["plan_clashes"])))

  def build_building(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    unit = _load_json((directory / "unit-study.json").read_text(encoding="utf-8"))
    floor = _load_json((directory / "floor-study.json").read_text(encoding="utf-8"))
    try:
      report = evaluate_building(context.root / project.source, unit, floor, root=context.root)
    except (KeyError, AttributeError) as error:
      raise ValueError(f"Malformed building study input: {error}.") from error
    mismatches = [name for name, case in report["scenarios"].items() if case["status"] != case["expected"]]
    report["status"] = "fail" if mismatches else "pass"
    path = directory / "building-study.json"
    usd = directory / "building-layout.usda"
    write_json(path, report)
    write_building_usd(report, usd)
    return TargetResult("pass" if context.profile == "evaluation" else report["status"], (
      Artifact("building-study", path.relative_to(context.root).as_posix()),
      Artifact("building-layout", usd.relative_to(context.root).as_posix()),
    ), details={"verification": report["status"], "fidelity": "concept"},
       diagnostics=tuple({"scenario": name, "message": "Building scenario differs from its expectation."}
                         for name in mismatches))

  def build_air_environment(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    unit, floor, building = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                             for name in ("unit", "floor", "building"))
    source = context.root / project.source
    distribution = (_load_json((directory / "air-distribution.json").read_text(encoding="utf-8"))
                    if (source / "air-distribution.study.json").is_file() else None)
    try:
      report = evaluate_air_environment(source, unit, floor, building, distribution)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed air environment input: {error}.") from error
    path, usd = directory / "air-environment.json", directory / "air-environment-layout.usda"
    write_json(path, report)
    write_air_environment_usd(report, usd)
    diagnostics = [{"scenario": name, "failed_units": case["failed_units"], "message": "Air environment expectation differs."}
                   for name, case in report["cases"].items() if case["status"] != case["expected"]]
    diagnostics.extend({**item, "message": "Rack or aisle layout conflicts with air environment."}
                       for item in report["plan_conflicts"])
    return TargetResult(report["status"], (
      Artifact("air-environment", path.relative_to(context.root).as_posix()),
      Artifact("air-environment-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple(diagnostics))

  def build_air_distribution(self, context):
    project = inspect_project(context.root)
    source = context.root / project.source
    if not (source / "air-distribution.study.json").is_file():
      return TargetResult("pass", (), details={"air_basis": "declared-branch-resistances"})
    directory = context.build_path / "datacenter"
    reports = (_load_json((directory / f"{name}.json").read_text(encoding="utf-8")) for name in (
      "unit-study", "floor-study", "building-study", "frame-study", "fws-branches", "cooling-equipment",
    ))
    try:
      report = evaluate_air_distribution(source, *reports)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed air distribution input: {error}.") from error
    path, usd = directory / "air-distribution.json", directory / "air-distribution-layout.usda"
    write_json(path, report)
    write_air_distribution_usd(report, usd)
    return TargetResult(report["status"], (
      Artifact("air-distribution", path.relative_to(context.root).as_posix()),
      Artifact("air-distribution-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple({**item, "message": "Air routing or maintenance volume conflicts."}
                         for item in report["conflicts"]))

  def build_frame(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building, floor = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                       for name in ("building", "floor"))
    try:
      report = evaluate_main_frame(context.root / project.source, building, floor)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed main frame study input: {error}.") from error
    path, usd = directory / "frame-study.json", directory / "frame-layout.usda"
    write_json(path, report)
    write_frame_usd(report, usd, directory / "deck-slab.usda")
    diagnostics = [{"scenario": name, "message": "Gravity case differs from its expectation."}
                   for name, case in report["cases"].items() if case["status"] != case["expected"]]
    diagnostics.extend({**clash, "message": "Column overlaps a rack footprint."}
                       for clash in report["rack_column_clashes"])
    diagnostics.extend({**clash, "message": "Column overlaps a cooling equipment footprint."}
                       for clash in report["cooling_column_clashes"])
    return TargetResult("pass" if context.profile == "evaluation" else report["status"], (
      Artifact("frame-study", path.relative_to(context.root).as_posix()),
      Artifact("frame-layout", usd.relative_to(context.root).as_posix()),
    ), details={"verification": report["status"], "fidelity": "concept"}, diagnostics=tuple(diagnostics))

  def build_drainage(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building = _load_json((directory / "building-study.json").read_text(encoding="utf-8"))
    try:
      report = evaluate_drainage(context.root / project.source, building)
    except (KeyError, AttributeError) as error:
      raise ValueError(f"Malformed drainage study input: {error}.") from error
    mismatches = [name for name, case in report["scenarios"].items() if case["status"] != case["expected"]]
    report["status"] = "fail" if mismatches else "pass"
    path = directory / "drainage-study.json"
    write_json(path, report)
    return TargetResult(report["status"], (Artifact("drainage-study", path.relative_to(context.root).as_posix()),),
                        diagnostics=tuple({"scenario": name, "message": "Drainage scenario differs from its expectation."}
                                          for name in mismatches))

  def build_standby(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building = _load_json((directory / "building-study.json").read_text(encoding="utf-8"))
    unit = _load_json((directory / "unit-study.json").read_text(encoding="utf-8"))
    try:
      report = evaluate_standby(context.root, context.root / project.source, building, unit)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed standby study input: {error}.") from error
    mismatches = [name for name, case in report["scenarios"].items() if case["status"] != case["expected"]]
    report["status"] = "fail" if mismatches else "pass"
    path = directory / "standby-study.json"
    write_json(path, report)
    return TargetResult(report["status"], (Artifact("standby-study", path.relative_to(context.root).as_posix()),),
                        diagnostics=tuple({"scenario": name, "message": "Standby scenario differs from its expectation."}
                                          for name in mismatches))

  def build_fire(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building = _load_json((directory / "building-study.json").read_text(encoding="utf-8"))
    floor = _load_json((directory / "floor-study.json").read_text(encoding="utf-8"))
    try:
      report = evaluate_fire(context.root / project.source, building, floor)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed fire study input: {error}.") from error
    path = directory / "fire-study.json"
    write_json(path, report)
    usd = directory / "fire-layout.usda"
    write_fire_usd(report, usd)
    diagnostics = [{"check": key, "message": "Fire coordination check failed."}
                   for key, passed in report["checks"].items() if not passed and key != "modeled-penetrations"]
    diagnostics.extend({"crossing": key, "checks": report["penetrations"]["crossings"][key]["failed_checks"],
                        "message": "Penetration selection does not satisfy declared criteria."}
                       for key in report["penetrations"]["failed_crossings"])
    diagnostics.extend({"selection": key, "message": "Firestop selection has no corresponding route crossing."}
                       for key in report["penetrations"]["unused_selections"])
    return TargetResult(report["status"], (
      Artifact("fire-study", path.relative_to(context.root).as_posix()),
      Artifact("fire-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple(diagnostics))

  def build_egress(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building, fire = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                      for name in ("building", "fire"))
    try:
      report = evaluate_building_egress(context.root / project.source, building, fire)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed egress study input: {error}.") from error
    path, usd = directory / "egress-study.json", directory / "egress-layout.usda"
    write_json(path, report)
    write_egress_usd(report, usd)
    diagnostics = [{"link": name, "checks": failed, "message": "Egress link cannot be used."}
                   for name, failed in report["invalid_links"].items()]
    diagnostics.extend({"scenario": name, "message": "Egress case differs from its expectation."}
                       for name, case in report["cases"].items() if case["status"] != case["expected"])
    return TargetResult(report["status"], (
      Artifact("egress-study", path.relative_to(context.root).as_posix()),
      Artifact("egress-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple(diagnostics))

  def build_architecture(self, context):
    directory = context.build_path / "datacenter"
    reports = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
               for name in ("building", "fire", "egress"))
    path, usd = directory / "architecture-study.json", directory / "architecture-layout.usda"
    try:
      report = evaluate_architecture_layout(*reports, usd)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed architecture coordination input: {error}.") from error
    write_json(path, report)
    return TargetResult(report["status"], (
      Artifact("architecture-study", path.relative_to(context.root).as_posix()),
      Artifact("architecture-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple({**issue, "message": "Architectural coordination failed."} for issue in report["issues"]))

  def build_lateral(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    frame, egress = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                     for name in ("frame", "egress"))
    try:
      report = evaluate_lateral(context.root / project.source, frame, egress)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed lateral study input: {error}.") from error
    path, usd = directory / "lateral-study.json", directory / "lateral-layout.usda"
    write_json(path, report)
    write_lateral_usd(report, usd)
    diagnostics = [{"scenario": name, "checks": case["checks"], "message": "Lateral case differs from its expectation."}
                   for name, case in report["cases"].items() if case["status"] != case["expected"]]
    diagnostics.extend({**conflict, "message": "Brace bay overlaps a door opening."}
                       for conflict in report["door_conflicts"])
    return TargetResult(report["status"], (
      Artifact("lateral-study", path.relative_to(context.root).as_posix()),
      Artifact("lateral-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple(diagnostics))

  def build_diaphragm(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    frame, floor, building, lateral = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                                      for name in ("frame", "floor", "building", "lateral"))
    source = context.root / project.source
    openings = (_load_json((directory / "slab-openings.json").read_text(encoding="utf-8"))
                if (source / "slab-openings.study.json").is_file() else None)
    try:
      report = evaluate_diaphragm(source, frame, floor, building, lateral, openings)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed diaphragm study input: {error}.") from error
    path, usd = directory / "diaphragm-study.json", directory / "diaphragm-layout.usda"
    write_json(path, report)
    write_diaphragm_usd(report, usd)
    diagnostics = [{"scenario": name, "checks": case["checks"], "message": "Diaphragm case differs from its expectation."}
                   for name, case in report["cases"].items() if case["status"] != case["expected"]]
    diagnostics.extend({**conflict, "message": "Slab connection overlaps a diaphragm opening."}
                       for conflict in report["opening_connection_conflicts"])
    return TargetResult(report["status"], (
      Artifact("diaphragm-study", path.relative_to(context.root).as_posix()),
      Artifact("diaphragm-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple(diagnostics))

  def build_fws_branches(self, context):
    project = inspect_project(context.root)
    source = context.root / project.source
    if not (source / "fws-branches.study.json").is_file():
      return TargetResult("pass", (), details={"branch_basis": "floor-terminal-only"})
    directory = context.build_path / "datacenter"
    building, frame, unit = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                             for name in ("building", "frame", "unit"))
    inputs = _load_json((source / "building.study.json").read_text(encoding="utf-8"))
    try:
      water = BuildingWater(source / "fws.study.json", inputs["shared_heat_rejection"],
                            building["floor_zones"], building["levels_m"], unit, check_branch_geometry=True)
      report = evaluate_fws_branches(water.branches, building, frame)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed FWS branch study input: {error}.") from error
    path, usd = directory / "fws-branches.json", directory / "fws-branches-layout.usda"
    write_json(path, report)
    write_fws_branches_usd(report, usd)
    return TargetResult("pass" if context.profile == "evaluation" else report["status"], (
      Artifact("fws-branches", path.relative_to(context.root).as_posix()),
      Artifact("fws-branches-layout", usd.relative_to(context.root).as_posix()),
    ), details={"geometry_assessment": report["status"], "fidelity": "concept"},
       diagnostics=tuple({**issue, "message": "FWS branch coordination conflict."} for issue in report["conflicts"]))

  def build_cooling_equipment(self, context):
    project = inspect_project(context.root)
    source = context.root / project.source
    directory = context.build_path / "datacenter"
    reports = (_load_json((directory / name).read_text(encoding="utf-8")) for name in (
      "unit-study.json", "floor-study.json", "building-study.json", "fws-branches.json",
    ))
    path, usd = directory / "cooling-equipment.json", directory / "cooling-equipment-layout.usda"
    try:
      report = evaluate_cooling_equipment(context.root, source, *reports, usd)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed cooling equipment study input: {error}.") from error
    write_json(path, report)
    return TargetResult(report["status"], (
      Artifact("cooling-equipment", path.relative_to(context.root).as_posix()),
      Artifact("cooling-equipment-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple({**issue, "message": "Cooling equipment overlaps another object."}
                         for issue in report["conflicts"]))

  def build_tcs(self, context):
    project = inspect_project(context.root)
    source = context.root / project.source
    directory = context.build_path / "datacenter"
    reports = (_load_json((directory / name).read_text(encoding="utf-8")) for name in (
      "unit-study.json", "building-study.json", "fws-branches.json", "cooling-equipment.json",
    ))
    path, usd = directory / "tcs-study.json", directory / "tcs-layout.usda"
    try:
      report = evaluate_tcs(context.root, source, *reports, usd)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed TCS study input: {error}.") from error
    write_json(path, report)
    diagnostics = [{**issue, "message": "TCS physical coordination conflict."} for issue in report["conflicts"]]
    diagnostics.extend({"scenario": name, "message": "TCS scenario differs from its expectation."}
                       for name in report["mismatched_cases"])
    return TargetResult(report["status"], (
      Artifact("tcs-study", path.relative_to(context.root).as_posix()),
      Artifact("tcs-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple(diagnostics))

  def build_controls(self, context):
    project = inspect_project(context.root)
    source = context.root / project.source
    directory = context.build_path / "datacenter"
    reports = (_load_json((directory / name).read_text(encoding="utf-8")) for name in (
      "unit-study.json", "building-study.json", "fws-branches.json",
    ))
    try:
      report = evaluate_controls(context.root, source, *reports)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed controls study input: {error}.") from error
    path = directory / "controls-study.json"
    write_json(path, report)
    return TargetResult(report["status"], (Artifact("controls-study", path.relative_to(context.root).as_posix()),),
                        diagnostics=tuple({"scenario": name, "message": "Control case differs from its expectation."}
                                          for name in report["mismatched_cases"]))

  def build_slab_openings(self, context):
    project = inspect_project(context.root)
    source = context.root / project.source
    if not (source / "slab-openings.study.json").is_file():
      return TargetResult("pass", (), details={"opening_basis": "unit-templates-and-shaft-reservations"})
    directory = context.build_path / "datacenter"
    reports = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
               for name in ("building", "frame", "floor", "fire"))
    branches = (_load_json((directory / "fws-branches.json").read_text(encoding="utf-8"))
                if (source / "fws-branches.study.json").is_file() else None)
    deck = directory / "deck-openings-detail.usda"
    try:
      report = evaluate_slab_openings(source, *reports, deck, branches)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed slab opening study input: {error}.") from error
    path, usd = directory / "slab-openings.json", directory / "slab-openings-layout.usda"
    write_json(path, report)
    write_slab_openings_usd(report, usd)
    diagnostics = tuple({**issue, "message": "Slab opening coordination check failed."} for issue in report["conflicts"])
    return TargetResult(report["status"], (
      Artifact("slab-openings", path.relative_to(context.root).as_posix()),
      Artifact("slab-openings-layout", usd.relative_to(context.root).as_posix()),
      Artifact("deck-openings-detail", deck.relative_to(context.root).as_posix()),
    ), diagnostics=diagnostics)

  def build_foundations(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    frame, lateral = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                      for name in ("frame", "lateral"))
    try:
      report = evaluate_foundations(context.root / project.source, frame, lateral)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed foundation study input: {error}.") from error
    path, usd = directory / "foundation-study.json", directory / "foundation-layout.usda"
    write_json(path, report)
    write_foundations_usd(report, usd)
    diagnostics = tuple({"scenario": name, "checks": case["checks"], "message": "Foundation case differs from its expectation."}
                        for name, case in report["cases"].items() if case["status"] != case["expected"])
    return TargetResult(report["status"], (
      Artifact("foundation-study", path.relative_to(context.root).as_posix()),
      Artifact("foundation-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=diagnostics)

  def build_foundation_ties(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    foundation, frame = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                         for name in ("foundation", "frame"))
    try:
      report = evaluate_foundation_ties(context.root / project.source, foundation, frame)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed tied foundation study input: {error}.") from error
    path, usd = directory / "foundation-ties.json", directory / "foundation-ties.usda"
    write_json(path, report)
    write_foundations_usd(report, usd)
    diagnostics = tuple({"scenario": name, "checks": case["checks"], "message": "Tied case differs from its expectation."}
                        for name, case in report["cases"].items() if case["status"] != case["expected"])
    return TargetResult(report["status"], (
      Artifact("foundation-ties", path.relative_to(context.root).as_posix()),
      Artifact("foundation-ties-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=diagnostics)

  def build_seismic_envelope(self, context):
    directory = context.build_path / "datacenter"
    inputs = ("lateral-study", "diaphragm-study", "foundation-study", "foundation-ties")
    reports = (_load_json((directory / f"{name}.json").read_text(encoding="utf-8")) for name in inputs)
    report = evaluate_seismic_envelope(*reports)
    path = directory / "seismic-envelope.json"
    write_json(path, report)
    return TargetResult("pass", (Artifact("seismic-envelope", path.relative_to(context.root).as_posix()),))

  def build_protection(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building, unit, standby = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                               for name in ("building", "unit", "standby"))
    try:
      report = evaluate_protection(context.root / project.source, building, unit, standby)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed protection study input: {error}.") from error
    path = directory / "protection-study.json"
    write_json(path, report)
    diagnostics = tuple({"scenario": name, "actual": case["status"], "expected": case["expected"],
                         "checks": case.get("checks", {}), "message": "Protection case differs from its expectation."}
                        for name, case in report["cases"].items() if case["status"] != case["expected"])
    return TargetResult(report["status"], (Artifact("protection-study", path.relative_to(context.root).as_posix()),),
                        diagnostics=diagnostics)

  def build_grounding(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building, floor, unit = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                             for name in ("building", "floor", "unit"))
    try:
      report, modules = evaluate_grounding(context.root, context.root / project.source, building, floor, unit)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed grounding study input: {error}.") from error
    path, usd = directory / "grounding-study.json", directory / "grounding-layout.usda"
    write_json(path, report)
    write_grounding_usd(report, modules, usd)
    return TargetResult(report["status"], (
      Artifact("grounding-study", path.relative_to(context.root).as_posix()),
      Artifact("grounding-layout", usd.relative_to(context.root).as_posix()),
    ), diagnostics=tuple({"scenario": name, "message": "Grounding case differs from its expectation."}
                         for name, case in report["cases"].items() if case["status"] != case["expected"]))

  def build_ride_through(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building, unit, standby = (_load_json((directory / f"{name}-study.json").read_text(encoding="utf-8"))
                               for name in ("building", "unit", "standby"))
    try:
      report = evaluate_ride_through(context.root / project.source, building, unit, standby)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed ride-through study input: {error}.") from error
    path = directory / "ride-through-study.json"
    write_json(path, report)
    return TargetResult(report["status"], (Artifact("ride-through-study", path.relative_to(context.root).as_posix()),),
                        diagnostics=tuple({"scenario": name, "message": "Thermal case differs from its expectation."}
                                          for name, case in report["cases"].items()
                                          if case["status"] != case["expected"]))

  def build_water(self, context):
    project = inspect_project(context.root)
    directory = context.build_path / "datacenter"
    building = _load_json((directory / "building-study.json").read_text(encoding="utf-8"))
    unit = _load_json((directory / "unit-study.json").read_text(encoding="utf-8"))
    try:
      report = evaluate_water(context.root / project.source, building, unit)
    except (KeyError, AttributeError, TypeError) as error:
      raise ValueError(f"Malformed water study input: {error}.") from error
    path = directory / "water-study.json"
    write_json(path, report)
    return TargetResult(report["status"], (Artifact("water-study", path.relative_to(context.root).as_posix()),),
                        diagnostics=tuple({"scenario": name, "message": "Water case differs from its expectation."}
                                          for name, case in report["cases"].items()
                                          if case["status"] != case["expected"]))


package = DatacenterPackage()
