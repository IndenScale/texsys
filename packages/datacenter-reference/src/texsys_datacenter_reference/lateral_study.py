"""Screen hall lateral actions through roof collectors, rods and independent pads."""

from itertools import product
from math import hypot, isclose

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_structure.foundation import footing_contact
from texsys_structure.lateral import LateralMechanism

from .lateral_bracing import MODEL
from .roof import MODEL as ROOF, _schedule, roof_surfaces
from .roof_transfer import MODEL as TRANSFER
from .roof_truss import RoofTruss, assess_roof_members
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json
from .structure import MODEL as FRAME, _assumptions, _sections

STUDY = f"{MODELS}/lateral-study.json"
GRAVITY = f"{MODELS}/structure-gravity.json"
ROOF_ACTIONS = f"{MODELS}/roof-actions.json"


def _limits(config):
  keys = ("accidental_eccentricity_ratio", "seismic_orthogonal_fraction", "roof_live_mass_fraction",
          "stable_dead_weight_factor", "concurrent_roof_live_fraction", "rod_net_area_fraction",
          "soil_friction_coefficient")
  if any(number(config[key], key) > 1 for key in keys):
    raise ValueError("Project load fractions, net rod area fraction and friction must be in (0, 1].")
  if number(config["upper_dead_weight_factor"], "upper permanent weight factor") < 1:
    raise ValueError("The upper permanent load bound must include at least the unfactored dead weight.")
  return {
    "wind_strength": number(config["wind_strength_factor"], "candidate wind strength factor"),
    "seismic": number(config["seismic_static_coefficient"], "candidate static seismic coefficient"),
    "service_drift": number(config["service_drift_limit_ratio"], "candidate wind drift ratio"),
    "seismic_drift": number(config["seismic_drift_limit_ratio"], "candidate seismic drift ratio"),
    "rod_stress_mpa": number(config["rod_design_stress_mpa"], "candidate rod design stress"),
    "gusset_kn": number(config["gusset_axial_rating_kn"], "candidate gusset axial resistance"),
    "column_stress_mpa": number(config["column_base_screen_stress_mpa"], "candidate column base stress"),
    "base_shear_kn": number(config["base_shear_rating_kn"], "candidate column base shear rating"),
    "base_torsion_kn_m": number(config["base_torsion_rating_kn_m"], "candidate torsion rating at the column base"),
  }


def _weights(frame, bracing, gravity, roof_actions, config):
  members = frame["members"]
  steel_unit_weight = gravity["loads"]["steel_kn_m3"]
  steel = {name: row["physical_volume_m3"]*steel_unit_weight
           for name, row in members.items() if row["kind"] in {"column", "girder", "tie"}}
  live = dict.fromkeys((key for key, row in members.items() if row["kind"] == "column"), 0.)
  for name, rows in gravity["roof_reactions"].items():
    girder = members[name]
    start, end = girder["supports"]
    for point in rows:
      force = roof_actions["members"][point["purlin"]]["live_kn"]/2
      share_end = (point["x_m"]-girder["start_m"][0])/girder["length_m"]
      live[start] += force*(1-share_end)
      live[end] += force*share_end
  dead = {name: gravity["checks"][name]["base_kn"]["service"]-force for name, force in live.items()}
  roof_tie_weight = sum(gravity["roof_transfer_weight_by_node_kn"].values())
  mast_weight = sum(gravity["lightning_mast_weight_by_node_kn"].values())
  expected_dead = roof_actions["dead_weight_kn"]+sum(steel.values())+roof_tie_weight+mast_weight
  if (not isclose(sum(live.values()), roof_actions["live_weight_kn"], abs_tol=1e-7)
      or not isclose(sum(dead.values()),expected_dead,abs_tol=1e-7)):
    raise ValueError("The permanent and live roof reactions must balance at the primary columns.")
  rods = 0.
  vertical_rod_by_node = dict.fromkeys(dead, 0.)
  for row in bracing["bays"].values():
    weight = sum(rod["length_m"] for rod in row["rods"].values())*bracing["area_m2"]*steel_unit_weight
    rods += weight
    for name in row["columns"]:
      dead[name] += weight/2
      vertical_rod_by_node[name] += weight/2
  elevated_steel = sum(weight*(.5 if members[name]["kind"] == "column" else 1) for name, weight in steel.items())
  extra = number(config["additional_roof_mass_kn"], "suspended and facade seismic budget", zero=True)
  nodal_mass = {name: weight-.5*(steel[name]+vertical_rod_by_node[name])+
                config["roof_live_mass_fraction"]*live[name]+extra/len(dead) for name, weight in dead.items()}
  components = {
    "roof_dead_kn": roof_actions["dead_weight_kn"], "steel_effective_kn": elevated_steel,
    "rods_effective_kn": rods/2, "roof_live_part_kn": config["roof_live_mass_fraction"]*sum(live.values()),
    "additional_roof_mass_kn": extra, "roof_transfer_weight_kn": roof_tie_weight, "lightning_mast_weight_kn": mast_weight,
  }
  if not isclose(sum(nodal_mass.values()), sum(components.values()), abs_tol=1e-7):
    raise ValueError("Lumped roof nodal weights do not sum to the effective seismic weight.")
  return dead, live, components, nodal_mass


