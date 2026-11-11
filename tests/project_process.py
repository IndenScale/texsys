"""Read real project results after executing outside pytest's host imports."""

import json
from pathlib import Path
from types import SimpleNamespace

from texsys.project_operations import run_project_operation


def build_project(root, **options):
  root = Path(root).resolve()
  result = run_project_operation(root, "build", options)
  report = Path(result.pop("report"))
  document = json.loads(report.read_text())
  artifacts = {root / item["path"] for item in document["artifacts"]}
  return SimpleNamespace(**result, report=report, items=tuple(sorted(artifacts)))


def check_project(root):
  return SimpleNamespace(**run_project_operation(Path(root), "check"))
