"""Exercise the Studio ACP host with a scripted agent process."""

import json
import os
from pathlib import Path
import sys
import textwrap
import time

import pytest

from texsys.studio.agent import AgentService, load_agents

pytestmark = pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="Requires POSIX processes")

FAKE_AGENT = textwrap.dedent(r'''
  import json, os, sys, threading
  lock = threading.Lock()
  pending = {}
  counter = [100]
  state = {"cancel": threading.Event()}

  def send(message):
    with lock:
      sys.stdout.write(json.dumps(message) + "\n")
      sys.stdout.flush()

  def call(method, params):
    counter[0] += 1
    identifier = counter[0]
    event = threading.Event()
    pending[identifier] = {"event": event}
    send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
    event.wait()
    return pending.pop(identifier)["message"]

  def update(session, payload):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": payload}})

  def prompt(identifier, params):
    session = params["sessionId"]
    text = params["prompt"][0]["text"]
    links = [block["uri"] for block in params["prompt"][1:] if block["type"] == "resource_link"]
    cwd = os.getcwd()
    if text == "wait":
      state["cancel"].wait(10)
      send({"jsonrpc": "2.0", "id": identifier, "result": {"stopReason": "cancelled"}})
      return
    if text == "crash":
      os._exit(3)
    update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello "}})
    update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "links=" + str(len(links))}})
    update(session, {"sessionUpdate": "plan", "entries": [{"content": "edit", "priority": "high", "status": "in_progress"}]})
    read = call("fs/read_text_file", {"sessionId": session, "path": os.path.join(cwd, "notes.txt"), "line": 2, "limit": 1})
    outside = call("fs/read_text_file", {"sessionId": session, "path": "/etc/hosts"})
    git = call("fs/read_text_file", {"sessionId": session, "path": os.path.join(cwd, ".git", "config")})
    update(session, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Write notes", "kind": "edit", "status": "pending"})
    permission = call("session/request_permission", {"sessionId": session, "toolCall": {"toolCallId": "t1", "title": "Write notes"},
                      "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                                  {"optionId": "reject", "name": "Reject", "kind": "reject_once"}]})
    outcome = permission["result"]["outcome"]
    if outcome.get("optionId") == "allow":
      call("fs/write_text_file", {"sessionId": session, "path": os.path.join(cwd, "src", "new.txt"), "content": "created\n"})
    terminal = call("terminal/create", {"sessionId": session, "command": "echo", "args": ["terminal-ok"]})
    terminal_id = terminal["result"]["terminalId"]
    exit_status = call("terminal/wait_for_exit", {"sessionId": session, "terminalId": terminal_id})
    output = call("terminal/output", {"sessionId": session, "terminalId": terminal_id})
    call("terminal/release", {"sessionId": session, "terminalId": terminal_id})
    update(session, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
    report = {
      "read": read["result"]["content"],
      "outside": outside.get("error", {}).get("message", ""),
      "git": git.get("error", {}).get("message", ""),
      "permission": outcome,
      "exit": exit_status["result"],
      "output": output["result"]["output"],
    }
    update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": json.dumps(report)}})
    send({"jsonrpc": "2.0", "id": identifier, "result": {"stopReason": "end_turn"}})

  for line in sys.stdin:
    message = json.loads(line)
    if "method" not in message:
      pending[message["id"]]["message"] = message
      pending[message["id"]]["event"].set()
      continue
    method, params, identifier = message["method"], message.get("params", {}), message.get("id")
    if method == "initialize":
      send({"jsonrpc": "2.0", "id": identifier, "result": {"protocolVersion": 1,
            "agentInfo": {"name": "fake", "title": "Fake Agent", "version": "1.0"},
            "agentCapabilities": {"sessionCapabilities": {"close": {}}}, "authMethods": []}})
    elif method == "session/new":
      assert params["cwd"] == os.getcwd(), params
      send({"jsonrpc": "2.0", "id": identifier, "result": {"sessionId": "s-1", "configOptions": [
        {"id": "model", "name": "Model", "type": "select", "currentValue": "a",
         "options": [{"value": "a", "name": "A"}, {"value": "b", "name": "B"}]}]}})
    elif method == "session/set_config_option":
      send({"jsonrpc": "2.0", "id": identifier, "result": {"configOptions": [
        {"id": "model", "name": "Model", "type": "select", "currentValue": params["value"],
         "options": [{"value": "a", "name": "A"}, {"value": "b", "name": "B"}]}]}})
    elif method == "session/prompt":
      threading.Thread(target=prompt, args=(identifier, params), daemon=True).start()
    elif method == "session/cancel":
      state["cancel"].set()
    elif method == "session/close":
      send({"jsonrpc": "2.0", "id": identifier, "result": {}})
''')


