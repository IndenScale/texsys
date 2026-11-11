"""Read-only library discovery and explicit project references."""

import hashlib
import math
from pathlib import Path

import tomlkit

from texsys.dependencies import (
  LOCK_NAME,
  DependencySpec,
  _dependency_specs,
  _package_digest,
  _read_lock,
  _read_manifest,
  _read_package,
  _validate_name,
  add_dependency,
)
from texsys.formal import _load_json
from texsys.project import ProjectError, inspect_project

from .design import design_index

MAX_SOURCES = 16


def _model_catalog(root: Path, source: str, index: dict) -> None:
  relative = f"{source}/library/models.json"
  file = root / relative
  if not file.exists():
    return
  try:
    if file.resolve() != file or file.stat().st_size > 1024 * 1024:
      raise ProjectError("Model catalog must be a regular package file of at most 1 MiB.")
    data = _load_json(file.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1 or not isinstance(data.get("models"), dict)):
      raise ProjectError("Model catalog must declare schema_version 1 and a models object.")
    for key, model in data["models"].items():
      if not key.strip() or not isinstance(model, dict) or not all(
        isinstance(model.get(field), str) and model[field].strip()
        for field in ("family", "manufacturer", "model")
      ):
        raise ProjectError(f"Model {key!r} must declare family, manufacturer and model.")
      specs = model.get("specifications", {})
      if not isinstance(specs, dict) or not all(
        isinstance(value, (str, int)) or (isinstance(value, float) and math.isfinite(value))
        for value in specs.values()
      ):
        raise ProjectError(f"Model {key!r} specifications must contain scalar values.")
      if not isinstance(model.get("reference", ""), str):
        raise ProjectError(f"Model {key!r} reference must be text.")
  except (OSError, ValueError, ProjectError) as error:
    index["diagnostics"].append({"file": relative, "message": str(error)})
    return
  for key, model in data["models"].items():
    identity = f"{relative}#{key}"
    index["definitions"][identity] = {
      "name": model["model"], "file": relative, "instances": [], "ports": {}, "behavior": None,
      "connections": [], "model": {field: model[field] for field in
                                   ("family", "manufacturer", "model", "specifications", "reference") if field in model},
    }
    index["library"].append({"definition": identity, "kind": "models", "owner": None, "folder": None,
                             "tags": {"family": [model["family"]]}})


def _source(root: Path, folder: object, *, locked: bool = False) -> tuple[dict, dict]:
  if not isinstance(folder, str) or not folder.strip():
    raise ProjectError("Enter the path of a library folder.")
  path = root / Path(folder.strip()).expanduser()
  path = path.resolve()
  if path == root:
    raise ProjectError("The project library is already available.")
  workspace = inspect_project(root)
  if not locked and workspace is not None and path.is_relative_to((root / workspace.build).resolve()):
    raise ProjectError("Select the original library folder outside the project build directory.")
  if (path / "texsys.toml").is_symlink():
    raise ProjectError("A library manifest must be part of the selected directory.")
  project = inspect_project(path)
  if project is None:
    raise ProjectError(f"Library folder must contain texsys.toml: {path}")
  _validate_name(project.name)
  digest = _package_digest(path)
  version, _ = _read_package(DependencySpec(project.name, path, None))
  index = design_index(path, project.source)
  _model_catalog(path, project.source, index)
  if _package_digest(path) != digest:
    raise ProjectError("Library contents changed while reading. Refresh the library.")
  return {
    "id": hashlib.sha256(str(path).encode()).hexdigest(),
    "path": str(path), "name": project.name, "version": version, "digest": digest,
  }, index


