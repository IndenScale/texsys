from pathlib import Path

import pytest
from preview_records import preview_record
from pxr import Gf, Usd, UsdGeom

from texsys.geometry import Box
from texsys.project import ProjectError, initialize_project
from texsys.studio.scene import ScenePreview
from texsys.usd_brep import write_box_scene


def _cube(path: Path, size: float) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  stage = Usd.Stage.CreateNew(str(path))
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
  cube = UsdGeom.Cube.Define(stage, "/Cube")
  cube.CreateSizeAttr(size)
  cube.GetPrim().SetCustomDataByKey("identity", "Plant.Pump")
  stage.Save()


def test_preview_selects_declared_scene_not_newest_file_and_preserves_identity(tmp_path):
  initialize_project(tmp_path)
  preview = ScenePreview(tmp_path, "build")
  assert preview.read() == {"status": "empty", "directory": "build"}
  layout = tmp_path / "build/usd/layout.usda"
  asset = Path(__file__).parents[1] / "src/texsys/assets/unit_brep_cube.usda"
  write_box_scene((Box("Plant.Pump", (2, 3, 0), (1, 2, 3)),), layout, asset)
  # Unregistered output is never a preview candidate.
  _cube(tmp_path / "build/unregistered.usda", 20)
  source = {"file": "src/system.sdl.json", "path": "components.Plant.instances.Pump"}
  preview_record(tmp_path, "one", (
    ("scene", "build/usd/layout.usda", {"kind": "scene", "root": "Plant", "source_map": {"Plant.Pump": source}}),
    ("asset", "build/usd/assets/unit_brep_cube.usda", None),
  ))
  first = preview.read()
  item = first["objects"][0]
  assert item["name"] == item["semantic_ref"] == "Plant.Pump"
  assert item["id"] == item["prim_path"] and item["source"] == source
  assert item["points"] == [(x, y, z) for x in (2, 3) for y in (3, 5) for z in (0, 3)]
  assert item["envelope"] and first["current"]
  assert preview.read(first["revision"]) == {"status": "unchanged"}
  _cube(tmp_path / "build/second.usdc", 4)
  preview_record(tmp_path, "two", (("other-scene", "build/second.usdc", {"kind": "scene", "root": "Plant"}),), previous="one")
  selected = preview.read(run="two", target="test:preview", artifact="other-scene")
  assert selected["objects"][0]["points"][0] == (-2, -2, -2)
  assert selected["objects"][0]["semantic_ref"] == "Plant.Pump"  # customData identity
  previous = preview.read(run="one", target="test:preview", artifact="scene")
  assert previous["objects"][0]["points"] == item["points"]
  assert previous["artifact"]["fallback"] and not previous["current"]


def test_preview_transforms_mesh_and_rejects_external_layer(tmp_path):
  root = tmp_path / "project"
  initialize_project(root)
  file = root / "build/mesh.usda"
  stage = Usd.Stage.CreateNew(str(file))
  UsdGeom.SetStageMetersPerUnit(stage, 0.01)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
  mesh = UsdGeom.Mesh.Define(stage, "/Triangle")
  mesh.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(100, 0, 0), Gf.Vec3f(0, 100, 0)])
  mesh.CreateFaceVertexCountsAttr([3])
  mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
  stage.Save()
  preview_record(root, "one", (("scene", "build/mesh.usda", {"kind": "scene"}),))
  preview = ScenePreview(root, "build")
  assert preview.read()["objects"][0]["points"] == [(0, 0, 0), (1, 0, 0), (0, 0, 1)]
  _cube(tmp_path / "outside.usda", 4)
  stage.GetRootLayer().subLayerPaths.append(str(tmp_path / "outside.usda"))
  stage.Save()
  preview_record(root, "two", (("scene", "build/mesh.usda", {"kind": "scene"}),), previous="one")
  with pytest.raises(ProjectError, match="inside its build snapshot"):
    preview.read(run="two", target="test:preview", artifact="scene")


def test_scene_rejects_missing_or_corrupt_composition_dependencies(tmp_path):
  initialize_project(tmp_path)
  _cube(tmp_path / "build/asset.usda", 4)
  scene = Usd.Stage.CreateNew(str(tmp_path / "build/composite.usda"))
  scene.GetRootLayer().subLayerPaths.append("asset.usda")
  scene.Save()
  record = preview_record(tmp_path, "one", (
    ("scene", "build/composite.usda", {"kind": "scene"}),
    ("asset", "build/asset.usda", None),
  ))
  preview = ScenePreview(tmp_path, "build")
  assert preview.read()["objects"]
  asset = tmp_path / record["artifacts"][1]["stored_path"]
  asset.write_text(asset.read_text() + "\n")
  with pytest.raises(ProjectError, match="differs from the build manifest"):
    preview.read()
  asset.unlink()
  with pytest.raises(ProjectError, match="missing"):
    preview.read()


def test_scene_revision_changes_when_source_becomes_stale(tmp_path):
  initialize_project(tmp_path)
  _cube(tmp_path / "build/scene.usda", 4)
  preview_record(tmp_path, "one", (("scene", "build/scene.usda", {"kind": "scene"}),))
  preview = ScenePreview(tmp_path, "build")
  first = preview.read()
  (tmp_path / "src/system.sdl.json").write_text("{}")
  stale = preview.read(first["revision"])
  assert stale["status"] == "ready" and stale["freshness"] == "inputs-changed"
  assert stale["objects"] == first["objects"]


def test_multiple_scenes_require_explicit_selection(tmp_path):
  initialize_project(tmp_path)
  _cube(tmp_path / "build/one.usda", 1)
  _cube(tmp_path / "build/two.usda", 2)
  preview_record(tmp_path, "one", (
    ("one", "build/one.usda", {"kind": "scene"}), ("two", "build/two.usda", {"kind": "scene"}),
  ))
  preview = ScenePreview(tmp_path, "build")
  with pytest.raises(ProjectError, match="Multiple scene artifacts"):
    preview.read()
  assert preview.read(run="one", target="test:preview", artifact="two")["objects"][0]["points"][0] == (-1, -1, -1)
