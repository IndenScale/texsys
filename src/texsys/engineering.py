"""Engineering evaluation and immutable evidence for declared project inputs.

Adapters own domain meaning. Source files are edited through ordinary development
tools; any project input or tool change invalidates previous run evidence.
"""

from copy import deepcopy
from datetime import datetime, timezone
import inspect
from importlib import metadata
import json
import math
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import subprocess
import sys
import tempfile
import tomllib
from typing import Any
import uuid

from texsys.dependencies import lock_project
from texsys.evidence import canonical_digest, file_digest
from texsys.execution import _input_digests
from texsys.formal import VALUE_STATES, _atomic_write, _load_json
from texsys.packages import project_packages
from texsys.project import ProjectError, inspect_project, project_write_lock

ASSESSMENTS = {"pass", "fail", "indeterminate"}
COVERAGE = "declared-adapter-with-project-wide-invalidation"
WORKER_TIMEOUT_SECONDS = {"run": 600, "default": 120}


def _project(path):
  root = Path(path).expanduser().resolve()
  project = inspect_project(root)
  if project is None:
    raise ProjectError("Initialize the project before inspecting engineering inputs.")
  for name in ("texsys.toml", "texsys.lock", project.source, project.assets, project.build):
    target = root / name
    if target.resolve() != target:
      raise ProjectError(f"Engineering inputs and outputs cannot use symlinks: {name}")
  return root, project


def _records(root, project):
  records = _input_digests(root, project.source, project.assets)
  for record in records:
    path = root / record["path"]
    if path.resolve() != path:
      raise ProjectError(f"Engineering input cannot use symlinks: {record['path']}")
  return records


def _lock_error(root):
  try:
    manifest = tomllib.loads((root / "texsys.toml").read_text(encoding="utf-8"))
    if manifest.get("dependencies") or (root / "texsys.lock").exists():
      lock_project(root, check=True)
  except (OSError, ValueError, ProjectError) as error:
    return str(error)
  return None


def _discover(root):
  packages = project_packages(root)
  found = {}
  for package in packages:
    for adapter in package.engineering_adapters():
      if not all(isinstance(getattr(adapter, name, None), str) and getattr(adapter, name).strip()
                 for name in ("id", "name", "version")):
        raise ProjectError("Engineering adapters require id, name and version.")
      if not re.fullmatch(r"[a-z][a-z0-9-]*:[a-z][a-z0-9-]*", adapter.id):
        raise ProjectError("Engineering adapter identity must be namespaced.")
      if adapter.id in found:
        raise ProjectError(f"Duplicate engineering adapter: {adapter.id}")
      adapter_metadata = {"id": adapter.id, "name": adapter.name,
                          "version": adapter.version, "package": package.name}
      if hasattr(adapter, "description"):
        if not isinstance(adapter.description, str):
          raise ProjectError("Engineering adapter description must be a string when provided.")
        adapter_metadata["description"] = adapter.description
      if adapter.supports(root):
        found[adapter.id] = (adapter, adapter_metadata)
  return packages, found


def _toolchain(packages, adapter):
  """Record loaded Python package sources as well as declared versions.

  Native libraries are identified by distribution versions and platform, not by a
  claim of bit-identical executables. Adapters may include extra backend identity
  in their result when their solver needs it.
  """
  sources = {}
  for label, owner in [("texsys", _project), ("adapter", type(adapter)),
                       *((package.name, type(package)) for package in packages)]:
    try:
      source = Path(inspect.getfile(owner)).resolve()
    except (OSError, TypeError):
      continue
    files = sorted(source.parent.rglob("*.py")) if source.name == "__init__.py" else [source]
    if label == "texsys":
      files = sorted(source.parent.rglob("*.py"))
    sources[label] = []
    loaded = getattr(sys, "_texsys_loaded_sources", {})
    for file in files:
      if not file.is_file():
        continue
      digest = file_digest(file)
      if str(file) in loaded and loaded[str(file)] != digest:
        raise ProjectError(f"Executing Python source changed after it was loaded: {file.name}; retry in a fresh worker.")
      sources[label].append({"path": file.relative_to(source.parent).as_posix(), "digest": digest})
  distributions = sorted({(dist.metadata.get("Name", ""), dist.version) for dist in metadata.distributions()})
  return {"python": platform.python_version(), "platform": platform.platform(),
          "packages": [{"name": p.name, "version": p.version} for p in packages],
          "locked_runtime": [p._texsys_locked_runtime for p in packages if hasattr(p, "_texsys_locked_runtime")],
          "distributions": [{"name": name, "version": version} for name, version in distributions],
          "sources": sources, "adapter_version": adapter.version,
          "runner": "fresh-python-process" if hasattr(sys, "_texsys_loaded_sources") else "in-process-test"}


