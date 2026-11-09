"""Git operations confined to the opened project, including projects in a parent repo."""

import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile

from texsys.project import ProjectError
from .files import MAX_PREVIEW_BYTES, workspace_path
from .runtime import Conflict

MAX_CHANGES = 2000
MAX_COMMIT_DRAFT_DIFF_BYTES = 48 * 1024


def git(root: Path, *arguments: str, accepted=(0,), output_limit=8 * 1024 * 1024, truncate=False,
        index_file: Path | None = None) -> bytes:
  # Spool stdout to disk so a large binary/text diff cannot exhaust the API process.
  env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
  if index_file is not None:
    env["GIT_INDEX_FILE"] = str(index_file)
  try:
    with tempfile.TemporaryFile() as output:
      result = subprocess.run(["git", "--literal-pathspecs", "-c", "diff.relative=false", "-C", str(root), *arguments],
                              stdout=output, stderr=subprocess.PIPE, timeout=20, env=env)
      output.seek(0)
      content = output.read(output_limit + 1)
  except (OSError, subprocess.TimeoutExpired) as error:
    raise ProjectError(f"无法执行 Git：{error}") from error
  if result.returncode not in accepted:
    raise ProjectError(result.stderr.decode("utf-8", "replace").strip()[-2000:] or "Git 操作失败。")
  if len(content) > output_limit and not truncate:
    raise ProjectError("Git 输出超过上限，请缩小项目范围或在终端处理。")
  return content


def repository(root: Path) -> Path | None:
  try:
    result = git(root, "rev-parse", "--show-toplevel")
  except ProjectError as error:
    if "not a git repository" in str(error):
      return None
    raise
  return Path(os.fsdecode(result).strip()).resolve()


def _records(root: Path) -> list[dict]:
  data = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
  parts = data.split(b"\0")
  entries, index = [], 0
  while index < len(parts) and parts[index]:
    entry = parts[index]
    index += 1
    code, name = entry[:2].decode("ascii"), os.fsdecode(entry[3:])
    original = None
    if "R" in code or "C" in code:
      original = os.fsdecode(parts[index])
      index += 1
    entries.append({"path": name, "index": code[0], "worktree": code[1], "original": original})
  return entries


def _read_index(root: Path) -> bytes | None:
  index_path = Path(os.fsdecode(git(root, "rev-parse", "--git-path", "index")).rstrip("\n"))
  if not index_path.is_absolute():
    index_path = root / index_path
  try:
    return index_path.read_bytes()
  except FileNotFoundError:
    return None


def _source_status(root: Path, build: str) -> tuple[dict, str, str]:
  repo = repository(root)
  if repo is None:
    return {"available": False, "reason": "当前项目尚未使用 Git。", "entries": [], "snapshot": ""}, "", ""
  root = root.resolve()
  records = _records(root)
  entries, signature = [], []
  for record in records:
    # Porcelain v1 always emits repository-root-relative paths, regardless of cwd.
    absolute = repo / record["path"]
    if not absolute.is_relative_to(root):
      continue
    relative = absolute.relative_to(root).as_posix()
    try:
      file = workspace_path(root, relative, build, missing=True)
      if file.is_dir():
        continue
      original = record["original"]
      if original:
        old = repo / original
        if not old.is_relative_to(root):
          # A cross-project rename must not mutate the source outside the project.
          continue
        original = old.relative_to(root).as_posix()
        workspace_path(root, original, build, missing=True)
      info = file.stat() if file.exists() else None
      digest = hashlib.sha256(file.read_bytes()).hexdigest() if info and info.st_size <= MAX_PREVIEW_BYTES else None
      entries.append({**record, "path": relative, "original": original})
      signature.append((relative, record["index"], record["worktree"], original,
                        info.st_mtime_ns if info else None, info.st_size if info else None, digest))
    except (ProjectError, OSError):
      continue
  head = git(root, "rev-parse", "--verify", "HEAD", accepted=(0, 128)).decode().strip()
  # Index content belongs to the snapshot even if a file's two status letters stay the same.
  index = _read_index(root)
  index_digest = hashlib.sha256(index).hexdigest() if index is not None else ""
  snapshot = hashlib.sha256(json.dumps([head, index_digest, signature], ensure_ascii=True).encode()).hexdigest()
  branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", accepted=(0, 1)).decode().strip()
  return {"available": True, "repository": str(repo), "scope": root.relative_to(repo).as_posix(),
          "branch": branch or head[:8], "detached": not bool(branch),
          "entries": entries[:MAX_CHANGES], "truncated": len(entries) > MAX_CHANGES, "snapshot": snapshot}, head, index_digest


def source_status(root: Path, build: str) -> dict:
  return _source_status(root, build)[0]


