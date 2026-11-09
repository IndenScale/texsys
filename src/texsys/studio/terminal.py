"""Authenticated, bounded WebSocket transport for local interactive shells."""

import array
from contextlib import contextmanager
import errno
import hmac
import json
import os
from pathlib import Path
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, BytesMessage, CloseConnection, Ping, Request, TextMessage
from wsproto.utilities import ProtocolError

from texsys.project import ProjectError
from .source_control import repository

PROTOCOL = "texsys-terminal-v1"
CHUNK = 16 * 1024
WINDOW = 128 * 1024
MAX_MESSAGE = 64 * 1024


def dimensions(message):
  rows, cols = message.get("rows"), message.get("cols")
  if type(rows) is not int or type(cols) is not int or not (1 <= rows <= 1000 and 2 <= cols <= 1000):
    raise ProjectError("Invalid terminal dimensions.")
  return rows, cols


def terminal_options(root, message):
  rows, cols = dimensions(message)
  profile = message.get("shell", "default")
  if profile == "default":
    current = os.environ.get("SHELL", "")
    profile = Path(current).name if Path(current).name in ("zsh", "bash") else "zsh"
    shell = shutil.which(current) if Path(current).name == profile and Path(current).is_absolute() else None
    shell = shell or shutil.which(profile) or shutil.which("bash")
  elif profile in ("bash", "zsh"):
    shell = shutil.which(profile)
  else:
    shell = None
  if not shell:
    raise ProjectError("The requested terminal shell is unavailable.")
  cwd = message.get("cwd", "project")
  if cwd == "repository":
    cwd = repository(root) or root
  elif cwd == "project":
    cwd = root
  else:
    raise ProjectError("Invalid terminal starting directory.")
  command = message.get("command")
  if command is not None and (not isinstance(command, str) or "\x00" in command or len(command.encode()) > 32768):
    raise ProjectError("Invalid terminal command.")
  return dict(shell=shell, cwd=str(cwd), rows=rows, cols=cols, command=command)


class TerminalService:
  def __init__(self):
    self.stopped = threading.Event()
    self.active = set()
    self.lock = threading.Lock()

  def close(self):
    with self.lock:
      self.stopped.set()
      active = list(self.active)
    deadline = time.monotonic() + 5
    for thread in active:
      thread.join(max(0, deadline - time.monotonic()))

  def upgrade(self, handler, root, token, origins):
    if sys.platform not in {"darwin", "linux"}:
      handler._send(501, {"error": "Interactive terminals require macOS or Linux."})
      return
    protocols = [item.strip() for item in handler.headers.get("Sec-WebSocket-Protocol", "").split(",")]
    if (len(protocols) != 2 or protocols[0] != PROTOCOL
        or not hmac.compare_digest(protocols[1].encode(), token.encode())
        or handler.headers.get("Origin") not in origins):
      handler._send(403, {"error": "Studio terminal authorization required."})
      return
    thread = threading.current_thread()
    with self.lock:
      admitted = not self.stopped.is_set() and len(self.active) < 8
      if admitted:
        self.active.add(thread)
    if not admitted:
      handler._send(503, {"error": "Studio terminal session limit reached."})
      return
    try:
      ws = WSConnection(ConnectionType.SERVER)
      try:
        ws.initiate_upgrade_connection([(key.encode(), value.encode("latin1"))
                                        for key, value in handler.headers.raw_items()], handler.path)
        if not isinstance(next(ws.events(), None), Request):
          raise ProjectError("Invalid WebSocket upgrade")
        response = ws.send(AcceptConnection(subprotocol=PROTOCOL))
      except (ProjectError, ProtocolError, ValueError):
        handler._send(400, {"error": "Invalid terminal upgrade."})
        return
      handler.close_connection = True
      handler.connection.settimeout(2)
      handler.connection.sendall(response)
      handler.connection.setblocking(False)
      try:
        TerminalBridge(ws, handler.connection, root, self.stopped).run()
      finally:
        handler.connection.setblocking(True)
    except OSError:
      pass
    finally:
      with self.lock:
        self.active.remove(thread)