def _context(path, adapter_id=None, require=False):
  root, project = _project(path)
  records = _records(root, project)
  packages, adapters = _discover(root)
  if adapter_id is None and adapters:
    adapter_id = sorted(adapters)[0]
  if adapter_id is not None and adapter_id not in adapters:
    raise ProjectError(f"Engineering adapter is unavailable for this project: {adapter_id}")
  selected = adapters.get(adapter_id)
  if require and selected is None:
    raise ProjectError("No engineering adapter supports this project.")
  tools = _toolchain(packages, selected[0]) if selected else None
  dependency_error = _lock_error(root)
  if _records(root, project) != records:
    raise ProjectError("Engineering inputs changed while capturing tools and dependencies. Refresh and retry.")
  return {"root": root, "project": project, "records": records, "revision": canonical_digest(records),
          "adapters": adapters, "adapter": selected[0] if selected else None,
          "selected": selected[1] if selected else None, "toolchain": tools,
          "toolchain_digest": canonical_digest(tools) if tools else None,
          "dependency_error": dependency_error}


def _number(value):
  try:
    return type(value) in (int, float) and math.isfinite(value)
  except OverflowError:
    return False


def _validate_design(design):
  """Validate an explicit selection's shape without inferring model identity.

  The consumer must resolve the selection against its current design index;
  passing this check does not establish that an object exists in the model.
  """
  if isinstance(design, dict):
    if set(design) == {"definition"}:
      if isinstance(design["definition"], str) and design["definition"].strip():
        return
    elif set(design) == {"occurrence"}:
      path = design["occurrence"]
      if (isinstance(path, list) and path
          and all(isinstance(part, str) and part.strip() for part in path)):
        return
  raise ProjectError("Engineering design selection must contain exactly one non-empty definition string or non-empty occurrence string array.")


def _source_path(context, file):
  if not isinstance(file, str) or not file or "\\" in file or "\0" in file:
    raise ProjectError("Adapter input must identify a regular JSON source file.")
  relative = PurePosixPath(file)
  if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != file:
    raise ProjectError("Adapter input must stay inside the project source directory.")
  root = context["root"]
  target = root / file
  if (not target.is_relative_to(root / context["project"].source) or target.resolve() != target
      or not target.is_file() or target.suffix != ".json"):
    raise ProjectError("Adapter input must identify a regular JSON source file without symlinks.")
  return target


def _pointer_parent(document, pointer):
  if not isinstance(pointer, str) or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
    raise ProjectError("Adapter input requires an RFC 6901 JSON pointer.")
  parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
  current = document
  try:
    for part in parts[:-1]:
      current = current[int(part)] if isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", part) else current[part]
    key = parts[-1]
    if isinstance(current, list):
      if not re.fullmatch(r"0|[1-9][0-9]*", key):
        raise KeyError(key)
      key = int(key)
    current[key]
    return current, key
  except (KeyError, IndexError, TypeError, ValueError) as error:
    raise ProjectError(f"Adapter input pointer does not identify an existing value: {pointer}") from error


