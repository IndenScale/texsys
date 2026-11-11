"""Compile a hybrid room study from its SDL connections and declared ratings."""

from collections import defaultdict
from dataclasses import asdict
import math
from pathlib import Path

from texsys.formal import _load_json, resolve_sdl_imports
from texsys.library import library_entries
from texsys.sdl_catalog import read_sdl_catalog
from texsys_electrical import evaluate_power
from texsys_hvac_dynamics.hybrid import CduRating, LiquidLoop, split_it_heat

from .tcs import prepare_tcs, tcs_solver

def _number(value, label: str, *, minimum: float = 0, inclusive: bool = False) -> float:
  if (type(value) not in {int, float} or not math.isfinite(value)
      or value < minimum or (not inclusive and value == minimum)):
    raise ValueError(f"Invalid numeric design input: {label}.")
  return value


def _identifiers(values, label: str) -> set[str]:
  if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
    raise ValueError(f"{label} must be an array of nonempty identifiers.")
  result = set(values)
  if len(result) != len(values):
    raise ValueError(f"{label} contains duplicate identifiers.")
  return result


def _topology(root: Path, source: Path):
  catalog = read_sdl_catalog(source, root)
  documents = catalog.documents
  if catalog.diagnostics or "system.sdl.json" not in documents:
    raise ValueError("A unit study requires valid SDL sources and system.sdl.json.")
  design = documents["system.sdl.json"]
  system = design["components"][design["root"]]
  aliases, errors = resolve_sdl_imports(documents, library_entries(set(documents)), "system.sdl.json")
  if errors:
    raise ValueError(f"Unresolved unit imports: {errors}.")
  components = {**design["components"],
                **{alias: documents[file]["components"][name] for alias, (file, name) in aliases.items()}}
  ports = {instance: components[item["component"]].get("ports", {})
           for instance, item in system["instances"].items()}
  models = {}
  for instance, item in system["instances"].items():
    entry, definition = aliases.get(item["component"], ("system.sdl.json", item["component"]))
    manifest = catalog.modules.get(entry)
    if not manifest or not entry.startswith("@hvac-equipment/"):
      continue
    assets = {record["kind"]: record["path"] for record in manifest["assets"].values()}
    folder = catalog.files[entry].parent
    model = _load_json((folder / assets["behavior"]).read_text(encoding="utf-8"))
    spec = _load_json((folder / assets["specification"]).read_text(encoding="utf-8"))
    behavior = documents[entry]["components"][definition]["behavior"]
    parameters = {**behavior["parameters"], **item.get("parameters", {})}
    if (model["state"] != "assumed" or spec["state"] != "assumed" or behavior["reference"] != assets["behavior"]
        or {key: value["unit"] for key, value in parameters.items()} != model["parameters"]
        or spec["interfaces"].keys() != ports[instance].keys()):
      raise ValueError(f"Cooling module behavior or interfaces do not match the manifest for {instance}.")
    models[instance] = {"entry": entry, "module_version": manifest["version"], "model": model["model"],
                        "parameters": parameters, "specification": spec}
  power = []
  by_id, edge_ports = {}, {}
  for edge in system.get("connections", []):
    start, end = edge["from"].split(".")[0], edge["to"].split(".")[0]
    if "id" in edge:
      by_id[edge["id"]] = (start, end)
      edge_ports[edge["id"]] = (edge["id"], edge["from"], edge["to"])
    port = edge["from"].split(".")[1]
    if ports[start][port]["kind"] == "ac-power":
      power.append((start, end))
  nets = {name: (net["kind"], tuple(by_id[edge] for edge in net["connections"]))
          for name, net in system.get("nets", {}).items()}
  net_edges = {name: tuple(edge_ports[key] for key in net["connections"])
               for name, net in system.get("nets", {}).items() if net["kind"] in ("tcs", "room-air")}
  return ports, tuple(power), nets, models, net_edges


def _reachable(links, start: str, removed: set[str]) -> set[str]:
  if start in removed:
    return set()
  graph = defaultdict(list)
  for parent, child in links:
    if parent not in removed and child not in removed:
      graph[parent].append(child)
  seen = {start}
  pending = [start]
  while pending:
    children = set(graph[pending.pop()]) - seen
    seen.update(children)
    pending.extend(children)
  return seen


