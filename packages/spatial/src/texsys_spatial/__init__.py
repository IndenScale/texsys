"""Discipline-neutral location references in a project coordinate frame."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType


_TOL = 1e-8
_FACE_NAMES = {0: ("West", "East"), 1: ("South", "North"), 2: ("Bottom", "Top")}


def _finite(value: float) -> bool:
  return type(value) in {int, float} and math.isfinite(value)


def _point(value: Sequence[float]) -> tuple[float, float, float]:
  if not isinstance(value, Sequence) or len(value) != 3 or not all(_finite(v) for v in value):
    raise ValueError("A spatial point needs three finite coordinates in meters.")
  return tuple(value)


def _axis(lines: Mapping[str, float]) -> Mapping[str, float]:
  if not isinstance(lines, Mapping) or not lines or any(
    not isinstance(name, str) or not name.strip() or not _finite(value)
    for name, value in lines.items()
  ):
    raise ValueError("An axis family needs named lines at finite coordinates.")
  if len(set(lines.values())) != len(lines):
    raise ValueError("Axis lines in one family must have distinct coordinates.")
  return MappingProxyType(dict(sorted(lines.items(), key=lambda item: item[1])))


@dataclass(frozen=True)
class AxisGrid:
  """Orthogonal named grid lines, in meters in the specified local frame."""

  frame: str
  x: Mapping[str, float]
  y: Mapping[str, float]

  def __post_init__(self) -> None:
    if not isinstance(self.frame, str) or not self.frame.strip():
      raise ValueError("An axis grid needs a frame identifier.")
    object.__setattr__(self, "x", _axis(self.x))
    object.__setattr__(self, "y", _axis(self.y))

  def point(self, x_axis: str, y_axis: str, offset_m: Sequence[float] = (0, 0)) -> tuple[float, float]:
    if not isinstance(x_axis, str) or not isinstance(y_axis, str) or x_axis not in self.x or y_axis not in self.y:
      raise ValueError(f"Unknown grid intersection: {x_axis!r}/{y_axis!r} in {self.frame}.")
    if not isinstance(offset_m, Sequence) or len(offset_m) != 2 or not all(_finite(value) for value in offset_m):
      raise ValueError("A grid offset needs two finite coordinates in meters.")
    point = self.x[x_axis] + offset_m[0], self.y[y_axis] + offset_m[1]
    if not all(_finite(value) for value in point):
      raise ValueError("Resolved grid point must be finite.")
    return point


@dataclass(frozen=True)
class DatumPlane:
  """A named horizontal reference plane in a Z-up local frame."""

  frame: str
  name: str
  elevation_m: float

  def __post_init__(self) -> None:
    if not all(isinstance(value, str) and value.strip() for value in (self.frame, self.name)):
      raise ValueError("A datum plane needs a name and frame identifier.")
    if not _finite(self.elevation_m):
      raise ValueError("A datum plane needs a finite elevation in meters.")


@dataclass(frozen=True)
class Face:
  """A named axis-aligned planar face of a space envelope, in meters.

  ``axis`` is the normal axis (0=x, 1=y, 2=z); ``extent_m`` bounds the face in the
  other two axes in ascending axis order, matching the fire-barrier plane convention.
  """

  name: str
  axis: int
  station_m: float
  extent_m: tuple[tuple[float, float], tuple[float, float]]

  def __post_init__(self) -> None:
    if (not isinstance(self.name, str) or not self.name.strip()
        or type(self.axis) is not int or self.axis not in (0, 1, 2) or not _finite(self.station_m)
        or len(self.extent_m) != 2
        or any(not _finite(a) or not _finite(b) or a >= b for a, b in self.extent_m)):
      raise ValueError("A face needs a name, a normal axis, a finite station and positive extents.")

  def contains(self, point: Sequence[float], *, tol: float = _TOL) -> bool:
    """Whether a point lies on this face plane within its bounded extent."""
    point = _point(point)
    if abs(point[self.axis] - self.station_m) > tol:
      return False
    projected = tuple(value for axis, value in enumerate(point) if axis != self.axis)
    return all(low - tol <= value <= high + tol
               for value, (low, high) in zip(projected, self.extent_m, strict=True))


@dataclass(frozen=True)
class Space:
  """An axis-aligned orthogonal space envelope in meters in a Z-up local frame.

  Foundational container shared by architectural rooms, fire compartments and any
  discipline needing containment, boundary-face and adjacency queries.
  """

  minimum_m: tuple[float, float, float]
  maximum_m: tuple[float, float, float]

  def __post_init__(self) -> None:
    if (len(self.minimum_m) != 3 or len(self.maximum_m) != 3
        or any(not _finite(a) or not _finite(b) or a >= b
               for a, b in zip(self.minimum_m, self.maximum_m, strict=True))):
      raise ValueError("A space needs three positive finite extents with minimum below maximum.")

  @property
  def size_m(self) -> tuple[float, float, float]:
    return tuple(hi - lo for lo, hi in zip(self.minimum_m, self.maximum_m, strict=True))

  @property
  def center_m(self) -> tuple[float, float, float]:
    return tuple((lo + hi) / 2 for lo, hi in zip(self.minimum_m, self.maximum_m, strict=True))

  @property
  def volume_m3(self) -> float:
    x, y, z = self.size_m
    return x * y * z

  def contains_point(self, point: Sequence[float], *, inset_m: float = 0.0) -> bool:
    """Whether a point lies in the closed envelope, optionally shrunk by an inset."""
    point = _point(point)
    if not _finite(inset_m) or inset_m < 0:
      raise ValueError("A containment inset must be a finite non-negative distance in meters.")
    return all(low + inset_m - _TOL <= value <= high - inset_m + _TOL
               for value, low, high in zip(point, self.minimum_m, self.maximum_m, strict=True))

  def contains_space(self, other: "Space") -> bool:
    """Whether another space lies wholly within this envelope."""
    return (all(a <= b + _TOL for a, b in zip(self.minimum_m, other.minimum_m, strict=True))
            and all(a >= b - _TOL for a, b in zip(self.maximum_m, other.maximum_m, strict=True)))

  def intersects(self, other: "Space") -> bool:
    """Whether two envelopes share positive volume (touching faces do not count)."""
    return all(min(a_hi, b_hi) - max(a_lo, b_lo) > _TOL
               for a_lo, a_hi, b_lo, b_hi in zip(self.minimum_m, self.maximum_m,
                                                 other.minimum_m, other.maximum_m, strict=True))

  def intersection(self, other: "Space") -> "Space | None":
    """The positive-volume overlap envelope, or None when envelopes do not overlap."""
    low = tuple(max(a, b) for a, b in zip(self.minimum_m, other.minimum_m, strict=True))
    high = tuple(min(a, b) for a, b in zip(self.maximum_m, other.maximum_m, strict=True))
    if all(hi - lo > _TOL for lo, hi in zip(low, high, strict=True)):
      return Space(low, high)
    return None

  def faces(self) -> dict[str, Face]:
    """The six named boundary faces (West/East/South/North/Bottom/Top)."""
    (x0, y0, z0), (x1, y1, z1) = self.minimum_m, self.maximum_m
    return {
      "West": Face("West", 0, x0, ((y0, y1), (z0, z1))),
      "East": Face("East", 0, x1, ((y0, y1), (z0, z1))),
      "South": Face("South", 1, y0, ((x0, x1), (z0, z1))),
      "North": Face("North", 1, y1, ((x0, x1), (z0, z1))),
      "Bottom": Face("Bottom", 2, z0, ((x0, x1), (y0, y1))),
      "Top": Face("Top", 2, z1, ((x0, x1), (y0, y1))),
    }

  def boundary_faces(self, point: Sequence[float]) -> tuple[str, ...]:
    """Names of the faces a point lies on (several on an edge or corner, empty inside/outside)."""
    point = _point(point)
    return tuple(name for name, face in self.faces().items() if face.contains(point))

  def locate(self, point: Sequence[float]) -> str:
    """Classify a point as ``inside``, ``surface`` or ``outside`` the envelope."""
    if not self.contains_point(point):
      return "outside"
    return "surface" if self.boundary_faces(point) else "inside"

  def shared_faces(self, other: "Space") -> tuple[Face, ...]:
    """Faces where two envelopes touch on a plane and overlap in the other axes.

    Adjacent compartments share such a face; it is the candidate separating construction.
    """
    result = []
    for axis in range(3):
      others = [i for i in range(3) if i != axis]
      extent = tuple((max(self.minimum_m[i], other.minimum_m[i]),
                      min(self.maximum_m[i], other.maximum_m[i])) for i in others)
      if any(hi - lo <= _TOL for lo, hi in extent):
        continue
      for side, station in enumerate((self.minimum_m[axis], self.maximum_m[axis])):
        opposite = other.maximum_m[axis] if side == 0 else other.minimum_m[axis]
        if abs(station - opposite) <= _TOL:
          result.append(Face(_FACE_NAMES[axis][side], axis, station, extent))
    return tuple(result)
