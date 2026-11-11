"""Timed transfer and energy budgets for directed power distribution."""

from dataclasses import dataclass
import math

from . import PowerResult, evaluate_power


def _finite(value, label, *, minimum=0, inclusive=False):
  if (type(value) not in {int, float} or not math.isfinite(value)
      or value < minimum or (not inclusive and value == minimum)):
    raise ValueError(f"Invalid {label}: {value!r}.")


@dataclass(frozen=True)
class PowerRating:
  kw: float
  kva: float

  def __post_init__(self):
    _finite(self.kw, "active power rating")
    _finite(self.kva, "apparent power rating")
    if self.kw > self.kva:
      raise ValueError("An active power rating cannot exceed its apparent power rating.")


@dataclass(frozen=True)
class ACLoad:
  kw: float
  power_factor: float

  def __post_init__(self):
    _finite(self.kw, "active load")
    _finite(self.power_factor, "lagging power factor")
    if self.power_factor > 1:
      raise ValueError("Lagging power factor must be at most 1.")

  @property
  def kvar(self) -> float:
    return self.kw * math.sqrt(1 - self.power_factor ** 2) / self.power_factor


@dataclass(frozen=True)
class SwitchState:
  at_s: float
  unavailable: frozenset[str]


@dataclass(frozen=True)
class PowerInterval:
  start_s: float
  end_s: float
  unavailable: tuple[str, ...]
  service: PowerResult
  flow_kva: dict[str, float]
  overloaded_kva: dict[str, float]
  remaining_kwh: dict[str, float]


class StandbyPower:
  """Resolve ordered feeds and dispatch finite energy only while it serves load."""

  def __init__(
    self, links: tuple[tuple[str, str], ...], sources: set[str], ratings: dict[str, PowerRating],
    loads: dict[str, ACLoad], usable_kwh: dict[str, float],
  ):
    if usable_kwh.keys() - sources:
      raise ValueError("Stored energy must belong to declared power sources.")
    for value in usable_kwh.values():
      _finite(value, "usable energy")
    self.links = links
    self.sources = sources.copy()
    self.ratings = ratings.copy()
    self.capacities_kw = {name: rating.kw for name, rating in ratings.items()}
    self.demand_kw = {name: load.kw for name, load in loads.items()}
    self.demand_kvar = {name: load.kvar for name, load in loads.items()}
    self.usable_kwh = dict(sorted(usable_kwh.items()))
    evaluate_power(links, sources, self.capacities_kw, self.demand_kw, set())

  def simulate(self, duration_s: float, states: tuple[SwitchState, ...]) -> tuple[PowerInterval, ...]:
    _finite(duration_s, "study duration")
    if not states or states[0].at_s != 0:
      raise ValueError("Power transfer must declare the switching state at t=0.")
    for state in states:
      _finite(state.at_s, "switch time", inclusive=True)
    ends = [state.at_s for state in states[1:]] + [duration_s]
    remaining = self.usable_kwh.copy()
    timeline = []
    for state, end in zip(states, ends, strict=True):
      if end <= state.at_s or end > duration_s:
        raise ValueError("Switch times must increase strictly within the study duration.")
      start = state.at_s
      while start < end:
        off = set(state.unavailable) | {name for name, energy in remaining.items() if energy <= 0}
        service = evaluate_power(self.links, self.sources, self.capacities_kw, self.demand_kw, off)
        reactive = dict.fromkeys(self.ratings, 0.0)
        for load, path in service.paths.items():
          for node in path[:-1]:
            reactive[node] += self.demand_kvar[load]
        apparent = {node: math.hypot(kw, reactive[node]) for node, kw in service.flow_kw.items()}
        overloaded = {node: kva - self.ratings[node].kva for node, kva in apparent.items()
                      if kva > self.ratings[node].kva + 1e-8}
        exhausted_at = {name: start + (energy / service.flow_kw[name]) * 3600
                        for name, energy in remaining.items() if service.flow_kw[name] > 0}
        stop = min(end, *exhausted_at.values()) if exhausted_at else end
        for name, exhausted in exhausted_at.items():
          remaining[name] = (0.0 if exhausted <= stop
                             else max(0.0, remaining[name] - service.flow_kw[name] * (stop - start) / 3600))
        if stop > start:
          timeline.append(PowerInterval(start, stop, tuple(sorted(off)), service, apparent, overloaded,
                                        remaining.copy()))
        start = stop
    return tuple(timeline)
