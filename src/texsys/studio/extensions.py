"""Open VSX discovery and installation of declarative editor snippets."""

from functools import lru_cache
from hashlib import sha256
from http.client import HTTPException
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from zipfile import BadZipFile, ZipFile

from texsys.formal import _atomic_write
from texsys.project import ProjectError
from .files import file_operation, project_file, workspace_path
from .runtime import Conflict

REGISTRY = "https://open-vsx.org"
ID = re.compile(r"[a-zA-Z0-9_-]{1,100}\.[a-zA-Z0-9_-]{1,100}")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.+-]+)?")
MIB = 1024 * 1024
RECOMMENDATIONS = ".vscode/extensions.json"
DEFAULT_RECOMMENDATIONS = ("robole.markdown-snippets", "EricSia.pythonsnippets3")
JSONC_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\r\n]*|/\*[\s\S]*?\*/')
TRAILING_COMMA = re.compile(r'"(?:[^"\\]|\\.)*"|,(?=\s*[}\]])')


def _jsonc_source(text):
  # Keep offsets for edits to the recommendation array.
  text = JSONC_TOKENS.sub(lambda match: match[0] if match[0].startswith('"') else
                         re.sub(r"[^\r\n]", " ", match[0]), text)
  return TRAILING_COMMA.sub(lambda match: " " if match[0] == "," else match[0], text)


def _asset(url):
  if not isinstance(url, str):
    return None
  parsed = urlsplit(url)
  return url if parsed.scheme == "https" and parsed.netloc in {
    "open-vsx.org", "openvsx.eclipsecontent.org",
  } else None


class _RegistryRedirect(HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    if not _asset(newurl):
      raise ProjectError("Open VSX 返回了未知的下载地址。")
    return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url, maximum=MIB, *, optional=False):
  if not _asset(url):
    raise ProjectError("Open VSX 未提供可用的资源。")
  try:
    with build_opener(_RegistryRedirect()).open(url, timeout=10) as response:
      data = response.read(maximum + 1)
  except HTTPError as error:
    if optional and error.code == 404:
      return None
    raise ProjectError(f"Open VSX 请求失败（HTTP {error.code}）。") from error
  except (URLError, HTTPException, TimeoutError) as error:
    raise ProjectError("无法连接 Open VSX，请重试。") from error
  if len(data) > maximum:
    raise ProjectError("Open VSX 资源超过大小限制。")
  return data


def _get_json(url, *, optional=False):
  data = _get(url, optional=optional)
  if data is None:
    return None
  result = json.loads(data)
  if not isinstance(result, dict):
    raise ProjectError("Open VSX 返回了无效的元数据。")
  return result


def _entry(data):
  files = data.get("files", {})
  namespace, name = data["namespace"], data["name"]
  identity = f"{namespace}.{name}"
  if not ID.fullmatch(identity):
    raise ProjectError("Open VSX 返回了无效的扩展 ID。")
  return {
    "id": identity, "name": data.get("displayName") or name, "publisher": namespace,
    "version": data["version"], "description": data.get("description") or "",
    "downloads": data.get("downloadCount", 0), "rating": data.get("averageRating"),
    "icon": _asset(files.get("icon")), "vsix": _asset(files.get("download")),
    "verified": data.get("verified", False), "preRelease": data.get("preRelease", False),
  }


def _metadata(identity, version="latest"):
  if (not isinstance(identity, str) or not ID.fullmatch(identity)
      or not isinstance(version, str)
      or (version not in {"latest", "pre-release"} and not VERSION.fullmatch(version))):
    raise ProjectError("扩展 ID 或版本无效。")
  namespace, name = identity.split(".")
  data = _get_json(f"{REGISTRY}/api/{namespace}/{name}/{version}", optional=True)
  if not data:
    raise ProjectError("Open VSX 中没有此扩展版本。")
  entry = _entry(data)
  if (entry["id"].lower() != identity.lower()
      or (VERSION.fullmatch(version) and entry["version"] != version)):
    raise ProjectError("Open VSX 返回的扩展身份与请求不符。")
  return data, entry


def _compatibility(manifest):
  if manifest.get("main") or manifest.get("browser"):
    return "runtime"
  if manifest.get("extensionDependencies") or manifest.get("extensionPack"):
    return "dependencies"
  contributions = manifest.get("contributes", {})
  if set(contributions) != {"snippets"} or not contributions["snippets"]:
    return "contributions"
  return "snippets"


@lru_cache(maxsize=64)
def _version_manifest(url, version):
  # Versioned manifests are immutable on Open VSX. Search, version aliases and
  # package downloads remain live, and installs still validate the ZIP manifest.
  return _get_json(url, optional=True)


def _manifest(data):
  url = data.get("files", {}).get("manifest")
  return _version_manifest(url, data["version"]) if url else None


