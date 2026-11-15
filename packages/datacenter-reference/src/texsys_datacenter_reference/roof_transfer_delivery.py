"""Publish the modeled roof steel and its related demands."""

from pxr import Sdf, Usd

from texsys.evidence import file_digest
from texsys.targets import TargetResult

from .cw_network import _pipe_prim
from .lateral_scene import SCENE as BASE_SCENE, SPECIALTY as BASE_SPECIALTY
from .lateral_study import STUDY
from .roof import _schedule
from .roof_transfer import MODEL
from .spatial_design import MODELS, artifact, model_report, source_path, staged_json

SPECIALTY = "scenes/aidc-roof-transfer.usda"
SCENE = "scenes/aidc-roof-transfer-coordinated.usda"


def build_roof_transfer_scenes(context):
  model = staged_json(context, MODEL)
  source = source_path(context, "roof-transfer.json").relative_to(context.root).as_posix()
  objects = staged_json(context, f"{MODELS}/layout.json")
  results = []
  for original, path, identity, prefix, preview in (
      (BASE_SPECIALTY, SPECIALTY, "roof-transfer-scene", "/Frame/RoofTransfer", {}),
      (BASE_SCENE, SCENE, "roof-transfer-coordinated", "/AIDC/RoofTransfer", dict(objects["source_map"]))):
    stage = Usd.Stage.Open(str(context.build_path/original))
    if stage is None:
      raise ValueError(f"Could not open {original} for roof transfer coordination.")
    for key, row in model["ties"].items():
      identifier = f"{prefix}/{row['panel'].replace('-', '_')}/{row['role']}"
      color = (.83, .41, .21) if row["role"] == "upper" else (.31, .53, .75)
      _pipe_prim(stage, identifier, key, row["points_m"], model["assumptions"]["diameter_m"], color)
      prim = stage.GetPrimAtPath(identifier)
      prim.CreateAttribute("texsys:columns", Sdf.ValueTypeNames.StringArray).Set(row["nodes"])
      prim.CreateAttribute("texsys:collectors", Sdf.ValueTypeNames.StringArray).Set(
        model["panels"][row["panel"]]["collectors"])
      preview[key] = {"file": source, "path": "panels"}
    output = context.build_path/path
    if not stage.Flatten(False).Export(str(output)):
      raise OSError(f"Could not export {path}.")
    results.append(artifact(context, identity, output, preview={"kind": "scene", "source_map": preview}))
  return TargetResult("pass", tuple(results))


def build_roof_transfer_results(context):
  study = staged_json(context, STUDY)
  model = staged_json(context, MODEL)
  cases = study["cases"]
  geometry = model["collectors"] | model["ties"]
  members = _schedule(context, "aidc-roof-transfer-forces.csv",
                      ("case", "member", "role", "initial_kn", "incremental_kn", "total_kn",
                       "utilization", "axial_reference_kn", "end_1", "end_2"),
                      ((key, name, item["role"], item["initial_kn"], item["incremental_kn"],
                        item["total_kn"], item["utilization"], item["capacity_kn"], *geometry[name]["nodes"])
                       for key, row in cases.items() for name, item in row["roof_members"].items()))
  joints = _schedule(context, "aidc-roof-transfer-joints.csv",
                     ("case", "node", "fx_kn", "fy_kn", "brace_shear_kn", "moment_envelope_kn_m",
                      "moment_limit_kn_m", "dx_m", "dy_m"),
                     ((key, node, *row["brace_storey"]["node_loads_kn"][node],
                       item["vertical_brace_shear_kn"], item["moment_kn_m"], item["moment_limit_kn_m"],
                       *row["brace_storey"]["node_displacement_m"][node])
                      for key, row in cases.items() for node, item in row["roof_joints"].items()))
  result = model_report(context, "roof-transfer-results", {
    "study_digest": file_digest(context.build_path/STUDY), "case_count": len(cases),
    "node_count": len(model["nodes"]), "member_count": len(geometry),
    "issues": [issue for issue in study["issues"] if issue["rule"].startswith("AIDC_ROOF_TRANSFER_")],
  })
  return TargetResult(result.status, (*result.artifacts, members, joints), result.diagnostics)
