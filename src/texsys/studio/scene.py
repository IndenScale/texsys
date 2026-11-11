"""Preview named USD documents, editor buffers and immutable build snapshots."""

import math
from pathlib import Path
from threading import Lock

from pxr import Gf, Sdf, Tf, Usd, UsdGeom

from texsys.evidence import canonical_digest, file_digest
from texsys.project import ProjectError

from .preview import ProjectPreview, _inside
from .files import MAX_PREVIEW_BYTES, file_version, version as content_version, workspace_path


USD_SUFFIXES = {".usd", ".usda", ".usdc"}
_MAX_LAYER_BYTES = 32 * 1024 * 1024
_MAX_LAYERS = 64
_MAX_ELEMENTS = 120_000
_BOX_FACES = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
              (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))


def _scene_layers(path: Path, root: Path, validate) -> dict[Path, Sdf.Layer]:
  layers = {}
  pending = [path]
  while pending:
    candidate = pending.pop()
    if any(part.is_symlink() for part in (candidate, *candidate.parents)):
      raise ProjectError("USD preview does not load symbolic links.")
    file = candidate.resolve()
    if file in layers:
      continue
    if not file.is_relative_to(root) or file.suffix.lower() not in USD_SUFFIXES:
      raise ProjectError("USD preview only loads recorded layers inside its build snapshot.")
    validate(file)
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


def _geometry(stage, source_map):
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
    identity = None
    while owner:
      identity = (owner.GetAttribute("texsys:identity").Get() if owner.HasAttribute("texsys:identity")
                  else owner.GetCustomDataByKey("identity"))
      if identity:
        break
      owner = owner.GetParent()
    prim_path = str(prim.GetPath())
    objects.append({"id": prim_path, "prim_path": prim_path, "name": identity or prim.GetName(),
                    "semantic_ref": identity, "source": source_map.get(identity),
                    "points": vertices, "faces": faces,
                    "envelope": kind == "BrepArray" or bool(prim.GetCustomDataByKey("envelope"))})
  return objects


