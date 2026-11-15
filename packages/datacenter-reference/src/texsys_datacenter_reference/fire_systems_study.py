"""Validate geometric coverage, ring continuity and local release permissives."""

import csv
import math
from itertools import groupby, product

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .fire_systems import MODEL
from .spatial_design import artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


_FEEDBACKS = ("fan_stopped", "damper_closed", "actuator_healthy")


def _issue(rule, key, message, *related):
  return {"rule": rule, "status": "fail", "objects": [key, *related], "message": message}


def _reachable(ring, blocked):
  neighbours = {}
  for key, edge in ring["runs"].items():
    if key in blocked:
      continue
    a, b = edge["nodes"]
    neighbours.setdefault(a, []).append(b)
    neighbours.setdefault(b, []).append(a)
  order, seen = [ring["panel"]], {ring["panel"]}
  for node in order:
    for neighbour in neighbours.get(node, []):
      if neighbour not in seen:
        seen.add(neighbour)
        order.append(neighbour)
  return sorted(ring["devices"].keys() - seen)


def _zones(model, config, issues):
  max_length = number(config["max_loop_length_m"], "rated loop route length")
  max_devices = config["max_addresses_per_loop"]
  if type(max_devices) is not int or max_devices < 1:
    raise ValueError("The alarm loop needs a configured address limit.")
  unavailable = config["unavailable_ring_runs"]
  known = {key for ring in model["rings"].values() for key in ring["runs"]}
  if (not isinstance(unavailable, list) or any(not isinstance(key, str) or key not in known for key in unavailable)
      or len(set(unavailable)) != len(unavailable)):
    raise ValueError("Unavailable ring sections must use distinct compiled run IDs.")
  compiled = {}
  for name, ring in model["rings"].items():
    distance = sum(edge["length_m"] for edge in ring["runs"].values())
    count = sum(row["address"] is not None for row in ring["devices"].values())
    limits = {"length": distance <= max_length, "addresses": count <= max_devices}
    for kind, passed in limits.items():
      if not passed:
        issues.append(_issue("AIDC_FIRE_RING_CAPACITY", name, f"{name}: alarm loop {kind} exceeds its assumed limit."))
    cells = {}
    for key, record in ring["cells"].items():
      a, b, c, d = record["bounds_m"]
      present = key in ring["devices"]
      point = ring["devices"][key]["point_m"] if present else None
      worst = max(math.dist(point[:2], corner) for corner in product((a, c), (b, d))) if point else None
      passed = present and worst <= model["geometric_detector_radius_m"]
      cells[key] = {"worst_plan_distance_m": worst, "detector_present": present, "pass": passed}
      if not passed:
        issues.append(_issue("AIDC_FIRE_DETECTOR_COVERAGE", key, f"{key}: required geometric coverage cell is not served.", name))
    cases = {"configured": set(unavailable) & ring["runs"].keys()}
    cases.update({f"open:{key}": cases["configured"] | {key} for key in sorted(ring["runs"])})
    continuity = {}
    for key, broken in cases.items():
      lost = _reachable(ring, broken)
      continuity[key] = {"open_runs": sorted(broken), "line_trouble": bool(broken), "missing_addresses": lost, "pass": not lost}
      if lost:
        issues.append(_issue("AIDC_FIRE_RING_CONTINUITY", key, f"{name}: {key} disconnects {len(lost)} alarm devices.", *lost))
    compiled[name] = {"length_m": distance, "addresses": count, "limits": limits, "cells": cells, "continuity": continuity}
  return compiled


def _sequence(events, delay, gates, abort_interlock):
  # This event reducer produces review evidence and has no connection to hardware.
  confirmed_at, manual, smoke, signals, transition = None, False, set(), set(), []
  released = False
  for timestamp, batch in groupby(events, key=lambda row: row[0]):
    for _, signal in batch:
      if signal in {"S1", "S2"}:
        smoke.add(signal)
      elif signal == "local_manual":
        manual = True
      elif signal.startswith("!") and signal[1:] in _FEEDBACKS:
        signals.discard(signal[1:])
      else:
        signals.add(signal)
    if confirmed_at is None and (len(smoke) == 2 or manual):
      confirmed_at = timestamp
      transition.append({"t_s": timestamp, "commands": ["local-warning", "fan-stop-request", "damper-close-request"]})
    ready = (confirmed_at is not None and timestamp-confirmed_at >= delay and gates <= signals
             and ("abort" not in signals or not abort_interlock))
    if ready and not released:
      released = True
      transition.append({"t_s": timestamp, "commands": ["simulated-release-permission"]})
  return {"confirmed": confirmed_at is not None, "single_sensor": len(smoke) == 1,
          "local_manual": manual, "trouble": "supervisory_open" in signals, "permission": released, "trace": transition}