@pytest.fixture
def service(tmp_path, monkeypatch):
  project = tmp_path / "project"
  (project / ".git").mkdir(parents=True)
  (project / ".git" / "config").write_text("[core]\n")
  (project / "notes.txt").write_text("first\nsecond\nthird\n")
  agent = tmp_path / "fake_agent.py"
  agent.write_text(FAKE_AGENT)
  config = tmp_path / "config" / "texsys" / "studio-agents.json"
  config.parent.mkdir(parents=True)
  config.write_text(json.dumps({"agents": {
    "fake": {"name": "Fake", "command": [sys.executable, str(agent)], "env": {"FAKE_TOKEN": "secret-value"}},
    "gemini": {"disabled": True},
  }}))
  monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent.parent))
  monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
  agents = AgentService(project)
  try:
    yield agents
  finally:
    agents.close()


def wait_for(session, predicate, timeout=20):
  deadline = time.monotonic() + timeout
  seen = []
  after = 0
  while time.monotonic() < deadline:
    batch = session.read_events(after, 0.5)
    seen.extend(batch["events"])
    after = batch["seq"]
    if predicate(seen):
      return seen
  raise AssertionError(f"Timed out; events: {seen[-10:]}")


def test_registry_merges_local_agents_and_never_exposes_environment_values(service):
  state = service.state()
  agents = {agent["id"]: agent for agent in state["agents"]}
  assert "gemini" not in agents
  assert {"deepseek-harness", "claude-code", "codex", "fake"} <= set(agents)
  assert agents["fake"]["available"] is True and agents["fake"]["source"] == "local"
  assert agents["fake"]["env"] == ["FAKE_TOKEN"]
  assert "secret-value" not in json.dumps(state)
  assert state["diagnostics"] == []


def test_invalid_local_configuration_is_reported_without_losing_builtin_agents(tmp_path):
  config = tmp_path / "studio-agents.json"
  config.write_text(json.dumps({"agents": {"broken": {"command": []}}}))
  agents, diagnostics = load_agents(config)
  assert "deepseek-harness" in agents and diagnostics


def test_full_turn_covers_files_permissions_terminals_and_boundaries(service):
  summary = service.create("fake")
  session = service.get(summary["id"])
  events = wait_for(session, lambda items: any(item.get("state") == "ready" for item in items))
  assert any(item["type"] == "configured" and item["configOptions"][0]["currentValue"] == "a" for item in events)
  session.set_config("model", "b")
  service.prompt({"session": session.id, "text": "do it", "context": ["notes.txt"]})
  events = wait_for(session, lambda items: any(item["type"] == "permission" for item in items))
  permission = next(item for item in events if item["type"] == "permission")
  assert [option["optionId"] for option in permission["options"]] == ["allow", "reject"]
  session.resolve_permission(permission["key"], "allow")
  events = wait_for(session, lambda items: any(item["type"] == "turn_end" for item in items))
  chunks = [item["update"]["content"]["text"] for item in events
            if item["type"] == "update" and item["update"]["sessionUpdate"] == "agent_message_chunk"]
  report = json.loads(chunks[-1])
  assert chunks[:2] == ["Hello ", "links=1"]
  assert report["read"] == "second\n"
  assert "当前工作区" in report["outside"]
  assert "版本库" in report["git"]
  assert report["permission"] == {"outcome": "selected", "optionId": "allow"}
  assert report["exit"]["exitCode"] == 0 and report["output"].strip() == "terminal-ok"
  assert (service.root / "src" / "new.txt").read_text() == "created\n"
  writes = [item for item in events if item["type"] == "fs" and item["op"] == "write"]
  assert writes and writes[0]["path"] == "src/new.txt" and writes[0]["created"] is True
  assert any(item["type"] == "terminal" and item.get("exitStatus", {}).get("exitCode") == 0 for item in events)
  assert next(item for item in events if item["type"] == "user")["context"] == ["notes.txt"]
  assert service.get(session.id).state == "ready"
  assert summary["title"] == "" and service.get(session.id).summary()["title"] == "do it"


def test_cancel_resolves_turn_and_exit_is_reported(service):
  session = service.get(service.create("fake")["id"])
  wait_for(session, lambda items: any(item.get("state") == "ready" for item in items))
  service.prompt({"session": session.id, "text": "wait"})
  wait_for(session, lambda items: any(item.get("state") == "prompting" for item in items))
  session.cancel()
  events = wait_for(session, lambda items: any(item["type"] == "turn_end" for item in items))
  assert next(item for item in events if item["type"] == "turn_end")["stopReason"] == "cancelled"
  wait_for(session, lambda items: any(item.get("state") == "ready" for item in items[-3:]))
  service.prompt({"session": session.id, "text": "crash"})
  events = wait_for(session, lambda items: any(item.get("state") == "exited" for item in items))
  assert "退出码 3" in next(item for item in events if item.get("state") == "exited")["error"]


