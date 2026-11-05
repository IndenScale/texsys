"""Deterministic artifact and evidence helpers."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def canonical_digest(value: Any) -> str:
  """Content identity shared by engineering snapshots and derived artifacts."""
  return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                             allow_nan=False).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
  return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def write_json(path: Path, value: Any) -> None:
  content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
  path.parent.mkdir(parents=True, exist_ok=True)
  descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
  temporary = Path(temporary_name)
  try:
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
      stream.write(content)
    os.replace(temporary, path)
  finally:
    temporary.unlink(missing_ok=True)
