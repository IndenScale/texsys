from pathlib import Path

from pxr import Usd

from texsys.geometry import (
  AnalyticBoxKernel,
  GeometryEventSimulator,
  GeometryStimulus,
  InstallationEvent,
)

ASSET = Path(__file__).parents[1] / "src" / "texsys" / "assets" / "unit_brep_cube.usda"


def boxes(kernel: AnalyticBoxKernel):
  return (
    kernel.create_box("first", (0.0, 0.0, 0.0), (2.0, 1.0, 1.0)),
    kernel.create_box("second-box", (3.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
  )


def test_analytic_kernel_measures_and_separates_boxes() -> None:
  kernel = AnalyticBoxKernel()
  first, second = boxes(kernel)

  assert kernel.volume(first) == 2.0
  assert not kernel.intersects(first, second)
  assert kernel.intersects(first, second, clearance=1.01)


def test_installation_events_report_collisions_separately_from_status() -> None:
  kernel = AnalyticBoxKernel()
  first, second = boxes(kernel)
  overlapping = kernel.create_box("third", (1.0, 0.0, 0.0), (1.0, 1.0, 1.0))
  stimulus = GeometryStimulus(
    (first, second, overlapping),
    tuple(InstallationEvent("install", item.identity) for item in (first, second, overlapping)),
  )

  result = GeometryEventSimulator(kernel).solve(stimulus)

  assert result.status == "solved"
  assert [(item.first, item.second) for item in result.collisions] == [("third", "first")]
  assert result.visited == ("first", "second-box", "third")


def test_usd_scene_references_real_brep_asset_and_preserves_identity(tmp_path: Path) -> None:
  kernel = AnalyticBoxKernel()
  solids = boxes(kernel)
  target = tmp_path / "scene.usda"

  kernel.write_usd(solids, target, ASSET)

  asset_stage = Usd.Stage.Open(str(ASSET))
  brep = asset_stage.GetPrimAtPath("/World/CubeMats")
  assert brep.GetTypeName() == "BrepArray"
  assert brep.GetAttribute("brep:extent").Get() is not None

  stage = Usd.Stage.Open(str(target))
  prim = stage.GetPrimAtPath("/World/second_2d_box")
  assert prim.IsValid()
  assert prim.GetAttribute("texsys:identity").Get() == "second-box"
  references = prim.GetMetadata("references").GetAddedOrExplicitItems()
  assert str(references[0].assetPath) == "./assets/unit_brep_cube.usda"
  assert tuple(prim.GetAttribute("xformOp:translate").Get()) == (3.0, 0.0, 0.0)
  assert tuple(prim.GetAttribute("xformOp:scale").Get()) == (1.0, 1.0, 1.0)
  assert (tmp_path / "assets" / "unit_brep_cube.usda").read_bytes() == ASSET.read_bytes()
