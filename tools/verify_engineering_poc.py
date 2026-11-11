"""Replay the FWS change loop through public CLI commands in an isolated copy."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


REPO = Path(__file__).resolve().parents[1]
ADAPTER = "datacenter:fws-change"


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output", type=Path, help="New output directory; existing paths are refused.")
  options = parser.parse_args()
  output = (options.output or REPO / "build/engineering-poc" / uuid.uuid4().hex).resolve()
  output.mkdir(parents=True, exist_ok=False)
  root = output / "project"
  shutil.copytree(REPO / "examples/datacenter-hybrid-unit", root,
                  ignore=shutil.ignore_patterns("build", "reviews", ".texsys-write.lock"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  commands = output / "commands"
  commands.mkdir()
  log = []

  def cli(*arguments, expected=0):
    started = time.monotonic()
    process = subprocess.run([sys.executable, "-m", "texsys", *arguments, "-p", str(root)],
                             capture_output=True, text=True, cwd=REPO, check=False)
    index = len(log) + 1
    path = commands / f"{index:02d}-{arguments[0]}-{arguments[1] if len(arguments) > 1 else 'result'}.json"
    path.write_text(process.stdout)
    if process.stderr:
      path.with_suffix(".stderr.txt").write_text(process.stderr)
    log.append({"arguments": arguments, "exit_code": process.returncode,
                "elapsed_s": round(time.monotonic() - started, 3), "stdout": str(path.relative_to(output))})
    (output / "commands.json").write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n")
    if process.returncode != expected:
      raise RuntimeError(f"Unexpected exit {process.returncode}; expected {expected}. Inspect {path}.")
    return json.loads(process.stdout) if arguments[0] == "engineering" else process.stdout

  def inspect_state():
    return cli("engineering", "inspect", "--adapter", ADAPTER)

  def run(expected=0):
    return cli("engineering", "run", ADAPTER, expected=expected)

  def accept(record, expected=0):
    return cli("engineering", "accept", ADAPTER, record["run_id"],
               "--actor", "自动化 CLI POC 验收",
               "--rationale", "只验证概念设施水与证据机制，不代表真实工程签核。", expected=expected)

  def change(state, values):
    request = {"adapter": ADAPTER, "revision": state["revision"],
               "changes": [{"input_id": key, "value": value} for key, value in values.items()]}
    path = output / f"change-{len(log):02d}.json"
    path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    plan = cli("engineering", "plan", str(path))
    assert plan["status"] == "pass"
    applied = cli("engineering", "apply", str(path), "--plan", plan["plan"])
    assert applied["applied"]

  cli("lock")
  initial = inspect_state()
  baseline = run()
  assert baseline["execution"] == "completed" and baseline["result"]["assessment"] == "pass"
  accept(baseline)
  change(initial, {"fws.spine-x": 46.5, "fws.row-diameter": .05})
  stale = inspect_state()
  assert stale["latest"]["freshness"] == "inputs-changed"
  assert stale["reviews"][0]["state"] == "needs-review"
  failed = run(expected=1)
  assert failed["execution"] == "completed" and failed["result"]["assessment"] == "fail"
  accept(failed, expected=2)
  failures = [item for item in failed["result"]["checks"] if item["status"] == "fail"]
  assert failures and all(item["actual"] < item["limit"] for item in failures)
  old_extraction = {item["id"]: item for item in baseline["result"]["extraction"]}
  geometry_changes = [item["id"] for item in failed["result"]["extraction"]
                      if item["id"].startswith("length:") and item["value"] != old_extraction[item["id"]]["value"]]
  assert geometry_changes
  change(inspect_state(), {"fws.spine-x": 46.4, "fws.row-diameter": .16})
  repaired = run()
  assert repaired["result"]["checks"] == baseline["result"]["checks"]
  assert repaired["acceptance"] == "unreviewed"
  review = accept(repaired)
  final = inspect_state()
  assert final["latest"]["acceptance"] == "accepted"
  assert review["evidence_digest"] == repaired["evidence_digest"]
  summary = {"status": "pass", "created_at": datetime.now(timezone.utc).isoformat(),
             "adapter": ADAPTER, "baseline_run": baseline["run_id"], "failed_run": failed["run_id"],
             "repaired_run": repaired["run_id"], "baseline_revision": baseline["revision"],
             "failed_revision": failed["revision"], "repaired_revision": repaired["revision"],
             "toolchain_digest": repaired["toolchain_digest"], "evidence_digest": repaired["evidence_digest"],
             "review": review["path"], "failed_check_count": len(failures), "first_failure": failures[0],
             "changed_route_lengths": len(geometry_changes), "commands": len(log),
             "limitations": repaired["result"]["limitations"]}
  (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
  print(json.dumps({"status": "pass", "output": str(output), "summary": str(output / "summary.json")}, indent=2))


if __name__ == "__main__":
  main()
