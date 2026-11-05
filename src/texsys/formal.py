"""Static checks and deterministic formatting for the SDL JSON exchange subset."""

from dataclasses import dataclass
import difflib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, NoReturn

from texsys.project import ProjectError, inspect_project

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_DIRECTIONS = {"in", "out", "inout"}
VALUE_STATES = {"known", "assumed", "unknown", "missing", "not-applicable", "failed"}
_ALLOWED_DIRECTION_PAIRS = {
  ("out", "in"),
  ("out", "inout"),
  ("inout", "in"),
  ("inout", "out"),
  ("inout", "inout"),
}


@dataclass(frozen=True)
class Diagnostic:
  rule: str
  status: str
  file: str
  message: str
  object: str | None = None
  line: int | None = None
  column: int | None = None

  def as_dict(self) -> dict[str, Any]:
    result: dict[str, Any] = {
      "rule": self.rule,
      "status": self.status,
      "file": self.file,
      "object": self.object,
      "message": self.message,
    }
    if self.line is not None:
      result["line"] = self.line
    if self.column is not None:
      result["column"] = self.column
    return result


@dataclass(frozen=True)
class LintResult:
  status: str
  files: int
  diagnostics: tuple[Diagnostic, ...]

  def as_dict(self) -> dict[str, Any]:
    return {
      "status": self.status,
      "files": self.files,
      "diagnostics": [item.as_dict() for item in self.diagnostics],
    }


@dataclass(frozen=True)
class FormatResult:
  changed: tuple[str, ...]
  diagnostics: tuple[Diagnostic, ...]
  diff: str


class DuplicateKeyError(ValueError):
  pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
  result: dict[str, Any] = {}
  for key, value in pairs:
    if key in result:
      raise DuplicateKeyError(f"Duplicate JSON key: {key}")
    result[key] = value
  return result


def _reject_constant(value: str) -> NoReturn:
  raise ValueError(f"Non-standard JSON number: {value}")


def _load_json(text: str) -> Any:
  return json.loads(
    text,
    object_pairs_hook=_object_without_duplicates,
    parse_constant=_reject_constant,
  )


def _source_files(root: Path, source: str) -> tuple[Path, ...]:
  return tuple(sorted((root / source).rglob("*.sdl.json")))


def _relative(root: Path, path: Path) -> str:
  return path.relative_to(root).as_posix()


