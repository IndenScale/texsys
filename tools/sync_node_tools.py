"""Extract integrity-checked package-lock archives; never execute lifecycle scripts.

This repository uses portable JS/WASM packages. Invoke their CLI files with node.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def install(item):
  location, package = item
  target = ROOT / location
  if not target.is_relative_to(ROOT / "node_modules") or ".." in Path(location).parts:
    raise ValueError(f"Invalid package location: {location}")
  operating_system = {"darwin": "darwin", "win32": "win32"}.get(sys.platform, "linux")
  architecture = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(platform.machine())
  if package.get("os") and operating_system not in package["os"] or package.get("cpu") and architecture not in package["cpu"]:
    return
  url = package["resolved"]
  if not url.startswith("https://registry.npmjs.org/"):
    raise ValueError(f"Unexpected registry: {url}")
  with urllib.request.urlopen(url, timeout=60) as response:
    archive = response.read()
  algorithm, expected = package["integrity"].split("-", 1)
  if algorithm != "sha512" or base64.b64encode(hashlib.sha512(archive).digest()).decode() != expected:
    raise ValueError(f"Archive integrity mismatch: {location}")
  # Replace package contents without deleting nested lock entries.
  target.mkdir(parents=True, exist_ok=True)
  with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
    for member in bundle.getmembers():
      parts = Path(member.name).parts
      if not parts:
        continue
      relative = Path(*parts[1:])
      if relative.is_absolute() or ".." in relative.parts or not member.isfile():
        continue
      stream = bundle.extractfile(member)
      if stream is None:
        raise ValueError(f"Missing member: {member.name}")
      path = target / relative
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_bytes(stream.read())
  if json.loads((target / "package.json").read_text())["version"] != package["version"]:
    raise ValueError(f"Version mismatch: {location}")
  return f"Verified {location} {package['version']}"


def main():
  lock = json.loads((ROOT / "package-lock.json").read_text())
  with ThreadPoolExecutor(max_workers=6) as pool:
    for message in pool.map(install, ((name, value) for name, value in lock["packages"].items() if name)):
      if message:
        print(message)


if __name__ == "__main__":
  main()
