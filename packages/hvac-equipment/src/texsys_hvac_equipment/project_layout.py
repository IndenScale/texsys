"""Explicit concept envelopes bound to SDL instance identities, never inferred."""

import json
from pathlib import Path

from pxr import Gf, Sdf, Tf, Usd, UsdGeom

from texsys.evidence import write_json
from texsys.project import inspect_project
from texsys.targets import Artifact, BuildContext, TargetResult

from .domain import _vector


def layout_inputs(context: BuildContext):
  info = inspect_project(context.root)
  return (f"{info.source}/**", f"{info.assets}/layout.json")


def build_project_layout(context: BuildContext) -> TargetResult:
  info = inspect_project(context.root)
  source = Path(info.source) / "system.sdl.json"
  layout_path = Path(info.assets) / "layout.json"
  if context.scope:
    raise ValueError("Project layout does not accept undeclared scope fields.")
  if not (context.root / layout_path).is_file():
    return TargetResult("indeterminate", open_items=({"id": "missing-layout", "impact": f"Requires explicit geometry in {layout_path}."},))
  sdl = json.loads((context.root / source).read_text(encoding="utf-8"))
  layout = json.loads((context.root / layout_path).read_text(encoding="utf-8"))
  if not isinstance(layout, dict) or set(layout) != {"schema_version", "root", "basis", "objects"}:
    raise ValueError("Layout requires schema_version, root, basis and objects.")
  root = sdl["root"]
  if layout["schema_version"] != 1 or layout["root"] != root or not isinstance(layout["basis"], str) or not layout["basis"].strip():
    raise ValueError("Layout requires version 1, the SDL root identity and an explicit basis.")
  instances = sdl["components"][root].get("instances", {})
  if not instances or any(sdl["components"][item["component"]].get("instances") for item in instances.values()):
    raise ValueError("Concept layout supports a nonempty flat SDL root; nested expansion needs an explicit layout contract.")
  entries = layout["objects"]
  if not isinstance(entries, dict) or set(entries) - set(instances):
    raise ValueError("Layout objects must refer to existing SDL root instances.")
  missing = sorted(set(instances) - set(entries))
  output = context.build_path / "hvac" / "layout"
  output.mkdir(parents=True, exist_ok=True)
  scene = output / "scene.usda"
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Layout").GetPrim())
  objects, source_map = [], {}
  prim_paths = set()
  for name, entry in sorted(entries.items()):
    if not isinstance(entry, dict) or set(entry) != {"origin", "size", "basis"} or not isinstance(entry["basis"], str) or not entry["basis"].strip():
      raise ValueError(f"{name}: layout requires origin, size and explicit basis.")
    origin = _vector(entry["origin"], name + ".origin")
    size = _vector(entry["size"], name + ".size", positive=True)
    identity = f"{root}.{name}"
    prim_path = "/Layout/" + Tf.MakeValidIdentifier(name)
    if prim_path in prim_paths:
      raise ValueError("Layout instance identities collide after USD identifier conversion.")
    prim_paths.add(prim_path)
    cube = UsdGeom.Cube.Define(stage, prim_path)
    cube.CreateSizeAttr(1)
    cube.AddTranslateOp().Set(Gf.Vec3d(*(origin[i] + size[i] / 2 for i in range(3))))
    cube.AddScaleOp().Set(Gf.Vec3f(*size))
    cube.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)
    cube.GetPrim().SetCustomDataByKey("envelope", True)
    source_map[identity] = {"file": source.as_posix(), "path": f"components.{root}.instances.{name}",
                            "geometry": {"file": layout_path.as_posix(), "path": f"objects.{name}"}}
    objects.append({"semantic_ref": identity, "prim_path": prim_path, **entry})
  # Standalone USD has no mutable external layers to mix into historical builds.
  stage.GetRootLayer().Export(str(scene))
  report = {"schema_version": 1, "root": root, "basis": layout["basis"], "geometry": "concept-envelopes",
            "units": "m", "objects": objects, "source_map": source_map,
            "missing_objects": [f"{root}.{name}" for name in missing],
            "limits": ["Explicit conceptual envelopes; no surveyed location, equipment geometry or clearance acceptance."]}
  report_path = output / "layout.json"
  write_json(report_path, report)
  diagnostics = tuple({"rule": "LAYOUT_MISSING", "status": "indeterminate", "message": f"Missing explicit layout for {root}.{name}.",
                        "objects": [f"{root}.{name}"]} for name in missing)
  return TargetResult("indeterminate" if missing else "pass", (
    Artifact("project-scene", scene.relative_to(context.root).as_posix(), complete=not missing,
             preview={"kind": "scene", "title": "概念场景", "root": root, "source_map": source_map,
                      "geometry": "concept-envelopes", "basis": layout["basis"]}),
    Artifact("layout-data", report_path.relative_to(context.root).as_posix(), complete=not missing,
             preview={"kind": "report", "title": "布局依据", "root": root, "source_map": source_map}),
  ), diagnostics, tuple({"id": f"missing-layout:{root}.{name}", "impact": "Object omitted from this partial scene."} for name in missing))
