"""Parameterized scheme geometry for straight bars and reinforced concrete."""

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class StraightBar:
  axis: int
  center: tuple[float, float, float]
  length_m: float
  diameter_m: float


def _positive(value: float) -> bool:
  return type(value) in {int, float} and math.isfinite(value) and value > 0


def _dimensions(size: tuple[float, float, float], cover_m: float, *values: float) -> None:
  if len(size) != 3 or not all(map(_positive, (*size, cover_m, *values))):
    raise ValueError("Concrete dimensions and reinforcement inputs must be positive and finite.")
  if min(size) <= 2 * cover_m:
    raise ValueError("Concrete envelope is too small for the specified cover.")


def _stations(start: float, stop: float, spacing: float, diameter: float) -> tuple[float, ...]:
  span = stop - start
  if span <= 0:
    raise ValueError("Reinforcement needs a positive centerline span.")
  ratio = span / spacing
  if ratio > 1000:
    raise ValueError("Reinforcement spacing produces too many stations.")
  intervals = max(1, math.ceil(ratio))
  if span / intervals < diameter:
    raise ValueError("Reinforcement spacing produces overlapping bars.")
  return tuple(start + span * index / intervals for index in range(intervals + 1))


def mat_bars(
  size: tuple[float, float, float], *, normal: int, faces: int,
  cover_m: float, diameter_m: float, spacing_m: float,
) -> tuple[StraightBar, ...]:
  """Generate one or two orthogonal mats at the low and high concrete faces."""
  _dimensions(size, cover_m, diameter_m, spacing_m)
  if type(normal) is not int or normal not in range(3) or type(faces) is not int or faces not in (1, 2):
    raise ValueError("A reinforcement mat needs a principal normal and one or two faces.")
  if size[normal] < 2 * cover_m + 2 * faces * diameter_m:
    raise ValueError("The reinforcement mats do not fit between the cover planes.")
  edge = cover_m + diameter_m / 2
  outer = [edge] if faces == 1 else [edge, size[normal] - edge]
  axes = tuple(axis for axis in range(3) if axis != normal)
  bars = []
  for face, depth in enumerate(outer):
    for layer, axis in enumerate(axes):
      transverse = axes[1 - layer]
      for station in _stations(edge, size[transverse] - edge, spacing_m, diameter_m):
        center = [0., 0., 0.]
        center[axis] = size[axis] / 2
        center[transverse] = station
        center[normal] = depth + (-1 if face else 1) * layer * diameter_m
        bars.append(StraightBar(axis, tuple(center), size[axis] - 2 * cover_m, diameter_m))
  return tuple(bars)


def cage_bars(
  size: tuple[float, float, float], *, axis: int, cover_m: float,
  bar_diameter_m: float, tie_diameter_m: float, tie_spacing_m: float,
) -> tuple[StraightBar, ...]:
  """Generate four corner bars and repeated rectangular tie centerlines."""
  _dimensions(size, cover_m, bar_diameter_m, tie_diameter_m, tie_spacing_m)
  if type(axis) is not int or axis not in range(3):
    raise ValueError("A reinforcement cage needs a principal longitudinal axis.")
  a, b = (cross for cross in range(3) if cross != axis)
  long_edge = cover_m + tie_diameter_m + bar_diameter_m / 2
  tie_edge = cover_m + tie_diameter_m / 2
  if min(size[a], size[b]) < 2 * long_edge + bar_diameter_m:
    raise ValueError("The cage does not fit between the cover planes.")
  bars = []
  for x in (long_edge, size[a] - long_edge):
    for y in (long_edge, size[b] - long_edge):
      center = [0., 0., 0.]
      center[axis], center[a], center[b] = size[axis] / 2, x, y
      bars.append(StraightBar(axis, tuple(center), size[axis] - 2 * cover_m, bar_diameter_m))
  for station in _stations(tie_edge, size[axis] - tie_edge, tie_spacing_m, tie_diameter_m):
    for direction, transverse in ((a, b), (b, a)):
      for edge in (tie_edge, size[transverse] - tie_edge):
        center = [0., 0., 0.]
        center[axis], center[direction], center[transverse] = station, size[direction] / 2, edge
        bars.append(StraightBar(direction, tuple(center), size[direction] - 2 * tie_edge, tie_diameter_m))
  return tuple(bars)


def geometry_usda(spec: dict) -> str:
  """Produce the module-local concrete envelope, reinforcement and interface frames."""
  from pxr import Gf, Sdf, Usd, UsdGeom

  size = tuple(spec["envelope_m"])
  reinforcement = spec["properties"]["reinforcement"]
  pattern = reinforcement["pattern"]
  parameters = {name: value for name, value in reinforcement.items() if name != "pattern"}
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Module").GetPrim())
  UsdGeom.Scope.Define(stage, "/Module/Geometry")
  if pattern == "straight":
    axis, diameter = parameters["axis"], parameters["diameter_m"]
    if (len(size) != 3 or type(axis) is not int or axis not in range(3) or not all(map(_positive, (*size, diameter)))
        or any(size[i] != diameter for i in range(3) if i != axis)):
      raise ValueError("The straight bar must fit its cylindrical envelope.")
    bars = (StraightBar(axis, tuple(length / 2 for length in size), size[axis], diameter),)
  else:
    bars = {"mat": mat_bars, "cage": cage_bars}[pattern](size, **parameters)
    body = UsdGeom.Cube.Define(stage, "/Module/Geometry/Concrete")
    body.CreateSizeAttr(1.0)
    body.AddTranslateOp().Set(Gf.Vec3d(*(length / 2 for length in size)))
    body.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*size))
    body.CreateDisplayColorAttr([Gf.Vec3f(0.63, 0.60, 0.54)])
    body.CreateDisplayOpacityAttr([0.26])
  for index, bar in enumerate(bars, start=1):
    solid = UsdGeom.Cylinder.Define(stage, f"/Module/Geometry/Bar_{index:03d}")
    solid.CreateAxisAttr("XYZ"[bar.axis])
    solid.CreateRadiusAttr(bar.diameter_m / 2)
    solid.CreateHeightAttr(bar.length_m)
    solid.AddTranslateOp().Set(Gf.Vec3d(*bar.center))
    solid.CreateDisplayColorAttr([Gf.Vec3f(0.28, 0.39, 0.48)])
  for name, interface in sorted(spec.get("interfaces", {}).items()):
    node = UsdGeom.Xform.Define(stage, f"/Module/Ports/{name}")
    node.AddTranslateOp().Set(Gf.Vec3d(*interface["position_m"]))
    node.GetPrim().CreateAttribute("texsys:normal", Sdf.ValueTypeNames.Double3, custom=True).Set(
      Gf.Vec3d(*interface["normal"])
    )
  return stage.GetRootLayer().ExportToString()


def main() -> None:
  parser = argparse.ArgumentParser(description="Reproduce a structural module's geometry.usda from specification.json.")
  parser.add_argument("specification", type=Path)
  parser.add_argument("--check", action="store_true")
  args = parser.parse_args()
  generated = geometry_usda(json.loads(args.specification.read_text(encoding="utf-8")))
  target = args.specification.with_name("geometry.usda")
  if args.check:
    if generated != target.read_text(encoding="utf-8"):
      parser.exit(1, f"{target} is out of date.\n")
  else:
    target.write_text(generated, encoding="utf-8")


if __name__ == "__main__":
  main()
