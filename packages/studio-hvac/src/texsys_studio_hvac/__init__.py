"""HVAC Studio contribution, using existing engineering APIs and shared graphics."""

import copy
import json
from pathlib import Path
import tempfile

from texsys.api import build, evaluate, lint
from texsys.evidence import file_digest, write_json
from texsys.formal import _check_schema, _check_semantics, _load_json
from texsys.project import ProjectError, inspect_project
from texsys.studio import StudioPlugin
from texsys.studio.runtime import Conflict, input_records, snapshot_project
from texsys_hvac_dynamics.domain import compile_model, PhysicsUnresolved

MANIFEST = {"id": "texsys:hvac", "name": "Tex Studio HVAC", "discipline": "暖通", "version": "0.1.0", "api_version": 1,
            "capabilities": ["system-view", "equipment-table", "parameter-edit", "symbols", "source", "check", "build", "simulate", "results"]}


def read_json(file):
  return _load_json(file.read_text(encoding="utf-8"))


class HvacWorkbench(StudioPlugin):
  manifest = MANIFEST
  frontend = Path(__file__).parent / "assets" / "workbench.tsx"

  def state(self, root, artifacts):
    project = inspect_project(root)
    source_file = root / project.source / "system.sdl.json"
    source = read_json(source_file) if source_file.is_file() else None
    # Current run outputs win; historical outputs remain inspectable with a stale label.
    locations = sorted(artifacts, key=lambda item: not item["current"])
    locations.append({"root": root, "current": None, "id": None})
    diagrams, runs, reports, exports = {}, {}, [], []
    report_kinds = set()
    for location in locations:
      origin = location["root"]
      info = inspect_project(origin)
      if info is None:
        continue
      directory = origin / info.build
      for view in ("system", "schematic"):
        path = directory / "hvac" / view / "system.json"
        if view not in diagrams and path.is_file():
          document = read_json(path)
          current = location["current"] is True
          diagrams[view] = {"document": document, "current": current}
          for extension in ("svg", "drawio", "json"):
            file = path.with_suffix("." + extension)
            if file.is_file():
              exports.append({"name": f"{'原理图' if view == 'schematic' else '系统图'}.{extension}",
                              "path": file.relative_to(root).as_posix(), "current": current})
      for file in sorted((directory / "tests/scenarios").glob("*/*/results.json")):
        result = read_json(file)
        if "scenario" not in result or "time_s" not in result:
          continue
        key = f"{result['scenario']}:{file.parent.name}"
        if key in runs:
          continue
        # Imported CLI outputs have no Studio project revision; keep them visible
        # as historical until a run verifies this complete project input set.
        current = location["current"] is True
        runs[key] = {"result": result, "current": current, "path": file.relative_to(root).as_posix()}
      for relative in ("studio-check.json", "build.json"):
        file = directory / relative
        if relative not in report_kinds and file.is_file():
          report_kinds.add(relative)
          reports.append({"kind": relative, "data": read_json(file), "current": location["current"] is True,
                          "path": file.relative_to(root).as_posix()})
    scenarios = []
    for file in sorted((root / project.assets / "scenarios").glob("*.json")):
      value = read_json(file)
      scenarios.append({"id": value.get("id", file.stem), "path": file.relative_to(root).as_posix(),
                        "duration_s": value.get("duration_s"), "events": len({p[0] for signal in value.get("signals", {}).values() for p in signal.get("points", []) if p[0] > 0})})
    return {"source": source, "source_path": source_file.relative_to(root).as_posix(),
            "diagrams": diagrams, "runs": list(runs.values()), "scenarios": scenarios, "reports": reports, "exports": exports}

  def edit(self, root, operation):
    if not isinstance(operation, dict) or operation.get("kind") not in {"set-parameter", "reset-parameter"}:
      raise ProjectError("Supported edits: instance parameter override and restore inheritance.")
    info = inspect_project(root)
    file = root / info.source / "system.sdl.json"
    before_digest = file_digest(file)
    data = read_json(file)
    instance_name, parameter = operation.get("instance"), operation.get("parameter")
    component = data["components"][data["root"]]
    instance = component.get("instances", {}).get(instance_name)
    if instance is None:
      raise ProjectError("Unknown instance.")
    definition = data["components"][instance["component"]]
    if parameter not in definition.get("behavior", {}).get("parameters", {}):
      raise ProjectError("Parameter is not declared by the Component.")
    original = copy.deepcopy(instance.get("parameters", {}).get(parameter))
    if operation["kind"] == "reset-parameter":
      instance.get("parameters", {}).pop(parameter, None)
      if not instance.get("parameters"):
        instance.pop("parameters", None)
    else:
      instance.setdefault("parameters", {})[parameter] = operation.get("binding")
    diagnostics = _check_schema(data, file.name)
    if not diagnostics:
      diagnostics = _check_semantics(data, file.name)
    if diagnostics:
      raise ProjectError("; ".join(item.message for item in diagnostics))
    # Validate domain constraints on a copy, so rejected changes never touch authority.
    with tempfile.TemporaryDirectory(prefix="tex-studio-edit-") as directory:
      candidate = Path(directory) / "project"
      snapshot_project(root, candidate, input_records(root))
      write_json(candidate / info.source / "system.sdl.json", data)
      try:
        compile_model(candidate)
      except PhysicsUnresolved:
        pass  # Unknown/missing parameters are valid, visibly incomplete design states.
    if file_digest(file) != before_digest:
      raise Conflict("Source changed during validation; no data was saved.")
    write_json(file, data)
    undo = {"instance": instance_name, "parameter": parameter,
            "kind": "reset-parameter" if original is None else "set-parameter"}
    if original is not None:
      undo["binding"] = original
    return {"undo": undo, "file": file.relative_to(root).as_posix()}

  def validate_action(self, root, action, options):
    if action not in {"check", "build", "simulate"}:
      raise ProjectError("Unsupported HVAC action.")
    if set(options) - {"scenario", "backend"}:
      raise ProjectError("Unsupported task options.")
    if options.get("backend", "scipy") not in {"scipy", "openmodelica"}:
      raise ProjectError("Unsupported physics backend.")
    if action == "simulate":
      available = {item["id"] for item in self.state(root, [])["scenarios"]}
      if options.get("scenario") != "all" and options.get("scenario") not in available:
        raise ProjectError("Choose an existing scenario or all scenarios.")

  def execute(self, root, action, options):
    project = inspect_project(root)
    if action == "check":
      result = lint(root).as_dict()
      write_json(root / project.build / "studio-check.json", result)
      return {"status": result["status"], "diagnostics": result["diagnostics"]}
    result_paths, conclusions = [], []
    if action == "simulate":
      scenarios = sorted((root / project.assets / "scenarios").glob("*.json"))
      for file in scenarios:
        name = read_json(file)["id"]
        if options.get("scenario") not in {"all", name}:
          continue
        print(f"Solving {name} with {options.get('backend', 'scipy')}…", flush=True)
        result = evaluate(root, file.relative_to(root), options.get("backend", "scipy"))
        conclusions.append(result.status)
        if result.status in {"pass", "fail"}:
          result_paths.append(result.report.relative_to(root).as_posix())
        print(f"{name}: {result.status}", flush=True)
    for view in ("system", "schematic"):
      print(f"Building {view} drawing…", flush=True)
      scope = {"view": view}
      if result_paths:
        scope["results"] = result_paths
      result = build(root, targets=("hvac:system-schematic",), scope=scope)
      conclusions.append(result.status)
    status = next((state for state in ("error", "fail", "indeterminate") if state in conclusions), "pass")
    return {"status": status, "results": result_paths}


plugin = HvacWorkbench()