def _actions(bracing, roof, frame, config, limits, seismic_weight):
  lengths = [hi-lo for lo, hi in bracing["plan_m"]]
  eave, ridge = (roof_surfaces(roof, y)[-1] for y in
                 (bracing["plan_m"][1][0], sum(bracing["plan_m"][1])/2))
  base = min(row["start_m"][2] for row in frame["members"].values() if row["kind"] == "column")
  areas = [lengths[1]*((eave+ridge)/2-base), lengths[0]*(ridge-base)]
  wind = {axis: area*number(config["wind_net_pressure_kpa"][axis], f"{axis} net wind pressure")
          for axis, area in zip("xy", areas, strict=True)}
  seismic = limits["seismic"]*seismic_weight
  cases = {}
  for regime, axis, sign, offset in product(("wind-service", "wind-strength", "seismic"), "xy", (1, -1), (1, -1)):
    perpendicular = "yx"["xy".index(axis)]
    factor = limits["wind_strength"] if regime == "wind-strength" else 1
    first = sign*(seismic if regime == "seismic" else factor*wind[axis])
    fractions = (1, -1) if regime == "seismic" else (0,)
    for cross in fractions:
      vector = {axis: first, perpendicular: cross*seismic*config["seismic_orthogonal_fraction"]}
      x, y = vector["x"], vector["y"]
      # Move the complete force vector away from the plan centre in both directions.
      ex = offset*config["accidental_eccentricity_ratio"]*lengths[0]
      ey = -offset*config["accidental_eccentricity_ratio"]*lengths[1]
      case = f"{regime}/{axis}{sign:+d}/ecc{offset:+d}"
      if cross:
        case += f"/{perpendicular}{cross:+d}"
      cases[case] = {"regime": regime, "load_kn": [x, y, ex*y-ey*x], "eccentricity_m": [ex, ey]}
  return cases, dict(zip("xy", areas, strict=True))


def _transfer(bracing, solved, height):
  actions = {key: {"shear_kn": [0., 0.], "chord_kn": 0., "chord_first_moment_kn_m": [0., 0.],
                   "torsion_kn_m": 0.} for row in bracing["bays"].values() for key in row["columns"]}
  rods = {}
  for name, branches in solved["bays"].items():
    bay = bracing["bays"][name]
    first, last = (actions[key] for key in bay["columns"])
    translations = {}
    for rod, result in branches.items():
      axis = result["axis"]
      shear = result["shear_kn"]
      eccentricity = bay["rods"][rod]["eccentricity_m"]
      chord = shear*height/bay["span_m"]
      first["chord_kn"] -= chord
      last["chord_kn"] += chord
      first["chord_first_moment_kn_m"][1-axis] -= chord*eccentricity
      last["chord_first_moment_kn_m"][1-axis] += chord*eccentricity
      receives = 0 if rod.endswith("-P") else 1
      support = (first, last)[receives]
      support["shear_kn"][axis] += shear
      support["torsion_kn_m"] += (2*axis-1)*eccentricity*shear
      translations[rod] = {**result, "receiving_column": bay["columns"][receives],
                           "compression_at_end_kn": chord}
    lead = max(translations, key=lambda rod: translations[rod]["tension_kn"])
    rods[name] = {**translations[lead], "active_rod": lead, "branches": translations,
                  "shear_kn": sum(row["shear_kn"] for row in translations.values())}
  return actions, rods


