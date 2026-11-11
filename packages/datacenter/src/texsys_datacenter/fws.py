"""Bind shared water topology and leak interlocks to building spaces."""

from collections import defaultdict
from pathlib import Path

from texsys.formal import _load_json
from texsys_hvac_dynamics.facility_hydraulics import FacilityWaterHydraulics
from texsys_hvac_dynamics.facility_water import FacilityWaterNetwork

from .fws_branches import FwsBranches
from .unit import _identifiers


class BuildingWater:
  def __init__(self, path: Path, rejection: dict, zones: dict, levels: dict, unit: dict, *, check_branch_geometry=False):
    config = _load_json(path.read_text(encoding="utf-8"))
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1:
      raise ValueError("Facility water study requires schema_version 1.")
    if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
      raise ValueError("Facility water study requires assumed inputs and a basis.")
    self.basis = config["basis"]
    trains = config["trains"]
    if trains.keys() != rejection.keys():
      raise ValueError("FWS trains must match the building's shared heat-rejection groups.")
    self.network = FacilityWaterNetwork(trains)
    self.branches = None
    if (path.parent / "fws-branches.study.json").is_file():
      if "hydraulics" not in config:
        raise ValueError("FWS branches require upstream pipe and pump hydraulics.")
      self.branches = FwsBranches(path.parent, config, self.network, rejection, zones, levels, unit,
                                  check_geometry=check_branch_geometry)
    self.hydraulics = (FacilityWaterHydraulics(self.network, config["hydraulics"],
                                              distributions=self.branches.distributions if self.branches else None)
                       if "hydraulics" in config else None)
    if {terminal for _, terminal in self.network.paths} != levels.keys():
      raise ValueError("FWS terminals must cover the declared building levels.")
    self.risers = {pipe.riser for pipe in self.network.segments.values() if pipe.riser is not None}
    if self.risers - {key for key, zone in zones.items() if zone["role"] == "service-riser"}:
      raise ValueError("FWS risers must refer to declared service-riser reserves.")
    self.heat_per_flow = {}
    for name, group in rejection.items():
      conditions = {(data["facility"]["specific_heat_kj_kgk"], data["facility"]["supply_c"],
                     data["facility"]["return_c"]) for data in unit["cdus"].values()
                    if data["plant"] in group["unit_plants"]}
      if len(conditions) != 1:
        raise ValueError(f"FWS train {name} needs common CDU facility-side design conditions.")
      cp, supply, return_ = next(iter(conditions))
      self.heat_per_flow[name] = cp * (return_ - supply)
    self.leak_zones = config["leak_zones"]
    self.valves = {}
    self.detectors = set()
    for name, zone in self.leak_zones.items():
      detector = zone["detector"]
      if not isinstance(detector, str) or not detector.strip():
        raise ValueError(f"Leak zone {name} needs a detector identity.")
      self.detectors.add(detector)
      isolation = zone["isolation"]
      pipes = set(isolation.values())
      faulted = _identifiers(zone["faulted_segments"], f"{name}.faulted_segments")
      if (not faulted or not faulted <= pipes or pipes - self.network.segments.keys()
          or len(pipes) != len(isolation)
          or {self.network.segments[pipe].leg for pipe in pipes} != {"supply", "return"}):
        raise ValueError(f"Leak zone {name} needs faulted segments and supply and return isolation valves.")
      for valve, pipe in isolation.items():
        if not valve.strip() or self.valves.setdefault(valve, pipe) != pipe:
          raise ValueError(f"Conflicting FWS isolation valve {valve!r}.")

  def evaluate(self, case, demand_kw, source_kw, terminal_kw, *, demand_kw_by_unit=None):
    segments = _identifiers(case.get("unavailable_fws_segments", []), "unavailable_fws_segments")
    lost_risers = _identifiers(case.get("unavailable_risers", []), "unavailable_risers")
    leaks = _identifiers(case.get("leak_zones", []), "leak_zones")
    lost_detectors = _identifiers(case.get("unavailable_detectors", []), "unavailable_detectors")
    stuck_valves = _identifiers(case.get("stuck_open_valves", []), "stuck_open_valves")
    pumps = _identifiers(case.get("unavailable_fws_pumps", []), "unavailable_fws_pumps")
    speed_limits = case.get("fws_pump_speed_limits", {})
    lost_branches = _identifiers(case.get("unavailable_fws_branch_segments", []), "unavailable_fws_branch_segments")
    units_by_level, blocked = None, None
    if lost_branches or demand_kw_by_unit is not None:
      if self.branches is None or lost_branches - self.branches.routes.keys():
        raise ValueError("FWS scenario refers to an unknown floor branch segment.")
      units_by_level = {level: tuple(next(iter(self.branches.outlets[level].values()))) for level in demand_kw}
      lost_by_floor = defaultdict(set)
      for key in lost_branches:
        route = self.branches.routes[key]
        lost_by_floor[route["train"], route["level"]].add(key.split("/", 1)[1])
      blocked = {pair: {unit for unit, path in self.branches.distributions[pair].paths.items() if loss.intersection(path)}
                 for pair, loss in lost_by_floor.items()}
    if not isinstance(speed_limits, dict):
      raise ValueError("FWS pump speed limits must be keyed by train.")
    if (lost_risers - self.risers or leaks - self.leak_zones.keys() or lost_detectors - self.detectors
        or stuck_valves - self.valves.keys()):
      raise ValueError("FWS scenario refers to unknown risers, leak zones, detectors or valves.")
    segments.update(pipe.id for pipe in self.network.segments.values() if pipe.riser in lost_risers)
    actions = {}
    for name in sorted(leaks):
      zone = self.leak_zones[name]
      detected = zone["detector"] not in lost_detectors
      commanded = set(zone["isolation"]) if detected else set()
      closed = commanded - stuck_valves
      # A leaking pipe is unavailable for rated service even if isolation fails.
      segments.update(zone["faulted_segments"])
      segments.update(self.valves[valve] for valve in closed)
      actions[name] = {"detector": zone["detector"], "detected": detected,
                       "commanded_closed": sorted(commanded), "confirmed_closed": sorted(closed),
                       "isolation_confirmed": closed == zone["isolation"].keys()}
    allocation = self.network.allocate(demand_kw, source_kw, terminal_kw, self.heat_per_flow, segments,
                                       hydraulics=self.hydraulics, unavailable_pumps=pumps, pump_speed_limits=speed_limits,
                                       units_by_level=units_by_level, unavailable_outlets=blocked,
                                       demand_kw_by_unit=demand_kw_by_unit)
    if blocked is not None:
      allocation["unavailable_branch_segments"] = sorted(lost_branches)
      allocation["blocked_units_by_train_level"] = {f"{level}/{train}": sorted(units)
                                                    for (train, level), units in sorted(blocked.items())}
    return {**allocation, "unavailable_segments": sorted(segments), "interlocks": actions}
