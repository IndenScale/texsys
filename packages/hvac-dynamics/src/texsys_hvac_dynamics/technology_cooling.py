"""Controlled CDU circuits with pipe and rack component pressure losses."""

from dataclasses import dataclass
from math import isfinite

from .facility_hydraulics import FacilityWaterHydraulics
from .facility_water import FacilityWaterNetwork


@dataclass(frozen=True)
class RackCircuit:
  rated_flow_kg_s: float
  maximum_flow_kg_s: float
  losses_kpa: dict[str, float]
  maximum_inlet_c: float
  maximum_return_c: float

  def __post_init__(self):
    if (not all(isfinite(value) for value in (self.rated_flow_kg_s, self.maximum_flow_kg_s,
                                              self.maximum_inlet_c, self.maximum_return_c))
        or not 0 < self.rated_flow_kg_s <= self.maximum_flow_kg_s
        or self.maximum_return_c <= self.maximum_inlet_c or not self.losses_kpa
        or any(not isfinite(value) or value < 0 for value in self.losses_kpa.values())):
      raise ValueError("Rack circuit needs a valid flow range, temperature envelope and rated losses.")

  def losses(self, flow_kg_s):
    return {name: drop * (flow_kg_s / self.rated_flow_kg_s)**2 for name, drop in self.losses_kpa.items()}


class TechnologyCooling:
  """Allocate liquid heat across independent CDU paths to a shared rack load."""

  def __init__(self, trains, pipes, banks, loops, racks, *, density, viscosity):
    self.network = FacilityWaterNetwork(trains)
    if (not racks or trains.keys() != banks.keys() or trains.keys() != loops.keys()
        or self.network.paths.keys() != {(train, rack) for train in trains for rack in racks}):
      raise ValueError("Each TCS train must connect to all declared rack circuits.")
    self.loops, self.racks, self.density = loops, racks, density
    self.heat_per_flow = {train: loop.specific_heat_kj_kgk * (loop.return_c - loop.supply_c)
                          for train, loop in loops.items()}
    self.terminal_kw = {}
    hydraulic_banks = {}
    for train, loop in loops.items():
      self.terminal_kw[train] = {
        rack: circuit.maximum_flow_kg_s * self.heat_per_flow[train]
        if loop.supply_c <= circuit.maximum_inlet_c and loop.return_c <= circuit.maximum_return_c else 0.
        for rack, circuit in racks.items()
      }
      hydraulic_banks[train] = {
        **banks[train],
        "terminal_losses": {
          rack: {"rated_flow_m3_s": circuit.rated_flow_kg_s / density,
                 "rated_drop_kpa": sum(circuit.losses_kpa.values())}
          for rack, circuit in racks.items()
        },
      }
    self.hydraulics = FacilityWaterHydraulics(self.network, {
      "density_kg_m3": density, "viscosity_pa_s": viscosity, "pipes": pipes, "banks": hydraulic_banks,
    })

  def evaluate(self, demand, source_kw, unavailable=(), *, unavailable_pumps=(), pump_speed_limits=None):
    # FWS capacity caps the heat removed across each CDU. The TCS curve and routes
    # then determine how much of that capacity is deliverable at each rack.
    limits = {train: min(source_kw[train], loop.heat_kw) for train, loop in self.loops.items()}
    result = self.network.allocate(demand, limits, self.terminal_kw, self.heat_per_flow, set(unavailable),
                                   hydraulics=self.hydraulics, unavailable_pumps=set(unavailable_pumps),
                                   pump_speed_limits=pump_speed_limits)
    return self.with_circuit_details(result)

  def with_circuit_details(self, result):
    circuits = {}
    for train, racks in result["delivered_kw_by_path"].items():
      loop = self.loops[train]
      for rack, heat in racks.items():
        flow = heat / self.heat_per_flow[train]
        if flow <= 1e-9:
          continue
        loss = self.racks[rack].losses(flow)
        circuits.setdefault(rack, {})[train] = {
          "delivered_kw": heat, "flow_kg_s": flow, "supply_c": loop.supply_c,
          "return_c": loop.supply_c + heat / (flow * loop.specific_heat_kj_kgk),
          "component_loss_kpa": loss,
        }
    result["circuits_by_rack"] = circuits
    return result