def _inputs(context):
  inputs = context["adapter"].inputs(context["root"])
  if not isinstance(inputs, list):
    raise ProjectError("Engineering adapter inputs must be an array.")
  seen, locations, result = set(), set(), []
  for item in inputs:
    if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key]
                                           for key in ("id", "label", "file", "pointer", "unit", "object_ref")):
      raise ProjectError("Engineering input requires identity, label, source, pointer, unit and object reference.")
    if "design" in item:
      _validate_design(item["design"])
    if "state" in item:
      state = item["state"]
      if not isinstance(state, str) or state not in VALUE_STATES:
        raise ProjectError(f"Invalid engineering input value state: {item['id']}")
      if state not in {"known", "assumed"}:
        raise ProjectError(f"Engineering input state {state} forbids a numeric value; numeric evidence requires a known or assumed value: {item['id']}")
    location = (item["file"], item["pointer"])
    if item["id"] in seen or location in locations:
      raise ProjectError("Engineering input identities and source locations must be unique.")
    seen.add(item["id"])
    locations.add(location)
    path = _source_path(context, item["file"])
    try:
      document = _load_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
      raise ProjectError(f"Cannot read engineering input: {error}") from error
    parent, key = _pointer_parent(document, item["pointer"])
    value = parent[key]
    if not _number(value):
      raise ProjectError(f"Engineering input is not a finite number: {item['id']}")
    for bound in ("min", "max"):
      if bound in item and not _number(item[bound]):
        raise ProjectError(f"Invalid adapter input {bound}: {item['id']}")
    if "min" in item and "max" in item and item["min"] > item["max"]:
      raise ProjectError(f"Inverted adapter input bounds: {item['id']}")
    result.append({**item, "value": value})
  return result


def _safe_directory(root, relative):
  directory = root / relative
  if directory.resolve() != directory or not directory.is_relative_to(root):
    raise ProjectError("Engineering evidence directory must remain inside the project without symlinks.")
  return directory


def _run_directory(context):
  return _safe_directory(context["root"], context["project"].build) / "engineering/runs"


