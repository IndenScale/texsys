"""Versioned lumped HVAC physics, scenarios and coupled dynamic evaluation."""

from pathlib import Path

from texsys.evidence import file_digest, write_json
from texsys.execution import ExecutionResult
from texsys.formal import Diagnostic, _load_json
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys.targets import Artifact, BuildContext, TargetDefinition, TargetResult

from .domain import PhysicsInputError, PhysicsUnresolved, compile_model
from .scenario import load_scenario
from .thermal import ThermalNetwork, TransientFailure
from .hydraulics import HydraulicFailure


def has_physics(root: Path) -> bool:
  project = inspect_project(root)
  if project is None:
    return False
  path = root / project.source / "system.sdl.json"
  if not path.is_file():
    return False
  try:
    sdl = _load_json(path.read_text(encoding="utf-8"))
    reference = sdl["components"][sdl["root"]].get("behavior", {}).get("reference", "")
    return isinstance(reference, str) and reference.endswith(".physics.json")
  except (KeyError, TypeError, ValueError):
    return False  # Core SDL validation owns malformed documents.


class HvacDynamicsPackage(TexsysPackage):
  name = "hvac-dynamics"
  version = "0.1.0"

  def lint(self, root):
    if not has_physics(root):
      return ()
    try:
      compile_model(root)
    except PhysicsUnresolved:
      return ()  # Legal design intermediate state; executable targets report indeterminate.
    except (PhysicsInputError, OSError, KeyError, TypeError, ValueError) as error:
      return (Diagnostic("H_PHYSICS", "fail", "src/system.sdl.json", str(error)),)
    return ()

  def targets(self):
    return (TargetDefinition("hvac:physical-model", self.name, self.version, self.build_model,
                             profiles=("engineering",), default_profile="engineering"),)

  def build_model(self, context: BuildContext):
    if not has_physics(context.root):
      return TargetResult("indeterminate", open_items=({"id": "missing-physical-model", "impact": "Requires an explicit water-network physics contract."},))
    try:
      model = compile_model(context.root)
    except PhysicsUnresolved as error:
      return TargetResult("indeterminate", open_items=({"id": "unresolved-physical-model", "impact": str(error)},))
    output = context.build_path / "hvac/behavior/model.json"
    write_json(output, model.as_dict())
    return TargetResult(artifacts=(Artifact("physical-model", output.relative_to(context.root).as_posix()),),
                        details={"model_digest": model.snapshot_digest, "branches": len(model.branches),
                                 "components": len(model.components), "backend_model": ThermalNetwork.version})

  def evaluate(self, root, scenario, backend):
    if not has_physics(root):
      return None
    if backend not in {"scipy", "openmodelica"}:
      raise PhysicsInputError(f"Unknown physical backend: {backend}")
    info = inspect_project(root)
    directory = root / info.build / "tests/scenarios" / scenario.stem / backend
    artifacts = []
    try:
      model = compile_model(root)
      experiment = load_scenario(scenario, model)
      directory = root / info.build / "tests/scenarios" / experiment.identity / backend
      snapshot = directory / "model.json"
      write_json(snapshot, model.as_dict())
      artifacts.append(snapshot)
      if backend == "scipy":
        result = ThermalNetwork(model, experiment).run()
      elif backend == "openmodelica":
        from .modelica_backend import run_modelica
        result = run_modelica(model, experiment, directory)
      else:
        raise PhysicsInputError(f"Unknown physical backend: {backend}")
      result["source_digests"] = [{"path": p, "digest": d} for p, d in model.source_digests]
      result["scenario_source"] = {"path": scenario.relative_to(root).as_posix(), "digest": file_digest(scenario)}
    except PhysicsUnresolved as error:
      result = {"schema_version": 1, "status": "indeterminate", "backend": backend,
                "diagnostics": [{"rule": "H_UNRESOLVED", "status": "indeterminate", "message": str(error)}]}
    except (PhysicsInputError, TransientFailure, HydraulicFailure, OSError, KeyError, TypeError, ValueError, ImportError) as error:
      result = {"schema_version": 1, "status": "error", "backend": backend,
                "diagnostics": [{"rule": "H_EVALUATE", "status": "error", "message": str(error)}],
                "scenario_source": {"path": scenario.relative_to(root).as_posix(), "digest": file_digest(scenario)}}
    report = directory / "results.json"
    if backend == "openmodelica":
      artifacts.extend(directory / name for name in ("TexsysWaterNetwork.mo", "run.mos", "openmodelica.log", "TexsysWaterNetwork_res.csv") if (directory / name).is_file())
    write_json(report, result)
    return ExecutionResult(result["status"], report, (*artifacts, report))


package = HvacDynamicsPackage()
