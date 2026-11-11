"""Run a locked capability's own declared tests in a fresh interpreter."""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib
import uuid

from texsys.dependencies import lock_project
from texsys.evidence import write_json
from texsys.project import ProjectError, inspect_project


def _selected(root, name):
  locked = lock_project(root, check=True)
  package = next((item for item in locked.packages if item.name == name), None)
  if package is None:
    raise ProjectError(f"Package is not locked by this project: {name}")
  manifest = tomllib.loads((package.root / "texsys.toml").read_text(encoding="utf-8"))
  declared = manifest.get("package", {}).get("tests", [])
  if not isinstance(declared, list) or not declared:
    raise ProjectError(f"Package {name} does not declare bundled tests.")
  paths = []
  for value in declared:
    if not isinstance(value, str) or not value or "\\" in value:
      raise ProjectError("Package test paths must be package-relative paths.")
    relative = Path(value)
    path = package.root / relative
    if relative.is_absolute() or ".." in relative.parts or path.resolve() != path or not path.exists():
      raise ProjectError(f"Invalid package test path: {value}")
    if not path.is_relative_to(package.root) or path == package.root:
      raise ProjectError("Package tests must stay inside the locked package.")
    paths.append(path)
  return package, paths


def test_capability_package(root: Path, name: str) -> dict:
  """Preserve test output separately from project engineering acceptance."""
  root = root.expanduser().resolve()
  project = inspect_project(root)
  if project is None:
    raise ProjectError("Initialize a project before running capability tests.")
  package, paths = _selected(root, name)
  if importlib.util.find_spec("pytest") is None:
    raise ProjectError("Package verification requires pytest in the execution environment; sync does not install it.")
  started = time.monotonic()
  env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
  env.pop("PYTEST_ADDOPTS", None)
  env.pop("PYTEST_PLUGINS", None)
  command = [sys.executable, "-m", "texsys.package_tests", str(root), name]
  try:
    completed = subprocess.run(command, capture_output=True, text=True, env=env, check=False, timeout=180)
  except subprocess.TimeoutExpired as error:
    raise ProjectError("Capability tests exceeded the 180 second limit.") from error
  current, _ = _selected(root, name)
  if current.digest != package.digest or current.version != package.version:
    raise ProjectError("Package changed during its verification; the result is not current.")
  result = {
    "status": "pass" if completed.returncode == 0 else "fail" if completed.returncode == 1 else "error",
    "scope": "package-unit-tests", "engineering_acceptance": "not-evaluated",
    "package": {"name": name, "version": package.version, "digest": package.digest},
    "tests": [path.relative_to(package.root).as_posix() for path in paths],
    "exit_code": completed.returncode, "elapsed_s": round(time.monotonic() - started, 3),
    "created_at": datetime.now(timezone.utc).isoformat(),
    "stdout": completed.stdout, "stderr": completed.stderr,
  }
  report = root / project.build / "packages" / "verification" / f"{uuid.uuid4().hex}.json"
  result["report"] = report.relative_to(root).as_posix()
  write_json(report, result)
  return result


def _worker(root, name):
  from texsys.packages import project_packages
  project_packages(root, locked_only=True)
  package, paths = _selected(root, name)
  import pytest
  return pytest.main(["-q", "-p", "no:cacheprovider", "-c", os.devnull, "--import-mode=importlib",
                      "--confcutdir", str(package.root), *[str(path) for path in paths]])


if __name__ == "__main__":
  try:
    sys.exit(_worker(Path(sys.argv[1]).resolve(), sys.argv[2]))
  except ProjectError as error:
    print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
    sys.exit(2)
