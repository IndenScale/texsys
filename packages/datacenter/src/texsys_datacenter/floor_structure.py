"""Bind data center floor loads and support assumptions to a strip analysis."""

from dataclasses import asdict
from itertools import combinations, pairwise
import math

from texsys_structure import ISection
from texsys_structure.beam import simple_beam
from texsys_structure.contacts import screen_slab_contact
from texsys_structure.openings import PlanRect
from texsys_structure.strips import FloorLoads, PointLoad, StripRating, SupportLine, check_strips

from .unit import _number


def _rack_contacts(racks: dict, pattern: dict) -> dict[str, PointLoad]:
  fractions = {}
  locations = set()
  for name, contact in sorted(pattern.items()):
    x, y = (_number(value, f"{name}.position_fraction", inclusive=True)
            for value in contact["position_fraction"])
    if x > 1 or y > 1 or (x, y) in locations:
      raise ValueError("Rack contact positions must be distinct and lie in the normalized footprint.")
    locations.add((x, y))
    fractions[name] = (x, y, _number(contact["load_fraction"], f"{name}.load_fraction"))
  if not math.isclose(sum(load for _, _, load in fractions.values()), 1, abs_tol=1e-9, rel_tol=0):
    raise ValueError("Rack contact load fractions must sum to one.")
  return {
    f"{rack}/{contact}": PointLoad(
      plan["origin_m"][0] + x * plan["size_m"][0], plan["origin_m"][1] + y * plan["size_m"][1],
      plan["vertical_load_kn"] * load,
    )
    for rack, plan in racks.items() for contact, (x, y, load) in fractions.items()
  }


def _combination(phase: dict, area: float, points: dict[str, PointLoad], factors: dict) -> FloorLoads:
  expected = {"deck_and_concrete", "additional_uniform", "point"}
  if factors.keys() != expected:
    raise ValueError("Each combination needs deck/concrete, additional uniform and point load factors.")
  for name, value in factors.items():
    _number(value, f"load_factors.{name}", inclusive=True)
  deck_and_concrete = (phase["deck_kn"] + phase["concrete_kn"]) / area
  uniform = deck_and_concrete * factors["deck_and_concrete"]
  uniform += phase["additional_uniform_kn_m2"] * factors["additional_uniform"]
  return FloorLoads(uniform, {name: PointLoad(point.x_m, point.y_m, point.kn * factors["point"])
                              for name, point in points.items()})


def _opening_conflicts(openings: dict, edges: tuple[float, ...], lines: dict[str, SupportLine]) -> dict:
  stations = sorted(line.y_m for line in lines.values())
  pending = {}
  for name, opening in openings.items():
    x, y = opening["origin_m"]
    width, length = opening["size_m"]
    pending[name] = {
      "strip_indexes": [i for i, (a, b) in enumerate(pairwise(edges)) if max(a, x) < min(b, x + width)],
      "span_indexes": [i for i, (a, b) in enumerate(pairwise(stations)) if max(a, y) < min(b, y + length)],
      "support_lines": sorted(key for key, line in lines.items() if y <= line.y_m <= y + length),
    }
  return pending


def _equipment_contacts(equipment, bounds, openings, config, depth, point_factor):
  effective = _number(config["effective_depth_m"], "equipment contact effective depth")
  if effective >= depth:
    raise ValueError("Equipment contact effective depth must fit the minimum concrete depth.")
  holes = [PlanRect(x, y, x + w, y + h) for record in openings.values()
           for (x, y), (w, h) in [(record["origin_m"], record["size_m"])]]
  slab = PlanRect(0, 0, *bounds)
  contacts, checks = {}, {}
  for name, item in sorted(equipment.items()):
    ox, oy = item["origin_m"]
    width, length = item["size_m"]
    footprint = PlanRect(ox, oy, ox + width, oy + length)
    points = _rack_contacts({name: item}, item["feet"])
    for identity, point in points.items():
      check = screen_slab_contact(x=point.x_m, y=point.y_m, size_m=item["bearing_size_m"],
                                  load_kn=point.kn * point_factor, slab=slab, holes=holes, **config)
      if not footprint.contains(PlanRect(*check["contact_xyxy_m"])):
        raise ValueError(f"{identity}: bearing pad extends outside the equipment footprint.")
      checks[identity] = check
    contacts.update(points)
  for (first, a), (second, b) in combinations(checks.items(), 2):
    if PlanRect(*a["punching_xyxy_m"]).overlaps(PlanRect(*b["punching_xyxy_m"])):
      for identity, neighbor in ((first, second), (second, first)):
        checks[identity].setdefault("nearby_contacts_for_group_review", []).append(neighbor)
        checks[identity]["checks"]["independent-punching-perimeter"] = False
        checks[identity]["status"] = "fail"
  return contacts, checks


