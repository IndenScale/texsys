"""Trace the governing equivalent-seismic scenarios across structural studies."""


def _stage_result(case):
  return {"status": case["status"], "failed_checks": sorted(key for key, passed in case["checks"].items() if not passed),
          "governing": case.get("governing", {}), "failure": case.get("failure")}


def _recovered_contacts(independent, tied):
  stable = ("full", "partial")
  regained, losses = [], []
  for node, before in independent.get("footings", {}).items():
    if node not in tied.get("footings", {}):
      continue
    after = tied["footings"][node]
    comparisons = [("service", before["service"], after["service"])]
    comparisons.extend((mode, details["contact"], after["strength"][mode]["contact"])
                       for mode, details in before["strength"].items())
    for mode, old, new in comparisons:
      if (old["state"] in stable) != (new["state"] in stable):
        record = {"node": node, "combination": mode, "independent": old["state"], "tied": new["state"]}
        (regained if new["state"] in stable else losses).append(record)
  return regained, losses


def _slip(case):
  values = ((item.get("mobilized_slip_m", 0.), node, mode)
            for node, footing in case.get("footings", {}).items()
            for mode, item in (("service", footing["service"]), *footing["strength"].items()))
  peak, node, mode = max(values, default=(0., None, None))
  return {"value": peak, "node": node, "combination": mode}


def _envelopes(cases):
  definitions = {
    "interstorey_drift_ratio": ("lateral", "drift", "ratio"),
    "brace_tension_kn": ("lateral", "brace-tension", "demand_kn"),
    "diaphragm_connection_utilization": ("diaphragm", "slab-to-steel", "utilization"),
    "independent_service_sliding_utilization": ("independent", "sliding", "utilization"),
    "tied_service_sliding_utilization": ("tied", "sliding", "utilization"),
    "tied_bearing_utilization": ("tied", "bearing", "utilization"),
    "tied_punching_utilization": ("tied", "punching", "utilization"),
    "tie_axial_flexure_utilization": ("tied", "tie-axial-flexure", "utilization"),
  }
  maxima = {}
  for metric, (stage, mode, field) in definitions.items():
    values = [(row[stage]["governing"][mode][field], key, row[stage]["governing"][mode])
              for key, row in cases.items() if mode in row[stage]["governing"]]
    if values:
      peak, key, location = max(values)
      maxima[metric] = {"value": peak, "case": key, "location": location}
  key = max(cases, key=lambda name: cases[name]["tied_slip_m"]["value"])
  maxima["tied_mobilized_slip_m"] = {"case": key, **cases[key]["tied_slip_m"]}
  return maxima


def evaluate_seismic_envelope(lateral, diaphragm, independent, tied):
  matrix = lateral.get("seismic_matrix")
  if not matrix or not matrix["cases"]:
    raise ValueError("The seismic envelope requires a configured lateral seismic matrix.")
  names = matrix["cases"]
  reports = {"lateral": lateral, "diaphragm": diaphragm, "independent": independent, "tied": tied}
  if any(set(names) - report["cases"].keys() for report in reports.values()):
    raise ValueError("Every structural study must cover every seismic matrix case.")
  if any(report["frame"] != lateral["frame"] for report in reports.values()):
    raise ValueError("Seismic envelope requires a common structural frame.")
  for name in names:
    if (diaphragm["cases"][name]["input"]["lateral_case"] != name
        or independent["cases"][name]["input"]["lateral_case"] != name):
      raise ValueError(f"Seismic envelope case {name} has a mismatched source identity.")
  cases = {}
  for name in names:
    lateral_case = lateral["cases"][name]
    stages = {key: report["cases"][name] for key, report in reports.items()}
    restored, lost = _recovered_contacts(stages["independent"], stages["tied"])
    components = [{key: item[key] for key in ("axis", "sign", "scale", "eccentricity_m")}
                  for item in lateral_case["load_components"]]
    cases[name] = {
      "components": components,
      "base_actions_fx_fy_mz": [sum(action[i] for action in lateral_case["level_actions_fx_fy_mz"].values())
                               for i in range(3)],
      **{key: _stage_result(case) for key, case in stages.items()},
      "contacts_regained": restored, "contacts_lost": lost, "tied_slip_m": _slip(stages["tied"]),
    }
  failures = {key: [name for name, row in cases.items() if row[key]["status"] != "pass"] for key in reports}
  candidate_failures = sorted({name for stage in ("lateral", "diaphragm", "tied") for name in failures[stage]})
  return {
    "schema_version": 1, "state": "assumed", "formal_review": "pending", "frame": lateral["frame"],
    "case_count": len(cases), "uniaxial_count": sum(len(row["components"]) == 1 for row in cases.values()),
    "bidirectional_count": sum(len(row["components"]) == 2 for row in cases.values()),
    "matrix_basis": {key: matrix[key] for key in ("gravity_case", "orthogonal_ratio", "eccentricity_ratio",
                                                  "base_shear_coefficient", "height_exponent")},
    "screening": {"tied_candidate_status": "revision-required" if candidate_failures else "pass",
                  "failed_candidate_cases": candidate_failures, "failures_by_stage": failures},
    "governing": _envelopes(cases), "cases": cases,
  }
