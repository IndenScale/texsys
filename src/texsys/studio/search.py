"""Bounded project code search and version-checked replacement previews."""

import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading

from texsys.project import ProjectError
from . import files
from .runtime import Conflict

_REGEX_SLOTS = threading.BoundedSemaphore(2)
REGEX_TIMEOUT = 2


def path_matches(path: str, filters: str) -> bool:
  patterns = [item.strip() for item in filters.split(",") if item.strip()]
  return any(fnmatch.fnmatchcase(path.casefold(), pattern.casefold()) or
             (not any(char in pattern for char in "*?[") and pattern.casefold() in path.casefold())
             for pattern in patterns)


def _options(query, options):
  if not isinstance(query, str) or not query or len(query) > 500 or "\n" in query or "\r" in query:
    raise ProjectError("请输入最多 500 字符的单行搜索内容。")
  for key in ("include", "exclude"):
    value = options.get(key, "")
    if not isinstance(value, str) or len(value) > 500:
      raise ProjectError("路径筛选最多 500 字符。")
  for key in ("case_sensitive", "whole_word", "regex", "preserve_case"):
    if key in options and not isinstance(options[key], bool):
      raise ProjectError("搜索开关必须是布尔值。")


def compile_pattern(query, options):
  _options(query, options)
  expression = query if options.get("regex") else re.escape(query)
  if options.get("whole_word"):
    expression = r"(?<!\w)(?:" + expression + r")(?!\w)"
  try:
    return re.compile(expression, 0 if options.get("case_sensitive") else re.IGNORECASE)
  except re.error as error:
    raise ProjectError(f"正则表达式无效：{error}") from error


def _expand(match, replacement, regex, preserve_case):
  if regex:
    # Familiar editor substitutions: $1, ${name}, $$ and escaped newlines/tabs.
    replacement = replacement.replace("$$", "\x00")
    replacement = re.sub(r"\$(\d+)|\$\{([a-zA-Z_]\w*)\}",
                         lambda item: r"\g<" + (item[1] or item[2]) + ">", replacement)
    try:
      replacement = match.expand(replacement).replace("\x00", "$")
    except (re.error, IndexError) as error:
      raise ProjectError(f"替换表达式无效：{error}") from error
  if preserve_case:
    original = match.group()
    if original.isupper():
      replacement = replacement.upper()
    elif original.islower():
      replacement = replacement.lower()
    elif original.istitle():
      replacement = replacement.title()
  return replacement


def _scan(root, build, query, options, replacement=None):
  pattern = compile_pattern(query, options)
  listing = files.project_files(root, build)
  matches, lines, versions = [], [], {}
  scanned = skipped = used = 0
  truncated = listing["truncated"]
  paths = options.get("paths")
  if paths is not None:
    if not isinstance(paths, list) or len(paths) > files.MAX_FILES or any(not isinstance(path, str) for path in paths):
      raise ProjectError("替换范围必须是不超过 5000 项的文件路径列表。")
    paths = set(paths)
  for item in listing["files"]:
    path = item["path"]
    if paths is not None and path not in paths:
      continue
    if options.get("include") and not path_matches(path, options["include"]):
      continue
    if options.get("exclude") and path_matches(path, options["exclude"]):
      continue
    if item["size"] > files.MAX_PREVIEW_BYTES:
      skipped += 1
      continue
    used += item["size"]
    if used > files.MAX_SEARCH_BYTES:
      truncated = True
      break
    try:
      document = files.project_file(root, path, build)
    except (ProjectError, OSError):
      skipped += 1
      continue
    if not document["previewable"]:
      skipped += 1
      continue
    scanned += 1
    absolute_offset = 0
    for number, line in enumerate(document["content"].split("\n"), 1):
      line_matches = []
      for match in pattern.finditer(line):
        # Empty matches cannot be selected or meaningfully replaced in this UI.
        if match.start() == match.end():
          continue
        if len(matches) >= files.MAX_SEARCH_MATCHES:
          truncated = True
          break
        start = max(0, match.start() - 100)
        end = min(len(line), max(match.end() + 100, start + 240))
        record = {"path": path, "line": number, "column": match.start() + 1,
                  "end_column": match.end() + 1, "text": line[start:end],
                  "offset": match.start() - start, "length": match.end() - match.start()}
        if replacement is not None:
          record.update(absolute_offset=absolute_offset + match.start(),
                        replacement=_expand(match, replacement, options.get("regex"), options.get("preserve_case")))
        matches.append(record)
        line_matches.append(record)
      if line_matches:
        versions[path] = document["version"]
        start = max(0, line_matches[0]["column"] - 101)
        end = min(len(line), max(start + 480, line_matches[0]["end_column"] - 1))
        visible = [{"column": match["column"], "end_column": match["end_column"],
                    "offset": match["column"] - 1 - start, "length": match["length"]}
                   for match in line_matches if match["end_column"] - 1 <= end]
        lines.append({"path": path, "line": number, "text": line[start:end], "prefix": start > 0,
                      "suffix": end < len(line), "matches": visible, "count": len(line_matches)})
      absolute_offset += len(line) + 1
      if truncated and len(matches) >= files.MAX_SEARCH_MATCHES:
        break
    if truncated and len(matches) >= files.MAX_SEARCH_MATCHES:
      break
  return {"matches": matches, "lines": lines, "versions": versions, "scanned": scanned,
          "skipped": skipped, "truncated": truncated}


