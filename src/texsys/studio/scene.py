"""Read the newest generated USD scene as bounded preview geometry."""

import math
import os
from pathlib import Path
from threading import Lock

from pxr import Gf, Sdf, Tf, Usd, UsdGeom

from texsys.evidence import canonical_digest
from texsys.project import ProjectError


USD_SUFFIXES = {".usd", ".usda", ".usdc"}
_EXCLUDED_DIRECTORIES = {"assets", "dependencies", "studio"}
_MAX_LAYER_BYTES = 32 * 1024 * 1024
_MAX_LAYERS = 64
_MAX_ELEMENTS = 120_000
_BOX_FACES = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
              (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))


def _latest_scene(build: Path) -> Path | None:
  latest = None
  for directory, children, names in os.walk(build):
    children[:] = [name for name in children if name not in _EXCLUDED_DIRECTORIES and not name.startswith(".")]
    for name in names:
      path = Path(directory) / name
      if name.startswith(".") or path.suffix.lower() not in USD_SUFFIXES or path.is_symlink():
        continue
      try:
        if not path.is_file():
          continue
        candidate = (path.stat().st_mtime_ns, path)
      except FileNotFoundError:
        continue
      if latest is None or candidate > latest:
        latest = candidate
  return latest[1] if latest else None


def _scene_layers(path: Path, root: Path) -> dict[Path, Sdf.Layer]:
  layers = {}
  pending = [path]
  while pending:
    file = pending.pop().resolve()
    if file in layers:
      continue
    if not file.is_relative_to(root) or file.suffix.lower() not in USD_SUFFIXES:
      raise ProjectError("USD preview only loads local USD layers inside the project.")
    if not file.is_file():
      raise ProjectError(f"Missing USD layer: {file.relative_to(root)}")
    if len(layers) >= _MAX_LAYERS or file.stat().st_size > _MAX_LAYER_BYTES:
      raise ProjectError("USD layer limit exceeded for scene preview.")
    layer = Sdf.Layer.FindOrOpen(str(file))
    if layer is None:
      raise ProjectError(f"Cannot open USD layer: {file.relative_to(root)}")
    layer.Reload()
    layers[file] = layer
    pending.extend(file.parent / name for name in layer.GetCompositionAssetDependencies())
  return layers


def _box(low, high):
  points = [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
  return points, _BOX_FACES


def _geometry(stage):
  transforms = UsdGeom.XformCache()
  prims = list(stage.Traverse(Usd.TraverseInstanceProxies()))
  # Earlier texsys exports use Z-up meters without authoring stage metadata.
  legacy_layout = any(prim.HasAttribute("texsys:identity") for prim in prims)
  y_up = ((stage.HasAuthoredMetadata("upAxis") or not legacy_layout)
          and UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y)
  meters = 1 if legacy_layout and not stage.HasAuthoredMetadata("metersPerUnit") else UsdGeom.GetStageMetersPerUnit(stage)
  objects = []
  elements = 0
  for prim in prims:
    kind = prim.GetTypeName()
    if kind not in {"Mesh", "Cube", "BrepArray"} or UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
      continue
    if kind == "Mesh":
      mesh = UsdGeom.Mesh(prim)
      points = mesh.GetPointsAttr().Get() or ()
      indices = mesh.GetFaceVertexIndicesAttr().Get() or ()
      sizes = mesh.GetFaceVertexCountsAttr().Get() or ()
      if sum(sizes) != len(indices) or any(size < 3 for size in sizes) or any(i < 0 or i >= len(points) for i in indices):
        raise ProjectError(f"Invalid USD mesh topology: {prim.GetPath()}")
      faces, offset = [], 0
      for size in sizes:
        faces.append(list(indices[offset:offset + size]))
        offset += size
    elif kind == "Cube":
      half = UsdGeom.Cube(prim).GetSizeAttr().Get() / 2
      points, faces = _box((-half,) * 3, (half,) * 3)
    else:
      extent = prim.GetAttribute("brep:extent").Get()
      if not extent or len(extent) != 2:
        continue
      points, faces = _box(*extent)
    if not points or not faces:
      continue
    elements += len(points) + sum(map(len, faces))
    if elements > _MAX_ELEMENTS:
      raise ProjectError("USD geometry limit exceeded for scene preview.")
    transform = transforms.GetLocalToWorldTransform(prim)
    vertices = []
    for point in points:
      x, y, z = transform.Transform(Gf.Vec3d(*point)) * meters
      if not all(map(math.isfinite, (x, y, z))):
        raise ProjectError(f"Non-finite USD geometry: {prim.GetPath()}")
      vertices.append((x, -z, y) if y_up else (x, y, z))
    owner = prim
    while owner and not owner.HasAttribute("texsys:identity"):
      owner = owner.GetParent()
    name = owner.GetAttribute("texsys:identity").Get() if owner else prim.GetName()
    objects.append({"name": name, "points": vertices, "faces": faces, "envelope": kind == "BrepArray"})
  return objects


def _revision(files):
  records = []
  for path in sorted(files):
    try:
      stat = path.stat()
      records.append((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns))
    except FileNotFoundError:
      records.append((str(path),))
  return canonical_digest(records)


class ScenePreview:
  def __init__(self, root: Path, build: str):
    self.root = root.resolve()
    self.build = self.root / build
    self.lock = Lock()
    self.path = None
    self.files = ()
    self.scene = None

  def read(self, known_revision: str = "") -> dict:
    with self.lock:
      path = _latest_scene(self.build)
      if path is None:
        self.path, self.files, self.scene = None, (), None
        return {"status": "empty", "directory": self.build.relative_to(self.root).as_posix()}
      if path != self.path or self.scene is None or _revision(self.files) != self.scene["revision"]:
        try:
          initial = _revision((path,))
          layers = _scene_layers(path, self.root)
          files = tuple(layers)
          revision = _revision(files)
          stage = Usd.Stage.Open(layers[path.resolve()], load=Usd.Stage.LoadNone)
          if stage is None:
            raise ProjectError(f"Cannot open USD stage: {path.relative_to(self.root)}")
          objects = _geometry(stage)
          if _revision((path,)) != initial or _revision(files) != revision:
            raise ProjectError("USD scene changed while reading. Preview will retry automatically.")
        except Tf.ErrorException as error:
          raise ProjectError(f"Cannot preview {path.relative_to(self.root)}: {error}") from error
        self.path, self.files = path, files
        self.scene = {"status": "ready", "path": path.relative_to(self.root).as_posix(),
                      "revision": revision, "objects": objects}
      if known_revision == self.scene["revision"]:
        return {"status": "unchanged"}
      return self.scene
