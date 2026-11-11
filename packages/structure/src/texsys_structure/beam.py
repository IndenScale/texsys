"""Elastic response of a simply supported strip under downward static loads."""

from dataclasses import dataclass
import math


def _nonnegative(value: float) -> bool:
  return type(value) in {int, float} and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class BeamResponse:
  reaction_start_kn: float
  reaction_end_kn: float
  shear_kn: float
  moment_kn_m: float
  moment_at_m: float
  deflection_m: float
  deflection_at_m: float


def _maximum_station(slope) -> float:
  left, right = 0.0, 1.0
  for _ in range(52):
    middle = (left + right) / 2
    if slope(middle) > 0:
      left = middle
    else:
      right = middle
  return (left + right) / 2


def simple_beam(
  span_m: float, ei_kn_m2: float, uniform_kn_m: float,
  points: tuple[tuple[float, float], ...] = (),
  *, patches: tuple[tuple[float, float, float], ...] = (),
) -> BeamResponse:
  """Return positive peaks for uniform, partial uniform and point loads."""
  if not all(_nonnegative(value) and value > 0 for value in (span_m, ei_kn_m2)):
    raise ValueError("A beam needs a positive finite span and flexural rigidity.")
  if not _nonnegative(uniform_kn_m) or any(not _nonnegative(x) or x > span_m or not _nonnegative(load)
                                         for x, load in points):
    raise ValueError("Beam loads must be finite and downward, with points on the span.")
  if any(not all(map(_nonnegative, (a, b, load))) or not a < b <= span_m for a, b, load in patches):
    raise ValueError("Beam patches must be downward and bound a positive interval on the span.")
  uniform = uniform_kn_m * span_m
  # Loads exactly at the support contribute to its reaction without entering the span.
  at_start = sum(load for x, load in points if x == 0)
  at_end = sum(load for x, load in points if x == span_m)
  internal = [(x / span_m, load) for x, load in points if 0 < x < span_m]
  strips = [(a / span_m, b / span_m, load * span_m) for a, b, load in patches]
  patch_reactions = [(load * (b - a), (a + b) / 2) for a, b, load in strips]
  end = uniform / 2 + sum(t * load for t, load in internal)
  end += sum(weight * centroid for weight, centroid in patch_reactions)
  start = uniform / 2 + sum((1 - t) * load for t, load in internal)
  start += sum(weight * (1 - centroid) for weight, centroid in patch_reactions)
  if start == end == 0:
    return BeamResponse(at_start, at_end, 0, 0, 0, 0, 0)

  def patch_term(t, order):
    return sum(load * (max(t - a, 0) ** order - max(t - b, 0) ** order) for a, b, load in strips)

  # Integrate the dimensionless bending diagram, with zero deflection at both supports.
  slope_start = start / 6 - uniform / 24 - sum(load * (1 - t) ** 3 / 6 for t, load in internal)
  slope_start -= patch_term(1, 4) / 24
  moment_t = _maximum_station(lambda t: start - uniform * t - sum(load for x, load in internal if x <= t)
                              - patch_term(t, 1))
  deflection_t = _maximum_station(
    lambda t: slope_start - start * t ** 2 / 2 + uniform * t ** 3 / 6
    + sum(load * max(t - x, 0) ** 2 / 2 for x, load in internal) + patch_term(t, 3) / 6
  )
  moment = start * moment_t - uniform * moment_t ** 2 / 2
  moment -= sum(load * max(moment_t - t, 0) for t, load in internal)
  moment -= patch_term(moment_t, 2) / 2
  t = deflection_t
  deflection = slope_start * t - start * t ** 3 / 6 + uniform * t ** 4 / 24
  deflection += sum(load * max(t - x, 0) ** 3 / 6 for x, load in internal)
  deflection += patch_term(t, 4) / 24
  return BeamResponse(start + at_start, end + at_end, max(start, end), moment * span_m, moment_t * span_m,
                      deflection * span_m ** 3 / ei_kn_m2, deflection_t * span_m)
