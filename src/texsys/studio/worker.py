"""Isolated, cancellable Studio worker; never mutates the open project."""

import json
from pathlib import Path
import sys
import traceback

from texsys.evidence import write_json
from . import discover_plugins


def main():
  directory = Path(sys.argv[1])
  request = json.loads((directory / "job.json").read_text())
  plugins, diagnostics = discover_plugins()
  try:
    print(f"Starting {request['action']} on immutable project input copy.", flush=True)
    if request["plugin"] not in plugins:
      raise ValueError(f"Plugin unavailable: {diagnostics}")
    result = plugins[request["plugin"]].execute(directory / "project", request["action"], request["options"])
    write_json(directory / "response.json", result)
    print(f"Task completed; engineering conclusion: {result.get('status', 'indeterminate')}", flush=True)
    return 0
  except Exception as error:
    traceback.print_exc()
    write_json(directory / "response.json", {"status": "error", "message": str(error)})
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
