"""Linear in-plane concrete diaphragm coupled to axial steel framing."""

from dataclasses import dataclass
from itertools import product
from math import hypot, isfinite, sqrt

import numpy as np

from .openings import PlanRect, slab_regions


@dataclass(frozen=True)
class MembranePanel:
  """Counterclockwise rectangular corners from lower left, with plan openings."""

  nodes: tuple[str, str, str, str]
  thickness_m: float
  openings_m: tuple[tuple[float, float, float, float], ...] = ()


@dataclass(frozen=True)
class AxialMember:
  nodes: tuple[str, str]
  axial_rigidity_kn: float


def _dofs(nodes, index, offset=0):
  return np.array([2 * index[node] + offset + axis for node in nodes for axis in (0, 1)])


def _strain_matrix(xi, eta, width, height):
  dx = np.array((eta - 1, 1 - eta, eta + 1, -eta - 1)) / (2 * width)
  dy = np.array((xi - 1, -xi - 1, xi + 1, 1 - xi)) / (2 * height)
  matrix = np.zeros((3, 8))
  matrix[0, ::2], matrix[1, 1::2] = dx, dy
  matrix[2, ::2], matrix[2, 1::2] = dy, dx
  return matrix


def _panel_samples(panel, points, elasticity):
  a, b, c, d = (points[name] for name in panel.nodes)
  x0, y0 = a
  x1, y1 = c
  if (not isfinite(panel.thickness_m) or panel.thickness_m <= 0 or not (x0 < x1 and y0 < y1)
      or tuple(b) != (x1, y0) or tuple(d) != (x0, y1)):
    raise ValueError("Diaphragm panel needs rectangular corners and positive thickness.")
  membrane = elasticity * panel.thickness_m
  quadrature = []
  holes = tuple(PlanRect(*hole) for hole in panel.openings_m)
  for region in slab_regions(PlanRect(x0, y0, x1, y1), holes):
    left, bottom, right, top = region.xyxy
    area_weight = (right - left) * (top - bottom) / 4
    for gx, gy in product((-1 / sqrt(3), 1 / sqrt(3)), repeat=2):
      x = (left + right + gx * (right - left)) / 2
      y = (bottom + top + gy * (top - bottom)) / 2
      matrix = _strain_matrix(2 * (x - x0) / (x1 - x0) - 1, 2 * (y - y0) / (y1 - y0) - 1, x1 - x0, y1 - y0)
      quadrature.append((x, y, area_weight, matrix))
  if not quadrature:
    raise ValueError("An opening removes an entire diaphragm panel.")
  return membrane, quadrature


