"""CLI and authenticated Studio use the same change and evidence authority."""

from io import BytesIO
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from texsys import engineering
from texsys.cli import app
from texsys.packages import TexsysPackage
from texsys.project import initialize_project
from texsys.studio import server
from texsys.studio.runtime import StudioRuntime


class InterfaceAdapter:
  id, name, version = "test:capacity", "接口验收", "1"

  def supports(self, root):
    return True

  def inputs(self, root):
    return [{"id": "demand", "label": "需求", "file": "src/demand.json", "pointer": "/value",
             "object_ref": "Load", "unit": "kW", "value": 0, "min": 0, "max": 100}]

  def impact(self, root, changes):
    return [{"label": "供电容量", "object_ref": "Load", "reason": "需求变化影响容量判据"}]

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
  monkeypatch.setattr(engineering, "installed_packages", lambda: (InterfacePackage(),))
  monkeypatch.setattr(engineering, "_worker_call", engineering._dispatch)
  return tmp_path


def test_cli_reports_engineering_failure_and_rejects_accepting_it(engineering_project):
  root, runner = engineering_project, CliRunner()

  def cli(*args):
    result = runner.invoke(app, ["engineering", *args, "-p", str(root)])
    return result.exit_code, json.loads(result.output)

  code, state = cli("inspect")
  assert code == 0 and state["selected"]["id"] == "test:capacity"
  code, baseline = cli("run", "test:capacity")
  assert code == 0 and baseline["result"]["assessment"] == "pass"
  request = {"adapter": "test:capacity", "revision": state["revision"],
             "changes": [{"input_id": "demand", "value": 20}]}
  file = root / "request.json"
  file.write_text(json.dumps(request))
  code, plan = cli("plan", str(file))
  assert code == 0 and plan["changes"][0]["before"] == 5
  assert cli("apply", str(file), "--plan", "tampered")[0] == 2
  assert json.loads((root / "src/demand.json").read_text())["value"] == 5
  assert cli("apply", str(file), "--plan", plan["plan"])[0] == 0
  assert cli("apply", str(file), "--plan", plan["plan"])[0] == 3
  code, failed = cli("run", "test:capacity")
  assert code == 1 and failed["execution"] == "completed" and failed["result"]["verification"] == "pass"
  assert cli("accept", "test:capacity", failed["run_id"], "--actor", "Interface test", "--rationale", "Fail")[0] == 2
  assert cli("inspect")[1]["runs"][1]["freshness"] == "inputs-changed"


def test_http_checks_session_and_shares_apply_run_and_accept(engineering_project, monkeypatch):
  root = engineering_project
  runtime = StudioRuntime(root, plugins={})
  monkeypatch.setattr(server, "StudioServer", lambda address, handler, terminals: handler)
  handler_type = server.create_server(runtime, frontend=root, token="engineering-test")

  def request(path="/api/engineering", body=None, authorized=True, origin=None):
    handler = object.__new__(handler_type)
    handler.server = SimpleNamespace(server_port=8710)
    handler.headers = {"Host": "127.0.0.1:8710"}
    if authorized:
      handler.headers["X-Tex-Studio-Token"] = "engineering-test"
    if origin:
      handler.headers["Origin"] = origin
    response = []
    handler._send = lambda status, payload: response.extend([status, payload])
    handler.path = path
    if body is None:
      handler.do_GET()
    else:
      payload = json.dumps(body).encode()
      handler.headers["Content-Length"] = str(len(payload))
      handler.rfile = BytesIO(payload)
      handler.do_POST()
    return response

  try:
    assert request(authorized=False)[0] == 403
    assert request(origin="https://elsewhere.example")[0] == 403
    assert request("/api/engineering/run", {"adapter": "test:capacity"}, authorized=False)[0] == 403
    state = request()[1]
    change = {"adapter": state["selected"]["id"], "revision": state["revision"],
              "changes": [{"input_id": "demand", "value": 8}]}
    plan = request("/api/engineering/plan", change)[1]
    assert request("/api/engineering/apply", {"request": change, "plan": plan["plan"]})[1]["applied"]
    status, run = request("/api/engineering/run", {"adapter": "test:capacity"})
    assert status == 200 and run["result"]["checks"][0]["actual"] == 8
    status, review = request("/api/engineering/accept", {
      "adapter": "test:capacity", "run_id": run["run_id"], "actor": "Interface test", "rationale": "Test scope only.",
    })
    assert status == 200 and review["evidence_digest"] == run["evidence_digest"]
    assert request()[1]["latest"]["acceptance"] == "accepted"
  finally:
    runtime.close()
