"""Back-annotate physical water storage from the compiled FWS design."""

import csv
import math

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .fws_network import MODEL as ROUTE
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


MODEL = f"{MODELS}/fws-thermal-parameters.json"
HYDRAULIC = f"{MODELS}/fws-network-study.json"


def build_thermal_parameters(context):
  path = source_path(context, "fws-thermal-parameters.json")
  if not path.is_file():
    return missing_input(context, path, "fws-thermal-parameters")
  config = read_versioned(path)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("FWS thermal geometry requires an explicit design basis.")
  budget = number(config["design_pipe_volume_m3"], "budgeted pipe volume")
  route = staged_json(context, ROUTE)
  hydraulics = staged_json(context, HYDRAULIC)
  normal = hydraulics["cases"]["normal"]
  fluid = normal["fluid"]
  rho_cp = fluid["density_kg_m3"] * fluid["cp_kj_kg_k"]
  entries = {}
  for key, pipe in route["segments"].items():
    length, bore = pipe["length_m"], pipe["hydraulic_section"]["inside_m"]
    volume = math.pi*bore*bore/4*length
    flow = normal["pipes"][key]["flow_m3_s"]
    entries[key] = {"leg": pipe["leg"], "length_m": length, "bore_m": bore,
                    "water_volume_m3": volume, "water_capacity_kj_k": volume*rho_cp,
                    "normal_flow_m3_s": flow, "normal_residence_s": volume/abs(flow) if abs(flow) > 1e-11 else None,
                    "sdl_links": pipe["sdl_links"]}
  actual = sum(row["water_volume_m3"] for row in entries.values())
  buffers = {key: {"working_volume_m3": row["working_volume_m3"], "minimum_volume_m3": row["minimum_volume_m3"],
                  "water_capacity_kj_k": row["working_volume_m3"]*rho_cp, "plant": row["plant"],
                  "sdl_supply_link": row["sdl_supply_link"]} for key, row in route.get("buffers", {}).items()}
  result = model_report(context, "fws-thermal-parameters", {
    "state": config["state"], "basis": config["basis"], "method": "straight-pipe-water-volume/1",
    "route_digest": file_digest(context.build_path / ROUTE), "hydraulics_digest": file_digest(context.build_path / HYDRAULIC),
    "fluid": fluid, "budget_volume_m3": budget, "extracted_volume_m3": actual,
    "volume_difference_m3": actual-budget, "extracted_capacity_kj_k": actual*rho_cp, "segments": entries, "buffers": buffers,
    "reporting_resolution": {"length_m": 0.001, "bore_mm": 0.1, "volume_l": 0.001, "time_s": 0.001},
  })
  schedule = context.build_path / "schedules/aidc-fws-thermal-parameters.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("pipe", "leg", "centerline_m", "bore_mm", "water_volume_l", "water_capacity_kj_k", "normal_residence_s", "sdl_links"))
    for key, row in entries.items():
      time = row["normal_residence_s"]
      table.writerow((key, row["leg"], round(row["length_m"], 3), round(row["bore_m"]*1000, 1),
                      round(row["water_volume_m3"]*1000, 3), round(row["water_capacity_kj_k"], 4),
                      "" if time is None else round(time, 3), " ".join(row["sdl_links"])))
  return TargetResult(result.status, (*result.artifacts, artifact(context, "fws-thermal-parameters-csv", schedule)), result.diagnostics)
