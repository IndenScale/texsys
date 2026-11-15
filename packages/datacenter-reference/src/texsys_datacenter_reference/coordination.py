"""Compose the detailed frame with the routed system and check their envelopes."""

from itertools import combinations, pairwise

from pxr import Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .fws_network import MODEL as FWS_MODEL, add_network_geometry, pipe_envelope
from .spatial_design import MODELS, SCENE, artifact, model_report, staged_json
from .structure import MODEL


def _solid(part):
  return Space(part["origin"], tuple(a+b for a, b in zip(part["origin"], part["size"], strict=True)))


def _insert_frame(context, stage, layout):
  for record in layout["objects"].values():
    if record["module"].split("/")[-2] == "steel-frame-bay":
      if not stage.RemovePrim(record["prim_path"]):
        raise ValueError(f"Could not remove the coarse frame envelope at {record['prim_path']}.")
  geometry = context.build_path / "scenes/aidc-structure.usda"
  installed = UsdGeom.Xform.Define(stage, "/AIDC/Structure").GetPrim()
  if not installed.GetReferences().AddReference(str(geometry)) or not installed.GetChildren():
    raise ValueError("The detailed steel frame and its foundations could not be composed.")


def build_coordinated_scene(context):
  stage = Usd.Stage.Open(str(context.build_path / "scenes/aidc-fws.usda"))
  if stage is None:
    raise ValueError("The staged FWS scene is not readable.")
  structure = staged_json(context, MODEL)
  layout = staged_json(context, f"{MODELS}/layout.json")
  routes = staged_json(context, f"{MODELS}/fws-routing.json")["routes"]
  _insert_frame(context, stage, layout)
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


def build_fws_network_coordination(context):
  stage = Usd.Stage.Open(str(context.build_path / SCENE))
  if stage is None:
    raise ValueError("The staged asset placement scene is not readable.")
  structure = staged_json(context, MODEL)
  layout = staged_json(context, f"{MODELS}/layout.json")
  network = staged_json(context, FWS_MODEL)
  _insert_frame(context, stage, layout)
  add_network_geometry(stage, network["segments"])
  steel = [(f"{name}/{part['identity']}", _solid(part)) for name, record in structure["members"].items()
           for part in record["parts"]]
  assets = [(name, Space(record["minimum_m"], record["maximum_m"])) for name, record in layout["objects"].items()
            if record["module"].split("/")[-2] != "steel-frame-bay"]
  pipes = {key: pipe_envelope(pipe) for key, pipe in network["segments"].items()}
  issues = []

  def clash(first, second):
    issues.append({"rule": "AIDC_FWS_NETWORK_CLASH", "status": "fail", "objects": [first, second],
                   "message": f"{first} envelope intersects {second}."})

  for key, pipe in pipes.items():
    for name, other in (*steel, *assets):
      if pipe.intersects(other):
        clash(key, name)
  for a, b in combinations(sorted(pipes), 2):
    if pipes[a].intersects(pipes[b]):
      first, second = network["segments"][a], network["segments"][b]
      coincident_end = {first["from"], first["to"]} & {second["from"], second["to"]}
      # A connected tee may share its immediate fitting envelope.
      directions = [{i for i in range(3) if pipe["points_m"][0][i] != pipe["points_m"][1][i]}
                    for pipe in (first, second)]
      if not coincident_end or directions[0] == directions[1]:
        clash(a, b)
  for key, opening in network["penetrations"].items():
    x, y, z = opening["point_m"]
    half = opening["square_width_m"] / 2
    # Check free installation margin across the declared roof datum band.
    reserve = Space((x-half, y-half, z-.15), (x+half, y+half, z+.15))
    for name, other in steel:
      if reserve.intersects(other):
        clash(key, name)
  output = context.build_path / "scenes/aidc-fws-network-coordinated.usda"
  output.parent.mkdir(parents=True, exist_ok=True)
  if not stage.Flatten(False).Export(str(output)):
    raise OSError(f"Could not export {output.name}.")
  report = model_report(context, "fws-network-coordination", {
    "network_digest": file_digest(context.build_path / FWS_MODEL),
    "pipe_count": len(pipes), "steel_parts": len(steel), "equipment_envelopes": len(assets),
    "roof_openings": len(network["penetrations"]), "obstructions": issues,
    "coverage": "Insulated orthogonal straight envelopes and roof reservations in a ±0.15 m datum band.",
  }, issues)
  return TargetResult(report.status, (*report.artifacts, artifact(context, "fws-network-scene", output)),
                      report.diagnostics)
