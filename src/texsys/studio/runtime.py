"""Local project revisions, isolated input snapshots and persistent worker jobs."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import uuid

import tomlkit

from texsys.dependencies import _dependency_specs, _lock_text, lock_project
from texsys.evidence import canonical_digest, file_digest, write_json
from texsys.project import ProjectError, inspect_project
from . import discover_plugins


class Conflict(ProjectError):
  pass


def git_context(root: Path) -> dict:
  """Read project Git identity without changing checkout or treating input digests as branches."""
  try:
    branch = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"],
                            capture_output=True, text=True, timeout=2, check=False)
    if branch.returncode == 0:
      return {"kind": "branch", "name": branch.stdout.strip()}
    if branch.returncode == 1:
      commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=2, check=False)
      if commit.returncode == 0:
        return {"kind": "detached", "name": commit.stdout.strip()}
      return {"kind": "unavailable"}
    return {"kind": "unversioned"}
  except (OSError, subprocess.TimeoutExpired):
    return {"kind": "unavailable"}


def input_records(root: Path) -> list[dict]:
  project = inspect_project(root)
  if project is None:
    raise ProjectError("Initialize a texsys project before opening Studio.")
  files = [root / "texsys.toml", root / "texsys.lock"]
  for name in (project.source, project.assets):
    files.extend(sorted((root / name).rglob("*")))
  records = []
  for file in files:
    if file.is_file() and file.name != ".gitkeep":
      if not file.resolve().is_relative_to(root):
        raise ProjectError("Studio inputs must remain inside the project.")
      records.append({"path": file.relative_to(root).as_posix(), "digest": file_digest(file)})
  return sorted(records, key=lambda item: item["path"])


def revision(root: Path) -> str:
  return canonical_digest(input_records(root))


def _build_revision(root: Path, build: str) -> str:
  # The runner replaces this report atomically after publishing artifacts.
  try:
    stat = (root / build / "build.json").stat(follow_symlinks=False)
  except OSError:
    return ""
  return f"{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"


def snapshot_project(root: Path, destination: Path, records: list[dict]) -> None:
  # Lock paths are relative to canonical project roots. Temporary directories
  # may arrive through aliases such as macOS /var -> /private/var.
  root = root.expanduser().resolve()
  destination = destination.expanduser().resolve()
  project = inspect_project(root)
  # Verify the user's original lock before adapting paths in the isolated copy.
  manifest = tomlkit.parse((root / "texsys.toml").read_text())
  locked = None
  if manifest.get("dependencies"):
    locked = lock_project(root, check=True)
  destination.mkdir(parents=True)
  for record in records:
    if record["path"] == "texsys.lock":
      continue
    source = root / record["path"]
    if file_digest(source) != record["digest"]:
      raise Conflict("Project changed while preparing the task; refresh and try again.")
    target = destination / record["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if file_digest(target) != record["digest"]:
      raise Conflict("Project changed while copying task inputs.")
  if canonical_digest(input_records(root)) != canonical_digest(records):
    raise Conflict("Project changed while preparing the task.")
  for dependency in manifest.get("dependencies", {}).values():
    for key in ("path", "registry"):
      if key in dependency:
        dependency[key] = str((root / dependency[key]).resolve())
  (destination / "texsys.toml").write_text(tomlkit.dumps(manifest), encoding="utf-8")
  (destination / project.source).mkdir(parents=True, exist_ok=True)
  (destination / project.assets).mkdir(parents=True, exist_ok=True)
  (destination / project.build).mkdir(parents=True, exist_ok=True)
  if locked is not None:
    # Preserve exact original releases and digests; snapshot creation never
    # resolves a newer package version or rebinds changed published content.
    text = _lock_text(_dependency_specs(manifest, destination), locked.packages, destination)
    (destination / "texsys.lock").write_text(text, encoding="utf-8")
    lock_project(destination, check=True)


class StudioRuntime:
  def __init__(self, root: Path, plugins=None):
    self.root = root.expanduser().resolve()
    self.project = inspect_project(self.root)
    if self.project is None:
      raise ProjectError("Initialize a texsys project before opening Studio.")
    self.plugins, self.diagnostics = discover_plugins() if plugins is None else (plugins, [])
    self.directory = self.root / self.project.build / "studio"
    self.directory.mkdir(parents=True, exist_ok=True)
    self.lock = threading.RLock()
    self.processes = {}
    self.jobs = {}
    for file in sorted((self.directory / "jobs").glob("*/job.json")):
      try:
        job = json.loads(file.read_text())
        if job["state"] in {"running", "queued"}:
          job.update(state="interrupted", message="Studio stopped before task completion.")
          write_json(file, job)
        self.jobs[job["id"]] = job
      except (OSError, ValueError, KeyError):
        self.diagnostics.append({"message": f"Unreadable task record: {file.name}"})

  def _plugin(self, identity):
    if identity not in self.plugins:
      raise ProjectError("The requested discipline is not loaded.")
    return self.plugins[identity]

  def _refresh_jobs(self):
    for identity, process in list(self.processes.items()):
      code = process.poll()
      if code is None:
        continue
      job = self.jobs[identity]
      if job["state"] != "cancelled":
        response = self.directory / "jobs" / identity / "response.json"
        try:
          output = json.loads(response.read_text())
          job.update(state="completed" if code == 0 else "failed", result=output)
        except (OSError, ValueError):
          job.update(state="failed", message=f"Worker exited with code {code}; inspect its log.")
      job["finished_at"] = datetime.now(timezone.utc).isoformat()
      write_json(self.directory / "jobs" / identity / "job.json", job)
      self.processes.pop(identity)

  def state(self):
    with self.lock:
      self._refresh_jobs()
      current = revision(self.root)
      jobs = sorted(self.jobs.values(), key=lambda item: item["created_at"], reverse=True)
      result = {}
      for identity, plugin in self.plugins.items():
        artifacts = []
        for job in jobs:
          if job["plugin"] == identity and job["state"] == "completed":
            artifacts.append({**job, "current": job["revision"] == current,
                              "root": self.directory / "jobs" / job["id"] / "project"})
        try:
          result[identity] = plugin.state(self.root, artifacts)
        except Exception as error:
          result[identity] = {"error": str(error)}
      return {"api_version": 1, "project": {"name": self.project.name, "path": str(self.root), "revision": current,
                                            "build_revision": _build_revision(self.root, self.project.build),
                                            "branch": git_context(self.root)},
              "plugins": [plugin.manifest for plugin in self.plugins.values()], "data": result,
              "jobs": [{**job, "current": job["revision"] == current} for job in jobs],
              "diagnostics": self.diagnostics}

  def edit(self, request):
    with self.lock:
      if request.get("revision") != revision(self.root):
        raise Conflict("Source changed outside this editor. Refresh before saving.")
      output = self._plugin(request.get("plugin")).edit(self.root, request.get("operation", {}))
      return {"revision": revision(self.root), **output}

  def submit(self, request):
    with self.lock:
      self._refresh_jobs()
      if self.processes:
        raise Conflict("A task is already running. Wait for it or cancel it first.")
      records = input_records(self.root)
      base = canonical_digest(records)
      if request.get("revision") != base:
        raise Conflict("Project changed; refresh before starting the task.")
      identity = request.get("plugin")
      plugin = self._plugin(identity)
      action, options = request.get("action"), request.get("options", {})
      if not isinstance(action, str) or not isinstance(options, dict):
        raise ProjectError("Invalid task action or options.")
      plugin.validate_action(self.root, action, options)
      job_id = uuid.uuid4().hex
      directory = self.directory / "jobs" / job_id
      try:
        snapshot_project(self.root, directory / "project", records)
        job = {"id": job_id, "plugin": identity, "action": action, "options": options,
               "revision": base, "inputs": records, "state": "running",
               "created_at": datetime.now(timezone.utc).isoformat(),
               "log": (directory / "worker.log").relative_to(self.root).as_posix()}
        write_json(directory / "job.json", job)
        with (directory / "worker.log").open("w") as log:
          process = subprocess.Popen([sys.executable, "-m", "texsys.studio.worker", str(directory)],
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
      except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
      self.jobs[job_id], self.processes[job_id] = job, process
      return job

  def cancel(self, identity):
    with self.lock:
      self._refresh_jobs()
      process = self.processes.get(identity)
      if process is None:
        raise Conflict("Task is no longer running.")
      if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
      else:
        process.terminate()
      try:
        process.wait(timeout=5)
      except subprocess.TimeoutExpired:
        if os.name == "posix":
          os.killpg(process.pid, signal.SIGKILL)
        else:
          process.kill()
        process.wait()
      self.jobs[identity]["state"] = "cancelled"
      self._refresh_jobs()
      return self.jobs[identity]

  def close(self):
    for identity in list(self.processes):
      try:
        self.cancel(identity)
      except Conflict:
        pass