def _write_record(path, record):
  if path.resolve() != path:
    raise ProjectError("Engineering evidence cannot use symlinks.")
  path.parent.mkdir(parents=True, exist_ok=True)
  _atomic_write(path, json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n", create_only=True)


def _read_record(path, digest_key):
  if path.resolve() != path or not path.is_file():
    raise ProjectError("Engineering evidence must be a regular file without symlinks.")
  try:
    record = _load_json(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get(digest_key) != canonical_digest({k: v for k, v in record.items() if k != digest_key}):
      raise ValueError("content digest does not match")
  except (OSError, ValueError, TypeError) as error:
    raise ProjectError(f"Engineering evidence integrity check failed: {path.name}: {error}") from error
  return record


def _freshness(context, run):
  if context["dependency_error"]:
    return "dependency-invalid"
  if not run.get("inputs_consistent", False) or run.get("revision") != context["revision"]:
    return "inputs-changed"
  if run.get("toolchain_digest") != context["toolchain_digest"]:
    return "tools-changed"
  if run.get("adapter") != context["selected"]:
    return "adapter-changed"
  return "current"


def _runs(context):
  result, diagnostics = [], []
  directory = _run_directory(context)
  if directory.resolve() != directory:
    raise ProjectError("Engineering run storage cannot use symlinks.")
  for path in directory.glob("*.json"):
    try:
      run = _read_record(path, "evidence_digest")
      if run.get("adapter", {}).get("id") != context["selected"]["id"]:
        continue
      if run.get("run_id") != path.stem:
        raise ProjectError("Run file identity does not match its record.")
      freshness = _freshness(context, run)
      result.append({**run, "current": freshness == "current", "freshness": freshness,
                     "path": path.relative_to(context["root"]).as_posix()})
    except (ProjectError, KeyError, TypeError) as error:
      diagnostics.append({"path": path.relative_to(context["root"]).as_posix(), "message": str(error)})
  return sorted(result, key=lambda item: (item["created_at"], item["run_id"]), reverse=True), diagnostics


def _comparison(before, after):
  if before is None or after is None:
    return None
  previous = {item["id"]: item for item in before["result"].get("checks", [])}
  current = {item["id"]: item for item in after["result"].get("checks", [])}
  changes = []
  for key in sorted(previous.keys() | current.keys()):
    a, b = previous.get(key), current.get(key)
    if a != b:
      changes.append({"id": key, "before": a, "after": b})
  return {"base_run_id": before["run_id"], "run_id": after["run_id"], "checks": changes,
          "before_assessment": before["result"]["assessment"], "after_assessment": after["result"]["assessment"],
          "before_extraction": before["result"].get("extraction", []),
          "after_extraction": after["result"].get("extraction", [])}


def _inspect_engineering_local(path: Path, adapter_id: str | None = None) -> dict[str, Any]:
  """Inspect engineering inputs and recalculate historical evidence applicability."""
  context = _context(path, adapter_id)
  state = {"version": 1, "available": context["adapter"] is not None, "revision": context["revision"],
           "adapters": [entry[1] for entry in context["adapters"].values()], "selected": context["selected"],
           "coverage": COVERAGE, "dependency_error": context["dependency_error"], "inputs": [],
           "runs": [], "latest": None, "comparison": None, "diagnostics": []}
  if context["adapter"] is None:
    return state
  state["inputs"] = _inputs(context)
  state["runs"], state["diagnostics"] = _runs(context)
  if state["runs"]:
    state["latest"] = state["runs"][0]
  if len(state["runs"]) > 1:
    state["comparison"] = _comparison(state["runs"][1], state["runs"][0])
  if _records(context["root"], context["project"]) != context["records"]:
    raise ProjectError("Engineering inputs changed while reading. Refresh the current revision.")
  return state


def _validate_result(result):
  if not isinstance(result, dict) or result.get("assessment") not in ASSESSMENTS:
    raise ProjectError("Adapter evaluation requires a separate engineering assessment.")
  checks = result.get("checks")
  if not isinstance(checks, list) or not checks:
    raise ProjectError("Engineering assessment requires explicit checks.")
  seen = set()
  for check in checks:
    if (not isinstance(check, dict) or not all(isinstance(check.get(key), str) and check[key]
                                              for key in ("id", "label", "object_ref"))
        or check.get("status") not in ASSESSMENTS | {"not-applicable"} or check["id"] in seen):
      raise ProjectError("Engineering checks require unique identity, object reference, label and assessment status.")
    seen.add(check["id"])
    if "design" in check:
      _validate_design(check["design"])
  statuses = {item["status"] for item in checks}
  if result["assessment"] == "pass" and (statuses & {"fail", "indeterminate"} or "pass" not in statuses):
    raise ProjectError("An engineering pass cannot hide failed, missing or unassessed checks.")
  for key in ("extraction", "limitations", "targets"):
    if key not in result:
      result[key] = []
    if not isinstance(result[key], list):
      raise ProjectError(f"Engineering result {key} must be an array.")
  for extraction in result["extraction"]:
    if isinstance(extraction, dict) and "design" in extraction:
      _validate_design(extraction["design"])
  canonical_digest(result)  # Reject non-JSON and non-finite evidence.
  return result


def _run_engineering_local(path: Path, adapter_id: str) -> dict[str, Any]:
  """Run an adapter, preserve immutable evidence and detect input/tool drift."""
  root, _ = _project(path)
  with project_write_lock(root):
    context = _context(root, adapter_id, require=True)
    if context["dependency_error"]:
      raise ProjectError(context["dependency_error"])
    values = _inputs(context)
    run = {"version": 1, "run_id": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
           "adapter": context["selected"], "revision": context["revision"], "inputs": context["records"],
           "input_values": values, "toolchain": context["toolchain"],
           "toolchain_digest": context["toolchain_digest"], "coverage": COVERAGE,
           "execution": "completed", "inputs_consistent": True, "diagnostics": []}
    try:
      run["result"] = _validate_result(deepcopy(context["adapter"].evaluate(root)))
    except Exception as error:
      run["execution"] = "error"
      run["diagnostics"].append({"code": "ENGINEERING_EXECUTION", "message": str(error)})
      run["result"] = {"assessment": "indeterminate", "checks": [], "extraction": [],
                       "limitations": ["Execution did not produce valid engineering evidence."], "targets": []}
    try:
      latest = _context(root, adapter_id, require=True)
      changed = (latest["records"] != context["records"] or latest["toolchain_digest"] != context["toolchain_digest"]
                 or latest["dependency_error"])
    except Exception as error:
      latest = {**context, "dependency_error": str(error)}
      changed = True
    if changed:
      run["inputs_consistent"] = False
      run["execution"] = "error"
      run["diagnostics"].append({"code": "ENGINEERING_INPUT_CHANGED",
                                 "message": "Inputs, dependencies or executing tools changed during this run; evidence is not current."})
    run["evidence_digest"] = canonical_digest(run)
    output = _run_directory(context) / f"{run['run_id']}.json"
    _write_record(output, run)
    freshness = _freshness(latest, run)
    return {**run, "current": freshness == "current", "freshness": freshness,
            "path": output.relative_to(root).as_posix()}


def _dispatch(action, arguments):
  """Private worker protocol. Only known operations can be dispatched."""
  actions = {
    "inspect": _inspect_engineering_local,
    "run": _run_engineering_local,
  }
  if action not in actions or not isinstance(arguments, list):
    raise ProjectError("Unknown engineering worker operation.")
  return actions[action](*arguments)


def _worker_call(action, arguments):
  """Use fresh loaded Python code for all domain and evidence operations.

  Each inspection and evaluation uses a fresh worker, so a long-lived caller
  cannot combine old adapter code with a new source digest. Its temporary
  pycache prefix prevents stale timestamp-based bytecode.
  """
  try:
    payload = json.dumps({"version": 1, "action": action, "arguments": arguments}, allow_nan=False)
  except (TypeError, ValueError) as error:
    raise ProjectError(f"Engineering request must contain finite JSON values: {error}") from error
  timeout = WORKER_TIMEOUT_SECONDS.get(action, WORKER_TIMEOUT_SECONDS["default"])
  try:
    with tempfile.TemporaryDirectory(prefix="texsys-engineering-pycache-") as cache:
      worker = subprocess.Popen(
        [sys.executable, "-B", "-X", f"pycache_prefix={cache}", "-m", "texsys.engineering_worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
      )
      try:
        stdout, stderr = worker.communicate(payload, timeout=timeout)
      except subprocess.TimeoutExpired as error:
        # Let project_write_lock's finally block release this worker's lock.
        # If native code cannot be interrupted, retain the lock for inspection.
        forced = False
        try:
          worker.send_signal(signal.SIGINT)
          worker.communicate(timeout=5)
        except subprocess.TimeoutExpired:
          forced = True
          worker.kill()
          worker.communicate()
        except ProcessLookupError:
          worker.communicate()
        detail = " The worker required force-stop; inspect its project writer lock before retrying." if forced else ""
        raise ProjectError(f"Engineering {action} worker exceeded {timeout} seconds.{detail}") from error
  except OSError as error:
    raise ProjectError(f"Cannot start engineering worker: {error}") from error
  try:
    envelope = _load_json(stdout)
    if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
      raise ValueError("missing worker envelope")
  except (ValueError, TypeError) as error:
    detail = stderr[-2000:].strip()
    raise ProjectError(f"Engineering worker did not return valid evidence: {detail or str(error)}") from error
  if not envelope["ok"] or worker.returncode != 0:
    raise ProjectError(str(envelope.get("error", "Engineering worker failed.")))
  result = envelope.get("result")
  if not isinstance(result, dict):
    raise ProjectError("Engineering worker returned an invalid result.")
  return result


def inspect_engineering(path: Path, adapter_id: str | None = None) -> dict[str, Any]:
  return _worker_call("inspect", [str(Path(path).expanduser().resolve()), adapter_id])


def run_engineering(path: Path, adapter_id: str) -> dict[str, Any]:
  return _worker_call("run", [str(Path(path).expanduser().resolve()), adapter_id])
