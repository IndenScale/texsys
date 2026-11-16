"""Measure Studio file reads in the current runtime without starting another server."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import re
import statistics
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from texsys.project import inspect_project
from texsys.studio.files import project_file, project_files
from texsys.studio.preview import ProjectPreview


def measure(operation, repeat):
  samples = []
  for _ in range(repeat):
    started = time.perf_counter()
    result = operation()
    samples.append(round((time.perf_counter() - started) * 1000, 3))
  return result, {"samples_ms": samples, "median_ms": round(statistics.median(samples), 3),
                  "p95_ms": sorted(samples)[math.ceil(len(samples) * 0.95) - 1],
                  "max_ms": max(samples)}


def benchmark_http(paths, scenes, repeat):
  endpoint = "http://127.0.0.1:8710"
  client = build_opener(ProxyHandler({}))
  with client.open(endpoint, timeout=10) as page:
    html = page.read().decode()
  token = re.search(r'<meta name="tex-studio-token" content="([^"]+)"', html)
  if token is None:
    raise ValueError("Tex Studio session metadata is missing at 127.0.0.1:8710.")

  def read(route, query):
    url = f"{endpoint}/api/{route}?{urlencode(query)}"
    with client.open(Request(url, headers={"X-Tex-Studio-Token": token[1]}), timeout=30) as response:
      payload = response.read()
    return json.loads(payload), len(payload)

  def run(route, query, count):
    (data, size), timings = measure(lambda: read(route, query), count)
    return data, {"bytes": size, "status": data.get("status"), **timings}

  files = []
  for path in paths:
    query = {"path": path}
    first, opening = run("file", query, 1)
    _, warm = run("file", query, repeat)
    _, unchanged = run("file", {**query, "revision": first["poll_revision"]}, repeat)
    files.append({"path": path, "first": opening, "warm": warm, "conditional": unchanged})
  scene_results = []
  for path in scenes:
    document, opening = run("file", {"path": path}, 1)
    query = {"path": path, "version": document["version"]}
    scene, initial = run("scene", query, 1)
    _, unchanged = run("scene", {**query, "revision": scene["revision"]}, repeat)
    scene_results.append({"path": path, "objects": len(scene["objects"]), "file": opening,
                          "first": initial, "conditional": unchanged})
  state, state_first = run("state", {"summary": 1}, 1)
  _, state_unchanged = run("state", {"summary": 1, "revision": state["poll_revision"]}, repeat)
  _, listing = run("files", {"compact": 1}, 1)
  return {"scope": "HTTP on the existing 8710 instance, including response decode and excluding browser rendering.",
          "project": state["project"]["path"], "files": files, "scenes": scene_results,
          "state": {"first": state_first, "conditional": state_unchanged}, "listing": listing}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("-p", "--project", type=Path, default=Path("examples/hvac-dynamic-station"))
  parser.add_argument("--file", action="append", dest="files", help="Project-relative file; repeat for multiple files.")
  parser.add_argument("--http", action="store_true", help="Measure the existing Studio session at 127.0.0.1:8710.")
  parser.add_argument("--scene", action="append", default=[], help="USD path for --http; repeat for multiple scenes.")
  parser.add_argument("--shared", action="store_true", help="Reuse one preview service in direct-call mode.")
  parser.add_argument("--repeat", type=int, default=5)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()
  if args.repeat < 1 or (args.scene and not args.http):
    parser.error("Use a positive --repeat and pair --scene with --http.")
  root = args.project.resolve()
  project = inspect_project(root)
  if project is None and not args.http:
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
  if args.http:
    report.update(benchmark_http(paths, args.scene, args.repeat))
  else:
    previews = ProjectPreview(root, project.build) if args.shared else None
    for path in paths:
      result, timings = measure(lambda: project_file(root, path, project.build, previews=previews), args.repeat)
      report["files"].append({"path": path, "bytes": result["size"], "version": result["version"], **timings})
    listing, timings = measure(lambda: project_files(root, project.build, previews=previews), args.repeat)
    report["listing"] = {"file_count": len(listing["files"]), "truncated": listing["truncated"], **timings}
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
  main()
