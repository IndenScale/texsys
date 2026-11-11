"""Replay real capability publication, two consumers and an explicit version change."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import tomlkit


REPO = Path(__file__).resolve().parents[1]


def digest(path):
  return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output", type=Path)
  options = parser.parse_args()
  output = (options.output or REPO / "build/package-platform-poc" / uuid.uuid4().hex).resolve()
  output.mkdir(parents=True, exist_ok=False)
  author = output / "author"
  registry = output / "registry"
  cache = output / "cache"
  commands = output / "commands"
  commands.mkdir()
  os.environ["TEXSYS_CACHE_DIR"] = str(cache)
  env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
  log = []

  def cli(*args, project=None, expected=0, json_result=False):
    command = [sys.executable, "-m", "texsys", *map(str, args)]
    if project is not None:
      command.extend(["-p", str(project)])
    start = time.monotonic()
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, env=env, check=False, timeout=180)
    label = f"{len(log) + 1:02d}-{args[0]}"
    stdout, stderr = commands / f"{label}.stdout", commands / f"{label}.stderr"
    stdout.write_text(result.stdout)
    stderr.write_text(result.stderr)
    log.append({"arguments": command[3:], "project": str(project) if project else None,
                "exit_code": result.returncode, "elapsed_s": round(time.monotonic() - start, 3),
                "stdout": str(stdout.relative_to(output)), "stdout_digest": digest(stdout),
                "stderr": str(stderr.relative_to(output)), "stderr_digest": digest(stderr)})
    (output / "commands.json").write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n")
    if result.returncode != expected:
      raise RuntimeError(f"{label} returned {result.returncode}, expected {expected}: {result.stderr[-1600:]}")
    return json.loads(result.stdout) if json_result else result.stdout

  def inspect(project):
    return cli("engineering", "inspect", "--adapter", "hvac:capacity-change", project=project, json_result=True)

  # Only public capability code is published. Each project's physical inputs stay local.
  shutil.copytree(REPO / "packages", author, ignore=shutil.ignore_patterns("build", "__pycache__", "*.egg-info", "node_modules"))
  cli("package", "publish", author / "hvac-checks", "--registry", registry)
  cli("package", "publish", author / "hvac-dynamics", "--registry", registry)
  releases = cli("package", "list", "--registry", registry, json_result=True)
  for release in releases["packages"]:
    detail = cli("package", "show", release["name"], "--version", release["version"], "--registry", registry, json_result=True)
    assert not any(item["path"].startswith(("reviews/", "build/", "refs/")) for item in detail["files"])

  # Prepare a second version only in the disposable author's copy, then remove the author.
  v2 = author / "hvac-dynamics"
  manifest = tomlkit.parse((v2 / "texsys.toml").read_text())
  manifest["project"]["version"] = "0.1.1"
  manifest["package"]["description"] += "（包升级机制验收版本；物理方程保持相同）"
  (v2 / "texsys.toml").write_text(tomlkit.dumps(manifest))
  pyproject = tomlkit.parse((v2 / "pyproject.toml").read_text())
  pyproject["project"]["version"] = "0.1.1"
  (v2 / "pyproject.toml").write_text(tomlkit.dumps(pyproject))
  entry = v2 / "src/texsys_hvac_dynamics/__init__.py"
  entry.write_text(entry.read_text().replace('version = "0.1.0"', 'version = "0.1.1"'))
  cli("package", "publish", v2, "--registry", registry)
  shutil.rmtree(author)
  assert not author.exists()

  projects = [output / "project-a", output / "project-b"]
  for project in projects:
    cli("init", project=project)
    for directory in ("src", "assets"):
      shutil.copytree(REPO / "examples/hvac-dynamic-station" / directory, project / directory, dirs_exist_ok=True)
    cli("add", "hvac-checks", "--version", "0.1.0", "--registry", registry, project=project)
    cli("add", "hvac-dynamics", "--version", "0.1.0", "--registry", registry, project=project)
    # Prove a clean build can be restored after author removal.
    shutil.rmtree(project / "build")
    cli("sync", project=project)
    cli("check", project=project)
    cli("build", "--target", "hvac:physical-model", project=project)
  a, b = projects
  a_source = digest(a / "src/system.sdl.json")
  b_sdl = json.loads((b / "src/system.sdl.json").read_text())
  b_sdl["components"]["Chiller"]["behavior"]["parameters"]["capacity_kw"]["value"] = 60
  (b / "src/system.sdl.json").write_text(json.dumps(b_sdl, ensure_ascii=False, indent=2) + "\n")
  baseline = cli("engineering", "run", "hvac:capacity-change", project=a, json_result=True)
  failed = cli("engineering", "run", "hvac:capacity-change", project=b, expected=1, json_result=True)
  assert baseline["result"]["assessment"] == "pass" and failed["result"]["assessment"] == "fail"
  assert baseline["toolchain_digest"] == failed["toolchain_digest"]
  cli("engineering", "accept", "hvac:capacity-change", baseline["run_id"], "--actor", "能力包机制验收",
      "--rationale", "只接受预设概念工况以验证版本失效，不代表真实设备接受。", project=a)
  cli("engineering", "accept", "hvac:capacity-change", failed["run_id"], "--actor", "能力包机制验收",
      "--rationale", "失败结果必须拒绝。", project=b, expected=2)
  test_a = cli("package", "test", "hvac-dynamics", project=a, json_result=True)
  test_b = cli("package", "test", "hvac-checks", project=b, json_result=True)
  assert test_a["status"] == test_b["status"] == "pass"
  before_a = (a / "texsys.lock").read_bytes()
  before_b = (b / "texsys.lock").read_bytes()
  cli("sync", project=a)
  assert (a / "texsys.lock").read_bytes() == before_a  # v0.1.1 exists, sync keeps v0.1.0.
  request = output / "upgrade.json"
  request.write_text(json.dumps({"action": "update", "name": "hvac-dynamics", "version": "0.1.1"}))
  plan = cli("package", "plan", request, project=a, json_result=True)
  cli("package", "apply", request, "--plan", plan["plan"], project=a, json_result=True)
  stale = inspect(a)
  assert stale["latest"]["freshness"] != "current"
  assert stale["reviews"][0]["state"] == "needs-review"
  assert (b / "texsys.lock").read_bytes() == before_b
  assert digest(a / "src/system.sdl.json") == a_source
  upgraded = cli("engineering", "run", "hvac:capacity-change", project=a, json_result=True)
  assert upgraded["result"]["assessment"] == "pass"
  assert upgraded["toolchain_digest"] != baseline["toolchain_digest"]
  assert upgraded["acceptance"] == "unreviewed"
  # The same IDE plugin must run each project's own version, without importing
  # its private implementation dependencies into the engineering process.
  from texsys.studio.runtime import StudioRuntime
  studio_checks = []
  for project, expected_version in ((a, "0.1.1"), (b, "0.1.0")):
    studio = StudioRuntime(project)
    try:
      state = studio.state()
      job = studio.submit({"plugin": "texsys:hvac", "action": "check", "options": {},
                           "revision": state["project"]["revision"]})
      deadline = time.monotonic() + 60
      while time.monotonic() < deadline:
        current = next(item for item in studio.state()["jobs"] if item["id"] == job["id"])
        if current["state"] != "running":
          break
        time.sleep(.05)
      assert current["state"] == "completed", current
      response = json.loads((project / "build/studio/jobs" / job["id"] / "response.json").read_text())
      runtime = next(item for item in response["runtime"] if item["name"] == "hvac-dynamics")
      assert runtime["version"] == expected_version
      assert response["status"] == "pass"
      studio_checks.append({"project": project.name, "job_id": job["id"], "status": response["status"],
                            "runtime": runtime})
    finally:
      studio.close()
  # Use verified local cache while the registry is absent.
  offline_registry = output / "registry-offline"
  registry.rename(offline_registry)
  try:
    shutil.rmtree(b / "build")
    cli("sync", project=b)
    cli("check", project=b)
  finally:
    offline_registry.rename(registry)
  request.write_text(json.dumps({"action": "update", "name": "hvac-dynamics", "version": "0.1.0"}))
  rollback = cli("package", "plan", request, project=a, json_result=True)
  cli("package", "apply", request, "--plan", rollback["plan"], project=a, json_result=True)
  restored = cli("engineering", "run", "hvac:capacity-change", project=a, json_result=True)
  assert restored["result"]["assessment"] == "pass"
  assert restored["toolchain_digest"] == baseline["toolchain_digest"]
  status = cli("package", "status", project=a, json_result=True)
  from texsys.studio.library_sources import library_index, reference_library_entry
  index = library_index(a, "src", [])
  sources = {item["name"]: item for item in index["sources"]}
  assert set(sources) == {"hvac-equipment", "structure"}, sources
  equipment = sources["hvac-equipment"]
  selected = next(item for item in index["library"] if item.get("source") == equipment["id"] and item["owner"] is None)
  definition = index["definitions"][selected["definition"]]
  before_dependencies = tomlkit.parse((a / "texsys.toml").read_text())["dependencies"].unwrap()
  before_lock = (a / "texsys.lock").read_bytes()
  reference = reference_library_entry(a, {"folder": equipment["path"], "digest": equipment["digest"],
                                          "entry": definition["entry"]})
  assert tomlkit.parse((a / "texsys.toml").read_text())["dependencies"].unwrap() == before_dependencies
  assert (a / "texsys.lock").read_bytes() == before_lock
  summary = {"status": "pass", "scope": "capability-package-distribution-and-project-isolation",
             "source_removed": not author.exists(), "offline_cache_restored": True,
             "project_inputs_isolated": True, "plain_sync_kept_version": True,
             "upgrade_invalidated_acceptance": True, "rollback_reproduced_toolchain": True,
             "ide_project_versions_isolated": True, "studio_checks": studio_checks,
             "component_sources": sorted(sources), "component_reference": reference,
             "component_reference_kept_dependencies": True,
             "baseline": {k: baseline[k] for k in ("run_id", "toolchain_digest", "evidence_digest")},
             "failed": {k: failed[k] for k in ("run_id", "toolchain_digest", "evidence_digest")},
             "upgraded": {k: upgraded[k] for k in ("run_id", "toolchain_digest", "evidence_digest")},
             "restored": {k: restored[k] for k in ("run_id", "toolchain_digest", "evidence_digest")},
             "package_tests": [test_a, test_b], "package_status": status,
             "commands": len(log), "command_elapsed_s": round(sum(item["elapsed_s"] for item in log), 3),
             "script_digest": digest(Path(__file__)),
             "limitations": ["概念模型与自动化接受，不是现场性能验证。", "宿主Python与SciPy等第三方依赖预先具备；不是完整离线环境还原。",
                             "0.1.1是隔离作者副本中的版本升级机制用例，未改变物理方程，也未发布到公共仓库。"],
             "finished_at": datetime.now(timezone.utc).isoformat()}
  (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
  print(json.dumps({"status": "pass", "output": str(output), "commands": len(log)}, ensure_ascii=False))


if __name__ == "__main__":
  main()
