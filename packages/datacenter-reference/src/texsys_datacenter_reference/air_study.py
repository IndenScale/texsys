"""Check installed airflow, sensible heat, humidity and local air-unit redundancy."""

import csv

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_hvac_dynamics.room_airflow import RackAirflow, SupplyAirflow, room_airflow

from .air_routing import MODEL as AIR
from .checks import load_design
from .fws_network_study import _rated
from .spatial_design import (
  MODELS,
  artifact,
  missing_input,
  model_report,
  number,
  read_versioned,
  source_path,
  staged_json,
)
from .tcs_study import MODEL as TCS


MODEL = f"{MODELS}/air-study.json"


def _parameters(raw):
  values = _positive(
    raw,
    (
      "supply_c",
      "dew_point_c",
      "minimum_relative_humidity",
      "maximum_relative_humidity",
      "minimum_pipe_dew_margin_k",
      "maximum_mixed_return_c",
      "rack_fan_flow_m3_s",
      "minimum_rack_recirculation",
      "maximum_rack_recirculation",
      "other_room_sensible_kw",
      "candidate_external_static_pa",
      "equivalent_distribution_resistance",
      "equivalent_path_resistance_per_m",
      "unit_bypass_conductance",
    ),
    zero={"minimum_rack_recirculation", "other_room_sensible_kw", "unit_bypass_conductance"},
  )
  if not (
    values["minimum_relative_humidity"] < values["maximum_relative_humidity"] <= 1
    and values["minimum_rack_recirculation"] < values["maximum_rack_recirculation"] < 1
    and values["dew_point_c"] < values["supply_c"] < values["maximum_mixed_return_c"]
  ):
    raise ValueError("Check supply and return temperatures, humidity and recirculation limits.")
  values["air"] = _positive(raw["air"], ("density_kg_m3", "cp_kj_kg_k"))
  return values


def _positive(rows, fields, zero=()):
  return {key: number(rows[key], key, zero=key in zero) for key in fields}


def _criteria(design):
  (row,) = [row for row in design.basis["requirements"] if row["id"] == "GB-5.1.1"]
  values = _positive(row["parameters"], ("inlet_min_c", "inlet_max_c"))
  return {**values, "source_requirement": row["id"]}


def _resistance(coefficient, area, density):
  return 0.5 * density * coefficient / area**2


def _unit(record, branches, geometry, settings, equipment):
  cfg, ret, riser = (geometry["parameters"][name] for name in ("supply", "return_grille", "rack_riser"))
  rho = settings["air"]["density_kg_m3"]
  opening = record["supply_area_m2"]
  neck = record["neck_area_m2"]
  width, height = cfg["duct_width_m"], cfg["duct_height_m"]
  diameter = 2 * width * height / (width + height)
  common = _resistance(cfg["elbow_k"], neck, rho)
  common += _resistance(
    cfg["terminal_k"] + cfg["roughness_factor"] * record["supply_length_m"] / diameter, opening, rho
  )
  common += _resistance(ret["local_loss_k"], record["return_area_m2"], rho)
  losses = {}
  for name, connection in branches.items():
    back = geometry["racks"][name]
    local = _resistance(
      riser["turn_k"] + riser["roughness_factor"] * back["upflow_length_m"] / back["hydraulic_diameter_m"],
      back["flow_area_m2"],
      rho,
    )
    path = (
      settings["equivalent_distribution_resistance"]
      + settings["equivalent_path_resistance_per_m"] * connection["capture_distance_m"]
    )
    losses[name] = {"riser_pa_per_m6_s2": local, "distribution_pa_per_m6_s2": path}
  branch = {name: sum(row.values()) for name, row in losses.items()}
  flow = min(min(neck, opening) * cfg["maximum_velocity_m_s"], _rated(equipment, "supply_airflow", "m3/s"))
  capacity = _rated(equipment, "sensible_capacity", "kW")
  return SupplyAirflow(
    flow, capacity, settings["candidate_external_static_pa"], branch, settings["unit_bypass_conductance"], common
  ), losses


