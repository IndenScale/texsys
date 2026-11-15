"""Publish the official AIDC reference design closure to the local read-only registry."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import urllib.request


REPO = Path(__file__).resolve().parents[1]
ROOTS = ("datacenter-reference", "sheet")


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--registry", type=Path, default=REPO / "build/package-registry")
  parser.add_argument("--origin", default="http://127.0.0.1:8711")
  options = parser.parse_args()
  registry = options.registry.resolve()
  published = []
  for name in ROOTS:
    result = subprocess.run(
      [sys.executable, "-m", "texsys", "package", "publish", str(REPO / "packages" / name),
       "--registry", str(registry)],
      cwd=REPO, capture_output=True, text=True, check=False, timeout=300,
    )
    if result.returncode != 0:
      print(result.stderr, file=sys.stderr)
      return 1
    published.extend(json.loads(result.stdout)["packages"])
  verified = []
  for item in published:
    url = f"{options.origin}/packages/{item['name']}/{item['version']}/package.json"
    try:
      with urllib.request.urlopen(url, timeout=10) as response:
        metadata = json.load(response)
    except OSError as error:
      print(f"Registry origin unavailable ({options.origin}); check the dev container: {error}", file=sys.stderr)
      return 1
    if metadata["digest"] != item["digest"] or metadata["archive_digest"] != item["archive_digest"]:
      print(f"HTTP release does not match published content: {url}", file=sys.stderr)
      return 1
    verified.append({"name": item["name"], "version": item["version"], "url": url,
                     "digest": item["digest"], "already_published": item["already_published"]})
  summary = {"registry": str(registry), "origin": options.origin, "packages": verified}
  (registry.parent / "package-registry-publication.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
  print(json.dumps(summary, ensure_ascii=False, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