def _cause_effect(config, model, issues):
  delay = number(config["release_delay_s"], "pre-discharge delay")
  gates = config["release_permissives"]
  if (not isinstance(gates, list) or any(signal not in _FEEDBACKS for signal in gates)
      or len(set(gates)) != len(gates) or type(config["abort_inhibits_release"]) is not bool):
    raise ValueError("Use distinct modeled dry-contact permissives and declare the abort policy.")
  ready = [(0, feedback) for feedback in _FEEDBACKS]
  dual = [(0, "S1"), (0, "S2")]
  tick = [(delay, "timer")]
  cases = {
    "normal": (tick, False, False),
    "one_detector": ([(0, "S1"), *ready, *tick], False, False),
    "hall_alarm_only": ([(0, "hall_alarm"), *ready, *tick], False, False),
    "early": ([*dual, *ready, (delay/2, "timer")], True, False),
    "dual_with_feedback": ([*dual, *ready, *tick], True, True),
    "local_manual": ([(0, "local_manual"), *ready, *tick], True, True),
    "abort": ([*dual, *ready, (delay/2, "abort"), *tick], True, False),
    "abort_at_deadline": ([*dual, *ready, *tick, (delay, "abort")], True, False),
    "feedback_revoked": ([*dual, *ready, (delay/2, "!fan_stopped"), *tick], True, False),
    "supervisory_open": ([(0, "supervisory_open"), *ready, *tick], False, False),
    "local_confirmed_with_supervisory_open": ([(0, "supervisory_open"), *dual, *ready, *tick], True, True),
  }
  for missing in _FEEDBACKS:
    cases[f"no_{missing}"] = ([*dual, *[(t, feedback) for t, feedback in ready if feedback != missing], *tick], True, False)
  results = {}
  for name, (events, wanted_confirmation, wanted_permission) in cases.items():
    actual = _sequence(events, delay, set(gates), config["abort_inhibits_release"])
    wanted_trouble = any(signal == "supervisory_open" for _, signal in events)
    passed = (actual["confirmed"], actual["permission"], actual["trouble"]) == (wanted_confirmation, wanted_permission, wanted_trouble)
    results[name] = {**actual, "expected_permission": wanted_permission, "pass": passed}
    if not passed:
      issues.append(_issue("AIDC_FIRE_RELEASE_SEQUENCE", name, f"{name}: local release or trouble output differs from the audited sequence."))
  recipients = {name: {"controller": row["controller"], "feedbacks": gates, "local_detector_ids": sorted(row["detectors"]),
                       "supervision_sdl_port": row["signal_port"], "agent_sdl_port": row["agent_port"]}
                for name, row in model["pods"].items()}
  return {"delay_s": delay, "cases": results, "controller_bindings": recipients}


def build_fire_systems_study(context):
  source = source_path(context, "fire-systems-study.json")
  if not source.is_file():
    return missing_input(context, source, "fire-systems-study")
  model = staged_json(context, MODEL)
  config = read_versioned(source)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("The fire study needs a declared basis and input status.")
  issues = []
  loops = _zones(model, config, issues)
  sequence = _cause_effect(config, model, issues)
  coverage = "Geometric plan cells, alarm loop reachability, and discrete pod permissive evidence. Field detector response and agent delivery excluded."
  report = model_report(context, "fire-systems-study", {"source_digest": file_digest(context.build_path / MODEL),
                        "basis": config["basis"], "loops": loops, "cause_effect": sequence, "coverage": coverage}, issues)
  root = context.build_path / "schedules"
  root.mkdir(parents=True, exist_ok=True)
  loop_path, action_path = root / "aidc-fire-circuit-cases.csv", root / "aidc-fire-cause-effect.csv"
  with loop_path.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("compartment", "case", "open_runs", "line_trouble", "missing_devices", "status"))
    for name, loop in loops.items():
      for key, row in loop["continuity"].items():
        table.writerow((name, key, " ".join(row["open_runs"]), row["line_trouble"], " ".join(row["missing_addresses"]), row["pass"]))
  with action_path.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("case", "confirmed", "local_permission", "expected_permission", "trouble", "status"))
    for name, row in sequence["cases"].items():
      table.writerow((name, row["confirmed"], row["permission"], row["expected_permission"], row["trouble"], row["pass"]))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "fire-circuit-cases", loop_path),
                                      artifact(context, "fire-cause-effect", action_path)), report.diagnostics)
