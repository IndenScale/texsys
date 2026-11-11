"""Fire barrier, round penetration and firestop applicability checks."""

from dataclasses import asdict, dataclass
from itertools import combinations, pairwise
import math


Point = tuple[float, float, float]
_TOL = 1e-8


def _finite(value, *, positive=False):
  return type(value) in {int, float} and math.isfinite(value) and (not positive or value > 0)


def _ratings(values):
  return values and all(key.strip() and _finite(value, positive=True) for key, value in values.items())


@dataclass(frozen=True)
class Barrier:
  axis: int
  station_m: float
  extent_m: tuple[tuple[float, float], tuple[float, float]]
  substrate: str
  thickness_mm: float
  required_min: dict[str, float]

  def __post_init__(self):
    if (type(self.axis) is not int or self.axis not in (0, 1, 2) or not _finite(self.station_m)
        or len(self.extent_m) != 2
        or any(not _finite(a) or not _finite(b) or a >= b for a, b in self.extent_m)
        or not self.substrate.strip() or not _finite(self.thickness_mm, positive=True)
        or not _ratings(self.required_min)):
      raise ValueError("A fire barrier needs a bounded plane, substrate, thickness and rating criteria.")

  @property
  def host(self):
    return "floor" if self.axis == 2 else "wall"

  def project(self, point):
    return tuple(value for axis, value in enumerate(point) if axis != self.axis)

  def contains(self, point, inset_m=0):
    return all(low + inset_m - _TOL <= value <= high - inset_m + _TOL
               for value, (low, high) in zip(self.project(point), self.extent_m, strict=True))


@dataclass(frozen=True)
class Route:
  points_m: tuple[Point, ...]
  service: str
  insulation: str
  outside_mm: float

  def __post_init__(self):
    if (len(self.points_m) < 2 or any(len(point) != 3 or not all(map(_finite, point)) for point in self.points_m)
        or any(math.dist(a, b) <= _TOL for a, b in pairwise(self.points_m))
        or not self.service.strip() or not self.insulation.strip() or not _finite(self.outside_mm, positive=True)):
      raise ValueError("A route needs a nondegenerate centerline and a round penetrant specification.")


@dataclass(frozen=True)
class Firestop:
  host: str
  substrate: str
  min_thickness_mm: float
  service: str
  insulation: str
  max_outside_mm: float
  annular_range_mm: tuple[float, float]
  edge_spacing_mm: float
  rating_min: dict[str, float]
  evidence: str
  state: str

  def __post_init__(self):
    low, high = self.annular_range_mm
    if (self.host not in {"wall", "floor"} or not all(v.strip() for v in
                                                     (self.substrate, self.service, self.insulation, self.evidence))
        or not all(_finite(v, positive=True) for v in (self.min_thickness_mm, self.max_outside_mm))
        or not all(_finite(v) and v >= 0 for v in (low, high, self.edge_spacing_mm)) or low > high
        or not _ratings(self.rating_min) or self.state not in {"assumed", "documented"}):
      raise ValueError("A firestop needs applicable geometry, rating criteria and an evidence reference.")


@dataclass(frozen=True)
class Selection:
  system: str
  center_m: Point
  diameter_mm: float

  def __post_init__(self):
    if (not self.system.strip() or len(self.center_m) != 3 or not all(map(_finite, self.center_m))
        or not _finite(self.diameter_mm, positive=True)):
      raise ValueError("A firestop selection needs a system and a round opening at a finite position.")


def _side(distance):
  return 1 if distance > _TOL else -1 if distance < -_TOL else 0


def _touches_face(barrier, a, b):
  entry, leave = 0.0, 1.0
  for start, end, (low, high) in zip(barrier.project(a), barrier.project(b), barrier.extent_m, strict=True):
    delta = end - start
    if abs(delta) <= _TOL:
      if not low - _TOL <= start <= high + _TOL:
        return False
    else:
      first, last = sorted(((low - _TOL - start) / delta, (high + _TOL - start) / delta))
      entry, leave = max(entry, first), min(leave, last)
  return entry <= leave


def _normal(barrier, a, b):
  return math.dist(barrier.project(a), barrier.project(b)) <= _TOL


