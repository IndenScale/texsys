"""Loopback-only Web host for plugin workbenches and the local task API."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, urlparse

from texsys.project import ProjectError
from .ai import AiSettings, draft_commit_message, is_ai_config_path, test_connection
from .runtime import Conflict, StudioRuntime
from .scene import ScenePreview
from .preview import ProjectPreview
from .files import file_operation, project_file, project_files, search_project
from .search import replacement
from .source_control import (repository, source_commit_diff, source_diff, source_history, source_operation,
                             source_status, staged_commit_diff, verify_source_snapshot)
from .terminal import TerminalService

ASSETS = Path(__file__).parents[1] / "assets" / "studio"
FRONTEND_ASSETS = {
  "/studio.js": "text/javascript",
  "/studio.css": "text/css",
  "/studio-editor-worker.js": "text/javascript",
  "/studio-json-worker.js": "text/javascript",
  "/studio-codicon.ttf": "font/ttf",
  "/favicon.svg": "image/svg+xml",
}


class StudioServer(ThreadingHTTPServer):
  def __init__(self, address, handler, terminals):
    self.terminals = terminals
    super().__init__(address, handler)

  def server_close(self):
    self.terminals.close()
    super().server_close()


def run_terminal_command(root: Path, request: dict) -> dict:
  root = root.resolve()
  boundary = (repository(root) if shutil.which("git") else None) or root
  command = request.get("command", "").strip()
  cwd = (root / request.get("cwd", "")).resolve()
  if not cwd.is_relative_to(boundary) or not cwd.is_dir():
    raise ProjectError("终端工作目录不可用，请新建终端。")
  result = {"stdout": "", "stderr": "", "code": 0, "cwd": str(cwd)}
  if not command:
    return result
  try:
    with tempfile.TemporaryDirectory(prefix="texsys-terminal-") as temporary:
      cwd_file = Path(temporary) / "cwd"
      env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "en_US.UTF-8",
             "TEXSYS_TERMINAL_CWD_FILE": str(cwd_file)}
      env.pop("TEXSYS_TERMINAL_PREVIOUS_CWD", None)
      if request.get("previousCwd"):
        previous = (root / request["previousCwd"]).resolve()
        if previous.is_relative_to(boundary) and previous.is_dir():
          env["TEXSYS_TERMINAL_PREVIOUS_CWD"] = str(previous)
      # A separate file keeps the final working directory out of terminal output.
      script = """if [ -n "$TEXSYS_TERMINAL_PREVIOUS_CWD" ]; then
  OLDPWD="$TEXSYS_TERMINAL_PREVIOUS_CWD"
else
  unset OLDPWD
