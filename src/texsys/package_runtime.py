"""Load explicitly declared Python capabilities from verified design-package content.

The package manager supplies source, not a Python dependency installer or sandbox.
Registry projects never substitute an ambient editable distribution for locked code.
"""

from dataclasses import dataclass
import hashlib
import importlib
from importlib import metadata
from importlib.machinery import PathFinder, SourceFileLoader
import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from texsys.dependencies import lock_project
from texsys.project import ProjectError, inspect_project


_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENTRY = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):([A-Za-z_][A-Za-z0-9_]*)")
_OWNERS = {}
_LOADED = {}
_SOURCES = {}


@dataclass(frozen=True)
class RuntimeDeclaration:
  locked: object
  source: Path
  modules: tuple[str, ...]
  entry_point: str | None


def _requirements(values):
  if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
    raise ProjectError("package.runtime.requires must be a list of PEP 508 requirement strings.")
  result = []
  for value in values:
    try:
      requirement = Requirement(value)
    except InvalidRequirement as error:
      raise ProjectError(f"Invalid runtime requirement {value!r}: {error}") from error
    if requirement.url or requirement.extras:
      raise ProjectError(f"Runtime requirement {value!r} uses a URL or extras; declare installed distributions explicitly.")
    item = {"requirement": value, "installed_version": None, "message": ""}
    if requirement.marker and not requirement.marker.evaluate():
      item.update(status="not-applicable", message="Environment marker does not apply.")
    else:
      try:
        installed = metadata.version(requirement.name)
        item["installed_version"] = installed
        valid = not requirement.specifier or Version(installed) in requirement.specifier
        item.update(status="satisfied" if valid else "incompatible",
                    message="" if valid else f"Installed {requirement.name} {installed} does not satisfy {value}; prepare the Python environment with uv.")
      except metadata.PackageNotFoundError:
        item.update(status="missing", message=f"Missing Python runtime dependency {value}; prepare the Python environment with uv.")
      except InvalidVersion:
        item.update(status="incompatible", message=f"Installed {requirement.name} has an invalid version.")
    result.append(item)
  return result


def _declaration(locked):
  project = inspect_project(locked.root)
  with (locked.root / "texsys.toml").open("rb") as stream:
    manifest = tomllib.load(stream)
  package = manifest.get("package", {})
  if not isinstance(package, dict):
    raise ProjectError("package metadata must be a table.")
  runtime = package.get("runtime")
  if runtime is None:
    return None, []
  if not isinstance(runtime, dict) or set(runtime) - {"api_version", "entry_point", "modules", "requires"}:
    raise ProjectError("package.runtime supports api_version, entry_point, modules and requires.")
  if type(runtime.get("api_version")) is not int or runtime["api_version"] != 1:
    raise ProjectError("Unsupported package.runtime.api_version; expected 1.")
  entry = runtime.get("entry_point")
  match = _ENTRY.fullmatch(entry) if isinstance(entry, str) else None
  if entry is not None and match is None:
    raise ProjectError("package.runtime.entry_point must be module:object.")
  modules = runtime.get("modules", [match[1].split(".")[0]] if match else [])
  if (not isinstance(modules, list) or not modules
      or any(not isinstance(name, str) or not _MODULE.fullmatch(name) for name in modules)
      or len(modules) != len(set(modules))):
    raise ProjectError("package.runtime.modules must declare unique top-level Python module names.")
  if match and match[1].split(".")[0] not in modules:
    raise ProjectError("Runtime entry_point must belong to a declared module.")
  source = locked.root / project.source
  for name in modules:
    if name == "texsys" or name in sys.stdlib_module_names:
      raise ProjectError(f"Runtime cannot replace core or standard-library module {name}.")
    file, directory = source / f"{name}.py", source / name
    if not file.is_file() and not (directory.is_dir() and any(directory.rglob("*.py"))):
      raise ProjectError(f"Declared Python module {name} is missing from locked source.")
    target = file if file.is_file() else directory
    if target.resolve() != target:
      raise ProjectError(f"Runtime source cannot use symbolic links: {name}.")
  return RuntimeDeclaration(locked, source, tuple(modules), entry), _requirements(runtime.get("requires", []))