def _contact(pad, size, normal, moments):
  contact = footing_contact(size, normal, moments)
  resisted = contact.resultants()
  if contact.state in {"partial", "full"} and not all(
      isclose(realized, expected, abs_tol=1e-6) for realized, expected
      in zip(resisted, (normal, *moments), strict=True)):
    raise ValueError(f"{pad}: soil pressure does not reproduce the applied normal force and moments.")
  return contact, resisted


def _foundations(frame, gravity, model, config, limits, vertical, actions, wind_service):
  members = frame["members"]
  dead, live = vertical
  section = _sections(frame["settings"])["column"]
  web_height = section.depth-2*section.flange
  weak = (2*section.flange*section.width**3+web_height*section.web**3)/12
  strong = section.major_inertia_m4
  allowable_soil = gravity["loads"]["bearing_limit_kpa"]
  elasticity = 1e6*gravity["loads"]["elastic_modulus_gpa"]
  results = {}
  for column, action in actions.items():
    pad = members[column]["pad"]
    footing = members[pad]
    height_to_soil = model["lower_joint_m"]-footing["start_m"][2]
    height_to_base = model["lower_joint_m"]-members[column]["start_m"][2]
    h = action["shear_kn"]
    pad_weight = gravity["checks"][pad]["own_weight_kn"]
    low_factor = 1 if wind_service else config["stable_dead_weight_factor"]
    high_factor = 1 if wind_service else config["upper_dead_weight_factor"]
    live_factor = 1 if wind_service else config["concurrent_roof_live_fraction"]
    n_low = low_factor*dead[column]+action["chord_kn"]
    n_high = high_factor*dead[column]+live_factor*live[column]+action["chord_kn"]
    normal_low, normal_high = n_low+low_factor*pad_weight, n_high+high_factor*pad_weight
    mx, my = action["chord_first_moment_kn_m"]
    first_moments = [h[0]*height_to_soil+mx, h[1]*height_to_soil+my]
    dimensions = footing["parts"][0]["size"][:2]
    low_contact, low_soil = _contact(pad, dimensions, normal_low, first_moments)
    high_contact, high_soil = _contact(pad, dimensions, normal_high, first_moments)
    peak, resisted, normal, n_column = max(((low_contact, low_soil, normal_low, n_low),
                                            (high_contact, high_soil, normal_high, n_high)),
                                           key=lambda row: row[0].peak_kpa)
    base_moments = [h[0]*height_to_base+mx, h[1]*height_to_base+my]
    stress = (max(abs(n_low), abs(n_high))/section.area_m2 + abs(base_moments[0])*section.width/(2*weak)
              + abs(base_moments[1])*section.depth/(2*strong))/1000
    # Add an upper-bound estimate of stub translation and rotation to the brace drift.
    above = model["upper_joint_m"]-model["lower_joint_m"]
    flex = height_to_base**3/3 + height_to_base**2*above/2
    # The eccentric chord also applies a small end couple in the perpendicular plane.
    couple = height_to_base**2/2 + height_to_base*above
    movement = hypot((abs(h[0])*flex+abs(mx)*couple)/(elasticity*weak),
                     (abs(h[1])*flex+abs(my)*couple)/(elasticity*strong))
    friction = config["soil_friction_coefficient"]*normal_low
    area = dimensions[0]*dimensions[1]
    results[pad] = {
      "column": column, "column_normal_kn": n_column, "pad_normal_kn": normal, "shear_kn": h,
      "first_moments_kn_m": first_moments, "base_moments_kn_m": base_moments,
      "base_torsion_kn_m": action["torsion_kn_m"], "peak_soil_kpa": peak.peak_kpa,
      "contact_state": peak.state, "contact_fraction": peak.area_m2/area,
      "stability_normal_kn": normal_low, "stability_contact_state": low_contact.state,
      "stability_contact_fraction": low_contact.area_m2/area, "stability_soil_resultants": low_soil,
      "soil_resultants": resisted, "column_stress_mpa": stress, "stub_movement_m": movement,
      "utilization": {
        "bearing": peak.peak_kpa/allowable_soil,
        "sliding": hypot(*h)/friction if friction > 0 else None,
        "column_stress": stress/limits["column_stress_mpa"],
        "base_shear": hypot(*h)/limits["base_shear_kn"],
        "base_torsion": abs(action["torsion_kn_m"])/limits["base_torsion_kn_m"],
      },
    }
  return results


