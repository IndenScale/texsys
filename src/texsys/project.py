"""Project scaffolding and read-only project inspection."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import tomllib

MANIFEST_NAME = "texsys.toml"
SCHEMA_VERSION = 1
DEFAULT_LAYOUT = {"source": "src", "assets": "assets", "build": "build"}
DEPENDENCY_FILES = ("pyproject.toml", "package.json")
LOCK_FILES = ("texsys.lock", "uv.lock", "package-lock.json")


class ProjectError(Exception):
  """A project operation cannot be completed safely."""


@contextmanager
def project_write_lock(root: Path) -> Iterator[None]:
  """Serialize cooperating design and dependency writers for this project."""
  lock = root / ".texsys-write.lock"
  try:
    lock.mkdir()
  except FileExistsError as error:
    raise ProjectError("Project write is locked (.texsys-write.lock). Retry after the writer exits.") from error
  except OSError as error:
    raise ProjectError(f"Cannot lock project for writing: {error}") from error
  try:
    yield
  finally:
    lock.rmdir()


@dataclass(frozen=True)
class Project:
  root: Path
  name: str
  source: str
  assets: str
  build: str


@dataclass(frozen=True)
class InitializationResult:
  project: Project
  created: tuple[str, ...]
  manifest_existed: bool


def _resolved_root(path: Path) -> Path:
  return path.expanduser().resolve()


def _validate_layout_path(value: object, key: str) -> str:
  if not isinstance(value, str) or not value:
    raise ProjectError(f"Layout path '{key}' must be a non-empty relative path.")

  layout_path = PurePosixPath(value)
  if layout_path == PurePosixPath("."):
    raise ProjectError(f"Layout path '{key}' must identify a child directory.")
  if layout_path.is_absolute() or ".." in layout_path.parts or "\\" in value:
    raise ProjectError(f"Layout path '{key}' must stay inside the project root.")
  return layout_path.as_posix()


def _validate_layout(root: Path, paths: dict[str, str]) -> None:
  resolved = {key: (root / value).resolve() for key, value in paths.items()}
  for key, target in resolved.items():
    if target == root or not target.is_relative_to(root):
      raise ProjectError(f"Layout path '{key}' must stay inside the project root.")
  values = list(resolved.values())
  if len(set(values)) != len(values):
    raise ProjectError("Layout paths for source, assets and build must be distinct.")
  for index, target in enumerate(values):
    for other in values[index + 1:]:
      if target.is_relative_to(other) or other.is_relative_to(target):
        raise ProjectError("Layout paths for source, assets and build must not overlap.")


def _read_project(root: Path) -> Project | None:
  manifest = root / MANIFEST_NAME
  if not manifest.exists():
    return None
  if not manifest.is_file():
    raise ProjectError(f"Manifest is not a file: {manifest}")

  try:
    with manifest.open("rb") as stream:
      data = tomllib.load(stream)
  except (OSError, tomllib.TOMLDecodeError) as error:
    raise ProjectError(f"Cannot read {manifest}: {error}") from error

  schema_version = data.get("schema_version")
  if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
    raise ProjectError(
      f"Unsupported or missing schema_version; expected {SCHEMA_VERSION}."
    )

  project_data = data.get("project")
  if not isinstance(project_data, dict):
    raise ProjectError("Missing [project] table.")
  name = project_data.get("name")
  if not isinstance(name, str) or not name.strip():
    raise ProjectError("Project name must be a non-empty string.")

  layout = data.get("layout")
  if not isinstance(layout, dict):
    raise ProjectError("Missing [layout] table.")

  paths = {
    key: _validate_layout_path(layout.get(key), key) for key in DEFAULT_LAYOUT
  }
  _validate_layout(root, paths)
  return Project(root=root, name=name, **paths)


def inspect_project(path: Path) -> Project | None:
  """Read a project at the exact path without searching parent directories."""
  root = _resolved_root(path)
  if not root.exists():
    raise ProjectError(f"Project path does not exist: {root}")
  if not root.is_dir():
    raise ProjectError(f"Project path is not a directory: {root}")
  return _read_project(root)


def _manifest_text(name: str) -> str:
  return f"""schema_version = {SCHEMA_VERSION}

[project]
name = {json.dumps(name, ensure_ascii=False)}

[layout]
source = \"{DEFAULT_LAYOUT['source']}\"
assets = \"{DEFAULT_LAYOUT['assets']}\"
build = \"{DEFAULT_LAYOUT['build']}\"
"""


def initialize_project(path: Path) -> InitializationResult:
  """Create the minimal layout without overwriting existing files."""
  root = _resolved_root(path)
  if root.exists() and not root.is_dir():
    raise ProjectError(f"Project path is not a directory: {root}")

  project = _read_project(root) if root.exists() else None
  layout = {
    key: getattr(project, key) if project else value
    for key, value in DEFAULT_LAYOUT.items()
  }
  _validate_layout(root, layout)

  for key, relative_path in layout.items():
    target = root / relative_path
    if target.exists() and not target.is_dir():
      raise ProjectError(f"Layout path '{key}' is not a directory: {target}")

  gitignore = root / ".gitignore"
  if gitignore.exists() and not gitignore.is_file():
    raise ProjectError(f"Expected a file but found: {gitignore}")

  created: list[str] = []
  try:
    root.mkdir(parents=True, exist_ok=True)
  except OSError as error:
    raise ProjectError(f"Cannot create project root {root}: {error}") from error

  manifest = root / MANIFEST_NAME
  if project is None:
    name = root.name or "texsys-project"
    try:
      with manifest.open("x", encoding="utf-8") as stream:
        stream.write(_manifest_text(name))
    except OSError as error:
      raise ProjectError(f"Cannot create {manifest}: {error}") from error
    created.append(MANIFEST_NAME)

  for relative_path in layout.values():
    target = root / relative_path
    if not target.exists():
      try:
        target.mkdir(parents=True)
      except OSError as error:
        raise ProjectError(f"Cannot create layout directory {target}: {error}") from error
      created.append(f"{relative_path}/")

  if not gitignore.exists():
    try:
      gitignore.write_text(f"/{layout['build']}/\n", encoding="utf-8")
    except OSError as error:
      raise ProjectError(f"Cannot create {gitignore}: {error}") from error
    created.append(".gitignore")

  initialized = _read_project(root)
  if initialized is None:
    raise ProjectError(f"Failed to initialize project at {root}")
  return InitializationResult(
    project=initialized,
    created=tuple(created),
    manifest_existed=project is not None,
  )
