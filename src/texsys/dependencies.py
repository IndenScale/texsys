"""Path and immutable-registry design dependency resolution and synchronization."""

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib
from typing import Any

import tomlkit
from tomlkit.toml_document import TOMLDocument

from texsys.project import MANIFEST_NAME, ProjectError, inspect_project, project_write_lock

LOCK_NAME = "texsys.lock"
LOCK_SCHEMA_VERSION = 2
_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class DependencySpec:
  name: str
  root: Path | None
  version: str | None
  registry: Path | None = None


@dataclass
class LockedPackage:
  name: str
  version: str
  root: Path
  digest: str
  dependencies: tuple[str, ...] = ()
  direct: bool = False
  source: str = "path"
  registry: Path | None = None
  archive_digest: str | None = None


@dataclass(frozen=True)
class LockResult:
  packages: tuple[LockedPackage, ...]
  text: str


def _read_manifest(root: Path) -> dict[str, Any]:
  path = root / MANIFEST_NAME
  try:
    with path.open("rb") as stream:
      return tomllib.load(stream)
  except (OSError, tomllib.TOMLDecodeError) as error:
    raise ProjectError(f"Cannot read {path}: {error}") from error


def _validate_name(name: object) -> str:
  if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
    raise ProjectError(f"Invalid dependency name: {name!r}")
  return name


def _dependency_specs(data: dict[str, Any], base: Path, *, registry_origin: Path | None = None) -> tuple[DependencySpec, ...]:
  declarations = data.get("dependencies", {})
  if not isinstance(declarations, dict):
    raise ProjectError("[dependencies] must be a table.")

  specs: list[DependencySpec] = []
  for raw_name, declaration in declarations.items():
    name = _validate_name(raw_name)
    if not isinstance(declaration, dict):
      raise ProjectError(f"Dependency '{name}' must be an inline table.")
    if set(declaration) - {"path", "registry", "version"} or ("path" in declaration) == ("registry" in declaration):
      raise ProjectError(f"Dependency '{name}' requires exactly one of 'path' or 'registry', plus optional exact version.")
    if "registry" in declaration:
      raw_registry = declaration["registry"]
      version = declaration.get("version")
      if not isinstance(raw_registry, str) or not raw_registry or not isinstance(version, str) or not version:
        raise ProjectError(f"Registry dependency '{name}' requires a registry directory and exact version.")
      _validate_name(version)
      origin = registry_origin if raw_registry == "." and registry_origin is not None else Path(raw_registry).expanduser()
      if not origin.is_absolute():
        origin = base / origin
      specs.append(DependencySpec(name, None, version, origin.resolve()))
      continue
    raw_path = declaration.get("path")
    if not isinstance(raw_path, str) or not raw_path:
      raise ProjectError(f"Dependency '{name}' requires a non-empty path.")
    version = declaration.get("version")
    if version is not None and (not isinstance(version, str) or not version):
      raise ProjectError(f"Dependency '{name}' version must be a non-empty string.")
    package_root = Path(raw_path).expanduser()
    if not package_root.is_absolute():
      package_root = base / package_root
    specs.append(DependencySpec(name, package_root.resolve(), version))
  return tuple(sorted(specs, key=lambda spec: spec.name))


def _relative_path(root: Path, target: Path) -> str:
  return Path(os.path.relpath(target, root)).as_posix()


