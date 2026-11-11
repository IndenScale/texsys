"""A paired radial terminal distribution with controlled equal branch flows."""

from functools import lru_cache
from math import isfinite

from .facility_hydraulics import PipeHydraulics
from .facility_water import FacilityWaterNetwork
from .hydraulics import pipe_loss


class FacilityDistribution:
  """Evaluate the farthest served outlet and pipe limits at a header flow."""

  def __init__(self, definition, pipes, density, viscosity):
    if any(type(value) not in (int, float) or not isfinite(value) or value <= 0 for value in (density, viscosity)):
      raise ValueError("Floor distribution fluid properties must be finite and positive.")
    network = FacilityWaterNetwork({"floor": definition})
    if not network.paths or network.segments.keys() != pipes.keys():
      raise ValueError("A floor distribution needs paired outlet paths and hydraulic data for every pipe.")
    self.pipes = {name: PipeHydraulics(**pipe) for name, pipe in pipes.items()}
    self.paths = {unit: path for (_, unit), path in network.paths.items()}
    self.pipe_capacity_m3_s = {
      name: min(pipe.area_m2 * pipe.max_velocity_m_s, network.segments[name].capacity_kg_s / density)
      for name, pipe in self.pipes.items()
    }
    count = len(self.paths)
    self.fractions = {name: sum(name in path for path in self.paths.values()) / count for name in pipes}
    if min(self.fractions.values()) <= 0:
      raise ValueError("Every floor pipe must serve at least one controlled outlet.")
    self.max_flow_m3_s = min(limit / self.fractions[name] for name, limit in self.pipe_capacity_m3_s.items())
    self.density, self.viscosity = density, viscosity
    self._critical = lru_cache(maxsize=512)(self._critical_uncached)

  def _losses(self, flows):
    return {name: pipe_loss(flows[name], diameter=pipe.diameter_m, length=pipe.length_m,
                            roughness=pipe.roughness_m, minor_loss=pipe.minor_loss_coefficient,
                            density=self.density, viscosity=self.viscosity)
            for name, pipe in self.pipes.items()}

  def _critical_uncached(self, flow):
    if flow == 0:
      return 0.
    losses = self._losses({name: flow * fraction for name, fraction in self.fractions.items()})
    return max(sum(losses[name] for name in path) for path in self.paths.values())

  def pressure_pa(self, flow):
    return self._critical(float(flow))

  def group_coefficients(self, units):
    return {name: sum(name in self.paths[unit] for unit in units) / len(units) for name in self.pipes}

  def outlet_state(self, flows):
    """Recover simultaneous losses when outlets on a header carry different flows."""
    pipe_flows = dict.fromkeys(self.pipes, 0.)
    for unit, flow in flows.items():
      for name in self.paths[unit]:
        pipe_flows[name] += flow
    losses = self._losses(pipe_flows)
    return {unit: sum(losses[name] for name in path) for unit, path in self.paths.items()}, pipe_flows

  def report(self, flow, *, outlet_flows=None, critical_unit=None):
    flows = dict.fromkeys(self.paths, flow / len(self.paths)) if outlet_flows is None else outlet_flows
    totals, pipe_flows = self.outlet_state(flows)
    live = list(flows) if outlet_flows is None else [unit for unit, amount in flows.items() if amount > 1e-12]
    critical = critical_unit or (max(live, key=totals.__getitem__) if live else None)
    return {"critical_unit": critical, "critical_loss_kpa": totals[critical] / 1000 if critical else 0.,
            "unit_loss_kpa": {unit: totals[unit] / 1000 for unit in live},
            "unit_flow_m3_s": flow / len(self.paths) if outlet_flows is None else None,
            "flow_m3_s_by_unit": flows, "max_header_flow_m3_s": self.max_flow_m3_s,
            "segment_flow_m3_s": pipe_flows}
