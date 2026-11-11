"""Compose facility and technology water constraints over the same rack heat variables."""

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from texsys_hvac_dynamics.facility_hydraulics import dispatch_heat, retain_dispatch


@dataclass(frozen=True)
class TcsFault:
  segments: frozenset[str]
  pumps: frozenset[str]
  speeds: tuple[tuple[str, float], ...]


def _room_groups(water, upstream, faults, feed_by_cdu):
  facility = upstream["fws"]
  source_kw, terminal_kw = upstream["fws_source_kw"], upstream["fws_terminal_kw"]
  blocked_segments = set(facility["unavailable_segments"])
  blocked_rooms = {key: set(names) for key, names in facility.get("blocked_units_by_train_level", {}).items()}
  available = {train for train, bank in facility["hydraulics"]["banks"].items() if bank["available_dispatch_pumps"]}
  groups = defaultdict(list)
  room_counts = defaultdict(int)
  for identity, fault in sorted(faults.items()):
    level, room = identity.split("/")
    room_counts[level] += 1
    ready = tuple(cdu for cdu, train in sorted(feed_by_cdu.items())
                  if train in available and source_kw[train] > 0 and terminal_kw[train] > 0
                  and not blocked_segments.intersection(water.network.paths[train, level])
                  and room not in blocked_rooms.get(f"{level}/{train}", ()))
    groups[level, ready, fault].append(room)
  return groups, room_counts