fi
trap 'command pwd -P > "$TEXSYS_TERMINAL_CWD_FILE"' EXIT
""" + command
      completed = subprocess.run(script, shell=True, cwd=cwd, capture_output=True, text=True,
                                 errors="replace", timeout=30, env=env)
      result.update(stdout=completed.stdout[-16384:], stderr=completed.stderr[-16384:], code=completed.returncode)
      recorded_cwd = os.fsdecode(cwd_file.read_bytes().removesuffix(b"\n")) if cwd_file.is_file() else ""
      if recorded_cwd:
        destination = Path(recorded_cwd).resolve()
        if destination.is_relative_to(boundary) and destination.is_dir():
          result["cwd"] = str(destination)
        else:
          result.update(code=1, directoryBlocked=True)
  except subprocess.TimeoutExpired:
    result.update(stderr="Command timed out after 30 seconds.", code=124)
  except OSError as error:
    result.update(stderr=str(error), code=1)
  return result


def compile_frontend(runtime: StudioRuntime):
  node = shutil.which("node")
  if not node:
    raise ProjectError("Node.js is required to start Tex Studio.")
  roots = (*Path(__file__).resolve().parents, Path.cwd(), *Path.cwd().parents)
  root = next((p for p in roots if (p / "node_modules/esbuild-wasm/package.json").is_file()), None)
  if root is None:
    raise ProjectError("Studio frontend dependencies are missing; run uv run tools/sync_node_tools.py.")
  output = runtime.directory / "frontend"
  request = {"root": str(root), "host": str(ASSETS / "host.tsx"), "output": str(output),
             "plugins": [str(plugin.frontend) for plugin in runtime.plugins.values()]}
  completed = subprocess.run([node, str(ASSETS / "build.mjs")], input=json.dumps(request),
                             text=True, capture_output=True, timeout=90)
  if completed.returncode:
    raise ProjectError(f"Studio frontend build failed: {completed.stderr[-3000:]}")
  if (ASSETS / "favicon.svg").is_file():
    shutil.copyfile(ASSETS / "favicon.svg", output / "favicon.svg")
  return output


def create_server(runtime: StudioRuntime, port=8710, frontend=None, token=None, public_port=None):
  frontend = compile_frontend(runtime) if frontend is None else frontend
  token = token or secrets.token_urlsafe(32)
  ai_settings = AiSettings()
  terminals = TerminalService()
  scene = ScenePreview(runtime.root, runtime.project.build)
  previews = ProjectPreview(runtime.root, runtime.project.build)

  def scm_root(scope):
    if scope not in {"project", "repository"}:
      raise ProjectError("未知的源码管理范围。")
    return (repository(runtime.root) or runtime.root) if scope == "repository" else runtime.root

  class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
      pass

    def _send(self, status, content, content_type="application/json; charset=utf-8"):
      data = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False, allow_nan=False).encode()
      self.send_response(status)
      self.send_header("Content-Type", content_type)
      self.send_header("Content-Length", str(len(data)))
      self.send_header("Cache-Control", "no-store")
      self.send_header("X-Content-Type-Options", "nosniff")
      self.send_header("Referrer-Policy", "no-referrer")
      port = self.server.server_port
      self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                       f"img-src 'self' data:; connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; frame-ancestors 'none'")
      self.end_headers()
      self.wfile.write(data)

    def _host_allowed(self):
      return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def _authorize(self):
      if not self._host_allowed() or self.headers.get("X-Tex-Studio-Token") != token:
        self._send(403, {"error": "Studio session authorization required."})
        return False
      origin = self.headers.get("Origin")
      if origin and origin != "http://" + self.headers["Host"]:
        self._send(403, {"error": "Cross-origin project access is not permitted."})
        return False
      return True

    def do_GET(self):
      parsed = urlparse(self.path)
      if not self._host_allowed():
        self._send(403, {"error": "Invalid local host."})
        return
      if self.path == "/api/terminal/ws":
        origins = ({f"http://127.0.0.1:{public_port}", f"http://localhost:{public_port}"}
                   if public_port else {"http://" + self.headers["Host"]})
        terminals.upgrade(self, runtime.root, token, origins)
        return
      if parsed.path == "/":
        page = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<meta name="tex-studio-token" content="{token}">'
                "<title>Tex Studio</title>"
                '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
                '<link rel="stylesheet" href="/studio.css"></head><body><div id="root"></div>'
                '<script src="/studio.js" defer></script></body></html>')
        self._send(200, page.encode(), "text/html; charset=utf-8")
        return
      if parsed.path in {"/favicon.ico", "/favicon.svg"}:
        favicon = (frontend / "favicon.svg") if (frontend / "favicon.svg").is_file() else (ASSETS / "favicon.svg")
        if favicon.is_file():
          self._send(200, favicon.read_bytes(), "image/svg+xml")
        else:
          self._send(204, b"", "image/svg+xml")
        return
      if parsed.path in FRONTEND_ASSETS:
        file = frontend / parsed.path[1:]
        if file.is_file():
          self._send(200, file.read_bytes(), FRONTEND_ASSETS[parsed.path])
        else:
          self._send(404, {"error": "Frontend asset missing."})
        return
      if not self._authorize():
        return
      try:
        if parsed.path == "/api/state":
          self._send(200, runtime.state())
        elif parsed.path == "/api/design-index":
          from .library_sources import library_index

          self._send(200, library_index(runtime.root, runtime.project.source, []))
        elif parsed.path == "/api/preview":
          self._send(200, previews.read(parse_qs(parsed.query).get("root", [""])[0]))
        elif parsed.path == "/api/preview/artifact":
          query = parse_qs(parsed.query)
          self._send(200, previews.artifact(query.get("run", [""])[0], query.get("target", [""])[0], query.get("id", [""])[0]))
        elif parsed.path == "/api/scene":
          query = parse_qs(parsed.query)
          if "path" in query:
            self._send(200, scene.read_file(query["path"][0], query.get("revision", [""])[0], version=query.get("version", [""])[0]))
          else:
            self._send(200, scene.read(query.get("revision", [""])[0], run=query.get("run", [""])[0],
                                      target=query.get("target", [""])[0], artifact=query.get("artifact", [""])[0]))
        elif parsed.path == "/api/ai/settings":
          self._send(200, ai_settings.read().public())
        elif parsed.path == "/api/files":
          self._send(200, project_files(runtime.root, runtime.project.build))
        elif parsed.path == "/api/file":
          relative = parse_qs(parsed.query).get("path", [""])[0]
          self._send(200, project_file(runtime.root, relative, runtime.project.build))
        elif parsed.path == "/api/search":
          query = parse_qs(parsed.query)
          self._send(200, search_project(runtime.root, runtime.project.build, query.get("q", [""])[0],
                     case_sensitive=query.get("case", [""])[0] == "true", include=query.get("include", [""])[0],
                     exclude=query.get("exclude", [""])[0], whole_word=query.get("word", [""])[0] == "true",
                     regex=query.get("regex", [""])[0] == "true"))
        elif parsed.path == "/api/scm":
          with runtime.lock:
            scope = parse_qs(parsed.query).get("scope", ["project"])[0]
            root = scm_root(scope)
            result = source_status(root, runtime.project.build if root == runtime.root else "build")
            result["project_prefix"] = runtime.root.relative_to(root).as_posix() if root != runtime.root else ""
            self._send(200, result)
        elif parsed.path == "/api/scm/history":
          query = parse_qs(parsed.query)
          try:
            limit = int(query.get("limit", ["50"])[0])
          except ValueError:
            raise ProjectError("历史条数必须是整数。")
          with runtime.lock:
            self._send(200, source_history(runtime.root, limit))
        elif parsed.path == "/api/scm/commit":
          with runtime.lock:
            self._send(200, source_commit_diff(scm_root(parse_qs(parsed.query).get("scope", ["project"])[0]), parse_qs(parsed.query).get("id", [""])[0]))
        elif parsed.path == "/api/scm/diff":
          query = parse_qs(parsed.query)
          with runtime.lock:
            root = scm_root(query.get("scope", ["project"])[0])
            self._send(200, source_diff(root, runtime.project.build if root == runtime.root else "build", query.get("path", [""])[0],
                       staged=query.get("staged", [""])[0] == "true"))
        elif parsed.path == "/api/artifact":
          relative = parse_qs(parsed.query).get("path", [""])[0]
          file = (runtime.root / relative).resolve()
          build = (runtime.root / runtime.project.build).resolve()
          if not file.is_relative_to(build) or not file.is_file() or is_ai_config_path(file):
            self._send(404, {"error": "Artifact not found."})
          elif file.suffix not in {".json", ".svg", ".drawio", ".log", ".csv", ".mo", ".txt"}:
            self._send(403, {"error": "Unsupported artifact type."})
          else:
            kind = mimetypes.guess_type(str(file))[0] or "text/plain"
            self._send(200, file.read_bytes(), kind + "; charset=utf-8")
        else:
          self._send(404, {"error": "Unknown API route."})
      except Conflict as error:
        self._send(409, {"error": str(error)})
      except (ProjectError, OSError, ValueError) as error:
        self._send(400, {"error": str(error)})

    def do_POST(self):
      if not self._authorize():
        return
      try:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 8 * 1024 * 1024:
          raise ProjectError("Request body must contain at most 8 MiB of JSON.")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
          raise ProjectError("Request must be an object.")
        if self.path == "/api/library":
          from .library_sources import library_index, reference_library_entry

          if body.get("action") == "reference":
            with runtime.lock:
              result = reference_library_entry(runtime.root, body)
          elif body.get("action") == "index":
            result = library_index(runtime.root, runtime.project.source, body.get("folders"))
          else:
            raise ProjectError("Unknown library operation.")
        elif self.path == "/api/search/replace":
          with runtime.lock:
            result = replacement(runtime.root, runtime.project.build, body)
        elif self.path == "/api/scene":
          if not isinstance(body.get("content"), str):
            raise ProjectError("Scene preview requires the current document text.")
          result = scene.read_file(body.get("path"), version=body.get("version", ""), content=body["content"])
        elif self.path == "/api/file":
          with runtime.lock:
            result = file_operation(runtime.root, runtime.project.build, body)
        elif self.path == "/api/scm":
          with runtime.lock:
            root = scm_root(body.get("scope", "project"))
            result = source_operation(root, runtime.project.build if root == runtime.root else "build", body)
            result["project_prefix"] = runtime.root.relative_to(root).as_posix() if root != runtime.root else ""
        elif self.path == "/api/ai/settings":
          workspace = (repository(runtime.root) if shutil.which("git") else None) or runtime.root
          config = ai_settings.path.resolve()
          if config.is_relative_to(workspace) or any(parent.is_dir() and parent.samefile(workspace) for parent in config.parents):
            raise ProjectError("AI 设置必须保存在当前项目及其仓库之外。")
          result = ai_settings.save(body).public()
        elif self.path == "/api/ai/test":
          connection = ai_settings.read()
          test_connection(connection)
          ai_settings.verify(connection)
          result = {"ok": True}
        elif self.path == "/api/ai/commit-message":
          locale = body.get("locale")
          if locale not in ("zh-CN", "en"):
            raise ProjectError("未知的提交说明语言。")
          connection = ai_settings.read()
          if body.get("destination") != connection.destination():
            raise Conflict("AI 连接设置已更新，请重新确认后再试。")
          snapshot = body.get("snapshot")
          with runtime.lock:
            root = scm_root(body.get("scope", "project"))
            build = runtime.project.build if root == runtime.root else "build"
            diff = staged_commit_diff(root, build, snapshot)
          ai_settings.verify(connection)
          message = draft_commit_message(connection, diff, locale)
          with runtime.lock:
            verify_source_snapshot(root, build, snapshot)
          ai_settings.verify(connection)
          result = {"message": message}
        elif self.path == "/api/edit":
          result = runtime.edit(body)
        elif self.path == "/api/jobs":
          result = runtime.submit(body)
        elif self.path.startswith("/api/jobs/") and self.path.endswith("/cancel"):
          result = runtime.cancel(self.path.split("/")[3])
        elif self.path == "/api/terminal":
          with runtime.lock:
            result = run_terminal_command(runtime.root, body)
        else:
          self._send(404, {"error": "Unknown API route."})
          return
        self._send(200, result)
      except Conflict as error:
        self._send(409, {"error": str(error)})
      except (ProjectError, OSError, ValueError, TypeError, KeyError) as error:
        self._send(400, {"error": str(error)})

  return StudioServer(("127.0.0.1", port), Handler, terminals)


def serve(root: Path, port=8710, open_browser=False):
  runtime = StudioRuntime(root)
  server = create_server(runtime, port)
  url = f"http://127.0.0.1:{server.server_port}"
  print(f"Tex Studio: {url}\nProject: {runtime.project.name}\nPress Ctrl+C to stop.", flush=True)
  if open_browser:
    import webbrowser
    webbrowser.open(url)
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    pass
  finally:
    server.server_close()
    runtime.close()


def serve_hmr(root: Path, port=8710, open_browser=False):
  """Run the Studio API and a Vite React development server with HMR."""
  node = shutil.which("node")
  if not node:
    raise ProjectError("Node.js is required to start Tex Studio.")
  roots = (*Path(__file__).resolve().parents, Path.cwd(), *Path.cwd().parents)
  root_dir = next((p for p in roots if (p / "node_modules/vite/package.json").is_file()), None)
  if root_dir is None:
    raise ProjectError("Vite is missing; run uv run tools/sync_node_tools.py.")
  runtime = StudioRuntime(root)
  token = secrets.token_urlsafe(32)
  api = create_server(runtime, 0, frontend=runtime.directory / "frontend", token=token, public_port=port)
  api_thread = threading.Thread(target=api.serve_forever, daemon=True)
  api_thread.start()
  frontend = runtime.directory / "hmr"
  frontend.mkdir(parents=True, exist_ok=True)
  if (ASSETS / "favicon.svg").is_file():
    shutil.copyfile(ASSETS / "favicon.svg", frontend / "favicon.svg")
  entry = frontend / "main.tsx"
  imports = [f'import {{ startStudio }} from {json.dumps(str(ASSETS / "host.tsx"))};']
  imports.extend(f'import plugin{index} from {json.dumps(str(plugin.frontend))};'
                 for index, plugin in enumerate(runtime.plugins.values()))
  imports.append(f'startStudio([{", ".join(f"plugin{i}" for i in range(len(runtime.plugins)))}]);')
  entry.write_text("\n".join(imports) + "\n")
  (frontend / "index.html").write_text(
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    f'<meta name="tex-studio-token" content="{token}">'
    '<title>Tex Studio</title>'
    '<link rel="icon" type="image/svg+xml" href="/favicon.svg"></head>'
    '<body><div id="root"></div><script type="module" src="/main.tsx"></script></body></html>')
  config = frontend / "vite.config.mjs"
  allowed = sorted({str(root_dir), *(str(plugin.frontend.parent) for plugin in runtime.plugins.values())})
  config.write_text(f'''import {{ defineConfig }} from {json.dumps(str(root_dir / "node_modules/vite/dist/node/index.js"))};
import react from {json.dumps(str(root_dir / "node_modules/@vitejs/plugin-react/dist/index.js"))};
export default defineConfig({{
  plugins: [react(), {{ name: "tex-studio-token", transformIndexHtml(html) {{ return html; }} }}],
  server: {{ host: "127.0.0.1", port: {port}, strictPort: true, fs: {{ allow: {json.dumps(allowed)} }},
    proxy: {{ "^/api/terminal/ws$": {{ target: "ws://127.0.0.1:{api.server_port}", changeOrigin: true, ws: true }},
      "/api": {{ target: "http://127.0.0.1:{api.server_port}", changeOrigin: true,
      headers: {{ Origin: "http://127.0.0.1:{api.server_port}" }} }} }} }}
}});
''')
  # Vite serves the entry directory and resolves its absolute host/plugin imports.
  command = [node, str(root_dir / "node_modules/vite/bin/vite.js"), str(frontend),
             "--config", str(config), "--host", "127.0.0.1"]
  process = subprocess.Popen(command, cwd=root_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env={**__import__("os").environ, "TEX_STUDIO_TOKEN": token})
  try:
    line = process.stdout.readline()
    if process.poll() is not None and not line:
      raise ProjectError("Vite failed to start; install frontend dependencies and retry.")
    while line and "http://" not in line:
      line = process.stdout.readline()
    url = line.strip().split()[-1] if line else f"http://127.0.0.1:{port}"
    print(f"Tex Studio HMR: {url}\nProject: {runtime.project.name}\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
      import webbrowser
      webbrowser.open(url)
    def forward_output():
      for output in process.stdout:
        print(output, end="", flush=True)
    threading.Thread(target=forward_output, daemon=True).start()
    process.wait()
  except KeyboardInterrupt:
    pass
  finally:
    process.terminate()
    process.wait(timeout=10)
    api.shutdown()
    api.server_close()
    api_thread.join(timeout=2)
    runtime.close()
