"""Rated flow allocation on paired radial facility-water networks."""

from dataclasses import dataclass
from collections import defaultdict
import math
from typing import TYPE_CHECKING

from scipy.optimize import linprog

if TYPE_CHECKING:
  from .facility_hydraulics import FacilityWaterHydraulics


@dataclass(frozen=True)
class WaterSegment:
  id: str
  start: str
  end: str
  leg: str
  capacity_kg_s: float
  riser: str | None = None

  def __post_init__(self):
    if any(not isinstance(name, str) or not name.strip() for name in (self.id, self.start, self.end)):
      raise ValueError("FWS segments need nonempty identifiers.")
    if self.start == self.end or self.leg not in {"supply", "return"}:
      raise ValueError(f"Invalid FWS endpoints or leg for {self.id}.")
    if self.riser is not None and (not isinstance(self.riser, str) or not self.riser.strip()):
      raise ValueError(f"Invalid FWS riser reference for {self.id}.")
    value = self.capacity_kg_s
    if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
      raise ValueError(f"FWS segment {self.id} needs a positive finite flow rating.")


def _radial_paths(root: str, segments: tuple[WaterSegment, ...], leg: str) -> dict[str, tuple[str, ...]]:
  parents = {}
  for segment in segments:
    if segment.leg != leg:
      continue
    start, end = (segment.start, segment.end) if leg == "supply" else (segment.end, segment.start)
    if end == root or end in parents:
      raise ValueError(f"FWS {leg} must be radial from {root}, with one path per node.")
    parents[end] = (start, segment.id)
  paths: dict[str, tuple[str, ...]] = {root: ()}
  pending = dict(parents)
  while pending:
    attached = [node for node, (parent, _) in pending.items() if parent in paths]
    if not attached:
      raise ValueError(f"FWS {leg} has disconnected or cyclic segments at {root}.")
    for node in attached:
      parent, segment = pending.pop(node)
      paths[node] = (*paths[parent], segment)
  return paths


def _outlet_groups(ready, units_by_level, unavailable_outlets, unit_demand, limits):
  """Group outlets with the same demand and reachable trains."""
  variables, membership, demand, ratings = [], {}, {}, {}
  for level, outlets in sorted(units_by_level.items()):
    if not outlets or len(set(outlets)) != len(outlets):
      raise ValueError("Each active floor needs a distinct, nonempty outlet list.")
    groups = defaultdict(list)
    for unit in sorted(outlets):
      reach = tuple(train for train, target in ready if target == level and unit not in unavailable_outlets.get((train, level), ()))
      groups[reach, unit_demand[f"{level}/{unit}"]].append(unit)
    for index, ((trains, required), members) in enumerate(sorted(groups.items())):
      share = len(members) / len(outlets)
      demand[level, index] = required * len(members)
      for train in trains:
        key = (train, level, index)
        variables.append(key)
        membership[key] = tuple(members)
        ratings[key] = limits[train, level] * share
  return variables, membership, demand, ratings


