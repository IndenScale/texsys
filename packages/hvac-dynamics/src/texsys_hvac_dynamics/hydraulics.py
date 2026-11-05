"""Simultaneous quasi-steady hydraulic solve for incompressible lumped networks.

Positive branch flow follows each branch's reference orientation, not an imposed
physical direction. Independent pressure islands use separate gauge references.
"""

from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import least_squares

from .domain import Branch, PhysicalModel, PhysicsInputError
from .scenario import Scenario


class HydraulicFailure(ValueError):
  pass


@dataclass(frozen=True)
class HydraulicSolution:
  volume_flows_m3_s: tuple[float, ...]
  pressures_pa: dict[str, float]
  blocked_branches: tuple[str, ...]
  mass_residual_kg_s: float
  pressure_residual_pa: float
  gauge_references: tuple[str, ...]


def pipe_loss(flow: float, *, diameter: float, length: float, roughness: float,
              minor_loss: float, density: float, viscosity: float) -> float:
  """Darcy-Weisbach; laminar exact limit and Haaland turbulent approximation."""
  area = math.pi * diameter**2 / 4
  reynolds = density * abs(flow) * diameter / (viscosity * area)
  laminar = 128 * viscosity * length * flow / (math.pi * diameter**4)
  if reynolds <= 2300:
    friction = laminar
  else:
    factor = (-1.8 * math.log10((roughness / (3.7 * diameter))**1.11 + 6.9 / reynolds))**-2
    turbulent = factor * length / diameter * density * flow * abs(flow) / (2 * area**2)
    mix = min(1.0, (reynolds - 2300) / 1700)
    friction = laminar * (1-mix) + turbulent * mix
  return friction + minor_loss * density * flow * abs(flow) / (2 * area**2)