def _inspect(root):
  packages = lock_project(root, check=True).packages
  declarations, records, errors = [], [], []
  for locked in packages:
    record = {"name": locked.name, "version": locked.version, "digest": locked.digest,
              "source": getattr(locked, "source", "path"), "status": "asset-only",
              "entry_point": None, "modules": [], "requirements": [], "message": "No Python runtime declared."}
    try:
      declaration, requirements = _declaration(locked)
      if declaration:
        records_status = {item["status"] for item in requirements}
        status = "missing" if "missing" in records_status else "incompatible" if "incompatible" in records_status else "ready"
        record.update(status=status, entry_point=declaration.entry_point, modules=list(declaration.modules),
                      requirements=requirements, message="" if status == "ready" else "Python environment requirements are not satisfied.")
        declarations.append(declaration)
        errors.extend(item["message"] for item in requirements if item["status"] in {"missing", "incompatible"})
    except (OSError, ValueError, TypeError, ProjectError) as error:
      record.update(status="invalid", message=str(error))
      errors.append(f"{locked.name}: {error}")
    records.append(record)
  strict = any(getattr(package, "source", "path") == "registry" for package in packages)
  owners, conflicts = {}, set()
  for declaration in declarations:
    for name in declaration.modules:
      if name in owners:
        errors.append(f"Locked packages {owners[name]} and {declaration.locked.name} both declare Python module {name}.")
        conflicts.add(name)
      owners[name] = declaration.locked.name
  if errors:
    for record in records:
      if record["status"] == "ready" and conflicts.intersection(record["modules"]):
        record.update(status="invalid", message="Python module ownership conflicts with another locked package.")
  return packages, declarations, {"status": "unavailable" if errors else "ready", "packages": records, "errors": errors}, strict


def runtime_status(root: Path) -> dict:
  """Inspect requirements and source declarations without importing package code."""
  try:
    root = Path(root).expanduser().resolve()
    with (root / "texsys.toml").open("rb") as stream:
      manifest = tomllib.load(stream)
    if not manifest.get("dependencies") and not (root / "texsys.lock").exists():
      return {"status": "ready", "packages": [], "errors": []}
    return _inspect(root)[2]
  except (OSError, ValueError, TypeError, ProjectError) as error:
    return {"status": "unavailable", "packages": [], "errors": [str(error)]}


class _LockedLoader(SourceFileLoader):
  def get_code(self, fullname):
    raw = self.get_data(self.path)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    path = str(Path(self.path).resolve())
    _SOURCES[path] = digest
    if hasattr(sys, "_texsys_loaded_sources"):
      sys._texsys_loaded_sources[path] = digest
    return self.source_to_code(raw, self.path)


class _LockedFinder:
  @staticmethod
  def find_spec(fullname, path=None, target=None):
    owner = _OWNERS.get(fullname.split(".")[0])
    if owner is None:
      return None
    declaration = owner
    spec = PathFinder.find_spec(fullname, [str(declaration.source)] if path is None else path)
    if spec is None:
      raise ImportError(f"Module {fullname} is absent from locked package {declaration.locked.name}; ambient fallback is disabled.")
    if spec.origin and spec.origin not in {"built-in", "frozen"}:
      origin = Path(spec.origin).resolve()
      if not origin.is_relative_to(declaration.source):
        raise ImportError(f"Module {fullname} resolved outside its locked package.")
      if not isinstance(spec.loader, SourceFileLoader):
        raise ImportError(f"Locked runtime supports Python source only: {fullname}.")
      spec.loader = _LockedLoader(fullname, str(origin))
    elif spec.submodule_search_locations is not None:
      if any(not Path(value).resolve().is_relative_to(declaration.source) for value in spec.submodule_search_locations):
        raise ImportError(f"Namespace {fullname} escapes its locked package.")
    return spec


def _identity(declaration):
  package = declaration.locked
  return package.name, package.version, package.digest, str(declaration.source)


