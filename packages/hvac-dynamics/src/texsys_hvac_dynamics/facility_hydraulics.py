"""Pressure-constrained flow dispatch for controlled closed-loop facility water."""

from dataclasses import dataclass
from collections import defaultdict
from collections.abc import Callable
import math

import numpy as np
from scipy.optimize import linprog, minimize

from .hydraulics import pipe_loss, pump_head_m


_GRAVITY = 9.80665
_PRESSURE_SCALE = 1e5
_PRESSURE_TOL_PA = .1


def _number(value, label, *, zero=False):
  if type(value) not in {int, float} or not math.isfinite(value) or value < 0 or (value == 0 and not zero):
    raise ValueError(f"Invalid FWS hydraulic input: {label}.")
  return value


@dataclass(frozen=True)
class PipeHydraulics:
  length_m: float
  diameter_m: float
  roughness_m: float
  minor_loss_coefficient: float
  max_velocity_m_s: float

  def __post_init__(self):
    for name in ("length_m", "diameter_m", "max_velocity_m_s"):
      _number(getattr(self, name), name)
    for name in ("roughness_m", "minor_loss_coefficient"):
      _number(getattr(self, name), name, zero=True)
    if self.roughness_m >= self.diameter_m:
      raise ValueError("FWS pipe roughness must be below its inside diameter.")

  @property
  def area_m2(self):
    return math.pi * self.diameter_m**2 / 4


@dataclass(frozen=True)
class EquipmentLoss:
  rated_flow_m3_s: float
  rated_drop_kpa: float

  def __post_init__(self):
    _number(self.rated_flow_m3_s, "rated equipment flow")
    _number(self.rated_drop_kpa, "rated equipment pressure drop", zero=True)

  def pressure_pa(self, flow):
    return 1000 * self.rated_drop_kpa * (flow / self.rated_flow_m3_s)**2


@dataclass(frozen=True)
class PumpBank:
  pumps: tuple[str, ...]
  max_running: int
  shutoff_head_m: float
  curve_m_per_m6_s2: float
  max_flow_m3_s_each: float
  efficiency: float
  reserve_kpa: float
  common_loss: EquipmentLoss
  terminal_losses: dict[str, EquipmentLoss]

  def __post_init__(self):
    if (not isinstance(self.pumps, (list, tuple))
        or any(not isinstance(name, str) or not name.strip() for name in self.pumps)
        or len(set(self.pumps)) != len(self.pumps)
        or type(self.max_running) is not int or not 1 <= self.max_running <= len(self.pumps)):
      raise ValueError("A pump bank needs distinct pump IDs and a valid maximum running count.")
    for name in ("shutoff_head_m", "curve_m_per_m6_s2", "max_flow_m3_s_each", "efficiency"):
      _number(getattr(self, name), name)
    _number(self.reserve_kpa, "pump control reserve", zero=True)
    if self.efficiency > 1 or pump_head_m(self.max_flow_m3_s_each, self.shutoff_head_m,
                                         self.curve_m_per_m6_s2, 1) < 0:
      raise ValueError("Pump efficiency and maximum-flow head must stay within the curve domain.")


@dataclass(frozen=True)
class HydraulicDispatch:
  """Constraints and evidence for heat variables mapped to one hydraulic network."""

  matrix: np.ndarray
  ceilings: np.ndarray
  upper_kw: np.ndarray
  margins: Callable
  report: Callable


