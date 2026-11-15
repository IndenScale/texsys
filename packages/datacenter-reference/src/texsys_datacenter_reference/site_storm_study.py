"""Trace storm flows and solve a detention reservoir from installed geometry."""

from math import ceil, isclose, pi, sqrt

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .roof import _schedule
from .site_grading import MODEL as GRADING, issue
from .site_storm import DETENTION_IN, DETENTION_OUT, MODEL, OUTFALL, _number
from .spatial_design import MODELS, model_report, number, staged_json

ROOF_STUDY = f"{MODELS}/roof-drainage.json"
STUDY = f"{MODELS}/site-storm-study.json"


def _rating(volume, detention, control):
  surface = detention["bottom_m"]+volume/detention["plan_area_m2"]
  head = max(surface-control, 0)
  bore = detention["outlet_diameter_m"]
  return detention["discharge_coefficient"]*pi*bore*bore/4*sqrt(2*9.81*head)


def _attenuate(inflow_l_s, storm, model):
  tank = model["detention"]
  data = model["assumptions"]
  site = data["outfall"]
  dt = _number(data, "time_step_s")
  if dt > 60:
    raise ValueError("Detention integration requires a timestep of 60 s or less.")
  duration = 60*_number(storm, "duration_min")
  period = 3600*_number(tank, "maximum_drain_hours")
  tail = site["tailwater_m"]
  control = max(tank["bottom_m"]+tank["outlet_center_above_floor_m"], tail)
  dead = (control-tank["bottom_m"])*tank["plan_area_m2"]
  volume = dead
  if volume >= tank["working_capacity_m3"]:
    raise ValueError("Receiving tailwater occupies the entire working storage before the rainfall event.")
  inflow, released = 0., 0.
  peak_volume, peak_release = volume, 0.
  history = [{"time_s": 0., "inflow_l_s": 0., "outflow_l_s": 0., "volume_m3": volume,
              "water_level_m": tank["bottom_m"]+volume/tank["plan_area_m2"]}]
  elapsed = 0.
  end = duration+period
  for _ in range(ceil(end/dt)+2):
    if elapsed >= end:
      break
    increment = min(dt, end-elapsed, duration-elapsed if elapsed < duration else dt)
    landed = increment*inflow_l_s/1000 if elapsed < duration else 0.
    available = volume+landed
    low, high = min(dead, available), available
    # Solve V_{n+1} + Q(V_{n+1}) Δt = V_n + incoming volume.
    for _ in range(38):
      middle = (low+high)/2
      if middle+_rating(middle, tank, control)*increment < available:
        low = middle
      else:
        high = middle
    volume = (low+high)/2
    discharge = max(0, (available-volume)/increment)
    elapsed += increment
    inflow += landed
    released += increment*discharge
    peak_volume = max(peak_volume, volume)
    peak_release = max(peak_release, discharge)
    history.append({"time_s": elapsed, "inflow_l_s": 1000*landed/increment, "outflow_l_s": 1000*discharge,
                    "volume_m3": volume, "water_level_m": tank["bottom_m"]+volume/tank["plan_area_m2"]})
  residual = dead+inflow-released-volume
  if not isclose(residual, 0, abs_tol=1e-7):
    raise ValueError("Discrete detention volume balance does not close.")
  return {"peak_volume_m3": peak_volume, "capacity_m3": tank["working_capacity_m3"],
          "peak_water_level_m": tank["bottom_m"]+peak_volume/tank["plan_area_m2"],
          "peak_release_l_s": peak_release*1000, "initial_storage_m3": dead,
          "inflow_volume_m3": inflow, "released_volume_m3": released, "final_storage_m3": volume,
          "drawdown_remaining_m3": max(0, volume-dead), "mass_balance_residual_m3": residual, "timeline": history}


