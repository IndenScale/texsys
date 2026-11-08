"""Plan and apply structured changes to one authoritative SDL source file."""

import difflib
from pathlib import Path, PurePosixPath
from typing import Any

from texsys.evidence import canonical_digest
from texsys.execution import _input_digests
from texsys.formal import (
  Diagnostic,
  _atomic_write,
  _canonical_json,
  _check_schema,
  _check_semantics,
  _load_json,
  _valid_identifier,
)
from texsys.project import ProjectError, inspect_project, project_write_lock

_OPERATIONS = {
  "create-design": {"id"},
  "declare-component": {"id", "definition"},
  "add-instance": {"id", "component"},
  "add-connection": {"id", "from", "to"},
  "name-connection": {"id", "from", "to"},
  "declare-net": {"id", "kind", "connections"},
  "set-net-members": {"id", "connections"},
}


def _inputs(path: Path, source: str) -> tuple[Path, Path, str]:
  root = path.expanduser().resolve()
  project = inspect_project(root)
  if project is None:
    raise ProjectError("Initialize the project before editing a design.")
  relative = PurePosixPath(source) if isinstance(source, str) else PurePosixPath()
  if (relative.is_absolute() or ".." in relative.parts or "\\" in str(relative)
      or "\0" in str(relative) or not relative.name.endswith(".sdl.json")):
    raise ProjectError("source must be a relative .sdl.json path within the project source directory.")
  source_root = root / project.source
  target = source_root / relative
  if (not target.is_relative_to(source_root) or target != target.resolve()
      or not target.parent.is_dir() or (target.exists() and not target.is_file())):
    raise ProjectError("Design path must identify a regular file in an existing source directory, without symlinks.")
  return root, target, canonical_digest(_input_digests(root, project.source, project.assets))


def read_design(path: Path, source: str = "system.sdl.json") -> dict[str, Any]:
  """Read the SDL document and the revision required for a batch edit."""
  root, target, revision = _inputs(path, source)
  try:
    text = target.read_text(encoding="utf-8") if target.exists() else None
    document = _load_json(text) if text is not None else None
  except ValueError as error:
    raise ProjectError(f"Invalid SDL JSON: {error}") from error
  if _inputs(path, source)[2] != revision:
    raise ProjectError("Design inputs changed while reading. Retry with stable inputs.")
  return {"version": 1, "source": source, "file": target.relative_to(root).as_posix(),
          "revision": revision, "document": document}


def _apply_operation(document: Any, operation: Any) -> dict:
  if not isinstance(operation, dict):
    raise ProjectError("Operation must be an object.")
  kind = operation.get("op")
  if not isinstance(kind, str) or kind not in _OPERATIONS:
    raise ProjectError("Unknown design operation.")
  if set(operation) != _OPERATIONS[kind] | {"op"} or not _valid_identifier(operation.get("id")):
    raise ProjectError(f"{kind} requires a valid id and fields: {', '.join(sorted(_OPERATIONS[kind]))}.")
  name = operation["id"]
  if kind == "create-design":
    if document is not None:
      raise ProjectError("The design source already exists.")
    return {"schema_version": 1, "root": name, "components": {name: {}}}
  if document is None:
    raise ProjectError("Start a new source with create-design.")

  components = document["components"]
  root = components.get(document["root"])
  if kind == "declare-component":
    definition = operation["definition"]
    if not isinstance(definition, dict) or set(definition) - {"ports", "behavior"}:
      raise ProjectError("A component definition accepts ports and behavior.")
    if name in components:
      raise ProjectError(f"Component already exists: {name}")
    components[name] = definition
  elif root is None:
    raise ProjectError("Declare the root component before editing its topology.")
  elif kind == "add-instance":
    instances = root.setdefault("instances", {})
    if name in instances:
      raise ProjectError(f"Instance already exists: {name}")
    instances[name] = {"component": operation["component"]}
  elif kind in {"add-connection", "name-connection"}:
    connections = root.setdefault("connections", [])
    if any(c.get("id") == name for c in connections):
      raise ProjectError(f"Connection id already exists: {name}")
    matches = [c for c in connections if (c["from"], c["to"]) == (operation["from"], operation["to"])]
    if kind == "add-connection":
      if matches:
        raise ProjectError("Connection endpoint pair already exists.")
      connections.append({field: operation[field] for field in ("id", "from", "to")})
    else:
      if len(matches) != 1 or "id" in matches[0]:
        raise ProjectError("name-connection requires one existing, unnamed endpoint pair.")
      matches[0]["id"] = name
    document["schema_version"] = 2
  else:
    nets = root.setdefault("nets", {})
    if kind == "declare-net":
      if name in nets:
        raise ProjectError(f"Net already exists: {name}")
      nets[name] = {"kind": operation["kind"], "connections": operation["connections"]}
      document["schema_version"] = 2
    else:
      if name not in nets:
        raise ProjectError(f"Unknown Net: {name}")
      nets[name]["connections"] = operation["connections"]
  return document