def test_prompt_context_must_stay_inside_project(service):
  session = service.get(service.create("fake")["id"])
  wait_for(session, lambda items: any(item.get("state") == "ready" for item in items))
  for context in (["../outside.txt"], [".git/config"], ["/etc/hosts"]):
    with pytest.raises(Exception):
      service.prompt({"session": session.id, "text": "x", "context": context})
  assert session.state == "ready"


def test_unavailable_agent_cannot_start(service, monkeypatch):
  monkeypatch.setenv("PATH", "/nonexistent")
  monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent-home"))
  with pytest.raises(Exception, match="未找到命令"):
    service.create("codex")
  assert os.environ["PATH"] == "/nonexistent"


def test_empty_workspace_session_is_isolated_from_bound_project(service):
  empty_state = service.state("empty")
  assert empty_state["workspace"] == "empty"
  assert Path(empty_state["root"]) != service.root
  summary = service.create("fake", workspace="empty")
  assert summary["workspace"] == "empty"
  assert [item["id"] for item in service.state("empty")["sessions"]] == [summary["id"]]
  assert service.state("project")["sessions"] == []
  session = service.get(summary["id"])
  wait_for(session, lambda items: any(item.get("state") == "ready" for item in items))
  (session.cwd / "notes.txt").write_text("a\nempty-line\nc\n")
  service.prompt({"session": session.id, "text": "ask in empty workspace"})
  events = wait_for(session, lambda items: any(item["type"] == "permission" for item in items))
  permission = next(item for item in events if item["type"] == "permission")
  session.resolve_permission(permission["key"], "allow")
  events = wait_for(session, lambda items: any(item["type"] == "turn_end" for item in items))
  chunks = [item["update"]["content"]["text"] for item in events
            if item["type"] == "update" and item["update"]["sessionUpdate"] == "agent_message_chunk"]
  report = json.loads(chunks[-1])
  assert report["read"] == "empty-line\n"
  assert not (service.root / "src" / "new.txt").exists()
  assert (session.cwd / "src" / "new.txt").read_text() == "created\n"


def test_http_routes_drive_a_session_and_keep_secrets_private(service, tmp_path, monkeypatch):
  from io import BytesIO
  from types import SimpleNamespace

  from texsys.project import initialize_project
  from texsys.studio import server
  from texsys.studio.runtime import StudioRuntime

  project = tmp_path / "routes"
  initialize_project(project)
  (project / "notes.txt").write_text("first\nsecond\n")
  runtime = StudioRuntime(project, plugins={})
  monkeypatch.setattr(server, "StudioServer", lambda address, handler, terminals: handler)
  handler_class = server.create_server(runtime, port=8710, frontend=tmp_path, token="agent-token")

  def request(path, body=None, *, authorized=True):
    handler = object.__new__(handler_class)
    handler.server = SimpleNamespace(server_port=8710)
    handler.path = path
    handler.headers = {"Host": "127.0.0.1:8710"}
    if authorized:
      handler.headers["X-Tex-Studio-Token"] = "agent-token"
    result = []
    handler._send = lambda code, payload, *args, **kwargs: result.extend([code, payload])
    if body is None:
      handler.do_GET()
    else:
      raw = json.dumps(body).encode()
      handler.rfile = BytesIO(raw)
      handler.headers["Content-Length"] = str(len(raw))
      handler.do_POST()
    return tuple(result)

  try:
    assert request("/api/agent", authorized=False)[0] == 403
    code, state = request("/api/agent")
    assert code == 200 and "secret-value" not in json.dumps(state)
    assert {agent["id"] for agent in state["agents"]} >= {"fake", "deepseek-harness"}
    code, created = request("/api/agent/sessions", {"agent": "fake"})
    assert code == 200
    after, seen = 0, []
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not any(event.get("state") == "ready" for event in seen):
      code, batch = request(f"/api/agent/events?session={created['id']}&after={after}&wait=1")
      assert code == 200
      seen.extend(batch["events"])
      after = batch["seq"]
    assert any(event.get("state") == "ready" for event in seen)
    assert request("/api/agent/prompt", {"session": created["id"], "text": " "})[0] == 400
    assert request(f"/api/agent/events?session={created['id']}&after=x")[0] == 400
    assert request("/api/agent/permission", {"session": created["id"], "key": "missing", "option": "x"})[0] == 409
    assert request("/api/agent/sessions", {"agent": "unknown"})[0] == 400
    assert request("/api/agent/close", {"session": created["id"]})[0] == 200
    assert request(f"/api/agent/events?session={created['id']}")[0] == 400
  finally:
    handler_class.agents.close()
    runtime.close()
