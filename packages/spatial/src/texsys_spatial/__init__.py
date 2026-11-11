"""Discipline-neutral location references in a project coordinate frame."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType


def _finite(value: float) -> bool:
  return type(value) in {int, float} and math.isfinite(value)


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
