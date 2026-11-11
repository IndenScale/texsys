"""Plan clearance and slab regions for structural openings."""

from dataclasses import dataclass
from itertools import pairwise
from math import hypot, isfinite


@dataclass(frozen=True)
class PlanRect:
  left: float
  bottom: float
  right: float
  top: float

  def __post_init__(self):
    if not all(map(isfinite, self.xyxy)) or self.left >= self.right or self.bottom >= self.top:
      raise ValueError("A plan rectangle requires finite positive extents.")

  @property
  def xyxy(self):
    return self.left, self.bottom, self.right, self.top

  def inset(self, amount):
    return PlanRect(self.left + amount, self.bottom + amount, self.right - amount, self.top - amount)

  def contains(self, other, clearance=0.):
    return min(other.left - self.left, other.bottom - self.bottom,
               self.right - other.right, self.top - other.top) >= clearance - 1e-9

  def gap(self, other):
    return hypot(max(0., self.left - other.right, other.left - self.right),
                 max(0., self.bottom - other.top, other.bottom - self.top))

  def overlaps(self, other):
    return max(self.left, other.left) < min(self.right, other.right) and max(self.bottom, other.bottom) < min(self.top, other.top)


@dataclass(frozen=True)
class Sleeve:
  centre_m: tuple[float, float]
  radius_m: float

  def __post_init__(self):
    if (len(self.centre_m) != 2 or not all(map(isfinite, (*self.centre_m, self.radius_m)))
        or self.radius_m <= 0):
      raise ValueError("A sleeve needs a finite plan centre and positive radius.")

  @property
  def bounds(self):
    x, y = self.centre_m
    r = self.radius_m
    return PlanRect(x - r, y - r, x + r, y + r)

  def gap(self, rect):
    x, y = self.centre_m
    return hypot(max(0., rect.left - x, x - rect.right), max(0., rect.bottom - y, y - rect.top)) - self.radius_m


def slab_regions(bounds, holes):
  """Partition a rectangle outside its cutouts into nonoverlapping regions."""
  active = tuple(hole for hole in holes if bounds.overlaps(hole))
  xs = sorted({bounds.left, bounds.right, *(max(bounds.left, hole.left) for hole in active),
               *(min(bounds.right, hole.right) for hole in active)})
  for a, b in pairwise(xs):
    cuts = sorted((max(bounds.bottom, hole.bottom), min(bounds.top, hole.top))
                  for hole in active if hole.left < b and a < hole.right)
    current = bounds.bottom
    for start, end in cuts:
      if current < start:
        yield PlanRect(a, current, b, start)
      current = max(current, end)
    if current < bounds.top:
      yield PlanRect(a, current, b, bounds.top)
