"""Immutable engineering packages in a shared filesystem registry."""

from contextlib import contextmanager
from functools import wraps
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

import tomlkit

from texsys.project import MANIFEST_NAME, ProjectError, inspect_project
from texsys.component_exports import component_exports


HEADER = "texsys-package.json"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_FILES = 20000


def _io_errors(function):
  @wraps(function)
  def run(*args, **kwargs):
    try:
      return function(*args, **kwargs)
    except OSError as error:
      raise ProjectError(f"Package storage operation failed: {error}") from error
  return run


def _digest(content: bytes) -> str:
  return "sha256:" + hashlib.sha256(content).hexdigest()


def _json(content: bytes) -> dict:
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError(f"Duplicate JSON key: {key}")
      result[key] = value
    return result
  try:
    value = json.loads(content, object_pairs_hook=unique)
  except (ValueError, UnicodeError) as error:
    raise ProjectError(f"Invalid package metadata: {error}") from error
  if not isinstance(value, dict):
    raise ProjectError("Package metadata must be an object.")
  return value


def _identity(name: str, version: str) -> None:
  from texsys.dependencies import _validate_name
  _validate_name(name)
  _validate_name(version)
  if name in {".", ".."} or version in {".", ".."}:
    raise ProjectError("Invalid package identity.")


def _canonical_member(name: str) -> str:
  path = PurePosixPath(name)
  if (not name or "\\" in name or "\0" in name or ":" in name or path.is_absolute()
      or any(part in {"", ".", ".."} for part in name.split("/")) or path.as_posix() != name):
    raise ProjectError(f"Unsafe package member: {name!r}")
  return name


def package_metadata(root: Path) -> dict:
  from texsys.dependencies import _read_manifest
  data = _read_manifest(root)
  metadata = data.get("package", {})
  if not isinstance(metadata, dict) or metadata.get("kind") not in {"capability", "component"}:
    raise ProjectError("Publishing requires [package] kind = 'capability' or 'component'; projects are not packages by default.")
  component_exports(root, data)
  for key in ("description", "scope"):
    if key in metadata and not isinstance(metadata[key], str):
      raise ProjectError(f"package.{key} must be text.")
  for key in ("capabilities", "include", "limitations", "tests"):
    if key in metadata and (not isinstance(metadata[key], list)
                            or not all(isinstance(item, str) and item.strip() for item in metadata[key])):
      raise ProjectError(f"package.{key} must be a list of non-empty strings.")
  runtime = metadata.get("runtime")
  if runtime is not None:
    if (not isinstance(runtime, dict) or set(runtime) - {"api_version", "entry_point", "modules", "requires"}
        or type(runtime.get("api_version")) is not int or runtime["api_version"] != 1
        or not isinstance(runtime.get("requires", []), list)
        or not all(isinstance(item, str) and item.strip() for item in runtime.get("requires", []))):
      raise ProjectError("package.runtime requires api_version=1 and a requires list.")
    if "entry_point" in runtime and (not isinstance(runtime["entry_point"], str) or not runtime["entry_point"].strip()):
      raise ProjectError("package.runtime.entry_point must be non-empty text.")
    if "modules" in runtime and (not isinstance(runtime["modules"], list) or not runtime["modules"]
                                 or not all(isinstance(item, str) and item.isidentifier() for item in runtime["modules"])):
      raise ProjectError("package.runtime.modules must list top-level Python module names.")
    if not runtime.get("entry_point") and not runtime.get("modules"):
      raise ProjectError("package.runtime needs an entry_point or declared modules.")
  for test in metadata.get("tests", []):
    _canonical_member(test)
    target = root / test
    if target.resolve() != target or not target.exists():
      raise ProjectError(f"Declared package test is missing or linked: {test}")
  return metadata