class HydraulicNetwork:
  def __init__(self, model: PhysicalModel):
    self.model = model
    self.components = model.by_id
    self.ends = [(model.port_junctions[b.inlet], model.port_junctions[b.outlet]) for b in model.branches]
    # Validate every required constitutive input before any numerical iteration.
    for branch in model.branches:
      self._loss(branch, 0.001, 1.0)
      component = self.components[branch.component]
      component.number("elevation_change_m", "m")
      if component.kind == "pump":
        component.number("shutoff_head_m", "m", positive=True)
        component.number("curve_m_per_m6_s2", "s2/m5", positive=True)
        check = component.number("check_valve", "1", nonnegative=True)
        if check not in {0, 1}:
          raise PhysicsInputError(f"{component.identity}.check_valve must be 0 or 1.")

  def _loss(self, branch, flow, opening):
    component = self.components[branch.component]
    if component.kind == "pipe":
      return pipe_loss(flow, diameter=component.number("diameter_m", "m", positive=True),
                       length=component.number("length_m", "m", positive=True),
                       roughness=component.number("roughness_m", "m", nonnegative=True),
                       minor_loss=component.number("minor_loss_coefficient", "1", nonnegative=True),
                       density=self.model.density_kg_m3, viscosity=self.model.viscosity_pa_s)
    if component.kind == "valve":
      kv = component.number("kv_m3_h", "m3/h", positive=True)
      if opening <= 0:
        return 0.0  # A closed valve is enforced as an exact zero-flow constraint.
      return 1e5 * self.model.density_kg_m3 / 1000 * (3600 / kv / opening)**2 * flow * abs(flow)
    prefix = branch.circuit + "_" if component.kind == "chiller" else ""
    nominal_loss = component.number(prefix + "nominal_pressure_drop_pa", "Pa", nonnegative=True)
    nominal_flow = component.number(prefix + "nominal_flow_m3_s", "m3/s", positive=True)
    return nominal_loss * flow * abs(flow) / nominal_flow**2

  def _head(self, branch, flow, speed):
    component = self.components[branch.component]
    if component.kind != "pump":
      return 0.0
    head = component.number("shutoff_head_m", "m", positive=True) * speed**2
    head -= component.number("curve_m_per_m6_s2", "s2/m5", positive=True) * flow * abs(flow)
    return self.model.density_kg_m3 * self.model.gravity_m_s2 * head

  def solve(self, scenario: Scenario, time: float) -> HydraulicSolution:
    components, branches = self.components, self.model.branches
    blocked = {i for i, b in enumerate(branches) if components[b.component].kind == "valve" and scenario.value(b.component, "opening", time) == 0}
    speed = {c.identity: scenario.value(c.identity, "speed_ratio", time) for c in components.values() if c.kind == "pump"}
    opening = {c.identity: scenario.value(c.identity, "opening", time) for c in components.values() if c.kind == "valve"}
    last = None
    # Active-set check-valve handling: a reverse solution closes that branch and
    # re-solves all pressure islands. No per-device sequential state updates.
    for _ in range(len(branches) + 1):
      last = self._solve_active(scenario, speed, opening, blocked)
      additions = {i for i, (b, q) in enumerate(zip(branches, last.volume_flows_m3_s))
                   if i not in blocked and components[b.component].kind == "pump"
                   and components[b.component].number("check_valve", "1") == 1 and q < -1e-10}
      if not additions:
        return last
      blocked |= additions
    raise HydraulicFailure("Check-valve active set did not converge.")

  def _solve_active(self, scenario, speed, opening, blocked):
    branches, density = self.model.branches, self.model.density_kg_m3
    adjacency = {node: set() for node in self.model.junctions}
    active = [i for i in range(len(branches)) if i not in blocked]
    for i in active:
      a, b = self.ends[i]
      adjacency[a].add(b)
      adjacency[b].add(a)
    references, seen, island = [], set(), {}
    for node in sorted(adjacency):
      if node in seen:
        continue
      references.append(node)
      pending = [node]
      while pending:
        current = pending.pop()
        if current not in seen:
          seen.add(current)
          island[current] = node
          pending.extend(sorted(adjacency[current] - seen))
    free = [node for node in self.model.junctions if node not in references]
    node_index = {node: i for i, node in enumerate(free)}
    n_pressure = len(free)
    pressure_scale, flow_scale = 1e5, 0.005

    def unpack(vector):
      pressures = {node: 0.0 for node in references}
      pressures.update({node: float(vector[i]) * pressure_scale for node, i in node_index.items()})
      flows = np.zeros(len(branches))
      for index, branch_index in enumerate(active):
        flows[branch_index] = vector[n_pressure + index] * flow_scale
      return pressures, flows

    def raw_residuals(pressures, flows):
      continuity = {node: 0.0 for node in self.model.junctions}
      losses = []
      for i in active:
        branch, flow = branches[i], flows[i]
        a, b = self.ends[i]
        continuity[a] -= flow
        continuity[b] += flow
        component = self.components[branch.component]
        elevation = density * self.model.gravity_m_s2 * component.number("elevation_change_m", "m")
        loss = self._loss(branch, flow, opening.get(branch.component, 1.0))
        gain = self._head(branch, flow, speed.get(branch.component, 0.0))
        losses.append(pressures[a] - pressures[b] + gain - loss - elevation)
      return continuity, losses

    def residual(vector):
      pressures, flows = unpack(vector)
      continuity, losses = raw_residuals(pressures, flows)
      return np.array([continuity[node] / flow_scale for node in free] + [value / pressure_scale for value in losses])

    count = n_pressure + len(active)
    if count:
      initial = np.zeros(count)
      powered = {island[self.ends[i][0]] for i in active if self.components[branches[i].component].kind == "pump" and speed[branches[i].component] > 0}
      initial[n_pressure:] = [0.5 if island[self.ends[i][0]] in powered else 0.0 for i in active]
      solution = least_squares(residual, initial, xtol=1e-12, ftol=1e-12, gtol=1e-12,
                               max_nfev=400, diff_step=1e-5)
      pressures, flows = unpack(solution.x)
      if not solution.success or not np.all(np.isfinite(solution.x)):
        raise HydraulicFailure(f"Nonlinear hydraulic solve failed: {solution.message}")
    else:
      pressures, flows = unpack(np.zeros(0))
    continuity, losses = raw_residuals(pressures, flows)
    mass_residual = max((abs(value) * density for value in continuity.values()), default=0.0)
    pressure_residual = max((abs(value) for value in losses), default=0.0)
    if mass_residual > scenario.solver["hydraulic_mass_tolerance_kg_s"] or pressure_residual > scenario.solver["hydraulic_pressure_tolerance_pa"]:
      raise HydraulicFailure(f"Hydraulic residuals exceed tolerance: mass={mass_residual:g} kg/s, pressure={pressure_residual:g} Pa.")
    return HydraulicSolution(tuple(float(q) for q in flows), pressures,
                              tuple(branches[i].identity for i in sorted(blocked)), mass_residual, pressure_residual, tuple(references))