def _cooling_beam_shares(strips, width, points, factors):
  """Follow each foot through the same strip midpoint and beam ends as total load."""
  result = {}
  for strip in strips:
    x_fraction = sum(strip["x_m"]) / (2 * width)
    for span in strip["spans"]:
      start, end = span["y_m"]
      for identity in span["point_load_ids"]:
        if identity not in points:
          continue
        point = points[identity]
        equipment, _ = identity.rsplit("/", 1)
        y_fraction = (point.y_m - start) / (end - start)
        for beam, share in zip(span["supports"], (1 - y_fraction, y_fraction), strict=True):
          target = result.setdefault(beam, {}).setdefault(equipment, {})
          for combination, key in (("strength", "strength"), ("service", "deflection")):
            ends = target.setdefault(combination, {"start": 0., "end": 0.})
            load = point.kn * share * factors[key]["point"]
            ends["start"] += load * (1 - x_fraction)
            ends["end"] += load * x_fraction
  for combination, key in (("strength", "strength"), ("service", "deflection")):
    expected = sum(point.kn for point in points.values()) * factors[key]["point"]
    applied = sum(sum(item[combination].values()) for devices in result.values() for item in devices.values())
    if not math.isclose(applied, expected, abs_tol=1e-7):
      raise ValueError("Cooling equipment foot reactions must balance at the steel beam ends.")
  return result


def _steel_beams(config, permanent, supports, width, strips, factors, steel_unit_weight) -> dict:
  assignments = config["assignments"]
  if assignments.keys() != permanent.keys():
    raise ValueError("Steel beams must cover every permanent floor support.")
  result = {}
  for name, type_id in sorted(assignments.items()):
    specification = config["types"][type_id]
    section = ISection(**specification["section_m"])
    ei = section.major_inertia_m4 * _number(specification["modulus_gpa"], f"{type_id}.modulus_gpa") * 1e6
    weight = section.area_m2 * steel_unit_weight
    responses = {}
    for combination, key in (("strength", "reaction_kn"), ("deflection", "deflection_case_reaction_kn")):
      points = tuple((sum(strip["x_m"]) / 2, strip["support_lines"][name][key]) for strip in strips)
      responses[combination] = simple_beam(width, ei, weight * factors[combination]["deck_and_concrete"], points)
    limits = {
      "moment_kn_m": _number(specification["moment_kn_m"], f"{type_id}.moment_kn_m"),
      "shear_kn": _number(specification["shear_kn"], f"{type_id}.shear_kn"),
      "deflection_m": width / _number(specification["deflection_span_ratio"], f"{type_id}.deflection_span_ratio"),
    }
    strength, deflection = responses["strength"], responses["deflection"]
    utilization = {"moment": strength.moment_kn_m / limits["moment_kn_m"],
                   "shear": strength.shear_kn / limits["shear_kn"],
                   "deflection": deflection.deflection_m / limits["deflection_m"]}
    result[name] = {
      "type": type_id, "section_m": asdict(section), "span_m": width, "y_m": supports[name].y_m,
      "ei_kn_m2": ei, "self_weight_kn_m": weight, "limits": limits, "utilization": utilization,
      "strength": asdict(strength), "deflection": asdict(deflection),
    }
  return result