def _prepare(pod, air, materials, settings, instance):
  units, rack_losses = {}, {}
  rating = {"parameters": materials, "racks": air["racks"]}
  groups = {}
  for side, links in air["binding"].items():
    families = {}
    for rack, data in links.items():
      families.setdefault(data["unit"], {})[rack] = data
    for name, branches in sorted(families.items()):
      roster = tuple(sorted(branches))
      groups.setdefault(roster, {})[side] = name
      units[name], rack_losses[name] = _unit(air["units"][name], branches, rating, settings, instance.children[name])
  it_kw = _rated(instance, "it_load_kw", "kW")
  return {"pod": pod, "units": units, "pairs": list(groups.values()), "rack_losses": rack_losses, "it_kw": it_kw}


def _duties(tcs, models):
  unit_names = sorted({unit for model in models.values() for unit in model["units"]})
  alternatives = {"normal": ("normal", set(), "A", None)}
  for unit in unit_names:
    alternatives[f"unit-off:{unit}"] = ("normal", set(), "A", unit)
  for side, survivor in (("A", "B"), ("B", "A")):
    alternatives[f"electrical-train-off:{side}"] = (f"local-cdu-off:{side}", {side}, survivor, None)
  for name in tcs["cases"]:
    if name.startswith("fws-peak:"):
      off = name.rsplit(":", 1)[-1]
      alternatives[f"combined:{name}"] = (name, {off}, "A" if off == "B" else "B", None)
  return alternatives


def _solve(model, support, settings, criteria, locked, missing_sides, preferred, missing_unit):
  pod = model["pod"]
  sides = (preferred, "B" if preferred == "A" else "A")
  active = []
  for pair in model["pairs"]:
    unit = next(
      (
        pair[side]
        for side in sides
        if side not in missing_sides and pair[side] != missing_unit and f"{pod}/{pair[side]}" not in locked
      ),
      None,
    )
    if unit is not None:
      active.append(unit)
  load = model["it_kw"] / len(support["racks"])
  rack_air = {
    key: RackAirflow(
      settings["rack_fan_flow_m3_s"], max(0.0, load - row["requested_kw"]), settings["minimum_rack_recirculation"]
    )
    for key, row in support["racks"].items()
  }
  report = room_airflow(
    rack_air,
    {key: model["units"][key] for key in active},
    density_kg_m3=settings["air"]["density_kg_m3"],
    cp_kj_kgk=settings["air"]["cp_kj_kg_k"],
    supply_c=settings["supply_c"],
    other_room_kw=settings["other_room_sensible_kw"],
    dew_point_c=settings["dew_point_c"],
  )
  temperatures = [row["tcs_supply_c"] for row in support["racks"].values() if "tcs_supply_c" in row]
  cold_water = min(temperatures, default=None)
  margin = cold_water - settings["dew_point_c"] if cold_water is not None else None
  for key, row in report["racks"].items():
    inlet, humidity = row["inlet_c"], row["inlet_relative_humidity"]
    row["checks"] = {
      "inlet-temperature": inlet is not None and criteria["inlet_min_c"] <= inlet <= criteria["inlet_max_c"],
      "relative-humidity": humidity is not None
      and settings["minimum_relative_humidity"] <= humidity <= settings["maximum_relative_humidity"],
      "recirculation": row["recirculated_fraction"] <= settings["maximum_rack_recirculation"],
      "heat-split": support["racks"][key]["requested_kw"] <= load + 1e-8,
    }
  checks = {
    "spare-coverage": len(active) == len(model["pairs"]),
    "sensible-capacity": report["maintains_supply_setpoint"],
    "return-temperature": report["mixed_return_c"] is not None
    and report["mixed_return_c"] <= settings["maximum_mixed_return_c"],
    "upstream-liquid-duty": support["status"] == "pass",
    "condensation-margin": margin is not None and margin >= settings["minimum_pipe_dew_margin_k"],
    "energy-balance": report["energy_residual_kw"] is not None
    and abs(report["energy_residual_kw"]) < 1e-8
    and abs(report["hot_pool_residual_kw"]) < 1e-8,
  }
  passed = all(checks.values()) and all(all(row["checks"].values()) for row in report["racks"].values())
  return {
    **report,
    "active_units": active,
    "checks": checks,
    "coldest_active_tcs_c": cold_water,
    "pipe_dew_margin_k": margin,
    "status": "pass" if passed else "fail",
  }


