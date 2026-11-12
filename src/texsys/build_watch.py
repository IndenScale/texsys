"""Debounce saved project inputs and invoke the ordinary build in a fresh worker."""

from pathlib import Path
import time
import tomllib

from texsys.build_records import BuildBusy, build_input_paths
from texsys.formal import _load_json
from texsys.project import ProjectError, inspect_project
from texsys.project_operations import run_project_operation

InputSignature = tuple[tuple[Path, int, int, int, int], ...]


def _signature(paths) -> InputSignature:
  signatures = []
  for path in paths:
    stat = path.stat()
    signatures.append((path, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
  return tuple(signatures)


def _observe(root: Path) -> tuple[InputSignature, str | None]:
  try:
    project = inspect_project(root)
    if project is None:
      raise ProjectError(f"Project is not initialized: {root}")
    return _signature(build_input_paths(root, project.source, project.assets)), None
  except (OSError, ProjectError) as error:
    # Keep observing the manifest after a temporary deletion or invalid save.
    return (), str(error)


def _check_syntax(root: Path, paths: InputSignature) -> None:
  for path, *_ in paths:
    try:
      if path.suffix == ".json":
        _load_json(path.read_text(encoding="utf-8"))
      elif path.suffix == ".toml" or path.name == "texsys.lock":
        with path.open("rb") as stream:
          tomllib.load(stream)
    except (OSError, ValueError) as error:
      raise ProjectError(f"{path.relative_to(root)}: {error}") from error


def watch_build(root: Path, options: dict, debounce: float, emit) -> int:
  root = root.expanduser().resolve()
  if not root.is_dir():
    raise ProjectError(f"Project path is not a directory: {root}")
  interval = min(0.25, debounce)
  observed = None
  pending = True
  last_exit = 0
  waiting_for_lock = False
  emit(f"Watching saved project inputs (debounce {debounce:g}s). Press Ctrl+C to stop.")
  try:
    while True:
      observation = _observe(root)
      now = time.monotonic()
      if observation != observed:
        observed = observation
        ready_at = now + debounce
        pending = True
        waiting_for_lock = False
      if pending and now >= ready_at:
        pending = False
        signature, error = observation
        try:
          if error:
            raise ProjectError(error)
          _check_syntax(root, signature)
          if _observe(root) != observation:
            continue
          result = run_project_operation(root, "build", options, timeout=None)
          emit(f"Status: {result['status']}")
          emit(f"Evidence: {Path(result['report']).relative_to(root)}")
          last_exit = {"pass": 0, "fail": 1, "error": 2, "indeterminate": 3}[result["status"]]
        except BuildBusy:
          if not waiting_for_lock:
            emit("Another build is running. Waiting for the project build lock.", err=True)
          waiting_for_lock = True
          pending = True
          ready_at = now + max(debounce, 1.0)
        except ProjectError as error:
          emit(f"Error: {error}", err=True)
          last_exit = 2
      time.sleep(interval)
  except KeyboardInterrupt:
    return last_exit