def evaluate_floor_structure(config: dict, bounds, phases: dict, racks: dict, openings: dict,
                             steel_unit_weight: float, *, cooling=None, concrete_min_depth=None) -> dict:
  basis = config["basis"]
  if not isinstance(basis, str) or not basis.strip():
    raise ValueError("Structural screening needs an explicit rating and load basis.")
  width, length = bounds
  edges = tuple(_number(value, "strip_edges_x_m", inclusive=True) for value in config["strip_edges_x_m"])
  if not edges or not math.isclose(edges[-1], width, abs_tol=1e-8, rel_tol=0):
    raise ValueError("Structural strips must cover the bay width.")
  permanent = config["permanent_supports"]
  temporary = config["temporary_supports"]
  if permanent.keys() & temporary.keys():
    raise ValueError("Permanent and temporary supports need distinct identities.")
  if len(permanent) < 2 or any(not name.strip() for name in permanent.keys() | temporary.keys()):
    raise ValueError("Structural screening needs named permanent support lines.")
  permanent_y = [_number(record["y_m"], "permanent support y_m", inclusive=True) for record in permanent.values()]
  if min(permanent_y) != 0 or not math.isclose(max(permanent_y), length, abs_tol=1e-8, rel_tol=0):
    raise ValueError("Permanent supports must bound the bay in the span direction.")
  if any(not 0 < _number(record["y_m"], "temporary support y_m") < length for record in temporary.values()):
    raise ValueError("Temporary supports must be inside the bay.")

  base_points = {
    "service": _rack_contacts(racks, config["rack_contacts"]),
    "pour": {name: PointLoad(**record) for name, record in sorted(config["pour_point_loads"].items())},
  }
  if config["ratings"].keys() != phases.keys() or config["load_factors"].keys() != phases.keys():
    raise ValueError("Structural ratings and load factors must cover both phases.")
  ratings = {phase: StripRating(**rating) for phase, rating in config["ratings"].items()}
  cooling_points, contact_checks = {}, {}
  if cooling:
    cooling_points, contact_checks = _equipment_contacts(cooling, bounds, openings, config["equipment_contacts"],
                                                         concrete_min_depth, config["load_factors"]["service"]["strength"]["point"])
  cases = {}
  for name, case in sorted(config["cases"].items()):
    phase, expected, shored = case["phase"], case["expected"], case["include_temporary"]
    include_racks = case.get("include_rack_loads", phase == "service")
    include_cooling = case.get("include_cooling_loads", include_racks and bool(cooling))
    if (phase not in phases or expected not in {"pass", "fail"} or type(shored) is not bool
        or type(include_racks) is not bool or (include_racks and phase != "service")
        or type(include_cooling) is not bool or (include_cooling and (phase != "service" or not cooling))
        or (shored and (phase != "pour" or not temporary))):
      raise ValueError(f"Invalid structural floor case {name}.")
    selected = {**permanent, **temporary} if shored else permanent
    supports = {key: SupportLine(record["y_m"], record["reaction_kn_per_m"][phase])
                for key, record in selected.items()}
    points = base_points[phase] if phase != "service" or include_racks else {}
    if include_cooling:
      points = points | cooling_points
    factors = config["load_factors"][phase]
    strength = _combination(phases[phase], width * length, points, factors["strength"])
    deflection = _combination(phases[phase], width * length, points, factors["deflection"])
    result = check_strips(edges, supports, ratings[phase], strength, deflection)
    if include_cooling:
      result["checks"]["equipment-contacts"] = all(record["status"] == "pass" for record in contact_checks.values())
    beams = {}
    if "steel_support_beams" in config:
      beams = _steel_beams(config["steel_support_beams"], permanent, supports, width, result["strips"], factors,
                           steel_unit_weight)
      for mode in ("moment", "shear", "deflection"):
        utilization = max(beam["utilization"][mode] for beam in beams.values())
        result["max_utilization"][f"steel-beam-{mode}"] = utilization
        result["checks"][f"steel-beam-{mode}"] = utilization <= 1 + 1e-9
    cases[name] = {
      **result, "phase": phase, "include_rack_loads": include_racks, "include_cooling_loads": include_cooling,
      **({"equipment_contacts": contact_checks,
          "cooling_beam_shares_kn": _cooling_beam_shares(result["strips"], width, cooling_points, factors)}
         if include_cooling else {}),
      "temporary_supports": sorted(temporary) if shored else [],
      "status": "pass" if all(result["checks"].values()) else "fail", "expected": expected,
      "supports": {key: asdict(line) for key, line in supports.items()},
      "steel_support_beams": beams,
      "rating": asdict(ratings[phase]), "load_factors": factors,
      "strength_loads": asdict(strength), "deflection_loads": asdict(deflection),
      "openings_pending": _opening_conflicts(openings, edges, supports),
    }
  if {case["phase"] for case in cases.values()} != phases.keys():
    raise ValueError("Structural cases must cover both pour and service phases.")
  return {
    "basis": basis, "scope": "intact-simply-supported-one-way-strips",
    "phase_resistance": {"pour": "permanent-form", "service": "reinforced-concrete-only"},
    "cases": cases, "point_loads": {phase: {key: asdict(load) for key, load in points.items()}
                                   for phase, points in base_points.items()},
    "cooling_point_loads": {key: asdict(load) for key, load in cooling_points.items()},
    "pending": ["opening-reinforcement-and-trimmers", "local-rack-bearing-and-punching",
                "form-local-instability-and-ponding", "construction-stage-shoring-and-connections",
                "service-cracking-creep-and-vibration"],
  }