class FacilityWaterHydraulics:
  """Allocate controlled terminal flows within radial pipe and pump envelopes."""

  def __init__(self, network, config: dict, *, distributions=None):
    self.network = network
    self.density = _number(config["density_kg_m3"], "density_kg_m3")
    self.viscosity = _number(config["viscosity_pa_s"], "viscosity_pa_s")
    if config["pipes"].keys() != network.segments.keys() or config["banks"].keys() != network.train_names:
      raise ValueError("FWS hydraulics must specify every pipe and train.")
    self.pipes = {name: PipeHydraulics(**values) for name, values in sorted(config["pipes"].items())}
    self.banks = {}
    pumps = set()
    for name, values in sorted(config["banks"].items()):
      bank = PumpBank(**{**values, "common_loss": EquipmentLoss(**values["common_loss"]),
                         "terminal_losses": {level: EquipmentLoss(**loss)
                                             for level, loss in values["terminal_losses"].items()}})
      if bank.terminal_losses.keys() != {level for train, level in network.paths if train == name}:
        raise ValueError(f"Pump bank {name} needs a terminal loss for each served level.")
      if pumps.intersection(bank.pumps):
        raise ValueError("Pump identities must be unique across FWS banks.")
      pumps.update(bank.pumps)
      self.banks[name] = bank
    self.pumps = pumps
    self.distributions = dict(distributions or {})
    if self.distributions and self.distributions.keys() != network.paths.keys():
      raise ValueError("FWS floor distributions must cover every train and terminal.")

  def _pressure(self, bank, total_flow, count, speed):
    if not count:
      return 0.0
    return self.density * _GRAVITY * pump_head_m(total_flow / count, bank.shutoff_head_m,
                                                bank.curve_m_per_m6_s2, speed)

  def dispatch(self, variables, heat_per_flow, constraints, bounds, limits, rated, unavailable, speed_limits,
               outlet_groups=None):
    model = self.prepare_dispatch(variables, heat_per_flow, limits, unavailable, speed_limits, outlet_groups)
    if not variables:
      return [], model.report([], rated, "no-paths")
    matrix = np.vstack((constraints, model.matrix))
    ceilings = np.concatenate((bounds, model.ceilings))
    heat, method, _ = dispatch_heat(matrix, ceilings, model.upper_kw, model.margins)
    return heat, model.report(heat, rated, method)

  def prepare_dispatch(self, variables, heat_per_flow, limits, unavailable, speed_limits, outlet_groups=None):
    if unavailable - self.pumps or speed_limits.keys() - self.banks.keys():
      raise ValueError("FWS scenario refers to an unknown pump or pump bank.")
    speeds = {}
    for train in self.banks:
      speed = _number(speed_limits.get(train, 1), f"{train}.max_speed_ratio", zero=True)
      if speed > 1:
        raise ValueError("FWS pump speed limits must be between zero and rated speed.")
      speeds[train] = speed
    running = {}
    for train, bank in self.banks.items():
      available = tuple(pump for pump in bank.pumps if pump not in unavailable)[:bank.max_running]
      shutoff_pa = self._pressure(bank, 0, len(available), speeds[train])
      running[train] = available if shutoff_pa > bank.reserve_kpa * 1000 else ()
    if not variables:
      def report(heat, rated, method):
        return self._report({}, {}, {}, {}, running, speeds, rated, method)

      return HydraulicDispatch(np.empty((0, 0)), np.empty(0), np.empty(0), lambda heat: np.empty(0), report)

    pipe_names = tuple(self.pipes)
    train_names = tuple(self.banks)
    volume_per_heat = np.array([1 / (self.density * heat_per_flow[key[0]]) for key in variables])
    paths = np.array([[pipe in self.network.paths[key[:2]] for pipe in pipe_names] for key in variables], dtype=float)
    train_membership = np.array([[key[0] == train for key in variables] for train in train_names], dtype=float)
    pipe_matrix = paths.T * volume_per_heat
    train_matrix = train_membership * volume_per_heat
    matrix = np.vstack((pipe_matrix, train_matrix))
    ceilings = np.array([*[pipe.area_m2 * pipe.max_velocity_m_s for pipe in self.pipes.values()],
                         *[len(running[train]) * speeds[train] * bank.max_flow_m3_s_each
                           for train, bank in self.banks.items()]])
    by_floor = defaultdict(list)
    if outlet_groups is not None:
      for i, key in enumerate(variables):
        by_floor[key[:2]].append(i)
      rows, pipe_caps = [], []
      for pair, positions in by_floor.items():
        distribution = self.distributions[pair]
        coefficients = [distribution.group_coefficients(outlet_groups[variables[i]]) for i in positions]
        for pipe, capacity in distribution.pipe_capacity_m3_s.items():
          row = np.zeros(len(variables))
          row[positions] = [values[pipe] * volume_per_heat[i] for i, values in zip(positions, coefficients, strict=True)]
          rows.append(row)
          pipe_caps.append(capacity)
      matrix = np.vstack((matrix, rows))
      ceilings = np.concatenate((ceilings, pipe_caps))
    upper_kw = []
    for key, conversion in zip(variables, volume_per_heat, strict=True):
      limit = limits[key] if running[key[0]] else 0.
      if self.distributions and outlet_groups is None:
        limit = min(limit, self.distributions[key].max_flow_m3_s / conversion)
      upper_kw.append(limit)

    train_index = [train_names.index(key[0]) for key in variables]
    live = np.array([bool(running[key[0]]) for key in variables], dtype=bool)
    terminal_losses = [self.banks[key[0]].terminal_losses[key[1]] for key in variables]

    def terminal_pressure_at(flows):
      if outlet_groups is None:
        pressure = np.array([loss.pressure_pa(q) for q, loss in zip(flows, terminal_losses, strict=True)])
        if self.distributions:
          pressure += [self.distributions[key].pressure_pa(q) for key, q in zip(variables, flows, strict=True)]
        return pressure, {}
      pressure = np.zeros(len(variables))
      details = {}
      for pair, positions in by_floor.items():
        distribution = self.distributions[pair]
        unit_flows = dict.fromkeys(distribution.paths, 0.)
        for i in positions:
          units = outlet_groups[variables[i]]
          for unit in units:
            unit_flows[unit] = float(flows[i]) / len(units)
        losses, _ = distribution.outlet_state(unit_flows)
        governing = None
        for i in positions:
          if flows[i] <= 1e-12:
            continue
          critical = max(outlet_groups[variables[i]], key=losses.__getitem__)
          equipment = terminal_losses[i].pressure_pa(unit_flows[critical] * len(distribution.paths))
          pressure[i] = losses[critical] + equipment
          if governing is None or pressure[i] > governing[0]:
            governing = (pressure[i], critical, losses[critical], equipment)
        if governing is not None:
          _, critical, pipe, equipment = governing
          details[pair] = {"critical_unit": critical, "flow_m3_s_by_unit": unit_flows,
                           "terminal_equipment": equipment / 1000, "floor_distribution": pipe / 1000}
      return pressure, details

    def pressures(heat):
      pipe_flow, train_flow = pipe_matrix @ heat, train_matrix @ heat
      pipe_pressure = np.array([
        pipe_loss(q, diameter=pipe.diameter_m, length=pipe.length_m, roughness=pipe.roughness_m,
                  minor_loss=pipe.minor_loss_coefficient, density=self.density, viscosity=self.viscosity)
        for q, pipe in zip(pipe_flow, self.pipes.values(), strict=True)
      ])
      plant_pressure = np.array([bank.common_loss.pressure_pa(q)
                                 for q, bank in zip(train_flow, self.banks.values(), strict=True)])
      terminal_pressure, details = terminal_pressure_at(heat * volume_per_heat)
      pump_pressure = np.array([self._pressure(bank, q, len(running[train]), speeds[train])
                                for train, q, bank in zip(train_names, train_flow, self.banks.values(), strict=True)])
      reserves = np.array([bank.reserve_kpa * 1000 for bank in self.banks.values()])
      required = paths @ pipe_pressure + (plant_pressure + reserves)[train_index] + terminal_pressure
      return pipe_flow, train_flow, pipe_pressure, required, pump_pressure, details

    def margins(heat):
      *_, required, pump_pressure, _ = pressures(heat)
      return (pump_pressure[train_index] - required)[live]

    def report(heat, rated, method):
      pipe_flow, train_flow, pipe_pressure, required, pump_pressure, details = pressures(heat)
      operating_paths = {}
      for key, flow, need, is_live in zip(variables, heat * volume_per_heat, required, live, strict=True):
        if is_live and flow > 1e-10:
          total, peak = operating_paths.get(key[:2], (0., 0.))
          operating_paths[key[:2]] = (total + float(flow), max(peak, float(need)))
      pipes = {name: (float(flow), float(loss)) for name, flow, loss in
               zip(pipe_names, pipe_flow, pipe_pressure, strict=True)}
      bank_flows = dict(zip(train_names, map(float, train_flow), strict=True))
      pump_pa = dict(zip(train_names, map(float, pump_pressure), strict=True))
      return self._report(operating_paths, pipes, bank_flows, pump_pa, running, speeds, rated, method, details)

    return HydraulicDispatch(matrix, ceilings, np.array(upper_kw), margins, report)

  def _report(self, paths, pipes, bank_flows, pump_pa, running, speeds, rated, method, details=None):
    banks = {}
    details = details or {}
    for name, bank in self.banks.items():
      flow = bank_flows.get(name, 0)
      available = pump_pa.get(name, self._pressure(bank, flow, len(running[name]), speeds[name]))
      required = {level: pressure for (train, level), (q, pressure) in paths.items() if train == name and q > 1e-10}
      critical = max(required, key=required.get) if required else None
      need = required[critical] if critical is not None else 0.0
      operating_speed, operating_head = 0.0, 0.0
      dispatch_pumps = ()
      if critical is not None:
        count = next((n for n in range(1, len(running[name]) + 1)
                      if flow <= n * bank.max_flow_m3_s_each * speeds[name] + 1e-9
                      and self._pressure(bank, flow, n, speeds[name]) >= need - _PRESSURE_TOL_PA),
                     len(running[name]))
        dispatch_pumps = running[name][:count]
        each = flow / count
        speed_for_head = math.sqrt((need / (self.density * _GRAVITY) + bank.curve_m_per_m6_s2 * each**2)
                                   / bank.shutoff_head_m)
        operating_speed = min(speeds[name], max(speed_for_head, each / bank.max_flow_m3_s_each))
        operating_head = self._pressure(bank, flow, count, operating_speed)
      banks[name] = {
        "dispatch_pumps": dispatch_pumps,
        "available_dispatch_pumps": running[name],
        "max_speed_ratio": speeds[name], "flow_m3_s": flow,
        "pump_curve_limit_kpa": available / 1000,
        "required_kpa": need / 1000,
        "head_margin_kpa": (available - need) / 1000 if critical is not None else None,
        "critical_terminal": critical, "estimated_input_kw": operating_head * flow / 1000 / bank.efficiency,
        "estimated_speed_ratio": operating_speed, "estimated_pump_head_kpa": operating_head / 1000,
        "flow_m3_s_by_terminal": {level: q for (train, level), (q, _) in paths.items() if train == name and q > 1e-10},
        "required_kpa_by_terminal": {level: pressure / 1000 for level, pressure in required.items()},
        "pressure_components_kpa_by_terminal": {
          level: {
            "pipe": sum(pipes[pipe][1] for pipe in self.network.paths[name, level]) / 1000,
            "common_equipment": bank.common_loss.pressure_pa(flow) / 1000,
            "terminal_equipment": (details[name, level]["terminal_equipment"] if (name, level) in details
                                   else bank.terminal_losses[level].pressure_pa(paths[name, level][0]) / 1000),
            "control_reserve": bank.reserve_kpa,
            **({"floor_distribution": (details[name, level]["floor_distribution"] if (name, level) in details
                                       else self.distributions[name, level].pressure_pa(paths[name, level][0]) / 1000)}
               if self.distributions else {}),
          }
          for level in required
        },
        **({"floor_outlets_by_terminal": {level: details[name, level] for level in required if (name, level) in details}}
           if details else {}),
        "balancing_kpa_by_terminal": {level: max(0, operating_head - pressure) / 1000
                                     for level, pressure in required.items()},
      }
    return {
      "method": method, "rated_capacity_dispatch_kw": float(sum(rated)), "banks": banks,
      "pipe_velocity_m_s": {name: pipes.get(name, (0, 0))[0] / pipe.area_m2 for name, pipe in self.pipes.items()},
      "pipe_pressure_drop_kpa": {name: pipes.get(name, (0, 0))[1] / 1000 for name in self.pipes},
    }


