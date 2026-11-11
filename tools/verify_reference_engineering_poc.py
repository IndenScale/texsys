"""Replay conditional capacity review from a reported configuration, without engineering acceptance."""

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


REPO = Path(__file__).resolve().parents[1]
ADAPTER = "hvac:reference-capacity"
STUDY = "src/reference-capacity.study.json"
SNAPSHOT = "assets/reference/cold-station-acceptance.json"
FACT_POINTER = "/configuration/per_chiller_capacity_kw/value"
FRACTIONS = ("reference.air-load-fraction", "reference.liquid-load-fraction")
METRICS = ("reference:conditional-load", "reference:available-capacity", "reference:capacity-margin")


def write_json(path, value):
  path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
  return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
  return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
  if not condition:
    raise RuntimeError(message)


def replace_value(document, pointer, value):
  parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
  current = document
  for part in parts[:-1]:
    current = current[int(part)] if isinstance(current, list) else current[part]
  key = int(parts[-1]) if isinstance(current, list) else parts[-1]
  current[key] = value


def metrics(record):
  checks = {item["id"]: item for item in record["result"]["checks"]}
  require(all(identity in checks for identity in METRICS), "Capacity review did not provide the agreed numerical checks.")
  require(all(checks[identity].get("unit") == "kW" for identity in METRICS), "Capacity metrics must be explicit kW values.")
  return {identity: checks[identity]["actual"] for identity in METRICS}


def verify_run(record, assessment, expected):
  result = record["result"]
  require(record["execution"] == "completed" and record["current"], "Expected a completed, current adapter evaluation.")
  require(result["assessment"] == assessment, f"Expected engineering assessment {assessment}.")
  require(result["solver_execution"] == "not-run" and result["arithmetic_execution"] == "completed",
          "Conditional arithmetic must not be presented as an executed physical solver.")
  actual = metrics(record)
  require(all(math.isclose(actual[identity], value, rel_tol=0, abs_tol=1e-8)
              for identity, value in zip(METRICS, expected, strict=True)), f"Unexpected capacity arithmetic: {actual}.")
  field = next((item for item in result["checks"] if item["id"] == "reference:field-verification"), None)
  require(field is not None and field["status"] == "indeterminate", "Unverified real performance must remain indeterminate.")
  require(record["acceptance"] == "unreviewed", "Reported configuration alone must not restore acceptance.")


