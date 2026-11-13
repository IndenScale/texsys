"""File policy for Git and engineering package distribution."""

import argparse
import os
from pathlib import PurePosixPath
import subprocess
import sys


REFERENCE_EXTENSIONS = frozenset({
  ".pdf", ".dwg", ".dxf", ".dgn", ".dwf", ".dwfx", ".rvt", ".rfa", ".ifc", ".ifczip", ".skp",
  ".step", ".stp", ".iges", ".igs", ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz",
  ".doc", ".docx", ".xls", ".xlsx",
})


def private_reference(path: str) -> bool:
  file = PurePosixPath(path.casefold())
  return "refs" in file.parts or file.suffix in REFERENCE_EXTENSIONS


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  selection = parser.add_mutually_exclusive_group(required=True)
  selection.add_argument("--staged", action="store_true")
  selection.add_argument("--push", action="store_true", help="Check the history selected by the pre-push hook.")
  selection.add_argument("--revision", help="Check the Git history reachable from this release commit.")
  options = parser.parse_args()
  if options.staged:
    arguments = ("diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ACMRT", "-z")
  else:
    revision = (os.environ.get("PRE_COMMIT_TO_REF") or "HEAD") if options.push else options.revision
    arguments = ("log", "--full-history", "-m", "--format=", "--name-only", "--no-renames",
                 "--diff-filter=ACMRT", "-z", revision, "--")
  names = subprocess.check_output(["git", *arguments]).split(b"\0")
  blocked = sorted({os.fsdecode(name) for name in names if name and private_reference(os.fsdecode(name))})
  for path in blocked:
    print(f"原始参考资料禁止提交或分发，请存放在本地 refs/：{path!r}", file=sys.stderr)
  return int(bool(blocked))


if __name__ == "__main__":
  raise SystemExit(main())
