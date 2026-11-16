"""Preview named USD documents, editor buffers and immutable build snapshots."""

import math
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from time import monotonic

from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade

from texsys.evidence import canonical_digest, file_digest
from texsys.project import ProjectError

from .preview import ProjectPreview, _directory_stamp, _inside, file_stamp
from .files import MAX_PREVIEW_BYTES, file_version, version as content_version, workspace_path


USD_SUFFIXES = {".usd", ".usda", ".usdc"}
_MAX_LAYER_BYTES = 32 * 1024 * 1024
_MAX_LAYERS = 64
_MAX_ELEMENTS = 300_000
_MAX_CACHED_SCENES = 6
_SCENE_CACHE_BYTES = 96 * 1024 * 1024
_DRAFT_CACHE_SECONDS = 10
_BOX_FACES = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
              (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))


def _scene_layers(path: Path, root: Path, validate, watch) -> dict[Path, Sdf.Layer]:
  layers = {}
  pending = [path]
  while pending:
    candidate = pending.pop()
    if any(part.is_symlink() for part in (candidate, *candidate.parents)):
      raise ProjectError("USD preview does not load symbolic links.")
    watch(candidate)
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


_FACETS = ("discipline", "level", "zone", "material", "role")


def _inspectable(value):
  if type(value) in (str, bool, int):
    return value[:512] if isinstance(value, str) else value
  if type(value) is float and math.isfinite(value):
    return value
  return None


def _authored_metadata(node):
  facets, properties = {}, {}
  for facet in _FACETS:
    attr = node.GetAttribute(f"texsys:{facet}")
    value = attr.Get() if attr else node.GetCustomDataByKey(f"texsys:{facet}")
    if isinstance(value, str) and value.strip():
      facets[facet] = value.strip()[:128]
  path = str(node.GetPath())
  for attr in node.GetAuthoredAttributes():
    if len(properties) >= 32:
      break
    if not attr.IsCustom() or attr.GetTypeName().isArray:
      continue
    value = _inspectable(attr.Get())
    if value is not None:
      item = {"value": value, "prim": path}
      unit = attr.GetCustomDataByKey("unit")
      if isinstance(unit, str) and unit:
        item["unit"] = unit[:32]
      properties[attr.GetName()] = item
  return facets, properties, path if node.HasAttribute("texsys:assembly") else None


def _scene_metadata(prim, collections, authored):
  facets, properties = {}, {}
  assembly = None
  node = prim
  while node and not node.IsPseudoRoot():
    path = node.GetPath()
    if path not in authored:
      authored[path] = _authored_metadata(node)
    local_facets, local_properties, local_assembly = authored[path]
    assembly = assembly or local_assembly
    for name, value in local_facets.items():
      facets.setdefault(name, value)
    for name, value in local_properties.items():
      if len(properties) >= 32:
        break
      properties.setdefault(name, value)
    node = node.GetParent()
  if "material" not in facets:
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if material:
      facets["material"] = str(material.GetPath())
  memberships = [name for name, query in collections if query.IsPathIncluded(prim.GetPath())]
  color_var = UsdGeom.PrimvarsAPI(prim).FindPrimvarWithInheritance("displayColor")
  colors = color_var.Get() if color_var else None
  color = tuple(colors[0]) if colors and len(colors) == 1 and all(map(math.isfinite, colors[0])) else None
  return {"facets": facets, "properties": properties, "collections": memberships, "color": color,
          **({"owner_path": assembly} if assembly else {})}