def library_index(root: Path, source: str, folders: object) -> dict:
  if not isinstance(folders, list) or len(folders) > MAX_SOURCES or not all(isinstance(path, str) for path in folders):
    raise ProjectError(f"Select at most {MAX_SOURCES} library folders.")
  index = design_index(root, source)
  _model_catalog(root, source, index)
  studio = _read_manifest(root).get("studio", {})
  references = studio.get("library_references", []) if isinstance(studio, dict) else []
  index.update(sources=index.pop("dependency_sources", []), source_errors=[], references=[])
  if isinstance(references, list):
    for record in references:
      fields = ("package", "version", "digest", "entry")
      if isinstance(record, dict) and all(isinstance(record.get(key), str) for key in fields):
        index["references"].append({key: record[key] for key in fields})
  seen = {item["id"] for item in index["sources"]}
  attached = set()

  def append(folder: str, *, locked: bool = False, expected_digest: str | None = None) -> None:
    try:
      metadata, external = _source(root, folder, locked=locked)
      if expected_digest is not None and metadata["digest"] != expected_digest:
        raise ProjectError("The synchronized library differs from its project lock.")
    except (OSError, ValueError, ProjectError) as error:
      index["source_errors"].append({"path": folder, "message": str(error)})
      return
    source_id = metadata["id"]
    if source_id in seen:
      return
    seen.add(source_id)
    if not locked:
      attached.add((metadata["name"], metadata["digest"]))
    metadata["locked"] = locked
    index["sources"].append(metadata)
    keys = {key: f"external:{source_id}:{key}" for key in external["definitions"]}
    for key, definition in external["definitions"].items():
      for item in definition["instances"]:
        item["definition"] = keys.get(item["definition"])
      index["definitions"][keys[key]] = {**definition, "source": source_id, "entry": key}
    for item in external["library"]:
      index["library"].append({
        **item, "source": source_id, "definition": keys[item["definition"]],
        "owner": keys.get(item["owner"]), "folder": None,
      })
    for issue in external["diagnostics"]:
      index["diagnostics"].append({**issue, "file": f"{metadata['name']}: {issue['file']}"})

  for folder in folders:
    append(folder)
  if index["references"] and (root / LOCK_NAME).exists():
    try:
      _, packages = _read_lock(root)
    except ProjectError as error:
      index["source_errors"].append({"path": LOCK_NAME, "message": str(error)})
    else:
      referenced = {(record["package"], record["digest"]) for record in index["references"]}
      project = inspect_project(root)
      for package in packages:
        key = (package.name, package.digest)
        if key in referenced and key not in attached:
          folder = root / project.build / "dependencies" / package.name
          append(str(folder), locked=True, expected_digest=package.digest)
  return index


def reference_library_entry(root: Path, request: dict) -> dict:
  metadata, index = _source(root, request.get("folder"))
  if request.get("digest") != metadata["digest"]:
    raise ProjectError("Library contents changed. Refresh the library before referencing this entry.")
  entry = request.get("entry")
  item = next((item for item in index["library"] if item["definition"] == entry), None)
  if item is None or item["owner"] is not None:
    raise ProjectError("Select a public library entry.")
  name, path, version, digest = (metadata[key] for key in ("name", "path", "version", "digest"))
  record = {"package": name, "version": version, "digest": digest, "entry": entry, "kind": item["kind"]}
  previous_digest = request.get("previous_digest")
  if previous_digest is not None and (not isinstance(previous_digest, str) or previous_digest == digest):
    raise ProjectError("The previous library version is invalid. Refresh the library.")

  def record_reference(document, result):
    selected = next(package for package in result.packages if package.name == name)
    if selected.digest != digest:
      raise ProjectError("Library contents changed. Refresh the library before referencing this entry.")
    for dependency in _dependency_specs(_read_manifest(root), root):
      if dependency.name == name and dependency.root != selected.root and previous_digest is None:
        raise ProjectError(f"The project already uses a different library named '{name}'.")
    if (root / LOCK_NAME).exists():
      _, locked = _read_lock(root)
      current = {package.name: package for package in result.packages}
      for package in locked:
        if package.name == name and package.digest == previous_digest:
          continue
        replacement = current.get(package.name)
        if replacement is None or (replacement.root, replacement.digest) != (package.root, package.digest):
          raise ProjectError(f"Project dependency '{package.name}' changed. Review dependencies before adding a reference.")
    studio = document.setdefault("studio", tomlkit.table())
    if not isinstance(studio, dict):
      raise ProjectError("studio must be a table.")
    references = studio.setdefault("library_references", tomlkit.aot())
    if not isinstance(references, list) or any(not isinstance(value, dict) for value in references):
      raise ProjectError("studio.library_references must be an array of tables.")
    if previous_digest is not None:
      matching = [value for value in references if value.get("package") == name]
      if not matching or any(value.get("digest") != previous_digest for value in matching):
        raise ProjectError("Project library references changed. Refresh the library.")
      public = {(item["definition"], item["kind"]) for item in index["library"] if item["owner"] is None}
      for value in matching:
        if (value.get("entry"), value.get("kind")) not in public:
          raise ProjectError(f"Referenced entry is absent from the new library: {value.get('entry')}")
        value["version"] = version
        value["digest"] = digest
    else:
      if any(value.get("package") == name and value.get("digest") != digest for value in references):
        raise ProjectError("Update the project library explicitly before referencing another entry.")
      if record not in references:
        references.append(tomlkit.item(record))

  add_dependency(root, Path(path), version, on_resolved=record_reference)
  return record
