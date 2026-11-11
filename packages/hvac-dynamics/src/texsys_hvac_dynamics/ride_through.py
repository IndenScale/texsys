"""Coupled rack, room air and technology coolant thermal ride-through."""

from dataclasses import dataclass
import math

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq


NODES = ("rack_solid", "cold_zone_air", "tcs_bulk")


def _finite(value, *, positive=False):
  return type(value) in {int, float} and math.isfinite(value) and (not positive or value > 0)


@dataclass(frozen=True)
class ThermalNode:
  capacity_kj_k: float
  initial_c: float
  max_c: float

  def __post_init__(self):
    if (not _finite(self.capacity_kj_k, positive=True) or not all(map(_finite, (self.initial_c, self.max_c)))
        or self.initial_c >= self.max_c):
      raise ValueError("Thermal nodes need positive capacity and an initial temperature below their limit.")


@dataclass(frozen=True)
class ThermalInterval:
  start_s: float
  end_s: float
  it_kw: float
  air_ceiling_kw: tuple[float, float]
  liquid_ceiling_kw: tuple[float, float]
  circulation: tuple[float, float]

  def __post_init__(self):
    if (not all(_finite(x) and x >= 0 for x in (self.start_s, self.end_s, self.it_kw))
        or self.start_s >= self.end_s):
      raise ValueError("Thermal intervals need increasing time and nonnegative IT heat.")
    for pair in (self.air_ceiling_kw, self.liquid_ceiling_kw, self.circulation):
      if len(pair) != 2 or not all(_finite(x) and x >= 0 for x in pair):
        raise ValueError("Thermal ramp endpoints must be finite and nonnegative.")
    if max(self.circulation) > 1:
      raise ValueError("TCS circulation fraction must not exceed one.")


