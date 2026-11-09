"""Project-scoped file browsing, text editing and bounded literal search."""

import hashlib
import os
from pathlib import Path
import tempfile

from texsys.project import ProjectError
from .ai import is_ai_config_path

SKIP_DIRECTORIES = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
                    ".pytest_cache", ".mypy_cache", ".ruff_cache"}
MAX_FILES = 5000
MAX_PREVIEW_BYTES = 1024 * 1024
MAX_SEARCH_MATCHES = 1000
MAX_SEARCH_BYTES = 32 * 1024 * 1024


def workspace_path(root: Path, relative: str, build: str, *, missing=False) -> Path:
  if not isinstance(relative, str) or not relative or "\0" in relative:
    raise ProjectError("请输入项目内的相对路径。")
  path = Path(relative)
  if path.is_absolute() or ".." in path.parts or not path.parts or any(part in SKIP_DIRECTORIES for part in path.parts):
    raise ProjectError("File must be inside the project source workspace.")
  root = root.resolve()
  candidate = root / path
  # Reject aliases even when their target happens to stay inside the workspace.
  if any((root.joinpath(*path.parts[:i])).is_symlink() for i in range(1, len(path.parts) + 1)):
    raise ProjectError("不允许通过符号链接访问项目文件。")
  candidate = candidate.resolve()
  if (candidate == root or not candidate.is_relative_to(root)
      or candidate.is_relative_to((root / build).resolve())
      or is_ai_config_path(candidate)
      or (not missing and not candidate.exists())):
    raise ProjectError("Project file not found.")
  return candidate


def _file_path(root: Path, relative: str, build: str) -> Path:
  candidate = workspace_path(root, relative, build)
  if not candidate.is_file():
    raise ProjectError("Project file not found.")
  return candidate


def project_files(root: Path, build: str) -> dict:
  root = root.resolve()
  output = (root / build).resolve()
  entries, folders = [], []
  for parent, directories, names in os.walk(root, followlinks=False):
    directories[:] = sorted(name for name in directories if name not in SKIP_DIRECTORIES
                            and not (Path(parent) / name).is_symlink()
                            and not (Path(parent) / name).resolve().is_relative_to(output))
    folders.extend((Path(parent) / name).relative_to(root).as_posix() for name in directories)
    if len(folders) >= MAX_FILES:
      return {"files": entries, "directories": folders[:MAX_FILES], "truncated": True}
    for name in sorted(names):
      file = Path(parent) / name
      if name == ".DS_Store" or file.is_symlink() or not file.is_file() or is_ai_config_path(file):
        continue
      try:
        size = file.stat().st_size
      except OSError:
        continue
      relative = file.relative_to(root)
      entries.append({"path": relative.as_posix(), "name": name,
                      "parent": "" if relative.parent == Path(".") else relative.parent.as_posix(), "size": size})
      if len(entries) >= MAX_FILES:
        return {"files": entries, "directories": folders, "truncated": True}
  return {"files": entries, "directories": folders, "truncated": False}


def version(content: bytes) -> str:
  return "sha256:" + hashlib.sha256(content).hexdigest()


def file_version(file: Path) -> str:
  stat = file.stat()
  if stat.st_size > MAX_PREVIEW_BYTES:
    return "stat:" + hashlib.sha256(repr((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)).encode()).hexdigest()
  return version(file.read_bytes())


def project_file(root: Path, relative: str, build: str) -> dict:
  file = _file_path(root, relative, build)
  size = file.stat().st_size
  result = {"path": file.relative_to(root.resolve()).as_posix(), "size": size, "previewable": False, "version": file_version(file)}
  if size > MAX_PREVIEW_BYTES:
    return {**result, "reason": "文件超过 1 MiB，暂不提供文本编辑。"}
  with file.open("rb") as stream:
    content = stream.read(MAX_PREVIEW_BYTES + 1)
  if len(content) > MAX_PREVIEW_BYTES:
    return {**result, "reason": "文件超过 1 MiB，暂不提供文本编辑。"}
  try:
    text = content.decode("utf-8-sig")
    if "\0" in text:
      raise UnicodeError("binary content")
  except UnicodeError:
    return {**result, "reason": "该文件不能以 UTF-8 文本编辑。"}
  return {**result, "previewable": True, "content": text.replace("\r\n", "\n").replace("\r", "\n"), "version": version(content)}


