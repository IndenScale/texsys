"""Linear rigid-diaphragm response of aligned, tension-only X-braced bays."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise, product
from math import hypot, isfinite

import numpy as np


@dataclass(frozen=True)
class BraceBay:
  axis: str
  start_m: tuple[float, float]
  end_m: tuple[float, float]
  area_m2: float
  modulus_kn_m2: float

  def __post_init__(self):
    if self.axis not in ("x", "y"):
      raise ValueError("Brace direction must be x or y.")
    active = 0 if self.axis == "x" else 1
    fixed = 1 - active
    if (not all(isfinite(value) for value in (*self.start_m, *self.end_m, self.area_m2, self.modulus_kn_m2))
        or self.end_m[active] <= self.start_m[active]
        or self.end_m[fixed] != self.start_m[fixed]
        or min(self.area_m2, self.modulus_kn_m2) <= 0):
      raise ValueError("Brace bay requires aligned points in ascending order and positive EA.")

  @property
  def span_m(self):
    return hypot(*(end - start for start, end in zip(self.start_m, self.end_m, strict=True)))


class LateralMechanism(ValueError):
  """The active brace bays cannot restrain both translations and twist."""

  def __init__(self, level: str):
    super().__init__(f"Active brace bays do not restrain the diaphragm at {level}.")
    self.level = level


def solve_braced_frame(
  levels_m: Mapping[str, float],
  base_m: float,
  plan_m: tuple[tuple[float, float], tuple[float, float]],
  bays: Mapping[str, BraceBay],
  actions_kn: Mapping[str, Sequence[float]],
) -> dict:
  """Resolve (Fx, Fy, Mz) at the plan centre into story drifts and brace forces.

  Chord increments are positive in compression. Only one of each pair of
  equal diagonals resists load at any time. Chord axial flexibility, joint
  flexibility and second-order effects are outside this model.
  """
  levels = sorted(levels_m.items(), key=lambda item: item[1])
  if (not levels or set(actions_kn) != set(levels_m)
      or any(len(values) != 3 or not all(map(isfinite, values)) for values in actions_kn.values())
      or any(not (isfinite(a) and isfinite(b) and b > a) for a, b in pairwise((base_m, *(z for _, z in levels))))
      or any(not (isfinite(a) and isfinite(b) and b > a) for a, b in plan_m)):
    raise ValueError("The lateral model requires a finite plan, increasing levels and one action vector per level.")

  center_x, center_y = ((lo + hi) / 2 for lo, hi in plan_m)
  radius = max(hi - lo for lo, hi in plan_m) / 2
  corners = tuple(product(*plan_m))
  # Scale rotational displacement and moment to the same units as translation and force.
  direction = {}
  for name, bay in bays.items():
    x, y = bay.start_m
    direction[name] = np.array((1., 0., -(y - center_y) / radius) if bay.axis == "x"
                               else (0., 1., (x - center_x) / radius))

  storeys, total_shear = {}, np.zeros(3)
  story_bounds = list(pairwise((base_m, *(z for _, z in levels))))
  for ((name, _), (lower, upper)) in reversed(tuple(zip(levels, story_bounds, strict=True))):
    height = upper - lower
    applied = actions_kn[name]
    total_shear += (applied[0], applied[1], applied[2] / radius)
    lengths = {key: hypot(bay.span_m, height) for key, bay in bays.items()}
    stiffness = {key: bay.modulus_kn_m2 * bay.area_m2 / lengths[key] * (bay.span_m / lengths[key]) ** 2
                 for key, bay in bays.items()}
    matrix = np.zeros((3, 3))
    for key, vector in direction.items():
      matrix += stiffness[key] * np.outer(vector, vector)
    if np.linalg.matrix_rank(matrix) < 3:
      raise LateralMechanism(name)
    dx, dy, twist = np.linalg.solve(matrix, total_shear)
    rotation = twist / radius
    ratios = [hypot(dx - rotation * (y - center_y), dy + rotation * (x - center_x)) / height for x, y in corners]
    forces, resolved = {}, np.zeros(3)
    for key, bay in bays.items():
      local_shear = float(stiffness[key] * (direction[key] @ (dx, dy, twist)))
      resolved += local_shear * direction[key]
      forces[key] = {
        "shear_kn": local_shear,
        "tension_kn": abs(local_shear) * lengths[key] / bay.span_m,
        "compression_at_end_kn": local_shear * height / bay.span_m,
        "diagonal_length_m": lengths[key],
      }
    storeys[name] = {
      "bottom_m": lower, "top_m": upper,
      "translation_m": (float(dx), float(dy)), "rotation_rad": float(rotation),
      "max_corner_drift_ratio": float(max(ratios)),
      "shear_kn": (float(total_shear[0]), float(total_shear[1])),
      "torsion_kn_m": float(total_shear[2] * radius),
      "equilibrium_residual": {
        "force_kn": [float(value) for value in (resolved - total_shear)[:2]],
        "torsion_kn_m": float((resolved - total_shear)[2] * radius),
      },
      "bays": forces,
    }
  return {name: storeys[name] for name, _ in levels}
