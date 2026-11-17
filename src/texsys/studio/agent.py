"""Agent Client Protocol host for the Studio agent panel.

Studio does not implement an agent loop. It starts an external ACP agent as a
subprocess, speaks JSON-RPC 2.0 over newline-delimited stdio, answers the
client-side methods (project files, terminals and permission prompts) and
relays session updates to the browser as an ordered event log.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid

from texsys.formal import _atomic_write
from texsys.project import ProjectError
from .ai import is_ai_config_path
from .runtime import Conflict

PROTOCOL_VERSION = 1
CONFIG_FILENAME = "studio-agents.json"
MAX_EVENTS = 20000
MAX_PROMPT_LENGTH = 64 * 1024
MAX_CONTEXT_FILES = 16
MAX_READ_BYTES = 4 * 1024 * 1024
DEFAULT_TERMINAL_OUTPUT = 256 * 1024
MAX_SESSIONS = 8
STDERR_LINES = 80
PROTECTED_PARTS = {".git", ".hg", ".svn"}

# Well-known ACP agents. Each one owns its model access, tools and login; Studio
# only needs the launch command. Local overrides live in studio-agents.json.
BUILTIN_AGENTS = {
  "deepseek-harness": {
    "name": "DeepSeek Harness",
    "command": ["dsh", "--profile", "acp"],
    "description": "DeepSeek 官方 Harness 的 ACP 配置（@deepseek-ai/dsh）。",
  },
  "claude-code": {
    "name": "Claude Code",
    "command": ["claude-agent-acp"],
    "description": "Claude Code 的官方 ACP 适配器（@agentclientprotocol/claude-agent-acp）。",
  },
  "codex": {
    "name": "Codex",
    "command": ["codex-acp"],
    "description": "Codex 的官方 ACP 适配器（@agentclientprotocol/codex-acp）。",
  },
  "gemini": {
    "name": "Gemini CLI",
    "command": ["gemini", "--acp"],
    "description": "Gemini CLI 原生 ACP 模式。",
  },
}


class AgentError(ProjectError):
  def __init__(self, message, code=None, data=None):
    super().__init__(message)
    self.code = code
    self.data = data


def config_path() -> Path:
  config_root = Path(os.environ.get("XDG_CONFIG_HOME", ""))
  if not config_root.is_absolute():
    config_root = Path.home() / ".config"
  return config_root / "texsys" / CONFIG_FILENAME


def scratch_path() -> Path:
  cache_root = Path(os.environ.get("XDG_CACHE_HOME", ""))
  if not cache_root.is_absolute():
    cache_root = Path.home() / ".cache"
  return cache_root / "texsys" / "agent-scratch"


def is_agent_config_path(path: Path) -> bool:
  if path.name.casefold() != CONFIG_FILENAME:
    return False
  config = config_path().resolve()
  if path.resolve() == config:
    return True
  try:
    return path.samefile(config)
  except (FileNotFoundError, OSError):
    return False


def _search_path(env: dict) -> str:
  extra = [str(Path.home() / ".local" / "bin")]
  current = env.get("PATH", os.defpath).split(os.pathsep)
  return os.pathsep.join([*current, *(item for item in extra if item not in current)])


def _command(value, label):
  if isinstance(value, str):
    value = shlex.split(value)
  if (not isinstance(value, list) or not value or len(value) > 64
      or not all(isinstance(item, str) and item and "\0" not in item for item in value)):
    raise ProjectError(f"智能体 {label} 的 command 必须是非空字符串数组。")
  return value


def _environment(value, label):
  if value is None:
    return {}
  if not isinstance(value, dict) or not all(
      isinstance(key, str) and key and "=" not in key and "\0" not in key
      and isinstance(item, str) and "\0" not in item for key, item in value.items()):
    raise ProjectError(f"智能体 {label} 的 env 必须是字符串映射。")
  return dict(value)


@dataclass
class AgentDefinition:
  id: str
  name: str
  command: list
  env: dict = field(default_factory=dict)
  description: str = ""
  source: str = "builtin"

  def environment(self) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith("TEX_STUDIO_")}
    env.update(self.env)
    env["PATH"] = _search_path(env)
    return env

  def executable(self) -> str | None:
    program = self.command[0]
    if os.sep in program:
      candidate = Path(program).expanduser()
      return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return shutil.which(program, path=self.environment()["PATH"])

  def public(self) -> dict:
    executable = self.executable()
    return {
      "id": self.id,
      "name": self.name,
      "description": self.description,
      "command": shlex.join(self.command),
      "source": self.source,
      "available": executable is not None,
      "reason": "" if executable else f"未找到命令 {self.command[0]}。",
      "env": sorted(self.env),
    }


def load_agents(path: Path | None = None) -> tuple[dict[str, AgentDefinition], list[str]]:
  """Merge built-in agents with the local, private studio-agents.json."""
  path = path or config_path()
  agents = {key: AgentDefinition(key, value["name"], list(value["command"]), {}, value["description"])
            for key, value in BUILTIN_AGENTS.items()}
  diagnostics = []
  try:
    raw = path.read_text(encoding="utf-8")
  except FileNotFoundError:
    return agents, diagnostics
  except OSError as error:
    return agents, [f"无法读取智能体配置：{error.strerror or error}"]
  try:
    data = json.loads(raw)
    entries = data.get("agents", {}) if isinstance(data, dict) else None
    if not isinstance(entries, dict):
      raise ProjectError("智能体配置需要 agents 对象。")
    for key, entry in entries.items():
      if not isinstance(key, str) or not key or len(key) > 64 or not isinstance(entry, dict):
        raise ProjectError("智能体配置的每一项需要标识和对象。")
      base = agents.get(key)
      if entry.get("disabled") is True:
        agents.pop(key, None)
        continue
      command = _command(entry["command"], key) if "command" in entry else (base.command if base else None)
      if command is None:
        raise ProjectError(f"智能体 {key} 缺少 command。")
      name = entry.get("name", base.name if base else key)
      if not isinstance(name, str) or not name.strip():
        raise ProjectError(f"智能体 {key} 的 name 无效。")
      description = entry.get("description", base.description if base else "")
      agents[key] = AgentDefinition(key, name.strip(), command, _environment(entry.get("env"), key),
                                    description if isinstance(description, str) else "",
                                    "builtin" if base and "command" not in entry else "local")
  except (ValueError, KeyError, ProjectError) as error:
    diagnostics.append(f"智能体配置无效：{error}")
  return agents, diagnostics


class AcpConnection:
  """JSON-RPC 2.0 over newline-delimited stdio with one agent process."""

  def __init__(self, definition: AgentDefinition, cwd: Path, on_request, on_notification, on_exit):
    self.definition = definition
    self.cwd = cwd
    self.on_request = on_request
    self.on_notification = on_notification
    self.on_exit = on_exit
    self.process = None
    self.next_id = 0
    self.pending = {}
    self.lock = threading.Lock()
    self.write_lock = threading.Lock()
    self.stderr = []
    self.closed = threading.Event()

  def start(self):
    executable = self.definition.executable()
    if executable is None:
      raise AgentError(f"未找到智能体命令 {self.definition.command[0]}。")
    self.process = subprocess.Popen(
      [executable, *self.definition.command[1:]], cwd=self.cwd, env=self.definition.environment(),
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    threading.Thread(target=self._read, daemon=True, name="acp-stdout").start()
    threading.Thread(target=self._read_stderr, daemon=True, name="acp-stderr").start()

  def stderr_tail(self) -> str:
    with self.lock:
      return "\n".join(self.stderr[-12:])

  def _read_stderr(self):
    for raw in self.process.stderr:
      line = raw.decode("utf-8", "replace").rstrip()
      if line:
        with self.lock:
          self.stderr.append(line[:2000])
          del self.stderr[:-STDERR_LINES]

  def _read(self):
    try:
      for raw in self.process.stdout:
        line = raw.strip()
        if not line:
          continue
        try:
          message = json.loads(line)
        except ValueError:
          with self.lock:
            self.stderr.append("stdout: " + line.decode("utf-8", "replace")[:2000])
          continue
        if isinstance(message, dict):
          self._dispatch(message)
    finally:
      self.process.wait()
      self.closed.set()
      with self.lock:
        pending, self.pending = self.pending, {}
      for waiter in pending.values():
        waiter["error"] = AgentError("智能体进程已退出。")
        waiter["event"].set()
      self.on_exit(self.process.returncode)

  def _dispatch(self, message):
    if "method" in message:
      method, params = message["method"], message.get("params") or {}
      if "id" in message:
        identifier = message["id"]
        threading.Thread(target=self._serve, args=(identifier, method, params), daemon=True,
                         name=f"acp-{method}").start()
      else:
        self.on_notification(method, params)
      return
    with self.lock:
      waiter = self.pending.pop(message.get("id"), None)
    if waiter is None:
      return
    if "error" in message:
      error = message["error"] if isinstance(message["error"], dict) else {}
      details = error.get("data")
      text = str(error.get("message") or "智能体请求失败。")
      if isinstance(details, dict) and isinstance(details.get("details"), str):
        text = f"{text}：{details['details']}"
      waiter["error"] = AgentError(text, error.get("code"), details)
    else:
      waiter["result"] = message.get("result")
    waiter["event"].set()

  def _serve(self, identifier, method, params):
    try:
      result = self.on_request(method, params)
      self._write({"jsonrpc": "2.0", "id": identifier, "result": result})
    except AgentError as error:
      self._write({"jsonrpc": "2.0", "id": identifier,
                   "error": {"code": error.code or -32603, "message": str(error)}})
    except (ProjectError, OSError, ValueError, TypeError, KeyError) as error:
      self._write({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": str(error)}})

  def _write(self, message):
    data = (json.dumps(message, ensure_ascii=False) + "\n").encode()
    with self.write_lock:
      if self.closed.is_set() or self.process.stdin.closed:
        return
      try:
        self.process.stdin.write(data)
        self.process.stdin.flush()
      except (BrokenPipeError, OSError, ValueError):
        pass

  def request(self, method, params, timeout=None):
    waiter = {"event": threading.Event()}
    with self.lock:
      if self.closed.is_set():
        raise AgentError("智能体进程已退出。")
      identifier = self.next_id
      self.next_id += 1
      self.pending[identifier] = waiter
    self._write({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
    if not waiter["event"].wait(timeout):
      with self.lock:
        self.pending.pop(identifier, None)
      raise AgentError(f"智能体在 {int(timeout)} 秒内没有响应 {method}。")
    if "error" in waiter:
      raise waiter["error"]
    return waiter.get("result")

  def notify(self, method, params):
    self._write({"jsonrpc": "2.0", "method": method, "params": params})

  def close(self):
    if self.process is None or self.process.poll() is not None:
      return
    try:
      self.process.stdin.close()
    except OSError:
      pass
    try:
      self.process.wait(1.5)
      return
    except subprocess.TimeoutExpired:
      pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
      try:
        os.killpg(self.process.pid, sig)
      except (ProcessLookupError, PermissionError):
        return
      try:
        self.process.wait(2)
        return
      except subprocess.TimeoutExpired:
        continue


class AgentTerminal:
  """A client-side ACP terminal: one command with bounded, observable output."""

  def __init__(self, identifier, argv, cwd, env, limit, on_change):
    self.id = identifier
    self.argv = argv
    self.limit = limit
    self.output = ""
    self.truncated = False
    self.exit = None
    self.lock = threading.Lock()
    self.done = threading.Event()
    self.on_change = on_change
    self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, start_new_session=True)
    threading.Thread(target=self._read, daemon=True, name="acp-terminal").start()

  def _read(self):
    decoder_buffer = b""
    last = 0.0
    while True:
      chunk = self.process.stdout.read1(8192) if hasattr(self.process.stdout, "read1") else self.process.stdout.read(8192)
      if not chunk:
        break
      decoder_buffer += chunk
      try:
        text = decoder_buffer.decode("utf-8")
        decoder_buffer = b""
      except UnicodeDecodeError as error:
        text = decoder_buffer[:error.start].decode("utf-8", "replace")
        decoder_buffer = decoder_buffer[error.start:]
        if len(decoder_buffer) > 8:
          text += decoder_buffer.decode("utf-8", "replace")
          decoder_buffer = b""
      with self.lock:
        self.output += text
        encoded = self.output.encode("utf-8")
        if len(encoded) > self.limit:
          self.output = encoded[-self.limit:].decode("utf-8", "ignore")
          self.truncated = True
      now = time.monotonic()
      if now - last > 0.25:
        last = now
        self.on_change(self)
    code = self.process.wait()
    with self.lock:
      self.exit = {"exitCode": code, "signal": None} if code >= 0 else {"exitCode": None, "signal": signal.Signals(-code).name}
    self.done.set()
    self.on_change(self)

  def snapshot(self):
    with self.lock:
      result = {"output": self.output, "truncated": self.truncated}
      if self.exit is not None:
        result["exitStatus"] = dict(self.exit)
      return result

  def kill(self):
    if self.process.poll() is None:
      try:
        os.killpg(self.process.pid, signal.SIGTERM)
      except (ProcessLookupError, PermissionError):
        pass


class AgentSession:
  def __init__(self, service, definition: AgentDefinition, workspace: str = "project"):
    self.service = service
    self.definition = definition
    self.workspace = workspace
    self.cwd = service.workspace_root(workspace)
    self.id = uuid.uuid4().hex
    self.created_at = time.time()
    self.title = ""
    self.state = "starting"
    self.error = ""
    self.acp_session = None
    self.agent_info = {}
    self.capabilities = {}
    self.auth_methods = []
    self.events = []
    self.first_seq = 1
    self.seq = 0
    self.condition = threading.Condition()
    self.permissions = {}
    self.terminals = {}
    self.terminal_seq = 0
    self.closed = False
    self.connection = AcpConnection(definition, self.cwd, self._request, self._notification, self._exit)

  # Event log -------------------------------------------------------------

  def emit(self, kind, **payload):
    with self.condition:
      self.seq += 1
      self.events.append({"seq": self.seq, "type": kind, **payload})
      if len(self.events) > MAX_EVENTS:
        drop = len(self.events) - MAX_EVENTS
        del self.events[:drop]
        self.first_seq = self.events[0]["seq"]
      self.condition.notify_all()

  def set_state(self, state, error=""):
    self.state = state
    self.error = error
    self.emit("state", state=state, error=error)

  def read_events(self, after: int, wait: float) -> dict:
    with self.condition:
      if wait > 0:
        self.condition.wait_for(lambda: self.seq > after or self.closed, timeout=wait)
      reset = after + 1 < self.first_seq
      events = [event for event in self.events if event["seq"] > after] if not reset else list(self.events)
      return {"session": self.summary(), "events": events, "seq": self.seq, "reset": reset}

  def summary(self) -> dict:
    return {
      "id": self.id,
      "workspace": self.workspace,
      "agent": self.definition.id,
      "agent_name": self.agent_info.get("title") or self.definition.name,
      "agent_version": self.agent_info.get("version", ""),
      "title": self.title,
      "state": self.state,
      "error": self.error,
      "created_at": self.created_at,
      "auth_methods": self.auth_methods,
      "capabilities": self.capabilities,
    }

  # Lifecycle -------------------------------------------------------------

  def start(self):
    threading.Thread(target=self._start, daemon=True, name="acp-start").start()

  def _start(self):
    try:
      self.connection.start()
      result = self.connection.request("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": True},
        "clientInfo": {"name": "tex-studio", "title": "Tex Studio", "version": "0.1"},
      }, timeout=90) or {}
      if result.get("protocolVersion") != PROTOCOL_VERSION:
        raise AgentError(f"智能体使用不兼容的 ACP 协议版本 {result.get('protocolVersion')}。")
      self.agent_info = result.get("agentInfo") or {}
      self.capabilities = result.get("agentCapabilities") or {}
      self.auth_methods = result.get("authMethods") or []
      self.emit("initialized", agent=self.agent_info, capabilities=self.capabilities, authMethods=self.auth_methods)
      self._new_session()
    except AgentError as error:
      self._fail(error)
    except OSError as error:
      self._fail(AgentError(f"无法启动智能体：{error.strerror or error}"))

  def _new_session(self):
    try:
      result = self.connection.request("session/new", {"cwd": str(self.cwd), "mcpServers": []},
                                       timeout=120) or {}
    except AgentError as error:
      if error.code == -32000 and self.auth_methods:
        self.set_state("auth_required", str(error))
        return
      raise
    self.acp_session = result.get("sessionId")
    if not isinstance(self.acp_session, str):
      raise AgentError("智能体没有返回会话标识。")
    self.emit("configured", configOptions=result.get("configOptions") or [], modes=result.get("modes"),
              models=result.get("models"))
    self.set_state("ready")

  def _fail(self, error):
    tail = self.connection.stderr_tail()
    self.set_state("error", str(error) + (f"\n{tail}" if tail and self.connection.closed.is_set() else ""))

  def authenticate(self, method):
    if self.state != "auth_required":
      raise Conflict("当前会话不需要登录。")
    if not any(isinstance(item, dict) and item.get("id") == method for item in self.auth_methods):
      raise ProjectError("未知的登录方式。")
    self.set_state("starting")

    def run():
      try:
        self.connection.request("authenticate", {"methodId": method}, timeout=300)
        self._new_session()
      except AgentError as error:
        self._fail(error)
    threading.Thread(target=run, daemon=True, name="acp-auth").start()

  def prompt(self, text, context):
    if self.state != "ready":
      raise Conflict("智能体尚未就绪或正在处理上一条消息。")
    blocks = [{"type": "text", "text": text}]
    for relative in context:
      file = self.service.resolve(relative, must_exist=True, root=self.cwd)
      blocks.append({"type": "resource_link", "uri": file.as_uri(), "name": file.name})
    if not self.title:
      self.title = " ".join(text.split())[:80]
    self.emit("user", text=text, context=list(context))
    self.set_state("prompting")

    def run():
      try:
        result = self.connection.request("session/prompt", {"sessionId": self.acp_session, "prompt": blocks}) or {}
        self.emit("turn_end", stopReason=result.get("stopReason", "end_turn"))
        if not self.closed:
          self.set_state("ready")
      except AgentError as error:
        self.emit("turn_end", stopReason="error", error=str(error))
        if not self.connection.closed.is_set():
          self.set_state("ready")
    threading.Thread(target=run, daemon=True, name="acp-prompt").start()

  def cancel(self):
    if self.state != "prompting":
      return
    self.connection.notify("session/cancel", {"sessionId": self.acp_session})
    for key in list(self.permissions):
      self.resolve_permission(key, None)

  def set_config(self, config, value):
    if self.acp_session is None:
      raise Conflict("智能体会话尚未建立。")
    result = self.connection.request("session/set_config_option",
                                     {"sessionId": self.acp_session, "configId": config, "value": value},
                                     timeout=60) or {}
    if isinstance(result.get("configOptions"), list):
      self.emit("configured", configOptions=result["configOptions"])

  def set_mode(self, mode):
    if self.acp_session is None:
      raise Conflict("智能体会话尚未建立。")
    self.connection.request("session/set_mode", {"sessionId": self.acp_session, "modeId": mode}, timeout=60)
    self.emit("update", update={"sessionUpdate": "current_mode_update", "currentModeId": mode})

  def close(self):
    if self.closed:
      return
    self.closed = True
    for key in list(self.permissions):
      self.resolve_permission(key, None)
    if self.acp_session and isinstance(self.capabilities.get("sessionCapabilities"), dict) \
        and "close" in self.capabilities["sessionCapabilities"] and not self.connection.closed.is_set():
      try:
        self.connection.request("session/close", {"sessionId": self.acp_session}, timeout=3)
      except AgentError:
        pass
    for terminal in list(self.terminals.values()):
      terminal.kill()
    self.connection.close()
    self.state = "closed"
    with self.condition:
      self.condition.notify_all()

  def _exit(self, code):
    for key in list(self.permissions):
      self.resolve_permission(key, None)
    if not self.closed:
      tail = self.connection.stderr_tail()
      self.set_state("exited", f"智能体进程已退出（退出码 {code}）。" + (f"\n{tail}" if tail else ""))

  # Agent -> client -------------------------------------------------------

  def _notification(self, method, params):
    if method == "session/update" and isinstance(params.get("update"), dict):
      self.emit("update", update=params["update"])

  def _request(self, method, params):
    if method == "fs/read_text_file":
      return self._read_file(params)
    if method == "fs/write_text_file":
      return self._write_file(params)
    if method == "session/request_permission":
      return self._permission(params)
    if method == "terminal/create":
      return self._terminal_create(params)
    if method in {"terminal/output", "terminal/wait_for_exit", "terminal/kill", "terminal/release"}:
      return self._terminal(method, params)
    raise AgentError(f"Tex Studio 不支持 {method}。", -32601)

  def _read_file(self, params):
    file = self.service.resolve_absolute(params.get("path"), root=self.cwd)
    if not file.is_file():
      raise AgentError("文件不存在。", -32002)
    if file.stat().st_size > MAX_READ_BYTES:
      raise AgentError("文件超过 4 MiB，无法通过智能体读取。")
    text = file.read_text(encoding="utf-8", errors="replace")
    line, limit = params.get("line"), params.get("limit")
    if line is not None or limit is not None:
      lines = text.splitlines(keepends=True)
      start = max(int(line or 1) - 1, 0)
      end = start + int(limit) if limit is not None else None
      text = "".join(lines[start:end])
    self.emit("fs", op="read", path=self.service.relative(file, root=self.cwd))
    return {"content": text}

  def _write_file(self, params):
    file = self.service.resolve_absolute(params.get("path"), root=self.cwd)
    content = params.get("content")
    if not isinstance(content, str):
      raise AgentError("写入内容必须是文本。", -32602)
    existed = file.exists()
    old = file.read_text(encoding="utf-8", errors="replace") if existed and file.stat().st_size <= MAX_READ_BYTES else None
    file.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(file, content)
    self.emit("fs", op="write", path=self.service.relative(file, root=self.cwd), created=not existed,
              oldText=old if old is not None and len(old) <= 256 * 1024 else None,
              newText=content if len(content) <= 256 * 1024 else None)
    return None

  def _permission(self, params):
    options = [item for item in params.get("options") or [] if isinstance(item, dict) and "optionId" in item]
    key = uuid.uuid4().hex
    waiter = {"event": threading.Event(), "option": None}
    self.permissions[key] = waiter
    self.emit("permission", key=key, toolCall=params.get("toolCall") or {}, options=options)
    if self.closed:
      self.resolve_permission(key, None)
    waiter["event"].wait()
    if waiter["option"] is None:
      return {"outcome": {"outcome": "cancelled"}}
    return {"outcome": {"outcome": "selected", "optionId": waiter["option"]}}

  def resolve_permission(self, key, option):
    waiter = self.permissions.pop(key, None)
    if waiter is None:
      raise Conflict("该授权请求已处理或已失效。")
    waiter["option"] = option
    waiter["event"].set()
    self.emit("permission_resolved", key=key, optionId=option)

  def _terminal_create(self, params):
    command = params.get("command")
    args = params.get("args") or []
    if not isinstance(command, str) or not command or not isinstance(args, list) \
        or not all(isinstance(item, str) for item in args):
      raise AgentError("终端命令无效。", -32602)
    argv = [command, *args] if args or shutil.which(command) else ["/bin/sh", "-c", command]
    cwd = (self.service.resolve_absolute(params["cwd"], directory=True, root=self.cwd)
           if params.get("cwd") else self.cwd)
    env = self.definition.environment()
    for item in params.get("env") or []:
      if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("value"), str):
        env[item["name"]] = item["value"]
    limit = params.get("outputByteLimit")
    limit = int(limit) if isinstance(limit, int) and limit > 0 else DEFAULT_TERMINAL_OUTPUT
    self.terminal_seq += 1
    identifier = f"term-{self.terminal_seq}"
    display = shlex.join(argv[2:] if argv[:2] == ["/bin/sh", "-c"] else argv)
    self.emit("terminal", terminalId=identifier, command=display, cwd=self.service.relative(cwd, root=self.cwd) or ".",
              output="", truncated=False)
    self.terminals[identifier] = AgentTerminal(identifier, argv, cwd, env, min(limit, 4 * 1024 * 1024),
                                               lambda terminal: self.emit("terminal", terminalId=terminal.id,
                                                                          **terminal.snapshot()))
    return {"terminalId": identifier}

  def _terminal(self, method, params):
    terminal = self.terminals.get(params.get("terminalId"))
    if terminal is None:
      raise AgentError("终端不存在或已释放。", -32002)
    if method == "terminal/output":
      return terminal.snapshot()
    if method == "terminal/wait_for_exit":
      terminal.done.wait()
      return dict(terminal.exit)
    if method == "terminal/kill":
      terminal.kill()
      return None
    terminal.kill()
    self.terminals.pop(terminal.id, None)
    return None


class AgentService:
  """Owns agent sessions for Studio project and empty-workspace scopes."""

  def __init__(self, root: Path, build: str = "build", config: Path | None = None, scratch: Path | None = None):
    self.root = root.resolve()
    self.build = build
    self.config = config
    self.scratch = scratch
    self.sessions: dict[str, AgentSession] = {}
    self.lock = threading.Lock()

  def workspace_root(self, workspace: str = "project") -> Path:
    if workspace == "project":
      return self.root
    if workspace == "empty":
      directory = (self.scratch or scratch_path()).expanduser().resolve()
      if directory == self.root or directory.is_relative_to(self.root):
        raise ProjectError("空白工作区缓存目录不得位于项目目录内。")
      directory.mkdir(parents=True, exist_ok=True)
      return directory
    raise ProjectError("未知的工作区模式。")

  # Paths -----------------------------------------------------------------

  def _check(self, file: Path, root: Path | None = None) -> Path:
    boundary = root or self.root
    if not file.is_relative_to(boundary):
      raise AgentError("智能体只能访问当前工作区内的文件。", -32602)
    parts = file.relative_to(boundary).parts
    if any(part in PROTECTED_PARTS for part in parts):
      raise AgentError("智能体不能直接访问版本库内部目录。", -32602)
    if is_ai_config_path(file) or is_agent_config_path(file):
      raise AgentError("智能体不能访问本机 AI 设置。", -32602)
    return file

  def resolve_absolute(self, value, *, directory=False, root: Path | None = None) -> Path:
    if not isinstance(value, str) or not value or "\0" in value or not os.path.isabs(value):
      raise AgentError("ACP 文件路径必须是绝对路径。", -32602)
    file = self._check(Path(value).resolve(), root)
    if directory and not file.is_dir():
      raise AgentError("工作目录不存在。", -32602)
    return file

  def resolve(self, relative, *, must_exist=False, root: Path | None = None) -> Path:
    boundary = root or self.root
    if not isinstance(relative, str) or not relative or "\0" in relative or os.path.isabs(relative):
      raise ProjectError("请输入项目内的相对路径。")
    file = self._check((boundary / relative).resolve(), boundary)
    if must_exist and not file.is_file():
      raise ProjectError(f"文件不存在：{relative}")
    return file

  def relative(self, file: Path, root: Path | None = None) -> str:
    boundary = root or self.root
    return file.relative_to(boundary).as_posix() if file != boundary else ""

  # Sessions --------------------------------------------------------------

  def state(self, workspace: str = "project") -> dict:
    boundary = self.workspace_root(workspace)
    agents, diagnostics = load_agents(self.config)
    with self.lock:
      sessions = [session.summary() for session in self.sessions.values() if session.workspace == workspace]
    path = self.config or config_path()
    home = Path.home()
    display = "~/" + path.relative_to(home).as_posix() if path.is_relative_to(home) else str(path)
    return {
      "agents": [agent.public() for agent in agents.values()],
      "sessions": sorted(sessions, key=lambda item: item["created_at"], reverse=True),
      "config_path": display,
      "diagnostics": diagnostics,
      "root": str(boundary),
      "workspace": workspace,
    }

  def create(self, agent_id, workspace: str = "project") -> dict:
    self.workspace_root(workspace)
    agents, _ = load_agents(self.config)
    definition = agents.get(agent_id)
    if definition is None:
      raise ProjectError("未知的智能体。")
    if definition.executable() is None:
      raise ProjectError(definition.public()["reason"])
    with self.lock:
      live = [item for item in self.sessions.values() if item.state not in {"closed"}]
      if len(live) >= MAX_SESSIONS:
        raise Conflict("智能体会话数量已达上限，请先关闭不用的会话。")
      session = AgentSession(self, definition, workspace=workspace)
      self.sessions[session.id] = session
    session.start()
    return session.summary()

  def get(self, identifier) -> AgentSession:
    with self.lock:
      session = self.sessions.get(identifier)
    if session is None:
      raise ProjectError("智能体会话不存在或已关闭。")
    return session

  def prompt(self, body):
    text = body.get("text")
    context = body.get("context") or []
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_PROMPT_LENGTH:
      raise ProjectError("消息不能为空且不超过 64 KiB。")
    if not isinstance(context, list) or len(context) > MAX_CONTEXT_FILES:
      raise ProjectError("上下文文件过多。")
    session = self.get(body.get("session"))
    session.prompt(text, context)
    return session.summary()

  def close_session(self, identifier):
    session = self.get(identifier)
    with self.lock:
      self.sessions.pop(identifier, None)
    session.close()
    return {"closed": identifier}

  def close(self):
    with self.lock:
      sessions = list(self.sessions.values())
      self.sessions.clear()
    for session in sessions:
      session.close()