def file_operation(root: Path, build: str, request: dict) -> dict:
  from .runtime import Conflict
  action, relative = request.get("action"), request.get("path")
  file = workspace_path(root, relative, build, missing=action in {"create", "create_text", "mkdir"})
  if action in {"create", "mkdir"}:
    if file.exists():
      raise Conflict("路径已经存在，请刷新文件列表。")
    if not file.parent.is_dir():
      raise ProjectError("父目录不存在。")
    if action == "mkdir":
      file.mkdir()
    else:
      with file.open("xb"):
        pass
  elif action == "create_text":
    if not file.parent.is_dir():
      raise ProjectError("父目录不存在。")
    text = request.get("content")
    if not isinstance(text, str) or "\0" in text:
      raise ProjectError("文件内容必须是 UTF-8 文本。")
    try:
      raw = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeError as error:
      raise ProjectError("文件内容必须是 UTF-8 文本。") from error
    if len(raw) > MAX_PREVIEW_BYTES:
      raise ProjectError("文件超过 1 MiB。")
    descriptor, temporary = tempfile.mkstemp(prefix=".studio-", dir=file.parent)
    try:
      with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
      try:
        os.link(temporary, file)
      except FileExistsError as error:
        raise Conflict("目标路径已经存在。") from error
    finally:
      Path(temporary).unlink(missing_ok=True)
    return project_file(root, relative, build)
  elif action == "save":
    original = project_file(root, relative, build)
    if not original["previewable"]:
      raise ProjectError(original["reason"])
    if request.get("version") != original["version"]:
      raise Conflict("文件已被外部修改。请重新读取并比较后保存；编辑内容已保留。")
    text = request.get("content")
    if not isinstance(text, str) or "\0" in text:
      raise ProjectError("文件内容必须是 UTF-8 文本。")
    current_bytes = file.read_bytes()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    newline = "\r\n" if b"\r\n" in current_bytes else "\r" if b"\r" in current_bytes else "\n"
    raw = text.replace("\n", newline).encode("utf-8")
    if file.read_bytes().startswith(b"\xef\xbb\xbf"):
      raw = b"\xef\xbb\xbf" + raw
    if len(raw) > MAX_PREVIEW_BYTES:
      raise ProjectError("文件超过 1 MiB。")
    descriptor, temporary = tempfile.mkstemp(prefix=".studio-", dir=file.parent)
    try:
      with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
      os.chmod(temporary, file.stat().st_mode & 0o777)
      if version(file.read_bytes()) != request["version"]:
        raise Conflict("文件在保存期间发生变化，编辑内容已保留。")
      os.replace(temporary, file)
    finally:
      Path(temporary).unlink(missing_ok=True)
    return project_file(root, relative, build)
  elif action in {"rename", "delete"}:
    if file.is_file() and request.get("version") != file_version(file):
      raise Conflict("文件已发生变化，请刷新后重试。")
    if file.is_dir() and any(file.iterdir()):
      raise ProjectError("本轮仅支持重命名或删除空文件夹。")
    if action == "rename":
      target = workspace_path(root, request.get("target"), build, missing=True)
      if target.exists():
        raise Conflict("目标路径已经存在。")
      if not target.parent.is_dir():
        raise ProjectError("目标父目录不存在。")
      file.rename(target)
    elif file.is_dir():
      file.rmdir()
    else:
      file.unlink()
  else:
    raise ProjectError("未知文件操作。")
  return {"path": relative, "action": action}


def search_project(root: Path, build: str, query: str, *, case_sensitive=False, include="", exclude="", whole_word=False, regex=False) -> dict:
  """Compatibility entry point; shared search implementation lives in its own module."""
  from .search import search_project as search
  return search(root, build, query, case_sensitive=case_sensitive, include=include,
                exclude=exclude, whole_word=whole_word, regex=regex)
