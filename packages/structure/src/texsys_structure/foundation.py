"""Compression-only contact and sectional demands for rigid rectangular footings."""

from dataclasses import dataclass
from itertools import pairwise
from math import isfinite

import numpy as np


def _rectangle(half_x, half_y):
  return [(-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y)]


def _clip(polygon, plane):
  """Keep the part on the nonnegative side of a planar linear field."""
  if not polygon:
    return []
  a, b, c = plane
  values = [a + b * x + c * y for x, y in polygon]
  result = []
  for i, q in enumerate(polygon):
    p, f, g = polygon[i - 1], values[i - 1], values[i]
    if (f < 0 < g) or (g < 0 < f):
      ratio = f / (f - g)
      result.append(tuple(x + ratio * (y - x) for x, y in zip(p, q, strict=True)))
    if g >= 0:
      result.append(q)
  return result


def _gram(polygon):
  """Integrate the outer product of (1, x, y) over a convex polygon."""
  matrix = np.zeros((3, 3))
  if len(polygon) < 3:
    return matrix
  p = np.array(polygon[0])
  for qa, ra in pairwise(polygon[1:]):
    q, r = np.array(qa), np.array(ra)
    u, v = q - p, r - p
    weight = abs(u[0] * v[1] - u[1] * v[0]) / 6
    for centre in (p, q, r):
      x, y = (p + q + r + 3 * centre) / 6
      basis = (1, x, y)
      matrix += weight * np.outer(basis, basis)
  return matrix


@dataclass(frozen=True)
class FootingContact:
  """Pressure q = max(0, a + bx*x + by*y), with x/y in metres."""

  state: str
  size_m: tuple[float, float]
  pressure: tuple[float, float, float]
  polygon_m: tuple[tuple[float, float], ...]

  @property
  def peak_kpa(self):
    a, b, c = self.pressure
    return max((a + b * x + c * y for x, y in self.polygon_m), default=0.)

  @property
  def area_m2(self):
    return float(_gram(self.polygon_m)[0, 0])

  def resultants(self, cut=None):
    """Return compressive force and its first moments (N, Nx, Ny)."""
    polygon = _clip(self.polygon_m, cut) if cut is not None else self.polygon_m
    return tuple(float(value) for value in _gram(polygon) @ self.pressure)


def _edge_contact(ex, ey):
  """Closed form for triangular or trapezoidal contact near a footing edge."""
  offsets = (abs(ex), abs(ey))
  major = int(offsets[1] > offsets[0])
  along, across = offsets[major], offsets[1 - major]
  signs = (1 if ex >= 0 else -1, 1 if ey >= 0 else -1)
  if across >= .5:
    a, b = (4 * (1 - value) for value in offsets)
    return 24 / (a * b) * np.array((1 - 1 / a - 1 / b, signs[0] / a, signs[1] / b))

  # Along the short loaded dimension, the triangular strip resultant is
  # proportional to the square of the peak pressure at that transverse station.
  low, high = 0., 1.
  for _ in range(45):
    taper = (low + high) / 2
    square = 1 - taper + taper ** 2 / 3
    centroid = 2 * (.5 - 2 * taper / 3 + taper ** 2 / 4) / square
    if centroid > 1 - across:
      low = taper
    else:
      high = taper
  cubic = 1 - 1.5 * taper + taper ** 2 - taper ** 3 / 4
  width = 3 * (1 - along) * square / cubic
  if width > 2:
    return None
  slopes = [0., 0.]
  slopes[major] = signs[major] / width
  slopes[1 - major] = signs[1 - major] * taper / 2
  return 4 / (width * square) * np.array((1 - 1 / width - taper / 2, *slopes))


