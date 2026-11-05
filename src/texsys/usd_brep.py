"""Deterministic USD scene export with references to a BrepArray unit asset."""

import os
from pathlib import Path
import re
import shutil
import tempfile

from pxr import Sdf, Usd, UsdGeom

from texsys.geometry import Box


def _prim_name(identity: str) -> str:
  """Encode arbitrary SDL identities as stable, valid USD prim names."""
  encoded = "".join(
    character if character.isascii() and character.isalnum() else f"_{ord(character):02x}_"
    for character in identity
  )
  return encoded if re.match(r"[A-Za-z]", encoded) else f"box_{encoded}"


def write_box_scene(solids: tuple[Box, ...], target: Path, asset: Path) -> None:
  """Write boxes as transformed references; geometry operations stay elsewhere."""
  target = target.resolve()
  target.parent.mkdir(parents=True, exist_ok=True)
  bundled_asset = target.parent / "assets" / "unit_brep_cube.usda"
  bundled_asset.parent.mkdir(parents=True, exist_ok=True)
  shutil.copyfile(asset, bundled_asset)

  descriptor, temporary_name = tempfile.mkstemp(suffix=".usda", dir=target.parent)
  os.close(descriptor)
  temporary = Path(temporary_name)
  temporary.unlink()
  try:
    stage = Usd.Stage.CreateNew(str(temporary))
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    for solid in sorted(solids, key=lambda item: item.identity):
      prim = stage.DefinePrim(f"/World/{_prim_name(solid.identity)}", "Xform")
      prim.GetReferences().AddReference("./assets/unit_brep_cube.usda", "/World")
      prim.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String, custom=True).Set(
        solid.identity
      )
      transform = UsdGeom.Xformable(prim)
      translate = transform.AddTranslateOp()
      scale = transform.AddScaleOp()
      translate.Set(solid.origin)
      scale.Set(solid.size)
    stage.Save()
    stage = None
    os.replace(temporary, target)
  finally:
    temporary.unlink(missing_ok=True)