def _crossings(barrier, points):
  """Yield intersections in route order, with exceptional plane contacts flagged."""
  distances = [p[barrier.axis] - barrier.station_m for p in points]
  sides = [_side(distance) for distance in distances]
  for i, (a, b) in enumerate(pairwise(points)):
    if sides[i] == sides[i + 1] == 0 and _touches_face(barrier, a, b):
      raise ValueError("Route lies in the fire barrier plane.")
    if sides[i] == 0 and barrier.contains(a):
      if i == 0:
        raise ValueError("Route starts on a fire barrier.")
      if sides[i - 1] * sides[i + 1] == -1:
        yield a, _normal(barrier, points[i - 1], a) and _normal(barrier, a, b)
    if sides[i] * sides[i + 1] == -1:
      t = distances[i] / (distances[i] - distances[i + 1])
      point = tuple(start + (end - start) * t for start, end in zip(a, b, strict=True))
      if barrier.contains(point):
        yield point, _normal(barrier, a, b)
  if sides[-1] == 0 and barrier.contains(points[-1]):
    raise ValueError("Route ends on a fire barrier.")


def _applicability(barrier, route, point, perpendicular, selection, system):
  radius_m = selection.diameter_mm / 2000
  eccentric_mm = 1000 * math.dist(barrier.project(point), barrier.project(selection.center_m))
  centered_gap = (selection.diameter_mm - route.outside_mm) / 2
  gap = (centered_gap - eccentric_mm, centered_gap + eccentric_mm)
  checks = {
    "opening-on-host": abs(selection.center_m[barrier.axis] - barrier.station_m) <= _TOL
    and barrier.contains(selection.center_m, radius_m),
    "normal-route": perpendicular,
    "host": system.host == barrier.host and system.substrate == barrier.substrate,
    "thickness": barrier.thickness_mm >= system.min_thickness_mm,
    "penetrant": system.service == route.service and system.insulation == route.insulation
    and route.outside_mm <= system.max_outside_mm,
    "annular-space": gap[0] >= system.annular_range_mm[0] - _TOL and gap[1] <= system.annular_range_mm[1] + _TOL,
    "rating": all(system.rating_min.get(key, 0) >= required for key, required in barrier.required_min.items()),
  }
  return checks, gap


def check_penetrations(
  barriers: dict[str, Barrier], routes: dict[str, Route], selections: dict[str, Selection], systems: dict[str, Firestop],
) -> dict:
  """Match real centerline crossings to independent selections and their declared evidence."""
  if any(not name.strip() or "@" in name or "#" in name for name in routes.keys() | barriers.keys()):
    raise ValueError("Fire route and barrier identities cannot contain the crossing delimiters '@' and '#'.")
  for (first, a), (second, b) in combinations(sorted(barriers.items()), 2):
    if a.axis == b.axis and abs(a.station_m - b.station_m) <= _TOL and all(
      min(end_a, end_b) - max(start_a, start_b) > _TOL
      for (start_a, end_a), (start_b, end_b) in zip(a.extent_m, b.extent_m, strict=True)
    ):
      raise ValueError(f"Fire barriers {first} and {second} overlap on the same plane.")
  results, occupied = {}, {}
  for route_id, route in sorted(routes.items()):
    for barrier_id, barrier in sorted(barriers.items()):
      try:
        crossings = tuple(_crossings(barrier, route.points_m))
      except ValueError as error:
        raise ValueError(f"{route_id} at {barrier_id}: {error}") from error
      for index, (point, perpendicular) in enumerate(crossings, 1):
        key = f"{route_id}@{barrier_id}#{index}"
        record = results[key] = {"route": route_id, "barrier": barrier_id, "point_m": point}
        if key not in selections:
          record["checks"] = {"selection": False}
          continue
        selection = selections[key]
        if selection.system not in systems:
          raise ValueError(f"Unknown firestop system {selection.system!r} at {key}.")
        system = systems[selection.system]
        checks, gap = _applicability(barrier, route, point, perpendicular, selection, system)
        record.update({"checks": checks, "selection": asdict(selection), "annular_space_mm": gap,
                       "evidence": system.evidence, "evidence_state": system.state})
        occupied.setdefault(barrier_id, []).append((key, selection, system.edge_spacing_mm))
  for barrier_id, openings in occupied.items():
    barrier = barriers[barrier_id]
    for key, _, _ in openings:
      results[key]["checks"]["opening-spacing"] = True
    for (first, a, spacing_a), (second, b, spacing_b) in combinations(openings, 2):
      gap = 1000 * math.dist(barrier.project(a.center_m), barrier.project(b.center_m)) - (a.diameter_mm + b.diameter_mm) / 2
      if gap < max(spacing_a, spacing_b) - _TOL:
        results[first]["checks"]["opening-spacing"] = results[second]["checks"]["opening-spacing"] = False
  for record in results.values():
    record["failed_checks"] = sorted(key for key, passed in record["checks"].items() if not passed)
    record["status"] = "fail" if record["failed_checks"] else "pass"
  unused = sorted(selections.keys() - results.keys())
  failed = [key for key, record in results.items() if record["status"] == "fail"]
  return {"status": "fail" if failed or unused else "pass", "crossings": results, "failed_crossings": failed,
          "unused_selections": unused}
