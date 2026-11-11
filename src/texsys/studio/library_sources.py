"""Read-only library discovery and explicit project references."""

import hashlib
import math
import shlex
from pathlib import Path

import tomlkit

from texsys.dependencies import (
  LOCK_NAME,
  DependencySpec,
  _atomic_write,
  _package_digest,
  _read_manifest,
  _read_package,
  _validate_name,
  lock_project,
)
from texsys.component_exports import component_exports, is_component_export
from texsys.formal import _load_json
from texsys.project import ProjectError, inspect_project, project_write_lock

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
  exports = component_exports(path)
  if not exports:
    raise ProjectError("This package does not declare component exports; use CLI package status for general dependencies.")
  index = design_index(path, project.source)
  _model_catalog(path, project.source, index)
  index["library"] = [item for item in index["library"]
                      if is_component_export(item["definition"].split("#", 1)[0], exports)]
  if _package_digest(path) != digest:
    raise ProjectError("Library contents changed while reading. Refresh the library.")
  identity = {"name": project.name, "version": version, "digest": digest}
  for key, definition in index["definitions"].items():
    if not key.startswith("@"):
      definition["package_identity"] = {**identity, "entry": key}
  return {
    "id": hashlib.sha256(str(path).encode()).hexdigest(),
    "path": str(path), **identity, "kind": "component", "exports": list(exports),
    "cli_hint": f"texsys add {shlex.quote(str(path))} -p {shlex.quote(str(root))}",
  }, index


def library_index(root: Path, source: str, folders: object) -> dict:
  root = root.expanduser().resolve()
  if not isinstance(folders, list) or len(folders) > MAX_SOURCES or not all(isinstance(path, str) for path in folders):
    raise ProjectError(f"Select at most {MAX_SOURCES} library folders.")
  index = design_index(root, source)
  _model_catalog(root, source, index)
  # Imported definitions remain available to the instance tree, but dependency
  # packages only enter the component browser through verified explicit exports.
  index["library"] = [entry for entry in index["library"] if not entry["definition"].startswith("@")]
  index.pop("dependency_sources", None)
  studio = _read_manifest(root).get("studio", {})
  references = studio.get("library_references", []) if isinstance(studio, dict) else []
  index.update(sources=[], requested_sources=[], source_errors=[], references=[])
  project = inspect_project(root)
  packages = ()
  if (root / LOCK_NAME).exists():
    try:
      packages = lock_project(root, check=True).packages
    except ProjectError as error:
      index["source_errors"].append({"path": LOCK_NAME, "message": str(error)})
  seen = set()

  def append(folder: str, package=None) -> str | None:
    locked = package is not None
    try:
      metadata, external = _source(root, folder, locked=locked)
      if locked and (metadata["name"], metadata["version"], metadata["digest"]) != (package.name, package.version, package.digest):
        raise ProjectError("The synchronized component package differs from its project lock.")
    except (OSError, ValueError, ProjectError) as error:
      index["source_errors"].append({"path": folder, "message": str(error)})
      return
    if locked:
      origin = f"registry:{package.registry}:{package.name}" if package.source == "registry" else str(package.root)
      source_id = hashlib.sha256(f"{origin}@{package.version}:{package.digest}".encode()).hexdigest()
      metadata.update(id=source_id, origin=package.source,
                      registry=str(package.registry) if package.registry else None, cli_hint="")
    else:
      source_id = metadata["id"]
    if source_id in seen:
      return source_id
    seen.add(source_id)
    metadata["locked"] = locked
    index["sources"].append(metadata)
    source_project = inspect_project(Path(metadata["path"]))
    prefix = f"{source_project.source}/"
    keys = {key: (key if key.startswith("@") else
                  f"@{package.name}/{key[len(prefix):]}" if locked and key.startswith(prefix)
                  else f"external:{source_id}:{key}") for key in external["definitions"]}
    for key, definition in external["definitions"].items():
      target = keys[key]
      if key.startswith("@"):
        index["definitions"].setdefault(target, definition)
        continue
      for item in definition["instances"]:
        item["definition"] = keys.get(item["definition"])
      index["definitions"][target] = {**definition, "source": source_id, "entry": key}
    existing = {item["definition"] for item in index["library"]}
    for item in external["library"]:
      if keys[item["definition"]] in existing:
        continue
      index["library"].append({
        **item, "source": source_id, "definition": keys[item["definition"]],
        "owner": keys.get(item["owner"]), "folder": None,
      })
    for issue in external["diagnostics"]:
      index["diagnostics"].append({**issue, "file": f"{metadata['name']}: {issue['file']}"})
    return source_id

  installed = {}
  for package in packages:
    folder = (root / project.build / "dependencies" / package.name).resolve()
    try:
      if not folder.is_dir() or _package_digest(folder) != package.digest:
        continue
      if not component_exports(folder):
        continue
    except (OSError, ValueError, ProjectError) as error:
      index["source_errors"].append({"path": str(folder), "message": str(error)})
      continue
    installed[str(folder)] = package
    installed[str(package.root)] = package
    append(str(folder), package)
  for folder in folders:
    path = (root / Path(folder).expanduser()).resolve()
    source_id = append(str(path), installed.get(str(path)))
    if source_id is not None:
      index["requested_sources"].append({"folder": folder, "source_id": source_id})
  if isinstance(references, list):
    locked_sources = {item["id"] for item in index["sources"] if item["locked"]}
    public = {(definition["package_identity"]["name"], definition["package_identity"]["entry"])
              for item in index["library"] if item.get("source") in locked_sources and item["owner"] is None
              if "package_identity" in (definition := index["definitions"][item["definition"]])}
    locked_by_name = {package.name: package for package in packages}
    for record in references:
      fields = ("package", "version", "digest", "entry")
      if not isinstance(record, dict) or not all(isinstance(record.get(key), str) for key in fields):
        continue
      package = locked_by_name.get(record["package"])
      state = "missing" if package is None or (record["package"], record["entry"]) not in public else (
        "current" if (record["version"], record["digest"]) == (package.version, package.digest) else "needs-review")
      index["references"].append({**{key: record[key] for key in fields}, "state": state})
  return index


