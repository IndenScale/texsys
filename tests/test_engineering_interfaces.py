"""Engineering CLI evaluates edited source files and reports structured results."""

import json

import pytest
from typer.testing import CliRunner

from texsys import engineering
from texsys.cli import app
from texsys.packages import TexsysPackage
from texsys.project import initialize_project


class InterfaceAdapter:
  id, name, version = "test:capacity", "接口验收", "1"

  def supports(self, root):
    return True

  def inputs(self, root):
    return [{"id": "demand", "label": "需求", "file": "src/demand.json", "pointer": "/value",
             "object_ref": "Load", "unit": "kW", "value": 0, "min": 0, "max": 100}]

  def evaluate(self, root):
    value = json.loads((root / "src/demand.json").read_text())["value"]
    status = "pass" if value <= 10 else "fail"
    return {"assessment": status, "verification": "pass", "checks": [
      {"id": "capacity", "object_ref": "Load", "label": "容量", "status": status,
       "actual": value, "limit": 10, "unit": "kW"}], "limitations": ["Interface test only."]}


class InterfacePackage(TexsysPackage):
  name = "interface-test"

  def engineering_adapters(self):
    return (InterfaceAdapter(),)


@pytest.fixture
def engineering_project(tmp_path, monkeypatch):
  initialize_project(tmp_path)
  (tmp_path / "src/demand.json").write_text('{"value": 5}\n')
  monkeypatch.setattr(engineering, "project_packages", lambda root: (InterfacePackage(),))
  monkeypatch.setattr(engineering, "_worker_call", engineering._dispatch)
  return tmp_path


def test_cli_reports_source_edit_failure_and_preserves_evidence(engineering_project):
  root, runner = engineering_project, CliRunner()

  def cli(*args):
    result = runner.invoke(app, ["engineering", *args, "-p", str(root)])
    return result.exit_code, json.loads(result.output)

  code, state = cli("inspect")
  assert code == 0 and state["selected"]["id"] == "test:capacity"
  code, baseline = cli("run", "test:capacity")
  assert code == 0 and baseline["result"]["assessment"] == "pass"
  assert (root / baseline["path"]).is_file()
  (root / "src/demand.json").write_text('{"value":20}\n')
  code, failed = cli("run", "test:capacity")
  assert code == 1 and failed["execution"] == "completed" and failed["result"]["verification"] == "pass"
  assert failed["result"]["checks"][0]["actual"] == 20
  assert cli("inspect")[1]["runs"][1]["freshness"] == "inputs-changed"
  (root / "src/demand.json").write_text('{"value":5}\n')
  code, repaired = cli("run", "test:capacity")
  assert code == 0 and repaired["result"]["assessment"] == "pass"
  assert repaired["run_id"] != baseline["run_id"]