def build_air_study(context):
  path = source_path(context, "air-study.json")
  if not path.is_file():
    return missing_input(context, path, "air-study")
  source = read_versioned(path)
  if source.get("state") != "assumed" or not source.get("basis"):
    raise ValueError("Specify the assumed airflow and thermal basis.")
  settings = _parameters(source)
  air, tcs = staged_json(context, AIR), staged_json(context, TCS)
  design = load_design(path.parent, context.root)
  if design.diagnostics:
    return model_report(context, "air-study", {}, design.diagnostics)
  criteria = _criteria(design)
  models = {
    name: _prepare(name, content, air["parameters"], settings, design.instances[name])
    for name, content in air["pods"].items()
  }
  locked = set(source["unavailable_units"])
  known = {f"{pod}/{key}" for pod, row in models.items() for key in row["units"]}
  if locked - known:
    raise ValueError("Each lockout must name a placed air-conditioning unit as pod/equipment.")
  cases = {}
  for name, (tcs_case, missing_sides, preferred, unit) in sorted(_duties(tcs, models).items()):
    upstream = tcs["cases"][tcs_case]["pods"]
    pods = {
      pod: _solve(model, upstream[pod], settings, criteria, locked, missing_sides, preferred, unit)
      for pod, model in models.items()
    }
    cases[name] = {
      "tcs_case": tcs_case,
      "pods": pods,
      "failed_pods": [pod for pod, row in pods.items() if row["status"] != "pass"],
    }
  evidence = {
    pod: {
      name: {
        "common_pa_per_m6_s2": unit.common_resistance,
        "flow_ceiling_m3_s": unit.rated_flow_m3_s,
        "branch_loss": model["rack_losses"][name],
      }
      for name, unit in model["units"].items()
    }
    for pod, model in models.items()
  }
  result = model_report(
    context,
    "air-study",
    {
      "basis": source["basis"],
      "settings": settings,
      "criteria": criteria,
      "cases": cases,
      "air_digest": file_digest(context.build_path / AIR),
      "tcs_digest": file_digest(context.build_path / TCS),
      "resistances": evidence,
    },
  )
  output = context.build_path / "schedules/aidc-air-rack-duty.csv"
  output.parent.mkdir(parents=True, exist_ok=True)
  with output.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(
      (
        "case",
        "pod",
        "rack",
        "direct_supply_m3_s",
        "recirculated_fraction",
        "inlet_c",
        "outlet_c",
        "relative_humidity",
        "pass",
      )
    )
    for case, record in cases.items():
      for pod, row in record["pods"].items():
        for rack, duty in row["racks"].items():
          table.writerow(
            (
              case,
              pod,
              rack,
              duty["directed_supply_m3_s"],
              duty["recirculated_fraction"],
              duty["inlet_c"],
              duty["exhaust_c"],
              duty["inlet_relative_humidity"],
              all(duty["checks"].values()),
            )
          )
  return TargetResult(result.status, (*result.artifacts, artifact(context, "air-rack-duty", output)))


def build_air_check(context):
  model = staged_json(context, MODEL)
  issues = []
  for case, record in model["cases"].items():
    for pod, row in record["pods"].items():
      failed = [check for check, passed in row["checks"].items() if not passed]
      racks = [key for key, v in row["racks"].items() if not all(v["checks"].values())]
      if failed or racks:
        issues.append(
          {
            "rule": "AIDC_AIR_ENVIRONMENT",
            "status": "fail",
            "objects": [case, pod, *failed, *racks],
            "message": f"{case}/{pod}: check pod duty {', '.join(failed) or 'pass'}, rack inlets {', '.join(racks) or 'pass'}.",
          }
        )
  return model_report(
    context,
    "air-check",
    {"issues": issues, "cases_checked": len(model["cases"]), "study_digest": file_digest(context.build_path / MODEL)},
    issues,
  )
