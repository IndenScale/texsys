"""Short-lived regex worker so expensive expressions cannot hang the Studio API."""
import json
from pathlib import Path
import sys

from texsys.project import ProjectError
from .search import _replace_plan, _scan

if __name__ == "__main__":
  try:
    request = json.load(sys.stdin)
    function = _replace_plan if request["action"] == "replace" else _scan
    arguments = [Path(request["root"]), request["build"], request["query"], request["options"]]
    if request["action"] == "replace":
      arguments.append(request["replacement"])
    print(json.dumps(function(*arguments)))
  except (ProjectError, ValueError, TypeError, KeyError) as error:
    print(json.dumps({"error": str(error)}))
    sys.exit(1)
