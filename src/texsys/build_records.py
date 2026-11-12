"""Content-addressed build reuse and immutable, inspectable build records.

Builders declare their inputs and outputs. Undeclared external effects require
``TargetDefinition(cache=False)``; hooks are deliberately never reused.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import fnmatch
import hashlib
from importlib import metadata
import inspect
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
from types import CodeType

from texsys.dependencies import lock_project
from texsys.evidence import canonical_digest, file_digest, write_json
from texsys.project import ProjectError, inspect_project


def build_input_paths(root, source, assets):
  """Enumerate authoritative input files in the order used by build records."""
  for name in ("texsys.toml", "texsys.lock"):
    path = root / name
    if path.is_file():
      if path.resolve() != path:
        raise ProjectError(f"Build inputs cannot use symlinks: {name}")
      yield path
  for directory in (source, assets):
    for path in sorted((root / directory).rglob("*")):
      if path.is_file() and path.name != ".gitkeep":
        if path.resolve() != path:
          raise ProjectError(f"Build inputs cannot use symlinks: {path.relative_to(root)}")
        yield path


def input_records(root, source, assets):
  return [{"path": path.relative_to(root).as_posix(), "digest": file_digest(path)}
          for path in build_input_paths(root, source, assets)]


def _safe(root, relative):
  path = root / relative
  if path.resolve() != path or not path.is_relative_to(root) or ".." in Path(relative).parts:
    raise ProjectError(f"Build storage must stay inside the project without symlinks: {relative}")
  return path


@contextmanager
def build_lock(root, build):
  directory = _safe(root, build)
  directory.mkdir(parents=True, exist_ok=True)
  lock = directory / ".build.lock"
  try:
    lock.mkdir()
  except FileExistsError as error:
    raise BuildBusy("Another build holds this project's build lock. Retry after it finishes.") from error
  try:
    (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "started_at": timestamp()}))
    yield directory
  finally:
    (lock / "owner.json").unlink(missing_ok=True)
    lock.rmdir()


class BuildBusy(ProjectError):
  """A concurrent build already owns the project's publication lock."""


def timestamp():
  return datetime.now(timezone.utc).isoformat()


def _simple(value):
  if value is None or type(value) in (str, int, float, bool):
    return value
  if isinstance(value, tuple):
    return [_simple(item) for item in value]
  if isinstance(value, dict):
    return {str(key): _simple(item) for key, item in value.items()
            if item is None or type(item) in (str, int, float, bool, tuple, dict)}
  return {"type": type(value).__qualname__}


def _code_constant(value):
  if isinstance(value, CodeType):
    return _code_identity(value)
  if isinstance(value, bytes):
    return {"bytes": value.hex()}
  if isinstance(value, (tuple, frozenset)):
    values = [_code_constant(item) for item in value]
    return sorted(values, key=repr) if isinstance(value, frozenset) else values
  if value is None or type(value) in (str, int, float, bool):
    return value
  return repr(value)


def _code_identity(code):
  # marshal includes mutable string-interning/reference flags and therefore can
  # change after the same function is executed earlier in a build. Bytecode and
  # semantic constants remain stable across fresh CLI workers and cache hits.
  return {"bytecode": code.co_code.hex(), "constants": [_code_constant(item) for item in code.co_consts],
          "names": code.co_names, "varnames": code.co_varnames, "freevars": code.co_freevars,
          "cellvars": code.co_cellvars, "arguments": [code.co_argcount, code.co_posonlyargcount,
                                                       code.co_kwonlyargcount],
          "flags": code.co_flags, "exceptions": code.co_exceptiontable.hex()}


def builder_identity(builder):
  function = getattr(builder, "__func__", builder)
  code = getattr(function, "__code__", None)
  bindings = {}
  for name, cell in zip(getattr(code, "co_freevars", ()), getattr(function, "__closure__", ()) or ()):
    try:
      value = cell.cell_contents
      # Mutable counters/caches are not part of a pure builder's configuration.
      if type(value) in (str, int, float, bool, tuple, dict) or value is None:
        bindings[name] = _simple(value)
      elif name == "self":
        bindings[name] = _simple(vars(value))
    except ValueError:
      continue
  return {"module": getattr(function, "__module__", ""),
          "name": getattr(function, "__qualname__", type(function).__qualname__),
          "code": canonical_digest(_code_identity(code)) if code else None,
          "defaults": _simple(getattr(function, "__defaults__", None)), "bindings": bindings}


def toolchain(packages, definitions):
  files = set(Path(__file__).parent.rglob("*.py"))
  for owner in [*(type(package) for package in packages), *(item.builder for item in definitions),
                *(item.input_patterns for item in definitions if callable(item.input_patterns)),
                *(item.cache for item in definitions if callable(item.cache))]:
    try:
      file = Path(inspect.getfile(owner)).resolve()
    except (OSError, TypeError):
      continue
    files.update(file.parent.rglob("*.py") if file.name == "__init__.py" else (file,))
  sources = []
  loaded = getattr(sys, "_texsys_loaded_sources", {})
  for file in sorted(files):
    if not file.is_file():
      continue
    digest = file_digest(file)
    if str(file) in loaded and loaded[str(file)] != digest:
      raise ProjectError(f"Executing Python source changed after loading: {file.name}; retry in a fresh worker.")
    sources.append({"path": str(file), "digest": digest})
  return {"python": platform.python_version(), "platform": platform.platform(),
          "sources": sources,
          "packages": [{"name": p.name, "version": p.version} for p in packages],
          "locked_runtime": [p._texsys_locked_runtime for p in packages if hasattr(p, "_texsys_locked_runtime")],
          "distributions": distribution_versions()}