class Diaphragm:
  """A planar slab and coincident steel nodes joined by in-plane springs.

  Slab uses four-node plane-stress elements. Openings subtract stiffness
  by integration over retained concrete in each element. The interpolation
  remains coarse around hole edges and does not resolve local stress peaks.
  Steel member forces are axial only. The model excludes form-deck stiffness.
  """

  def __init__(self, points_m, panels, members, connections_kn_m, modulus_kn_m2, poisson):
    if (len(points_m) < 4 or not panels or not members or not isfinite(modulus_kn_m2) or modulus_kn_m2 <= 0
        or not 0 <= poisson < .5 or set(connections_kn_m) != set(points_m)
        or any(not isfinite(value) or value <= 0 for value in connections_kn_m.values())
        or any(len(point) != 2 or not all(map(isfinite, point)) for point in points_m.values())
        or len({tuple(point) for point in points_m.values()}) != len(points_m)):
      raise ValueError("Invalid diaphragm geometry, materials or connection stiffness.")
    self.points = dict(points_m)
    self.index = {name: i for i, name in enumerate(self.points)}
    self.offset = 2 * len(self.index)
    self.stiffness = np.zeros((2 * self.offset, 2 * self.offset))
    self.panels, self.members, self.connections = {}, {}, dict(connections_kn_m)

    elasticity = modulus_kn_m2 / (1 - poisson ** 2) * np.array(
      ((1, poisson, 0), (poisson, 1, 0), (0, 0, (1 - poisson) / 2))
    )
    for name, panel in panels.items():
      dofs = _dofs(panel.nodes, self.index)
      membrane, samples = _panel_samples(panel, self.points, elasticity)
      self.stiffness[np.ix_(dofs, dofs)] += sum(w * (b.T @ membrane @ b) for _, _, w, b in samples)
      self.panels[name] = (dofs, membrane, samples)

    for name, member in members.items():
      a, b = (self.points[node] for node in member.nodes)
      delta = np.subtract(b, a)
      length = hypot(*delta)
      if length <= 0 or not isfinite(member.axial_rigidity_kn) or member.axial_rigidity_kn <= 0:
        raise ValueError("Axial member needs distinct endpoints and positive rigidity.")
      direction = delta / length
      transform = np.concatenate((-direction, direction))
      stiffness = member.axial_rigidity_kn / length
      dofs = _dofs(member.nodes, self.index, self.offset)
      self.stiffness[np.ix_(dofs, dofs)] += stiffness * np.outer(transform, transform)
      self.members[name] = (dofs, transform, stiffness)

    for node, value in self.connections.items():
      dofs = _dofs((node,), self.index)
      steel = dofs + self.offset
      self.stiffness[dofs, dofs] += value
      self.stiffness[steel, steel] += value
      self.stiffness[dofs, steel] -= value
      self.stiffness[steel, dofs] -= value

    # Remove rigid translations and rotation through three gauge constraints.
    first = next(iter(self.points))
    a = self.points[first]
    second = max(self.points, key=lambda name: hypot(*(np.subtract(self.points[name], a))))
    dx, dy = np.subtract(self.points[second], a)
    fixed = (2 * self.index[first], 2 * self.index[first] + 1, 2 * self.index[second] + int(abs(dx) >= abs(dy)))
    self.free = np.array([i for i in range(2 * self.offset) if i not in fixed])
    self.reduced = self.stiffness[np.ix_(self.free, self.free)]
    try:
      np.linalg.cholesky(self.reduced)
    except np.linalg.LinAlgError as error:
      raise ValueError("Diaphragm has an internal mechanism.") from error

  def solve(self, slab_loads_kn, steel_loads_kn):
    """Apply self-equilibrating slab and framing loads in common coordinates."""
    loads = np.zeros(2 * self.offset)
    for actions, offset in ((slab_loads_kn, 0), (steel_loads_kn, self.offset)):
      for node, vector in actions.items():
        if len(vector) != 2 or not all(map(isfinite, vector)):
          raise ValueError("Diaphragm loads require finite in-plane vectors.")
        loads[_dofs((node,), self.index, offset)] += vector
    slab = loads[:self.offset].reshape(-1, 2)
    steel = loads[self.offset:].reshape(-1, 2)
    net = slab + steel
    xy = np.array(tuple(self.points.values()), dtype=float)
    xy -= xy.mean(axis=0)
    fx, fy = net.sum(axis=0)
    mz = float((xy[:, 0] * net[:, 1] - xy[:, 1] * net[:, 0]).sum())
    force_scale = max(1., float(np.abs(loads).sum()))
    if max(abs(fx), abs(fy)) > 1e-8 * force_scale or abs(mz) > 1e-8 * force_scale * max(1., float(np.abs(xy).max())):
      raise ValueError("Diaphragm loads do not balance in-plane force and moment.")
    displacement = np.zeros_like(loads)
    displacement[self.free] = np.linalg.solve(self.reduced, loads[self.free])
    residual = self.stiffness @ displacement - loads
    if float(np.abs(residual).max()) > 1e-7 * force_scale:
      raise ValueError("Diaphragm nodal equilibrium did not converge.")

    panel_resultants = {}
    for name, (dofs, membrane, samples) in self.panels.items():
      values = [(membrane @ b @ displacement[dofs], (x, y)) for x, y, _, b in samples]
      governing, location = max(values, key=lambda item: abs(item[0][2]))
      panel_resultants[name] = {
        "net_area_m2": sum(w for _, _, w, _ in samples),
        "max_abs_shear_kn_m": float(abs(governing[2])),
        "shear_at_m": location,
        "max_abs_normal_kn_m": float(max(max(abs(value[0]), abs(value[1])) for value, _ in values)),
      }
    axial = {name: float(stiffness * (transform @ displacement[dofs]))
             for name, (dofs, transform, stiffness) in self.members.items()}
    connection = {}
    for node, stiffness in self.connections.items():
      dofs = _dofs((node,), self.index)
      slip = displacement[dofs] - displacement[dofs + self.offset]
      connection[node] = {"to_steel_kn": (stiffness * slip).tolist(), "slip_m": hypot(*slip)}
    return {
      "max_nodal_residual_kn": float(np.abs(residual).max()),
      "input_force_residual_kn": [float(fx), float(fy)], "input_moment_residual_kn_m": mz,
      "panels": panel_resultants, "axial_kn": axial, "connections": connection,
    }
