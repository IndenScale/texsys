"""Place fire systems in the federated scene and check their installed envelopes."""

import csv

from pxr import Usd

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH_MODEL, space
from .fire_systems import MODEL
from .fws_network import MODEL as WATER_MODEL, pipe_envelope
from .spatial_design import MODELS, artifact, model_report, staged_json
from .structure import MODEL as STRUCTURE_MODEL
from .telecom import MODEL as TELECOM_MODEL, tray_space
from .telecom_coordination import SCENE as BASE_SCENE, _fire_clearance, _prim


SCENE = "scenes/aidc-fire-systems-coordinated.usda"


def _tube(row):
  first, second = row["points_m"]
  radius = row["outside_diameter_m"]/2
  return Space(tuple(min(a, b)-(radius if a == b else 0) for a, b in zip(first, second, strict=True)),
               tuple(max(a, b)+(radius if a == b else 0) for a, b in zip(first, second, strict=True)))


def _body(point, dimensions):
  return Space(tuple(x-d/2 for x, d in zip(point, dimensions, strict=True)),
               tuple(x+d/2 for x, d in zip(point, dimensions, strict=True)))


def _system_objects(model):
  outside_pods, inside_pods = {}, {}
  for ring in model["rings"].values():
    for key, row in (ring["runs"] | ring["supervision"]).items():
      outside_pods[key] = (_tube(row), (.79, .27, .20))
    for key, row in ring["devices"].items():
      size = (.18, .15, .26) if row["kind"] == "panel" else (.108, .108, .073)
      outside_pods[key] = (_body(row["point_m"], size), (.78, .43, .28))
  for pod, record in model["pods"].items():
    assigned = {f"AG-{pod}": space(record["cabinet"])}
    assigned.update({key: _tube(row) for key, row in record["pipes"].items()})
    for field in ("nozzles", "detectors"):
      assigned.update({key: _body(point, (.08, .08, .08)) for key, point in record[field].items()})
    inside_pods[pod] = {"host": space(record["host_bounds"]), "objects": assigned}
  return outside_pods, inside_pods


def _obstacles(context, architecture):
  structure = staged_json(context, STRUCTURE_MODEL)
  layout = staged_json(context, f"{MODELS}/layout.json")
  water = staged_json(context, WATER_MODEL)
  telecom = staged_json(context, TELECOM_MODEL)
  obstacles = {name: space(record) for name, record in architecture["walls"].items()}
  obstacles.update({key: space(row) for key, row in layout["objects"].items() if row["discipline"] not in {"architecture", "structure"}})
  for key, row in structure["members"].items():
    obstacles.update({f"{key}/{part['identity']}": Space(tuple(part["origin"]), tuple(a+b for a, b in zip(part["origin"], part["size"], strict=True)))
                      for part in row["parts"]})
  obstacles.update({key: pipe_envelope(row) for key, row in water["segments"].items()})
  obstacles.update({key: _body(row["point_m"], row["size_m"]) for key, row in water["valves"].items()})
  for assembly in telecom["planes"].values():
    obstacles.update({key: tray_space(row) for key, row in assembly["runs"].items()})
    cabinet = assembly["cabinet"]
    obstacles[cabinet["id"]] = space(cabinet)
  obstacles.update(_fire_clearance(architecture))
  return obstacles


def build_fire_systems_coordination(context):
  model = staged_json(context, MODEL)
  architecture = staged_json(context, ARCH_MODEL)
  outside, inside = _system_objects(model)
  obstacles = _obstacles(context, architecture)
  issues = [{"rule": "AIDC_FIRE_SERVICE_CLASH", "status": "fail", "objects": [name, blocker],
             "message": f"{name} envelope overlaps {blocker}."}
            for name, (shape, _) in outside.items() for blocker, volume in obstacles.items() if shape.intersects(volume)]
  for pod, data in inside.items():
    for key, shape in data["objects"].items():
      if not data["host"].contains_space(shape):
        issues.append({"rule": "AIDC_FIRE_POD_ENVELOPE", "status": "fail", "objects": [pod, key],
                       "message": f"{key} does not fit its declared host {pod}."})
  report = model_report(context, "fire-systems-coordination", {
    "fire_source_digest": file_digest(context.build_path / MODEL), "coordination_scene_digest": file_digest(context.build_path / BASE_SCENE),
    "hall_object_count": len(outside), "pod_object_count": sum(len(row["objects"]) for row in inside.values()),
    "issues": issues, "coverage": "Hall envelopes versus modeled disciplines and containment of candidate local devices within their pods.",
  }, issues)
  if issues:
    return report
  stage = Usd.Stage.Open(str(context.build_path / BASE_SCENE))
  if stage is None:
    raise ValueError("The federated source scene could not be opened.")
  for key, (shape, color) in outside.items():
    _prim(stage, f"/AIDC/FireSystems/Hall/{key.replace('-', '_')}", key, shape, color)
  for pod, row in inside.items():
    for key, shape in row["objects"].items():
      _prim(stage, f"/AIDC/FireSystems/Pods/{pod}/{key.replace('-', '_')}", key, shape, (.69, .29, .27))
  scene = context.build_path / SCENE
  if not stage.Flatten(False).Export(str(scene)):
    raise OSError(f"Could not export {scene.name}.")
  schedule = context.build_path / "schedules/aidc-fire-runs.csv"
  schedule.parent.mkdir(parents=True, exist_ok=True)
  with schedule.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("run", "service", "compartment", "pod", "centerline_m", "outside_diameter_mm"))
    for key, ring in model["rings"].items():
      for service, rows in (("alarm-ring", ring["runs"]), ("supervisory", ring["supervision"])):
        for name, row in rows.items():
          table.writerow((name, service, key, row.get("pod", ""), round(row["length_m"], 3), 1000*row["outside_diameter_m"]))
    for pod, record in model["pods"].items():
      for name, row in record["pipes"].items():
        table.writerow((name, "agent", record["compartment"], pod, round(row["length_m"], 3), 1000*row["outside_diameter_m"]))
  return TargetResult(report.status, (*report.artifacts, artifact(context, "fire-systems-scene", scene),
                                      artifact(context, "fire-equipment-runs", schedule)), report.diagnostics)
