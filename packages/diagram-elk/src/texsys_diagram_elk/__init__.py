"""Versioned local ELK worker adapter; never downloads at build time."""

import json
import os
from pathlib import Path

from texsys.evidence import file_digest

OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.randomSeed": "1",
  "elk.spacing.nodeNode": "90",
  "elk.layered.spacing.nodeNodeBetweenLayers": "150",
  "elk.spacing.edgeNode": "35",
  "elk.spacing.edgeEdge": "25",
  "elk.padding": "[top=40,left=40,bottom=40,right=40]",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
}


class BackendUnavailable(ValueError):
  pass


def resolve_backend() -> Path:
  override = os.environ.get("TEXSYS_ELK_ROOT")
  if override:
    candidates = [Path(override)]
  else:
    candidates = [p / "node_modules/elkjs" for p in Path(__file__).resolve().parents]
    candidates += [p / "node_modules/elkjs" for p in (Path.cwd(), *Path.cwd().parents)]
  for candidate in candidates:
    if (candidate / "lib/elk.bundled.js").is_file():
      lock = json.loads(Path(__file__).with_name("backend-lock.json").read_text())
      metadata = candidate / "package.json"
      if not metadata.is_file() or json.loads(metadata.read_text()).get("version") != lock["version"]:
        raise BackendUnavailable("ELK version mismatch; reinstall the pinned backend.")
      if file_digest(candidate / "lib/elk.bundled.js") != lock["bundle_digest"]:
        raise BackendUnavailable("ELK bundle digest mismatch; reinstall the pinned backend.")
      return candidate
  raise BackendUnavailable("ELK unavailable; run uv run tools/sync_node_tools.py at the repository root, or set TEXSYS_ELK_ROOT.")
