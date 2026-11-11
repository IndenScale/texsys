"""Fresh Python execution and a narrow JSON protocol for engineering operations."""

from contextlib import redirect_stdout
import hashlib
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sys


class TrackedSourceLoader(SourceFileLoader):
  def get_code(self, fullname):
    # Compile exactly the bytes whose digest is recorded; never infer a loaded
    # implementation from a source file read after import or from stale pyc.
    source = self.get_data(self.path)
    code = self.source_to_code(source, self.path)
    sys._texsys_loaded_sources[str(Path(self.path).resolve())] = "sha256:" + hashlib.sha256(source).hexdigest()
    return code


class TrackedSourceFinder:
  finders = ()

  @classmethod
  def find_spec(cls, fullname, path=None, target=None):
    for finder in cls.finders:
      spec = finder.find_spec(fullname, path, target)
      if spec is not None:
        if isinstance(spec.loader, SourceFileLoader):
          spec.loader = TrackedSourceLoader(fullname, spec.loader.path)
        return spec
    return None


def main():
  sys._texsys_loaded_sources = {}
  TrackedSourceFinder.finders = tuple(sys.meta_path)
  # Keep the original finders visible to importlib.metadata.find_distributions.
  sys.meta_path.insert(0, TrackedSourceFinder)
  try:
    request = json.load(sys.stdin)
    if (not isinstance(request, dict) or set(request) != {"version", "action", "arguments"}
        or type(request["version"]) is not int or request["version"] != 1):
      raise ValueError("Invalid engineering worker request.")
    # Adapter output is ordinary log text, never part of the protocol stream.
    with redirect_stdout(sys.stderr):
      from texsys.engineering import _dispatch
      result = _dispatch(request["action"], request["arguments"])
    response = {"ok": True, "result": result}
    code = 0
  except Exception as error:
    response = {"ok": False, "error": str(error)}
    code = 1
  print(json.dumps(response, ensure_ascii=False, allow_nan=False))
  return code


if __name__ == "__main__":
  raise SystemExit(main())