def _manifest_digest(specs: tuple[DependencySpec, ...], root: Path) -> str:
  canonical = [
    ({"name": spec.name, "registry": _relative_path(root, spec.registry), "version": spec.version}
     if spec.registry is not None else
     {"name": spec.name, "path": _relative_path(root, spec.root), "version": spec.version})
    for spec in specs
  ]
  payload = json.dumps(
    canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
  ).encode("utf-8")
  return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _package_files(root: Path) -> tuple[Path, ...]:
  project = inspect_project(root)
  if project is None:
    raise ProjectError(f"Dependency is not a texsys project: {root}")

  files = [root / MANIFEST_NAME]
  if files[0].is_symlink():
    raise ProjectError("Package manifest cannot use a symlink.")
  for relative_directory in (project.source, project.assets):
    directory = root / relative_directory
    if directory.is_symlink() or not directory.is_dir():
      raise ProjectError(f"Dependency layout is not a directory: {directory}")
    for path in directory.rglob("*"):
      relative = path.relative_to(root)
      if (
        "__pycache__" in relative.parts
        or path.suffix == ".pyc"
        or any(part.endswith(".egg-info") for part in relative.parts)
      ):
        continue
      if path.is_symlink():
        raise ProjectError(f"Dependency content cannot contain symlinks: {path}")
      if path.is_file():
        files.append(path)
      elif not path.is_dir():
        raise ProjectError(f"Dependency content is not a regular file: {path}")
  package = _read_manifest(root).get("package", {})
  if not isinstance(package, dict):
    raise ProjectError("[package] must be a table.")
  if package:
    include = package.get("include", [])
    if not isinstance(include, list) or not all(isinstance(item, str) and item for item in include):
      raise ProjectError("package.include must be a list of relative files or directories.")
    if (root / "pyproject.toml").exists():
      include = [*include, "pyproject.toml"]
    from texsys.registry import _canonical_member
    for relative in include:
      _canonical_member(relative)
      target = root / relative
      if (target.resolve() != target or not target.exists()
          or target.is_relative_to(root / project.build)
          or any(part in {".git", "refs", "reviews", ".env", ".venv", "node_modules"} for part in target.relative_to(root).parts)):
        raise ProjectError(f"Package include is missing, linked or outside distributable inputs: {relative}")
      children = target.rglob("*") if target.is_dir() else [target]
      for file in children:
        if file.is_symlink() or not (file.is_file() or file.is_dir()):
          raise ProjectError(f"Package include contains a non-regular file: {file}")
        if any(part in {".git", "refs", "reviews", ".env", ".venv", "node_modules"} for part in file.relative_to(root).parts):
          raise ProjectError(f"Package include contains private or generated content: {file}")
        if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
          files.append(file)
  return tuple(sorted(set(files), key=lambda path: path.relative_to(root).as_posix()))


def _package_digest(root: Path) -> str:
  digest = hashlib.sha256()
  for path in _package_files(root):
    relative = path.relative_to(root).as_posix().encode("utf-8")
    content = path.read_bytes()
    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
  return f"sha256:{digest.hexdigest()}"


def _read_package(spec: DependencySpec) -> tuple[str, dict[str, Any]]:
  project = inspect_project(spec.root)
  if project is None:
    raise ProjectError(f"Dependency is not initialized: {spec.root}")
  if project.name != spec.name:
    raise ProjectError(
      f"Dependency name mismatch: declared '{spec.name}', found '{project.name}'."
    )
  data = _read_manifest(spec.root)
  version = data["project"].get("version")
  if not isinstance(version, str) or not version:
    raise ProjectError(f"Dependency '{spec.name}' must declare project.version.")
  if spec.version is not None and version != spec.version:
    raise ProjectError(
      f"Dependency '{spec.name}' requires version {spec.version}, found {version}."
    )
  return version, data


def _lock_text(
  specs: tuple[DependencySpec, ...], packages: tuple[LockedPackage, ...], root: Path
) -> str:
  lines = [
    f"schema_version = {LOCK_SCHEMA_VERSION if any(item.source == 'registry' for item in packages) else 1}",
    f'manifest_digest = "{_manifest_digest(specs, root)}"',
  ]
  for package in packages:
    dependencies = ", ".join(json.dumps(name) for name in package.dependencies)
    lines.extend(
      [
        "",
        "[[packages]]",
        f"name = {json.dumps(package.name, ensure_ascii=False)}",
        f"version = {json.dumps(package.version, ensure_ascii=False)}",
        f"source = {json.dumps(package.source)}",
        *( [f"registry = {json.dumps(_relative_path(root, package.registry), ensure_ascii=False)}",
             f"archive_digest = {json.dumps(package.archive_digest)}"] if package.source == "registry" else
           [f"path = {json.dumps(_relative_path(root, package.root), ensure_ascii=False)}"] ),
        f'digest = "{package.digest}"',
        f"direct = {str(package.direct).lower()}",
        f"dependencies = [{dependencies}]",
      ]
    )
  return "\n".join(lines) + "\n"


