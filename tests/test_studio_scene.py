import os
from pathlib import Path

import pytest
from pxr import Gf, Usd, UsdGeom

from texsys.geometry import Box
from texsys.project import ProjectError
from texsys.studio.scene import ScenePreview
from texsys.usd_brep import write_box_scene


def _cube(path: Path, size: float) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  stage = Usd.Stage.CreateNew(str(path))
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
  UsdGeom.Cube.Define(stage, "/Cube").CreateSizeAttr(size)
  stage.Save()


def test_preview_follows_newest_scene_and_ignores_assets(tmp_path):
  preview = ScenePreview(tmp_path, "out")
  assert preview.read() == {"status": "empty", "directory": "out"}
  layout = tmp_path / "out/usd/layout.usda"
  asset = Path(__file__).parents[1] / "src/texsys/assets/unit_brep_cube.usda"
  write_box_scene((Box("pump-1", (2, 3, 0), (1, 2, 3)),), layout, asset)
  assert preview.read()["objects"] == [{
    "name": "pump-1",
    "points": [(x, y, z) for x in (2, 3) for y in (3, 5) for z in (0, 3)],
    "faces": ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)),
    "envelope": True,
  }]
  os.utime(layout, (1, 1))
  revision = preview.read()["revision"]
  assert preview.read(revision) == {"status": "unchanged"}
  newer = tmp_path / "out/usd/newer.usdc"
  _cube(newer, 4)
  _cube(tmp_path / "out/dependencies/example/asset.usda", 12)
  result = preview.read(revision)
  assert result["path"] == "out/usd/newer.usdc"
  assert result["objects"][0]["points"][0] == (-2, -2, -2)
  assert not result["objects"][0]["envelope"]
  newer.unlink()
  assert preview.read(result["revision"])["path"] == "out/usd/layout.usda"


def test_preview_transforms_mesh_and_rejects_external_layer(tmp_path):
  root = tmp_path / "project"
  build = root / "build"
  build.mkdir(parents=True)
  file = build / "mesh.usda"
  stage = Usd.Stage.CreateNew(str(file))
  UsdGeom.SetStageMetersPerUnit(stage, 0.01)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
  mesh = UsdGeom.Mesh.Define(stage, "/Triangle")
  mesh.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(100, 0, 0), Gf.Vec3f(0, 100, 0)])
  mesh.CreateFaceVertexCountsAttr([3])
  mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
  stage.Save()
  preview = ScenePreview(root, "build")
  assert preview.read()["objects"][0]["points"] == [(0, 0, 0), (1, 0, 0), (0, 0, 1)]
  _cube(tmp_path / "outside.usda", 4)
  stage.GetRootLayer().subLayerPaths.append("../../outside.usda")
  stage.Save()
  with pytest.raises(ProjectError, match="inside the project"):
    preview.read()
