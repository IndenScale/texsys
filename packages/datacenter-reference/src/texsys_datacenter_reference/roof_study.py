"""Derive secondary reactions and screen roof drainage against the declared storm."""

from dataclasses import asdict
from math import isclose

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_structure.beam import simple_beam

from .roof import LAYERS, MODEL, _issue, _schedule
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json
from .structure import _assumptions


def build_roof_actions(context):
  source = source_path(context, "structure-loads.json")
  if not source.is_file():
    return missing_input(context, source, "roof-actions")
  loads = _assumptions(source)
  roof = staged_json(context, MODEL)
  config = roof["assumptions"]
  size = config["purlins"]
  b, h, t = (number(size[key], f"purlin {key}") for key in ("width_m", "depth_m", "wall_m"))
  if 2*t >= min(b, h):
    raise ValueError("The purlin wall does not bound a hollow section.")
  area = b*h-(b-2*t)*(h-2*t)
  inertia = (b*h**3-(b-2*t)*(h-2*t)**3)/12
  modulus = inertia/(h/2)
  web_area = 2*t*(h-2*t)
  steel = number(loads["steel_kn_m3"], "steel unit weight")
  elasticity = 1e6*number(loads["elastic_modulus_gpa"], "elastic modulus")
  live = number(loads["roof_live_kpa"], "roof live pressure", zero=True)
  miscellaneous = number(config["miscellaneous_dead_kpa"], "attachment weight allowance", zero=True)
  combinations = {"service": (1, 1), "strength": tuple(number(loads["strength_factors"][item], item)
                                                     for item in ("permanent", "variable"))}
  members, girder_actions, issues = {}, {}, []
  total_dead, total_live = 0., 0.
  for key, row in roof["purlins"].items():
    width_area, length = row["gross_area_m2"], row["span_m"]
    material = {layer: row["volume_m3"][layer]*config["layers"][layer]["effective_unit_weight_kn_m3"]
                for layer in LAYERS}
    own = area*length*steel
    dead_kn = own+sum(material.values())+miscellaneous*width_area
    live_kn = width_area*live
    solved = {case: simple_beam(length, elasticity*inertia, (a*dead_kn+b*live_kn)/length)
              for case, (a, b) in combinations.items()}
    strength, service = solved["strength"], solved["service"]
    ratios = {
      "bending": strength.moment_kn_m/(1000*number(loads["bending_limit_mpa"], "bending limit")*modulus),
      "shear": strength.shear_kn/(1000*number(loads["web_shear_limit_mpa"], "web shear limit")*web_area),
      "deflection": service.deflection_m/(length/number(loads["deflection_ratio"], "deflection ratio")),
      "seat_reaction": max(service.reaction_start_kn, service.reaction_end_kn) /
      number(size["seat_service_limit_kn"], "purlin candidate seat rating"),
    }
    for check, ratio in ratios.items():
      if ratio > 1:
        _issue(issues, "PURLIN_"+check.upper(), [key], f"{key} {check} demand/capacity = {ratio:.3f}.")
    members[key] = {"dead_kn": dead_kn, "live_kn": live_kn, "section_area_m2": area, "own_weight_kn": own,
                    "roof_material_weight_kn": material, "utilization": ratios,
                    "response": {case: asdict(value) for case, value in solved.items()}}
    for girder, support in zip(row["supports"], ("reaction_start_kn", "reaction_end_kn"), strict=True):
      girder_actions.setdefault(girder, []).append({
        "purlin": key, "x_m": row["center_x_m"],
        "force_kn": {case: getattr(value, support) for case, value in solved.items()},
      })
    total_dead += dead_kn
    total_live += live_kn
  balances = {}
  for case, (a, b) in combinations.items():
    applied = a*total_dead+b*total_live
    delivered = sum(action["force_kn"][case] for group in girder_actions.values() for action in group)
    if not isclose(applied, delivered, abs_tol=1e-8):
      raise ValueError(f"Purlin to girder equilibrium failed for {case}.")
    balances[case] = {"applied_kn": applied, "delivered_kn": delivered, "residual_kn": applied-delivered}
  gross = sum(row["gross_area_m2"] for row in roof["panels"].values())
  allocated = sum(row["gross_area_m2"] for row in roof["purlins"].values())
  if not isclose(allocated, gross, abs_tol=1e-8):
    raise ValueError("Roof deck tributary area must be allocated exactly once.")
  for layer in LAYERS:
    supplied = sum(row["volume_m3"][layer] for row in roof["panels"].values())
    delivered = sum(row["volume_m3"][layer] for row in roof["purlins"].values())
    if not isclose(supplied, delivered, abs_tol=1e-8):
      raise ValueError(f"{layer} net material quantity is inconsistent with tributary strips.")
  dead_budget = number(loads["roof_dead_kpa"], "roof dead weight budget")
  if total_dead/gross > dead_budget:
    _issue(issues, "DEAD_BUDGET", ["roof"], f"Extracted roof weight {total_dead/gross:.3f} kPa exceeds {dead_budget:.3f} kPa.")
  report = model_report(context, "roof-actions", {
    "assembly_digest": file_digest(context.build_path / MODEL), "load_source_digest": file_digest(source),
    "members": members, "girder_actions": girder_actions, "equilibrium": balances,
    "gross_area_m2": gross, "dead_weight_kn": total_dead, "dead_kpa": total_dead/gross,
    "dead_budget_kpa": dead_budget, "live_weight_kn": total_live,
    "checks": "fail" if issues else "pass", "issues": issues,
    "coverage": "Simply supported purlins with tributary strip weights averaged over the span, including edge "
                "overhangs. Girder inputs preserve every point reaction. Seats use the declared service load "
                "limit. Anchors, lateral stability, local sheet and curb strengthening, and uplift remain pending.",
  })
  table = _schedule(context, "aidc-roof-purlins.csv",
                    ("purlin", "roof_panel", "span_m", "tributary_m2", "steel_kn", "dead_kn", "live_kn",
                     "service_deflection_mm", "service_reaction_kn", "support_1", "support_2"),
                    ((key, row["panel"], row["span_m"], row["gross_area_m2"], members[key]["own_weight_kn"],
                      members[key]["dead_kn"], members[key]["live_kn"],
                      1000*members[key]["response"]["service"]["deflection_m"],
                      members[key]["response"]["service"]["reaction_start_kn"], *row["supports"])
                     for key, row in roof["purlins"].items()))
  return TargetResult(report.status, (*report.artifacts, table), report.diagnostics)


