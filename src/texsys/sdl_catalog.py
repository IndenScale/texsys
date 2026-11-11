"""Read local SDL and any explicitly imported, lock-verified dependency libraries."""

from dataclasses import dataclass
from pathlib import Path

from texsys.dependencies import LockedPackage, lock_project
from texsys.formal import Diagnostic, read_sdl_sources
from texsys.library import library_entries
from texsys.module_package import read_module_manifests
from texsys.project import ProjectError, inspect_project


@dataclass
class SdlCatalog:
  documents: dict[str, dict]
  files: dict[str, Path]
  modules: dict[str, dict]
  diagnostics: list[Diagnostic]
  local_count: int
  packages: dict[str, LockedPackage]


def read_sdl_catalog(source: Path, root: Path, *, replacement: tuple[str, dict] | None = None) -> SdlCatalog:
  documents, diagnostics, count = read_sdl_sources(source, root)
  if replacement is not None:
    name, document = replacement
    documents[name] = document
  for name in tuple(documents):
    if name.startswith("@"):
      diagnostics.append(Diagnostic("S001", "fail", (source / name).relative_to(root).as_posix(),
                                    "The @package namespace is reserved for locked libraries."))
      del documents[name]
  files = {name: source / name for name in documents}
  modules, errors = read_module_manifests(source, library_entries(set(documents)), documents)
  diagnostics.extend(Diagnostic("M001", "fail", file.relative_to(root).as_posix(), message) for file, message in errors.items())
  catalog = SdlCatalog(documents, files, modules, diagnostics, count, {})

  def requirements(data, prefix, allowed=None):
    names = set()
    for name, document in data.items():
      for alias, target in document.get("imports", {}).items():
        if not target.startswith("@"):
          continue
        package = target[1:].split("/", 1)[0]
        if allowed is not None and package not in allowed:
          diagnostics.append(Diagnostic("L014", "fail", f"{prefix}/{name}",
                                        f"External import is not a declared dependency: {package}", f"imports.{alias}"))
        else:
          names.add(package)
    return names

  pending = requirements(documents, source.relative_to(root).as_posix())
  if not pending:
    return catalog
  try:
    locked = {package.name: package for package in lock_project(root, check=True).packages}
  except ProjectError as error:
    diagnostics.append(Diagnostic("L014", "fail", "texsys.lock", str(error)))
    return catalog
  visited = set()
  while pending:
    package = min(pending)
    pending.remove(package)
    if package in visited:
      continue
    visited.add(package)
    if package not in locked:
      diagnostics.append(Diagnostic("L014", "fail", "texsys.lock", f"External library is not locked: {package}"))
      continue
    reference = locked[package]
    project = inspect_project(reference.root)
    external = reference.root / project.source
    provided, issues, _ = read_sdl_sources(external, reference.root)
    prefix = f"@{package}"
    diagnostics.extend(Diagnostic(issue.rule, issue.status, f"{prefix}/{issue.file}", issue.message,
                                  issue.object, issue.line, issue.column) for issue in issues)
    manifests, errors = read_module_manifests(external, library_entries(set(provided)), provided)
    diagnostics.extend(Diagnostic("M001", "fail", f"{prefix}/{file.relative_to(reference.root).as_posix()}", message)
                       for file, message in errors.items())
    documents.update({f"{prefix}/{key}": value for key, value in provided.items()})
    files.update({f"{prefix}/{key}": external / key for key in provided})
    modules.update({f"{prefix}/{key}": manifest for key, manifest in manifests.items()})
    catalog.packages[package] = reference
    pending.update(requirements(provided, prefix, set(reference.dependencies)))
  return catalog
