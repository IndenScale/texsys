"""Small immutable build records for preview endpoint and USD reader tests."""

from pathlib import Path
import platform

from texsys.build_records import distribution_versions, input_records, snapshot
from texsys.evidence import canonical_digest, file_digest, write_json


def preview_record(root, run, artifacts=(), *, previous=None, status="pass", complete=True, build="build", diagnostics=()):
  run_root = root / build / "runs" / run
  run_root.mkdir(parents=True)
  inputs = input_records(root, "src", "assets")
  snapshot(root, run_root, inputs)
  records = []
  for identifier, path, preview in artifacts:
    records.append({"id": identifier, "path": path, "target": "test:preview", "digest": file_digest(root / path),
                    "complete": complete, "partial": not complete, "preview": preview})
  records = snapshot(root, run_root, records)
  report = {"schema_version": 3, "run_id": run, "previous_run_id": previous, "status": status,
            "inputs": inputs, "inputs_consistent": True,
            "toolchain": {"sources": [], "python": platform.python_version(), "platform": platform.platform(),
                          "distributions": distribution_versions()},
            "targets": [{"id": "test:preview", "status": status, "execution": "executed", "diagnostics": list(diagnostics)}],
            "artifacts": records}
  save_record(root, report, build)
  return report


def save_record(root: Path, report: dict, build="build"):
  report["record_digest"] = canonical_digest({key: value for key, value in report.items() if key != "record_digest"})
  write_json(root / build / "runs" / report["run_id"] / "build.json", report)
  write_json(root / build / "build.json", report)