class ScenePreview:
  def __init__(self, root: Path, build: str):
    self.root = root.resolve()
    self.build = self.root / build
    self.previews = ProjectPreview(self.root, build)
    self.lock = Lock()
    self.scene = None

  def read_file(self, relative: str, known_revision: str = "", *, version: str = "", content: str | None = None) -> dict:
    """Preview the current document or an unsaved USDA buffer without writing it."""
    from .runtime import Conflict

    with self.lock:
      build = self.build.relative_to(self.root).as_posix()
      path = workspace_path(self.root, relative, build)
      if not path.is_file() or path.suffix.lower() not in USD_SUFFIXES:
        raise ProjectError("Scene preview requires a USD document.")
      baseline = file_version(path)
      if version and version != baseline:
        raise Conflict("文件已被外部修改，请刷新后预览。")
      if content is not None and (not isinstance(content, str) or "\0" in content or len(content.encode("utf-8")) > MAX_PREVIEW_BYTES):
        raise ProjectError("场景预览文本必须是最多 1 MiB 的 UTF-8 字符串。")
      declarations = self.previews.file_entries(relative)
      source_map = declarations[0].get("preview", {}).get("source_map", {}) if declarations else {}
      if not isinstance(source_map, dict):
        source_map = {}
      digests = {}

      def validate(file):
        try:
          local = file.resolve().relative_to(self.root).as_posix()
        except ValueError as error:
          raise ProjectError("USD dependencies must stay inside the project workspace.") from error
        checked = workspace_path(self.root, local, build)
        if not checked.is_file():
          raise ProjectError("Missing USD document dependency.")
        actual = file_digest(checked)
        if checked in digests and digests[checked] != actual:
          raise ProjectError("USD dependency changed while reading; retry the preview.")
        digests[checked] = actual

      try:
        if content is None:
          layers = _scene_layers(path, self.root, validate)
          layer = layers[path]
          document_digest = digests[path]
        else:
          layer = Sdf.Layer.CreateAnonymous(path.name + ".usda")
          if not layer.ImportFromString(content):
            raise ProjectError("无法解析当前 USD 文本。")
          dependencies = layer.GetCompositionAssetDependencies()
          if len(dependencies) >= _MAX_LAYERS:
            raise ProjectError("USD layer limit exceeded for scene preview.")
          layers = {}
          for name in dependencies:
            dependency = path.parent / name
            layers.update(_scene_layers(dependency, self.root, validate))
            if len(layers) >= _MAX_LAYERS:
              raise ProjectError("USD layer limit exceeded for scene preview.")
            # The anonymous layer has no file anchor. Bind only dependencies
            # already validated against the original document's directory.
            layer.UpdateCompositionAssetDependency(name, str(dependency.resolve()))
          document_digest = content_version(content.encode("utf-8"))
        revision = canonical_digest({"path": relative, "content": document_digest,
                                     "layers": {str(file): value for file, value in digests.items()}, "source_map": source_map})
        if self.scene is None or self.scene["revision"] != revision:
          stage = Usd.Stage.Open(layer, load=Usd.Stage.LoadAll)
          if stage is None:
            raise ProjectError("Cannot open the current USD document.")
          objects = _geometry(stage, source_map)
          for file in digests:
            validate(file)
          if file_version(path) != baseline:
            raise Conflict("文件在预览期间发生变化，请重试。")
          self.scene = {"status": "ready", "path": relative, "revision": revision, "version": baseline,
                        "content_digest": document_digest, "objects": objects, "source_map": source_map}
      except Tf.ErrorException as error:
        raise ProjectError(f"Cannot preview current USD document: {error}") from error
      self.scene["version"] = baseline
      if known_revision == self.scene["revision"]:
        return {"status": "unchanged", "path": relative, "version": baseline}
      return self.scene

  def read(self, known_revision: str = "", *, run: str = "", target: str = "", artifact: str = "") -> dict:
    with self.lock:
      if not artifact:
        # Compatibility for an existing scene tab: select a declared scene from
        # the manifest, never discover arbitrary files by modification time.
        available = self.previews.read()["artifacts"]
        scenes = [item for item in available if item["kind"] == "scene" and item["available"]]
        if len(scenes) > 1:
          raise ProjectError("Multiple scene artifacts are available; select an explicit artifact and build run.")
        selected = scenes[0] if scenes else None
        if selected is None:
          return {"status": "empty", "directory": self.build.relative_to(self.root).as_posix()}
        run, target, artifact = selected["run_id"], selected["target"], selected["id"]
      report, record, path, selected = self.previews.resolve(run, target, artifact)
      if selected["kind"] != "scene" or path.suffix.lower() not in USD_SUFFIXES:
        raise ProjectError("Selected artifact is not a USD scene.")
      snapshot = self.build / "runs" / report["run_id"] / "files"
      recorded = {item["path"]: item["digest"] for item in (*report.get("inputs", []), *report["artifacts"])}

      def validate(file):
        relative = file.relative_to(snapshot).as_posix()
        checked = _inside(self.root, file.relative_to(self.root).as_posix(), snapshot)
        if relative not in recorded or file_digest(checked) != recorded[relative]:
          raise ProjectError("USD composition layer is missing from or differs from the build manifest.")

      source_map = record["preview"].get("source_map", {})
      if not isinstance(source_map, dict):
        raise ProjectError("Scene source map must be an object.")
      try:
        layers = _scene_layers(path, snapshot, validate)
        revision = canonical_digest({"artifact": selected, "layers": {str(file): file_digest(file) for file in layers}})
        if self.scene is None or self.scene["revision"] != revision:
          stage = Usd.Stage.Open(layers[path.resolve()], load=Usd.Stage.LoadAll)
          if stage is None:
            raise ProjectError(f"Cannot open USD stage: {record['path']}")
          objects = _geometry(stage, source_map)
          for file in layers:
            validate(file)
          self.scene = {"status": "ready", "path": record["path"], "revision": revision,
                        "objects": objects, "artifact": selected, "source_map": source_map,
                        "current": selected["current"], "freshness": selected["freshness"]}
      except Tf.ErrorException as error:
        raise ProjectError(f"Cannot preview {record['path']}: {error}") from error
      if known_revision == self.scene["revision"]:
        return {"status": "unchanged"}
      return self.scene
