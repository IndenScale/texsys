"""Local-path design dependency resolution, locking and synchronization."""

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
LOCK_SCHEMA_VERSION = 1
_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class DependencySpec:
  name: str
  root: Path
  version: str | None


@dataclass
class LockedPackage:
  name: str
  version: str
  root: Path
  digest: str
  dependencies: tuple[str, ...] = ()
  direct: bool = False


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


def _dependency_specs(data: dict[str, Any], base: Path) -> tuple[DependencySpec, ...]:
  declarations = data.get("dependencies", {})
  if not isinstance(declarations, dict):
    raise ProjectError("[dependencies] must be a table.")

  specs: list[DependencySpec] = []
  for raw_name, declaration in declarations.items():
    name = _validate_name(raw_name)
    if not isinstance(declaration, dict):
      raise ProjectError(f"Dependency '{name}' must be an inline table.")
    if set(declaration) - {"path", "version"}:
      raise ProjectError(f"Dependency '{name}' only supports 'path' and 'version'.")
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
    {
      "name": spec.name,
      "path": _relative_path(root, spec.root),
      "version": spec.version,
    }
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
  for relative_directory in (project.source, project.assets):
    directory = root / relative_directory
    if not directory.is_dir():
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
  return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


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
    f"schema_version = {LOCK_SCHEMA_VERSION}",
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
        'source = "path"',
        f"path = {json.dumps(_relative_path(root, package.root), ensure_ascii=False)}",
        f'digest = "{package.digest}"',
        f"direct = {str(package.direct).lower()}",
        f"dependencies = [{dependencies}]",
      ]
    )
  return "\n".join(lines) + "\n"


def resolve_dependencies(
  root: Path, data: dict[str, Any] | None = None
) -> LockResult:
  """Resolve the complete local-path graph without writing any files."""
  root = root.expanduser().resolve()
  if inspect_project(root) is None:
    raise ProjectError(f"Project is not initialized: {root}")
  data = _read_manifest(root) if data is None else data
  specs = _dependency_specs(data, root)
  packages: dict[str, LockedPackage] = {}
  package_roots: dict[Path, str] = {}
  visiting: list[Path] = []

  def visit(spec: DependencySpec, direct: bool) -> None:
    if spec.root in visiting:
      cycle = " -> ".join(path.name for path in [*visiting, spec.root])
      raise ProjectError(f"Dependency cycle detected: {cycle}")
    if spec.root in package_roots and package_roots[spec.root] != spec.name:
      raise ProjectError(f"Dependency path has multiple names: {spec.root}")

    version, package_data = _read_package(spec)
    existing = packages.get(spec.name)
    if existing is not None:
      if existing.root != spec.root or existing.version != version:
        raise ProjectError(f"Conflicting dependency versions for '{spec.name}'.")
      existing.direct = existing.direct or direct
      return

    package_roots[spec.root] = spec.name
    package = LockedPackage(
      name=spec.name,
      version=version,
      root=spec.root,
      digest=_package_digest(spec.root),
      direct=direct,
    )
    packages[spec.name] = package
    visiting.append(spec.root)
    try:
      transitive = _dependency_specs(package_data, spec.root)
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
    result = resolve_dependencies(root)
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
  if data.get("schema_version") != LOCK_SCHEMA_VERSION:
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
      raw_path = item["path"]
      digest = item["digest"]
      direct = item["direct"]
      dependencies = item["dependencies"]
    except KeyError as error:
      raise ProjectError(f"Incomplete locked package: {error.args[0]}") from error
    if (
      not isinstance(version, str)
      or not version
      or source != "path"
      or not isinstance(raw_path, str)
      or not raw_path
      or not isinstance(digest, str)
      or type(direct) is not bool
      or not isinstance(dependencies, list)
      or not all(isinstance(name, str) for name in dependencies)
    ):
      raise ProjectError(f"Invalid locked package: {name}")
    if name in names:
      raise ProjectError(f"Duplicate locked package: {name}")
    names.add(name)
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


def sync_project(path: Path) -> tuple[LockedPackage, ...]:
  """Restore the locked dependency view without changing manifest or lock."""
  root = path.expanduser().resolve()
  with project_write_lock(root):
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
    version, _ = _read_package(DependencySpec(package.name, package.root, package.version))
    if version != package.version or _package_digest(package.root) != package.digest:
      raise ProjectError(
        f"Locked content changed for '{package.name}'; run texsys lock explicitly."
      )

  resolved = resolve_dependencies(root)
  if _lock_text(specs, packages, root) != resolved.text:
    raise ProjectError("Lock file does not match the complete dependency graph; run texsys lock.")

  build = root / project.build
  build.mkdir(parents=True, exist_ok=True)
  staging = Path(tempfile.mkdtemp(prefix=".dependencies-staging-", dir=build))
  target = build / "dependencies"
  backup = build / f".dependencies-backup-{os.getpid()}"
  moved_old = False
  try:
    for package in packages:
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
  return packages


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
