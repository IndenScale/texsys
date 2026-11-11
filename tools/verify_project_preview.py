"""Replay incremental builds and read-only project previews in two isolated projects."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from texsys.studio.preview import ProjectPreview


REPO = Path(__file__).resolve().parents[1]
LAYOUT_TARGET = "hvac:project-layout"
EXPECTED_KINDS = {"bom", "topology", "schematic", "scene"}


def write_json(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def require(condition, message):
  if not condition:
    raise RuntimeError(message)


def digest(path):
  return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output", type=Path, help="New directory for projects, command logs and evidence.")
  options = parser.parse_args()
  output = (options.output or REPO / "build/project-preview-poc" / uuid.uuid4().hex).resolve()
  output.mkdir(parents=True, exist_ok=False)
  example = REPO / "examples/hvac-dynamic-station"
  original = {str(path.relative_to(example)): digest(path) for directory in ("src", "assets")
              for path in (example / directory).rglob("*") if path.is_file()}
  projects = []
  for name in ("project-a", "project-b"):
    root = output / name
    shutil.copytree(example, root, ignore=shutil.ignore_patterns("build", ".texsys-write.lock"))
    manifest = root / "texsys.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("../../packages/", f"{REPO}/packages/"),
                        encoding="utf-8")
    projects.append(root)
  first, second = projects
  commands = []
  summary = {"status": "running", "created_at": datetime.now(timezone.utc).isoformat(),
             "scope": "概念暖通项目的增量构建、部分失败与只读预览；不代表真实设备校准。",
             "projects": [str(path) for path in projects], "steps": {}}

  def cli(root, *arguments, expected=0):
    command = [sys.executable, "-m", "texsys", *arguments, "-p", str(root)]
    start = time.monotonic()
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    prefix = output / "commands" / f"{len(commands) + 1:02d}-{root.name}-{arguments[0]}"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".stdout.txt").write_text(result.stdout, encoding="utf-8")
    prefix.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")
    commands.append({"command": command, "exit_code": result.returncode, "expected": expected,
                     "elapsed_s": round(time.monotonic() - start, 3),
                     "stdout": str(prefix.with_suffix(".stdout.txt").relative_to(output)),
                     "stderr": str(prefix.with_suffix(".stderr.txt").relative_to(output))})
    write_json(output / "commands.json", commands)
    require(result.returncode == expected, f"Unexpected exit {result.returncode}: {prefix}")

  def build(root, label, expected=0):
    cli(root, "build", expected=expected)
    report = json.loads((root / "build/build.json").read_text(encoding="utf-8"))
    preview = ProjectPreview(root, "build").read()
    write_json(output / "previews" / f"{label}.json", preview)
    summary["steps"][label] = {"run_id": report["run_id"], "status": report["status"],
                               "targets": {item["id"]: {key: item[key] for key in ("execution", "status")}
                                           for item in report["targets"]},
                               "preview_kinds": sorted({item["kind"] for item in preview["artifacts"]})}
    require(report["inputs_consistent"], f"Unstable inputs during {label}")
    return report, preview

  def all_cached(report):
    return all(item["execution"] == "cached" for item in report["targets"])

  try:
    for root in projects:
      cli(root, "lock")
    baseline, preview = build(first, "baseline")
    require(EXPECTED_KINDS <= {item["kind"] for item in preview["artifacts"]}, "Missing project preview kinds")
    require(all(item["current"] and item["available"] for item in preview["artifacts"]), "Initial previews are not current")
    require(all(item["execution"] == "executed" for item in baseline["targets"]), "First build unexpectedly reused another project")
    old_bytes = (first / "build/runs" / baseline["run_id"] / "build.json").read_bytes()
    for item in preview["artifacts"]:
      if item["kind"] != "scene":
        ProjectPreview(first, "build").artifact(item["run_id"], item["target"], item["id"])

    repeated, _ = build(first, "cached")
    require(all_cached(repeated), "Unchanged public CLI build did not reuse every target")
    layout = first / "assets/layout.json"
    layout_original = layout.read_bytes()
    changed = json.loads(layout_original)
    identity = next(iter(changed["objects"]))
    changed["objects"][identity]["origin"][0] += 1
    write_json(layout, changed)
    stale = ProjectPreview(first, "build").read()
    require(not stale["build"]["current"], "Source edit retained current preview status")
    write_json(output / "previews/source-changed.json", stale)
    modified, _ = build(first, "layout-changed")
    require(next(item for item in modified["targets"] if item["id"] == LAYOUT_TARGET)["execution"] == "executed",
            "Layout change did not rebuild the scene")
    require(all(item["execution"] == "cached" for item in modified["targets"] if item["id"] != LAYOUT_TARGET),
            "Layout-only change rebuilt unrelated targets")

    incomplete = json.loads(layout_original)
    incomplete["objects"].pop(identity)
    write_json(layout, incomplete)
    partial, partial_preview = build(first, "layout-partial", expected=3)
    require(next(item for item in partial["targets"] if item["id"] == LAYOUT_TARGET)["status"] == "indeterminate",
            "Missing layout input did not produce an indeterminate assessment")
    partial_scene = next(item for item in partial_preview["artifacts"] if item["kind"] == "scene")
    require(partial_scene["current"] and partial_scene["partial"] and partial_scene["available"],
            "Incomplete scene lost its current-input or partial-result identity")
    require(not partial_scene["fallback"], "A current partial scene was replaced by a historical complete scene")

    layout.write_text("{ invalid layout\n", encoding="utf-8")
    failed, old_preview = build(first, "layout-error", expected=2)
    require(next(item for item in failed["targets"] if item["id"] == LAYOUT_TARGET)["status"] == "error",
            "Malformed layout was not an explicit scene error")
    require(all(item["execution"] == "cached" and item["status"] == "pass"
                for item in failed["targets"] if item["id"] != LAYOUT_TARGET),
            "Independent build branches were lost after scene failure")
    scene = next(item for item in old_preview["artifacts"] if item["kind"] == "scene")
    require(scene["fallback"] and not scene["current"], "Historical scene silently replaced the failed current scene")

    layout.write_bytes(layout_original)
    restored, restored_preview = build(first, "restored")
    require(all_cached(restored), "Restored inputs did not reuse their original valid cache")
    require(all(item["current"] for item in restored_preview["artifacts"]), "Restored previews remained stale")
    artifact = next(item for item in restored["artifacts"] if (item.get("preview") or {}).get("kind") == "bom")
    (first / artifact["path"]).unlink()
    recovered, _ = build(first, "deleted-output-restored")
    require(all_cached(recovered) and digest(first / artifact["path"]) == artifact["digest"],
            "Cached output recovery failed")
    require((first / "build/runs" / baseline["run_id"] / "build.json").read_bytes() == old_bytes,
            "A later build overwrote the historical run manifest")
    for item in baseline["artifacts"]:
      require(digest(first / item["stored_path"]) == item["digest"], "Historical artifact changed")

    second_layout = second / "assets/layout.json"
    alternate = json.loads(second_layout.read_text(encoding="utf-8"))
    alternate["objects"][identity]["origin"][0] += 3
    write_json(second_layout, alternate)
    other, other_preview = build(second, "second-project")
    require(EXPECTED_KINDS <= {item["kind"] for item in other_preview["artifacts"]}, "Second project cannot preview the same capabilities")
    require(all(item["execution"] == "executed" for item in other["targets"]), "Second project consumed undeclared project-local cache")
    other_scene = next(item for item in other_preview["artifacts"] if item["kind"] == "scene")
    first_scene = next(item for item in restored_preview["artifacts"] if item["kind"] == "scene")
    require(other_scene["digest"] != first_scene["digest"], "Second project did not use its own geometry inputs")
    require(layout.read_bytes() == layout_original and ProjectPreview(first, "build").read()["build"]["current"],
            "Second project changed the first project's inputs or applicability")
    repeated_other, _ = build(second, "second-project-cached")
    require(all_cached(repeated_other), "Second project's own cache failed")
    require(all(digest(example / name) == value for name, value in original.items()), "Replay changed the standard example")
    summary.update(status="pass", command_count=len(commands), elapsed_s=round(sum(item["elapsed_s"] for item in commands), 3))
  except Exception as error:
    summary.update(status="error", error=str(error))
    raise
  finally:
    write_json(output / "summary.json", summary)
  print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
  main()