def search(query, offset=0, sort="relevance"):
  if len(query) > 250 or not 0 <= offset <= 5000 or sort not in {"relevance", "downloadCount", "averageRating", "timestamp"}:
    raise ProjectError("扩展搜索参数无效。")
  if query.lower().startswith("@id:"):
    try:
      _, entry = _metadata(query[4:].strip())
    except ProjectError as error:
      if str(error) != "Open VSX 中没有此扩展版本。":
        raise
      return {"items": [], "total": 0}
    return {"items": [entry], "total": 1}
  params = {"query": query, "offset": offset, "size": 24}
  if sort != "relevance":
    params.update(sortBy=sort, sortOrder="desc")
  data = _get_json(f"{REGISTRY}/api/-/search?{urlencode(params)}")
  return {"items": [_entry(item) for item in data["extensions"]], "total": data["totalSize"]}


def detail(identity, version):
  data, entry = _metadata(identity, version)
  versions = data.get("allVersions", {})
  manifest = _manifest(data) if entry["vsix"] else None
  return {
    **entry, "versions": [v for v in versions if VERSION.fullmatch(v)],
    "readme": _asset(data.get("files", {}).get("readme")),
    "hasPreRelease": "pre-release" in versions,
    "compatibility": (_compatibility(manifest) if manifest else "manifest" if entry["vsix"] else "unavailable"),
  }


def _read_member(archive, path):
  member = archive.getinfo(path)
  if member.file_size > MIB:
    raise ProjectError("VSIX 中的声明文件超过大小限制。")
  return archive.read(member).decode("utf-8-sig")


def _strings(value):
  if isinstance(value, str):
    return [value]
  if isinstance(value, list) and all(isinstance(item, str) for item in value):
    return value
  return []


def _snippets(archive, contributions):
  snippets = []
  total_size = 0
  if not isinstance(contributions, list) or len(contributions) > 64:
    raise ProjectError("VSIX 的代码片段清单无效。")
  for contribution in contributions:
    path = PurePosixPath(contribution["path"])
    if path.is_absolute() or ".." in path.parts or "\\" in str(path):
      raise ProjectError("VSIX 的代码片段路径无效。")
    language = contribution.get("language")
    member_path = f"extension/{path}"
    total_size += archive.getinfo(member_path).file_size
    if total_size > 8 * MIB:
      raise ProjectError("VSIX 的代码片段总大小超过限制。")
    definitions = json.loads(_jsonc_source(_read_member(archive, member_path)))
    for name, value in definitions.items():
      prefixes = _strings(value.get("prefix", name))
      body = _strings(value.get("body"))
      scopes = value.get("scope", "")
      description = _strings(value.get("description"))
      if prefixes and body:
        snippets.append({
          "name": name, "prefixes": prefixes, "body": "\n".join(body),
          "description": "\n".join(description),
          "languages": [language] if language else [scope.strip() for scope in scopes.split(",") if scope.strip()],
        })
    if len(snippets) > 5000:
      raise ProjectError("VSIX 包含过多代码片段。")
  if not snippets:
    raise ProjectError("VSIX 不含可用的代码片段。")
  return snippets


def _install(identity, version):
  data, entry = _metadata(identity, version)
  manifest = _manifest(data)
  if not manifest or _compatibility(manifest) != "snippets":
    raise ProjectError("此扩展需要 Tex Studio 尚未提供的扩展能力。")
  raw = _get(entry["vsix"], 32 * MIB)
  digest_url = data.get("files", {}).get("sha256")
  digest = _get(digest_url, 256, optional=True) if digest_url else None
  if digest and sha256(raw).hexdigest().encode() != digest.strip().lower():
    raise ProjectError("VSIX 的 SHA-256 与 Open VSX 公布的摘要不一致。")
  try:
    with ZipFile(BytesIO(raw)) as archive:
      manifest = json.loads(_read_member(archive, "extension/package.json"))
      if (f"{manifest['publisher']}.{manifest['name']}".lower() != entry["id"].lower()
          or manifest["version"] != entry["version"] or _compatibility(manifest) != "snippets"):
        raise ProjectError("VSIX 清单与请求的扩展身份、版本或能力不符。")
      return entry, _snippets(archive, manifest["contributes"]["snippets"])
  except (BadZipFile, AttributeError, KeyError, TypeError, ValueError, RuntimeError, NotImplementedError) as error:
    raise ProjectError("无法读取此 VSIX 的代码片段。") from error


def _recommendation_ids(config, name):
  items = config.get(name, [])
  if not isinstance(items, list) or any(not isinstance(item, str) or not ID.fullmatch(item) for item in items):
    raise ProjectError(f"{RECOMMENDATIONS} 的 {name} 必须是扩展 ID 列表。")
  return items


def _recommendation_file(root, build):
  path = workspace_path(root, RECOMMENDATIONS, build, missing=True)
  if not path.exists():
    return None, {}
  file = project_file(root, RECOMMENDATIONS, build)
  if not file["previewable"]:
    raise ProjectError(file["reason"])
  config = json.loads(_jsonc_source(file["content"]))
  if not isinstance(config, dict):
    raise ProjectError(f"{RECOMMENDATIONS} 必须包含一个 JSON 对象。")
  return file, config