def _valid_identifier(value: object) -> bool:
  return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _check_schema(data: Any, file: str) -> tuple[Diagnostic, ...]:
  diagnostics: list[Diagnostic] = []

  def fail(message: str, object_path: str | None = None) -> None:
    diagnostics.append(Diagnostic("S002", "fail", file, message, object_path))

  if not isinstance(data, dict):
    fail("SDL document must be a JSON object.")
    return tuple(diagnostics)
  unknown = set(data) - {"schema_version", "root", "components"}
  if unknown:
    fail(f"Unknown document fields: {', '.join(sorted(unknown))}.")
  if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
    fail("schema_version must be 1.", "schema_version")
  if not _valid_identifier(data.get("root")):
    fail("root must be a valid component identifier.", "root")
  components = data.get("components")
  if not isinstance(components, dict):
    fail("components must be an object.", "components")
    return tuple(diagnostics)

  for component_name, component in components.items():
    component_path = f"components.{component_name}"
    if not _valid_identifier(component_name):
      fail("Component name is not a valid identifier.", component_path)
    if not isinstance(component, dict):
      fail("Component must be an object.", component_path)
      continue
    unknown = set(component) - {"ports", "instances", "connections", "behavior"}
    if unknown:
      fail(f"Unknown component fields: {', '.join(sorted(unknown))}.", component_path)

    if "behavior" in component:
      behavior = component["behavior"]
      behavior_path = f"{component_path}.behavior"
      if not isinstance(behavior, dict):
        fail("behavior must be an object.", behavior_path)
      else:
        if set(behavior) != {"reference", "state", "source", "parameters"}:
          fail("behavior requires exactly reference, state, source and parameters.", behavior_path)
        for name in ("reference", "source"):
          if not isinstance(behavior.get(name), str) or not behavior[name].strip():
            fail(f"behavior.{name} must be a non-empty string.", behavior_path)
        if not isinstance(behavior.get("state"), str) or behavior["state"] not in VALUE_STATES:
          fail("Invalid behavior state.", behavior_path)
        _check_parameters(behavior.get("parameters"), behavior_path, fail)

    ports = component.get("ports", {})
    if not isinstance(ports, dict):
      fail("ports must be an object.", f"{component_path}.ports")
    else:
      for port_name, port in ports.items():
        port_path = f"{component_path}.ports.{port_name}"
        if not _valid_identifier(port_name):
          fail("Port name is not a valid identifier.", port_path)
        if not isinstance(port, dict):
          fail("Port must be an object.", port_path)
          continue
        unknown = set(port) - {"kind", "direction", "max_connections"}
        if unknown:
          fail(f"Unknown port fields: {', '.join(sorted(unknown))}.", port_path)
        if not isinstance(port.get("kind"), str) or not port.get("kind"):
          fail("Port kind must be a non-empty string.", f"{port_path}.kind")
        if not isinstance(port.get("direction"), str) or port["direction"] not in _DIRECTIONS:
          fail("Port direction must be in, out or inout.", f"{port_path}.direction")
        maximum = port.get("max_connections", 1)
        if type(maximum) is not int or maximum < 1:
          fail("max_connections must be a positive integer.", f"{port_path}.max_connections")

    instances = component.get("instances", {})
    if not isinstance(instances, dict):
      fail("instances must be an object.", f"{component_path}.instances")
    else:
      for instance_name, instance in instances.items():
        instance_path = f"{component_path}.instances.{instance_name}"
        if not _valid_identifier(instance_name):
          fail("Instance name is not a valid identifier.", instance_path)
        if not isinstance(instance, dict):
          fail("Instance must be an object.", instance_path)
          continue
        unknown = set(instance) - {"component", "parameters"}
        if unknown:
          fail(f"Unknown instance fields: {', '.join(sorted(unknown))}.", instance_path)
        if not _valid_identifier(instance.get("component")):
          fail("Instance component must be a valid identifier.", f"{instance_path}.component")
        if "parameters" in instance:
          _check_parameters(instance["parameters"], instance_path, fail)

    connections = component.get("connections", [])
    if not isinstance(connections, list):
      fail("connections must be an array.", f"{component_path}.connections")
    else:
      for index, connection in enumerate(connections):
        connection_path = f"{component_path}.connections[{index}]"
        if not isinstance(connection, dict):
          fail("Connection must be an object.", connection_path)
          continue
        unknown = set(connection) - {"from", "to"}
        if unknown:
          fail(f"Unknown connection fields: {', '.join(sorted(unknown))}.", connection_path)
        for endpoint in ("from", "to"):
          value = connection.get(endpoint)
          if not isinstance(value, str) or len(value.split(".")) != 2:
            fail("Endpoint must have the form instance.port.", f"{connection_path}.{endpoint}")
  return tuple(diagnostics)


def _check_parameters(parameters: Any, path: str, fail) -> None:
  """Validate explicit parameter bindings; unresolved states never get defaults."""
  if not isinstance(parameters, dict):
    fail("parameters must be an object.", path)
    return
  for name, value in parameters.items():
    parameter_path = f"{path}.parameters.{name}"
    if not _valid_identifier(name) or not isinstance(value, dict):
      fail("Parameter requires a valid name and an object binding.", parameter_path)
      continue
    if set(value) - {"value", "unit", "state", "source"}:
      fail("Unknown parameter binding fields.", parameter_path)
    state = value.get("state")
    if not isinstance(state, str) or state not in VALUE_STATES:
      fail("Invalid parameter state.", parameter_path)
      continue
    for field in ("unit", "source"):
      if not isinstance(value.get(field), str) or not value[field].strip():
        fail(f"Parameter {field} must be a non-empty string.", parameter_path)
    if state in {"known", "assumed"}:
      number = value.get("value")
      if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
        fail("Known/assumed parameter value must be a finite number.", parameter_path)
    elif "value" in value:
      fail("Unresolved/not-applicable parameter must omit value.", parameter_path)