def _validate_snapshot(status: dict, snapshot):
  if not status["available"]:
    raise ProjectError("当前项目没有 Git 仓库。")
  if snapshot != status["snapshot"]:
    raise Conflict("Git 变更已更新，请刷新后再操作。")
  if status.get("truncated"):
    raise ProjectError("变更数量超过显示上限，请先在终端处理部分变更。")


def verify_source_snapshot(root: Path, build: str, snapshot):
  _validate_snapshot(source_status(root, build), snapshot)


def _validate_staged_paths(root: Path, build: str, status: dict, *, base: str | None = None,
                           index_file: Path | None = None):
  staged = git(root, "diff", "--cached", "--no-renames", "--name-only", "-z",
               *(["--ignore-submodules=dirty", base] if base else []), index_file=index_file).split(b"\0")
  staged = [os.fsdecode(name) for name in staged if name]
  if not staged:
    raise ProjectError("请先暂存需要提交的文件。")
  root = root.resolve()
  repo = Path(status["repository"])
  visible_staged = {path for entry in status["entries"] if entry["index"] not in {" ", "?"}
                    for path in (entry["path"], entry["original"]) if path}
  for name in staged:
    absolute = repo / name
    if not absolute.is_relative_to(root):
      raise ProjectError("仓库中还有当前项目以外的暂存变更；请先处理这些变更，再提交当前项目。")
    relative = absolute.relative_to(root).as_posix()
    workspace_path(root, relative, build, missing=True)
    if relative not in visible_staged:
      raise ProjectError("暂存区包含当前列表未显示的变更，请先在终端处理。")
  if any(entry["index"] == "U" or entry["worktree"] == "U" or
         entry["index"] + entry["worktree"] in {"AA", "DD"} for entry in status["entries"]):
    raise ProjectError("请先解决合并冲突。")


def staged_commit_diff(root: Path, build: str, snapshot) -> str:
  """Read only the staged patch for the exact reviewed Git snapshot."""
  status, head, index_digest = _source_status(root, build)
  _validate_snapshot(status, snapshot)
  if not index_digest:
    raise ProjectError("请先暂存需要提交的文件。")
  index = _read_index(root)
  if index is None or hashlib.sha256(index).hexdigest() != index_digest:
    raise Conflict("Git 变更已更新，请刷新后再操作。")
  # Pin both diff inputs so a terminal Git operation cannot substitute a different patch.
  base = head or git(root, "hash-object", "-t", "tree", os.devnull).decode().strip()
  with tempfile.TemporaryDirectory(prefix="texsys-git-") as directory:
    frozen_index = Path(directory) / "index"
    frozen_index.write_bytes(index)
    _validate_staged_paths(root, build, status, base=base, index_file=frozen_index)
    raw = git(root, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames",
              "--submodule=short", "--ignore-submodules=dirty", "--unified=0", "--inter-hunk-context=0",
              base, "--", ".", index_file=frozen_index,
              output_limit=MAX_COMMIT_DRAFT_DIFF_BYTES, truncate=True)
  verify_source_snapshot(root, build, snapshot)
  if len(raw) > MAX_COMMIT_DRAFT_DIFF_BYTES:
    raise ProjectError("暂存差异超过 48 KiB，请缩小暂存范围后重试。")
  if b"\0" in raw or not raw:
    raise ProjectError("暂存区没有可用的文本差异。")
  try:
    return raw.decode("utf-8")
  except UnicodeDecodeError:
    raise ProjectError("暂存差异不是 UTF-8 文本。") from None


def source_diff(root: Path, build: str, relative: str, staged=False) -> dict:
  status = source_status(root, build)
  record = next((entry for entry in status["entries"] if entry["path"] == relative), None)
  if record is None:
    raise ProjectError("该文件已不在当前项目变更中，请刷新。")
  if staged and record["index"] in {" ", "?"}:
    raise ProjectError("该文件没有已暂存的变更。")
  if record["index"] == "?":
    from .files import project_file
    file = project_file(root, relative, build)
    if not file["previewable"]:
      return {"path": relative, "staged": False, "content": file["reason"], "truncated": False}
    text = file["content"]
    content = f"--- /dev/null\n+++ {relative}\n@@ -0,0 +1,{len(text.splitlines())} @@\n"
    content += "\n".join("+" + line for line in text.splitlines())
    if text and not text.endswith("\n"):
      content += "\n\\ No newline at end of file"
    raw = content.encode()
  else:
    paths = [relative, *([record["original"]] if record["original"] else [])]
    raw = git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames",
              *(["--cached"] if staged else []), "--", *paths, output_limit=MAX_PREVIEW_BYTES, truncate=True)
  return {"path": relative, "staged": staged, "content": raw[:MAX_PREVIEW_BYTES].decode("utf-8", "replace"),
          "truncated": len(raw) > MAX_PREVIEW_BYTES}


