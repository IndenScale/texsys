"""Exercise the Studio DeepSeek Harness host with a scripted ACP agent process."""

import json
import os
from pathlib import Path
import sys
import textwrap
import time

import pytest

from texsys.studio.agent import MAX_HISTORY_BYTES, AgentService, load_kernel

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

  def log(method, params):
    if os.environ.get("FAKE_LOG"):
      with open(os.environ["FAKE_LOG"], "a") as stream:
        stream.write(json.dumps({"method": method, "params": params}) + "\n")

  def options(value):
    return [{"id": "model", "name": "Model", "type": "select", "currentValue": value,
             "options": [{"value": "a", "name": "A"}, {"value": "b", "name": "B"}]}]

  capabilities = {"sessionCapabilities": {"close": {}}}
  if os.environ.get("FAKE_RESUME") == "1":
    capabilities["sessionCapabilities"]["resume"] = {}

  for line in sys.stdin:
    message = json.loads(line)
    if "method" not in message:
      pending[message["id"]]["message"] = message
      pending[message["id"]]["event"].set()
      continue
    method, params, identifier = message["method"], message.get("params", {}), message.get("id")
    log(method, params)
    if method == "initialize":
      send({"jsonrpc": "2.0", "id": identifier, "result": {"protocolVersion": 1,
            "agentInfo": {"name": "dsh", "title": "DeepSeek Harness", "version": "0.2.2"},
            "agentCapabilities": capabilities, "authMethods": []}})
    elif method == "session/new":
      assert params["cwd"] == os.getcwd(), params
      send({"jsonrpc": "2.0", "id": identifier, "result": {"sessionId": "s-%d" % os.getpid(), "configOptions": options("a")}})
    elif method == "session/resume":
      assert params["cwd"] == os.getcwd(), params
      send({"jsonrpc": "2.0", "id": identifier, "result": {"configOptions": options("b")}})
    elif method == "session/set_config_option":
      send({"jsonrpc": "2.0", "id": identifier, "result": {"configOptions": options(params["value"])}})
    elif method == "session/prompt":
      threading.Thread(target=prompt, args=(identifier, params), daemon=True).start()
    elif method == "session/cancel":
      state["cancel"].set()
    elif method == "session/close":
      send({"jsonrpc": "2.0", "id": identifier, "result": {}})
    elif identifier is not None:
      send({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found"}})
''')


def write_config(path, agent, *, resume=True, log=None, extra=None):
  env = {"FAKE_TOKEN": "secret-value", "FAKE_RESUME": "1" if resume else "0"}
  if log:
    env["FAKE_LOG"] = str(log)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps({"agents": {
    "deepseek-harness": {"command": [sys.executable, str(agent)], "env": env},
    "codex": {"command": ["codex-acp"], "env": {"OTHER": "ignored"}},
    **(extra or {}),
  }}))


@pytest.fixture
def setup(tmp_path, monkeypatch):
  project = tmp_path / "project"
  (project / ".git").mkdir(parents=True)
  (project / ".git" / "config").write_text("[core]\n")
  (project / "notes.txt").write_text("first\nsecond\nthird\n")
  agent = tmp_path / "fake_agent.py"
  agent.write_text(FAKE_AGENT)
  config = tmp_path / "config" / "texsys" / "studio-agents.json"
  log = tmp_path / "calls.jsonl"
  write_config(config, agent, log=log)
  monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent.parent))
  monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
  monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
  services = []

  def make(**kwargs):
    service = AgentService(project, **kwargs)
    services.append(service)
    return service

  try:
    yield {"project": project, "agent": agent, "config": config, "log": log, "make": make,
           "history": tmp_path / "state" / "texsys" / "agent-sessions"}
  finally:
    for service in services:
      service.close()


@pytest.fixture
def service(setup):
  return setup["make"]()


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


def ready(session):
  return wait_for(session, lambda items: any(item.get("state") == "ready" for item in items))


def calls(log):
  return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def run_turn(service, session, text="do it", context=()):
  before = len([item for item in session.events if item["type"] == "turn_end"])
  service.prompt({"session": session.id, "text": text, "context": list(context)})
  events = wait_for(session, lambda items: any(item["type"] == "permission" and item["key"] in session.permissions
                                              for item in items))
  permission = next(item for item in reversed(events) if item["type"] == "permission")
  session.resolve_permission(permission["key"], "allow")
  wait_for(session, lambda items: len([item for item in session.events if item["type"] == "turn_end"]) > before)
  wait_for(session, lambda items: session.state == "ready")
  return permission


def test_kernel_is_deepseek_harness_and_config_only_honours_its_entry(service, setup):
  state = service.state()
  kernel = state["kernel"]
  assert kernel["id"] == "deepseek-harness" and kernel["name"] == "DeepSeek Harness"
  assert kernel["available"] is True and kernel["reason"] == ""
  assert kernel["command"].endswith("fake_agent.py")
  assert set(state) == {"kernel", "diagnostics", "sessions", "history", "root", "workspace"}
  assert "secret-value" not in json.dumps(state) and "agents" not in state
  assert state["diagnostics"] == [] and state["history"] == [] and state["sessions"] == []
  definition, diagnostics = load_kernel(setup["config"])
  assert definition.env["FAKE_TOKEN"] == "secret-value" and "OTHER" not in definition.env
  assert diagnostics == []


def test_kernel_defaults_and_invalid_configuration(tmp_path):
  definition, diagnostics = load_kernel(tmp_path / "missing.json")
  assert definition.command == ["dsh", "--profile", "acp"] and diagnostics == []
  config = tmp_path / "studio-agents.json"
  config.write_text(json.dumps({"agents": {"claude-code": {"command": []}, "deepseek-harness": {"env": {"K": "v"}}}}))
  definition, diagnostics = load_kernel(config)
  assert definition.command == ["dsh", "--profile", "acp"] and definition.env == {"K": "v"} and diagnostics == []
  config.write_text(json.dumps({"agents": {"deepseek-harness": {"command": []}}}))
  definition, diagnostics = load_kernel(config)
  assert definition.command == ["dsh", "--profile", "acp"] and diagnostics


def test_search_path_includes_user_bin_directories(monkeypatch, tmp_path):
  home = tmp_path / "home"
  binary = home / ".bun" / "bin" / "dsh"
  binary.parent.mkdir(parents=True)
  binary.write_text("#!/bin/sh\n")
  binary.chmod(0o755)
  monkeypatch.setattr(Path, "home", lambda: home)
  monkeypatch.setenv("PATH", "/nonexistent")
  definition, _ = load_kernel(tmp_path / "missing.json")
  assert definition.executable() == str(binary)
  assert str(home / ".local" / "bin") in definition.environment()["PATH"].split(os.pathsep)


def test_full_turn_covers_files_permissions_terminals_and_boundaries(service):
  summary = service.create()
  assert set(summary) == {"id", "workspace", "title", "state", "error", "created_at", "updated_at", "seq", "cwd",
                          "stderr", "kernel", "resumed"}
  assert summary["resumed"] is False and "agent" not in summary
  session = service.get(summary["id"])
  events = ready(session)
  assert any(item["type"] == "configured" and item["configOptions"][0]["currentValue"] == "a" for item in events)
  assert session.summary()["kernel"] == {"name": "DeepSeek Harness", "version": "0.2.2"}
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
  wait_for(session, lambda items: session.state == "ready")
  assert summary["title"] == "" and service.get(session.id).summary()["title"] == "do it"


def test_cancel_resolves_turn_and_exit_is_reported(service):
  session = service.get(service.create()["id"])
  ready(session)
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
  session = service.get(service.create()["id"])
  ready(session)
  for context in (["../outside.txt"], [".git/config"], ["/etc/hosts"]):
    with pytest.raises(Exception):
      service.prompt({"session": session.id, "text": "x", "context": context})
  assert session.state == "ready"
  assert service.state()["history"] == []


def test_unavailable_kernel_cannot_start(service, setup, monkeypatch):
  setup["config"].write_text(json.dumps({"agents": {"deepseek-harness": {"command": ["dsh-missing"]}}}))
  monkeypatch.setenv("PATH", "/nonexistent")
  monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent-home"))
  state = service.state()
  assert state["kernel"]["available"] is False and "dsh-missing" in state["kernel"]["reason"]
  with pytest.raises(Exception, match="未找到命令"):
    service.create()
  assert os.environ["PATH"] == "/nonexistent"


def test_empty_workspace_session_is_isolated_from_bound_project(service):
  empty_state = service.state("empty")
  assert empty_state["workspace"] == "empty"
  assert Path(empty_state["root"]) != service.root
  summary = service.create(workspace="empty")
  assert summary["workspace"] == "empty"
  assert [item["id"] for item in service.state("empty")["sessions"]] == [summary["id"]]
  assert service.state("project")["sessions"] == []
  session = service.get(summary["id"])
  ready(session)
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
  wait_for(session, lambda items: session.state == "ready")
  assert [item["id"] for item in service.state("empty")["history"]] == [summary["id"]]
  assert service.state("project")["history"] == []


def test_history_starts_with_first_prompt_and_survives_restart(service, setup):
  history = setup["history"]
  session = service.get(service.create()["id"])
  ready(session)
  session.set_config("model", "b")
  assert service.state()["history"] == []
  assert not (history / f"{session.id}.jsonl").exists()
  run_turn(service, session)
  entry = json.loads((history / "index.json").read_text())["sessions"][session.id]
  assert entry["title"] == "do it" and entry["workspace"] == "project" and entry["cwd"] == str(service.root)
  assert entry["acp_session"].startswith("s-") and entry["updated_at"] >= entry["created_at"]
  stored = [json.loads(line) for line in (history / f"{session.id}.jsonl").read_text().splitlines()]
  types = [item["type"] for item in stored]
  # Events emitted before the first prompt are flushed when the session enters history.
  assert types[:3] == ["configured", "configured", "user"] and types[-1] == "turn_end"
  assert not {"state", "initialized", "history"} & set(types)
  assert all("seq" not in item for item in stored)
  terminals = [item for item in stored if item["type"] == "terminal"]
  assert len(terminals) == 2 and "command" in terminals[0] and "exitStatus" in terminals[-1]
  listed = service.state()["history"]
  assert listed == [{"id": session.id, "title": "do it", "created_at": entry["created_at"],
                     "updated_at": entry["updated_at"], "live": True}]
  assert service.close_session(session.id) == {"closed": session.id}
  assert service.state()["history"][0]["live"] is False
  assert (history / f"{session.id}.jsonl").exists()


def test_resume_replays_history_and_calls_session_resume(setup):
  first = setup["make"]()
  session = first.get(first.create()["id"])
  ready(session)
  run_turn(first, session)
  identifier, acp_session = session.id, session.acp_session
  stored = len((setup["history"] / f"{identifier}.jsonl").read_text().splitlines())
  first.close()

  second = setup["make"]()
  listed = second.state()["history"]
  assert [item["id"] for item in listed] == [identifier] and listed[0]["live"] is False
  with pytest.raises(Exception, match="不属于当前工作区"):
    second.resume(identifier, "empty")
  summary = second.resume(identifier, "project")
  assert summary["id"] == identifier and summary["resumed"] is True and summary["title"] == "do it"
  resumed = second.get(identifier)
  events = ready(resumed)
  assert [item["seq"] for item in events] == list(range(1, len(events) + 1))
  marker = next(index for index, item in enumerate(events) if item["type"] == "history")
  assert events[marker]["count"] == stored == marker
  assert [item["type"] for item in events[:marker]][-1] == "turn_end"
  assert any(item["type"] == "configured" and item["configOptions"][0]["currentValue"] == "b"
             for item in events[marker:])
  resume_calls = [item for item in calls(setup["log"]) if item["method"] == "session/resume"]
  assert resume_calls == [{"method": "session/resume",
                           "params": {"sessionId": acp_session, "cwd": str(second.root), "mcpServers": []}}]
  assert second.resume(identifier, "project")["id"] == identifier
  assert len(second.sessions) == 1
  run_turn(second, resumed, "again")
  lines = (setup["history"] / f"{identifier}.jsonl").read_text().splitlines()
  assert len(lines) > stored and json.loads(lines[-1])["type"] == "turn_end"
  assert any(json.loads(line).get("text") == "again" for line in lines[stored:])
  prompts = [item for item in calls(setup["log"]) if item["method"] == "session/prompt"]
  assert prompts[-1]["params"]["sessionId"] == acp_session
  assert second.state()["history"][0]["live"] is True


def test_unresolved_permissions_are_dropped_on_replay(setup):
  service = setup["make"]()
  session = service.get(service.create()["id"])
  ready(session)
  run_turn(service, session)
  log = setup["history"] / f"{session.id}.jsonl"
  service.close()
  with log.open("a") as stream:
    stream.write(json.dumps({"type": "permission", "key": "dangling", "toolCall": {}, "options": []}) + "\n")
    stream.write("not json\n")
  resumed = setup["make"]()
  session = resumed.get(resumed.resume(session.id)["id"])
  events = ready(session)
  permissions = [item for item in events if item["type"] == "permission"]
  assert len(permissions) == 1 and permissions[0]["key"] != "dangling"


def test_resume_without_kernel_support_keeps_transcript_readable(setup):
  first = setup["make"]()
  session = first.get(first.create()["id"])
  ready(session)
  run_turn(first, session)
  identifier = session.id
  first.close()
  write_config(setup["config"], setup["agent"], resume=False, log=setup["log"])
  second = setup["make"]()
  summary = second.resume(identifier)
  resumed = second.get(summary["id"])
  events = wait_for(resumed, lambda items: any(item.get("state") == "error" for item in items))
  error = next(item for item in events if item.get("state") == "error")["error"]
  assert error.startswith("无法在 DeepSeek Harness 中恢复该会话：")
  assert any(item["type"] == "user" and item["text"] == "do it" for item in events)
  assert any(item["type"] == "history" for item in events)
  assert resumed.summary()["state"] == "error"
  with pytest.raises(Exception):
    second.prompt({"session": identifier, "text": "x"})
  assert not [item for item in calls(setup["log"]) if item["method"] == "session/resume"]


def test_delete_history_closes_live_session_and_removes_records(service, setup):
  session = service.get(service.create()["id"])
  ready(session)
  run_turn(service, session)
  log = setup["history"] / f"{session.id}.jsonl"
  assert log.exists()
  assert service.delete_history(session.id) == {"deleted": session.id}
  assert session.closed and session.id not in service.sessions
  assert not log.exists()
  assert session.id not in json.loads((setup["history"] / "index.json").read_text())["sessions"]
  assert service.state()["history"] == []
  with pytest.raises(Exception):
    service.delete_history(session.id)
  with pytest.raises(Exception):
    service.resume(session.id)
  with pytest.raises(Exception, match="会话标识无效"):
    service.delete_history("../index")


def test_history_is_capped_and_io_errors_do_not_break_sessions(service, setup, monkeypatch):
  import texsys.studio.agent as agent_module

  session = service.get(service.create()["id"])
  ready(session)
  run_turn(service, session)
  log = setup["history"] / f"{session.id}.jsonl"
  size = log.stat().st_size
  monkeypatch.setattr(agent_module, "MAX_HISTORY_BYTES", size + 10)
  run_turn(service, session, "capped")
  assert log.stat().st_size == size
  monkeypatch.setattr(agent_module, "MAX_HISTORY_BYTES", MAX_HISTORY_BYTES)

  blocker = setup["project"].parent / "blocker"
  blocker.write_text("not a directory")
  service.history.directory = blocker / "agent-sessions"
  service.history.sizes.clear()
  run_turn(service, session, "still works")
  assert session.state == "ready"
  assert any(item["type"] == "user" and item["text"] == "still works" for item in session.events)


def test_history_directory_must_stay_outside_project(setup):
  service = setup["make"](history=setup["project"] / "history")
  assert service.history is None
  assert any("历史" in item for item in service.state()["diagnostics"])


def test_http_routes_drive_a_session_and_keep_secrets_private(setup, tmp_path, monkeypatch):
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

  def drain(identifier, predicate, after=0):
    seen = []
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not predicate(seen):
      code, batch = request(f"/api/agent/events?session={identifier}&after={after}&wait=1")
      assert code == 200
      seen.extend(batch["events"])
      after = batch["seq"]
    assert predicate(seen)
    return seen, batch

  try:
    assert request("/api/agent", authorized=False)[0] == 403
    code, state = request("/api/agent")
    assert code == 200 and "secret-value" not in json.dumps(state)
    assert state["kernel"]["id"] == "deepseek-harness" and state["history"] == []
    code, created = request("/api/agent/sessions", {"workspace": "project"})
    assert code == 200 and created["resumed"] is False
    identifier = created["id"]
    seen, batch = drain(identifier, lambda items: any(event.get("state") == "ready" for event in items))
    assert batch["session"]["state"] == "ready"
    assert created["cwd"] == str(project.resolve()) and "secret-value" not in json.dumps(created)
    for route in ("/api/agent/model", "/api/agent/mode", "/api/agent/authenticate"):
      assert request(route, {"session": identifier, "model": "b"})[0] == 404
    assert request("/api/agent/config", {"session": identifier, "config": "model", "value": "b"})[0] == 200
    assert request("/api/agent/prompt", {"session": identifier, "text": " "})[0] == 400
    assert request(f"/api/agent/events?session={identifier}&after=x")[0] == 400
    assert request("/api/agent/permission", {"session": identifier, "key": "missing", "option": "x"})[0] == 409
    assert request("/api/agent/prompt", {"session": identifier, "text": "via http"})[0] == 200
    seen, batch = drain(identifier, lambda items: any(event["type"] == "permission" for event in items))
    key = next(event["key"] for event in seen if event["type"] == "permission")
    assert request("/api/agent/permission", {"session": identifier, "key": key, "option": "allow"})[0] == 200
    drain(identifier, lambda items: any(event["type"] == "turn_end" for event in items), batch["seq"])
    code, state = request("/api/agent")
    assert [item["id"] for item in state["history"]] == [identifier] and state["history"][0]["live"] is True
    assert request("/api/agent/close", {"session": identifier})[0] == 200
    assert request(f"/api/agent/events?session={identifier}")[0] == 400
    assert request("/api/agent")[1]["history"][0]["live"] is False
    code, resumed = request("/api/agent/resume", {"session": identifier, "workspace": "project"})
    assert code == 200 and resumed["id"] == identifier and resumed["resumed"] is True
    drain(identifier, lambda items: any(event["type"] == "history" for event in items))
    assert request("/api/agent/resume", {"session": identifier, "workspace": "empty"})[0] == 200  # live wins
    assert request("/api/agent/resume", {"session": "0" * 32, "workspace": "project"})[0] == 400
    assert request("/api/agent/history/delete", {"session": identifier}) == (200, {"deleted": identifier})
    assert request("/api/agent")[1]["history"] == []
    assert request("/api/agent/history/delete", {"session": identifier})[0] == 400
  finally:
    handler_class.agents.close()
    runtime.close()
