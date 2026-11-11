"""Backend-neutral build, behavior-test and simulation orchestration."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tomllib
from typing import Any

from texsys.dependencies import LOCK_NAME, lock_project
from texsys.evidence import file_digest, write_json
from texsys.formal import check_project
from texsys.hooks import load_hooks, run_hook
from texsys.packages import installed_packages, project_packages
from texsys.project import ProjectError, inspect_project
from texsys.solver import TestResult
from texsys.targets import BuildContext, ResolvedTarget, TargetResult, resolve_targets


@dataclass(frozen=True)
class ExecutionResult:
  status: str
  report: Path
  items: tuple[Any, ...] = ()
  targets: tuple[ResolvedTarget, ...] = ()


def _project(root: Path):
  project = inspect_project(root)
  if project is None:
    raise ProjectError(f"Project is not initialized: {root}")
  return project


def _manifest(root: Path) -> dict[str, Any]:
  try:
    with (root / "texsys.toml").open("rb") as stream:
      return tomllib.load(stream)
  except (OSError, tomllib.TOMLDecodeError) as error:
    raise ProjectError(f"Cannot read texsys.toml: {error}") from error


def _input_digests(root: Path, source: str, assets: str) -> list[dict[str, str]]:
  result: list[dict[str, str]] = []
  for name in ("texsys.toml", "texsys.lock"):
    path = root / name
    if path.is_file():
      result.append({"path": name, "digest": file_digest(path)})
  for directory in (source, assets):
    for path in sorted((root / directory).rglob("*")):
      if path.is_file() and path.name != ".gitkeep":
        result.append({"path": path.relative_to(root).as_posix(), "digest": file_digest(path)})
  return result


def _artifact_records(
  root: Path,
  build_root: Path,
  target: str,
  declared_artifacts,
  existing_ids: set[str],
) -> list[dict[str, Any]]:
  records: list[dict[str, Any]] = []
  for declared in declared_artifacts:
    if declared.id in existing_ids:
      raise ProjectError(f"Target {target} declared duplicate artifact: {declared.id}")
    existing_ids.add(declared.id)
    artifact = (root / declared.path).resolve()
    try:
      artifact.relative_to(build_root)
    except ValueError as error:
      raise ProjectError(f"Artifact is outside the build directory: {declared.path}") from error
    if not artifact.is_file():
      raise ProjectError(f"Target {target} did not create artifact: {declared.path}")
    records.append(
      {
        "target": target,
        "id": declared.id,
        "kind": declared.kind,
        "path": artifact.relative_to(root).as_posix(),
        "digest": file_digest(artifact),
        "partial": True,
      }
    )
  return records


def build_project(
  path: Path,
  targets: tuple[str, ...] = (),
  profile: str | None = None,
  scope: dict[str, Any] | None = None,
) -> ExecutionResult:
  root = path.expanduser().resolve()
  manifest_path = root / "texsys.toml"
  manifest_digest = file_digest(manifest_path) if manifest_path.is_file() else None
  project = _project(root)
  input_records = _input_digests(root, project.source, project.assets)
  manifest = _manifest(root)
  if input_records[:1] != [{"path": "texsys.toml", "digest": manifest_digest}] or file_digest(manifest_path) != manifest_digest:
    raise ProjectError("texsys.toml changed while preparing the build. Retry with a stable input revision.")
  packages = project_packages(root, installed=installed_packages)
  selection = resolve_targets(packages, manifest, targets, profile, scope)

  check = check_project(root)
  if check.status != "pass":
    raise ProjectError(f"Cannot build while check status is {check.status}; run texsys check.")
  dependencies = manifest.get("dependencies", {})
  lock_path = root / LOCK_NAME
  verify_lock = dependencies != {} or lock_path.exists() or lock_path.is_symlink()
  if verify_lock:
    lock_project(root, check=True)
  if _input_digests(root, project.source, project.assets) != input_records:
    raise ProjectError("Project inputs changed while preparing the build. Retry with a stable input revision.")

  artifact_records: list[dict[str, Any]] = []
  target_records: list[dict[str, Any]] = []
  generated: set[Path] = set()
  statuses: set[str] = set()
  build_root = (root / project.build).resolve()
  authority_paths = (
    root / project.source,
    root / project.assets,
    root / "texsys.toml",
    root / "texsys.lock",
  )
  for resolved in selection.resolved:
    definition = resolved.definition
    safe_name = definition.id.replace(":", "-").replace("/", "-")
    context = BuildContext(
      root=root,
      target=definition.id,
      profile=resolved.profile,
      scope=resolved.scope,
      build_path=build_root,
      scratch_path=build_root / ".scratch" / safe_name,
      inputs=tuple(input_records),
    )
    status = "pass"
    diagnostics: list[dict[str, Any]] = []
    open_items: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    hook_records: list[dict[str, Any]] = []
    current_artifacts: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    try:
      declarations = load_hooks(manifest, definition.id)
      for declaration in (item for item in declarations if item.phase == "pre"):
        run = run_hook(declaration, context, current_artifacts, authority_paths)
        hook_records.append(run.evidence)
        diagnostics.extend(run.diagnostics)
        open_items.extend(run.open_items)
        if run.affects_target:
          status = _combined_status({status, run.status})
          if run.status != "pass":
            break

      if status == "pass":
        result = definition.builder(context)
        if not isinstance(result, TargetResult):
          raise TypeError(f"Target {definition.id} returned an invalid result.")
        status = result.status
        diagnostics.extend(result.diagnostics)
        open_items.extend(result.open_items)
        details = result.details
        current_artifacts.extend(
          _artifact_records(root, build_root, definition.id, result.artifacts, artifact_ids)
        )

      if status == "pass":
        for declaration in (item for item in declarations if item.phase == "post"):
          run = run_hook(declaration, context, current_artifacts, authority_paths)
          hook_records.append(run.evidence)
          diagnostics.extend(run.diagnostics)
          open_items.extend(run.open_items)
          current_artifacts.extend(
            _artifact_records(root, build_root, definition.id, run.artifacts, artifact_ids)
          )
          if run.affects_target:
            status = _combined_status({status, run.status})
            if run.status != "pass":
              break
    except (OSError, ProjectError, TypeError, ValueError) as error:
      status = "error"
      diagnostics.append({"rule": "E001", "status": "error", "message": str(error)})

    for record in current_artifacts:
      artifact = root / record["path"]
      record["digest"] = file_digest(artifact) if artifact.is_file() else "missing"
      record["partial"] = status != "pass"
      if artifact.is_file():
        generated.add(artifact.resolve())
    artifact_records.extend(current_artifacts)
    statuses.add(status)
    target_records.append(
      {
        "id": definition.id,
        "package": {"name": definition.package_name, "version": definition.package_version},
        "profile": resolved.profile,
        "scope": dict(resolved.scope),
        "resolution_source": resolved.resolution_source,
        "dependencies": list(definition.dependencies),
        "status": status,
        "diagnostics": diagnostics,
        "open_items": open_items,
        "details": details,
        "hooks": hook_records,
      }
    )
    if status != "pass":
      break

  build_diagnostics = []
  try:
    if _input_digests(root, project.source, project.assets) != input_records:
      raise ProjectError("Project inputs changed during the build.")
    if verify_lock:
      lock_project(root, check=True)
  except (OSError, ProjectError) as error:
    statuses.add("error")
    build_diagnostics.append({"rule": "E_INPUT_CHANGED", "status": "error", "message": str(error)})
    for record in artifact_records:
      record["partial"] = True
  status = _combined_status(statuses)
  selected_packages = {
    (item.definition.package_name, item.definition.package_version) for item in selection.resolved
  }
  report = root / project.build / "build.json"
  write_json(
    report,
    {
      "schema_version": 2,
      "command": "build",
      "status": status,
      "requested_targets": list(selection.requested),
      "resolved_targets": [
        {
          "id": item.definition.id,
          "profile": item.profile,
          "scope": dict(item.scope),
          "resolution_source": item.resolution_source,
          "dependencies": list(item.definition.dependencies),
        }
        for item in selection.resolved
      ],
      "packages": [
        {"name": name, "version": version} for name, version in sorted(selected_packages)
      ],
      "inputs": input_records,
      "diagnostics": build_diagnostics,
      "targets": target_records,
      "artifacts": sorted(
        artifact_records, key=lambda item: (item["path"], item["target"], item["id"])
      ),
    },
  )
  unique = sorted(generated, key=lambda item: item.relative_to(root).as_posix())
  return ExecutionResult(status, report, tuple(unique), selection.resolved)


def _combined_status(statuses: set[str], empty: str = "indeterminate") -> str:
  if not statuses:
    return empty
  if "error" in statuses:
    return "error"
  if "fail" in statuses:
    return "fail"
  if "indeterminate" in statuses:
    return "indeterminate"
  return "pass"


def test_project(path: Path) -> ExecutionResult:
  root = path.expanduser().resolve()
  project = _project(root)
  results: list[TestResult] = []
  try:
    for package in project_packages(root, installed=installed_packages):
      for result in package.test(root):
        if not isinstance(result, TestResult):
          raise TypeError(f"Package {package.name} returned an invalid test result.")
        results.append(result)
  except (OSError, TypeError, ValueError) as error:
    raise ProjectError(f"Test execution failed: {error}") from error
  status = _combined_status({item.status for item in results})
  report = root / project.build / "tests" / "results.json"
  write_json(
    report,
    {
      "schema_version": 1,
      "command": "test",
      "status": status,
      "results": [item.as_dict() for item in results],
    },
  )
  return ExecutionResult(status, report, tuple(results))


def evaluate_project(path: Path, scenario: str | Path, backend: str = "scipy") -> ExecutionResult:
  """Evaluate one fixed design-time scenario through its owning domain package."""
  root = path.expanduser().resolve()
  project = _project(root)
  source = (root / scenario).resolve()
  if not source.is_relative_to((root / project.assets).resolve()) or not source.is_file():
    raise ProjectError("Scenario must be an existing file inside the project assets directory.")
  check = check_project(root)
  if check.status != "pass":
    raise ProjectError(f"Cannot evaluate while check status is {check.status}; run texsys check.")
  manifest = _manifest(root)
  if manifest.get("dependencies"):
    lock_project(root, check=True)
  results = []
  for package in project_packages(root, installed=installed_packages):
    result = package.evaluate(root, source, backend)
    if result is not None:
      if not isinstance(result, ExecutionResult):
        raise ProjectError(f"Package {package.name} returned an invalid scenario result.")
      results.append(result)
  if len(results) != 1:
    raise ProjectError(f"Expected exactly one scenario owner, found {len(results)}.")
  return results[0]


def simulate_project(path: Path, kind: str) -> ExecutionResult:
  if kind == "field":
    raise ProjectError("sim(field) is not implemented; no finite-element backend is available.")
  if kind != "event":
    raise ProjectError(f"Unsupported simulation kind: {kind}")
  root = path.expanduser().resolve()
  project = _project(root)
  results: list[dict[str, Any]] = []
  try:
    for package in project_packages(root, installed=installed_packages):
      result = package.simulate(root, kind)
      if result is not None:
        if not isinstance(result, dict):
          raise TypeError(f"Package {package.name} returned an invalid simulation result.")
        results.append(result)
  except (OSError, TypeError, ValueError) as error:
    raise ProjectError(f"Simulation execution failed: {error}") from error
  status = _combined_status({str(item.get("status")) for item in results})
  report = root / project.build / "simulations" / f"{kind}.json"
  write_json(
    report,
    {
      "schema_version": 1,
      "command": "sim",
      "kind": kind,
      "status": status,
      "results": results,
    },
  )
  return ExecutionResult(status, report, tuple(results))


def digest_result(result: TestResult) -> str:
  payload = json.dumps(result.as_dict(), sort_keys=True).encode("utf-8")
  return hashlib.sha256(payload).hexdigest()
