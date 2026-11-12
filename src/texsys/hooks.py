"""Contract-bound external hook declarations and process execution."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any, Iterable

from texsys.build_records import artifact_path
from texsys.evidence import file_digest, write_json
from texsys.project import ProjectError
from texsys.targets import Artifact, BuildContext

HOOK_PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class HookDeclaration:
  id: str
  target: str
  phase: str
  command: tuple[str, ...]
  inputs: tuple[str, ...]
  outputs: tuple[str, ...]
  timeout_seconds: float
  tool_version: str
  closure: str
  max_output_bytes: int
  environment: tuple[str, ...]
  declaration_source: str
  order: int


@dataclass(frozen=True)
class HookRun:
  declaration: HookDeclaration
  status: str
  diagnostics: tuple[dict[str, Any], ...]
  open_items: tuple[dict[str, Any], ...]
  artifacts: tuple[Artifact, ...]
  evidence: dict[str, Any]

  @property
  def affects_target(self) -> bool:
    return self.declaration.closure == "authoritative"


class HookProtocolError(ValueError):
  pass


def _strings(value: Any, label: str, *, nonempty: bool = False) -> tuple[str, ...]:
  if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
    raise ProjectError(f"{label} must be an array of non-empty strings.")
  if nonempty and not value:
    raise ProjectError(f"{label} must not be empty.")
  return tuple(value)


def load_hooks(manifest: dict[str, Any], target: str) -> tuple[HookDeclaration, ...]:
  """Load only hooks bound to a selected target, preserving declaration order."""

  build = manifest.get("build", {})
  raw_hooks = build.get("hooks", []) if isinstance(build, dict) else []
  if not isinstance(raw_hooks, list):
    raise ProjectError("build.hooks must be an array of tables.")
  declarations: list[HookDeclaration] = []
  identifiers: set[str] = set()
  for index, raw in enumerate(raw_hooks):
    source = f"texsys.toml:build.hooks[{index}]"
    if not isinstance(raw, dict):
      raise ProjectError(f"{source} must be a table.")
    if raw.get("target") != target:
      continue
    required = {"id", "target", "phase", "command", "inputs", "outputs", "timeout_seconds", "tool_version"}
    allowed = required | {"closure", "max_output_bytes", "environment"}
    if not required <= set(raw) or set(raw) - allowed:
      raise ProjectError(f"{source} has missing or unknown fields.")
    identifier = raw["id"]
    if not isinstance(identifier, str) or not identifier or identifier in identifiers:
      raise ProjectError(f"{source} has an empty or duplicate hook id.")
    identifiers.add(identifier)
    phase = raw["phase"]
    if phase not in ("pre", "post"):
      raise ProjectError(f"{source}.phase must be pre or post.")
    closure = raw.get("closure", "authoritative")
    if closure not in ("authoritative", "diagnostic"):
      raise ProjectError(f"{source}.closure must be authoritative or diagnostic.")
    timeout = raw["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
      raise ProjectError(f"{source}.timeout_seconds must be positive.")
    limit = raw.get("max_output_bytes", 1_000_000)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
      raise ProjectError(f"{source}.max_output_bytes must be a positive integer.")
    if not isinstance(raw["tool_version"], str) or not raw["tool_version"]:
      raise ProjectError(f"{source}.tool_version must be a non-empty locked version string.")
    declarations.append(
      HookDeclaration(
        id=identifier,
        target=target,
        phase=phase,
        command=_strings(raw["command"], f"{source}.command", nonempty=True),
        inputs=_strings(raw["inputs"], f"{source}.inputs"),
        outputs=_strings(raw["outputs"], f"{source}.outputs"),
        timeout_seconds=float(timeout),
        tool_version=raw["tool_version"],
        closure=closure,
        max_output_bytes=limit,
        environment=_strings(raw.get("environment", []), f"{source}.environment"),
        declaration_source=source,
        order=index,
      )
    )
  return tuple(declarations)


def _error_run(declaration: HookDeclaration, message: str) -> HookRun:
  diagnostic = {"rule": "H001", "status": "error", "message": message}
  evidence = _evidence(declaration, "error", [diagnostic], [], [], [])
  return HookRun(declaration, "error", (diagnostic,), (), (), evidence)


def _evidence(
  declaration: HookDeclaration,
  status: str,
  diagnostics: list[dict[str, Any]],
  open_items: list[dict[str, Any]],
  inputs: list[dict[str, Any]],
  outputs: list[dict[str, Any]],
) -> dict[str, Any]:
  command = json.dumps(declaration.command, ensure_ascii=False, separators=(",", ":"))
  return {
    "id": declaration.id,
    "phase": declaration.phase,
    "order": declaration.order,
    "declaration_source": declaration.declaration_source,
    "command": list(declaration.command),
    "command_digest": f"sha256:{hashlib.sha256(command.encode('utf-8')).hexdigest()}",
    "tool_version": declaration.tool_version,
    "protocol_version": HOOK_PROTOCOL_VERSION,
    "closure": declaration.closure,
    "status": status,
    "diagnostics": diagnostics,
    "open_items": open_items,
    "inputs": inputs,
    "outputs": outputs,
    "target_status_impact": "required" if declaration.closure == "authoritative" else "diagnostic-only",
  }


def _authority_snapshot(paths: Iterable[Path]) -> dict[str, str]:
  result: dict[str, str] = {}
  for path in paths:
    if path.is_file():
      result[path.as_posix()] = file_digest(path)
    elif path.is_dir():
      for item in sorted(path.rglob("*")):
        if item.is_file() and item.name != ".gitkeep":
          result[item.as_posix()] = file_digest(item)
  return result


def _terminate(process: subprocess.Popen) -> None:
  if process.poll() is not None:
    return
  if os.name == "posix":
    try:
      os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
      pass
  else:
    process.kill()
  process.wait()


def _read_result(path: Path) -> dict[str, Any]:
  try:
    result = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise HookProtocolError(f"Hook did not return a valid structured result: {error}") from error
  if not isinstance(result, dict):
    raise HookProtocolError("Hook result must be a JSON object.")
  required = {"status", "diagnostics", "outputs", "open_items"}
  if not required <= set(result) or set(result) - (required | {"details"}):
    raise HookProtocolError("Hook result has missing or unknown fields.")
  if result["status"] not in ("pass", "fail", "indeterminate", "error"):
    raise HookProtocolError("Hook result has an invalid status.")
  for name in ("diagnostics", "open_items"):
    if not isinstance(result[name], list) or not all(isinstance(item, dict) for item in result[name]):
      raise HookProtocolError(f"Hook result {name} must be an array of objects.")
  _strings(result["outputs"], "Hook result outputs")
  return result


def run_hook(
  declaration: HookDeclaration,
  context: BuildContext,
  artifacts: list[dict[str, Any]],
  authority_paths: Iterable[Path],
  *,
  public_build: Path,
) -> HookRun:
  """Execute one trusted project hook; this is reproducibility control, not a sandbox."""

  root = context.root

  def staged_path(name):
    public = artifact_path(root, public_build, name)
    return context.build_path / public.relative_to(public_build)

  try:
    available_artifacts = {item["id"]: item for item in artifacts}
    available_inputs = {item["path"]: item for item in context.inputs}
    resolved_inputs: list[dict[str, Any]] = []
    context_inputs: list[dict[str, Any]] = []
    for identifier in declaration.inputs:
      if identifier.startswith("artifact:"):
        artifact_id = identifier.removeprefix("artifact:")
        if declaration.phase != "post" or artifact_id not in available_artifacts:
          raise HookProtocolError(f"Hook input is not an available target artifact: {identifier}")
        record = available_artifacts[artifact_id]
        path = record["path"]
        context_inputs.append(
          {"id": artifact_id, "kind": "artifact", "path": str(staged_path(path)), "digest": record["digest"]}
        )
      elif identifier.startswith("input:"):
        path = identifier.removeprefix("input:")
        if path not in available_inputs:
          raise HookProtocolError(f"Hook input is not a resolved project input: {identifier}")
        record = available_inputs[path]
        context_inputs.append(
          {"id": path, "kind": "input", "path": str(root / path), "digest": record["digest"]}
        )
      else:
        raise HookProtocolError(f"Unsupported hook input: {identifier}")
      resolved_inputs.append({"id": identifier, "path": path, "digest": record["digest"]})

    artifact_paths = {item["path"] for item in artifacts}
    staged_outputs = []
    for name in declaration.outputs:
      output = Path(name)
      if output.is_absolute() or ".." in output.parts:
        raise HookProtocolError(f"Hook output must be a project-relative safe path: {name}")
      if declaration.phase == "pre":
        destination = root / output
        destination.relative_to(context.scratch_path)
      else:
        destination = staged_path(name)
      if destination.resolve() != destination:
        raise HookProtocolError(f"Hook output cannot use symlinks: {name}")
      if name in artifact_paths:
        raise HookProtocolError(f"Hook output cannot overwrite a target artifact: {name}")
      if destination.exists():
        if declaration.phase == "post":
          raise HookProtocolError(f"Hook output cannot overwrite a staged file: {name}")
        destination.unlink()
      destination.parent.mkdir(parents=True, exist_ok=True)
      staged_outputs.append(destination)

    scratch = context.scratch_path / "hooks" / declaration.id.replace(":", "-").replace("/", "-")
    if scratch.exists():
      import shutil

      shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    (scratch / "tmp").mkdir()
    context_path = scratch / "context.json"
    result_path = scratch / "result.json"
    stdout_path = scratch / "stdout.log"
    stderr_path = scratch / "stderr.log"
    write_json(
      context_path,
      {
        "schema_version": 1,
        "protocol_version": HOOK_PROTOCOL_VERSION,
        "project_root": str(root),
        "target": context.target,
        "profile": context.profile,
        "scope": dict(context.scope),
        "hook": {"id": declaration.id, "phase": declaration.phase},
        "inputs": context_inputs,
        "artifacts": [
          {
            "id": item["id"],
            "path": str(staged_path(item["path"])),
            "digest": item["digest"],
            "kind": item["kind"],
          }
          for item in artifacts
        ],
        "outputs": [str(path) for path in staged_outputs],
        "scratch": str(scratch),
      },
    )

    environment = {
      "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
      "LANG": "C.UTF-8",
      "LC_ALL": "C.UTF-8",
      "TZ": "UTC",
      "PYTHONUTF8": "1",
      "PYTHONHASHSEED": "0",
      "TMPDIR": str(scratch / "tmp"),
      "TEXSYS_HOOK_CONTEXT": str(context_path),
      "TEXSYS_HOOK_RESULT": str(result_path),
    }
    for name in declaration.environment:
      if name in os.environ:
        environment[name] = os.environ[name]

    before_authority = _authority_snapshot(authority_paths)
    before_artifacts = {item["path"]: item["digest"] for item in artifacts}
    timed_out = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
      try:
        process = subprocess.Popen(
          list(declaration.command),
          cwd=root,
          env=environment,
          stdin=subprocess.DEVNULL,
          stdout=stdout,
          stderr=stderr,
          start_new_session=os.name == "posix",
        )
      except OSError as error:
        raise HookProtocolError(f"Hook command could not start: {error}") from error
      try:
        process.wait(timeout=declaration.timeout_seconds)
      except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(process)

    if _authority_snapshot(authority_paths) != before_authority:
      raise HookProtocolError("Hook modified authoritative project inputs or dependency locks.")
    for item in artifacts:
      path = staged_path(item["path"])
      if not path.is_file() or file_digest(path) != before_artifacts[item["path"]]:
        raise HookProtocolError("Post hook modified a declared target artifact.")
    if stdout_path.stat().st_size > declaration.max_output_bytes or stderr_path.stat().st_size > declaration.max_output_bytes:
      raise HookProtocolError("Hook stdout or stderr exceeded the declared output limit.")
    if timed_out:
      raise HookProtocolError("Hook exceeded its declared timeout.")

    result = _read_result(result_path)
    staged_names = tuple(path.relative_to(root).as_posix() for path in staged_outputs)
    if tuple(result["outputs"]) not in (declaration.outputs, staged_names):
      raise HookProtocolError("Hook result outputs must exactly match declared outputs in declaration order.")
    output_evidence = []
    output_artifacts = []
    for index, (name, path) in enumerate(zip(declaration.outputs, staged_outputs)):
      if path.resolve() != path or not path.is_file():
        raise HookProtocolError(f"Hook did not create declared output: {name}")
      digest = file_digest(path)
      output_evidence.append({"path": name, "digest": digest})
      if declaration.phase == "post":
        kind = "authoritative" if declaration.closure == "authoritative" else "diagnostic"
        output_artifacts.append(Artifact(f"hook.{declaration.id}.output-{index + 1}", name, kind))

    diagnostics = list(result["diagnostics"])
    open_items = list(result["open_items"])
    evidence = _evidence(
      declaration, result["status"], diagnostics, open_items, resolved_inputs, output_evidence
    )
    return HookRun(
      declaration,
      result["status"],
      tuple(diagnostics),
      tuple(open_items),
      tuple(output_artifacts),
      evidence,
    )
  except (HookProtocolError, OSError, ProjectError, ValueError) as error:
    return _error_run(declaration, str(error))
