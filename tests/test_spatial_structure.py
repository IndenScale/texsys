import json
from pathlib import Path

from pxr import Usd, UsdGeom
import pytest

from texsys_spatial import AxisGrid, DatumPlane
from texsys_structure import ISection, grid_frame
from texsys_structure.concrete import geometry_usda, mat_bars


STRUCTURAL_MODULES = Path(__file__).parents[1] / "packages/structure/src/library/modules"


def test_spatial_references_use_named_axes_in_a_local_frame():
  grid = AxisGrid("L02", {"B": 4, "A": -2}, {"2": 8, "1": 2})
  assert tuple(grid.x) == ("A", "B")
  assert grid.point("A", "2", (0.5, -1)) == (-1.5, 7)
  with pytest.raises(ValueError, match="Unknown grid intersection"):
    grid.point("C", "2")
  with pytest.raises(ValueError, match="distinct coordinates"):
    AxisGrid("L02", {"A": 1, "B": 1}, {"1": 0})


def test_structure_uses_grid_and_datum_without_an_hvac_project():
  grid = AxisGrid("L02", {"A": -2, "B": 4}, {"1": 2, "2": 8})
  base = DatumPlane("L02", "slab", 1.5)
  top = DatumPlane("L02", "beam-top", 5.5)
  column = ISection(0.25, 0.25, 0.012, 0.014)
  beam = ISection(0.3, 0.15, 0.008, 0.012)
  parts = {(owner, part.identity): part for owner, part in grid_frame(
    grid, ("1", "2"), base, top, column, beam, base_plate=(0.34, 0.34, 0.024),
  )}
  plate = parts["COL-A-1", "BasePlate"]
  assert plate.origin == pytest.approx((-2.17, 1.83, 1.5))
  assert parts["BEAM-A-B-1", "TopFlange"].maximum[2] == pytest.approx(5.5)
  assert parts["BEAM-A-1-2", "Web"].size[1] == pytest.approx(5.85)
  with pytest.raises(ValueError, match="same frame"):
    tuple(grid_frame(grid, ("1", "2"), base, DatumPlane("L03", "top", 5.5),
                     column, beam, base_plate=(0.34, 0.34, 0.024)))
  with pytest.raises(ValueError, match="fit the section envelope"):
    ISection(0.3, 0.15, 0.16, 0.012)


@pytest.mark.parametrize("name", [
  "reinforcing-bar", "reinforced-concrete-wall", "structural-slab",
  "reinforced-concrete-beam", "reinforced-concrete-column", "isolated-footing",
  "concrete-equipment-plinth",
])
def test_structural_detail_assets_are_reproducible_and_fit_the_cover(name):
  folder = STRUCTURAL_MODULES / name / "assets"
  specification = json.loads((folder / "specification.json").read_text())
  geometry = folder / "geometry.usda"
  assert geometry.read_text() == geometry_usda(specification)
  stage = Usd.Stage.Open(str(geometry))
  shapes = stage.GetPrimAtPath("/Module/Geometry").GetChildren()
  bars = [shape for shape in shapes if shape.IsA(UsdGeom.Cylinder)]
  assert bars
  reinforcement = specification["properties"]["reinforcement"]
  if reinforcement["pattern"] == "straight":
    assert len(shapes) == 1
    return

  assert stage.GetPrimAtPath("/Module/Geometry/Concrete").IsA(UsdGeom.Cube)
  assert len({UsdGeom.Cylinder(bar).GetAxisAttr().Get() for bar in bars}) >= 2
  cover = reinforcement["cover_m"]
  bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
  for bar in bars:
    box = bounds.ComputeWorldBound(bar).ComputeAlignedRange()
    assert min(box.GetMin()) >= cover - 1e-6
    assert all(high <= size - cover + 1e-6
               for high, size in zip(box.GetMax(), specification["envelope_m"], strict=True))


def test_two_reinforcement_mats_require_room_between_cover_planes():
  with pytest.raises(ValueError, match="between the cover planes"):
    mat_bars((1.2, 1.2, 0.09), normal=2, faces=2, cover_m=0.025, diameter_m=0.012, spacing_m=0.2)