def dispatch_heat(matrix, ceilings, upper_kw, margins):
  """Maximize delivered heat subject to linear capacity and nonlinear pressure."""
  path_bounds = list(zip(np.zeros(len(upper_kw)), upper_kw, strict=True))
  capacity = linprog(-np.ones(len(upper_kw)), A_ub=matrix, b_ub=ceilings, bounds=path_bounds, method="highs")
  if not capacity.success:
    raise ValueError(f"Unable to allocate pipe and pump capacity: {capacity.message}")
  if any(margins(capacity.x) < -_PRESSURE_TOL_PA):
    heat = _dispatch_pressure(capacity.x, matrix, ceilings, path_bounds, margins)
    return heat, "pressure-constrained", capacity.x
  return capacity.x, "capacity-optimum", capacity.x


def retain_dispatch(heat, rated, matrix, ceilings, upper_kw, margins, projection, baseline, fixed):
  """Among equally served points, retain the most of the original facility allocation."""
  live = baseline > 0
  projection, baseline = projection[live], baseline[live]
  if not len(baseline) or not sum(heat):
    return heat, "unchanged"
  count = len(heat)
  scale = max(float(max(upper_kw)), 1.)
  # Extra variables count only the heat that remains on its original facility path.
  base = np.vstack((np.hstack((matrix, np.zeros((len(matrix), len(baseline))))),
                    np.hstack((-projection, np.eye(len(baseline))))))
  ceilings = np.concatenate((ceilings / scale, np.zeros(len(baseline))))
  row_scale = np.maximum(np.max(np.abs(base), axis=1), 1e-9)
  base, ceilings = base / row_scale[:, None], ceilings / row_scale
  bounds = list(zip(np.zeros(count + len(baseline)), np.concatenate((upper_kw, baseline)) / scale, strict=True))
  total = min(sum(heat), sum(rated))
  equality = np.concatenate((np.ones(count), np.zeros(len(baseline))))
  objective = np.concatenate((np.zeros(count), -np.ones(len(baseline))))
  linear = linprog(objective, A_ub=base, b_ub=ceilings, A_eq=[equality], b_eq=[total / scale],
                   bounds=bounds, method="highs")
  if not linear.success:
    return heat, "primary-feasible"
  preferred = linear.x[:count] * scale
  if all(margins(preferred) >= -_PRESSURE_TOL_PA):
    return preferred, "baseline-retention"

  if fixed:
    local_bounds = [((heat[i] / scale,) * 2 if i in fixed else bound) for i, bound in enumerate(bounds)]
    local = linprog(objective, A_ub=base, b_ub=ceilings, A_eq=[equality], b_eq=[total / scale],
                    bounds=local_bounds, method="highs")
    if local.success and all(margins(local.x[:count] * scale) >= -_PRESSURE_TOL_PA):
      return local.x[:count] * scale, "baseline-retention"

  initial = np.concatenate((heat, np.minimum(baseline, projection @ heat))) / scale
  solved = minimize(lambda x: objective @ x, initial, jac=lambda x: objective,
                    method="SLSQP", bounds=bounds, constraints=(
                      {"type": "eq", "fun": lambda x: equality @ x - total / scale, "jac": lambda x: equality},
                      {"type": "ineq", "fun": lambda x: ceilings - base @ x, "jac": lambda x: -base},
                      {"type": "ineq", "fun": lambda x: margins(x[:count] * scale) / _PRESSURE_SCALE},
                    ), options={"ftol": 1e-11, "maxiter": 200})
  preferred = solved.x[:count] * scale
  if (solved.success and abs(sum(preferred) - total) <= 1e-6 and all(ceilings - base @ solved.x >= -1e-8)
      and all(margins(preferred) >= -_PRESSURE_TOL_PA) and objective @ solved.x <= objective @ initial + 1e-8):
    return preferred, "baseline-retention"
  return heat, "primary-feasible"