def distribution_versions():
  return sorted([list(pair) for pair in {(dist.metadata.get("Name", ""), dist.version)
                                         for dist in metadata.distributions()}])


def target_inputs(records, patterns):
  if patterns is None:
    return list(records)
  return [item for item in records if item["path"] in {"texsys.toml", "texsys.lock"}
          or any(fnmatch.fnmatchcase(item["path"], pattern) for pattern in patterns)]


def cache_environment(definitions):
  return {name: canonical_digest(os.environ.get(name))
          for name in sorted({name for definition in definitions for name in definition.cache_environment})}


def target_cache_key(resolved, records, tools_digest, dependencies, patterns=None):
  definition = resolved.definition
  return canonical_digest({"schema_version": 2, "target": resolved.id, "using": definition.id,
                           "output": resolved.output,
                           "package": [definition.package_name, definition.package_version],
                           "builder": builder_identity(definition.builder), "toolchain": tools_digest,
                           "profile": resolved.profile, "scope": dict(resolved.scope),
                           "input_patterns": patterns,
                           "input_selector": builder_identity(definition.input_patterns)
                           if callable(definition.input_patterns) else None,
                           "cache_policy": builder_identity(definition.cache) if callable(definition.cache)
                           else definition.cache,
                           # Never store raw environment values, which may contain credentials.
                           "environment": cache_environment((definition,)),
                           "inputs": target_inputs(records, patterns),
                           "dependencies": dependencies})


def _verified_report(root, path):
  try:
    report = json.loads(_safe(root, path).read_text())
    identifier = report.get("run_id")
    if (isinstance(identifier, str) and len(identifier) == 32
        and all(character in "0123456789abcdef" for character in identifier)
        and report.get("record_digest") == canonical_digest(
          {key: value for key, value in report.items() if key != "record_digest"}
        )):
      return report
  except (AttributeError, OSError, ValueError, TypeError, ProjectError):
    pass
  return None


def previous_run_id(root, build_root):
  """Read only a verified latest record; malformed history never becomes a link."""
  report = _verified_report(root, build_root / "build.json")
  return report["run_id"] if report else None


def _prior_publication(root, build_root, run_id, path, digest):
  seen = set()
  while isinstance(run_id, str) and run_id not in seen and len(seen) < 100:
    seen.add(run_id)
    if len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
      break
    report = _verified_report(root, build_root / "runs" / run_id / "build.json")
    if report is None or report["run_id"] != run_id:
      break
    records = report.get("artifacts", [])
    if not isinstance(records, list):
      break
    for record in records:
      if (isinstance(record, dict) and record.get("path") == path
          and record.get("published") is not False and record.get("digest") == digest):
        return record
    run_id = report.get("previous_run_id")
  return None


def _copy_verified(source, destination, digest):
  data = source.read_bytes()
  if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
    raise ProjectError(f"Build content changed while reading: {source.name}")
  destination.parent.mkdir(parents=True, exist_ok=True)
  if destination.exists():
    if file_digest(destination) != digest:
      raise ProjectError(f"Two build outputs conflict at: {destination.name}")
    return
  with destination.open("xb") as stream:
    stream.write(data)


def snapshot(root, run_root, records, source_paths=None):
  result = []
  for record in records:
    source_path = source_paths[record["path"]] if source_paths is not None else record["path"]
    source = _safe(root, source_path)
    destination = _safe(root, (run_root / "files" / record["path"]).relative_to(root))
    _copy_verified(source, destination, record["digest"])
    result.append({**record, "stored_path": destination.relative_to(root).as_posix()})
  return result


def _cache_directory(root, build_root, key):
  return _safe(root, (build_root / ".cache" / "targets" / key.removeprefix("sha256:")).relative_to(root))


def restore_cache(root, build_root, stage, key):
  directory = _cache_directory(root, build_root, key)
  try:
    manifest_path = _safe(root, (directory / "entry.json").relative_to(root))
    entry = json.loads(manifest_path.read_text())
    if entry.get("cache_key") != key or entry.get("entry_digest") != canonical_digest(
      {key: value for key, value in entry.items() if key != "entry_digest"}
    ):
      return None
    if entry["target"]["status"] not in {"pass", "fail", "indeterminate"}:
      return None
    sources = []
    for record in entry["artifacts"]:
      destination = artifact_path(root, build_root, record["path"])
      if not record["complete"]:
        return None
      source = _safe(root, (directory / "files" / record["path"]).relative_to(root))
      if file_digest(source) != record["digest"]:
        return None
      sources.append((source, stage / destination.relative_to(build_root), record["digest"]))
    for source, destination, digest in sources:
      _copy_verified(source, destination, digest)
    return entry
  except (OSError, ValueError, KeyError, TypeError, ProjectError):
    if stage.is_dir():
      shutil.rmtree(stage)
    return None


