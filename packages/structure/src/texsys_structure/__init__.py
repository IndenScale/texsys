"""Reusable structural steel geometry and grid-based frame assembly."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import pairwise, product
import math

from texsys.geometry import Box
from texsys_spatial import AxisGrid, DatumPlane


@dataclass(frozen=True)
class ISection:
  """Nominal doubly symmetric I/H section dimensions in meters."""

  depth: float
  width: float
  web: float
  flange: float

  def __post_init__(self) -> None:
    if not all(type(value) in {int, float} and math.isfinite(value) and value > 0
               for value in (self.depth, self.width, self.web, self.flange)):
      raise ValueError("Steel section dimensions must be positive and finite.")
    if self.web >= self.width or 2 * self.flange >= self.depth:
      raise ValueError("Steel section web and flanges must fit the section envelope.")

  @property
  def area_m2(self) -> float:
    return 2 * self.width * self.flange + self.web * (self.depth - 2 * self.flange)

  @property
  def major_inertia_m4(self) -> float:
    web_depth = self.depth - 2 * self.flange
    return (self.width * (self.depth ** 3 - web_depth ** 3) + self.web * web_depth ** 3) / 12

  def beam(self, start: float, stop: float, cross: float, top: float, *, along_x: bool) -> tuple[Box, ...]:
    """Place a horizontal beam by span, centerline and top elevation."""
    if not all(map(math.isfinite, (start, stop, cross, top))) or start >= stop:
      raise ValueError("A steel beam needs a finite positive span.")
    bottom = top - self.depth
    parts = (
      ("BottomFlange", self.width, bottom, self.flange),
      ("Web", self.web, bottom + self.flange, self.depth - 2 * self.flange),
      ("TopFlange", self.width, top - self.flange, self.flange),
    )
    boxes = []
    for name, width, elevation, thickness in parts:
      origin = (start, cross - width / 2, elevation)
      size = (stop - start, width, thickness)
      if not along_x:
        origin, size = (origin[1], origin[0], origin[2]), (size[1], size[0], size[2])
      boxes.append(Box(name, origin, size))
    return tuple(boxes)

  def column(self, center: tuple[float, float], base: float, top: float) -> tuple[Box, ...]:
    """Place a vertical column at a centerline intersection."""
    x, y = center
    if not all(map(math.isfinite, (x, y, base, top))) or base >= top:
      raise ValueError("A steel column needs a finite positive height.")
    height = top - base
    return (
      Box("SouthFlange", (x - self.width / 2, y - self.depth / 2, base), (self.width, self.flange, height)),
      Box("NorthFlange", (x - self.width / 2, y - self.depth / 2 + self.depth - self.flange, base),
          (self.width, self.flange, height)),
      Box("Web", (x - self.web / 2, y - self.depth / 2 + self.flange, base),
          (self.web, self.depth - 2 * self.flange, height)),
    )


def grid_frame(
  grid: AxisGrid,
  column_lines: Sequence[str],
  base: DatumPlane,
  beam_top: DatumPlane,
  column_section: ISection,
  beam_section: ISection,
  *,
  base_plate: tuple[float, float, float],
) -> Iterator[tuple[str, Box]]:
  """Generate a scheme frame with longitudinal bays and transverse ties."""
  if base.frame != grid.frame or beam_top.frame != grid.frame:
    raise ValueError("Steel frame grid and datum planes must use the same frame.")
  lines = set(column_lines)
  if len(lines) != len(column_lines) or len(lines) < 2 or not lines <= grid.y.keys() or len(grid.x) < 2:
    raise ValueError("Steel frame needs distinct grid lines and at least one bay in each direction.")
  if not all(type(value) in {int, float} and math.isfinite(value) and value > 0 for value in base_plate):
    raise ValueError("Steel base plate dimensions must be positive and finite.")
  ordered_lines = [line for line in grid.y if line in lines]
  plate_x, plate_y, _ = base_plate
  bottom, top = base.elevation_m, beam_top.elevation_m
  for x_axis, y_axis in product(grid.x, ordered_lines):
    x, y = grid.point(x_axis, y_axis)
    owner = f"COL-{x_axis}-{y_axis}"
    for part in column_section.column((x, y), bottom, top - beam_section.depth):
      yield owner, part
    yield owner, Box("BasePlate", (x - plate_x / 2, y - plate_y / 2, bottom), base_plate)
  for y_axis in ordered_lines:
    for first, last in pairwise(grid.x):
      owner = f"BEAM-{first}-{last}-{y_axis}"
      for part in beam_section.beam(grid.x[first], grid.x[last], grid.y[y_axis], top, along_x=True):
        yield owner, part
  first, last = ordered_lines[0], ordered_lines[-1]
  start, stop = grid.y[first] + beam_section.width / 2, grid.y[last] - beam_section.width / 2
  for x_axis in grid.x:
    owner = f"BEAM-{x_axis}-{first}-{last}"
    for part in beam_section.beam(start, stop, grid.x[x_axis], top, along_x=False):
      yield owner, part
