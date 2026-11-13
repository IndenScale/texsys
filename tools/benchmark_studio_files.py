"""Measure Studio file reads in the current runtime without starting another server."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import subprocess
import time

from texsys.project import inspect_project
from texsys.studio.files import project_file, project_files


def measure(operation, repeat):
  samples = []
  for _ in range(repeat):
    started = time.perf_counter()
    result = operation()
    samples.append(round((time.perf_counter() - started) * 1000, 3))
  return result, {"samples_ms": samples, "median_ms": round(statistics.median(samples), 3),
                  "max_ms": max(samples)}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("-p", "--project", type=Path, default=Path("examples/hvac-dynamic-station"))
  parser.add_argument("--file", action="append", dest="files", help="Project-relative file; repeat for multiple files.")
  parser.add_argument("--repeat", type=int, default=5)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()
  if args.repeat < 1:
    parser.error("--repeat must be positive")
  root = args.project.resolve()
  project = inspect_project(root)
  if project is None:
    parser.error("Project manifest not found")
  paths = args.files or ["texsys.toml", "src/system.sdl.json", "build/drawings/schematics/system-check.json",
                         "build/drawings/schematics/system.html", "build/drawings/schematics/system.json"]
  repo = Path(__file__).resolve().parents[1]
  revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
  dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
  report = {"created_at": datetime.now(timezone.utc).isoformat(), "revision": revision,
            "working_tree_dirty": bool(dirty), "project": str(root), "platform": platform.platform(),
            "python": platform.python_version(), "repeat": args.repeat,
            "scope": "Sequential backend calls in one process; includes filesystem/runtime caches, excludes HTTP and editor rendering.",
            "files": []}
  for path in paths:
    result, timings = measure(lambda: project_file(root, path, project.build), args.repeat)
    report["files"].append({"path": path, "bytes": result["size"], "version": result["version"], **timings})
  listing, timings = measure(lambda: project_files(root, project.build), args.repeat)
  report["listing"] = {"file_count": len(listing["files"]), "truncated": listing["truncated"], **timings}
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
  main()