def _worker(action, root, build, query, options, replacement=None):
  _options(query, options)
  if not _REGEX_SLOTS.acquire(timeout=1):
    raise ProjectError("搜索正忙，请稍后重试。")
  try:
    try:
      result = subprocess.run([sys.executable, "-m", "texsys.studio.search_worker"],
                              input=json.dumps({"action": action, "root": str(root), "build": build,
                                                "query": query, "options": options, "replacement": replacement}),
                              capture_output=True, text=True, timeout=REGEX_TIMEOUT)
    except subprocess.TimeoutExpired as error:
      raise ProjectError("正则搜索超过 2 秒，请简化表达式或缩小文件范围。") from error
    output = json.loads(result.stdout)
    if result.returncode or "error" in output:
      raise ProjectError(output.get("error", "搜索执行失败。"))
    return output
  finally:
    _REGEX_SLOTS.release()


def search_project(root: Path, build: str, query: str, **options) -> dict:
  if options.get("regex"):
    return _worker("search", root, build, query, options)
  return _scan(root, build, query, options)


def _replace_plan(root, build, query, options, replacement):
  if not isinstance(replacement, str) or len(replacement) > 4000 or "\0" in replacement:
    raise ProjectError("替换内容必须是不超过 4000 字符的文本。")
  result = _scan(root, build, query, options, replacement)
  if result["truncated"]:
    raise ProjectError("搜索结果超过上限，请缩小范围后再替换。")
  changes = []
  for path in result["versions"]:
    document = files.project_file(root, path, build)
    if document["version"] != result["versions"][path]:
      raise Conflict("文件在准备替换预览时发生变化，请重新搜索。")
    content = document["content"]
    matches = [match for match in result["matches"] if match["path"] == path]
    for match in reversed(matches):
      start = match["absolute_offset"]
      content = content[:start] + match["replacement"] + content[start + match["length"]:]
    if content == document["content"]:
      continue
    if len(content.encode("utf-8")) > files.MAX_PREVIEW_BYTES:
      raise ProjectError(f"替换后文件超过 1 MiB：{path}")
    sample = next(line for line in result["lines"] if line["path"] == path)
    changes.append({"path": path, "version": document["version"], "content": content, "count": len(matches),
                    "before": sample["text"], "after": content.split("\n")[sample["line"] - 1][:480] if len(content.split("\n")) >= sample["line"] else "", "line": sample["line"]})
  versions = {change["path"]: change["version"] for change in changes}
  snapshot = hashlib.sha256(json.dumps([query, options, replacement, versions], sort_keys=True).encode()).hexdigest()
  return {"changes": changes, "versions": versions, "snapshot": snapshot,
          "count": sum(change["count"] for change in changes), "skipped": result["skipped"]}


def replacement(root, build, request):
  query = request.get("query")
  options = {key: request.get(key, default) for key, default in
             (("case_sensitive", False), ("whole_word", False), ("regex", False),
              ("include", ""), ("exclude", ""), ("preserve_case", False))}
  if request.get("paths") is not None:
    options["paths"] = request["paths"]
  plan = (_worker("replace", root, build, query, options, request.get("replacement")) if options["regex"]
          else _replace_plan(root, build, query, options, request.get("replacement")))
  if not request.get("apply"):
    return {**plan, "changes": [{key: value for key, value in change.items() if key != "content"}
                               for change in plan["changes"]]}
  if request.get("snapshot") != plan["snapshot"]:
    raise Conflict("替换预览已过期，文件或搜索条件发生变化；请重新预览。")
  # Validate the entire batch before the first write. Each file then uses atomic save.
  for change in plan["changes"]:
    if files.project_file(root, change["path"], build)["version"] != change["version"]:
      raise Conflict("文件已被外部修改，请重新预览替换。")
  applied = []
  for change in plan["changes"]:
    try:
      files.file_operation(root, build, {"action": "save", "path": change["path"],
                                       "version": change["version"], "content": change["content"]})
      applied.append(change["path"])
    except (ProjectError, OSError) as error:
      return {"complete": False, "applied": applied, "error": str(error)}
  return {"complete": True, "applied": applied, "count": plan["count"]}