def resolve_dependencies(
  root: Path, data: dict[str, Any] | None = None, *,
  pinned: dict[str, LockedPackage] | None = None, allow_fetch: bool = True,
) -> LockResult:
  """Resolve the complete graph; registry releases may populate the content cache."""
  root = root.expanduser().resolve()
  if inspect_project(root) is None:
    raise ProjectError(f"Project is not initialized: {root}")
  data = _read_manifest(root) if data is None else data
  specs = _dependency_specs(data, root)
  packages: dict[str, LockedPackage] = {}
  package_roots: dict[Path, str] = {}
  visiting: list[str] = []

  def visit(spec: DependencySpec, direct: bool) -> None:
    if spec.name in visiting:
      cycle = " -> ".join([*visiting, spec.name])
      raise ProjectError(f"Dependency cycle detected: {cycle}")
    if spec.registry is not None:
      from texsys.registry import package_details, materialize_package
      fixed = pinned.get(spec.name) if pinned is not None else None
      if pinned is not None:
        if (fixed is None or fixed.source != "registry" or fixed.registry != spec.registry or fixed.version != spec.version):
          raise ProjectError(f"Lock file does not match the complete dependency graph: {spec.name}")
        digest, archive_digest = fixed.digest, fixed.archive_digest
      else:
        release = package_details(spec.registry, spec.name, spec.version)
        digest, archive_digest = release["digest"], release["archive_digest"]
      project = inspect_project(root)
      materialized = materialize_package(spec.registry, spec.name, spec.version, digest, archive_digest,
                                         allow_fetch=allow_fetch,
                                         installed=(root / project.build / "dependencies" / spec.name) if fixed is not None else None)
      source = "registry"
    else:
      materialized = spec.root
      digest, archive_digest = _package_digest(materialized), None
      source = "path"
    if materialized in package_roots and package_roots[materialized] != spec.name:
      raise ProjectError(f"Dependency path has multiple names: {materialized}")

    version, package_data = _read_package(DependencySpec(spec.name, materialized, spec.version))
    existing = packages.get(spec.name)
    if existing is not None:
      if (existing.version != version or existing.source != source or existing.registry != spec.registry
          or existing.digest != digest or (source == "path" and existing.root != materialized)):
        raise ProjectError(f"Conflicting dependency versions for '{spec.name}'.")
      existing.direct = existing.direct or direct
      return

    package_roots[materialized] = spec.name
    package = LockedPackage(
      name=spec.name,
      version=version,
      root=materialized,
      digest=digest,
      direct=direct,
      source=source, registry=spec.registry, archive_digest=archive_digest,
    )
    packages[spec.name] = package
    visiting.append(spec.name)
    try:
      transitive = _dependency_specs(package_data, materialized, registry_origin=spec.registry)
      for child in transitive:
        visit(child, False)
      package.dependencies = tuple(child.name for child in transitive)
    finally:
      visiting.pop()

  for spec in specs:
    visit(spec, True)

  ordered = tuple(packages[name] for name in sorted(packages))
  return LockResult(ordered, _lock_text(specs, ordered, root))