def _geometry(stage, source_map):
  transforms = UsdGeom.XformCache()
  prims = list(stage.Traverse(Usd.TraverseInstanceProxies()))
  collections = [(str(collection.GetCollectionPath()), collection.ComputeMembershipQuery())
                 for prim in prims for collection in Usd.CollectionAPI.GetAllCollections(prim)]
  # Earlier texsys exports use Z-up meters without authoring stage metadata.
  legacy_layout = any(prim.HasAttribute("texsys:identity") for prim in prims)
  y_up = ((stage.HasAuthoredMetadata("upAxis") or not legacy_layout)
          and UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y)
  meters = 1 if legacy_layout and not stage.HasAuthoredMetadata("metersPerUnit") else UsdGeom.GetStageMetersPerUnit(stage)
  objects = []
  authored = {}
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
    source = source_map.get(prim_path) or source_map.get(identity)
    metadata = _scene_metadata(prim, collections, authored)
    objects.append({"id": prim_path, "prim_path": prim_path, "name": identity or prim.GetName(),
                    "semantic_ref": identity, "source": source, "type": kind,
                    "owner_path": str(owner.GetPath()) if identity else prim_path,
                    "points": vertices, "faces": faces,
                    "envelope": kind == "BrepArray" or bool(prim.GetCustomDataByKey("envelope")), **metadata})
  return objects