def _archive(source: Path, versions: dict[str, str], output: Path) -> dict:
  from texsys.dependencies import _package_digest, _package_files, _read_manifest
  project = inspect_project(source)
  if project is None:
    raise ProjectError(f"Not a package project: {source}")
  original = _read_manifest(source)
  version = original.get("project", {}).get("version")
  _identity(project.name, version)
  package_metadata(source)
  before = _package_digest(source)
  with tempfile.TemporaryDirectory(prefix="texsys-pack-") as directory:
    staged = Path(directory).resolve()
    for layout in (project.source, project.assets):
      (staged / layout).mkdir(parents=True, exist_ok=True)
    for file in _package_files(source):
      target = staged / file.relative_to(source)
      target.parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(file, target)
    if _package_digest(staged) != before or _package_digest(source) != before:
      raise ProjectError("Package inputs changed while preparing the archive.")
    document = tomlkit.parse((staged / MANIFEST_NAME).read_text(encoding="utf-8"))
    dependencies = document.get("dependencies", {})
    for name in tuple(dependencies):
      if name not in versions:
        raise ProjectError(f"Unresolved package dependency: {name}")
      declaration = tomlkit.inline_table()
      declaration["registry"] = "."
      declaration["version"] = versions[name]
      dependencies[name] = declaration
    (staged / MANIFEST_NAME).write_text(tomlkit.dumps(document), encoding="utf-8")
    metadata = package_metadata(staged)
    files = {file.relative_to(staged).as_posix(): file.read_bytes() for file in _package_files(staged)}
    if HEADER in files:
      raise ProjectError(f"{HEADER} is reserved for generated package metadata.")
    header = {"schema_version": 1, "name": project.name, "version": version,
              "digest": _package_digest(staged), "dependencies": {name: versions[name] for name in sorted(dependencies)},
              "description": metadata.get("description", ""), "scope": metadata.get("scope", ""),
              "capabilities": metadata.get("capabilities", []), "limitations": metadata.get("limitations", []),
              "tests": metadata.get("tests", []), "include": metadata.get("include", []),
              "runtime": metadata.get("runtime"), "kind": metadata["kind"], "exports": metadata.get("exports", {}),
              "files": [{"path": name, "size": len(content), "digest": _digest(content)} for name, content in sorted(files.items())]}
    files[HEADER] = (json.dumps(header, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{project.name}-{version}.texpkg"
    descriptor, temporary = tempfile.mkstemp(prefix=".package-", dir=output)
    os.close(descriptor)
    try:
      with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
          info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
          info.create_system = 3
          info.external_attr = (stat.S_IFREG | 0o644) << 16
          archive.writestr(info, content)
      read_archive(Path(temporary))
      os.replace(temporary, target)
    finally:
      Path(temporary).unlink(missing_ok=True)
  return {**header, "artifact": str(target), "archive_digest": _digest(target.read_bytes())}


@_io_errors
def pack_package(source: Path, output: Path | None = None) -> dict:
  """Pack a capability and its complete dependency graph without changing sources."""
  from texsys.dependencies import resolve_dependencies, _read_manifest
  source = source.expanduser().resolve()
  project = inspect_project(source)
  if project is None:
    raise ProjectError(f"Not a package project: {source}")
  package_metadata(source)
  resolved = resolve_dependencies(source)
  packages = [(item.name, item.version, item.root) for item in resolved.packages]
  version = _read_manifest(source).get("project", {}).get("version")
  _identity(project.name, version)
  packages.append((project.name, version, source))
  versions = {name: value for name, value, _ in packages}
  output = (output or source / project.build / "packages").expanduser().resolve()
  # Output may be under build, but never inside authoritative input directories.
  if any(output.is_relative_to(source / folder) for folder in (project.source, project.assets)):
    raise ProjectError("Package output must not be inside source or assets.")
  artifacts = [_archive(path, versions, output) for _, _, path in sorted(packages)]
  root_artifact = next(item["artifact"] for item in artifacts if item["name"] == project.name)
  return {"name": project.name, "version": version, "root_artifact": root_artifact, "artifacts": artifacts}


def read_archive(path: Path, destination: Path | None = None) -> dict:
  """Validate every archive byte and optionally extract into an empty staging tree."""
  if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_ARCHIVE_BYTES:
    raise ProjectError(f"Package archive is missing, linked, or too large: {path}")
  content = path.read_bytes()
  try:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
      members = archive.infolist()
      if not members or len(members) > MAX_FILES:
        raise ProjectError("Package archive file count is invalid.")
      names = set()
      total = 0
      for member in members:
        name = _canonical_member(member.filename)
        mode = member.external_attr >> 16
        if name.casefold() in names or member.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
          raise ProjectError(f"Duplicate or non-regular package member: {name}")
        names.add(name.casefold())
        total += member.file_size
        if member.file_size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
          raise ProjectError("Package archive exceeds extraction limits.")
      if HEADER not in archive.namelist():
        raise ProjectError("Package archive has no metadata.")
      header = _json(archive.read(HEADER))
      if type(header.get("schema_version")) is not int or header["schema_version"] != 1:
        raise ProjectError("Unsupported package archive schema.")
      _identity(header.get("name"), header.get("version"))
      records = header.get("files")
      if not isinstance(records, list) or not records:
        raise ProjectError("Package archive has no file inventory.")
      inventory = {}
      for item in records:
        if not isinstance(item, dict) or set(item) != {"path", "size", "digest"}:
          raise ProjectError("Invalid package file inventory.")
        name = _canonical_member(item["path"])
        if name == HEADER or name in inventory or type(item["size"]) is not int or item["size"] < 0:
          raise ProjectError("Duplicate or invalid package file inventory.")
        inventory[name] = item
      if set(archive.namelist()) != set(inventory) | {HEADER}:
        raise ProjectError("Package inventory does not match archive contents.")
      payload = {}
      for name, item in inventory.items():
        data = archive.read(name)
        if len(data) != item["size"] or _digest(data) != item["digest"]:
          raise ProjectError(f"Package member digest mismatch: {name}")
        payload[name] = data
  except (OSError, ValueError, TypeError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
    raise ProjectError(f"Cannot read package archive {path}: {error}") from error
  # The declared tree must also match the ordinary package hashing and layout rules.
  from texsys.dependencies import _package_digest, _package_files, _read_manifest
  with tempfile.TemporaryDirectory(prefix="texsys-validate-") as directory:
    staged = Path(directory).resolve()
    for name, data in payload.items():
      target = staged / name
      target.parent.mkdir(parents=True, exist_ok=True)
      target.write_bytes(data)
    project = inspect_project(staged)
    if project is None or project.name != header["name"]:
      raise ProjectError("Archive project identity does not match package metadata.")
    for layout in (project.source, project.assets):
      (staged / layout).mkdir(parents=True, exist_ok=True)
    data = _read_manifest(staged)
    metadata = package_metadata(staged)
    dependencies = data.get("dependencies", {})
    if (data.get("project", {}).get("version") != header["version"]
        or not isinstance(dependencies, dict)
        or any(not isinstance(value, dict) or set(value) != {"registry", "version"} or value["registry"] != "."
               for value in dependencies.values())
        or {name: item["version"] for name, item in dependencies.items()} != header.get("dependencies")):
      raise ProjectError("Archive version or dependency metadata mismatch.")
    for name, version in header["dependencies"].items():
      _identity(name, version)
    if (set(payload) != {file.relative_to(staged).as_posix() for file in _package_files(staged)}
        or _package_digest(staged) != header.get("digest") or metadata.get("runtime") != header.get("runtime")):
      raise ProjectError("Archive content digest, inventory or runtime metadata mismatch.")
    for key, default in (("description", ""), ("scope", ""), ("capabilities", []), ("limitations", []), ("tests", []), ("include", [])):
      if metadata.get(key, default) != header.get(key):
        raise ProjectError(f"Archive {key} metadata mismatch.")
    for key, default in (("kind", "capability"), ("exports", {})):
      if metadata.get(key, default) != header.get(key, default):
        raise ProjectError(f"Archive {key} metadata mismatch.")
    if destination is not None:
      if destination.exists() and any(destination.iterdir()):
        raise ProjectError("Archive extraction destination must be empty.")
      shutil.copytree(staged, destination, dirs_exist_ok=True)
  return {**header, "archive_digest": _digest(content)}


@contextmanager
def _registry_write(root: Path):
  root.mkdir(parents=True, exist_ok=True)
  lock = root / ".texsys-registry.lock"
  try:
    lock.mkdir()
  except FileExistsError as error:
    raise ProjectError(f"Registry is being updated: {root}") from error
  try:
    yield
  finally:
    lock.rmdir()


def _release_path(registry: Path, name: str, version: str) -> Path:
  _identity(name, version)
  base = registry.expanduser().resolve()
  path = base / "packages" / name / version
  if path.resolve() != path:
    raise ProjectError("Registry release paths cannot contain symlinks.")
  return path


def package_details(registry: Path, name: str, version: str) -> dict:
  root = registry.expanduser().resolve()
  release = _release_path(root, name, version)
  metadata_file = release / "package.json"
  try:
    if metadata_file.is_symlink():
      raise ProjectError("Registry metadata cannot use symlinks.")
    metadata = _json(metadata_file.read_bytes())
  except OSError as error:
    raise ProjectError(f"Package release is unavailable: {name}@{version} in {root}") from error
  actual = read_archive(release / "artifact.texpkg")
  if metadata != actual or actual["name"] != name or actual["version"] != version:
    raise ProjectError(f"Registry release metadata mismatch: {name}@{version}")
  return {**metadata, "registry": str(root), "artifact": str(release / "artifact.texpkg")}


def search_packages(registry: Path, query: str = "") -> dict:
  root = registry.expanduser().resolve()
  if not root.is_dir():
    raise ProjectError(f"Registry directory does not exist: {root}")
  packages = []
  for path in sorted((root / "packages").glob("*/*/package.json")):
    if path.parent.name.startswith("."):
      continue  # A publisher's private staging directory is not a release.
    entry = package_details(root, path.parent.parent.name, path.parent.name)
    if query.casefold() in f"{entry['name']} {entry.get('description', '')} {' '.join(entry.get('capabilities', []))}".casefold():
      packages.append(entry)
  return {"registry": str(root), "packages": packages}


@_io_errors
def publish_package(source: Path, registry: Path) -> dict:
  """Publish a complete prepared set atomically per immutable release."""
  source = source.expanduser().resolve()
  root = registry.expanduser().resolve()
  with tempfile.TemporaryDirectory(prefix="texsys-publish-") as directory:
    if source.is_dir():
      packed = pack_package(source, Path(directory) / "archives")
      archives = [Path(item["artifact"]) for item in packed["artifacts"]]
      primary = (packed["name"], packed["version"])
    else:
      primary_metadata = read_archive(source)
      primary = (primary_metadata["name"], primary_metadata["version"])
      archives = [source]
      pending = dict(primary_metadata["dependencies"])
      visited = {primary}
      while pending:
        name, version = pending.popitem()
        if (name, version) in visited:
          continue
        visited.add((name, version))
        sibling = source.parent / f"{name}-{version}.texpkg"
        if sibling.is_file():
          metadata = read_archive(sibling)
          if (metadata["name"], metadata["version"]) != (name, version):
            raise ProjectError("Sibling archive identity mismatch.")
          archives.append(sibling)
          pending.update(metadata["dependencies"])
        else:
          metadata = package_details(root, name, version)
          pending.update(metadata["dependencies"])
    entries = [(path, read_archive(path)) for path in archives]
    by_identity = {}
    for path, metadata in entries:
      identity = (metadata["name"], metadata["version"])
      if identity in by_identity and by_identity[identity][1]["archive_digest"] != metadata["archive_digest"]:
        raise ProjectError(f"Conflicting archives for {identity[0]}@{identity[1]}")
      by_identity[identity] = (path, metadata)
    ordered, visiting, complete = [], set(), set()

    def visit(identity):
      if identity in visiting:
        raise ProjectError("Package release dependency cycle detected.")
      if identity in complete:
        return
      visiting.add(identity)
      entry = by_identity.get(identity)
      metadata = entry[1] if entry is not None else package_details(root, *identity)
      for child in metadata["dependencies"].items():
        visit(child)
      visiting.remove(identity)
      complete.add(identity)
      if entry is not None:
        ordered.append(entry)

    for identity in sorted(by_identity):
      visit(identity)
    entries = ordered
    identities = {(item["name"], item["version"]) for _, item in entries}
    published = []
    with _registry_write(root):
      for _, metadata in entries:
        for name, version in metadata["dependencies"].items():
          if (name, version) not in identities:
            package_details(root, name, version)
        release = _release_path(root, metadata["name"], metadata["version"])
        if release.exists():
          existing = package_details(root, metadata["name"], metadata["version"])
          if existing["archive_digest"] != metadata["archive_digest"]:
            raise ProjectError(f"Immutable release already exists with different content: {metadata['name']}@{metadata['version']}")
      created = []
      try:
        for archive, metadata in entries:
          release = _release_path(root, metadata["name"], metadata["version"])
          existed = release.exists()
          if not existed:
            release.parent.mkdir(parents=True, exist_ok=True)
            staged = Path(tempfile.mkdtemp(prefix=".release-", dir=release.parent))
            try:
              shutil.copyfile(archive, staged / "artifact.texpkg")
              (staged / "package.json").write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
              if read_archive(staged / "artifact.texpkg") != metadata:
                raise ProjectError("Archive changed while publishing.")
              staged.rename(release)
              created.append(release)
            finally:
              if staged.exists():
                shutil.rmtree(staged)
          published.append({**metadata, "registry": str(root), "already_published": existed})
      except Exception:
        for release in reversed(created):
          shutil.rmtree(release)
        raise
    return {"name": primary[0], "version": primary[1], "registry": str(root), "packages": published}


def cache_path(digest: str) -> Path:
  import re
  if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    raise ProjectError("Package content digest must be a complete SHA-256.")
  base = Path(os.environ.get("TEXSYS_CACHE_DIR", str(Path.home() / ".cache" / "texsys"))).expanduser().resolve()
  return base / "packages" / digest.removeprefix("sha256:")


@_io_errors
def materialize_package(registry: Path, name: str, version: str, digest: str, archive_digest: str,
                        *, allow_fetch: bool = True, installed: Path | None = None) -> Path:
  """Return a verified immutable tree, using a local copy before registry access."""
  from texsys.dependencies import _package_digest, _read_manifest
  cached = cache_path(digest)
  for candidate in (cached, installed):
    if candidate is None or not candidate.exists():
      continue
    if candidate.resolve() != candidate:
      raise ProjectError(f"Package content cannot use symlinks: {candidate}")
    metadata = _read_manifest(candidate)
    if (metadata.get("project", {}).get("name") != name or metadata.get("project", {}).get("version") != version
        or _package_digest(candidate) != digest):
      raise ProjectError(f"Locked package content changed: {name}@{version}")
    return candidate
  if not allow_fetch:
    raise ProjectError(f"Package {name}@{version} is not materialized; run texsys sync.")
  release = package_details(registry, name, version)
  if release["digest"] != digest or release["archive_digest"] != archive_digest:
    raise ProjectError(f"Locked registry release changed: {name}@{version}")
  cached.parent.mkdir(parents=True, exist_ok=True)
  staged = Path(tempfile.mkdtemp(prefix=".package-", dir=cached.parent))
  try:
    actual = read_archive(Path(release["artifact"]), staged)
    if actual["digest"] != digest or actual["archive_digest"] != archive_digest:
      raise ProjectError("Registry archive changed while extracting.")
    if cached.exists():
      if _package_digest(cached) != digest:
        raise ProjectError("Concurrent package cache has different content.")
    else:
      staged.rename(cached)
  finally:
    if staged.exists():
      shutil.rmtree(staged)
  return cached