def footing_contact(size_m, normal_kn, first_moments_kn_m):
  """Fit a rigid plane to a compression-only Winkler bed.

  First moments are integrals of pressure times x and y. This avoids mixing
  sign conventions for moments about the global axes with pressure resultants.
  """
  bx, by = size_m
  if (not all(map(isfinite, (bx, by, normal_kn, *first_moments_kn_m)))
      or min(bx, by) <= 0 or len(first_moments_kn_m) != 2):
    raise ValueError("Footing contact requires positive dimensions and finite actions.")
  if normal_kn <= 0:
    return FootingContact("detached", size_m, (0., 0., 0.), ())
  hx, hy = bx / 2, by / 2
  mx, my = first_moments_kn_m
  ex, ey = mx / (normal_kn * hx), my / (normal_kn * hy)
  if max(abs(ex), abs(ey)) >= 1 - 1e-9:
    return FootingContact("overturned", size_m, (0., 0., 0.), ())

  # Scale to a square and unit average pressure to condition the contact fit.
  target = np.array((4., 4 * ex, 4 * ey))
  plane = np.array((1., 3 * ex, 3 * ey))
  square = _rectangle(1., 1.)
  polygon = _clip(square, plane)
  if 3 * (abs(ex) + abs(ey)) <= 1:
    state = "full"
  else:
    state = "partial"
    edge_plane = _edge_contact(ex, ey)
    if edge_plane is not None:
      plane, polygon = edge_plane, _clip(square, edge_plane)
    else:
      plane, polygon = _solve_contact(square, target, plane, polygon)
  average = normal_kn / (bx * by)
  pressure = tuple(float(value) for value in plane * (average, average / hx, average / hy))
  return FootingContact(state, size_m, pressure, tuple((x * hx, y * hy) for x, y in polygon))


def _solve_contact(square, target, plane, polygon):
  for _ in range(60):
    matrix = _gram(polygon)
    residual = matrix @ plane - target
    error = np.linalg.norm(residual)
    if error <= 1e-10:
      return plane, polygon
    step = np.linalg.solve(matrix, residual)
    for power in range(25):
      candidate = plane - step / (2 ** power)
      region = _clip(square, candidate)
      if np.linalg.norm(_gram(region) @ candidate - target) < error:
        plane, polygon = candidate, region
        break
    else:
      raise ValueError("Footing contact iteration stalled.")
  raise ValueError("Footing contact did not converge.")


def footing_sections(contact, plate_m, effective_depth_m, dead_pressure_kpa, face_actions=()):
  """Integrate net upward loads outside flexure, one-way and punching cuts.

  Face actions are (x, y, downward force, two pressure first moments).
  """
  bx, by = contact.size_m
  px, py = plate_m
  d = effective_depth_m
  if (contact.state not in ("full", "partial") or not all(map(isfinite, (px, py, d, dead_pressure_kpa)))
      or min(px, py, d) <= 0 or min(bx - px, by - py) <= 2 * d or dead_pressure_kpa < 0):
    raise ValueError("Footing sections require stable contact and critical cuts within the footing.")
  gross = _rectangle(bx / 2, by / 2)

  def net(cut):
    geometric = _gram(_clip(gross, cut))[:, 0]
    result = np.array(contact.resultants(cut)) - dead_pressure_kpa * geometric
    a, b, c = cut
    for x, y, force, mx, my in face_actions:
      if a + b * x + c * y >= 0:
        result -= (force, force * x + mx, force * y + my)
    return result

  results = {}
  for axis, (plate, width) in enumerate(((px, by), (py, bx))):
    for direction, label in ((-1, "-"), (1, "+")):
      normal = (direction, 0) if axis == 0 else (0, direction)
      force, *moments = net((-plate / 2, *normal))
      moment = direction * moments[axis] - plate / 2 * force
      shear, *_ = net((-plate / 2 - d, *normal))
      results[f"{'xy'[axis]}{label}"] = {
        "flexure_kn_m_per_m": float(moment / width),
        "one_way_shear_kn_per_m": float(shear / width),
      }

  # Subtract the inside rectangle once so outside corners are not double-counted.
  inside_soil = contact.polygon_m
  for plane in ((px / 2 + d / 2, 1, 0), (px / 2 + d / 2, -1, 0),
                (py / 2 + d / 2, 0, 1), (py / 2 + d / 2, 0, -1)):
    inside_soil = _clip(inside_soil, plane)
  inside_area = (px + d) * (py + d)
  outside_force = contact.resultants()[0] - float(_gram(inside_soil)[0] @ contact.pressure)
  punching = outside_force - dead_pressure_kpa * (bx * by - inside_area)
  punching -= sum(force for x, y, force, _, _ in face_actions if abs(x) > (px + d) / 2 or abs(y) > (py + d) / 2)
  return {
    "edges": results,
    "punching_perimeter_m": 2 * (px + py + 2 * d),
    "punching_kn": punching,
  }