def run_summary(record):
  return {key: record[key] for key in ("run_id", "revision", "toolchain_digest", "evidence_digest", "execution")} | {
    "assessment": record["result"]["assessment"], "solver_execution": record["result"]["solver_execution"],
    "arithmetic_execution": record["result"]["arithmetic_execution"], "checks": record["result"]["checks"],
    "current": record["current"], "acceptance_at_run": record["acceptance"], "path": record["path"],
  }


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output", type=Path, help="New output directory; existing paths are refused.")
  options = parser.parse_args()
  output = (options.output or REPO / "build/reference-engineering-poc" / uuid.uuid4().hex).resolve()
  output.mkdir(parents=True, exist_ok=False)
  root = output / "project"
  example = REPO / "examples/hvac-cold-station"
  commands = output / "commands"
  commands.mkdir()
  edits = output / "source-edits"
  edits.mkdir()
  log, operations = [], []
  summary = {
    "status": "running", "workflow_verification": "running", "engineering_acceptance": "not-permitted",
    "created_at": datetime.now(timezone.utc).isoformat(), "adapter": ADAPTER,
    "scope": "终验报告声明配置的条件 0D 容量审查与证据机制验收；不是物理仿真、真实性能通过或工程接受。",
    "success_meaning": "status=pass 仅表示预期的未定、失败、失效与拒绝接受流程均已验证；不是任何方案的真实工程 pass。",
    "original_example": str(example), "all_project_operations_in_isolated_copies": True,
    "replay_script": {"path": Path(__file__).resolve().relative_to(REPO).as_posix(), "sha256": digest(Path(__file__))},
  }

  def operation(value):
    operations.append({"at": datetime.now(timezone.utc).isoformat(), **value})
    write_json(output / "operations.json", operations)

  def cli(*arguments, project=root, expected=0):
    require(project.is_relative_to(output), "Verification commands must run against an isolated output copy.")
    command = [sys.executable, "-m", "texsys", *arguments, "-p", str(project)]
    started_at, started = datetime.now(timezone.utc).isoformat(), time.monotonic()
    process = subprocess.run(command, capture_output=True, text=True, cwd=REPO, check=False)
    elapsed = round(time.monotonic() - started, 3)
    name = f"{len(log) + 1:02d}-{arguments[0]}-{arguments[1] if len(arguments) > 1 else 'result'}"
    stdout = commands / f"{name}.{'json' if arguments[0] == 'engineering' else 'txt'}"
    stderr = commands / f"{name}.stderr.txt"
    stdout.write_text(process.stdout, encoding="utf-8")
    stderr.write_text(process.stderr, encoding="utf-8")
    entry = {"command": command, "cwd": str(REPO), "project": project.relative_to(output).as_posix(),
             "isolated_copy": True, "started_at": started_at, "elapsed_s": elapsed,
             "exit_code": process.returncode, "expected_exit_code": expected,
             "stdout": stdout.relative_to(output).as_posix(), "stdout_sha256": digest(stdout),
             "stderr": stderr.relative_to(output).as_posix(), "stderr_sha256": digest(stderr)}
    log.append(entry)
    write_json(output / "commands.json", log)
    operation({"operation": "cli", "project": entry["project"], "isolated_copy": True, "command_number": len(log)})
    require(process.returncode == expected, f"Unexpected exit {process.returncode}; expected {expected}. Inspect {stdout}.")
    result = json.loads(process.stdout) if arguments[0] == "engineering" else process.stdout
    if isinstance(result, dict):
      entry.update({key: result[key] for key in ("revision", "run_id", "evidence_digest", "toolchain_digest", "plan") if key in result})
      if result.get("latest"):
        entry["latest_run"] = {key: result["latest"][key] for key in ("run_id", "revision", "evidence_digest")}
      write_json(output / "commands.json", log)
    return result

  def inspect_state(project=root):
    return cli("engineering", "inspect", "--adapter", ADAPTER, project=project)

  def run(project=root, expected=3):
    return cli("engineering", "run", ADAPTER, project=project, expected=expected)

  def reject_acceptance(record, project=root):
    response = cli("engineering", "accept", ADAPTER, record["run_id"],
                   "--actor", "自动化条件审查拒绝路径测试员",
                   "--rationale", "仅测试未定或失败证据不可接受；不授权将报告声明配置提升为真实工程通过。",
                   project=project, expected=2)
    require(response["status"] == "error", "An unverified or failed conditional review was accepted.")

  def change(state, value):
    request = {"adapter": ADAPTER, "revision": state["revision"],
               "changes": [{"input_id": identity, "value": value} for identity in FRACTIONS]}
    path = output / f"change-{len(log):02d}.json"
    write_json(path, request)
    before = (root / STUDY).read_text(encoding="utf-8")
    plan = cli("engineering", "plan", str(path))
    require(plan["status"] == "pass" and plan["impacts"], "Expected a valid change preview with declared impacts.")
    applied = cli("engineering", "apply", str(path), "--plan", plan["plan"])
    require(applied["applied"], "Expected the reviewed utilization changes to be applied.")
    after = (root / STUDY).read_text(encoding="utf-8")
    expected_document = json.loads(before)
    for item in plan["changes"]:
      require(item["file"] == STUDY, "Utilization changes must stay in the declared study source.")
      replace_value(expected_document, item["pointer"], item["after"])
    require(json.loads(after) == expected_document, "Numeric changes modified other facts, states or source endorsements.")
    name = f"fractions-{value:g}"
    (edits / f"{name}.before.json").write_text(before, encoding="utf-8")
    (edits / f"{name}.after.json").write_text(after, encoding="utf-8")
    write_json(edits / f"{name}.json", {"operation": "engineering-plan-apply", "project": "project", "file": STUDY,
      "request": path.relative_to(output).as_posix(), "plan": plan["plan"], "changes": plan["changes"],
      "before_sha256": digest(edits / f"{name}.before.json"), "after_sha256": digest(root / STUDY),
      "diff": "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{STUDY}", f"b/{STUDY}", lineterm=""))})
    require(read_json(root / STUDY)["reference"] == original_reference, "Changing utilization altered the bound reference source.")
    require(digest(root / SNAPSHOT) == original_digests[SNAPSHOT], "Changing utilization altered reported reference facts.")

  try:
    require(all((example / file).is_file() for file in (STUDY, SNAPSHOT)), "Reference fixture is not available yet.")
    original_digests = {path.relative_to(example).as_posix(): digest(path)
                        for directory in ("src", "assets") for path in sorted((example / directory).rglob("*")) if path.is_file()}
    summary["original_input_sha256"] = original_digests
    shutil.copytree(example, root, ignore=shutil.ignore_patterns("build", "reviews", ".texsys-write.lock"))
    manifest = root / "texsys.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("../../packages/", f"{REPO}/packages/"), encoding="utf-8")
    operation({"operation": "copy-project", "project": "project", "from": str(example), "isolated_copy": True,
               "manifest_adjustment": "Only the isolated copy uses absolute local package paths; original source and assets are unchanged."})
    original_reference = read_json(root / STUDY)["reference"]
    require(original_reference["path"] == SNAPSHOT and original_reference["digest"] == "sha256:" + digest(root / SNAPSHOT),
            "The baseline study must bind the current reported-configuration snapshot by its exact digest.")
    write_json(output / "reference-provenance.json", {"study_source": STUDY, "study_sha256": digest(root / STUDY),
               "reference_binding": original_reference, "snapshot_sha256": digest(root / SNAPSHOT),
               "snapshot": read_json(root / SNAPSHOT), "state_note": "保留报告引用、位置及事实状态；转录事实不升级为实测性能。"})
    cli("lock")
    initial = inspect_state()
    require(initial["selected"]["id"] == ADAPTER and not initial["dependency_error"], "Reference adapter or locked dependencies unavailable.")
    inputs = {item["id"]: item for item in initial["inputs"]}
    require(all(inputs[identity]["value"] == .6 for identity in FRACTIONS)
            and inputs["reference.available-chillers"]["value"] == 2, "Expected the .6/.6 utilization with two available chillers.")
    baseline = run()
    verify_run(baseline, "indeterminate", (117, 150, 33))
    reject_acceptance(baseline)
    summary["baseline"] = run_summary(baseline)

    change(initial, 1)
    stale = inspect_state()
    require(stale["latest"]["run_id"] == baseline["run_id"] and stale["latest"]["freshness"] == "inputs-changed",
            "Changed assumptions retained applicability of the previous result.")
    failed = run(expected=1)
    verify_run(failed, "fail", (195, 150, -45))
    margin = next(item for item in failed["result"]["checks"] if item["id"] == "reference:capacity-margin")
    require(margin["status"] == "fail" and margin["limit"] == 0 and margin["operator"] == ">=", "Capacity shortfall lacks an explicit failed margin check.")
    reject_acceptance(failed)
    summary["failed"] = run_summary(failed)

    change(inspect_state(), .6)
    repaired = run()
    verify_run(repaired, "indeterminate", (117, 150, 33))
    require(repaired["run_id"] not in {baseline["run_id"], failed["run_id"]}, "Restored assumptions reused a historical run identity.")
    checks_before = {item["id"]: (item["status"], item.get("actual")) for item in baseline["result"]["checks"]}
    checks_after = {item["id"]: (item["status"], item.get("actual")) for item in repaired["result"]["checks"]}
    require(checks_before == checks_after, "Restored assumptions did not reproduce baseline check values and verdicts.")
    reject_acceptance(repaired)
    restored = inspect_state()
    require(restored["reviews"] == [], "A review acceptance was created despite unverified real performance.")
    summary["repaired"] = run_summary(repaired)

    untrusted = output / "project-reference-mismatch"
    shutil.copytree(root, untrusted, ignore=shutil.ignore_patterns(".texsys-write.lock"))
    operation({"operation": "copy-project", "project": "project-reference-mismatch", "from": "project", "isolated_copy": True})
    copied = inspect_state(untrusted)
    require(copied["latest"]["current"] and copied["latest"]["run_id"] == repaired["run_id"], "Copied evidence should initially apply to the identical inputs.")
    snapshot = untrusted / SNAPSHOT
    before = snapshot.read_text(encoding="utf-8")
    document = json.loads(before)
    fact = document["configuration"]["per_chiller_capacity_kw"]
    original_fact = dict(fact)
    require(fact["value"] == 75, "Expected the reported per-chiller capacity fact to be 75 kW.")
    fact["value"] += 1
    after = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    (edits / "reference-mismatch.before.json").write_text(before, encoding="utf-8")
    snapshot.write_text(after, encoding="utf-8")
    (edits / "reference-mismatch.after.json").write_text(after, encoding="utf-8")
    source_edit = {"operation": "direct-reference-edit", "project": "project-reference-mismatch", "isolated_copy": True,
      "file": SNAPSHOT, "pointer": FACT_POINTER, "before": original_fact, "after": fact,
      "unchanged_reference_binding": original_reference, "before_sha256": digest(edits / "reference-mismatch.before.json"),
      "after_sha256": digest(snapshot), "reason": "故意改写独立副本中的报告转录值，保留旧引用摘要以验证来源绑定拒绝路径；不改原始资料。",
      "diff": "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{SNAPSHOT}", f"b/{SNAPSHOT}", lineterm=""))}
    write_json(edits / "reference-mismatch.json", source_edit)
    operation(source_edit)
    require(read_json(untrusted / STUDY)["reference"] == original_reference, "Mismatch test must not update the study's bound digest.")
    stale_reference = inspect_state(untrusted)
    require(stale_reference["latest"]["freshness"] == "inputs-changed", "Changed reference facts retained the old result's applicability.")
    mismatch = run(untrusted)
    result = mismatch["result"]
    require(mismatch["execution"] == "completed" and mismatch["current"] and result["assessment"] == "indeterminate"
            and result["solver_execution"] == result["arithmetic_execution"] == "not-run",
            "A mismatched source digest was used for a new calculation.")
    require(result["extraction"] == [] and any(item["code"] == "REFERENCE_DIGEST_MISMATCH" for item in result["diagnostics"]),
            "Reference digest mismatch lacks an explicit diagnostic or still publishes derived quantities.")
    require(not any(item["id"] in METRICS and item.get("actual") is not None for item in result["checks"]),
            "A source mismatch still exposes usable numerical capacity checks.")
    reject_acceptance(mismatch, untrusted)
    require(inspect_state(untrusted)["reviews"] == [], "Mismatched reference facts received an acceptance record.")
    require(all(digest(example / file) == value for file, value in original_digests.items()), "Original example inputs changed during verification.")
    summary.update(status="pass", workflow_verification="pass", mismatch=run_summary(mismatch),
                   reference_provenance="reference-provenance.json", reference_mismatch_edit="source-edits/reference-mismatch.json",
                   limitations=repaired["result"]["limitations"], engineering_outcomes=["indeterminate", "fail", "indeterminate", "indeterminate"])
  except Exception as error:
    summary.update(status="error", workflow_verification="error", first_error={"type": type(error).__name__, "message": str(error), "after_command": len(log)})
    raise
  finally:
    summary.update(commands=len(log), command_elapsed_s=round(sum(item["elapsed_s"] for item in log), 3),
                   finished_at=datetime.now(timezone.utc).isoformat())
    write_json(output / "summary.json", summary)
    print(json.dumps({"status": summary["status"], "workflow_verification": summary["workflow_verification"],
                      "engineering_acceptance": "not-permitted", "output": str(output), "summary": str(output / "summary.json")}, indent=2))


if __name__ == "__main__":
  main()
