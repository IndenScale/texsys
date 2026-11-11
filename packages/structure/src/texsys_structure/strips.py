"""Demand checks for one-way strips with externally specified ratings."""

from bisect import bisect_right
from dataclasses import asdict, dataclass
from itertools import pairwise

from .beam import _nonnegative, simple_beam


def _positive(values) -> bool:
  return all(_nonnegative(value) and value > 0 for value in values)


@dataclass(frozen=True)
class StripRating:
  moment_kn_m_per_m: float
  shear_kn_per_m: float
  ei_kn_m2_per_m: float
  deflection_span_ratio: float

  def __post_init__(self):
    if not _positive((self.moment_kn_m_per_m, self.shear_kn_per_m,
                      self.ei_kn_m2_per_m, self.deflection_span_ratio)):
      raise ValueError("Strip ratings need positive finite moment, shear, rigidity and deflection ratio.")


@dataclass(frozen=True)
class SupportLine:
  y_m: float
  reaction_kn_per_m: float

  def __post_init__(self):
    if not _nonnegative(self.y_m) or not _positive((self.reaction_kn_per_m,)):
      raise ValueError("Support lines need a nonnegative station and a positive finite reaction limit.")


@dataclass(frozen=True)
class PointLoad:
  x_m: float
  y_m: float
  kn: float

  def __post_init__(self):
    if not all(_nonnegative(value) for value in (self.x_m, self.y_m, self.kn)):
      raise ValueError("Point loads need finite nonnegative coordinates and force.")


@dataclass(frozen=True)
class FloorLoads:
  uniform_kn_m2: float
  points: dict[str, PointLoad]

  def __post_init__(self):
    if not _nonnegative(self.uniform_kn_m2):
      raise ValueError("Floor pressure must be finite and nonnegative.")


def _load_buckets(edges, stations, loads: FloorLoads):
  buckets = {}
  for name, point in sorted(loads.points.items()):
    if point.x_m > edges[-1] or point.y_m > stations[-1]:
      raise ValueError(f"Point load {name} is outside the strip layout.")
    strip = min(bisect_right(edges, point.x_m) - 1, len(edges) - 2)
    span = min(bisect_right(stations, point.y_m) - 1, len(stations) - 2)
    buckets.setdefault((strip, span), {})[name] = (point.y_m - stations[span], point.kn)
  return buckets


def check_strips(
  edges_x_m: tuple[float, ...], supports: dict[str, SupportLine], rating: StripRating,
  strength: FloorLoads, deflection: FloorLoads,
) -> dict:
  """Analyze each strip span and sum adjacent reactions on each supporting line."""
  lines = sorted(supports, key=lambda name: supports[name].y_m)
  stations = tuple(supports[name].y_m for name in lines)
  for coordinates in (edges_x_m, stations):
    if (len(coordinates) < 2 or not all(_nonnegative(x) for x in coordinates) or coordinates[0] != 0
        or any(a >= b for a, b in pairwise(coordinates))):
      raise ValueError("Strip edges and support stations must increase from zero.")
  strength_points = _load_buckets(edges_x_m, stations, strength)
  deflection_points = _load_buckets(edges_x_m, stations, deflection)
  strips = []
  total_reactions = dict.fromkeys(lines, 0.0)
  for index, (left, right) in enumerate(pairwise(edges_x_m)):
    width = right - left
    reactions = dict.fromkeys(lines, 0.0)
    deflection_reactions = dict.fromkeys(lines, 0.0)
    spans = []
    for i, (start, end) in enumerate(pairwise(stations)):
      length = end - start
      limits = {"moment_kn_m": rating.moment_kn_m_per_m * width, "shear_kn": rating.shear_kn_per_m * width,
                "deflection_m": length / rating.deflection_span_ratio}
      ei = rating.ei_kn_m2_per_m * width
      strength_here = strength_points.get((index, i), {})
      deflection_here = deflection_points.get((index, i), {})
      strength_result = simple_beam(length, ei, strength.uniform_kn_m2 * width, tuple(strength_here.values()))
      deflection_result = simple_beam(length, ei, deflection.uniform_kn_m2 * width, tuple(deflection_here.values()))
      utilization = {"moment": strength_result.moment_kn_m / limits["moment_kn_m"],
                     "shear": strength_result.shear_kn / limits["shear_kn"],
                     "deflection": deflection_result.deflection_m / limits["deflection_m"]}
      reactions[lines[i]] += strength_result.reaction_start_kn
      reactions[lines[i + 1]] += strength_result.reaction_end_kn
      deflection_reactions[lines[i]] += deflection_result.reaction_start_kn
      deflection_reactions[lines[i + 1]] += deflection_result.reaction_end_kn
      spans.append({"y_m": [start, end], "supports": lines[i:i + 2], "limits": limits, "utilization": utilization,
                    "strength": asdict(strength_result), "deflection": asdict(deflection_result),
                    "point_load_ids": sorted(strength_here.keys() | deflection_here.keys())})
    bearings = {}
    for name, reaction in reactions.items():
      limit = supports[name].reaction_kn_per_m * width
      bearings[name] = {"reaction_kn": reaction, "limit_kn": limit, "utilization": reaction / limit,
                        "deflection_case_reaction_kn": deflection_reactions[name]}
      total_reactions[name] += reaction
    strips.append({"x_m": [left, right], "spans": spans, "support_lines": bearings})
  utilizations = {
    mode: max(span["utilization"][mode] for strip in strips for span in strip["spans"])
    for mode in ("moment", "shear", "deflection")
  }
  utilizations["support-reaction"] = max(line["utilization"] for strip in strips
                                        for line in strip["support_lines"].values())
  checks = {mode: value <= 1 + 1e-9 for mode, value in utilizations.items()}
  return {"strips": strips, "max_utilization": utilizations, "checks": checks,
          "strength_reactions_kn_by_support": total_reactions}