def redispatch(water, upstream, solver, demand, faults, feed_by_cdu):
  """Solve one operating point with shared FWS limits and rack-level TCS pressure."""
  groups, room_counts = _room_groups(water, upstream, faults, feed_by_cdu)
  facility = upstream["fws"]
  source_kw, terminal_kw = upstream["fws_source_kw"], upstream["fws_terminal_kw"]
  facility_banks = facility["hydraulics"]["banks"]
  running = {pump for bank in facility_banks.values() for pump in bank["available_dispatch_pumps"]}
  speeds = {name: bank["max_speed_ratio"] for name, bank in facility_banks.items()}

  room_plans, rack_variables, facility_groups, facility_limits = [], [], {}, {}
  for index, ((level, ready, fault), rooms) in enumerate(groups.items()):
    count = len(rooms)
    rack_paths = [(cdu, rack) for (cdu, rack), path in sorted(solver.network.paths.items())
                  if cdu in ready and demand[rack] > 0 and solver.terminal_kw[cdu][rack] > 0
                  and not fault.segments.intersection(path)]
    limits = {(cdu, rack): solver.terminal_kw[cdu][rack] for cdu, rack in rack_paths}
    model = solver.hydraulics.prepare_dispatch(rack_paths, solver.heat_per_flow, limits, fault.pumps, dict(fault.speeds))
    # Each rack variable represents the sum for that rack position in all identical rooms.
    start = len(rack_variables)
    columns = slice(start, start + len(rack_paths))
    rack_variables.extend((cdu, level, index) for cdu, rack in rack_paths)
    room_plans.append((level, rooms, rack_paths, columns, model))
    for cdu in sorted({cdu for cdu, _ in rack_paths}):
      train = feed_by_cdu[cdu]
      key = (train, level, index)
      facility_groups[key] = rooms
      facility_limits[key] = terminal_kw[train] * count / room_counts[level]

  facility_paths = list(facility_groups)
  facility_model = water.hydraulics.prepare_dispatch(facility_paths, water.heat_per_flow, facility_limits,
                                                     water.hydraulics.pumps - running, speeds, facility_groups)
  fws_groups = {(feed_by_cdu[cdu], level, group) for cdu, level, group in rack_variables}
  if fws_groups != facility_groups.keys():
    raise ValueError("Coupled dispatch has an inconsistent rack to facility mapping.")
  projection = np.array([[key == (feed_by_cdu[cdu], level, group) for cdu, level, group in rack_variables]
                         for key in facility_paths], dtype=float)

  if rack_variables:
    base_rows, base_bounds = water.network.capacity_constraints(facility_paths, source_kw, water.heat_per_flow)
    matrix = [np.asarray(base_rows) @ projection, facility_model.matrix @ projection, projection]
    ceilings = [base_bounds, facility_model.ceilings, facility_model.upper_kw]
    upper_kw = np.zeros(len(rack_variables))
    for _, rooms, paths, columns, model in room_plans:
      if not paths:
        continue
      count = len(rooms)
      rows, bounds = solver.network.capacity_constraints(paths, {cdu: loop.heat_kw for cdu, loop in solver.loops.items()},
                                                          solver.heat_per_flow)
      rack_rows = [[int(target == rack) for _, target in paths] for rack in demand]
      local = np.vstack((rows, rack_rows, model.matrix))
      expanded = np.zeros((len(local), len(rack_variables)))
      expanded[:, columns] = local / count
      matrix.append(expanded)
      ceilings.append(np.concatenate((bounds, list(demand.values()), model.ceilings)))
      upper_kw[columns] = model.upper_kw * count

    def margins(heat):
      return np.concatenate((facility_model.margins(projection @ heat),
                             *(model.margins(heat[columns] / len(rooms))
                               for _, rooms, _, columns, model in room_plans)))

    matrix, ceilings = np.vstack(matrix), np.concatenate(ceilings)
    heat, method, rated = dispatch_heat(matrix, ceilings, upper_kw, margins)
    explicit = facility.get("delivered_kw_by_unit_train")
    baseline = np.array([
      sum(explicit[f"{level}/{room}"][train] for room in facility_groups[train, level, group]) if explicit is not None
      else facility["delivered_kw_by_path"][train].get(level, 0.) * len(facility_groups[train, level, group]) / room_counts[level]
      for train, level, group in facility_paths
    ])
    pressure_limited = {i for _, rooms, _, columns, model in room_plans
                        if any(model.margins(heat[columns] / len(rooms)) < 1.)
                        for i in range(columns.start, columns.stop)}
    heat, priority = retain_dispatch(heat, rated, matrix, ceilings, upper_kw, margins,
                                     projection, baseline, pressure_limited)
    facility_heat, facility_rated = projection @ heat, projection @ rated
  else:
    heat, facility_heat, rated, facility_rated, method = np.empty(0), np.empty(0), np.empty(0), np.empty(0), "no-paths"
    priority = "unchanged"

  results = {}
  by_level = defaultdict(list)
  for level, rooms, paths, columns, model in room_plans:
    by_level[level].extend(rooms)
    per_room = heat[columns] / len(rooms)
    hydraulic = model.report(per_room, rated[columns] / len(rooms), method)
    hydraulic["limited_kw"] = max(0., hydraulic["rated_capacity_dispatch_kw"] - float(sum(per_room)))
    result = solver.network.summarize_dispatch(demand, solver.heat_per_flow, paths, per_room, hydraulic)
    solver.with_circuit_details(result)
    for room in rooms:
      results[f"{level}/{room}"] = result

  demand_by_room = dict.fromkeys(faults, sum(demand.values()))
  demand_by_level = {level: len(rooms) * sum(demand.values()) for level, rooms in by_level.items()}
  hydraulic = facility_model.report(facility_heat, facility_rated, method)
  hydraulic["limited_kw"] = max(0., hydraulic["rated_capacity_dispatch_kw"] - float(sum(facility_heat)))
  allocation = water.network.summarize_dispatch(demand_by_level, water.heat_per_flow, facility_paths, facility_heat,
                                                 hydraulic, units_by_level=by_level, outlet_groups=facility_groups,
                                                 unit_demand=demand_by_room)
  balance_error = 0.
  for identity, result in results.items():
    by_train = allocation["delivered_kw_by_unit_train"][identity]
    balance_error = max(balance_error, *(abs(by_train[train] - result["delivered_kw_by_train"][cdu])
                                        for cdu, train in feed_by_cdu.items()))
  if balance_error > 1e-6:
    raise ValueError("Coupled dispatch does not balance heat at every CDU.")
  allocation.update(priority=priority, source_capacity_kw=source_kw, cdu_heat_balance_error_kw=balance_error)
  allocation.update({key: facility[key] for key in ("unavailable_segments", "unavailable_branch_segments",
                                                    "blocked_units_by_train_level", "interlocks") if key in facility})
  return allocation, results
