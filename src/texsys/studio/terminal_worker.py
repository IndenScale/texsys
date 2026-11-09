"""Single-threaded POSIX PTY launcher and shell supervisor for Studio."""

import array
import json
import os
from pathlib import Path
import pty
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time


def shell_startup(shell, directory, env):
  """Install a prompt notification after the user's usual interactive configuration."""
  if Path(shell).name == "bash":
    rc = directory / "bashrc"
    rc.write_text(r'''[[ ! -r ~/.bashrc ]] || source ~/.bashrc
_texsys_command_done() { printf '\033]7777;texsys-command-done\007'; }
if [[ $(declare -p PROMPT_COMMAND 2>/dev/null) == 'declare -a '* ]]; then
  PROMPT_COMMAND+=(_texsys_command_done)
else
  PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND$'\n'}_texsys_command_done"
fi
''')
    return [shell, "--rcfile", str(rc), "-i"]

  original = env.get("ZDOTDIR", env["HOME"])
  env["TEXSYS_ZSH_CONFIG"] = original
  env["TEXSYS_ZSH_WRAPPER"] = str(directory)
  env["ZDOTDIR"] = str(directory)
  (directory / ".zshenv").write_text('''ZDOTDIR="$TEXSYS_ZSH_CONFIG"
[[ ! -r "$ZDOTDIR/.zshenv" ]] || source "$ZDOTDIR/.zshenv"
TEXSYS_ZSH_CONFIG="${ZDOTDIR:-$HOME}"
ZDOTDIR="$TEXSYS_ZSH_WRAPPER"
''')
  (directory / ".zshrc").write_text(r'''ZDOTDIR="$TEXSYS_ZSH_CONFIG"
unset TEXSYS_ZSH_CONFIG TEXSYS_ZSH_WRAPPER
[[ ! -r "$ZDOTDIR/.zshrc" ]] || source "$ZDOTDIR/.zshrc"
autoload -Uz add-zsh-hook
_texsys_command_done() { printf '\033]7777;texsys-command-done\007'; }
add-zsh-hook precmd _texsys_command_done
''')
  return [shell, "-i"]


def session_members(leader):
  # Job control puts foreground and background jobs in separate process groups.
  listing = subprocess.check_output(["/bin/ps", "-axo", "pid="], timeout=1)
  members = []
  for entry in listing.split():
    pid = int(entry)
    try:
      if os.getsid(pid) == leader:
        members.append(pid)
    except (ProcessLookupError, PermissionError):
      pass
  return members


def signal_session(leader, sig):
  for pid in session_members(leader):
    try:
      os.kill(pid, sig)
    except ProcessLookupError:
      pass


def supervise(control, pid):
  while True:
    ended, status = os.waitpid(pid, os.WNOHANG)
    if ended:
      signal_session(pid, signal.SIGHUP)
      signal_session(pid, signal.SIGKILL)
      return os.waitstatus_to_exitcode(status)
    if select.select([control], [], [], 0.1)[0]:
      if not control.recv(1):
        time.sleep(0.75)
      break
  signal_session(pid, signal.SIGHUP)
  time.sleep(0.15)
  signal_session(pid, signal.SIGKILL)
  return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])


def main(control, request):
  shell = request["shell"]
  env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
  env.setdefault("HOME", str(Path.home()))
  env.pop("TEX_STUDIO_TOKEN", None)
  with tempfile.TemporaryDirectory(prefix="texsys-pty-") as temporary:
    argv = shell_startup(shell, Path(temporary), env)
    if request.get("command"):
      argv += ["-c", request["command"]]
    # The handshake pipe ensures the slave's initial size is set before the shell
    # runs, including when a menu command starts immediately after exec.
    ready_read, ready_write = os.pipe()
    pid, master = pty.fork()
    if pid == 0:
      try:
        control.close()
        os.close(ready_write)
        if not os.read(ready_read, 1):
          os._exit(1)
        os.close(ready_read)
        for name in ("SIGPIPE", "SIGXFSZ", "SIGXFZ"):
          if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)
        os.chdir(request["cwd"])
        os.execve(shell, argv, env)
      except OSError as error:
        os.write(2, f"{error}\r\n".encode(errors="replace"))
        os._exit(126)
    os.close(ready_read)
    try:
      termios.tcsetwinsize(master, (request["rows"], request["cols"]))
      os.write(ready_write, b"1")
      control.sendmsg([struct.pack("!i", pid)], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [master]))])
    except OSError:
      signal_session(pid, signal.SIGKILL)
      os.waitpid(pid, 0)
      return
    finally:
      os.close(ready_write)
      os.close(master)
    code = supervise(control, pid)
    try:
      control.send(str(code).encode())
    except OSError:
      pass


if __name__ == "__main__":
  with socket.socket(fileno=int(sys.argv[1])) as channel:
    main(channel, json.loads(sys.argv[2]))