def _dispatch_pressure(upper, matrix, ceilings, bounds, margins):
  # Along a fixed allocation ratio, losses grow and available pump head falls.
  lower, higher = 0.0, 1.0
  for _ in range(36):
    middle = (lower + higher) / 2
    if all(margins(middle * upper) >= 0):
      lower = middle
    else:
      higher = middle
  scale = max(float(max(upper)), 1.0)
  limits = np.array([limit for _, limit in bounds])
  active = limits > 0
  # Keep unavailable branches exactly zero and out of the numerical Jacobian.
  # Relative three-point differences limit cancellation in head near balance.
  matrix = matrix[:, active]
  row_scale = np.maximum(ceilings, np.maximum(np.max(np.abs(matrix), axis=1) * scale, 1e-9))
  a, b = matrix * scale / row_scale[:, None], ceilings / row_scale

  def expand(x):
    heat = np.zeros(len(limits))
    heat[active] = x * scale
    return heat

  solved = minimize(
    lambda x: -float(sum(x)), lower * (1 - 1e-4) * upper[active] / scale, jac="3-point",
    method="SLSQP", bounds=[(0, limit / scale) for limit in limits[active]],
    constraints=({"type": "ineq", "fun": lambda x: b - a @ x, "jac": lambda _: -a},
                 {"type": "ineq", "fun": lambda x: margins(expand(x)) / _PRESSURE_SCALE}),
    options={"ftol": 1e-11, "maxiter": 200},
  )
  heat = np.clip(expand(solved.x), 0, limits)
  if (not solved.success or not np.all(np.isfinite(heat)) or any(b - a @ (heat[active] / scale) < -1e-8)
      or any(margins(heat) < -_PRESSURE_TOL_PA)):
    raise ValueError(f"FWS pressure-constrained dispatch did not converge to a feasible point: {solved.message}")
  return heat