def build_roof_gravity_check(context):
  path = f"{MODELS}/roof-actions.json"
  actions = staged_json(context, path)
  return model_report(context, "roof-gravity-check", {
    "actions_digest": file_digest(context.build_path / path),
    "issues": actions["issues"],
  }, actions["issues"])


def build_roof_drainage(context):
  assembly = staged_json(context, MODEL)
  config = assembly["assumptions"]["drainage"]
  intensity = number(config["rainfall_mm_h"], "design rainfall")
  coefficient = number(config["runoff_coefficient"], "runoff coefficient")
  width = number(config["gutter_width_m"], "gutter width")
  depth = number(config["flow_depth_m"], "gutter flow depth")
  grade = number(config["longitudinal_slope"], "gutter slope")
  friction = number(config["manning_n"], "gutter Manning friction")
  accepted = number(config["leader_capacity_l_s"], "declared leader capacity")
  if coefficient > 1 or depth >= config["gutter_depth_m"]:
    raise ValueError("Roof drainage needs runoff coefficient ≤1 and positive gutter freeboard.")
  wet_area = width*depth
  radius = wet_area/(width+2*depth)
  gutter_l_s = 1000*wet_area*radius**(2/3)*grade**.5/friction
  result, issues = {}, []
  for name, row in assembly["drainage"].items():
    flow = coefficient*intensity*row["catchment_m2"]/3600
    result[name] = {**row, "design_flow_l_s": flow, "gutter_capacity_l_s": gutter_l_s,
                    "leader_capacity_l_s": accepted, "freeboard_m": config["gutter_depth_m"]-depth}
    for role, limit in (("gutter", gutter_l_s), ("leader", accepted)):
      if flow > limit:
        _issue(issues, "DRAINAGE_CAPACITY", [name, role], f"{name}: {flow:.2f} L/s exceeds {role} capacity {limit:.2f} L/s.")
  gross = sum(row["gross_area_m2"] for row in assembly["panels"].values())
  total = sum(row["catchment_m2"] for row in result.values())
  if not isclose(total, gross, abs_tol=1e-8):
    raise ValueError("Roof catchment areas do not close to the horizontal roof projection.")
  report = model_report(context, "roof-drainage", {
    "geometry_digest": file_digest(context.build_path / MODEL), "rainfall_mm_h": intensity, "runoff_coefficient": coefficient,
    "outlets": result, "total_runoff_l_s": sum(row["design_flow_l_s"] for row in result.values()),
    "checks": "fail" if issues else "pass", "issues": issues,
    "coverage": "Gravity eave gutters at the declared depth and slope, with separate declared downpipe capacities "
                "and four above-yard handoffs. Site storm connection and emergency rainfall, blockage and overflow "
                "performance remain pending.",
  })
  table = _schedule(context, "aidc-roof-rainwater.csv",
                    ("outlet", "catchment_m2", "design_l_s", "gutter_limit_l_s", "leader_limit_l_s",
                     "handoff_x_m", "handoff_y_m", "handoff_z_m", "freeboard_mm"),
                    ((key, row["catchment_m2"], row["design_flow_l_s"], row["gutter_capacity_l_s"],
                      row["leader_capacity_l_s"], *row["handoff_m"], 1000*row["freeboard_m"])
                     for key, row in result.items()))
  return TargetResult(report.status, (*report.artifacts, table), report.diagnostics)


def build_roof_drainage_check(context):
  path = f"{MODELS}/roof-drainage.json"
  study = staged_json(context, path)
  return model_report(context, "roof-drainage-check", {
    "drainage_digest": file_digest(context.build_path / path),
    "issues": study["issues"],
  }, study["issues"])