def _atomic_write(path: Path, content: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
  temporary = Path(temporary_name)
  try:
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
      stream.write(content)
    os.replace(temporary, path)
  finally:
    temporary.unlink(missing_ok=True)


def lock_project(path: Path, check: bool = False) -> LockResult:
  root = path.expanduser().resolve()
  target = root / LOCK_NAME
  if check:
    _, locked = _read_lock(root)
    result = resolve_dependencies(root, pinned={item.name: item for item in locked}, allow_fetch=False)
    try:
      current = target.read_text(encoding="utf-8")
    except OSError as error:
      raise ProjectError(f"Cannot read {target}: {error}") from error
    if current != result.text:
      raise ProjectError(f"Lock file is missing or out of date: {target}")
    return result
  with project_write_lock(root):
    before = _dependency_inputs(root)
    result = resolve_dependencies(root)
    if _dependency_inputs(root) != before:
      raise ProjectError("Dependency inputs changed while resolving. Retry with stable inputs.")
    _atomic_write(target, result.text)
    return result


def _read_lock(root: Path) -> tuple[str, tuple[LockedPackage, ...]]:
  target = root / LOCK_NAME
  try:
    with target.open("rb") as stream:
      data = tomllib.load(stream)
  except (OSError, tomllib.TOMLDecodeError) as error:
    raise ProjectError(f"Cannot read {target}: {error}") from error
  if type(data.get("schema_version")) is not int or data.get("schema_version") not in {1, LOCK_SCHEMA_VERSION}:
    raise ProjectError("Unsupported or missing lock schema_version.")
  manifest_digest = data.get("manifest_digest")
  raw_packages = data.get("packages", [])
  if not isinstance(manifest_digest, str) or not isinstance(raw_packages, list):
    raise ProjectError("Lock file requires a valid manifest_digest and package list.")

  packages: list[LockedPackage] = []
  names: set[str] = set()
  for item in raw_packages:
    if not isinstance(item, dict):
      raise ProjectError("Each locked package must be a table.")
    try:
      name = _validate_name(item["name"])
      version = item["version"]
      source = item["source"]
      raw_path = item.get("path")
      digest = item["digest"]
      direct = item["direct"]
      dependencies = item["dependencies"]
    except KeyError as error:
      raise ProjectError(f"Incomplete locked package: {error.args[0]}") from error
    if (
      not isinstance(version, str)
      or not version
      or source not in {"path", "registry"}
      or (source == "path" and (not isinstance(raw_path, str) or not raw_path))
      or not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
      or type(direct) is not bool
      or not isinstance(dependencies, list)
      or not all(isinstance(name, str) and _NAME_PATTERN.fullmatch(name) for name in dependencies)
      or len(set(dependencies)) != len(dependencies)
    ):
      raise ProjectError(f"Invalid locked package: {name}")
    if name in names:
      raise ProjectError(f"Duplicate locked package: {name}")
    names.add(name)
    registry = None
    archive_digest = None
    if source == "registry":
      from texsys.registry import cache_path
      raw_registry = item.get("registry")
      archive_digest = item.get("archive_digest")
      if (data["schema_version"] != LOCK_SCHEMA_VERSION or not isinstance(raw_registry, str) or not raw_registry
          or not isinstance(archive_digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", archive_digest) is None):
        raise ProjectError(f"Invalid locked registry package: {name}")
      registry = Path(raw_registry).expanduser()
      if not registry.is_absolute():
        registry = root / registry
      registry = registry.resolve()
      package_root = cache_path(digest)
      project = inspect_project(root)
      installed = root / project.build / "dependencies" / name
      if not package_root.exists() and installed.exists():
        package_root = installed
    else:
      package_root = Path(raw_path).expanduser()
      if not package_root.is_absolute():
        package_root = root / package_root
    packages.append(
      LockedPackage(
        name=name,
        version=version,
        root=package_root.resolve(),
        digest=digest,
        dependencies=tuple(dependencies),
        direct=direct,
        source=source, registry=registry, archive_digest=archive_digest,
      )
    )
  return manifest_digest, tuple(packages)


def _copy_package(package: LockedPackage, staging: Path) -> None:
  project = inspect_project(package.root)
  if project is None:
    raise ProjectError(f"Dependency is not a texsys project: {package.root}")
  destination_root = staging / package.name
  for directory in (project.source, project.assets):
    (destination_root / directory).mkdir(parents=True, exist_ok=True)
  for source in _package_files(package.root):
    destination = destination_root / source.relative_to(package.root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
  if _package_digest(destination_root) != package.digest:
    raise ProjectError(f"Locked content changed while copying '{package.name}'; retry with stable inputs.")


def sync_project(path: Path, *, expected_revision: str | None = None) -> tuple[LockedPackage, ...]:
  """Restore the locked dependency view without changing manifest or lock."""
  root = path.expanduser().resolve()
  with project_write_lock(root):
    if expected_revision is not None and expected_revision != _dependency_revision(_dependency_inputs(root)):
      raise ProjectError("Package state changed; refresh before synchronizing.")
    return _sync_project(root)


def _sync_project(root: Path) -> tuple[LockedPackage, ...]:
  project = inspect_project(root)
  if project is None:
    raise ProjectError(f"Project is not initialized: {root}")
  specs = _dependency_specs(_read_manifest(root), root)
  expected_digest, packages = _read_lock(root)
  if _manifest_digest(specs, root) != expected_digest:
    raise ProjectError("Lock file does not match the project manifest; run texsys lock.")

  for package in packages:
    if package.source == "registry":
      continue
    version, _ = _read_package(DependencySpec(package.name, package.root, package.version))
    if version != package.version or _package_digest(package.root) != package.digest:
      raise ProjectError(
        f"Locked content changed for '{package.name}'; run texsys lock explicitly."
      )

  resolved = resolve_dependencies(root, pinned={item.name: item for item in packages})
  if _lock_text(specs, packages, root) != resolved.text:
    raise ProjectError("Lock file does not match the complete dependency graph; run texsys lock.")

  build = root / project.build
  build.mkdir(parents=True, exist_ok=True)
  staging = Path(tempfile.mkdtemp(prefix=".dependencies-staging-", dir=build))
  target = build / "dependencies"
  backup = build / f".dependencies-backup-{os.getpid()}"
  moved_old = False
  try:
    if target.is_symlink():
      raise ProjectError("Managed dependency directory cannot be a symlink.")
    for package in resolved.packages:
      _copy_package(package, staging)
    if backup.exists():
      raise ProjectError(f"Temporary dependency path already exists: {backup}")
    if target.exists():
      target.rename(backup)
      moved_old = True
    staging.rename(target)
    if moved_old:
      shutil.rmtree(backup)
  except Exception:
    if not staging.exists() and target.exists() and moved_old:
      target.rename(staging)
    if moved_old and backup.exists():
      if target.exists():
        shutil.rmtree(target)
      backup.rename(target)
    raise
  finally:
    if staging.exists():
      shutil.rmtree(staging)
  return resolved.packages


def _dependency_inputs(root: Path) -> tuple[bytes, bytes | None]:
  if inspect_project(root) is None:
    raise ProjectError(f"Project is not initialized: {root}")
  lock = root / LOCK_NAME
  return (root / MANIFEST_NAME).read_bytes(), lock.read_bytes() if lock.exists() else None


def _publish_dependencies(
  root: Path, document: TOMLDocument, before: tuple[bytes, bytes | None],
  on_resolved: Callable[[TOMLDocument, LockResult], None] | None = None,
) -> LockResult:
  result = resolve_dependencies(root, document)
  if on_resolved is not None:
    on_resolved(document, result)
  if _dependency_inputs(root) != before:
    raise ProjectError("Dependency inputs changed while resolving. Retry with stable inputs.")
  changes = (
    (root / MANIFEST_NAME, tomlkit.dumps(document), before[0]),
    (root / LOCK_NAME, result.text, before[1]),
  )
  written = []
  try:
    for file, after, original in changes:
      if after.encode("utf-8") != original:
        _atomic_write(file, after)
        written.append((file, after, original))
    _sync_project(root)
  except Exception:
    for file, after, original in reversed(written):
      if file.exists() and file.read_bytes() == after.encode("utf-8"):
        if original is None:
          file.unlink()
        else:
          _atomic_write(file, original.decode("utf-8"))
    raise
  return result


def add_dependency(
  path: Path, source: Path, version: str | None = None, *,
  on_resolved: Callable[[TOMLDocument, LockResult], None] | None = None,
) -> LockResult:
  """Add a path package and optionally record metadata against the resolved lock."""
  root = path.expanduser().resolve()
  source = source.expanduser().resolve()
  package_project = inspect_project(source)
  if package_project is None:
    raise ProjectError(f"Dependency is not initialized: {source}")

  with project_write_lock(root):
    before = _dependency_inputs(root)
    document = tomlkit.parse(before[0].decode("utf-8"))
    dependencies = document.get("dependencies")
    if dependencies is None:
      dependencies = tomlkit.table()
      document.add("dependencies", dependencies)
    if not isinstance(dependencies, dict):
      raise ProjectError("[dependencies] must be a table.")
    declaration = tomlkit.inline_table()
    declaration["path"] = _relative_path(root, source)
    if version is not None:
      if not version:
        raise ProjectError("Dependency version must be a non-empty string.")
      declaration["version"] = version
    dependencies[package_project.name] = declaration
    return _publish_dependencies(root, document, before, on_resolved)


def remove_dependency(path: Path, name: str) -> LockResult:
  root = path.expanduser().resolve()
  _validate_name(name)
  with project_write_lock(root):
    before = _dependency_inputs(root)
    document = tomlkit.parse(before[0].decode("utf-8"))
    dependencies = document.get("dependencies")
    if not isinstance(dependencies, dict) or name not in dependencies:
      raise ProjectError(f"Direct dependency does not exist: {name}")
    del dependencies[name]
    return _publish_dependencies(root, document, before)


def _dependency_revision(before: tuple[bytes, bytes | None]) -> str:
  digest = hashlib.sha256()
  for content in before:
    digest.update(b"missing" if content is None else len(content).to_bytes(8, "big") + content)
  return "sha256:" + digest.hexdigest()


def _package_row(package: LockedPackage, root: Path) -> dict:
  project = inspect_project(root)
  installed = root / project.build / "dependencies" / package.name
  state = "not-synced"
  errors = []
  runtime = None
  metadata = {}
  try:
    if package.root.exists():
      metadata = _read_manifest(package.root).get("package", {})
      if not isinstance(metadata, dict):
        raise ProjectError("Package metadata must be a table.")
      runtime = metadata.get("runtime")
    if installed.exists():
      if installed.is_symlink() or _package_digest(installed) != package.digest:
        state = "invalid"
        errors.append("Installed dependency content does not match the lock.")
      else:
        state = "ready"
  except (OSError, ProjectError, ValueError, TypeError) as error:
    state = "invalid"
    errors.append(str(error))
  return {"name": package.name, "version": package.version, "source": package.source,
          "registry": str(package.registry) if package.registry is not None else None,
          "path": str(package.root), "digest": package.digest, "archive_digest": package.archive_digest,
          "direct": package.direct, "dependencies": list(package.dependencies),
          "installed": state == "ready", "state": state, "runtime": runtime,
          "description": metadata.get("description", ""), "scope": metadata.get("scope", ""),
          "kind": metadata.get("kind"), "exports": metadata.get("exports", {}),
          "capabilities": metadata.get("capabilities", []), "tests": metadata.get("tests", []),
          "include": metadata.get("include", []), "diagnostics": errors}


def dependency_status(path: Path) -> dict:
  """Inspect declared, locked and installed package state without importing code."""
  root = path.expanduser().resolve()
  before = _dependency_inputs(root)
  diagnostics = []
  packages = ()
  if before[1] is not None:
    try:
      _, packages = _read_lock(root)
      lock_project(root, check=True)
    except ProjectError as error:
      diagnostics.append(str(error))
  elif _read_manifest(root).get("dependencies"):
    diagnostics.append("Dependencies are declared but texsys.lock is missing.")
  rows = [_package_row(package, root) for package in packages]
  return {"revision": _dependency_revision(before), "packages": rows, "diagnostics": diagnostics,
          "registries": sorted({row["registry"] for row in rows if row["registry"]}),
          "declared": _read_manifest(root).get("dependencies", {}),
          "synchronized": not diagnostics and all(row["installed"] for row in rows)}


def _change_document(root: Path, request: dict, before: tuple[bytes, bytes | None]) -> TOMLDocument:
  if not isinstance(request, dict) or set(request) - {"action", "name", "version", "registry", "revision"}:
    raise ProjectError("Invalid package change request.")
  action = request.get("action")
  name = _validate_name(request.get("name"))
  if action not in {"add", "update", "remove"}:
    raise ProjectError("Package action must be add, update or remove.")
  if "revision" in request and request["revision"] != _dependency_revision(before):
    raise ProjectError("Package state changed; inspect and preview the request again.")
  document = tomlkit.parse(before[0].decode("utf-8"))
  declarations = document.get("dependencies")
  if declarations is None:
    declarations = tomlkit.table()
    document.add("dependencies", declarations)
  if not isinstance(declarations, dict):
    raise ProjectError("[dependencies] must be a table.")
  if action == "remove":
    if name not in declarations:
      raise ProjectError(f"Direct dependency does not exist: {name}")
    if "registry" in request or "version" in request:
      raise ProjectError("Remove requests only identify the dependency name.")
    del declarations[name]
    return document
  version = request.get("version")
  if not isinstance(version, str) or not version:
    raise ProjectError("Package add and update require an exact version.")
  _validate_name(version)
  if action == "update":
    previous = declarations.get(name)
    if not isinstance(previous, dict) or "registry" not in previous:
      raise ProjectError(f"Update requires an existing direct registry dependency: {name}")
    raw_registry = request.get("registry", previous["registry"])
  else:
    raw_registry = request.get("registry")
  if not isinstance(raw_registry, str) or not raw_registry:
    raise ProjectError("Package add requires a registry directory.")
  registry = Path(raw_registry).expanduser()
  if not registry.is_absolute():
    registry = root / registry
  declaration = tomlkit.inline_table()
  declaration["registry"] = _relative_path(root, registry.resolve())
  declaration["version"] = version
  declarations[name] = declaration
  return document


def _change_plan(root: Path, request: dict, before: tuple[bytes, bytes | None], result: LockResult) -> dict:
  old_packages = ()
  if before[1] is not None:
    _, old_packages = _read_lock(root)
  old = {item.name: item for item in old_packages}
  new = {item.name: item for item in result.packages}

  def identity(item):
    return ({"version": item.version, "source": item.source, "registry": str(item.registry) if item.registry else None,
             "digest": item.digest, "archive_digest": item.archive_digest, "direct": item.direct}
            if item is not None else None)

  changes = [{"name": name, "before": identity(old.get(name)), "after": identity(new.get(name))}
             for name in sorted(set(old) | set(new)) if identity(old.get(name)) != identity(new.get(name))]
  revision = _dependency_revision(before)
  content = json.dumps({"revision": revision, "request": request, "lock": result.text}, sort_keys=True, ensure_ascii=False).encode()
  return {"revision": revision, "plan": "sha256:" + hashlib.sha256(content).hexdigest(), "changes": changes,
          "packages": [_package_row(item, root) for item in result.packages]}


def plan_dependency_change(path: Path, request: dict) -> dict:
  root = path.expanduser().resolve()
  before = _dependency_inputs(root)
  document = _change_document(root, request, before)
  result = resolve_dependencies(root, document)
  plan = _change_plan(root, request, before, result)
  if _dependency_inputs(root) != before:
    raise ProjectError("Package state changed while resolving the preview; retry.")
  return plan


def apply_dependency_change(path: Path, request: dict, plan: str) -> dict:
  root = path.expanduser().resolve()
  with project_write_lock(root):
    before = _dependency_inputs(root)
    document = _change_document(root, request, before)

    def verify(_, result):
      if _change_plan(root, request, before, result)["plan"] != plan:
        raise ProjectError("Package plan is stale or does not match this request; preview again.")

    result = _publish_dependencies(root, document, before, verify)
    return {"applied": True, "revision": _dependency_revision(_dependency_inputs(root)),
            "packages": [_package_row(item, root) for item in result.packages]}


def add_registry_dependency(path: Path, name: str, version: str, registry: Path) -> LockResult:
  root = path.expanduser().resolve()
  with project_write_lock(root):
    before = _dependency_inputs(root)
    document = _change_document(root, {"action": "add", "name": name, "registry": str(registry), "version": version}, before)
    return _publish_dependencies(root, document, before)


def update_registry_dependency(path: Path, name: str, version: str) -> LockResult:
  root = path.expanduser().resolve()
  with project_write_lock(root):
    before = _dependency_inputs(root)
    document = _change_document(root, {"action": "update", "name": name, "version": version}, before)
    return _publish_dependencies(root, document, before)