def reference_library_entry(root: Path, request: dict) -> dict:
  """Record selection from installed component exports without changing dependencies."""
  root = root.expanduser().resolve()
  with project_write_lock(root):
    if not (root / LOCK_NAME).is_file():
      raise ProjectError("Import the component package with texsys add and texsys sync before selecting a component.")
    packages = lock_project(root, check=True).packages
    folder = request.get("folder")
    if not isinstance(folder, str) or not folder.strip():
      raise ProjectError("Select an installed component package.")
    path = (root / Path(folder).expanduser()).resolve()
    project = inspect_project(root)
    selected = next((package for package in packages
                     if path in {package.root, (root / project.build / "dependencies" / package.name).resolve()}), None)
    if selected is None:
      raise ProjectError("Import this component package with texsys add and texsys sync; component selection does not install packages.")
    installed = root / project.build / "dependencies" / selected.name
    if not installed.is_dir() or _package_digest(installed) != selected.digest:
      raise ProjectError("Run texsys sync before selecting a component from this package.")
    metadata, index = _source(root, str(path), locked=True)
    if ((metadata["name"], metadata["version"], metadata["digest"]) != (selected.name, selected.version, selected.digest)
        or request.get("digest") != selected.digest):
      raise ProjectError("Library contents changed. Refresh the library before referencing this entry.")
    entry = request.get("entry")
    item = next((item for item in index["library"] if item["definition"] == entry), None)
    if item is None or item["owner"] is not None:
      raise ProjectError("Select a public exported component entry.")
    name, version, digest = selected.name, selected.version, selected.digest
    record = {"package": name, "version": version, "digest": digest, "entry": entry, "kind": item["kind"]}
    previous_digest = request.get("previous_digest")
    if previous_digest is not None and (not isinstance(previous_digest, str) or previous_digest == digest):
      raise ProjectError("The previous component reference is invalid. Refresh the library.")
    manifest = root / "texsys.toml"
    before = manifest.read_text(encoding="utf-8")
    document = tomlkit.parse(before)
    studio = document.setdefault("studio", tomlkit.table())
    if not isinstance(studio, dict):
      raise ProjectError("studio must be a table.")
    references = studio.setdefault("library_references", tomlkit.aot())
    if not isinstance(references, list) or any(not isinstance(value, dict) for value in references):
      raise ProjectError("studio.library_references must be an array of tables.")
    if previous_digest is not None:
      matching = [value for value in references if value.get("package") == name]
      if not matching or any(value.get("digest") != previous_digest for value in matching):
        raise ProjectError("Project component references changed. Refresh the library.")
      public = {(item["definition"], item["kind"]) for item in index["library"] if item["owner"] is None}
      for value in matching:
        if (value.get("entry"), value.get("kind")) not in public:
          raise ProjectError(f"Referenced entry is absent from the installed package: {value.get('entry')}")
        value["version"] = version
        value["digest"] = digest
    else:
      if any(value.get("package") == name and value.get("digest") != digest for value in references):
        raise ProjectError("Review the project component references after the CLI dependency update.")
      if record not in references:
        references.append(tomlkit.item(record))
    if manifest.read_text(encoding="utf-8") != before or _package_digest(installed) != digest or _package_digest(path) != digest:
      raise ProjectError("Component selection inputs changed; refresh before retrying.")
    _atomic_write(manifest, tomlkit.dumps(document))
  return record