def _closed_path(nets, name: str, kind: str, start: str, ends: set[str], removed: set[str]) -> bool:
  if name not in nets or nets[name][0] != kind:
    raise ValueError(f"Network {name!r} must be a declared {kind} net.")
  links = nets[name][1]
  outward = _reachable(links, start, removed)
  inward = _reachable(((b, a) for a, b in links), start, removed)
  return ends <= outward & inward


def _model_number(models, identity, parameter, record, legacy, unit):
  if identity not in models:
    return _number(record[legacy], f"{identity}.{legacy}")
  binding = models[identity]["parameters"][parameter]
  if legacy in record or binding["unit"] != unit or binding["state"] not in ("known", "assumed"):
    raise ValueError(f"{identity}.{parameter} requires a resolved SDL parameter in {unit}.")
  return _number(binding["value"], f"{identity}.{parameter}")


def _cdu_rating(identity, record: dict, models) -> CduRating:
  return CduRating(LiquidLoop(**record["facility"]), LiquidLoop(**record["technology"]),
                   _model_number(models, identity, "nameplate_capacity", record, "nameplate_kw", "kW"),
                   _model_number(models, identity, "minimum_approach", record, "min_approach_c", "K"))


def _equipment_layout(source, models):
  path = source / "cooling-equipment.study.json"
  if not path.is_file():
    return {}
  config = _load_json(path.read_text(encoding="utf-8"))
  if type(config["schema_version"]) is not int or config["schema_version"] != 1 or config["state"] != "assumed":
    raise ValueError("Cooling equipment placement requires assumed schema version 1 input.")
  if config["placements_m"].keys() != models.keys():
    raise ValueError("Cooling equipment placements must match the installed module instances.")
  result = {}
  for identity, origin in config["placements_m"].items():
    if len(origin) != 3 or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in origin):
      raise ValueError(f"Invalid installation origin for {identity}.")
    item = models[identity]
    spec = item["specification"]
    support = spec.get("properties", {}).get("installation_support")
    air = spec.get("properties", {}).get("installation_air")
    result[identity] = {
      "entry": item["entry"], "module_version": item["module_version"], "model": item["model"],
      "parameters": item["parameters"], "origin_m": origin, "envelope_m": spec["envelope_m"],
      "ports_m": {name: [a + b for a, b in zip(origin, frame["position_m"], strict=True)]
                  for name, frame in spec["interfaces"].items()},
      "port_normals": {name: frame["normal"] for name, frame in spec["interfaces"].items()},
      **({"installation_support": support} if support is not None else {}),
      **({"installation_air": air} if air is not None else {}),
    }
  return result


