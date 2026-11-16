"""Project-scoped file browsing, text editing and bounded literal search."""

import hashlib
import json
import os
import re
from pathlib import Path
import tempfile

from texsys.project import ProjectError
from texsys.evidence import canonical_digest
from .ai import is_ai_config_path
from .jsonc import strip_jsonc
from .preview import ProjectPreview

PROTECTED_DIRECTORIES = {".git", ".hg", ".svn"}
# VS Code default files.exclude plus product performance skips. Workspace
# .vscode/settings.json may override each pattern with true (hide) / false (show).
DEFAULT_EXCLUDES = {
    "**/.git": True, "**/.svn": True, "**/.hg": True, "**/CVS": True,
    "**/.DS_Store": True, "**/Thumbs.db": True,
    "**/node_modules": True, "**/.venv": True, "**/venv": True,
    "**/__pycache__": True, "**/.pytest_cache": True, "**/.mypy_cache": True,
    "**/.ruff_cache": True,
}
MAX_FILES = 5000
MAX_PREVIEW_BYTES = 1024 * 1024
MAX_SEARCH_MATCHES = 1000
MAX_SEARCH_BYTES = 32 * 1024 * 1024


def private_build_path(root: Path, file: Path, build: str) -> bool:
  return _private_build_path(file, (root / build).resolve())


def _private_build_path(file: Path, output: Path) -> bool:
  if not file.is_relative_to(output):
    return False
  parts = file.relative_to(output).parts
  return bool(parts) and (parts[0].casefold() in {"runs", "dependencies", "studio"}
                          or any(part.startswith(".") for part in parts))


def load_excludes(root: Path) -> dict:
  excludes = dict(DEFAULT_EXCLUDES)
  try:
    settings = json.loads(strip_jsonc((root / ".vscode" / "settings.json").read_text(encoding="utf-8")))
  except (OSError, ValueError):
    return excludes
  overrides = settings.get("files.exclude") if isinstance(settings, dict) else None
  if isinstance(overrides, dict):
    for pattern, value in overrides.items():
      if isinstance(pattern, str) and isinstance(value, bool):
        excludes[pattern] = value
  return excludes


def _exclude_regex(pattern: str) -> re.Pattern:
  if "/" not in pattern:
    pattern = "**/" + pattern
  parts = []
  index = 0
  while index < len(pattern):
    char = pattern[index]
    if char == "*":
      if pattern[index:index + 3] == "**/":
        parts.append("(?:[^/]+/)*")
        index += 3
        continue
      if pattern[index:index + 2] == "**":
        parts.append(".*")
        index += 2
        continue
      parts.append("[^/]*")
    elif char == "?":
      parts.append("[^/]")
    else:
      parts.append(re.escape(char))
    index += 1
  return re.compile("^" + "".join(parts) + "$")


def exclude_matcher(root: Path):
  rules = [_exclude_regex(pattern) for pattern, hide in load_excludes(root).items() if hide]
  return lambda relative: any(rule.match(relative) for rule in rules)


def workspace_path(root: Path, relative: str, build: str, *, missing=False) -> Path:
  if not isinstance(relative, str) or not relative or "\0" in relative:
    raise ProjectError("请输入项目内的相对路径。")
  path = Path(relative)
  if path.is_absolute() or ".." in path.parts or not path.parts or any(part in PROTECTED_DIRECTORIES for part in path.parts):
    raise ProjectError("File must be inside the project source workspace.")
  root = root.resolve()
  candidate = root / path
  # Reject aliases even when their target happens to stay inside the workspace.
  if any((root.joinpath(*path.parts[:i])).is_symlink() for i in range(1, len(path.parts) + 1)):
    raise ProjectError("不允许通过符号链接访问项目文件。")
  candidate = candidate.resolve()
  output = (root / build).resolve()
  if (candidate == root or not candidate.is_relative_to(root)
      or candidate == output
      or _private_build_path(candidate, output)
      or is_ai_config_path(candidate)
      or (not missing and not candidate.exists())):
    raise ProjectError("Project file not found.")
  return candidate


def _file_path(root: Path, relative: str, build: str) -> Path:
  candidate = workspace_path(root, relative, build)
  if not candidate.is_file():
    raise ProjectError("Project file not found.")
  return candidate


def project_files(root: Path, build: str, *, previews: ProjectPreview | None = None, compact=False) -> dict:
  root = root.resolve()
  output = (root / build).resolve()
  excluded = exclude_matcher(root)
  if compact:
    # The Explorer and quick picker need names and paths. Opening a document
    # resolves its actual preview and validates any claimed build identity.
    metadata = {}
  else:
    previews = previews if previews is not None else ProjectPreview(root, build)
    metadata = {entry["path"]: entry for entry in previews.file_entries(previews_only=True)}
  entries, folders = [], []
  pending = [root]
  while pending:
    parent = pending.pop()
    if parent != root and parent.is_symlink():
      continue
    directories, names = [], []
    try:
      with os.scandir(parent) as items:
        for item in sorted(items, key=lambda item: item.name):
          file = parent / item.name
          relative = file.relative_to(root).as_posix()
          if excluded(relative) or _private_build_path(file, output):
            continue
          if item.is_dir(follow_symlinks=False):
            directories.append(file)
            folders.append(relative)
          elif item.is_file(follow_symlinks=False) and not is_ai_config_path(file):
            try:
              size = {} if compact else {"size": item.stat(follow_symlinks=False).st_size}
            except OSError:
              continue
            names.append((file, relative, size))
    except OSError:
      continue
    pending.extend(reversed(directories))
    if len(folders) >= MAX_FILES:
      return {"files": entries, "directories": folders[:MAX_FILES], "truncated": True}
    relative_parent = parent.relative_to(root).as_posix() if parent != root else ""
    for file, relative, size in names:
      entry = {**metadata.get(relative, {}), "path": relative, "name": file.name, "parent": relative_parent, **size}
      if file.is_relative_to(output):
        entry["generated"] = True
      entries.append(entry)
      if len(entries) >= MAX_FILES:
        return {"files": entries, "directories": folders, "truncated": True}
  return {"files": entries, "directories": folders, "truncated": False}


