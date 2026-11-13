"""Exercise the Studio terminal protocol with real PTYs and an in-process socket pair."""

from collections import deque
from email.parser import BytesHeaderParser
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, BytesMessage, CloseConnection, Request, TextMessage

from texsys.studio.terminal import PROTOCOL, TerminalService

pytestmark = pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="Requires POSIX PTYs")
ORIGIN = "http://127.0.0.1:8710"
TOKEN = "terminal-test-token"
PROMPT = b"\x1b]7777;texsys-command-done\x07"


class Client:
  def __init__(self, service, root, token, origin):
    self.ws = WSConnection(ConnectionType.CLIENT)
    self.socket, peer = socket.socketpair()
    self.socket.settimeout(5)
    self.pending = deque()
    self.output = bytearray()
    request = self.ws.send(Request(
      host="127.0.0.1:8710", target="/api/terminal/ws", subprotocols=[PROTOCOL, token],
      extra_headers=[(b"origin", origin.encode())],
    ))

    class Handler:
      path = "/api/terminal/ws"
      connection = peer
      headers = BytesHeaderParser().parsebytes(request.split(b"\r\n", 1)[1])
      status = None

      def _send(self, status, _content):
        self.status = status

    self.handler = Handler()

    def serve():
      with peer:
        service.upgrade(self.handler, root, TOKEN, {ORIGIN})

    self.thread = threading.Thread(target=serve)
    self.thread.start()

  def close(self):
    self.socket.close()
    self.thread.join(6)
    assert not self.thread.is_alive()

  def send(self, message):
    self.socket.sendall(self.ws.send(TextMessage(data=json.dumps(message))))

  def input(self, data):
    self.socket.sendall(self.ws.send(BytesMessage(data=data)))

  def next(self):
    while not self.pending:
      try:
        data = self.socket.recv(65536)
      except TimeoutError:
        pytest.fail(f"Timed out waiting for terminal output: {bytes(self.output)!r}")
      assert data, "Terminal closed before the expected event"
      self.ws.receive_data(data)
      self.pending.extend(self.ws.events())
    event = self.pending.popleft()
    if isinstance(event, BytesMessage):
      self.output.extend(event.data)
      try:
        self.send(dict(type="ack", bytes=len(event.data)))
      except (BrokenPipeError, ConnectionResetError):
        pass
    return event

  def status(self, kind):
    while True:
      event = self.next()
      assert not isinstance(event, CloseConnection), bytes(self.output)
      if isinstance(event, TextMessage):
        message = json.loads(event.data)
        assert message["type"] != "error", message
        if message["type"] == kind:
          return message

  def until(self, pattern):
    while not (match := re.search(pattern, self.output)):
      assert not isinstance(self.next(), CloseConnection), bytes(self.output)
    result = tuple(match.groups())
    del self.output[:match.end()]
    return result


@pytest.fixture
def terminal_client(tmp_path, monkeypatch):
  monkeypatch.setenv("HOME", str(tmp_path))
  monkeypatch.delenv("ZDOTDIR", raising=False)
  (tmp_path / ".bashrc").write_text("set +H\n")
  (tmp_path / ".zshrc").write_text("unsetopt BANG_HIST\n")
  service = TerminalService()
  clients = []

  def connect(token=TOKEN, origin=ORIGIN):
    client = Client(service, tmp_path, token, origin)
    clients.append(client)
    return client

  yield connect
  for client in clients:
    client.close()
  service.close()


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_terminal_controls_tty_retains_state_and_reaps_jobs(shell, terminal_client, tmp_path):
  if not shutil.which(shell):
    pytest.skip(f"{shell} is unavailable")
  nested = tmp_path / "nested"
  nested.mkdir()
  terminal = terminal_client()
  assert isinstance(terminal.next(), AcceptConnection)
  terminal.send(dict(type="start", shell=shell, rows=24, cols=80))
  shell_pid = terminal.status("ready")["pid"]
  assert os.getsid(shell_pid) == shell_pid
  terminal.until(re.escape(PROMPT))
  terminal.input(b"test -t 0 && : </dev/tty && cd nested && export TEXSYS_PTY_TEST=sticky && printf 't%sy\\n' t\r")
  terminal.until(rb"\r\n(?:\x1b\[\?2004l\r)?tty\r\n")
  terminal.until(re.escape(PROMPT))
  terminal.send(dict(type="resize", rows=32, cols=96))
  terminal.input(b'printf "%s:%s\\n" "${PWD##*/}" "$TEXSYS_PTY_TEST"; stty size\r')
  terminal.until(rb"\r\n(?:\x1b\[\?2004l\r)?nested:sticky\r\n32 96\r\n")
  terminal.until(re.escape(PROMPT))
  terminal.input(b"sleep 60 & printf 'child-%s\\n' \"$!\" ; printf 'star%s\\n' ted; sleep 60\r")
  child_pid = int(terminal.until(rb"child-(\d+)\r\n")[0])
  terminal.until(rb"started\r\n")
  # Wait for the shell's foreground child to install its interrupt handler.
  time.sleep(0.1)
  terminal.input(b"\x03")
  terminal.until(re.escape(PROMPT))
  terminal.send(dict(type="close"))
  while not isinstance(terminal.next(), CloseConnection):
    pass
  deadline = time.monotonic() + 2
  while time.monotonic() < deadline:
    ps = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True)
    if not ps.stdout.strip() or ps.stdout.lstrip().startswith(b"Z"):
      break
    time.sleep(0.02)
  else:
    pytest.fail("Terminal background job is still running")
  with pytest.raises(ProcessLookupError):
    os.kill(shell_pid, 0)


@pytest.mark.parametrize("token,origin", [("invalid", ORIGIN), (TOKEN, "https://example.org")])
def test_terminal_rejects_untrusted_upgrade(terminal_client, token, origin):
  terminal = terminal_client(token, origin)
  terminal.thread.join(2)
  assert terminal.handler.status == 403


def test_terminal_menu_command_has_its_own_tty_and_reports_exit(terminal_client):
  terminal = terminal_client()
  assert isinstance(terminal.next(), AcceptConnection)
  terminal.send(dict(type="start", rows=27, cols=93, command="test -t 0 && : </dev/tty && stty size; exit 7"))
  assert terminal.status("ready")["pid"] > 0
  terminal.until(rb"27 93\r\n")
  assert terminal.status("exit")["code"] == 7