def evaluate_unit(root: Path, source: Path, *, powered_by_case: dict[str, set[str]] | None = None) -> dict:
  design = _load_json((source / "unit.study.json").read_text(encoding="utf-8"))
  if type(design.get("schema_version")) is not int or design["schema_version"] != 1:
    raise ValueError("Unit study schema_version must be 1.")
  if design.get("state") != "assumed" or not isinstance(design.get("basis"), str) or not design["basis"].strip():
    raise ValueError("The concept study requires explicit assumed inputs and a design basis.")
  ports, links, nets, models, net_edges = _topology(root, source)
  instances = ports.keys()
  racks, power, cooling = (design[name] for name in ("racks", "power", "cooling"))
  plants, cdus, air_units = (cooling[name] for name in ("plants", "cdus", "air_units"))
  if not all((racks, plants, cdus, air_units)):
    raise ValueError("Hybrid unit needs racks, facility plants, CDUs and air units.")
  sources = _identifiers(power["sources"], "power.sources")
  capacities, equipment_kw = power["capacities_kw"], dict(power["equipment_kw"])
  devices = {key: value for key, value in models.items() if key not in racks}
  if devices.keys() - (cdus.keys() | air_units.keys()) or devices.keys() & equipment_kw.keys():
    raise ValueError("Cooling module electrical ratings belong in SDL and must bind to CDUs or air units.")
  for name in devices:
    equipment_kw[name] = _model_number(devices, name, "electrical_input", {}, "", "kW")
  room_devices = set(racks) | plants.keys() | cdus.keys() | air_units.keys()
  if len(room_devices) != sum(map(len, (racks, plants, cdus, air_units))):
    raise ValueError("Rack, plant, CDU and air-unit identities must be distinct.")
  if set(equipment_kw) != room_devices - racks.keys() or (room_devices | capacities.keys()) - instances:
    raise ValueError("Power ratings must bind to SDL instances and cover the cooling equipment.")
  demand = {**equipment_kw, **{rack: config["it_kw"] for rack, config in racks.items()}}
  rack_heat = {rack: split_it_heat(data["it_kw"], data["liquid_fraction"]) for rack, data in racks.items()}
  q_liquid = sum(load[0] for load in rack_heat.values())
  q_air_it = sum(load[1] for load in rack_heat.values())
  other_room = _number(cooling["other_room_heat_kw"], "other_room_heat_kw", inclusive=True)
  q_air = q_air_it + other_room
  plant_limits = {key: _number(value, f"{key}.capacity_kw") for key, value in plants.items()}
  if any(models.get(key, {}).get("model", "two-loop-rated-point") != "two-loop-rated-point" for key in cdus):
    raise ValueError("CDU instances need a two-loop rated-point model.")
  if any(models.get(key, {}).get("model", "sensible-air-rated-point") != "sensible-air-rated-point" for key in air_units):
    raise ValueError("Precision air instances need a sensible-air rated-point model.")
  ratings = {key: _cdu_rating(key, value, models) for key, value in cdus.items()}
  air_limits = {key: (_model_number(models, key, "sensible_capacity", value, "capacity_kw", "kW"),
                      _model_number(models, key, "supply_airflow", value, "flow_m3_s", "m3/s"))
                for key, value in air_units.items()}
  air = cooling["room_air"]
  air_density = _number(air["density_kg_m3"], "air.density_kg_m3")
  air_cp = _number(air["specific_heat_kj_kgk"], "air.specific_heat_kj_kgk")
  temperatures = [air[key] for key in ("supply_c", "max_return_c", "dew_point_c", "min_surface_c")]
  if not all(type(value) in {int, float} and math.isfinite(value) for value in temperatures):
    raise ValueError("Room air temperatures must be finite numbers.")
  supply, max_return, dew_point, min_surface = temperatures
  if max_return <= supply:
    raise ValueError("Maximum room return temperature must exceed supply.")

  for cdu, record in cdus.items():
    if record["plant"] not in plants:
      raise ValueError(f"CDU {cdu} refers to an unknown facility plant.")
    if not _closed_path(nets, record["fws_net"], "fws", cdu, {record["plant"]}, set()):
      raise ValueError(f"CDU {cdu} has an incomplete FWS loop.")
    if not _closed_path(nets, record["tcs_net"], "tcs", cdu, set(racks), set()):
      raise ValueError(f"CDU {cdu} has an incomplete TCS loop to the declared racks.")
  for unit, record in air_units.items():
    if not _closed_path(nets, record["net"], "room-air", unit, set(racks), set()):
      raise ValueError(f"Air unit {unit} has an incomplete supply and return path.")

  equipment = _equipment_layout(source, devices)
  tcs_model = prepare_tcs(source, cdus, ratings, models, equipment, net_edges)
  technology = tcs_solver(tcs_model) if tcs_model is not None else None
  liquid_demand = {name: load[0] for name, load in rack_heat.items()}

  def cooling_at(inactive: set[str], *, passive_it: bool = False) -> dict:
    connected = inactive - racks.keys() if passive_it else inactive
    available_by_plant = defaultdict(float)
    active_cdus = []
    circulating_cdus = []
    for cdu, record in cdus.items():
      plant = record["plant"]
      # Unpowered IT cold plates can still carry coolant from a powered CDU.
      if not _closed_path(nets, record["tcs_net"], "tcs", cdu, set(racks), connected):
        continue
      circulating_cdus.append(cdu)
      if _closed_path(nets, record["fws_net"], "fws", cdu, {plant}, connected):
        available_by_plant[plant] += ratings[cdu].available_kw
        active_cdus.append(cdu)
    liquid_by_plant = {plant: min(available_by_plant[plant], capacity) for plant, capacity in plant_limits.items()}
    available_liquid = sum(liquid_by_plant.values())
    active_air = [unit for unit, record in air_units.items()
                  if _closed_path(nets, record["net"], "room-air", unit, set(racks), connected)]
    flow = sum(air_limits[unit][1] for unit in active_air)
    available_air = min(sum(air_limits[unit][0] for unit in active_air),
                        flow * air_density * air_cp * (max_return - supply))
    return_c = supply + q_air / (flow * air_density * air_cp) if flow else None
    result = {
      "checks": {"liquid-capacity": available_liquid + 1e-8 >= q_liquid,
                 "air-capacity": available_air + 1e-8 >= q_air,
                 "room-surface-above-dew-point": min_surface > dew_point},
      "active_cdus": sorted(active_cdus), "active_air_units": sorted(active_air),
      "circulating_cdus": sorted(circulating_cdus),
      "liquid_available_kw": available_liquid, "air_available_kw": available_air,
      "liquid_available_by_plant_kw": liquid_by_plant, "air_return_estimate_c": return_c,
    }
    if technology is not None:
      source_kw = {
        cdu: ratings[cdu].available_kw * liquid_by_plant[record["plant"]] / available_by_plant[record["plant"]]
        if cdu in active_cdus and available_by_plant[record["plant"]] > 0 else 0.
        for cdu, record in cdus.items()
      }
      # A zero-rated active CDU can circulate without removing heat.
      distribution = technology.evaluate(liquid_demand, source_kw, unavailable_pumps={
        pump for cdu, bank in tcs_model["banks"].items() if cdu not in circulating_cdus for pump in bank["pumps"]
      })
      result["tcs"] = distribution
      result["checks"]["liquid-capacity"] = not distribution["shortage_kw_by_terminal"]
    return result

  if powered_by_case is not None:
    if any(powered - room_devices for powered in powered_by_case.values()):
      raise ValueError("External power availability must bind to the unit's SDL loads.")
    return {name: cooling_at(room_devices - powered, passive_it=True) for name, powered in powered_by_case.items()}

  results = {}
  cases = design["scenarios"]
  if not isinstance(cases, dict) or not cases:
    raise ValueError("A unit study needs explicit scenarios.")
  for name, case in sorted(cases.items()):
    expected = case["expected"]
    if expected not in ("pass", "fail"):
      raise ValueError(f"Scenario {name} needs a pass or fail expectation.")
    unavailable = _identifiers(case["unavailable"], f"{name}.unavailable")
    if unavailable - instances:
      raise ValueError(f"Scenario {name} refers to unknown instances: {sorted(unavailable - instances)}.")
    service = evaluate_power(links, sources, capacities, demand, unavailable & (capacities.keys() | demand.keys()))
    thermal = cooling_at(unavailable | set(service.unserved))
    checks = {**thermal["checks"], "rack-power": set(racks) <= service.paths.keys(),
              "power-capacity": not service.overloaded_kw}
    results[name] = {**thermal, "status": "pass" if all(checks.values()) else "fail",
                     "expected": expected, "checks": checks, "power": asdict(service)}

  return {
    "schema_version": 1, "state": design["state"], "basis": design["basis"], "power_sources": sorted(sources),
    "power_model": {"links": links, "ratings_kw": capacities, "demand_kw": demand, "it_loads": sorted(racks)},
    "heat_budget_kw": {"it": sum(demand[rack] for rack in racks), "liquid_it": q_liquid,
                       "air_it": q_air_it, "other_room": other_room, "total_air": q_air},
    "rack_heat_kw": {rack: {"liquid": load[0], "air": load[1]} for rack, load in rack_heat.items()},
    "room_air": air,
    "equipment_layout": equipment,
    **({"tcs_model": tcs_model} if tcs_model is not None else {}),
    "cdus": {key: {"fws_net": cdus[key]["fws_net"], "tcs_net": cdus[key]["tcs_net"], "plant": cdus[key]["plant"],
                   "facility_ports": {name: port["direction"] for name, port in ports[key].items() if port["kind"] == "fws"},
                   "facility": asdict(rating.facility),
                   "terminal_approach_c": rating.terminal_approach_c, "available_kw": rating.available_kw}
             for key, rating in sorted(ratings.items())},
    "air_units": {key: {"capacity_kw": capacity, "flow_m3_s": flow,
                        "air_connections": net_edges[air_units[key]["net"]],
                        "air_ports": {name: port["direction"] for name, port in ports[key].items() if port["kind"] == "room-air"},
                        "airflow_limited_kw": flow * air_density * air_cp * (max_return - supply)}
                  for key, (capacity, flow) in sorted(air_limits.items())},
    "scenarios": results,
  }
