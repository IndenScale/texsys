"""Explicit Python design checks for the equipment-layout example."""

import math

from texsys.evidence import canonical_digest, write_json
from texsys.packages import TexsysPackage
from texsys.solver import Assertion, Solution, Stimulus, TestCase, run_test
from texsys.targets import Artifact, TargetDefinition, TargetResult
from texsys_hvac_equipment.domain import load_system


class DesignCheckSolver:
  version = "hvac-design-checks/1"

  def solve(self, stimulus):
    system = stimulus.model
    conditions = {**system.conditions, **stimulus.parameters}
    capacity = sum(item.capacity_kw for item in system.equipment)
    heat = sum(item.heat_rejection_kw for item in system.equipment)
    mass_flow = conditions["ventilation_m3_s"] * conditions["air_density_kg_m3"]
    rise = heat / (mass_flow * conditions["air_cp_kj_kgk"])
    temperature = conditions["ambient_temperature_c"]
    saturation = 6.112 * math.exp(17.67 * temperature / (temperature + 243.5))
    vapor = conditions["relative_humidity"] * saturation
    logarithm = math.log(vapor / 6.112)
    dew_point = 243.5 * logarithm / (17.67 - logarithm)
    values = {"capacity_margin_kw": capacity - conditions["cooling_load_kw"],
              "air_mass_flow_kg_s": mass_flow, "temperature_rise_c": rise,
              "room_temperature_c": temperature + rise,
              "saturation_vapor_pressure_hpa": saturation, "vapor_pressure_hpa": vapor,
              "dew_point_c": dew_point, "dew_point_margin_c": conditions["surface_temperature_c"] - dew_point}
    digest = canonical_digest({"equipment": [vars(item) for item in system.equipment], "conditions": conditions})
    return Solution("solved", values, self.version, digest)


def design_case(root):
  system = load_system(root)
  if system is None:
    return None
  requirements = system.requirements
  return TestCase("steady-state-capacity-and-environment", Stimulus(system), DesignCheckSolver(), (
    Assertion("capacity_margin_kw", ">=", requirements["min_capacity_margin_kw"], "Capacity must cover the design load."),
    Assertion("room_temperature_c", "<=", requirements["max_room_temperature_c"], "Ventilation temperature estimate must meet the limit."),
    Assertion("dew_point_margin_c", ">=", requirements["min_dew_point_margin_c"], "Surface temperature must retain its dew-point margin."),
  ))


class HvacChecksPackage(TexsysPackage):
  name = "hvac-checks"
  version = "0.1.0"

  def targets(self):
    return (TargetDefinition("hvac:design-checks", self.name, self.version, self.build_checks,
                             profiles=("engineering",), default_profile="engineering"),)

  def build_checks(self, context):
    case = design_case(context.root)
    if case is None:
      return TargetResult("indeterminate", open_items=({"id": "missing-hvac-input", "impact": "Design checks require equipment-layout input."},))
    result = run_test(case)
    path = context.build_path / "hvac/design-checks.json"
    write_json(path, result.as_dict())
    return TargetResult(result.status, artifacts=(Artifact("design-checks", path.relative_to(context.root).as_posix()),))

  def test(self, root):
    case = design_case(root)
    return () if case is None else (run_test(case),)


package = HvacChecksPackage()