def _issue(case, criterion, objects, message):
  return {"rule": "AIDC_LATERAL_"+criterion.upper(), "objects": [case, *objects],
          "status": "fail", "message": f"{case}: {message}"}


def _evaluate(frame, model, transfer, gravity, config, limits, cases, vertical, nodal_mass):
  stored, issues = {}, []
  elasticity = gravity["loads"]["elastic_modulus_gpa"]*1e6
  network = RoofTruss(transfer, model, elasticity)
  low, high = (model[field] for field in ("lower_joint_m", "upper_joint_m"))
  lever = transfer["action_elevation_m"]-low
  if lever <= high-low:
    raise ValueError("The candidate roof action plane must be above the vertical brace upper joints.")
  base = min(row["start_m"][2] for row in frame["members"].values() if row["kind"] == "column")
  rod_capacity = model["area_m2"]*config["rod_net_area_fraction"]*1000*limits["rod_stress_mpa"]
  for key, action in cases.items():
    try:
      solved = network.solve(action, nodal_mass)
    except LateralMechanism:
      issues.append(_issue(key, "mechanism", list(model["bays"]), "The active layout does not restrain three roof degrees of freedom."))
      break
    nodes, response = _transfer(model, solved, lever)
    actual_chord_moments = [sum((frame["members"][column]["start_m"][i])*row["chord_kn"]
                                for column, row in nodes.items()) for i in (0, 1)]
    force_residual = max(abs(value) for value in (*solved["equilibrium_residual"]["force_kn"],
                                                solved["equilibrium_residual"]["torsion_kn_m"],
                                                sum(row["chord_kn"] for row in nodes.values())))
    if force_residual > 1e-7 or any(not isclose(realized, force*lever, abs_tol=1e-7)
                                    for realized, force in zip(actual_chord_moments, solved["shear_kn"], strict=True)):
      raise ValueError(f"{key}: roof force and moment transfer do not close at the column joints.")
    service = action["regime"] == "wind-service"
    pads = _foundations(frame, gravity, model, config, limits, vertical, nodes, service)
    stub_movement = max(row["stub_movement_m"] for row in pads.values())
    drift = (solved["max_corner_drift_ratio"]*(high-low)+stub_movement)/(high-base)
    drift_limit = {"wind-service": limits["service_drift"], "seismic": limits["seismic_drift"]}.get(action["regime"])
    if drift_limit and drift > drift_limit:
      issues.append(_issue(key, "drift", list(model["bays"]), f"Roof drift ratio {drift:.6f} exceeds {drift_limit:.6f}."))
    for name, row in response.items():
      row["rod_utilization"] = row["tension_kn"]/rod_capacity
      row["gusset_utilization"] = row["tension_kn"]/limits["gusset_kn"]
      for criterion in ("rod", "gusset"):
        ratio = row[f"{criterion}_utilization"]
        if ratio > 1:
          issues.append(_issue(key, criterion, [row["active_rod"]], f"{name} {criterion} demand/capacity = {ratio:.3f}."))
    for pad, row in pads.items():
      if row["stability_contact_state"] in {"detached", "overturned"}:
        issues.append(_issue(key, "contact", [pad], f"{pad} loses contact equilibrium."))
      for criterion, ratio in row["utilization"].items():
        if ratio is not None and ratio > 1:
          issues.append(_issue(key, criterion, [row["column"], pad], f"{pad} {criterion} demand/capacity = {ratio:.3f}."))
    roof_members, roof_joints, roof_issues = assess_roof_members(transfer, model, gravity, solved, key)
    issues.extend(roof_issues)
    stored[key] = {**action, "brace_storey": solved, "roof_members": roof_members, "roof_joints": roof_joints, "drift_ratio": drift, "drift_limit_ratio": drift_limit,
                   "stub_allowance_m": stub_movement, "overturning_couple_kn_m": actual_chord_moments,
                   "rod_capacity_kn": rod_capacity, "rods": response, "foundations": pads}
  return stored, issues, network.pretension_residual_kn