def recommend(root, build, identity):
  if not isinstance(identity, str) or not ID.fullmatch(identity):
    raise ProjectError("扩展 ID 无效。")
  file, config = _recommendation_file(root, build)
  items = _recommendation_ids(config, "recommendations")
  if identity.lower() in {item.lower() for item in items}:
    return
  if file is None:
    workspace_path(root, RECOMMENDATIONS, build, missing=True).parent.mkdir(exist_ok=True)
    file_operation(root, build, {"action": "create_text", "path": RECOMMENDATIONS,
                                "content": json.dumps({"recommendations": [identity.lower()]}, indent=2) + "\n"})
    return
  text = file["content"]
  source = _jsonc_source(text)
  decoder = json.JSONDecoder()
  # Traverse top-level fields, retaining comments, indentation and unrelated settings.
  cursor = source.index("{") + 1
  entry = f"\n    {json.dumps(identity.lower())}"
  if "recommendations" in config:
    while True:
      start = re.search(r"\S", source[cursor:]).start() + cursor
      name, colon = decoder.raw_decode(source, start)
      value_start = source.index(":", colon) + 1
      start = re.search(r"\S", source[value_start:]).start() + value_start
      _, end = decoder.raw_decode(source, start)
      if name == "recommendations":
        insert_at = start + 1
        insertion = entry + ("," if items else "\n  ")
        break
      cursor = source.index(",", end) + 1
  else:
    insert_at = cursor
    insertion = '\n  "recommendations": [' + entry + "\n  ]" + ("," if config else "\n")
  file_operation(root, build, {"action": "save", "path": RECOMMENDATIONS, "version": file["version"],
                               "content": text[:insert_at] + insertion + text[insert_at:]})


class StudioExtensions:
  def __init__(self, path=None):
    root = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not root.is_absolute():
      root = Path.home() / ".local" / "share"
    self.path = path or root / "texsys" / "studio-extensions.json"
    self.lock = RLock()

  def _read(self):
    try:
      return json.loads(self.path.read_text(encoding="utf-8"))
    except FileNotFoundError:
      return {"installed": {}, "autoUpdate": False, "ignored": []}

  def _public(self, data, root):
    workspace = str(root)
    return {
      "autoUpdate": data["autoUpdate"], "ignored": data["ignored"],
      "installed": [
        {**item["entry"], "enabled": item["workspaces"].get(workspace, item["enabled"]),
         "globalEnabled": item["enabled"], "workspaceEnabled": item["workspaces"].get(workspace),
         "autoUpdate": item["autoUpdate"],
         "snippets": item["snippets"] if item["workspaces"].get(workspace, item["enabled"]) else []}
        for item in data["installed"].values()
      ],
    }

  def state(self, root):
    with self.lock:
      return self._public(self._read(), root)

  def recommendations(self, root, build):
    _, config = _recommendation_file(root, build)
    workspace = _recommendation_ids(config, "recommendations")
    unwanted = {item.lower() for item in _recommendation_ids(config, "unwantedRecommendations")}
    ids = {item.lower(): item for item in (*workspace, *DEFAULT_RECOMMENDATIONS) if item.lower() not in unwanted}
    return {"ids": list(ids.values())[:64], "workspace": workspace}

  def change(self, root, request):
    action, identity = request.get("action"), request.get("id")
    if action not in {"auto-update-all", "enable-all", "disable-all"} and (
        not isinstance(identity, str) or not ID.fullmatch(identity)):
      raise ProjectError("扩展 ID 无效。")
    key = identity.lower() if isinstance(identity, str) else None
    enabled = request.get("enabled")
    if action in {"enable", "auto-update", "auto-update-all", "ignore"} and type(enabled) is not bool:
      raise ProjectError("扩展开关值无效。")
    # Network and ZIP validation finish before the existing installation can change.
    if action == "install":
      entry, snippets = _install(identity, request.get("version", "latest"))
    with self.lock:
      data = self._read()
      installed = data["installed"]
      item = installed.get(key)
      if action == "install":
        expected = request.get("previousVersion")
        if expected != (item["entry"]["version"] if item else None):
          raise Conflict("扩展已发生变化，请刷新后重试。")
        installed[key] = {**(item or {"enabled": True, "autoUpdate": True, "workspaces": {}}),
                          "entry": entry, "snippets": snippets}
      elif action == "ignore":
        data["ignored"] = sorted((set(data["ignored"]) - {key}) | ({key} if enabled else set()))
      elif action == "auto-update-all":
        data["autoUpdate"] = enabled
      elif action in {"enable-all", "disable-all"}:
        for extension in installed.values():
          extension["enabled"] = action == "enable-all"
          extension["workspaces"].pop(str(root), None)
      elif item is None:
        raise ProjectError("扩展尚未安装。")
      elif action == "uninstall":
        del installed[key]
      elif action == "auto-update":
        item["autoUpdate"] = enabled
      elif action == "enable":
        if request.get("scope") == "workspace":
          item["workspaces"][str(root)] = enabled
        else:
          item["enabled"] = enabled
          item["workspaces"].pop(str(root), None)
      else:
        raise ProjectError("未知的扩展操作。")
      self.path.parent.mkdir(parents=True, exist_ok=True)
      _atomic_write(self.path, json.dumps(data, ensure_ascii=False, allow_nan=False))
      return self._public(data, root)
