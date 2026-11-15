"""Trace fiber loss, path diversity, capacity and single-component contingencies."""

import csv
import math

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .spatial_design import artifact, missing_input, model_report, number, read_versioned, source_path, staged_json, vector
from .telecom import MODEL, PLANES


def _optics(settings):
  model = {key: number(settings[key], key, zero=True) for key in ("attenuation_db_per_km", "connector_loss_db",
                                                                  "splice_loss_db", "reserve_db")}
  for key in ("connector_pairs", "splices"):
    if type(settings[key]) is not int or settings[key] < 0:
      raise ValueError(f"{key}: loss budget requires a nonnegative inventory count.")
    model[key] = settings[key]
  tx, receiver = vector([settings["tx_min_dbm"], settings["rx_sensitivity_dbm"]], 2, "transmit/receive dBm")
  return {**model, "available_db": number(tx-receiver, "available passive optical budget")}


def _issue(rule, key, message, *related):
  return {"rule": rule, "status": "fail", "objects": [key, *related], "message": message}


def _loss_report(model, optics, issues):
  cables, tray_report, domains = {}, {}, {}
  cable_area = math.pi*model["cable"]["outside_diameter_m"]**2/4
  for plane, assembly in model["planes"].items():
    cabinet = assembly["cabinet"]
    for key, path in assembly["cables"].items():
      attenuation = path["budget_length_m"]/1000 * optics["attenuation_db_per_km"]
      connector = optics["connector_pairs"]*optics["connector_loss_db"]
      splices = optics["splices"]*optics["splice_loss_db"]
      required = attenuation + connector + splices + optics["reserve_db"]
      remaining = optics["available_db"] - required
      cable_domains = [cabinet["id"], cabinet["power_dependency"], key, path["panel"], *path["runs"]]
      for domain in cable_domains:
        domains.setdefault(domain, []).append(key)
      cables[key] = {"plane": plane, "pod": path["pod"], "basis_length_m": path["budget_length_m"],
                     "fiber_db": attenuation, "connectors_db": connector, "splices_db": splices,
                     "reserve_db": optics["reserve_db"], "required_db": required, "available_db": optics["available_db"],
                     "remaining_db": remaining, "bend_clearance": path["bend_clearance"], "domains": cable_domains,
                     "pass": remaining >= 0 and path["bend_clearance"]}
      if remaining < 0:
        issues.append(_issue("AIDC_TELECOM_LOSS", key, f"{key}: required {required:.3f} dB exceeds the {optics['available_db']:.3f} dB budget."))
      if not path["bend_clearance"]:
        issues.append(_issue("AIDC_TELECOM_BEND", key, f"{key}: adjacent runs cannot fit the declared bend radius."))
    for key, run in assembly["runs"].items():
      area = math.prod(d for d in run["section_m"] if d > 0)
      fill = len(run["cables"])*cable_area/area
      fits = fill <= model["tray"]["usable_fill_fraction"]
      tray_report[key] = {"length_m": run["length_m"], "cables": len(run["cables"]), "fill_fraction": fill,
                          "allowed_fraction": model["tray"]["usable_fill_fraction"], "pass": fits}
      if not fits:
        issues.append(_issue("AIDC_TELECOM_TRAY_FILL", key, f"{key}: tray fill exceeds its declared allowable fraction."))
  return cables, tray_report, domains


def _sizing(cfg, model, issues):
  spec = model["cable"]
  user_links, assigned = {}, {plane: [] for plane in PLANES}
  for name, row in model["allocation"].items():
    user_links.setdefault(row["rack"], {})[row["plane"]] = row["cable"]
    assigned[row["plane"]].append(name)
    if row["last_core"] > spec["fiber_count"]:
      issues.append(_issue("AIDC_TELECOM_CORES", name, f"{name}: rack assignment exceeds the cable fiber count.", row["cable"]))
  demand = number(cfg["rack_demand_gbps"], "rack design demand")
  if set(cfg["switches"]) != set(PLANES):
    raise ValueError("Declare switching capacity for both service planes.")
  total = demand*len(user_links)
  capacity = {}
  for key in PLANES:
    spec = cfg["switches"][key]
    ports = spec["rack_ports"]
    if type(ports) is not int or ports < 1:
      raise ValueError(f"{key}: declare the number of provisioned switch ports.")
    port_rate = number(spec["port_gbps"], f"{key} downstream port rate")
    uplink_rate = number(spec["usable_uplinks_gbps"], f"{key} usable uplinks")
    checked = {"ports": len(assigned[key]) <= ports, "rack-rate": demand <= port_rate, "surviving-uplink": total <= uplink_rate}
    capacity[key] = {"terminals": len(assigned[key]), "port_count": ports, "switch_port_gbps": port_rate,
                     "uplink_demand_gbps": total, "usable_uplinks_gbps": uplink_rate, "checks": checked,
                     "pass": all(checked.values())}
    if not all(checked.values()):
      issues.append(_issue("AIDC_TELECOM_SWITCH_DUTY", key, f"{key}: switch ports or usable uplinks cannot sustain the declared rack demand."))
  return user_links, capacity


