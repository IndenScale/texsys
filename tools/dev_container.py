"""Install and manage the macOS login services for the Tex Studio development container."""

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MACHINE = "podman-machine-default"
VM = "local.texsys.podman-machine"
STUDIO = "local.texsys.studio"
CONTAINER = "texsys-studio-dev"
IMAGE = "localhost/texsys-studio-dev:local"
WORKSPACE = "/workspace/texsys"
DOMAIN = f"gui/{os.getuid()}"
AGENTS = Path.home() / "Library/LaunchAgents"
LOGS = Path.home() / "Library/Logs/TexSys"
LOCK = Path(f"/tmp/texsys-studio-{os.getuid()}.lock")
OWNER = f"{STUDIO} {ROOT}\n"


def run(*args, capture=False, check=True, timeout=None):
  return subprocess.run(args, check=check, text=True, capture_output=capture, timeout=timeout)


def output(*args):
  return run(*args, capture=True, timeout=20).stdout.strip()


def job(label):
  return f"{DOMAIN}/{label}"


def loaded(label):
  return run("launchctl", "print", job(label), capture=True, check=False).returncode == 0


def bootout(label):
  if loaded(label):
    run("launchctl", "bootout", job(label))
    for _ in range(40):
      if not loaded(label):
        return
      time.sleep(1)
    raise RuntimeError(f"launchd service did not unload: {job(label)}")


def bootstrap(label, podman):
  path = AGENTS / f"{label}.plist"
  AGENTS.mkdir(parents=True, exist_ok=True)
  LOGS.mkdir(parents=True, exist_ok=True)
  config = {
    "Label": label,
    "ProgramArguments": ["/usr/bin/python3", str(Path(__file__).resolve()), "studio-service", "--podman", podman],
    "EnvironmentVariables": {"PATH": f"{Path(podman).parent}:/usr/bin:/bin:/usr/sbin:/sbin"},
    "RunAtLoad": True,
    "ThrottleInterval": 15,
    "StandardOutPath": str(LOGS / f"{label}.log"),
    "StandardErrorPath": str(LOGS / f"{label}.log"),
  }
  if label == VM:
    # Podman starts the VM and its network forwarder as background children.
    config.update(
      AbandonProcessGroup=True,
      StartInterval=60,
      ProgramArguments=["/bin/sh", "-ec", '''
state=$("$1" machine inspect --format '{{.State}}' "$2")
if [ "$state" = stopped ]; then exec "$1" machine start --no-info "$2"; fi
''', "podman-machine", podman, MACHINE],
    )
  else:
    config.update(KeepAlive=True, ExitTimeOut=35)
  data = plistlib.dumps(config)
  if path.is_symlink() or (path.exists() and path.read_bytes() != data):
    raise RuntimeError(f"Refusing to overwrite a different launchd configuration: {path}")
  if not path.exists():
    with path.open("xb") as stream:
      stream.write(data)
  run("launchctl", "enable", job(label))
  if not loaded(label):
    run("launchctl", "bootstrap", DOMAIN, str(path))


def machine_state(podman):
  return output(podman, "machine", "inspect", "--format", "{{.State}}", MACHINE)


def engine(podman):
  rootful = output(podman, "machine", "inspect", "--format", "{{.Rootful}}", MACHINE)
  return (podman, "--connection", MACHINE + ("-root" if rootful == "true" else ""))


def wait_for_engine(podman, cli):
  for _ in range(40):
    if machine_state(podman) == "running" and run(*cli, "info", capture=True, check=False, timeout=10).returncode == 0:
      return
    time.sleep(2)
  raise RuntimeError(f"Podman is not ready; see {LOGS / (VM + '.log')}.")


def owns_container(cli):
  result = run(*cli, "container", "exists", CONTAINER, check=False, timeout=20)
  if result.returncode == 1:
    return False
  result.check_returncode()
  if output(*cli, "inspect", "--format", '{{index .Config.Labels "local.texsys.checkout"}}', CONTAINER) != str(ROOT):
    raise RuntimeError(f"Container {CONTAINER} belongs to a different checkout.")
  return True


def running(cli):
  return output(*cli, "inspect", "--format", "{{.State.Running}}", CONTAINER) == "true"


def check_port():
  result = run("/usr/sbin/lsof", "-nP", "-iTCP:8710", "-sTCP:LISTEN", capture=True, check=False)
  if result.returncode != 1 or result.stderr:
    raise RuntimeError(f"Cannot claim port 8710: {result.stdout or result.stderr}")


