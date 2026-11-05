"""Local TypeScript geometry and React/SVG bridge with shared export inputs."""

import json
from pathlib import Path
import shutil
import subprocess

from texsys.evidence import file_digest
from texsys_diagram import digest
from texsys_diagram_elk import BackendUnavailable, OPTIONS, resolve_backend


def _runtime_root() -> Path:
  for directory in (*Path(__file__).resolve().parents, Path.cwd(), *Path.cwd().parents):
    if (directory / "node_modules/react/package.json").is_file() and (directory / "node_modules/esbuild-wasm/package.json").is_file():
      return directory
  raise BackendUnavailable("Graphics dependencies unavailable; run uv run tools/sync_node_tools.py.")


def _invoke(operation: str, document: dict, results=None) -> dict:
  node = shutil.which("node")
  if not node:
    raise BackendUnavailable("Node.js is required for parameterized engineering graphics.")
  root = _runtime_root()
  backend = resolve_backend()
  assets = Path(__file__).parent / "assets"
  pins = {"react": "19.2.0", "react-dom": "19.2.0", "esbuild-wasm": "0.25.12"}
  for name, version in pins.items():
    if json.loads((root / "node_modules" / name / "package.json").read_text())["version"] != version:
      raise BackendUnavailable(f"Graphics dependency version mismatch: {name}; expected {version}.")
  payload = {"schema_version": 1, "operation": operation, "document": document,
             "layout_options": {**OPTIONS, "elk.layered.spacing.nodeNodeBetweenLayers": "110", "elk.spacing.nodeNode": "65"}, "results": results}
  try:
    result = subprocess.run([node, str(assets / "worker.mjs"), str(root), str(backend / "lib/elk.bundled.js")],
                            input=json.dumps(payload, ensure_ascii=False, allow_nan=False),
                            text=True, capture_output=True, timeout=90, check=False)
  except subprocess.TimeoutExpired as error:
    raise BackendUnavailable("Engineering graphics worker timed out.") from error
  if result.returncode:
    raise ValueError(f"Engineering graphics worker failed: {result.stderr[-2000:]}")
  response = json.loads(result.stdout)
  if response.get("schema_version") != 1:
    raise ValueError("Invalid engineering graphics worker response.")
  return response


def prepare_layout(document: dict) -> dict:
  response = _invoke("prepare", document)
  document.clear()
  document.update(response["document"])
  for item in document["nodes"]:
    item["symbol"]["symbol_digest"] = digest(item["symbol"]["actual"])
    item["symbol"]["catalog_digest"] = response["metadata"]["source_digest"]
  document["render_backend"] = "react-svg/1"
  backend = resolve_backend()
  return {"engine": "elkjs", "version": "0.10.2", "bundle_digest": file_digest(backend / "lib/elk.bundled.js"),
          "graphics": response["metadata"], "protocol": 1,
          "options": {**OPTIONS, "elk.layered.spacing.nodeNodeBetweenLayers": "110", "elk.spacing.nodeNode": "65"}}


def render(document: dict, results=None) -> dict:
  response = _invoke("render", document, results)
  if document.get("backend", {}).get("graphics", {}).get("source_digest") != response["metadata"]["source_digest"]:
    raise ValueError("Graphics sources changed after layout; rebuild the drawing before rendering.")
  return {key: response[key] for key in ("svg", "html")}
