"""Read-only project file navigation for the shared search center."""

import os
from pathlib import Path

from texsys.project import ProjectError

SKIP_DIRECTORIES = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
                    ".pytest_cache", ".mypy_cache", ".ruff_cache"}
MAX_FILES = 5000
MAX_PREVIEW_BYTES = 1024 * 1024


def _file_path(root: Path, relative: str, build: str) -> Path:
  path = Path(relative)
  if not relative or path.is_absolute() or ".." in path.parts or any(part in SKIP_DIRECTORIES for part in path.parts):
    raise ProjectError("File must be inside the project source workspace.")
  candidate = (root / path).resolve()
  root = root.resolve()
  output = (root / build).resolve()
  if (not candidate.is_relative_to(root) or candidate.is_relative_to(output) or not candidate.is_file()
      or any(part in SKIP_DIRECTORIES for part in candidate.relative_to(root).parts)):
    raise ProjectError("Project file not found.")
  return candidate


def project_files(root: Path, build: str) -> dict:
  root = root.resolve()
  output = (root / build).resolve()
  entries = []
  for parent, directories, names in os.walk(root, followlinks=False):
    directories[:] = sorted(name for name in directories if name not in SKIP_DIRECTORIES
                            and not (Path(parent) / name).is_symlink()
                            and not (Path(parent) / name).resolve().is_relative_to(output))
    for name in sorted(names):
      file = Path(parent) / name
      if name == ".DS_Store" or file.is_symlink() or not file.is_file():
        continue
      try:
        size = file.stat().st_size
      except OSError:
        continue
      relative = file.relative_to(root)
      entries.append({"path": relative.as_posix(), "name": name,
                      "parent": "" if relative.parent == Path(".") else relative.parent.as_posix(), "size": size})
      if len(entries) >= MAX_FILES:
        return {"files": entries, "truncated": True}
  return {"files": entries, "truncated": False}


def project_file(root: Path, relative: str, build: str) -> dict:
  file = _file_path(root, relative, build)
  size = file.stat().st_size
  result = {"path": relative, "size": size, "previewable": False}
  if size > MAX_PREVIEW_BYTES:
    return {**result, "reason": "文件超过 1 MiB，暂不提供文本预览。"}
  with file.open("rb") as stream:
    content = stream.read(MAX_PREVIEW_BYTES + 1)
  if len(content) > MAX_PREVIEW_BYTES:
    return {**result, "reason": "文件超过 1 MiB，暂不提供文本预览。"}
  try:
    text = content.decode("utf-8-sig")
    if "\0" in text:
      raise UnicodeError("binary content")
  except UnicodeError:
    return {**result, "reason": "该文件不能以 UTF-8 文本预览。"}
  return {**result, "previewable": True, "content": text}