def version(content: bytes) -> str:
  return "sha256:" + hashlib.sha256(content).hexdigest()


def file_version(file: Path) -> str:
  stat = file.stat()
  if stat.st_size > MAX_PREVIEW_BYTES:
    return _stat_version(stat)
  return version(file.read_bytes())


def _stat_version(stat) -> str:
  return "stat:" + hashlib.sha256(repr((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)).encode()).hexdigest()


_UTF8_BOM = b"\xef\xbb\xbf"
_LINE_ENDINGS = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}


def _text_format(raw: bytes) -> dict:
  without_pairs = raw.replace(b"\r\n", b"")
  kinds = (
    (["CRLF"] if len(without_pairs) < len(raw) else [])
    + (["CR"] if b"\r" in without_pairs else [])
    + (["LF"] if b"\n" in without_pairs else [])
  )
  return {
    "encoding": "utf8bom" if raw.startswith(_UTF8_BOM) else "utf8",
    "lineEnding": "mixed" if len(kinds) > 1 else kinds[0] if kinds else "LF",
  }


def _encode_text(text, request, original_format=None, original_bytes=None):
  if not isinstance(text, str) or "\0" in text:
    raise ProjectError("文件内容必须是 UTF-8 文本。")
  line_ending = request.get("lineEnding")
  encoding = request.get("encoding")
  if "lineEnding" in request and line_ending not in ("LF", "CRLF"):
    raise ProjectError("行尾只能设置为 LF 或 CRLF。")
  if "encoding" in request and encoding not in ("utf8", "utf8bom"):
    raise ProjectError("编码只能设置为 UTF-8 或带 BOM 的 UTF-8。")
  original = original_format or {"encoding": "utf8", "lineEnding": "LF"}
  newline = _LINE_ENDINGS[line_ending or original["lineEnding"]]
  try:
    raw = (original_bytes.removeprefix(_UTF8_BOM)
           if original_bytes is not None and not line_ending
           else re.sub(r"\r\n|\r|\n", lambda _: newline, text).encode("utf-8"))
  except UnicodeError as error:
    raise ProjectError("文件内容必须是 UTF-8 文本。") from error
  return (_UTF8_BOM if (encoding or original["encoding"]) == "utf8bom" else b"") + raw


def project_file(root: Path, relative: str, build: str, *, previews: ProjectPreview | None = None, known="") -> dict:
  root = root.resolve()
  file = _file_path(root, relative, build)
  metadata = {}
  if file.is_relative_to((root / build).resolve()):
    metadata["generated"] = True
    previews = previews if previews is not None else ProjectPreview(root, build)
    declarations = previews.file_entries(relative)
    if declarations:
      metadata.update({key: value for key, value in declarations[0].items()
                       if key in {"artifact", "preview", "preview_error"}})
  info = file.stat()
  size = info.st_size
  path = file.relative_to(root).as_posix()
  stamp = _stat_version(info)
  tag = canonical_digest({"path": path, "file": stamp, "metadata": metadata})
  if known == tag:
    return {"status": "unchanged", "path": path, "poll_revision": tag}
  result = {"path": path, "size": size, "previewable": False, "version": stamp, "poll_revision": tag, **metadata}
  if size > MAX_PREVIEW_BYTES:
    return {**result, "reason": "文件超过 1 MiB，暂不提供文本编辑。"}
  with file.open("rb") as stream:
    content = stream.read(MAX_PREVIEW_BYTES + 1)
  if len(content) > MAX_PREVIEW_BYTES:
    return {**result, "reason": "文件超过 1 MiB，暂不提供文本编辑。"}
  result["version"] = version(content)
  if result.get("artifact") and result["version"] != result["artifact"]["digest"]:
    result.pop("artifact")
    result["preview_error"] = "文件在读取期间发生变化，无法关联构建预览；请刷新。"
  try:
    text = content.decode("utf-8-sig")
    if "\0" in text:
      raise UnicodeError("binary content")
  except UnicodeError:
    return {**result, "reason": "该文件不能以 UTF-8 文本编辑。"}
  return {**result, "previewable": True, "content": text.replace("\r\n", "\n").replace("\r", "\n"),
          "textFormat": _text_format(content)}


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
    raw = _encode_text(request.get("content"), request)
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
      raise Conflict("文件已被外部修改，未覆盖文件；编辑内容已保留。请另存为，或还原文件后重新编辑。")
    current_bytes = file.read_bytes()
    original_format = original["textFormat"]
    if original_format["lineEnding"] == "mixed":
      # Keep the existing save convention unless a specific line ending is chosen.
      original_format["lineEnding"] = "CRLF" if b"\r\n" in current_bytes else "CR"
    text = request.get("content")
    raw = _encode_text(text, request, original_format, current_bytes if text == original["content"] else None)
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
