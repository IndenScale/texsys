"""Assess routed critical feeders under the loss of either supply train."""

import csv
from math import sqrt

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .power_routing import MODEL
from .spatial_design import MODELS, artifact, model_report, number, staged_json

STUDY = f"{MODELS}/power-study.json"


def _cable_rating(row):
  factor = number(row["installation_factor"], "installation factor")
  if factor > 1:
    raise ValueError("Cable installation factor cannot exceed one.")
  return row["parallel"] * number(row["rated_a"], "per-cable ampacity") * factor


def _drop_v(current, impedance, cos_phi, sin_phi):
  return sqrt(3)*current*(impedance["r_ohm"]*cos_phi + impedance["x_ohm"]*sin_phi)


def build_power_installation(context):
  routing, study = staged_json(context, MODEL), staged_json(context, STUDY)
  config = routing["assumptions"]
  pf = study["assumptions"]["power_factor"]
  sin_phi = sqrt(1-pf*pf)
  limits = config["limits"]
  fill_limit = number(limits["carrier_fill_fraction"], "permitted carrier fill")
  drop_limit = number(limits["critical_path_drop_pct"], "permitted voltage drop")
  if fill_limit > 1:
    raise ValueError("Carrier fill fraction cannot exceed one.")
  feed_ampacity = _cable_rating(config["cables"]["feed"])
  tap_ampacity = _cable_rating(config["cables"]["tap"])
  cases, issues = {}, []

  def check(rule, side, key, measured, limit, unit):
    if measured > limit:
      issues.append({"rule": rule, "status": "fail", "objects": [side, key],
                     "message": f"{measured:.4f} {unit} exceeds {limit:.4f} {unit}."})

  for side, train in routing["trains"].items():
    duty = study["trains"][side]
    volts = duty["voltage_kv"]*1000
    case = f"electrical-train-off:{'B' if side == 'A' else 'A'}"
    for key, segment in train["segments"].items():
      check("AIDC_POWER_CARRIER_FILL", side, key, segment["fill_fraction"], fill_limit, "fraction")
      check("AIDC_POWER_CARRIER_WIDTH", side, key, segment["single_layer_width_m"], segment["width_m"], "m")
      check("AIDC_POWER_CARRIER_DEPTH", side, key, segment["cable_diameter_m"], segment["depth_m"], "m")
    buses = {}
    for bus, row in train["busways"].items():
      current = duty["duty"][bus]["required"]
      check("AIDC_POWER_FEED_AMPACITY", side, row["link"], current, feed_ampacity, "A")
      feed_drop = _drop_v(current, row["impedance"], pf, sin_phi)
      taps = sorted(row["terminal_paths"].items(), key=lambda item: item[1]["bus_distance_m"])
      tap_currents = {pod: duty["terminal_kw"][pod]*1000/(sqrt(3)*volts*pf) for pod, _ in taps}
      distance, bus_drop = 0.0, 0.0
      loads = {}
      for index, (pod, tap) in enumerate(taps):
        step = tap["bus_distance_m"] - distance
        impedance = {key: value*step/tap["bus_distance_m"] for key, value in tap["busway_impedance"].items()}
        section_current = sum(tap_currents[name] for name, _ in taps[index:])
        bus_drop += _drop_v(section_current, impedance, pf, sin_phi)
        distance = tap["bus_distance_m"]
        tap_current = tap_currents[pod]
        cable_drop = _drop_v(tap_current, tap["cable_impedance"], pf, sin_phi)
        total_pct = 100*(feed_drop+bus_drop+cable_drop)/volts
        check("AIDC_POWER_TAP_AMPACITY", side, tap["link"], tap_current, tap_ampacity, "A")
        check("AIDC_POWER_VOLTAGE_DROP", side, tap["link"], total_pct, drop_limit, "%")
        loads[pod] = {"link": tap["link"], "tap_length_m": tap["length_m"], "bus_distance_m": distance,
                      "current_a": tap_current, "ampacity_a": tap_ampacity,
                      "busway_drop_v": bus_drop, "drop_cable_v": cable_drop, "critical_path_drop_pct": total_pct,
                      "terminal_voltage_v": volts*(1-total_pct/100)}
      buses[bus] = {"link": row["link"], "feed_length_m": row["length_m"], "current_a": current,
                    "ampacity_a": feed_ampacity, "feed_drop_v": feed_drop, "terminals": loads}
    cases[case] = {"operating_train": side, "busways": buses}
  report = model_report(context, "power-installation", {
    "input_digests": {name: file_digest(context.build_path / name) for name in (MODEL, STUDY)},
    "cases": cases, "limits": limits, "issues": issues,
    "coverage": "Balanced three-phase constant-current voltage-drop screen at full single-train duty. It uses "
                "temperature-basis AC conductor impedance and declared effective grouping factors. Weatherproofing, "
                "cable support and bend details, and thermal and harmonic studies require installation data.",
  }, issues)
  schedule = context.build_path / "schedules/aidc-power-feeders.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("operating_train", "busway", "pod", "feed_sdl_link", "tap_sdl_link", "feed_m", "busway_m", "tap_m",
                    "feed_a", "feed_ampacity_a", "tap_a", "tap_ampacity_a", "path_drop_pct", "pod_entry_v"))
    for case in cases.values():
      for bus, row in case["busways"].items():
        for pod, tap in row["terminals"].items():
          table.writerow((case["operating_train"], bus, pod, row["link"], tap["link"], round(row["feed_length_m"], 3),
                          tap["bus_distance_m"], tap["tap_length_m"], round(row["current_a"], 2), row["ampacity_a"],
                          round(tap["current_a"], 2), tap["ampacity_a"], round(tap["critical_path_drop_pct"], 3),
                          round(tap["terminal_voltage_v"], 2)))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "power-feeder-schedule", schedule)), report.diagnostics)
