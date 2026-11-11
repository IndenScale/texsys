"""Generate repeatable concept cabinet geometry from a module specification."""

import argparse
import math
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.formal import _load_json


def geometry_usda(spec):
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Module").GetPrim())
  UsdGeom.Scope.Define(stage, "/Module/Geometry")
  UsdGeom.Scope.Define(stage, "/Module/Ports")
  envelope = spec["envelope_m"]
  if len(envelope) != 3 or any(not math.isfinite(value) or value <= 0 for value in envelope):
    raise ValueError("Cooling equipment requires a finite positive cabinet envelope.")
  for name, part in spec["properties"]["geometry_parts"].items():
    origin, size = part["origin_m"], part["size_m"]
    if any(lo < 0 or length <= 0 or lo + length > hi + 1e-9
           for lo, length, hi in zip(origin, size, envelope, strict=True)):
      raise ValueError(f"Cabinet part {name} is outside the declared envelope.")
    cube = UsdGeom.Cube.Define(stage, f"/Module/Geometry/{name}")
    cube.CreateSizeAttr(1)
    cube.AddTranslateOp().Set(Gf.Vec3d(*(a + b / 2 for a, b in zip(origin, size, strict=True))))
    cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*size))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*part["color"])])
  for name, record in spec["interfaces"].items():
    position, normal = record["position_m"], record["normal"]
    if any(not 0 <= value <= extent for value, extent in zip(position, envelope, strict=True)):
      raise ValueError(f"Port {name} lies outside the cabinet.")
    axis = next((i for i, value in enumerate(normal) if value != 0), None)
    if (axis is None or sum(abs(value) for value in normal) != 1 or abs(normal[axis]) != 1
        or not math.isclose(position[axis], envelope[axis] if normal[axis] > 0 else 0., abs_tol=1e-9)):
      raise ValueError(f"Port {name} must lie on its outward cabinet face.")
    port = UsdGeom.Xform.Define(stage, f"/Module/Ports/{name}")
    port.AddTranslateOp().Set(Gf.Vec3d(*position))
    port.GetPrim().CreateAttribute("texsys:normal", Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(*normal))
  return stage.GetRootLayer().ExportToString()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("specification", type=Path)
  parser.add_argument("--check", action="store_true")
  args = parser.parse_args()
  geometry = args.specification.with_name("geometry.usda")
  result = geometry_usda(_load_json(args.specification.read_text(encoding="utf-8")))
  if args.check:
    if geometry.read_text(encoding="utf-8") != result:
      raise SystemExit(f"Geometry does not match specification: {geometry}")
  else:
    geometry.write_text(result, encoding="utf-8")


if __name__ == "__main__":
  main()