@contextmanager
def launch_pty(options):
  parent, child = socket.socketpair()
  process = None
  master = None
  try:
    with child:
      process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("terminal_worker.py")), str(child.fileno()), json.dumps(options)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, pass_fds=(child.fileno(),),
      )
    if not select.select([parent], [], [], 3)[0]:
      raise ProjectError("The terminal shell did not start.")
    descriptor = array.array("i")
    data, ancillary, _, _ = parent.recvmsg(4, socket.CMSG_SPACE(descriptor.itemsize), socket.MSG_WAITALL)
    for level, kind, payload in ancillary:
      if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
        descriptor.frombytes(payload[:descriptor.itemsize])
    if descriptor:
      master = descriptor[0]
    if len(data) != 4 or master is None:
      raise ProjectError("The terminal shell could not be started.")
    os.set_inheritable(master, False)
    os.set_blocking(master, False)
    yield parent, master, struct.unpack("!i", data)[0]
  finally:
    parent.close()
    if master is not None:
      os.close(master)
    if process is not None:
      try:
        process.wait(timeout=4)
      except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class TerminalBridge:
  def __init__(self, ws, connection, root, stopped):
    self.ws = ws
    self.connection = connection
    self.root = root
    self.stopped = stopped
    self.outgoing = bytearray()
    self.input = bytearray()
    self.fragments = bytearray()
    self.unacknowledged = 0
    self.closed = False
    self.explicit_close = False

  def queue(self, event):
    self.outgoing.extend(self.ws.send(event))
    if len(self.outgoing) > WINDOW * 2:
      raise ProjectError("Terminal output queue exceeded.")

  def notify(self, kind, **data):
    self.queue(TextMessage(data=json.dumps(dict(type=kind, **data))))

  def messages(self):
    try:
      data = self.connection.recv(CHUNK)
    except BlockingIOError:
      return
    if not data:
      self.closed = True
      return
    self.ws.receive_data(data)
    for event in self.ws.events():
      if isinstance(event, (TextMessage, BytesMessage)):
        self.fragments.extend(event.data.encode() if isinstance(event, TextMessage) else event.data)
        if len(self.fragments) > MAX_MESSAGE:
          raise ProjectError("Terminal message too large.")
        if event.message_finished:
          message = bytes(self.fragments)
          self.fragments.clear()
          yield json.loads(message) if isinstance(event, TextMessage) else message
      elif isinstance(event, CloseConnection):
        self.closed = True
        self.finish(event.response())
        return
      elif isinstance(event, Ping):
        self.queue(event.response())

  def flush(self):
    try:
      del self.outgoing[:self.connection.send(self.outgoing)]
    except BlockingIOError:
      pass

  def run(self):
    deadline = time.monotonic() + 5
    try:
      while not self.closed and not self.stopped.is_set() and time.monotonic() < deadline:
        reads, writes, _ = select.select([self.connection], [self.connection] if self.outgoing else [], [], 0.2)
        if writes:
          self.flush()
        if reads:
          for message in self.messages():
            if not isinstance(message, dict) or message.get("type") != "start":
              raise ProjectError("Expected a terminal start message.")
            options = terminal_options(self.root, message)
            with launch_pty(options) as (control, master, pid):
              try:
                self.notify("ready", pid=pid, shell=Path(options["shell"]).name, cwd=options["cwd"],
                            commandLine=options["shell"] + " -i")
                self.pump(control, master)
              finally:
                if self.explicit_close or self.stopped.is_set():
                  try:
                    control.send(b"x")
                  except OSError:
                    pass
            return
    except (ProjectError, ProtocolError, OSError, ValueError) as error:
      if not self.closed:
        try:
          self.notify("error", message=str(error))
        except (ProjectError, ProtocolError, ValueError):
          pass
    finally:
      if not self.closed:
        self.finish()

  def finish(self, event=None):
    try:
      self.queue(event or CloseConnection(code=1000))
      self.connection.settimeout(1)
      self.connection.sendall(self.outgoing)
    except (ProjectError, ProtocolError, OSError, ValueError):
      pass

  def handle(self, message, master):
    import termios

    if isinstance(message, bytes):
      if len(self.input) + len(message) > WINDOW:
        raise ProjectError("Terminal input queue exceeded.")
      self.input.extend(message)
      return
    if not isinstance(message, dict):
      raise ProjectError("Invalid terminal message.")
    match message.get("type"):
      case "ack":
        size = message.get("bytes")
        if type(size) is not int or not 0 < size <= self.unacknowledged:
          raise ProjectError("Invalid terminal output acknowledgement.")
        self.unacknowledged -= size
      case "resize":
        termios.tcsetwinsize(master, dimensions(message))
      case "close":
        self.explicit_close = True
      case _:
        raise ProjectError("Invalid terminal message.")

  def pump(self, control, master):
    eof = False
    code = None
    while not (self.closed or self.explicit_close or self.stopped.is_set()):
      if eof and code is not None:
        self.notify("exit", code=code)
        return
      reads = [self.connection]
      if code is None:
        reads.append(control)
      if not eof and self.unacknowledged < WINDOW:
        reads.append(master)
      writes = [self.connection] if self.outgoing else []
      if self.input and not eof and code is None:
        writes.append(master)
      reads, writes, _ = select.select(reads, writes, [], 0.2)
      if control in reads:
        status = control.recv(32)
        code = int(status) if status else 1
      if self.connection in writes:
        self.flush()
      if self.connection in reads:
        for message in self.messages():
          self.handle(message, master)
          if self.explicit_close:
            return
      if self.closed:
        return
      if master in reads:
        try:
          data = os.read(master, min(CHUNK, WINDOW - self.unacknowledged))
        except BlockingIOError:
          continue
        except OSError as error:
          if error.errno != errno.EIO:
            raise
          data = b""
        if data:
          self.unacknowledged += len(data)
          self.queue(BytesMessage(data=data))
        else:
          eof = True
      if master in writes and not eof and code is None:
        try:
          del self.input[:os.write(master, self.input)]
        except BlockingIOError:
          pass