def wait_for_container(cli, stopping):
  # Hold a single API request instead of opening an SSH connection every poll.
  waiter = subprocess.Popen([*cli, "wait", CONTAINER], stdout=subprocess.DEVNULL)
  try:
    while not stopping():
      try:
        code = waiter.wait(timeout=2)
      except subprocess.TimeoutExpired:
        continue
      if code and not stopping():
        raise subprocess.CalledProcessError(code, waiter.args)
      break
  finally:
    if waiter.poll() is None:
      waiter.kill()
      waiter.wait()


def supervise(podman, cli):
  stopping = False

  def request_stop(*_):
    nonlocal stopping
    stopping = True

  signal.signal(signal.SIGTERM, request_stop)
  signal.signal(signal.SIGINT, request_stop)
  if machine_state(podman) != "running":
    return
  if not owns_container(cli):
    raise RuntimeError("Run tools/dev_container.py install to provision the container.")
  try:
    LOCK.mkdir()
  except FileExistsError:
    if (LOCK / "owner").read_text() != OWNER:
      raise RuntimeError(f"Studio lock is held by another owner: {LOCK}")
  else:
    (LOCK / "owner").write_text(OWNER)
  try:
    if not stopping and not running(cli):
      check_port()
      run(*cli, "start", CONTAINER, timeout=30)
    if not stopping:
      wait_for_container(cli, lambda: stopping)
  finally:
    if stopping and running(cli):
      run(*cli, "stop", "--time", "8", CONTAINER, timeout=15)
    if not running(cli) and (LOCK / "owner").read_text() == OWNER:
      (LOCK / "owner").unlink()
      LOCK.rmdir()


def provision(cli):
  context = ROOT / "tools/dev-container"
  run(
    *cli, "build", "--tag", IMAGE,
    "--build-arg", f"DEV_UID={os.getuid()}", "--build-arg", f"DEV_GID={os.getgid()}", str(context),
  )


def create(cli):
  run(
    *cli, "create", "--name", CONTAINER, "--init",
    "--label", f"local.texsys.checkout={ROOT}",
    "--publish", "127.0.0.1:8710:8710",
    "--volume", f"{ROOT}:{WORKSPACE}",
    "--volume", "texsys-dev-python:/var/texsys",
    "--volume", f"texsys-dev-node:{WORKSPACE}/node_modules",
    "--volume", "texsys-dev-home:/home/texsys",
    IMAGE,
  )


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("action", choices=("install", "rebuild", "restart", "status", "shell", "studio-service"))
  parser.add_argument("--podman", default=shutil.which("podman"))
  args = parser.parse_args()
  if sys.platform != "darwin" or not args.podman or os.getuid() == 0:
    parser.error("Run on macOS as the Podman machine owner, with podman on PATH.")
  podman = os.path.realpath(args.podman)

  cli = engine(podman)
  if args.action == "studio-service":
    supervise(podman, cli)
  elif args.action in {"install", "rebuild"}:
    bootstrap(VM, podman)
    wait_for_engine(podman, cli)
    exists = owns_container(cli)
    if not exists or args.action == "rebuild":
      provision(cli)
      if exists:
        bootout(STUDIO)
        if running(cli):
          raise RuntimeError("Existing container is still running; refusing to replace it.")
        run(*cli, "rm", CONTAINER)
      create(cli)
    bootstrap(STUDIO, podman)
    print(f"Studio: http://127.0.0.1:8710/  |  Logs: {LOGS}", flush=True)
  elif args.action == "status":
    state = machine_state(podman)
    print(f"Podman VM: {state}; login service loaded: {loaded(VM)}", flush=True)
    print(f"Studio login service loaded: {loaded(STUDIO)}; lock: {LOCK}", flush=True)
    if state == "running":
      run(*cli, "ps", "-a", "--filter", f"name=^{CONTAINER}$")
  elif owns_container(cli):
    if args.action == "shell":
      os.execv(podman, [*cli, "exec", "-it", CONTAINER, "bash"])
    if not loaded(STUDIO):
      raise RuntimeError("Studio login service is not loaded; run install first.")
    run(*cli, "stop", "--ignore", CONTAINER, timeout=20)
    run("launchctl", "kickstart", job(STUDIO))
  else:
    raise RuntimeError("Development container is missing; run install first.")


if __name__ == "__main__":
  try:
    main()
  except (OSError, RuntimeError, subprocess.SubprocessError) as error:
    if isinstance(error, subprocess.CalledProcessError) and error.stderr:
      print(error.stderr.strip(), file=sys.stderr)
    raise SystemExit(str(error)) from error
