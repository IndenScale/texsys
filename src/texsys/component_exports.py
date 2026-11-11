"""Explicit component-package exports, independent of runtime capabilities."""

from pathlib import Path, PurePosixPath

from texsys.library import library_entries
from texsys.project import ProjectError, inspect_project


def component_exports(root: Path, manifest: dict | None = None) -> tuple[str, ...]:
  """Return validated package-relative component exports; ordinary packages export none."""
  root = root.expanduser().resolve()
  if manifest is None:
    from texsys.dependencies import _read_manifest
    manifest = _read_manifest(root)
  metadata = manifest.get("package", {})
  if not isinstance(metadata, dict):
    raise ProjectError("package must be a table.")
  exports = metadata.get("exports", {})
  if not isinstance(exports, dict) or set(exports) - {"components"}:
    raise ProjectError("package.exports must contain only the components export list.")
  declared = exports.get("components", [])
  if not isinstance(declared, list) or not all(isinstance(value, str) and value for value in declared):
    raise ProjectError("Component exports must be a list of non-empty package-relative paths.")
  if metadata.get("kind") != "component":
    if declared:
      raise ProjectError("Only kind = 'component' packages may export components.")
    return ()
  if not declared:
    raise ProjectError("Component packages must declare a non-empty package.exports.components list.")
  project = inspect_project(root)
  if project is None:
    raise ProjectError("Component package needs a texsys.toml project identity.")
  source = (root / project.source).resolve()
  entries = library_entries({file.relative_to(source).as_posix() for file in source.rglob("*.sdl.json")})
  public = {f"{project.source}/{path}" for path, entry in entries.items() if entry.owner is None}
  public.add(f"{project.source}/library/models.json")
  checked = []
  for value in declared:
    relative = PurePosixPath(value)
    if (not value or "\\" in value or "\0" in value or ":" in value or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/")) or relative.as_posix() != value):
      raise ProjectError(f"Component export must be a canonical package-relative path: {value!r}")
    target = root / value
    if target.resolve() != target or not target.exists() or not target.is_relative_to(source):
      raise ProjectError(f"Component export must exist inside package source without symlinks: {value}")
    candidates = tuple(target.rglob("*")) if target.is_dir() else (target,)
    if any(file.resolve() != file for file in candidates):
      raise ProjectError(f"Component export cannot include symlinks: {value}")
    if not any(file.is_file() and file.relative_to(root).as_posix() in public for file in candidates):
      raise ProjectError(f"Component export must identify public library entries or the library model catalog: {value}")
    if any(value == old or value.startswith(old + "/") or old.startswith(value + "/") for old in checked):
      raise ProjectError(f"Component exports overlap: {value}")
    checked.append(value)
  return tuple(checked)


def is_component_export(relative: str, exports: tuple[str, ...]) -> bool:
  """Match a package-relative file against its explicit file or directory exports."""
  return any(relative == entry or relative.startswith(entry + "/") for entry in exports)
