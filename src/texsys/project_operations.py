"""Execute project tools in a fresh interpreter, independently of the IDE host."""

from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import subprocess
import sys

from texsys.project import ProjectError, inspect_project


def run_project_operation(root: Path, action: str, options: dict | None = None) -> dict:
  """Invoke one project operation without importing its packages into the caller.

  The subprocess inherits the caller's process group so Studio cancellation also
  stops its engineering operation. This isolates Python module selection; it is
  not an operating-system sandbox or an external dependency installer.
  """
  root = root.expanduser().resolve()
  if inspect_project(root) is None:
    raise ProjectError("Initialize a project before executing engineering tools.")
  request = {"root": str(root), "action": action, "options": options or {}}
  try:
    completed = subprocess.run(
      [sys.executable, "-B", "-m", "texsys.project_operations"],
      input=json.dumps(request), capture_output=True, text=True, check=False,
      env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=180,
    )
  except subprocess.TimeoutExpired as error:
    raise ProjectError("Project operation exceeded the 180 second limit.") from error
  if completed.stderr:
    print(completed.stderr, end="", file=sys.stderr)
  try:
    response = json.loads(completed.stdout)
  except ValueError as error:
    raise ProjectError("Project worker returned an invalid response; inspect its log.") from error
  if completed.returncode:
    raise ProjectError(response.get("message", "Project operation failed."))
  return response


def _execute(root: Path, action: str, options: dict) -> dict:
  from texsys.packages import project_packages
  from texsys.formal import check_project
  from texsys.execution import build_project, evaluate_project

  if action == "check":
    if options:
      raise ProjectError("Project check does not accept options.")
    checked = check_project(root).as_dict()
    try:
      runtime = [package._texsys_locked_runtime for package in project_packages(root)]
    except (ImportError, OSError, TypeError, ValueError, ProjectError):
      # check_project has already recorded unavailable project dependencies as
      # diagnostics. Preserve that public check result, without inventing tools.
      runtime = []
    return {**checked, "runtime": runtime}
  packages = project_packages(root)
  runtime = [package._texsys_locked_runtime for package in packages]
  if action == "package-check":
    if set(options) != {"packages"} or not isinstance(options["packages"], list):
      raise ProjectError("Package check requires explicit project package names.")
    names = options["packages"]
    if not names or any(not isinstance(name, str) or not name for name in names):
      raise ProjectError("Package check requires non-empty package names.")
    selected = {package.name: package for package in packages}
    missing = set(names) - selected.keys()
    if missing:
      raise ProjectError("Required project capability is not declared: " + ", ".join(sorted(missing)))
    diagnostics = [item.as_dict() for name in names for item in selected[name].check(root)]
    statuses = {item["status"] for item in diagnostics}
    status = next((value for value in ("error", "fail", "indeterminate") if value in statuses), "pass")
    return {"status": status, "diagnostics": diagnostics, "runtime": runtime}
  if action == "build":
    if set(options) - {"targets", "profile", "scope"}:
      raise ProjectError("Unsupported build options.")
    result = build_project(root, targets=tuple(options.get("targets", ())),
                           profile=options.get("profile"), scope=options.get("scope"))
  elif action == "evaluate":
    if set(options) - {"scenario", "backend"} or not isinstance(options.get("scenario"), str):
      raise ProjectError("Evaluation requires a project-relative scenario.")
    scenario = Path(options["scenario"])
    if scenario.is_absolute() or ".." in scenario.parts:
      raise ProjectError("Scenario must be inside the project.")
    result = evaluate_project(root, scenario, options.get("backend", "scipy"))
  else:
    raise ProjectError(f"Unsupported project operation: {action}")
  return {"status": result.status, "report": str(result.report), "runtime": runtime}


def main() -> int:
  try:
    request = json.load(sys.stdin)
    if not isinstance(request, dict) or not isinstance(request.get("options"), dict):
      raise ProjectError("Invalid project operation request.")
    root = Path(request["root"]).expanduser().resolve()
    with redirect_stdout(sys.stderr):
      response = _execute(root, request["action"], request["options"])
    print(json.dumps(response, ensure_ascii=False))
    return 0
  except Exception as error:
    print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
