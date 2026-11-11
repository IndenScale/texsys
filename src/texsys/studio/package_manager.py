"""Studio transport for the shared package operations; no resolver or installer here."""

from pathlib import Path

from texsys.project import ProjectError


def _text(request: dict, name: str) -> str:
  value = request.get(name)
  if not isinstance(value, str) or not value.strip():
    raise ProjectError(f"Package operation requires {name}.")
  return value.strip()


def _registry(root: Path, request: dict) -> Path:
  return (root / Path(_text(request, "registry")).expanduser()).resolve()


def package_status(root: Path) -> dict:
  from texsys.dependencies import dependency_status
  from texsys.packages import runtime_status

  result = dependency_status(root)
  runtime = runtime_status(root)
  available = {item["name"]: item for item in runtime.get("packages", [])}
  return {**result, "runtime": runtime,
          "packages": [{**item, "runtime_status": available.get(item["name"])}
                       for item in result.get("packages", [])]}


def package_operation(root: Path, request: dict) -> dict:
  from texsys.dependencies import (apply_dependency_change,
                                   plan_dependency_change, sync_project)
  from texsys.registry import package_details, search_packages

  action = request.get("action")
  if action == "browse":
    query = request.get("query", "")
    if not isinstance(query, str):
      raise ProjectError("Package search query must be text.")
    return search_packages(_registry(root, request), query)
  if action == "details":
    return package_details(_registry(root, request), _text(request, "name"), _text(request, "version"))
  if action in {"plan", "apply"}:
    change = request.get("request")
    if not isinstance(change, dict):
      raise ProjectError("Package change request must be an object.")
    if action == "plan":
      return plan_dependency_change(root, change)
    return apply_dependency_change(root, change, _text(request, "plan"))
  if action == "sync":
    sync_project(root, expected_revision=_text(request, "revision"))
    return package_status(root)
  if action == "test":
    from texsys.package_tests import test_capability_package

    return test_capability_package(root, _text(request, "name"))
  raise ProjectError("Unknown package operation.")
