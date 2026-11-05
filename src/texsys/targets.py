"""Versioned target contracts and deterministic build-target resolution."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from texsys.project import ProjectError

STATUSES = ("pass", "fail", "indeterminate", "error")


@dataclass(frozen=True)
class Artifact:
  """A declared artifact identity; its path is resolved and checked by the runner."""

  id: str
  path: str
  kind: str = "authoritative"

  def __post_init__(self) -> None:
    if not self.id or not self.path:
      raise ValueError("Artifact id and path must be non-empty.")


@dataclass(frozen=True)
class BuildContext:
  root: Path
  target: str
  profile: str | None
  scope: Mapping[str, Any]
  build_path: Path
  scratch_path: Path
  inputs: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class TargetResult:
  status: str = "pass"
  artifacts: tuple[Artifact, ...] = ()
  diagnostics: tuple[dict[str, Any], ...] = ()
  open_items: tuple[dict[str, Any], ...] = ()
  details: dict[str, Any] = field(default_factory=dict)

  def __post_init__(self) -> None:
    if self.status not in STATUSES:
      raise ValueError(f"Unsupported target status: {self.status}")


TargetBuilder = Callable[[BuildContext], TargetResult]


@dataclass(frozen=True)
class TargetDefinition:
  id: str
  package_name: str
  package_version: str
  builder: TargetBuilder
  profiles: tuple[str, ...] = ()
  default_profile: str | None = None
  default_scope: Mapping[str, Any] = field(default_factory=dict)
  dependencies: tuple[str, ...] = ()

  def __post_init__(self) -> None:
    if not self.id:
      raise ValueError("Target id must be non-empty.")
    if self.default_profile is not None and self.profiles and self.default_profile not in self.profiles:
      raise ValueError(f"Default profile is not supported by target {self.id}.")


@dataclass(frozen=True)
class ResolvedTarget:
  definition: TargetDefinition
  profile: str | None
  scope: Mapping[str, Any]
  resolution_source: str


@dataclass(frozen=True)
class TargetSelection:
  requested: tuple[str, ...]
  resolved: tuple[ResolvedTarget, ...]


def _string_list(value: Any, label: str) -> tuple[str, ...]:
  if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
    raise ProjectError(f"{label} must be an array of non-empty target identifiers.")
  return tuple(value)


def _requested_targets(manifest: Mapping[str, Any], requested: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
  if requested:
    if not all(requested):
      raise ProjectError("Target identifiers must be non-empty.")
    return requested, "cli"
  build = manifest.get("build", {})
  if not isinstance(build, dict):
    raise ProjectError("build must be a TOML table.")
  if "default_targets" in build:
    return _string_list(build["default_targets"], "build.default_targets"), "build.default_targets"

  disciplines = manifest.get("disciplines", {})
  if not isinstance(disciplines, dict):
    raise ProjectError("disciplines must be a TOML table.")
  stages: list[str] = []
  for name, record in sorted(disciplines.items()):
    if not isinstance(record, dict) or not isinstance(record.get("current_stage"), str):
      raise ProjectError(f"disciplines.{name}.current_stage must be a target stage identifier.")
    stages.append(record["current_stage"])
  if stages:
    bindings = build.get("stage_targets", {})
    if not isinstance(bindings, dict):
      raise ProjectError("build.stage_targets must be a TOML table.")
    resolved: list[str] = []
    for stage in stages:
      if stage not in bindings:
        raise ProjectError(f"No default targets are bound to stage: {stage}")
      resolved.extend(_string_list(bindings[stage], f"build.stage_targets.{stage}"))
    return tuple(resolved), "build.stage_targets"
  raise ProjectError("No build target selected; use --target or configure build.default_targets.")


def _definitions(packages: tuple[Any, ...]) -> dict[str, TargetDefinition]:
  result: dict[str, TargetDefinition] = {}
  for package in packages:
    for definition in package.targets():
      if not isinstance(definition, TargetDefinition):
        raise ProjectError(f"Package {package.name} returned an invalid target declaration.")
      if definition.id in result:
        raise ProjectError(f"Duplicate target declaration: {definition.id}")
      result[definition.id] = definition
  return result


def resolve_targets(
  packages: tuple[Any, ...],
  manifest: Mapping[str, Any],
  requested: tuple[str, ...] = (),
  profile: str | None = None,
  scope: Mapping[str, Any] | None = None,
) -> TargetSelection:
  """Resolve explicit targets, defaults, profiles, scopes and their dependency DAG."""

  definitions = _definitions(packages)
  requested_items, source = _requested_targets(manifest, requested)
  requested_items = tuple(dict.fromkeys(requested_items))
  build = manifest.get("build", {})
  profiles = build.get("profiles", {})
  scopes = build.get("scopes", {})
  if not isinstance(profiles, dict) or not isinstance(scopes, dict):
    raise ProjectError("build.profiles and build.scopes must be TOML tables.")

  resolved: list[ResolvedTarget] = []
  visiting: list[str] = []
  visited: set[str] = set()

  def visit(identifier: str, target_source: str) -> None:
    if identifier in visited:
      return
    if identifier in visiting:
      cycle = " -> ".join((*visiting, identifier))
      raise ProjectError(f"Target dependency cycle: {cycle}")
    definition = definitions.get(identifier)
    if definition is None:
      raise ProjectError(f"Unknown build target: {identifier}")
    visiting.append(identifier)
    for dependency in definition.dependencies:
      visit(dependency, f"dependency of {identifier}")
    visiting.pop()

    configured_profile = profiles.get(identifier)
    if configured_profile is not None and not isinstance(configured_profile, str):
      raise ProjectError(f"Profile for target {identifier} must be a string.")
    selected_profile = profile or configured_profile or definition.default_profile
    if selected_profile is None and definition.profiles:
      raise ProjectError(f"Target {identifier} requires an explicit profile.")
    if definition.profiles and selected_profile not in definition.profiles:
      supported = ", ".join(definition.profiles)
      raise ProjectError(f"Target {identifier} does not support profile {selected_profile}; expected: {supported}")

    configured_scope = scopes.get(identifier, definition.default_scope)
    if not isinstance(configured_scope, dict):
      raise ProjectError(f"Scope for target {identifier} must be a TOML table.")
    selected_scope = dict(scope if scope is not None else configured_scope)
    resolved.append(ResolvedTarget(definition, selected_profile, selected_scope, target_source))
    visited.add(identifier)

  for item in requested_items:
    visit(item, source)
  return TargetSelection(requested_items, tuple(resolved))