def source_history(root: Path, limit=50) -> dict:
  """Repository-wide topology; no working tree or index mutations."""
  if not isinstance(limit, int) or not 1 <= limit <= 500:
    raise ProjectError("历史条数必须在 1 到 500 之间。")
  if repository(root) is None:
    return {"commits": [], "truncated": False}
  if not git(root, "rev-parse", "--verify", "HEAD", accepted=(0, 128)):
    return {"commits": [], "truncated": False}
  raw = git(root, "log", "--all", "HEAD", "--topo-order", f"--max-count={limit + 1}",
            "--format=%H%x00%P%x00%s%x00%an%x00%aI%x00%D%x00").decode("utf-8", "replace")
  fields = raw.split("\0")
  commits = []
  for index in range(0, len(fields) - 6, 6):
    oid, parents, subject, author, date, refs = fields[index:index + 6]
    commits.append({"id": oid.strip(), "parents": parents.split(), "subject": subject,
                    "author": author, "date": date, "refs": refs.split(", ") if refs else []})
  return {"commits": commits[:limit], "truncated": len(commits) > limit}


def source_commit_diff(root: Path, commit: str) -> dict:
  if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
    raise ProjectError("请选择有效的提交。")
  # Historical patches follow the same project scope as working changes.
  raw = git(root, "show", "--format=fuller", "--no-ext-diff", "--no-textconv", "--no-color",
            "--root", "--first-parent", commit, "--", ".", output_limit=MAX_PREVIEW_BYTES, truncate=True)
  return {"path": commit[:8], "title": "提交 " + commit[:8] + " · 所选范围差异",
          "staged": False, "historical": True,
          "content": raw[:MAX_PREVIEW_BYTES].decode("utf-8", "replace") or "此提交未更改当前管理范围内的文件。", "truncated": len(raw) > MAX_PREVIEW_BYTES}


def source_operation(root: Path, build: str, request: dict) -> dict:
  action = request.get("action")
  status = source_status(root, build)
  if action == "init":
    if status["available"]:
      raise Conflict("项目已经位于 Git 仓库中。")
    git(root, "init")
    return source_status(root, build)
  _validate_snapshot(status, request.get("snapshot"))
  if action in {"stage_all", "unstage_all"}:
    records = [entry for entry in status["entries"] if
               (entry["worktree"] != " " if action == "stage_all" else entry["index"] not in {" ", "?"})]
    paths = sorted({path for entry in records for path in (entry["path"], entry["original"]) if path})
    if not paths:
      raise ProjectError("没有可操作的变更。")
    for path in paths:
      workspace_path(root, path, build, missing=True)
    if action == "stage_all":
      git(root, "add", "-A", "--", *paths)
    elif git(root, "rev-parse", "--verify", "HEAD", accepted=(0, 128)):
      git(root, "reset", "-q", "HEAD", "--", *paths)
    else:
      git(root, "rm", "--cached", "--force", "--quiet", "--ignore-unmatch", "--", *paths)
  elif action == "discard":
    relative = request.get("path")
    record = next((entry for entry in status["entries"] if entry["path"] == relative), None)
    if record is None or record["worktree"] == " ":
      raise ProjectError("请选择未暂存的变更。")
    if record["index"] == "U" or record["worktree"] == "U" or record["index"] + record["worktree"] in {"AA", "DD"}:
      raise ProjectError("请先在编辑器或终端解决合并冲突。")
    file = workspace_path(root, relative, build, missing=True)
    if record["index"] == "?":
      file.unlink()
    else:
      git(root, "restore", "--worktree", "--", relative)
  elif action in {"stage", "unstage"}:
    relative = request.get("path")
    record = next((entry for entry in status["entries"] if entry["path"] == relative), None)
    if record is None:
      raise ProjectError("请选择当前项目中的变更文件。")
    paths = [relative, *([record["original"]] if record["original"] else [])]
    for path in paths:
      workspace_path(root, path, build, missing=True)
    if action == "stage":
      if record["worktree"] == " ":
        raise ProjectError("该文件没有未暂存的变更。")
      git(root, "add", "-A", "--", *paths)
    elif record["index"] in {" ", "?"}:
      raise ProjectError("该文件没有已暂存的变更。")
    elif git(root, "rev-parse", "--verify", "HEAD", accepted=(0, 128)):
      git(root, "reset", "-q", "HEAD", "--", *paths)
    else:
      git(root, "rm", "--cached", "--force", "--quiet", "--ignore-unmatch", "--", *paths)
  elif action == "commit":
    message = request.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 4000 or "\0" in message:
      raise ProjectError("请输入最多 4000 字符的提交说明。")
    _validate_staged_paths(root, build, status)
    git(root, "commit", "-m", message.strip())
  else:
    raise ProjectError("未知 Git 操作。")
  return source_status(root, build)