def _activate(declarations):
  proposed = {}
  requested = {name for declaration in declarations for name in declaration.modules}
  if _OWNERS and set(_OWNERS) != requested:
    raise ProjectError("A different locked Python package graph is already active; use a fresh process.")
  for declaration in declarations:
    for name in declaration.modules:
      if name in proposed and _identity(proposed[name]) != _identity(declaration):
        raise ProjectError(f"Locked packages both provide Python module {name}.")
      proposed[name] = declaration
      previous = _OWNERS.get(name)
      if previous and _identity(previous) != _identity(declaration):
        raise ProjectError(f"Python module {name} is already loaded from a different package revision; use a fresh process.")
      for fullname, module in tuple(sys.modules.items()):
        if fullname != name and not fullname.startswith(name + "."):
          continue
        file = getattr(module, "__file__", None)
        if not file:
          paths = getattr(module, "__path__", ())
          if not paths or any(not Path(path).resolve().is_relative_to(declaration.source) for path in paths):
            raise ProjectError(f"Python module {fullname} is already loaded outside the locked package; use a fresh process.")
          continue
        file = str(Path(file).resolve())
        known = _SOURCES.get(file, getattr(sys, "_texsys_loaded_sources", {}).get(file))
        if not Path(file).is_relative_to(declaration.source) or known is None:
          raise ProjectError(f"Python module {fullname} was not loaded from verified locked source; use a fresh process.")
        current = "sha256:" + hashlib.sha256(Path(file).read_bytes()).hexdigest()
        if known != current:
          raise ProjectError(f"Executing Python source changed after loading: {fullname}; use a fresh process.")
  _OWNERS.update(proposed)
  if _LockedFinder not in sys.meta_path:
    sys.meta_path.insert(0, _LockedFinder)
  importlib.invalidate_caches()


def load_project_packages(root, installed, *, locked_only=False):
  from texsys.packages import TexsysPackage

  root = Path(root).expanduser().resolve()
  manifest = root / "texsys.toml"
  if not manifest.is_file():
    return () if locked_only else installed()
  with manifest.open("rb") as stream:
    data = tomllib.load(stream)
  if not data.get("dependencies"):
    return () if locked_only else installed()
  if not isinstance(data["dependencies"], dict):
    raise ProjectError("[dependencies] must be a table.")
  registry_declared = any(isinstance(value, dict) and "registry" in value for value in data["dependencies"].values())
  lock = root / "texsys.lock"
  if lock.is_file():
    try:
      with lock.open("rb") as stream:
        registry_declared |= any(item.get("source") == "registry" for item in tomllib.load(stream).get("packages", []))
    except (OSError, ValueError, TypeError, AttributeError):
      pass
  try:
    _, declarations, status, strict = _inspect(root)
  except ProjectError:
    if registry_declared or locked_only:
      raise
    # Preserve legacy inspection of invalid path dependencies: its caller
    # reports dependency_error and invalidates prior evidence before any run.
    return installed()
  if status["errors"]:
    raise ProjectError("Package runtime unavailable: " + "; ".join(status["errors"]))
  # Existing local-path workspaces still use their installed developer plugins.
  # A package absent from that environment can opt into explicit source loading.
  registered = {entry.name for entry in metadata.entry_points(group="texsys.packages")}
  if not strict and not locked_only and all(not item.entry_point or item.locked.name in registered for item in declarations):
    return installed()
  _activate(declarations)
  environment = [{key: item[key] for key in ("name", "version", "digest", "source", "modules", "requirements")}
                 for item in status["packages"]]
  result = []
  for declaration in declarations:
    if not declaration.entry_point:
      continue
    identity = _identity(declaration)
    if identity not in _LOADED:
      module, attribute = declaration.entry_point.split(":")
      try:
        package = getattr(importlib.import_module(module), attribute)
      except (ImportError, AttributeError) as error:
        raise ProjectError(f"Cannot load locked runtime {declaration.locked.name}: {error}") from error
      if not isinstance(package, TexsysPackage):
        raise ProjectError(f"Locked runtime {declaration.locked.name} does not implement TexsysPackage.")
      if package.name != declaration.locked.name or package.version != declaration.locked.version:
        raise ProjectError(f"Locked runtime identity mismatch for {declaration.locked.name} {declaration.locked.version}.")
      _LOADED[identity] = package
    _LOADED[identity]._texsys_locked_runtime = {"name": declaration.locked.name, "version": declaration.locked.version,
                                              "digest": declaration.locked.digest, "entry_point": declaration.entry_point,
                                              "dependencies": environment}
    result.append(_LOADED[identity])
  return tuple(sorted(result, key=lambda item: item.name))