class FacilityWaterNetwork:
  """Allocate heat through explicit supply and return paths under rated flow limits."""

  def __init__(self, trains: dict[str, dict]):
    self.segments: dict[str, WaterSegment] = {}
    self.paths: dict[tuple[str, str], tuple[str, ...]] = {}
    self.train_names = set(trains)
    for train, definition in sorted(trains.items()):
      root = definition["plant_node"]
      segments = tuple(WaterSegment(**segment) for segment in definition["segments"])
      for segment in segments:
        if segment.id in self.segments:
          raise ValueError(f"Duplicate FWS segment: {segment.id}.")
        self.segments[segment.id] = segment
      supply = _radial_paths(root, segments, "supply")
      returns = _radial_paths(root, segments, "return")
      if len(set(definition["terminals"].values())) != len(definition["terminals"]):
        raise ValueError(f"FWS train {train} must use a distinct pipe endpoint for each terminal.")
      for terminal, node in definition["terminals"].items():
        if node == root or node not in supply or node not in returns:
          raise ValueError(f"FWS train {train} has no complete path to {terminal}.")
        self.paths[train, terminal] = supply[node] + returns[node]

  def capacity_constraints(self, variables, source_kw, heat_per_flow):
    constraints, bounds = [], []
    for train, limit in sorted(source_kw.items()):
      constraints.append([int(key[0] == train) for key in variables])
      bounds.append(limit)
    for segment, record in sorted(self.segments.items()):
      constraints.append([1 / heat_per_flow[key[0]] if segment in self.paths[key[:2]] else 0.0 for key in variables])
      bounds.append(record.capacity_kg_s)
    return constraints, bounds

  def allocate(
    self, demand_kw: dict[str, float], source_kw: dict[str, float],
    terminal_kw: dict[str, float | dict[str, float]],
    heat_per_flow: dict[str, float], unavailable: set[str],
    *, hydraulics: "FacilityWaterHydraulics | None" = None, unavailable_pumps: set[str] | None = None,
    pump_speed_limits: dict[str, float] | None = None,
    units_by_level: dict[str, tuple[str, ...]] | None = None,
    unavailable_outlets: dict[tuple[str, str], set[str]] | None = None,
    demand_kw_by_unit: dict[str, float] | None = None,
  ) -> dict:
    if hydraulics is None and (unavailable_pumps or pump_speed_limits):
      raise ValueError("Pump scenarios require an FWS hydraulic model.")
    if hydraulics is not None and hydraulics.network is not self:
      raise ValueError("FWS hydraulic model must bind to the allocated network.")
    if unavailable - self.segments.keys():
      raise ValueError(f"Unknown unavailable FWS segments: {sorted(unavailable - self.segments.keys())}.")
    if not (self.train_names == source_kw.keys() == terminal_kw.keys() == heat_per_flow.keys()):
      raise ValueError("FWS allocation inputs must cover the declared trains.")
    limits = {}
    for train, rating in terminal_kw.items():
      by_terminal = rating if isinstance(rating, dict) else dict.fromkeys(demand_kw, rating)
      if by_terminal.keys() != demand_kw.keys():
        raise ValueError(f"FWS train {train} needs a rating for each active terminal.")
      limits.update({(train, terminal): value for terminal, value in by_terminal.items()})
    if not all(type(value) in {int, float} and math.isfinite(value) and value >= 0
               for value in (*demand_kw.values(), *source_kw.values(), *limits.values())):
      raise ValueError("FWS demand and ratings must be finite nonnegative kW values.")
    if not all(type(value) in {int, float} and math.isfinite(value) and value > 0
               for value in heat_per_flow.values()):
      raise ValueError("FWS heat per unit flow must be positive and finite.")
    ready = [(train, terminal) for (train, terminal), segments in sorted(self.paths.items())
             if terminal in demand_kw and demand_kw[terminal] > 0 and source_kw[train] > 0
             and limits[train, terminal] > 0 and not unavailable.intersection(segments)]
    outlet_groups = None
    if units_by_level is None:
      if unavailable_outlets or demand_kw_by_unit is not None:
        raise ValueError("Per-outlet inputs need the unit list for each floor.")
      variables, group_demand, variable_limits = ready, {(key,): value for key, value in demand_kw.items()}, limits
    else:
      if hydraulics is None or not hydraulics.distributions or units_by_level.keys() != demand_kw.keys():
        raise ValueError("Outlet dispatch needs distribution hydraulics and a unit list for every active floor.")
      blocked = unavailable_outlets or {}
      if any(pair not in self.paths or (pair[1] in units_by_level and names - set(units_by_level[pair[1]]))
             for pair, names in blocked.items()):
        raise ValueError("Outlet dispatch contains an unknown blocked unit or train.")
      for pair, distribution in hydraulics.distributions.items():
        if pair[1] in units_by_level and set(units_by_level[pair[1]]) != distribution.paths.keys():
          raise ValueError("Outlet identities must match the hydraulic branch model.")
      unit_demand = {f"{level}/{unit}": demand_kw[level] / len(units)
                     for level, units in units_by_level.items() for unit in units}
      if demand_kw_by_unit is not None:
        if (demand_kw_by_unit.keys() != unit_demand.keys()
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in demand_kw_by_unit.values())
            or any(not math.isclose(demand_kw[level], sum(demand_kw_by_unit[f"{level}/{unit}"] for unit in units), abs_tol=1e-7)
                   for level, units in units_by_level.items())):
          raise ValueError("Outlet demands must be nonnegative and sum to their corresponding floor demands.")
        unit_demand = demand_kw_by_unit
      variables, outlet_groups, group_demand, variable_limits = _outlet_groups(ready, units_by_level, blocked, unit_demand, limits)
    constraints, bounds, heat = [], [], []
    if variables:
      for group, demand in sorted(group_demand.items()):
        constraints.append([int(key[1:] == group) for key in variables])
        bounds.append(demand)
      capacity_rows, capacity_bounds = self.capacity_constraints(variables, source_kw, heat_per_flow)
      constraints.extend(capacity_rows)
      bounds.extend(capacity_bounds)
      result = linprog([-1.0] * len(variables), A_ub=constraints, b_ub=bounds,
                       bounds=[(0, variable_limits[key]) for key in variables], method="highs")
      if not result.success:
        raise ValueError(f"Unable to allocate rated FWS flow: {result.message}")
      heat = result.x
    hydraulic_report = None
    if hydraulics is not None:
      heat, hydraulic_report = hydraulics.dispatch(variables, heat_per_flow, constraints, bounds, variable_limits, heat,
                                                   unavailable_pumps or set(), pump_speed_limits or {}, outlet_groups)
      hydraulic_report["limited_kw"] = max(0.0, hydraulic_report["rated_capacity_dispatch_kw"] - float(sum(heat)))
    return self.summarize_dispatch(demand_kw, heat_per_flow, variables, heat, hydraulic_report,
                                   units_by_level=units_by_level, outlet_groups=outlet_groups,
                                   unit_demand=unit_demand if units_by_level is not None else None)

  def summarize_dispatch(self, demand_kw, heat_per_flow, variables, heat, hydraulic_report, *,
                         units_by_level=None, outlet_groups=None, unit_demand=None):
    delivered = dict.fromkeys(demand_kw, 0.0)
    by_train = dict.fromkeys(sorted(self.train_names), 0.0)
    by_path = {train: {} for train in sorted(self.train_names)}
    segment_flow = dict.fromkeys(sorted(self.segments), 0.0)
    available_trains = (self.train_names if hydraulic_report is None else
                        {name for name, bank in hydraulic_report["banks"].items() if bank["available_dispatch_pumps"]})
    by_unit = {f"{level}/{unit}": dict.fromkeys(sorted(self.train_names), 0.)
               for level, units in (units_by_level or {}).items() for unit in units}
    reachable_units = {unit: [] for unit in by_unit}
    for key, amount in zip(variables, heat, strict=True):
      train, terminal = key[:2]
      delivered[terminal] += float(amount)
      by_train[train] += float(amount)
      by_path[train][terminal] = by_path[train].get(terminal, 0.) + float(amount)
      if outlet_groups is not None:
        units = outlet_groups[key]
        for unit in units:
          identity = f"{terminal}/{unit}"
          by_unit[identity][train] = float(amount) / len(units)
          if train in available_trains:
            reachable_units[identity].append(train)
      for segment in self.paths[train, terminal]:
        segment_flow[segment] += float(amount) / heat_per_flow[train]
    shortages = {terminal: required - delivered[terminal] for terminal, required in demand_kw.items()
                 if required - delivered[terminal] > 1e-6}
    allocation = {
      "delivered_kw_by_terminal": delivered, "delivered_kw_by_train": by_train,
      "delivered_kw_by_path": by_path,
      "shortage_kw_by_terminal": shortages, "flow_kg_s_by_segment": segment_flow,
      "available_trains_by_terminal": {
        terminal: sorted({key[0] for key in variables if key[1] == terminal and key[0] in available_trains})
        for terminal in demand_kw
      },
    }
    if units_by_level is not None:
      allocation["delivered_kw_by_unit_train"] = by_unit
      allocation["available_trains_by_unit"] = {key: sorted(trains) for key, trains in reachable_units.items()}
      allocation["shortage_kw_by_unit"] = {unit: demand - sum(by_unit[unit].values()) for unit, demand in unit_demand.items()
                                           if demand - sum(by_unit[unit].values()) > 1e-6}
    if hydraulic_report is not None:
      allocation["hydraulics"] = hydraulic_report
    return allocation
