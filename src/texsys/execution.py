"""Backend-neutral build, behavior-test and simulation orchestration."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
from typing import Any
from uuid import uuid4

from texsys.build_records import (
  artifact_path, build_lock, cache_environment, input_records, previous_run_id, publish_artifacts, restore_cache,
  seed_stage, snapshot, store_cache, target_cache_key, timestamp, toolchain,
)

from texsys.dependencies import LOCK_NAME, lock_project
from texsys.evidence import canonical_digest, file_digest, write_json
from texsys.formal import check_project
from texsys.hooks import load_hooks, run_hook
from texsys.packages import project_packages
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
  return input_records(root, source, assets)


def _artifact_records(
  root: Path,
  build_root: Path,
  stage: Path,
  target: str,
  declared_artifacts,
  existing_ids: set[str],
) -> list[dict[str, Any]]:
  records: list[dict[str, Any]] = []
  for declared in declared_artifacts:
    if declared.id in existing_ids:
      raise ProjectError(f"Target {target} declared duplicate artifact: {declared.id}")
    existing_ids.add(declared.id)
    artifact = root / declared.path
    if ".." in Path(declared.path).parts:
      raise ProjectError(f"Artifact cannot use symlinks or parent traversal: {declared.path}")
    if artifact.is_relative_to(stage):
      public = build_root / artifact.relative_to(stage)
    else:
      public = artifact
    public = artifact_path(root, build_root, public)
    artifact = stage / public.relative_to(build_root)
    if artifact.resolve() != artifact:
      raise ProjectError(f"Artifact cannot use symlinks or parent traversal: {declared.path}")
    if not artifact.is_file():
      raise ProjectError(f"Target {target} did not create artifact: {declared.path}")
    records.append(
      {
        "target": target,
        "id": declared.id,
        "kind": declared.kind,
        "path": public.relative_to(root).as_posix(),
        "digest": file_digest(artifact),
        "complete": declared.complete,
        "partial": not declared.complete,
        **({"publish_partial": True} if declared.publish_partial else {}),
        "preview": dict(declared.preview) if declared.preview is not None else None,
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
  project = _project(root)
  with build_lock(root, project.build) as build_root:
    with TemporaryDirectory(prefix=".stage-", dir=build_root) as temporary:
      return _build_project(root, project, build_root, Path(temporary), targets, profile, scope)


def _build_project(root, project, build_root, staging, targets, profile, scope):
  manifest_path = root / "texsys.toml"
  manifest_digest = file_digest(manifest_path)
  input_records = _input_digests(root, project.source, project.assets)
  manifest = _manifest(root)
  if (input_records[:1] != [{"path": "texsys.toml", "digest": manifest_digest}]
      or _project(root) != project or file_digest(manifest_path) != manifest_digest):
    raise ProjectError("texsys.toml changed while preparing the build. Retry with a stable input revision.")
  packages = project_packages(root)
  selection = resolve_targets(packages, manifest, targets, profile, scope)
  definitions = [item.definition for item in selection.resolved]
  tools = toolchain(packages, definitions)
  tools_digest = canonical_digest(tools)
  environment = cache_environment(definitions)

  check = check_project(root)
  declared_dependencies = manifest.get("dependencies", {})
  lock_path = root / LOCK_NAME
  verify_lock = declared_dependencies != {} or lock_path.exists() or lock_path.is_symlink()
  if verify_lock:
    lock_project(root, check=True)
  if _input_digests(root, project.source, project.assets) != input_records:
    raise ProjectError("Project inputs changed while preparing the build. Retry with a stable input revision.")

  previous_run = previous_run_id(root, build_root)
  run_id = uuid4().hex
  run_root = build_root / "runs" / run_id
  run_root.mkdir(parents=True, exist_ok=False)
  started_at = timestamp()
  # Preserve inputs alongside outputs so historical relative asset references
  # never resolve against a later edit of the source project.
  snapshot(root, run_root, input_records)
  artifact_records: list[dict[str, Any]] = []
  target_records: list[dict[str, Any]] = []
  generated: set[Path] = set()
  statuses: set[str] = {check.status}
  completed = {}
  pending_cache = []
  claimed_paths = {Path((build_root / output).as_posix().casefold()): owner
                   for output, owner in selection.named_outputs.items()}
  transitive_dependencies = {}
  authority_paths = (
    root / project.source, root / project.assets, root / "texsys.toml", root / "texsys.lock",
  )
  for index, resolved in enumerate(selection.resolved):
    definition = resolved.definition
    identifier = resolved.id
    dependencies = resolved.dependencies
    output = resolved.output
    target_fields = {"using": definition.id, "output": (build_root / output).relative_to(root).as_posix()} if output else {}
    base = {
      "id": identifier, **target_fields,
      "package": {"name": definition.package_name, "version": definition.package_version},
      "profile": resolved.profile, "scope": dict(resolved.scope),
      "resolution_source": resolved.resolution_source,
      "dependencies": list(dependencies),
    }
    blocked_by = [name for name in dependencies if completed[name]["status"] != "pass"]
    if blocked_by:
      record = {**base, "execution": "blocked", "status": "indeterminate", "blocked_by": blocked_by,
                "cache_key": None, "diagnostics": [{"rule": "E_DEPENDENCY_BLOCKED", "status": "indeterminate",
                  "message": "Required build dependencies did not pass: " + ", ".join(blocked_by)}],
                "open_items": [], "details": {}, "hooks": []}
      target_records.append(record)
      completed[identifier] = record
      statuses.add("indeterminate")
      continue
    upstream = [{"id": name, "cache_key": completed[name]["cache_key"],
                 "status": completed[name]["status"],
                 "artifacts": [{"id": a["id"], "digest": a["digest"], "complete": a["complete"]}
                               for a in artifact_records if a["target"] == name]}
                for name in dependencies]
    dependency_ids = set(dependencies)
    for name in dependencies:
      dependency_ids.update(transitive_dependencies[name])
    transitive_dependencies[identifier] = dependency_ids
    safe_name = identifier.replace(":", "-").replace("/", "-")
    stage = staging / str(index)
    context = BuildContext(root=root, target=definition.id, profile=resolved.profile, scope=resolved.scope,
                           build_path=stage, scratch_path=build_root / ".scratch" / safe_name,
                           inputs=tuple(input_records), instance_id=resolved.instance_id, output=output)
    cache_key = None
    status = "pass"
    diagnostics: list[dict[str, Any]] = []
    open_items: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    hook_records: list[dict[str, Any]] = []
    current_artifacts: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    cache_enabled = False
    cached = None
    try:
      patterns = definition.input_patterns(context) if callable(definition.input_patterns) else definition.input_patterns
      if patterns is not None and (not isinstance(patterns, tuple) or any(
        not isinstance(pattern, str) or not pattern or pattern.startswith("/") or ".." in pattern.split("/")
        for pattern in patterns
      )):
        raise ProjectError(f"Target {identifier} returned invalid input patterns.")
      base["input_patterns"] = list(patterns) if patterns is not None else None
      cache_key = target_cache_key(resolved, input_records, tools_digest, upstream, patterns)
      declarations = load_hooks(manifest, identifier)
      cache_policy = definition.cache(context) if callable(definition.cache) else definition.cache
      if not isinstance(cache_policy, bool):
        raise ProjectError(f"Target {definition.id} returned an invalid cache policy.")
      cache_enabled = cache_policy and not declarations
      cached = restore_cache(root, build_root, stage, cache_key) if cache_enabled else None
      if cached:
        previous = cached["target"]
        status = previous["status"]
        diagnostics = previous["diagnostics"]
        open_items = previous["open_items"]
        details = previous["details"]
        current_artifacts = cached["artifacts"]
      else:
        stage.mkdir(exist_ok=True)
        seed_stage(root, build_root, stage, (a for a in artifact_records if a["target"] in dependency_ids))
        for declaration in (item for item in declarations if item.phase == "pre"):
          run = run_hook(declaration, context, current_artifacts, authority_paths, public_build=build_root)
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
          current_artifacts.extend(_artifact_records(root, build_root, stage, identifier, result.artifacts, artifact_ids))
        if status == "pass":
          for declaration in (item for item in declarations if item.phase == "post"):
            run = run_hook(declaration, context, current_artifacts, authority_paths, public_build=build_root)
            hook_records.append(run.evidence)
            diagnostics.extend(run.diagnostics)
            open_items.extend(run.open_items)
            current_artifacts.extend(_artifact_records(root, build_root, stage, identifier, run.artifacts, artifact_ids))
            if run.affects_target:
              status = _combined_status({status, run.status})
              if run.status != "pass":
                break
      source_paths = {}
      produced_paths = set()
      for record in current_artifacts:
        public = root / record["path"]
        key = Path(public.as_posix().casefold())
        if any(key.is_relative_to(path) or path.is_relative_to(key) for path in produced_paths) or any(
          key.is_relative_to(path) or path.is_relative_to(key)
          for path, owner in claimed_paths.items() if owner != identifier or path != key
        ):
          raise ProjectError(f"Target {identifier} conflicts with another artifact at {record['path']}.")
        artifact = stage / public.relative_to(build_root)
        source_paths[public] = artifact
        produced_paths.add(key)
        record["digest"] = file_digest(artifact)
        # Execution errors cannot certify output completeness; an engineering
        # fail/indeterminate result can still have a fully formed report/scene.
        if status == "error":
          record["complete"] = False
        if status != "pass":
          record.pop("publish_partial", None)
        record["partial"] = not record["complete"]
      if output is not None and status == "pass" and build_root / output not in source_paths:
        raise ProjectError(f"Target {identifier} did not declare its configured output: {output}")
      current_artifacts = snapshot(root, run_root, current_artifacts, {
        public.relative_to(root).as_posix(): artifact for public, artifact in source_paths.items()
      })
      claimed_paths.update(dict.fromkeys(produced_paths, identifier))
    except Exception as error:
      status = "error"
      diagnostics.append({"rule": "E001", "status": "error", "message": str(error)})
      # Only artifacts already preserved with verified bytes are publishable.
      current_artifacts = [item for item in current_artifacts if "stored_path" in item]
      for item in current_artifacts:
        item.update(complete=False, partial=True)

    for record in current_artifacts:
      record["published"] = False
    artifact_records.extend(current_artifacts)
    statuses.add(status)
    record = {**base, "status": status, "execution": "cached" if cached else "executed",
              "cache_key": cache_key, "cache_enabled": cache_enabled, "blocked_by": [],
              "diagnostics": diagnostics, "open_items": open_items, "details": details, "hooks": hook_records}
    if cached:
      record["reused_from"] = cached["run_id"]
    target_records.append(record)
    completed[identifier] = record
    if not cached and cache_enabled and status != "error" and all(item["complete"] for item in current_artifacts):
      pending_cache.append((cache_key, record, current_artifacts))

  build_diagnostics = []
  consistent = True
  try:
    if _input_digests(root, project.source, project.assets) != input_records:
      raise ProjectError("Project inputs changed during the build.")
    if canonical_digest(toolchain(packages, definitions)) != tools_digest:
      raise ProjectError("Build tools or execution environment changed during the build.")
    if cache_environment(definitions) != environment:
      raise ProjectError("Declared target environment changed during the build.")
    if verify_lock:
      lock_project(root, check=True)
  except (OSError, ProjectError) as error:
    consistent = False
    statuses.add("error")
    build_diagnostics.append({"rule": "E_INPUT_CHANGED", "status": "error", "message": str(error)})
    for record in artifact_records:
      record["complete"], record["partial"] = False, True
  if consistent:
    publication_failed = set()
    for index, target in enumerate(target_records):
      if target["status"] == "error":
        continue
      artifacts = [record for record in artifact_records if record["target"] == target["id"]]
      unavailable = publication_failed.intersection(target["dependencies"])
      if unavailable:
        statuses.add("error")
        publication_failed.add(target["id"])
        target["diagnostics"].append({"rule": "E_PUBLISH_BLOCKED", "status": "error",
                                      "message": "Required outputs could not be published: " + ", ".join(sorted(unavailable))})
        continue
      try:
        published = publish_artifacts(root, build_root, staging / "publish" / str(index), artifacts, previous_run)
        generated.update(root / path for path in published)
        for record in artifacts:
          record["published"] = record["path"] in published
        if len(published) < len(artifacts):
          publication_failed.add(target["id"])
      except (OSError, ProjectError) as error:
        statuses.add("error")
        publication_failed.add(target["id"])
        target["diagnostics"].append({"rule": "E_PUBLISH", "status": "error", "message": str(error)})
    for key, record, artifacts in pending_cache:
      if record["id"] in publication_failed:
        continue
      try:
        store_cache(root, build_root, key, record, artifacts, run_id)
      except (OSError, ProjectError, ValueError) as error:
        # A cache write is an optimization failure, not a new engineering result.
        build_diagnostics.append({"rule": "W_CACHE_WRITE", "status": "warning", "message": str(error)})
  status = _combined_status(statuses)
  selected_packages = {(item.definition.package_name, item.definition.package_version) for item in selection.resolved}
  report = root / project.build / "build.json"
  payload = {
    "schema_version": 3, "command": "build", "run_id": run_id, "previous_run_id": previous_run,
    "started_at": started_at,
    "created_at": timestamp(), "status": status, "inputs_consistent": consistent,
    "input_digest": canonical_digest(input_records), "toolchain": tools, "toolchain_digest": tools_digest,
    "cache_environment": environment,
    "requested_targets": list(selection.requested),
    "resolved_targets": [{"id": item.id, "profile": item.profile, "scope": dict(item.scope),
                          "resolution_source": item.resolution_source,
                          "dependencies": list(item.dependencies),
                          **({"using": item.definition.id, "output": (build_root / item.output).relative_to(root).as_posix()}
                             if item.output is not None else {})} for item in selection.resolved],
    "packages": [{"name": name, "version": version} for name, version in sorted(selected_packages)],
    "inputs": input_records, "preflight": check.as_dict(), "diagnostics": build_diagnostics,
    "targets": target_records,
    "artifacts": sorted(artifact_records, key=lambda item: (item["path"], item["target"], item["id"])),
  }
  payload["record_digest"] = canonical_digest(payload)
  write_json(run_root / "build.json", payload)
  write_json(report, payload)
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
    for package in project_packages(root):
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
  for package in project_packages(root):
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
    for package in project_packages(root):
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