def _scenarios(cfg, user_links, cables, domains, issues):
  declared = cfg["unavailable_components"]
  if (not isinstance(declared, list) or any(not isinstance(item, str) or item not in domains for item in declared)
      or len(set(declared)) != len(declared)):
    raise ValueError("Declare unique unavailable components by a compiled identity.")
  for rack, paths in user_links.items():
    a, b = paths["A"], paths["B"]
    common = sorted(set(cables[a]["domains"]) & set(cables[b]["domains"]))
    if common:
      issues.append(_issue("AIDC_TELECOM_SHARED_DOMAIN", rack, f"{rack}: A and B share a modeled failure domain.", *common))
  cases = {"normal": set(declared)}
  cases.update({f"off:{domain}": set(declared) | {domain} for domain in sorted(domains)})
  results = {}
  for name, unavailable in cases.items():
    lost_cables = {cable for domain in unavailable for cable in domains[domain]}
    available = {rack: [plane for plane in PLANES if (cable := links[plane]) not in lost_cables and cables[cable]["pass"]]
                 for rack, links in user_links.items()}
    zero = [rack for rack, planes in available.items() if not planes]
    one = [rack for rack, planes in available.items() if len(planes) == 1]
    no_dual = zero + one
    results[name] = {"out_of_service": sorted(unavailable), "dual_racks": len(available)-len(no_dual),
                     "single_racks": len(one), "failed_racks": zero, "pass": not (no_dual if name == "normal" else zero)}
    if not results[name]["pass"]:
      affected = no_dual if name == "normal" else zero
      qualifier = "lacks two usable paths" if name == "normal" else "does not have a surviving path"
      issues.append(_issue("AIDC_TELECOM_SERVICE", name, f"{name}: {len(affected)} racks {qualifier}.", *affected))
  return results


def build_telecom_study(context):
  path = source_path(context, "telecom-study.json")
  if not path.is_file():
    return missing_input(context, path, "telecom-study")
  cfg = read_versioned(path)
  if cfg.get("state") != "assumed" or not isinstance(cfg.get("basis"), str) or not cfg["basis"].strip():
    raise ValueError("Document the assumed telecom acceptance basis.")
  model = staged_json(context, MODEL)
  optics = _optics(cfg["optics"])
  issues = []
  cables, trays, domains = _loss_report(model, optics, issues)
  user_links, capacity = _sizing(cfg, model, issues)
  scenarios = _scenarios(cfg, user_links, cables, domains, issues)
  report = model_report(context, "telecom-study", {"source_digest": file_digest(context.build_path / MODEL),
                        "basis": cfg["basis"], "rack_count": len(user_links), "cables": cables, "trays": trays,
                        "switching": capacity, "scenarios": scenarios,
                        "coverage": "Cuts of one tray, cable, panel, cabinet or declared electric feed. Shared hall and outdoor carriers remain open."}, issues)
  root = context.build_path / "schedules"
  root.mkdir(parents=True, exist_ok=True)
  budget = root / "aidc-telecom-links.csv"
  with budget.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("cable", "basis_length_m", "fiber_db", "connectors_db", "splices_db", "reserve_db", "available_db", "remaining_db", "status"))
    fields = ("basis_length_m", "fiber_db", "connectors_db", "splices_db", "reserve_db", "available_db", "remaining_db")
    for name, check in cables.items():
      table.writerow((name, *(round(check[key], 5) for key in fields), check["pass"]))
  outage = root / "aidc-telecom-contingencies.csv"
  with outage.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("case", "unavailable", "dual_racks", "single_racks", "lost_racks", "status"))
    for name, row in scenarios.items():
      table.writerow((name, " ".join(row["out_of_service"]), row["dual_racks"], row["single_racks"],
                      " ".join(row["failed_racks"]), row["pass"]))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "telecom-link-budget", budget),
                                      artifact(context, "telecom-contingencies", outage)), report.diagnostics)