def _prepare(path: Path, request: dict) -> tuple[dict[str, Any], Path, str | None]:
  if (not isinstance(request, dict) or set(request) != {"version", "source", "revision", "operations"}
      or type(request["version"]) is not int or request["version"] != 1
      or not isinstance(request["revision"], str)
      or not isinstance(request["operations"], list) or not request["operations"]):
    raise ProjectError("Edit requires version 1, a source, a base revision and a non-empty operations array.")
  root, target, revision = _inputs(path, request["source"])
  relative = target.relative_to(root).as_posix()
  result = {"status": "pass", "file": relative, "revision": revision, "diagnostics": [], "diff": "", "plan": None}
  if request["revision"] != revision:
    result.update(status="conflict", diagnostics=[Diagnostic(
      "A_CONFLICT", "error", relative, "Design inputs changed. Read the current revision and replan.",
    ).as_dict()])
    return result, target, None

  before = None
  document = None
  original = None
  diagnostics: tuple[Diagnostic, ...] = ()
  try:
    if target.exists():
      before = target.read_text(encoding="utf-8")
      document = _load_json(before)
      diagnostics = _check_schema(document, relative)
      if not diagnostics:
        original = _canonical_json(document)
  except ValueError as error:
    diagnostics = (Diagnostic("S001", "fail", relative, str(error)),)

  if not diagnostics:
    for index, operation in enumerate(request["operations"]):
      try:
        document = _apply_operation(document, operation)
      except ProjectError as error:
        diagnostics = (Diagnostic("A_OPERATION", "fail", relative, str(error), f"operations[{index}]"),)
        break
  if not diagnostics:
    diagnostics = _check_schema(document, relative) or _check_semantics(document, relative)
  if diagnostics:
    result.update(status="fail", diagnostics=[item.as_dict() for item in diagnostics])
    return result, target, None

  after = _canonical_json(document)
  if before is not None and after == original:
    after = before
  diff = difflib.unified_diff((before or "").splitlines(), after.splitlines(),
                              f"a/{relative}" if before is not None else "/dev/null", f"b/{relative}", lineterm="")
  result.update(diff="\n".join(diff), plan=canonical_digest({"request": request, "file": relative, "output": after}))
  return result, target, after if after != before else None


def plan_edit(path: Path, request: dict) -> dict[str, Any]:
  """Return a diff and diagnostics without writing to the design repository."""
  return _prepare(path, request)[0]


def apply_edit(path: Path, request: dict, plan: str) -> dict[str, Any]:
  """Revalidate an exact plan against the current inputs and publish its SDL file."""
  root = path.expanduser().resolve()
  if inspect_project(root) is None:
    raise ProjectError("Initialize the project before editing a design.")
  with project_write_lock(root):
    result, target, after = _prepare(root, request)
    if result["status"] != "pass":
      return result
    if not isinstance(plan, str) or result["plan"] != plan:
      raise ProjectError("Plan digest differs. Preview the exact request before applying it.")
    file = target.relative_to(root).as_posix()
    exists = target.exists()
    current = _inputs(root, request["source"])[2]
    if current != result["revision"]:
      return {**result, "status": "conflict", "revision": current, "plan": None, "diff": "", "diagnostics": [
        Diagnostic("A_CONFLICT", "error", file, "Design inputs changed during validation. Replan before applying.").as_dict(),
      ]}
    if after is not None:
      _atomic_write(target, after, create_only=not exists)
    result.update(applied=after is not None, revision=_inputs(root, request["source"])[2])
    return result