def _check_semantics(data: Any, file: str) -> tuple[Diagnostic, ...]:
  if not isinstance(data, dict) or not isinstance(data.get("components"), dict):
    return ()
  diagnostics: list[Diagnostic] = []
  components: dict[str, Any] = data["components"]
  root = data.get("root")
  if isinstance(root, str) and root not in components:
    diagnostics.append(
      Diagnostic("L001", "fail", file, f"Root component does not exist: {root}", root)
    )

  for component_name, component in components.items():
    if not isinstance(component, dict):
      continue
    ports = component.get("ports", {})
    instances = component.get("instances", {})
    connections = component.get("connections", [])
    if not isinstance(ports, dict) or not isinstance(instances, dict):
      continue
    for instance_name, instance in instances.items():
      object_path = f"{component_name}.{instance_name}"
      if not isinstance(instance, dict):
        continue
      definition = instance.get("component")
      if isinstance(definition, str) and definition not in components:
        diagnostics.append(
          Diagnostic(
            "L002",
            "fail",
            file,
            f"Instance references undefined Component: {definition}",
            object_path,
          )
        )
      elif isinstance(definition, str) and isinstance(components[definition], dict):
        declared = components[definition].get("behavior", {}).get("parameters", {})
        for name in instance.get("parameters", {}):
          if name not in declared:
            diagnostics.append(
              Diagnostic("L009", "fail", file, f"Undeclared behavior parameter: {name}", object_path)
            )
    if not isinstance(connections, list):
      continue

    connection_counts: dict[tuple[str, str], int] = {}
    for index, connection in enumerate(connections):
      if not isinstance(connection, dict):
        continue
      connection_path = f"{component_name}.connections[{index}]"
      endpoints: dict[str, tuple[str, str, dict[str, Any]]] = {}
      for direction in ("from", "to"):
        reference = connection.get(direction)
        if not isinstance(reference, str) or len(reference.split(".")) != 2:
          continue
        instance_name, port_name = reference.split(".")
        instance = instances.get(instance_name)
        if not isinstance(instance, dict):
          diagnostics.append(
            Diagnostic(
              "L003",
              "fail",
              file,
              f"Endpoint references undefined Instance: {instance_name}",
              f"{connection_path}.{direction}",
            )
          )
          continue
        definition_name = instance.get("component")
        definition = components.get(definition_name)
        if not isinstance(definition, dict):
          continue
        definition_ports = definition.get("ports", {})
        port = definition_ports.get(port_name) if isinstance(definition_ports, dict) else None
        if not isinstance(port, dict):
          diagnostics.append(
            Diagnostic(
              "L004",
              "fail",
              file,
              f"Endpoint references undefined Port: {reference}",
              f"{connection_path}.{direction}",
            )
          )
          continue
        endpoints[direction] = (instance_name, port_name, port)
        key = (instance_name, port_name)
        connection_counts[key] = connection_counts.get(key, 0) + 1

      source = endpoints.get("from")
      target = endpoints.get("to")
      if source is None or target is None:
        continue
      if source[:2] == target[:2]:
        diagnostics.append(
          Diagnostic("L008", "fail", file, "Connection cannot join a Port to itself.", connection_path)
        )
      if source[2].get("kind") != target[2].get("kind"):
        diagnostics.append(
          Diagnostic(
            "L005",
            "fail",
            file,
            f"Port kinds differ: {source[2].get('kind')} vs {target[2].get('kind')}",
            connection_path,
          )
        )
      pair = (source[2].get("direction"), target[2].get("direction"))
      if pair not in _ALLOWED_DIRECTION_PAIRS:
        diagnostics.append(
          Diagnostic(
            "L006",
            "fail",
            file,
            f"Directions are incompatible: {pair[0]} -> {pair[1]}",
            connection_path,
          )
        )

    for (instance_name, port_name), count in sorted(connection_counts.items()):
      instance = instances[instance_name]
      definition = components.get(instance.get("component"), {})
      port = definition.get("ports", {}).get(port_name, {})
      maximum = port.get("max_connections", 1)
      if type(maximum) is int and count > maximum:
        diagnostics.append(
          Diagnostic(
            "L007",
            "fail",
            file,
            f"Port has {count} connections; maximum is {maximum}",
            f"{component_name}.{instance_name}.{port_name}",
          )
        )
  return tuple(diagnostics)


