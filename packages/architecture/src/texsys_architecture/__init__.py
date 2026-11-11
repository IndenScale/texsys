"""Reusable architectural spaces, hosted openings and stair assemblies."""

from dataclasses import dataclass
from itertools import pairwise
from math import dist, isfinite


def _finite(value):
  return type(value) in (int, float) and isfinite(value)


@dataclass(frozen=True)
class Space:
  minimum_m: tuple[float, float, float]
  maximum_m: tuple[float, float, float]

  def __post_init__(self):
    if (len(self.minimum_m) != 3 or len(self.maximum_m) != 3
        or any(not _finite(a) or not _finite(b) or a >= b
               for a, b in zip(self.minimum_m, self.maximum_m, strict=True))):
      raise ValueError("Architectural spaces require three positive finite extents.")


@dataclass(frozen=True)
class Opening:
  along_m: tuple[float, float]
  vertical_m: tuple[float, float]

  def __post_init__(self):
    if any(len(extent) != 2 or not all(map(_finite, extent)) or extent[0] >= extent[1]
           for extent in (self.along_m, self.vertical_m)):
      raise ValueError("Wall openings need positive finite lateral and vertical extents.")


@dataclass(frozen=True)
class WallPanel:
  axis: int
  station_m: float
  extent_m: tuple[tuple[float, float], tuple[float, float]]
  thickness_m: float

  def __post_init__(self):
    if (type(self.axis) is not int or self.axis not in (0, 1) or not _finite(self.station_m)
        or not _finite(self.thickness_m) or self.thickness_m <= 0):
      raise ValueError("A wall needs a vertical axis-aligned plane and a positive thickness.")
    Opening(*self.extent_m)

  def contains(self, opening):
    return all(a - 1e-8 <= lo < hi <= b + 1e-8
               for (a, b), (lo, hi) in zip(self.extent_m, (opening.along_m, opening.vertical_m), strict=True))

  def solids(self, openings):
    """Slice the host into nonoverlapping pieces outside the opening union."""
    holes = tuple(openings)
    if any(not self.contains(hole) for hole in holes):
      raise ValueError("Wall cutout lies outside its host.")
    (start, end), (sill, head) = self.extent_m
    stations = sorted({start, end, *(min(end, max(start, coordinate)) for hole in holes for coordinate in hole.along_m)})
    for a, b in pairwise(stations):
      intervals = sorted((max(sill, hole.vertical_m[0]), min(head, hole.vertical_m[1]))
                         for hole in holes if hole.along_m[0] < b and a < hole.along_m[1])
      low = sill
      for bottom, top in [*intervals, (head, head)]:
        if low < bottom:
          minimum, maximum = [a, low], [b, bottom]
          minimum.insert(self.axis, self.station_m - self.thickness_m / 2)
          maximum.insert(self.axis, self.station_m + self.thickness_m / 2)
          yield Space(tuple(minimum), tuple(maximum))
        low = max(low, top)


@dataclass(frozen=True)
class Shaft:
  space: Space

  def wall_panels(self, bottom, top, thickness):
    x, y, base = self.space.minimum_m
    end_x, end_y, ceiling = self.space.maximum_m
    if not base <= bottom < top <= ceiling:
      raise ValueError("Shaft walls must cover a valid storey within the shaft.")
    return {
      face: WallPanel(axis, station, (extent, (bottom, top)), thickness)
      for face, axis, station, extent in (
        ("West", 0, x, (y, end_y)), ("East", 0, end_x, (y, end_y)),
        ("South", 1, y, (x, end_x)), ("North", 1, end_y, (x, end_x)),
      )
    }


def switchback(*, wall, sign, entry_y, bottom, top, projection, half_depth, clear_width,
               treads_per_flight, tread_m, landing_m, wall_thickness_m, stair_thickness_m):
  """Compose four flights, connected landings and a path on their walking line."""
  if (type(treads_per_flight) is not int or treads_per_flight < 2 or sign not in (-1, 1)
      or any(not _finite(value) or value <= 0 for value in
             (top - bottom, projection, half_depth, clear_width, tread_m, landing_m, wall_thickness_m, stair_thickness_m))
      or not all(map(_finite, (wall, entry_y)))):
    raise ValueError("A stair needs finite placement, positive extents and a flight schedule.")
  if landing_m < clear_width:
    raise ValueError("The project stair landing is narrower than the flight.")
  inset = wall_thickness_m / 2
  near, far = inset + landing_m, inset + landing_m + treads_per_flight * tread_m
  lane_gap = .12
  second_y = entry_y + clear_width + lane_gap
  y_bounds = (entry_y - clear_width / 2, second_y + clear_width / 2)
  if far + landing_m > projection - inset or y_bounds[1] > entry_y + half_depth - inset:
    raise ValueError("The stair flights and landings do not fit the tower clear envelope.")
  flight_rise = (top - bottom) / 4
  rise = flight_rise / (treads_per_flight + 1)
  steps, landings, path = [], [], []

  def box(a, b, y0, y1, walking_z):
    x0, x1 = sorted((wall + sign * a, wall + sign * b))
    return Space((x0, y0, walking_z - stair_thickness_m), (x1, y1, walking_z))

  near_center, far_center = near - landing_m / 2, far + landing_m / 2
  path.append((wall + sign * .5, entry_y, bottom))
  for index in range(5):
    z = bottom + index * flight_rise
    inner = index % 2 == 0
    landings.append(box(near - landing_m if inner else far, near if inner else far + landing_m, *y_bounds, z))
    center = near_center if inner else far_center
    if index:
      path.append((wall + sign * center, entry_y if index % 2 else second_y, z))
    if index == 4:
      path.extend(((wall + sign * center, entry_y, z), (wall + sign * .5, entry_y, z)))
      break
    lane = entry_y if inner else second_y
    path.append((wall + sign * center, lane, z))
    for step in range(treads_per_flight):
      a = near + step * tread_m if inner else far - (step + 1) * tread_m
      height = z + (step + 1) * rise
      steps.append(box(a, a + tread_m, lane - clear_width / 2, lane + clear_width / 2, height))
      path.append((wall + sign * (a + tread_m / 2), lane, height))
  return {"steps": steps, "landings": landings, "walking_path_m": path,
          "travel_m": sum(dist(a, b) for a, b in pairwise(path)), "riser_m": rise,
          "same_lane_headroom_m": 2 * flight_rise - stair_thickness_m}