class ScenePreview:
  def __init__(self, root: Path, build: str, *, previews: ProjectPreview | None = None):
    self.root = root.resolve()
    self.build = self.root / build
    self.previews = previews if previews is not None else ProjectPreview(self.root, build)
    # OpenUSD's layer registry is shared. A miss can reload layers, so keep
    # composition serialized while allowing verified hits during extraction.
    self.lock = Lock()
    self._cache_lock = Lock()
    self._scenes = OrderedDict()
    self._draft = None
    self._cache_bytes = 0

  @staticmethod
  def _watch(stamps, file):
    for parent in file.parents:
      if parent not in stamps:
        stamps[parent] = (_directory_stamp, _directory_stamp(parent))
    if file not in stamps:
      stamps[file] = (file_stamp, file_stamp(file))

  @staticmethod
  def _unchanged(stamps):
    try:
      return all(read(path) == stamp for path, (read, stamp) in stamps.items())
    except (OSError, ProjectError):
      return False

  def _cached(self, key, matches, *, draft=False):
    with self._cache_lock:
      if self._draft and self._draft[3] <= monotonic():
        self._draft = None
      if draft:
        entry = self._draft[:3] if self._draft and self._draft[0] == key else None
        if entry:
          _, scene, stamps = entry
      else:
        entry = self._scenes.get(key)
        if entry:
          scene, stamps, _ = entry
    if entry and matches(scene) and self._unchanged(stamps):
      if not draft:
        with self._cache_lock:
          if self._scenes.get(key) is entry:
            self._scenes.move_to_end(key)
      return scene
    return None

  def _remember(self, key, scene, stamps, *, draft=False):
    # Estimate Python object storage, including the geometry's boxed numbers.
    weight = 1024 * len(scene["objects"]) + sum(
      176 * len(obj["points"]) + 144 * len(obj["faces"])
      + sum(256 + 4 * len(str(prop["value"])) for prop in obj["properties"].values())
      for obj in scene["objects"])
    with self._cache_lock:
      if draft:
        if weight <= _SCENE_CACHE_BYTES // 8:
          self._draft = (key, scene, stamps, monotonic() + _DRAFT_CACHE_SECONDS)
        return
      previous = self._scenes.pop(key, None)
      if previous:
        self._cache_bytes -= previous[2]
      if weight > _SCENE_CACHE_BYTES:
        return
      self._scenes[key] = (scene, stamps, weight)
      self._cache_bytes += weight
      while len(self._scenes) > _MAX_CACHED_SCENES or self._cache_bytes > _SCENE_CACHE_BYTES:
        _, (_, _, removed) = self._scenes.popitem(last=False)
        self._cache_bytes -= removed

  def read_file(self, relative: str, known_revision: str = "", *, version: str = "", content: str | None = None) -> dict:
    """Preview the current document or an unsaved USDA buffer without writing it."""
    from .runtime import Conflict

    build = self.build.relative_to(self.root).as_posix()
    path = workspace_path(self.root, relative, build)
    if not path.is_file() or path.suffix.lower() not in USD_SUFFIXES:
      raise ProjectError("Scene preview requires a USD document.")
    if content is not None and (not isinstance(content, str) or "\0" in content or len(content.encode("utf-8")) > MAX_PREVIEW_BYTES):
      raise ProjectError("场景预览文本必须是最多 1 MiB 的 UTF-8 字符串。")
    draft = content is not None
    draft_digest = content_version(content.encode("utf-8")) if draft else None
    key = ("file", relative, draft_digest)

    def mapping():
      declarations = self.previews.file_entries(relative, include_artifact=False) if path.is_relative_to(self.build) else []
      source = declarations[0].get("preview", {}).get("source_map", {}) if declarations else {}
      return source if isinstance(source, dict) else {}

    def response(scene):
      if version and version != scene["version"]:
        raise Conflict("文件已被外部修改，请刷新后预览。")
      if known_revision == scene["revision"]:
        return {"status": "unchanged", "path": relative, "version": scene["version"]}
      return scene

    source_map = mapping()
    cached = self._cached(key, lambda scene: scene["source_map"] == source_map, draft=draft)
    if cached:
      return response(cached)
    with self.lock:
      # A request waiting for composition must use the publication visible
      # now, which may be newer than the one observed before it entered.
      source_map = mapping()
      cached = self._cached(key, lambda scene: scene["source_map"] == source_map, draft=draft)
      if cached:
        return response(cached)
      stamps = {}
      self._watch(stamps, path)
      baseline = file_version(path)
      if version and version != baseline:
        raise Conflict("文件已被外部修改，请刷新后预览。")
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
          layers = _scene_layers(path, self.root, validate, lambda file: self._watch(stamps, file))
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
            layers.update(_scene_layers(dependency, self.root, validate, lambda file: self._watch(stamps, file)))
            if len(layers) >= _MAX_LAYERS:
              raise ProjectError("USD layer limit exceeded for scene preview.")
            # The anonymous layer has no file anchor. Bind only dependencies
            # already validated against the original document's directory.
            layer.UpdateCompositionAssetDependency(name, str(dependency.resolve()))
          document_digest = draft_digest
        revision = canonical_digest({"path": relative, "content": document_digest,
                                     "layers": {str(file): value for file, value in digests.items()}, "source_map": source_map})
        stage = Usd.Stage.Open(layer, load=Usd.Stage.LoadAll)
        if stage is None:
          raise ProjectError("Cannot open the current USD document.")
        objects = _geometry(stage, source_map)
        for file in digests:
          validate(file)
        if not self._unchanged(stamps) or file_version(path) != baseline or mapping() != source_map:
          raise Conflict("文件或来源映射在预览期间发生变化，请重试。")
        scene = {"status": "ready", "path": relative, "revision": revision, "version": baseline,
                 "content_digest": document_digest, "objects": objects, "source_map": source_map}
      except Tf.ErrorException as error:
        raise ProjectError(f"Cannot preview current USD document: {error}") from error
      self._remember(key, scene, stamps, draft=draft)
      return response(scene)

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
      key = ("snapshot", run, target, artifact)
      cached = self._cached(key, lambda scene: scene["artifact"] == selected)
      if cached:
        return {"status": "unchanged"} if known_revision == cached["revision"] else cached
      stamps = {}
      try:
        layers = _scene_layers(path, snapshot, validate, lambda file: self._watch(stamps, file))
        revision = canonical_digest({"artifact": selected, "layers": {str(file): file_digest(file) for file in layers}})
        stage = Usd.Stage.Open(layers[path.resolve()], load=Usd.Stage.LoadAll)
        if stage is None:
          raise ProjectError(f"Cannot open USD stage: {record['path']}")
        objects = _geometry(stage, source_map)
        for file in layers:
          validate(file)
        if not self._unchanged(stamps):
          raise ProjectError("USD snapshot changed while reading; retry the preview.")
        scene = {"status": "ready", "path": record["path"], "revision": revision,
                 "objects": objects, "artifact": selected, "source_map": source_map,
                 "current": selected["current"], "freshness": selected["freshness"]}
      except Tf.ErrorException as error:
        raise ProjectError(f"Cannot preview {record['path']}: {error}") from error
      self._remember(key, scene, stamps)
      return {"status": "unchanged"} if known_revision == revision else scene
