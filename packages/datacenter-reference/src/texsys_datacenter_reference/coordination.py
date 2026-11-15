"""Compose the detailed frame with the routed system and check their envelopes."""

from itertools import pairwise

from pxr import Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .spatial_design import MODELS, artifact, model_report, staged_json
from .structure import MODEL


def _solid(part):
  return Space(part["origin"], tuple(a+b for a, b in zip(part["origin"], part["size"], strict=True)))


def build_coordinated_scene(context):
  stage = Usd.Stage.Open(str(context.build_path / "scenes/aidc-fws.usda"))
  if stage is None:
    raise ValueError("The staged FWS scene is not readable.")
  structure = staged_json(context, MODEL)
  layout = staged_json(context, f"{MODELS}/layout.json")
  routes = staged_json(context, f"{MODELS}/fws-routing.json")["routes"]
  for record in layout["objects"].values():
    if record["module"].split("/")[-2] == "steel-frame-bay":
      if not stage.RemovePrim(record["prim_path"]):
        raise ValueError(f"Could not remove the coarse frame envelope at {record['prim_path']}.")
  geometry = context.build_path / "scenes/aidc-structure.usda"
  installed = UsdGeom.Xform.Define(stage, "/AIDC/Structure").GetPrim()
  if not installed.GetReferences().AddReference(str(geometry)) or not installed.GetChildren():
    raise ValueError("The detailed steel frame and its foundations could not be composed.")

  shapes = [(name, part["identity"], _solid(part)) for name, record in structure["members"].items()
            for part in record["parts"]]
  diagnostics = []
  for leg, route in routes.items():
    for index, (start, end) in enumerate(pairwise(route["points_m"]), 1):
      # Use the same orthogonal segment envelope as the staged FWS scene.
      radius = route["outside_diameter_m"] / 2
      low = tuple(min(a, b) - (radius if a == b else 0) for a, b in zip(start, end, strict=True))
      high = tuple(max(a, b) + (radius if a == b else 0) for a, b in zip(start, end, strict=True))
      envelope = Space(low, high)
      diagnostics.extend({"rule": "AIDC_FWS_FRAME_CLASH", "status": "fail", "objects": [leg, member],
                          "message": f"FWS {leg} segment {index} intersects {member} {part}."}
                         for member, part, shape in shapes if envelope.intersects(shape))
  output = context.build_path / "scenes/aidc-coordinated-detailed.usda"
  output.parent.mkdir(parents=True, exist_ok=True)
  # OpenUSD otherwise embeds the absolute per-run staging path as documentation.
  if not stage.Flatten(False).Export(str(output)):
    raise OSError(f"Could not export {output.name}.")
  digests = {relative: file_digest(context.build_path / relative)
             for relative in (MODEL, f"{MODELS}/layout.json", f"{MODELS}/fws-routing.json")}
  report = model_report(context, "coordination", {
    "source_digests": digests, "structural_members": len(structure["members"]),
    "fws_segments": sum(len(route["points_m"])-1 for route in routes.values()), "obstructions": diagnostics,
    "coverage": "Orthogonal envelopes of detailed frame parts versus the selected FWS supply and return.",
  }, diagnostics)
  return TargetResult(report.status, (*report.artifacts, artifact(context, "detailed-coordination-scene", output)),
                      report.diagnostics)