def lint_project(path: Path) -> LintResult:
  root = path.expanduser().resolve()
  diagnostics: list[Diagnostic] = []
  try:
    project = inspect_project(root)
  except ProjectError as error:
    return LintResult("fail", 0, (Diagnostic("P001", "fail", "texsys.toml", str(error)),))
  if project is None:
    return LintResult(
      "fail", 0, (Diagnostic("P001", "fail", "texsys.toml", "Project is not initialized."),)
    )

  for name in ("source", "assets", "build"):
    relative = getattr(project, name)
    target = root / relative
    if not target.is_dir():
      diagnostics.append(
        Diagnostic("P002", "fail", relative, f"Project layout directory is missing: {relative}")
      )

  files = _source_files(root, project.source) if (root / project.source).is_dir() else ()
  for source in files:
    relative = _relative(root, source)
    try:
      text = source.read_text(encoding="utf-8")
      data = _load_json(text)
    except json.JSONDecodeError as error:
      diagnostics.append(
        Diagnostic("S001", "fail", relative, error.msg, line=error.lineno, column=error.colno)
      )
      continue
    except (UnicodeError, DuplicateKeyError, ValueError) as error:
      diagnostics.append(Diagnostic("S001", "fail", relative, str(error)))
      continue
    except OSError as error:
      diagnostics.append(Diagnostic("E001", "error", relative, str(error)))
      continue
    schema_diagnostics = _check_schema(data, relative)
    diagnostics.extend(schema_diagnostics)
    if not schema_diagnostics:
      diagnostics.extend(_check_semantics(data, relative))

  from texsys.packages import installed_packages

  try:
    for package in installed_packages():
      diagnostics.extend(package.lint(root))
  except (ImportError, OSError, TypeError, ValueError) as error:
    diagnostics.append(Diagnostic("P003", "error", "texsys.toml", str(error)))

  statuses = {item.status for item in diagnostics}
  if "error" in statuses:
    status = "error"
  elif "fail" in statuses:
    status = "fail"
  elif "indeterminate" in statuses:
    status = "indeterminate"
  else:
    status = "pass"
  return LintResult(status, len(files), tuple(diagnostics))


def _canonical_json(data: Any) -> str:
  return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, content: str) -> None:
  descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
  temporary = Path(temporary_name)
  try:
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
      stream.write(content)
    os.replace(temporary, path)
  finally:
    temporary.unlink(missing_ok=True)


def format_project(path: Path, check: bool = False, diff: bool = False) -> FormatResult:
  root = path.expanduser().resolve()
  project = inspect_project(root)
  if project is None:
    raise ProjectError(f"Project is not initialized: {root}")
  source_root = root / project.source
  if not source_root.is_dir():
    raise ProjectError(f"Source layout is not a directory: {source_root}")

  changed: list[str] = []
  diagnostics: list[Diagnostic] = []
  differences: list[str] = []
  for source in _source_files(root, project.source):
    relative = _relative(root, source)
    try:
      original = source.read_text(encoding="utf-8")
      formatted = _canonical_json(_load_json(original))
    except json.JSONDecodeError as error:
      diagnostics.append(
        Diagnostic("S001", "fail", relative, error.msg, line=error.lineno, column=error.colno)
      )
      continue
    except (UnicodeError, DuplicateKeyError, ValueError) as error:
      diagnostics.append(Diagnostic("S001", "fail", relative, str(error)))
      continue
    if original == formatted:
      continue
    changed.append(relative)
    if diff:
      differences.extend(
        difflib.unified_diff(
          original.splitlines(keepends=True),
          formatted.splitlines(keepends=True),
          fromfile=f"a/{relative}",
          tofile=f"b/{relative}",
        )
      )
    if not check and not diff:
      _atomic_write(source, formatted)

  from texsys.packages import installed_packages

  try:
    for package in installed_packages():
      result = package.format(root, check=check, diff=diff)
      if result is None:
        continue
      changed.extend(result.changed)
      diagnostics.extend(result.diagnostics)
      differences.append(result.diff)
  except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
    diagnostics.append(Diagnostic("P003", "error", "texsys.toml", str(error)))
  return FormatResult(tuple(changed), tuple(diagnostics), "".join(differences))
