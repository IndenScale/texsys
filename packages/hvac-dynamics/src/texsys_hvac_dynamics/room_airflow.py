"""Steady airflow and sensible mixing for a small IT room."""

from dataclasses import dataclass
from math import exp, isfinite, sqrt


@dataclass(frozen=True)
class RackAirflow:
  flow_m3_s: float
  sensible_kw: float
  minimum_recirculation: float = 0.


@dataclass(frozen=True)
class SupplyAirflow:
  rated_flow_m3_s: float
  sensible_capacity_kw: float
  static_limit_pa: float
  branch_resistance: dict[str, float]
  bypass_conductance: float = 0.
  common_resistance: float = 0.
  shared_branches: tuple = ()


@dataclass(frozen=True)
class SharedAirBranch:
  name: str
  racks: tuple[str, ...]
  resistance: float


def _vapor_pressure_pa(temperature):
  """Magnus saturation estimate for ordinary conditioned-room temperatures."""
  return 611.2 * exp(17.67 * temperature / (temperature + 243.5))


def room_airflow(racks, supplies, *, density_kg_m3, cp_kj_kgk, supply_c, other_room_kw, dew_point_c):
  """Resolve parallel supply paths and a shared hot-air recirculation pool.

  Branches obey dp = R * Q² at the available external fan static pressure.
  Rack fans draw fixed airflow. Uncaptured supply returns through bypass and
  rack shortfall entrains mixed hot exhaust. Humidity ratio is constant.
  """
  if (not racks or not all(map(isfinite, (density_kg_m3, cp_kj_kgk, supply_c, other_room_kw, dew_point_c)))
      or min(density_kg_m3, cp_kj_kgk) <= 0 or other_room_kw < 0
      or not -60 < dew_point_c <= 65 or not -30 < supply_c <= 65):
    raise ValueError("Room airflow needs finite air properties, heat and conditioned-room temperatures.")
  if any(not all(map(isfinite, (rack.flow_m3_s, rack.sensible_kw, rack.minimum_recirculation)))
         or rack.flow_m3_s <= 0 or rack.sensible_kw < 0 or not 0 <= rack.minimum_recirculation < 1
         for rack in racks.values()):
    raise ValueError("Rack airflow must be positive, heat nonnegative and minimum recirculation below one.")

  transported = density_kg_m3 * cp_kj_kgk
  directed = dict.fromkeys(racks, 0.)
  fan_results = {}
  for name, unit in supplies.items():
    values = (unit.rated_flow_m3_s, unit.sensible_capacity_kw, unit.static_limit_pa, unit.bypass_conductance,
              unit.common_resistance)
    if (not all(map(isfinite, values)) or min(values[:3]) <= 0 or min(values[3:]) < 0
        or unit.branch_resistance.keys() - racks.keys()
        or any(not isfinite(value) or value <= 0 for value in unit.branch_resistance.values())):
      raise ValueError(f"Supply {name} needs valid fan ratings and resistance for reachable racks.")
    conductances = {rack: 1 / sqrt(value) for rack, value in unit.branch_resistance.items()}
    grouped = set()
    for group in unit.shared_branches:
      members = set(group.racks)
      if (not members or len(members) != len(group.racks) or members - racks.keys() or members & grouped
          or not isfinite(group.resistance) or group.resistance < 0):
        raise ValueError(f"Supply {name} needs disjoint shared branch outlets and nonnegative resistance.")
      grouped.update(members)
      available = members & conductances.keys()
      branch_total = sum(conductances[rack] for rack in sorted(available))
      reduction = sqrt(1 + group.resistance * branch_total**2)
      for rack in available:
        conductances[rack] /= reduction
    total = sum(conductances.values()) + unit.bypass_conductance
    fan_pressure = unit.static_limit_pa / (1 + unit.common_resistance * total**2)
    pressure = min(fan_pressure, (unit.rated_flow_m3_s / total)**2) if total else 0.
    branch = {rack: coefficient * sqrt(pressure) for rack, coefficient in conductances.items()}
    direct_bypass = unit.bypass_conductance * sqrt(pressure)
    for rack, flow in branch.items():
      directed[rack] += flow
    flow = sum(branch.values()) + direct_bypass
    common_loss = unit.common_resistance * flow**2
    fan_results[name] = {
      "flow_m3_s": flow, "required_static_pa": pressure + common_loss,
      "manifold_static_pa": pressure, "common_loss_pa": common_loss,
      "shared_branch_loss_pa": {group.name: group.resistance * sum(branch.get(rack, 0.) for rack in group.racks)**2
                                for group in unit.shared_branches},
      "direct_bypass_m3_s": direct_bypass, "branch_flow_m3_s": branch, "static_limit_pa": unit.static_limit_pa,
    }

  fresh = {name: min(directed[name], rack.flow_m3_s * (1 - rack.minimum_recirculation)) for name, rack in racks.items()}
  captured = sum(fresh.values())
  supplied = sum(unit["flow_m3_s"] for unit in fan_results.values())
  total_heat = other_room_kw + sum(rack.sensible_kw for rack in racks.values())
  sensible_capacity = sum(unit.sensible_capacity_kw for unit in supplies.values())
  for name, fan in fan_results.items():
    fan["coil_load_kw"] = total_heat * fan["flow_m3_s"] / supplied if supplied else 0.
    fan["sensible_capacity_kw"] = supplies[name].sensible_capacity_kw
  maintainable = (supplied > 0 and (captured > 0 or total_heat == 0)
                  and all(fan["coil_load_kw"] <= fan["sensible_capacity_kw"] + 1e-8 for fan in fan_results.values()))
  hot_c = (supply_c + total_heat / (captured * transported) if captured else supply_c) if maintainable else None
  return_c = supply_c + total_heat / (supplied * transported) if maintainable else None
  results = {}
  for name, rack in racks.items():
    recirculation = rack.flow_m3_s - fresh[name]
    inlet = (fresh[name] * supply_c + recirculation * hot_c) / rack.flow_m3_s if hot_c is not None else None
    exhaust = inlet + rack.sensible_kw / (transported * rack.flow_m3_s) if inlet is not None else None
    humidity = _vapor_pressure_pa(dew_point_c) / _vapor_pressure_pa(inlet) if inlet is not None and inlet <= 65 else None
    results[name] = {
      "sensible_kw": rack.sensible_kw, "rack_fan_m3_s": rack.flow_m3_s, "directed_supply_m3_s": directed[name],
      "captured_supply_m3_s": fresh[name], "recirculated_m3_s": recirculation,
      "recirculated_fraction": recirculation / rack.flow_m3_s, "inlet_c": inlet, "exhaust_c": exhaust,
      "inlet_relative_humidity": humidity,
    }
  bypass = supplied - captured
  exhaust_pool_kw = None if hot_c is None else transported * sum(
    rack.flow_m3_s * (results[name]["exhaust_c"] - hot_c) for name, rack in racks.items()
  ) + other_room_kw
  return {
    "maintains_supply_setpoint": maintainable, "supplied_m3_s": supplied, "captured_m3_s": captured,
    "bypass_m3_s": bypass, "mixed_hot_c": hot_c, "mixed_return_c": return_c,
    "sensible_load_kw": total_heat, "sensible_capacity_kw": sensible_capacity,
    "energy_residual_kw": None if return_c is None else transported * supplied * (return_c - supply_c) - total_heat,
    "hot_pool_residual_kw": exhaust_pool_kw, "racks": results, "fans": fan_results,
  }