class RoomRideThrough:
  def __init__(
    self, nodes: dict[str, ThermalNode], *, air_it_kw: float, liquid_it_kw: float, other_air_kw: float,
    air_control_kw_k: float, liquid_control_kw_k: float, stagnant_transfer_fraction: float,
  ):
    if nodes.keys() != set(NODES):
      raise ValueError("Ride-through needs rack solid, cold zone air and TCS bulk nodes.")
    if not all(_finite(x, positive=True) for x in (air_it_kw, liquid_it_kw, air_control_kw_k, liquid_control_kw_k)):
      raise ValueError("Nominal transfer and controller gains must be positive and finite.")
    if (not _finite(other_air_kw) or other_air_kw < 0 or not _finite(stagnant_transfer_fraction)
        or not 0 <= stagnant_transfer_fraction <= 1):
      raise ValueError("Invalid room parasitic heat or stagnant TCS transfer fraction.")
    self.nodes = tuple(nodes[name] for name in NODES)
    rack, air, liquid = (node.initial_c for node in self.nodes)
    if rack <= max(air, liquid):
      raise ValueError("The nominal rack solid must be hotter than both receiving media.")
    self.air_conductance = air_it_kw / (rack - air)
    self.liquid_conductance = liquid_it_kw / (rack - liquid)
    self.other = other_air_kw
    self.sink_nominal = (air_it_kw + other_air_kw, liquid_it_kw)
    self.gains = (air_control_kw_k, liquid_control_kw_k)
    self.stagnant = stagnant_transfer_fraction

  def _rate(self, interval, time, state):
    fraction = (time - interval.start_s) / (interval.end_s - interval.start_s)
    air_max, liquid_max, circulation = (a + (b - a) * fraction for a, b in
                                      (interval.air_ceiling_kw, interval.liquid_ceiling_kw, interval.circulation))
    rack, air, liquid = state[:3]
    to_air = self.air_conductance * (rack - air)
    to_liquid = self.liquid_conductance * (self.stagnant + (1 - self.stagnant) * circulation) * (rack - liquid)
    air_removed, liquid_removed = (
      min(ceiling, max(0, base + gain * (temperature - node.initial_c)))
      for ceiling, base, gain, temperature, node in zip((air_max, liquid_max), self.sink_nominal, self.gains,
                                                       (air, liquid), self.nodes[1:], strict=True)
    )
    net = (interval.it_kw - to_air - to_liquid, to_air + self.other - air_removed, to_liquid - liquid_removed)
    return np.array([*(q / node.capacity_kj_k for q, node in zip(net, self.nodes, strict=True)),
                     air_removed, liquid_removed])

  def simulate(self, intervals: tuple[ThermalInterval, ...], max_step_s: float) -> dict:
    if (not intervals or intervals[0].start_s != 0 or not _finite(max_step_s, positive=True)
        or any(a.end_s != b.start_s for a, b in zip(intervals, intervals[1:]))):
      raise ValueError("Thermal intervals must be contiguous from zero, with a positive solver step limit.")
    initial = np.array([*(node.initial_c for node in self.nodes), 0., 0.])
    state = initial.copy()
    summary = {name: {"max_c": node.max_c, "peak_c": node.initial_c, "peak_at_s": 0.,
                      "first_above_s": None, "seconds_above": 0.,
                      "recovered_at_s": None} for name, node in zip(NODES, self.nodes, strict=True)}
    last_above_end = {}
    samples = [{"time_s": 0., "temperature_c": dict(zip(NODES, map(float, initial[:3]), strict=True))}]
    input_kj = 0.
    for interval in intervals:
      def rate(t, y):
        return self._rate(interval, t, y)

      def slope_event(index):
        return lambda t, y: rate(t, y)[index]

      solution = solve_ivp(rate, (interval.start_s, interval.end_s), state, method="DOP853",
                           rtol=1e-8, atol=1e-8, max_step=max_step_s, dense_output=True,
                           events=[slope_event(i) for i in range(3)])
      if not solution.success or not np.all(np.isfinite(solution.y)):
        raise ValueError(f"Thermal ride-through did not converge: {solution.message}")
      state = solution.y[:, -1]
      input_kj += (interval.it_kw + self.other) * (interval.end_s - interval.start_s)
      sample_times = set(solution.t[1:])
      for i, name in enumerate(NODES):
        result = summary[name]
        stations = sorted({*solution.t, *solution.t_events[i]})
        values = [float(solution.sol(time)[i]) for time in stations]
        limit = self.nodes[i].max_c
        crossings = set()
        for time, value in zip(stations, values, strict=True):
          if value > result["peak_c"]:
            result.update(peak_c=value, peak_at_s=float(time))
          if value == limit:
            crossings.add(time)
        for index, (a, b) in enumerate(zip(stations, stations[1:])):
          if (values[index] - limit) * (values[index + 1] - limit) < 0:
            crossings.add(brentq(lambda t: solution.sol(t)[i] - limit, a, b))
        sample_times.update(t for t in crossings if t > interval.start_s)
        cuts = sorted({interval.start_s, interval.end_s, *crossings})
        for start, end in zip(cuts, cuts[1:]):
          if solution.sol((start + end) / 2)[i] > limit:
            if result["first_above_s"] is None:
              result["first_above_s"] = float(start)
            result["seconds_above"] += float(end - start)
            last_above_end[name] = float(end)
      for time in sorted(sample_times):
        samples.append({"time_s": float(time), "temperature_c": dict(zip(NODES, map(float, solution.sol(time)[:3]),
                                                                        strict=True))})
    stored_kj = sum(float(state[i] - initial[i]) * node.capacity_kj_k for i, node in enumerate(self.nodes))
    removed_kj = float(state[3] + state[4])
    residual_kj = input_kj - stored_kj - removed_kj
    if abs(residual_kj) > 1e-6 * max(1., input_kj):
      raise ValueError("Thermal ride-through failed the energy balance.")
    for i, name in enumerate(NODES):
      summary[name]["end_c"] = float(state[i])
      if name in last_above_end and state[i] <= self.nodes[i].max_c:
        summary[name]["recovered_at_s"] = last_above_end[name]
    return {"nodes": summary, "samples": samples, "energy_kj": {
      "heat_input": input_kj, "air_removed": float(state[3]), "liquid_removed": float(state[4]),
      "stored_change": stored_kj, "residual": residual_kj,
    }}