def _case(name, storm, model, terrain, roof_study, diagnostics):
  config = model["assumptions"]
  multiplier = _number(storm, "roof_rainfall_factor")
  rainfall = roof_study["rainfall_mm_h"]*multiplier
  receipts, flows = {}, dict.fromkeys(model["runs"], 0.)
  roof_total, yard_total = 0., 0.
  admission = _number(config["criteria"], "receiver_admission_l_s")
  for node, area in terrain["catchments"].items():
    roof = roof_study["outlets"][node]
    roof_l_s = roof["design_flow_l_s"]*multiplier
    ground_l_s = rainfall*area["effective_area_m2"]/3600
    received = roof_l_s+ground_l_s
    roof_total += roof_l_s
    yard_total += ground_l_s
    receipts[node] = {"roof_l_s": roof_l_s, "surface_l_s": ground_l_s, "combined_l_s": received,
                      "admission_capacity_l_s": admission}
    if roof_l_s > min(roof["gutter_capacity_l_s"], roof["leader_capacity_l_s"]):
      issue(diagnostics, "ROOF_SUPPLY", [name, node], f"{node} exceeds the upstream roof system rating in {name}.")
    if received > admission:
      issue(diagnostics, "INLET_CAPACITY", [name, node], f"{node} receives {received:.2f} L/s, over {admission:.2f} L/s.")
    for run in model["catchment_paths"][node]:
      flows[run] += received
  total = roof_total+yard_total
  to_tank = sum(flows[key] for key, row in model["runs"].items() if row["to_node"] == DETENTION_IN)
  if not isclose(to_tank, total, abs_tol=1e-8):
    raise ValueError("Clean catchments do not balance to the detention inlet.")
  stored = _attenuate(to_tank, storm, model)
  for key, row in model["runs"].items():
    if row["from_node"] == DETENTION_OUT:
      flows[key] = stored["peak_release_l_s"]
    capacity = row["capacity_at_half_depth_m3_h"]/3.6
    if flows[key] > capacity:
      issue(diagnostics, "PIPE_CAPACITY", [name, key], f"{key} needs {flows[key]:.2f} L/s, over {capacity:.2f} L/s.")
  if stored["peak_volume_m3"] > stored["capacity_m3"]:
    issue(diagnostics, "STORAGE", [name, DETENTION_IN], f"Peak storage {stored['peak_volume_m3']:.2f} m³ exceeds active storage.")
  permit = _number(config["outfall"], "permitted_l_s")
  if stored["peak_release_l_s"] > permit:
    issue(diagnostics, "BOUNDARY_FLOW", [name, OUTFALL], f"Peak release {stored['peak_release_l_s']:.2f} L/s exceeds the declared {permit:.2f} L/s.")
  if stored["peak_water_level_m"] >= model["nodes"][DETENTION_IN]["point_m"][2]:
    issue(diagnostics, "TANK_SURCHARGE", [name, DETENTION_IN], "Design water level reaches the incoming storm pipe.")
  if stored["drawdown_remaining_m3"] > .02:
    issue(diagnostics, "NOT_DRAINED", [name, DETENTION_OUT], "Detention has more than 0.02 m³ active storage after the drawdown window.")
  dirty = terrain["oil_yard"]
  oil_rain = rainfall*storm["duration_min"]/60*dirty["plan_area_m2"]/1000
  oil_spill = _number(dirty, "credible_spill_m3")
  accepted = dirty["sealed_storage"]["working_capacity_m3"]
  if oil_rain+oil_spill > accepted:
    issue(diagnostics, "OIL_CONTAINMENT", [name, "oil-yard"], f"Sealed yard needs {oil_rain+oil_spill:.2f} m³ of containment.")
  return {"intensity_mm_h": rainfall, "duration_min": storm["duration_min"], "inlets": receipts,
          "run_peak_l_s": flows, "roof_total_l_s": roof_total, "surface_total_l_s": yard_total,
          "detention": stored, "oil_yard": {"rain_m3": oil_rain, "spill_m3": oil_spill, "capacity_m3": accepted,
                                           "combined_m3": oil_rain+oil_spill}}


def build_site_storm_study(context):
  model, terrain, roof = (staged_json(context, path) for path in (MODEL, GRADING, ROOF_STUDY))
  cases, diagnostics = {}, []
  count = number(model["detention"]["discharge_coefficient"], "outlet discharge coefficient")
  if count > 1:
    raise ValueError("Outlet discharge coefficient must not exceed one.")
  storms = model["assumptions"]["storms"]
  if "design" not in storms:
    raise ValueError("At least a named design storm is required.")
  for name, weather in storms.items():
    cases[name] = _case(name, weather, model, terrain, roof, diagnostics)
  report = model_report(context, "site-storm-study", {
    "input_digests": {path: file_digest(context.build_path / path) for path in (MODEL, GRADING, ROOF_STUDY)},
    "cases": cases, "checks": "fail" if diagnostics else "pass", "issues": diagnostics,
    "coverage": "Block rainfall on the contributing horizontal area; conservative coincident inflows and half-full pipe "
                "screening. Level-pool attenuation uses as-modeled storage area, outlet aperture and declared tailwater. "
                "Oil-yard runoff is reserved in sealed storage. Flow reversal, inlet choking, backwater propagation and "
                "offsite network dynamics require independent engineering.",
  })
  summary = _schedule(context, "aidc-storm-duty.csv",
                      ("event", "intensity_mm_h", "duration_min", "roof_l_s", "formed_surface_l_s", "max_storage_m3",
                       "max_release_l_s", "inflow_m3", "released_m3", "remaining_active_m3", "oil_yard_demand_m3"),
                      ((name, row["intensity_mm_h"], row["duration_min"], row["roof_total_l_s"], row["surface_total_l_s"],
                        row["detention"]["peak_volume_m3"], row["detention"]["peak_release_l_s"],
                        row["detention"]["inflow_volume_m3"], row["detention"]["released_volume_m3"],
                        row["detention"]["drawdown_remaining_m3"], row["oil_yard"]["combined_m3"])
                       for name, row in cases.items()))
  hydrographs = _schedule(context, "aidc-storm-hydrographs.csv",
                          ("event", "time_s", "inflow_l_s", "outflow_l_s", "volume_m3", "water_level_m"),
                          ((name, step["time_s"], step["inflow_l_s"], step["outflow_l_s"], step["volume_m3"], step["water_level_m"])
                           for name, row in cases.items() for step in row["detention"]["timeline"]))
  return TargetResult(report.status, (*report.artifacts, summary, hydrographs), report.diagnostics)


def build_site_storm_check(context):
  issues = []
  for path in (MODEL, STUDY):
    issues.extend(staged_json(context, path)["issues"])
  return model_report(context, "site-storm-check", {
    "input_digests": {path: file_digest(context.build_path / path) for path in (MODEL, STUDY)}, "issues": issues,
  }, issues)
