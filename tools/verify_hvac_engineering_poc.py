"""Replay the concept HVAC capacity and unresolved-input loops through public CLI commands."""

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


REPO = Path(__file__).resolve().parents[1]
ADAPTER = "hvac:capacity-change"
INPUT = "hvac.chiller-capacity"
CAPACITY_POINTER = "/components/Chiller/behavior/parameters/capacity_kw"
SOURCE = "src/system.sdl.json"


def write_json(path, value):
  path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
  return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
  if not condition:
    raise RuntimeError(message)


def binding(document):
  return document["components"]["Chiller"]["behavior"]["parameters"]["capacity_kw"]


def run_summary(record):
  return {key: record[key] for key in ("run_id", "revision", "toolchain_digest", "evidence_digest", "execution")} | {
    "assessment": record["result"]["assessment"],
    "solver_execution": record["result"]["solver_execution"],
    "current": record["current"], "acceptance_at_run": record["acceptance"], "path": record["path"],
  }


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output", type=Path, help="New output directory; existing paths are refused.")
  options = parser.parse_args()
  output = (options.output or REPO / "build/hvac-engineering-poc" / uuid.uuid4().hex).resolve()
  output.mkdir(parents=True, exist_ok=False)
  root = output / "project"
  example = REPO / "examples/hvac-dynamic-station"
  original_source_digest = digest(example / SOURCE)
  shutil.copytree(example, root, ignore=shutil.ignore_patterns("build", "reviews", ".texsys-write.lock"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text(encoding="utf-8").replace("../../packages/", f"{REPO}/packages/"), encoding="utf-8")
  commands = output / "commands"
  commands.mkdir()
  log = []
  summary = {"status": "running", "created_at": datetime.now(timezone.utc).isoformat(),
             "adapter": ADAPTER, "scope": "概念冷站正常负荷阶跃与证据机制验证，不代表真实工程或设备选型签核。",
             "original_example": str(example), "original_source_sha256": original_source_digest}

  def cli(*arguments, project=root, expected=0):
    command = [sys.executable, "-m", "texsys", *arguments, "-p", str(project)]
    started_at, started = datetime.now(timezone.utc).isoformat(), time.monotonic()
    process = subprocess.run(command, capture_output=True, text=True, cwd=REPO, check=False)
    elapsed = round(time.monotonic() - started, 3)
    name = f"{len(log) + 1:02d}-{arguments[0]}-{arguments[1] if len(arguments) > 1 else 'result'}"
    stdout = commands / f"{name}.{'json' if arguments[0] == 'engineering' else 'txt'}"
    stderr = commands / f"{name}.stderr.txt"
    stdout.write_text(process.stdout, encoding="utf-8")
    stderr.write_text(process.stderr, encoding="utf-8")
    entry = {"command": command, "cwd": str(REPO), "project": str(project.relative_to(output)),
             "started_at": started_at, "elapsed_s": elapsed, "exit_code": process.returncode,
             "expected_exit_code": expected, "stdout": str(stdout.relative_to(output)),
             "stdout_sha256": digest(stdout), "stderr": str(stderr.relative_to(output)),
             "stderr_sha256": digest(stderr)}
    log.append(entry)
    write_json(output / "commands.json", log)
    require(process.returncode == expected,
            f"Unexpected exit {process.returncode}; expected {expected}. Inspect {stdout}.")
    result = json.loads(process.stdout) if arguments[0] == "engineering" else process.stdout
    if isinstance(result, dict):
      entry.update({key: result[key] for key in ("revision", "run_id", "evidence_digest", "toolchain_digest",
                                                "decision_digest", "plan") if key in result})
      if result.get("latest"):
        entry["latest_run"] = {key: result["latest"][key] for key in ("run_id", "revision", "evidence_digest")}
      write_json(output / "commands.json", log)
    return result

  def inspect_state(project=root):
    return cli("engineering", "inspect", "--adapter", ADAPTER, project=project)

  def run(project=root, expected=0):
    return cli("engineering", "run", ADAPTER, project=project, expected=expected)

  def accept(record, project=root, expected=0):
    return cli("engineering", "accept", ADAPTER, record["run_id"],
               "--actor", "自动化 HVAC CLI 概念验证测试员",
               "--rationale", "仅验证正常负荷阶跃概念模型与证据机制；不代表真实工程、现场或设备选型签核。",
               project=project, expected=expected)

  def change(state, value):
    request = {"adapter": ADAPTER, "revision": state["revision"], "changes": [{"input_id": INPUT, "value": value}]}
    path = output / f"change-{len(log):02d}.json"
    write_json(path, request)
    plan = cli("engineering", "plan", str(path))
    require(plan["status"] == "pass" and plan["impacts"], "Expected a valid change preview with declared impacts.")
    applied = cli("engineering", "apply", str(path), "--plan", plan["plan"])
    require(applied["applied"], "Expected the reviewed capacity change to be applied.")
    require(binding(json.loads((root / SOURCE).read_text(encoding="utf-8"))) == {**original_binding, "value": value},
            "Numeric transaction changed the capacity's original source, unit or state.")

  try:
    cli("lock")
    initial = inspect_state()
    require(initial["selected"]["id"] == ADAPTER and not initial["dependency_error"], "HVAC adapter or dependencies unavailable.")
    original_binding = binding(json.loads((root / SOURCE).read_text(encoding="utf-8")))
    require(original_binding["value"] == 120 and original_binding["state"] == "assumed", "Expected the 120 kW concept baseline.")
    baseline = run()
    require(baseline["execution"] == "completed" and baseline["result"]["assessment"] == "pass"
            and baseline["result"]["solver_execution"] == "completed" and baseline["current"], "Baseline did not pass a current solver run.")
    baseline_review = accept(baseline)
    require(baseline_review["state"] == "accepted", "Baseline acceptance was not recorded.")
    summary["baseline"] = run_summary(baseline) | {"review": baseline_review["path"]}
    change(initial, 60)
    stale = inspect_state()
    require(stale["latest"]["freshness"] == "inputs-changed"
            and all(review["state"] == "needs-review" for review in stale["reviews"]), "Capacity change retained an old acceptance.")
    failed = run(expected=1)
    require(failed["execution"] == "completed" and failed["result"]["assessment"] == "fail"
            and failed["result"]["solver_execution"] == "completed" and failed["current"], "60 kW did not produce a completed engineering failure.")
    require(accept(failed, expected=2)["status"] == "error", "A failing run was accepted.")
    failures = [check for check in failed["result"]["checks"] if check["status"] == "fail"]
    require(bool(failures), "Failure has no failed engineering checks.")
    temperature = next(check for check in failures if check.get("evidence", {}).get("variable") == "ColdStation.LOAD-01.outlet_temperature_c")
    first, peak = temperature["evidence"]["first_violation_time_s"], temperature["evidence"]["peak_time_s"]
    require(temperature["actual"] > temperature["limit"] and first is not None and peak >= first,
            "Temperature failure lacks an actual limit violation or its sampled timing.")
    summary["failed"] = run_summary(failed) | {"failed_check_count": len(failures), "first_failure": failures[0],
                                              "first_violation_time_s": first, "peak_time_s": peak}
    change(inspect_state(), 120)
    repaired = run()
    require(repaired["execution"] == "completed" and repaired["result"]["assessment"] == "pass"
            and repaired["current"] and repaired["acceptance"] == "unreviewed", "Repaired run was not a new unreviewed pass.")
    require(repaired["run_id"] != baseline["run_id"], "Rerun reused the earlier run identity.")
    old_checks = {check["id"]: (check["status"], check.get("actual")) for check in baseline["result"]["checks"]}
    new_checks = {check["id"]: (check["status"], check.get("actual")) for check in repaired["result"]["checks"]}
    require(old_checks == new_checks, "Restored capacity did not reproduce baseline check values and verdicts.")
    review = accept(repaired)
    final = inspect_state()
    require(final["latest"]["acceptance"] == "accepted" and review["evidence_digest"] == repaired["evidence_digest"],
            "Repaired acceptance is not bound to the new evidence.")
    summary["repaired"] = run_summary(repaired) | {"review": review["path"], "decision_digest": review["decision_digest"]}

    unknown_root = output / "project-unknown"
    shutil.copytree(root, unknown_root, ignore=shutil.ignore_patterns(".texsys-write.lock"))
    copied = inspect_state(unknown_root)
    require(copied["latest"]["acceptance"] == "accepted", "Copied baseline evidence is not initially current.")
    source = unknown_root / SOURCE
    before = source.read_text(encoding="utf-8")
    document = json.loads(before)
    previous_binding = dict(binding(document))
    binding(document)["state"] = "unknown"
    del binding(document)["value"]
    after = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    edits = output / "source-edits"
    edits.mkdir()
    (edits / "unknown.before.json").write_text(before, encoding="utf-8")
    source.write_text(after, encoding="utf-8")
    (edits / "unknown.after.json").write_text(after, encoding="utf-8")
    write_json(edits / "unknown.json", {"operation": "direct-source-edit", "project": "project-unknown", "file": SOURCE,
      "pointer": CAPACITY_POINTER, "before": previous_binding, "after": binding(document),
      "before_sha256": digest(edits / "unknown.before.json"), "after_sha256": digest(source),
      "reason": "显式设置待定输入并删除 value；此操作不属于当前仅支持数值修改的 engineering plan/apply 事务。",
      "diff": "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{SOURCE}", f"b/{SOURCE}", lineterm=""))})
    unresolved_state = inspect_state(unknown_root)
    require(unresolved_state["inputs"] == [] and unresolved_state["latest"]["freshness"] == "inputs-changed"
            and all(item["state"] == "needs-review" for item in unresolved_state["reviews"]),
            "Unknown capacity retained editable numeric input or an old acceptance.")
    unresolved = run(unknown_root, expected=3)
    require(unresolved["execution"] == "completed" and unresolved["result"]["assessment"] == "indeterminate"
            and unresolved["result"]["solver_execution"] == "not-run", "Unknown capacity was treated as a solved engineering result.")
    require(accept(unresolved, unknown_root, expected=2)["status"] == "error", "Unknown-input result was accepted.")
    unknown_final = inspect_state(unknown_root)
    require(unknown_final["latest"]["acceptance"] == "unreviewed"
            and all(item["state"] == "needs-review" for item in unknown_final["reviews"]), "Unknown-input run restored an earlier acceptance.")
    require("value" not in binding(json.loads(source.read_text(encoding="utf-8"))), "Unknown capacity acquired a default value.")
    require(digest(example / SOURCE) == original_source_digest, "Original example source changed during verification.")
    summary.update(status="pass", unresolved=run_summary(unresolved) | {"source_edit": "source-edits/unknown.json"},
                   limitations=repaired["result"]["limitations"])
  except Exception as error:
    summary.update(status="error", first_error={"type": type(error).__name__, "message": str(error), "after_command": len(log)})
    raise
  finally:
    summary.update(commands=len(log), command_elapsed_s=round(sum(item["elapsed_s"] for item in log), 3),
                   finished_at=datetime.now(timezone.utc).isoformat())
    write_json(output / "summary.json", summary)
    print(json.dumps({"status": summary["status"], "output": str(output), "summary": str(output / "summary.json")}, indent=2))


if __name__ == "__main__":
  main()