def artifact_path(root, build_root, relative):
  """Resolve a published artifact without allowing writes into private build storage."""
  path = _safe(root, relative)
  if path == build_root or not path.is_relative_to(build_root):
    raise ProjectError(f"Artifact is outside the build directory: {relative}")
  parts = path.relative_to(build_root).parts
  if parts[0].casefold() in {"runs", "dependencies", "studio", "build.json"} or any(part.startswith(".") for part in parts):
    raise ProjectError(f"Artifact overlaps private build storage: {relative}")
  return path


def seed_stage(root, build_root, stage, artifacts):
  """Give a builder a private copy of its declared dependency outputs."""
  for record in artifacts:
    relative = artifact_path(root, build_root, record["path"]).relative_to(build_root)
    source = _safe(root, record["stored_path"])
    _copy_verified(source, stage / relative, record["digest"])


def publish_artifacts(root, build_root, stage, artifacts, previous_run):
  """Install one target's files with atomic replacement and restore on an I/O failure."""
  prepared = []
  installed = []
  published = set()
  try:
    for index, record in enumerate(artifacts):
      destination = artifact_path(root, build_root, record["path"])
      if destination.exists():
        if not destination.is_file():
          raise ProjectError(f"Artifact destination is not a regular file: {record['path']}")
        digest = file_digest(destination)
        if not record["complete"]:
          previous = _prior_publication(root, build_root, previous_run, record["path"], digest)
          if previous is None or (previous.get("complete") is not False and not record.get("publish_partial")):
            continue
        if digest == record["digest"]:
          published.add(record["path"])
          continue
      source = _safe(root, record["stored_path"])
      destination.parent.mkdir(parents=True, exist_ok=True)
      candidate = stage / str(index) / "new"
      _copy_verified(source, candidate, record["digest"])
      backup = candidate.with_name("old") if destination.exists() else None
      if backup is not None:
        shutil.copyfile(destination, backup)
      prepared.append((candidate, destination, backup))
      published.add(record["path"])
    for candidate, destination, backup in prepared:
      os.replace(candidate, destination)
      installed.append((destination, backup))
  except BaseException:
    for destination, backup in reversed(installed):
      if backup is None:
        destination.unlink()
      else:
        os.replace(backup, destination)
    raise
  return published


def store_cache(root, build_root, key, target, artifacts, run_id):
  directory = _cache_directory(root, build_root, key)
  directory.parent.mkdir(parents=True, exist_ok=True)
  temporary = Path(tempfile.mkdtemp(prefix=".target-", dir=directory.parent))
  try:
    records = []
    for record in artifacts:
      source = _safe(root, record["stored_path"])
      _copy_verified(source, temporary / "files" / record["path"], record["digest"])
      records.append({key: value for key, value in record.items() if key not in {"stored_path", "published"}})
    entry = {"schema_version": 1, "cache_key": key, "run_id": run_id,
             "target": target, "artifacts": records}
    entry["entry_digest"] = canonical_digest(entry)
    write_json(temporary / "entry.json", entry)
    if directory.exists():
      shutil.rmtree(directory)
    temporary.rename(directory)
  finally:
    if temporary.exists():
      shutil.rmtree(temporary)


def build_freshness(root, report):
  """Check source/tool applicability without importing project execution plugins."""
  root = Path(root).resolve()
  try:
    if report.get("schema_version") != 3 or report.get("record_digest") != canonical_digest(
      {key: value for key, value in report.items() if key != "record_digest"}
    ):
      return {"current": False, "freshness": "invalid-record"}
    if not report.get("inputs_consistent"):
      return {"current": False, "freshness": "input-inconsistent"}
    project = inspect_project(root)
    if project is None or input_records(root, project.source, project.assets) != report["inputs"]:
      return {"current": False, "freshness": "inputs-changed"}
    for record in report["toolchain"]["sources"]:
      if file_digest(Path(record["path"])) != record["digest"]:
        return {"current": False, "freshness": "tools-changed"}
    if (report["toolchain"].get("python") != platform.python_version()
        or report["toolchain"].get("platform") != platform.platform()
        or report["toolchain"].get("distributions") != distribution_versions()):
      return {"current": False, "freshness": "tools-changed"}
    # Studio may have a different process environment, so environment cache identity
    # is not used to claim an old result became invalid simply by being viewed.
    lock_path = root / "texsys.lock"
    if lock_path.exists():
      try:
        lock_project(root, check=True)
      except ProjectError:
        return {"current": False, "freshness": "dependency-invalid"}
    return {"current": True, "freshness": "current"}
  except (OSError, ValueError, KeyError, TypeError, ProjectError):
    return {"current": False, "freshness": "invalid-record"}
