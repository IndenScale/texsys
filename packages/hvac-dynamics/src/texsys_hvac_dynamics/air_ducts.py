"""Rectangular duct geometry and quadratic external pressure loss."""

from dataclasses import dataclass
from itertools import pairwise
from math import dist, isfinite


@dataclass(frozen=True)
class DuctSection:
  width_m: float
  height_m: float
  friction_factor: float

  def __post_init__(self):
    if any(type(value) not in (int, float) or not isfinite(value) or value <= 0
           for value in (self.width_m, self.height_m, self.friction_factor)):
      raise ValueError("Duct dimensions and friction factor must be positive finite numbers.")

  @property
  def area_m2(self):
    return self.width_m * self.height_m

  def resistance(self, length_m, loss_coefficient, density):
    hydraulic_diameter = 2 * self.area_m2 / (self.width_m + self.height_m)
    return density / (2 * self.area_m2**2) * (self.friction_factor * length_m / hydraulic_diameter + loss_coefficient)

  def boxes(self, points):
    for a, b in pairwise(points):
      moving = [i for i in range(3) if a[i] != b[i]]
      if len(moving) != 1:
        raise ValueError("Duct centerlines need nonzero orthogonal segments.")
      radius = [self.width_m / 2, self.height_m / 2]
      radius.insert(moving[0], 0.)
      yield tuple((min(x, y) - r, max(x, y) + r) for x, y, r in zip(a, b, radius, strict=True))


def duct_record(points, section, fittings, density):
  points = tuple(dict.fromkeys(tuple(p) for p in points))
  if len(points) < 2 or any(len(point) != 3 or not all(map(isfinite, point)) for point in points):
    raise ValueError("A duct needs finite centerline points.")
  boxes = tuple(section.boxes(points))
  length = sum(dist(a, b) for a, b in pairwise(points))
  return {"points_m": points, "boxes_m": boxes, "length_m": length, "area_m2": section.area_m2,
          "resistance_pa_per_m6_s2": section.resistance(length, fittings, density)}