def build_lateral_study(context):
  source = source_path(context, "lateral-actions.json")
  if not source.is_file():
    return missing_input(context, source, "lateral-study")
  paths = (MODEL, FRAME, GRAVITY, ROOF, ROOF_ACTIONS, TRANSFER)
  model, frame, gravity, roof, roof_actions, transfer = (staged_json(context, path) for path in paths)
  config = _assumptions(source)
  limits = _limits(config)
  dead, live, mass, nodal_mass = _weights(frame, model, gravity, roof_actions, config)
  actions, projected = _actions(model, roof, frame, config, limits, sum(mass.values()))
  cases, issues, pretension_residual = _evaluate(
    frame, model, transfer, gravity, config, limits, actions, (dead, live), nodal_mass)
  report = model_report(context, "lateral-study", {
    "input_digests": {path: file_digest(context.build_path / path) for path in paths},
    "assumptions": config, "projected_area_m2": projected, "effective_weight_kn": sum(mass.values()),
    "lumped_weight_components_kn": mass, "column_dead_kn": dead, "column_roof_live_kn": live,
    "cases": cases, "issues": issues, "nodal_effective_weight_kn": nodal_mass,
    "action_elevation_m": transfer["action_elevation_m"], "pretension_equilibrium_residual_kn": pretension_residual,
    "intended_cases": len(actions), "checks": "fail" if issues else "pass",
    "coverage": "Axial members model the installed prestressed roof truss. Each vertical rod engages only if its "
                "upper node stretches the rod. Gross section elastic stiffness and transparent candidate capacities "
                "are used. The additional overturning lever at the roof plane is assigned to the associated pairs "
                "of columns. Connection stiffness, local stability, prestress losses, soil springs and dynamic "
                "site-specific design remain pending.",
  })
  table = _schedule(context, "aidc-lateral-brace-forces.csv",
                    ("case", "bay", "active_rod", "shear_kn", "tension_kn", "rod_utilization",
                     "gusset_utilization", "receiving_column"),
                    ((key, bay, row["active_rod"], row["shear_kn"], row["tension_kn"], row["rod_utilization"],
                      row["gusset_utilization"], row["receiving_column"])
                     for key, case in cases.items() for bay, row in case["rods"].items()))
  footings = _schedule(context, "aidc-lateral-pad-reactions.csv",
                       ("case", "pad", "column", "pad_normal_kn", "hx_kn", "hy_kn", "nx_kn_m", "ny_kn_m",
                        "soil_peak_kpa", "contact_fraction", "low_load_normal_kn", "low_load_contact_fraction",
                        "friction_utilization", "column_stress_mpa"),
                       ((key, pad, row["column"], row["pad_normal_kn"], *row["shear_kn"], *row["first_moments_kn_m"],
                         row["peak_soil_kpa"], row["contact_fraction"], row["stability_normal_kn"],
                         row["stability_contact_fraction"], row["utilization"]["sliding"],
                         row["column_stress_mpa"])
                        for key, case in cases.items() for pad, row in case["foundations"].items()))
  return TargetResult(report.status, (*report.artifacts, table, footings), report.diagnostics)


def build_lateral_check(context):
  result = staged_json(context, STUDY)
  return model_report(context, "lateral-check", {
    "study_digest": file_digest(context.build_path / STUDY),
    "completed_cases": len(result["cases"]), "intended_cases": result["intended_cases"],
    "issues": result["issues"],
  }, result["issues"])
