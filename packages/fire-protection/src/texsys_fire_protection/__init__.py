"""Fire barrier, round penetration and firestop applicability checks."""

from dataclasses import asdict, dataclass
from itertools import combinations, pairwise
import math

from texsys_spatial import Space


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


@dataclass(frozen=True)
class FireCompartment:
  """A fire compartment declared as a space envelope (GB 50174 4.3.2 / 13.2.4).

  Modeling the compartment as a ``Space`` rather than a device makes membership,
  boundary-face and adjacency queries deterministic geometric checks.
  """

  identity: str
  space: Space
  role: str = ""

  def __post_init__(self):
    if not isinstance(self.identity, str) or not self.identity.strip():
      raise ValueError("A fire compartment needs a non-empty identity.")
    if not isinstance(self.space, Space):
      raise ValueError("A fire compartment needs a spatial envelope.")
    if not isinstance(self.role, str):
      raise ValueError("A fire compartment role must be a string.")

  def contains_point(self, point, *, inset_m=0.0):
    return self.space.contains_point(point, inset_m=inset_m)


def compartment_of(point, compartments):
  """Identity of the smallest-volume compartment containing a point, or None.

  Smallest volume wins for nested fire zones so the innermost zone resolves first.
  Declare compartments at the fire-separation granularity, not individual rooms.
  """
  candidates = [compartment for compartment in compartments.values() if compartment.contains_point(point)]
  if not candidates:
    return None
  return min(candidates, key=lambda compartment: compartment.space.volume_m3).identity


def _barrier_covers_face(barrier, face):
  return (barrier.axis == face.axis and abs(barrier.station_m - face.station_m) <= _TOL
          and all(b_lo - _TOL <= f_lo and f_hi <= b_hi + _TOL
                  for (b_lo, b_hi), (f_lo, f_hi) in zip(barrier.extent_m, face.extent_m, strict=True)))


_SEPARATION_FAILURE = {"same-compartment": "different-compartment",
                       "unprotected-separation": "barrier-declared",
                       "inadequate-rating": "rating"}


def check_separation(compartments, barriers, point_a, point_b, required_min, ratings):
  """Deterministic common-cause independence of two locations (GB 50174 4.3.2 + 13.2.4).

  Pass requires both points located in different compartments, an adjacent shared face,
  a declared barrier covering that face, and a provided fire rating meeting ``required_min``.
  Missing compartment or rating evidence, and non-adjacent compartments, return
  ``undecidable`` rather than pass. ``ratings`` maps a barrier identity to its provided
  (tested) rating, e.g. ``{"L01/Hall-Services": {"wall": 2.0}}``.

  Direct adjacency is checked here; independence across several compartment hops needs
  the adjacency graph and is reported as ``non-adjacent`` (undecidable) for now.
  """
  if not _ratings(required_min):
    raise ValueError("Separation needs positive finite required fire ratings.")
  if any(not isinstance(name, str) or not name.strip() for name in compartments.keys() | barriers.keys()):
    raise ValueError("Compartment and barrier identities must be non-empty strings.")
  if any(not isinstance(compartment, FireCompartment) for compartment in compartments.values()):
    raise ValueError("Every compartment must be a FireCompartment.")
  if not isinstance(ratings, dict) or any(not _ratings(value) for value in ratings.values()):
    raise ValueError("Barrier ratings must map identities to positive finite rating criteria.")

  ca, cb = compartment_of(point_a, compartments), compartment_of(point_b, compartments)
  checks = {"located": ca is not None and cb is not None,
            "different-compartment": ca is not None and cb is not None and ca != cb,
            "adjacent": False, "barrier-declared": False, "rating": False}
  result = {"compartment_a": ca, "compartment_b": cb, "shared_face": None, "barrier": None,
            "required_min": dict(required_min), "checks": checks}

  if not checks["located"]:
    result.update(status="undecidable", reason="missing-compartment")
  elif not checks["different-compartment"]:
    result.update(status="fail", reason="same-compartment")
  else:
    faces = compartments[ca].space.shared_faces(compartments[cb].space)
    checks["adjacent"] = bool(faces)
    if not faces:
      result.update(status="undecidable", reason="non-adjacent")
    else:
      face = faces[0]
      result["shared_face"] = asdict(face)
      covering = sorted(name for name, barrier in barriers.items() if _barrier_covers_face(barrier, face))
      checks["barrier-declared"] = bool(covering)
      if not covering:
        result.update(status="fail", reason="unprotected-separation")
      else:
        barrier_id = covering[0]
        result["barrier"] = barrier_id
        provided = ratings.get(barrier_id)
        if provided is None:
          result.update(status="undecidable", reason="missing-rating-evidence")
        else:
          checks["rating"] = all(provided.get(key, 0) >= required for key, required in required_min.items())
          result["provided_rating"] = dict(provided)
          result.update(status="pass" if checks["rating"] else "fail",
                        reason="rated-separation" if checks["rating"] else "inadequate-rating")

  result["failed_checks"] = [_SEPARATION_FAILURE[result["reason"]]] if result["status"] == "fail" else []
  return result
