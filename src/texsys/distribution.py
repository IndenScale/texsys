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


CREDENTIAL_NAMES = frozenset({
  ".env", ".netrc", ".npmrc", ".pypirc", "studio-ai.json", "credentials.json", "service-account.json",
  "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
CREDENTIAL_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx", ".ppk"})


def distribution_block_reason(path: str) -> str | None:
  file = PurePosixPath(path.casefold())
  if (".ssh" in file.parts or file.name in CREDENTIAL_NAMES or file.suffix in CREDENTIAL_EXTENSIONS
      or (file.name.startswith(".env.") and file.name != ".env.example")):
    return "本机凭证或配置文件"
  if "refs" in file.parts or file.suffix in REFERENCE_EXTENSIONS:
    return "原始参考资料"
  return None


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
  blocked = False
  for path in sorted({os.fsdecode(name) for name in names if name}):
    if reason := distribution_block_reason(path):
      print(f"{reason}禁止提交或分发：{path!r}", file=sys.stderr)
      blocked = True
  return int(blocked)


if __name__ == "__main__":
  raise SystemExit(main())
