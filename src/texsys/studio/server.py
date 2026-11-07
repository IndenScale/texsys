"""Loopback-only Web host for plugin workbenches and the local task API."""

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
from urllib.parse import parse_qs, urlparse

from texsys.project import ProjectError
from .runtime import Conflict, StudioRuntime
from .files import project_file, project_files

ASSETS = Path(__file__).parents[1] / "assets" / "studio"


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
  return output


def create_server(runtime: StudioRuntime, port=8710, frontend=None, token=None):
  frontend = compile_frontend(runtime) if frontend is None else frontend
  token = token or secrets.token_urlsafe(32)

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
      self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
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
      if parsed.path == "/":
        page = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<meta name="tex-studio-token" content="{token}">'
                f"<title>Tex Studio — {html.escape(runtime.project.name)}</title>"
                '<link rel="stylesheet" href="/studio.css"></head><body><div id="root"></div>'
                '<script src="/studio.js" defer></script></body></html>')
        self._send(200, page.encode(), "text/html; charset=utf-8")
        return
      if parsed.path in {"/studio.js", "/studio.css"}:
        file = frontend / parsed.path[1:]
        if file.is_file():
          self._send(200, file.read_bytes(), "text/javascript" if file.suffix == ".js" else "text/css")
        else:
          self._send(404, {"error": "Frontend asset missing."})
        return
      if not self._authorize():
        return
      try:
        if parsed.path == "/api/state":
          self._send(200, runtime.state())
        elif parsed.path == "/api/files":
          self._send(200, project_files(runtime.root, runtime.project.build))
        elif parsed.path == "/api/file":
          relative = parse_qs(parsed.query).get("path", [""])[0]
          self._send(200, project_file(runtime.root, relative, runtime.project.build))
        elif parsed.path == "/api/artifact":
          relative = parse_qs(parsed.query).get("path", [""])[0]
          file = (runtime.root / relative).resolve()
          build = (runtime.root / runtime.project.build).resolve()
          if not file.is_relative_to(build) or not file.is_file():
            self._send(404, {"error": "Artifact not found."})
          elif file.suffix not in {".json", ".svg", ".drawio", ".log", ".csv", ".mo", ".txt"}:
            self._send(403, {"error": "Unsupported artifact type."})
          else:
            kind = mimetypes.guess_type(str(file))[0] or "text/plain"
            self._send(200, file.read_bytes(), kind + "; charset=utf-8")
        else:
          self._send(404, {"error": "Unknown API route."})
      except (ProjectError, OSError, ValueError) as error:
        self._send(400, {"error": str(error)})

    def do_POST(self):
      if not self._authorize():
        return
      try:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 1_000_000:
          raise ProjectError("Request body must contain at most 1 MB of JSON.")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
          raise ProjectError("Request must be an object.")
        if self.path == "/api/edit":
          result = runtime.edit(body)
        elif self.path == "/api/jobs":
          result = runtime.submit(body)
        elif self.path.startswith("/api/jobs/") and self.path.endswith("/cancel"):
          result = runtime.cancel(self.path.split("/")[3])
        else:
          self._send(404, {"error": "Unknown API route."})
          return
        self._send(200, result)
      except Conflict as error:
        self._send(409, {"error": str(error)})
      except (ProjectError, OSError, ValueError, TypeError, KeyError) as error:
        self._send(400, {"error": str(error)})

  return ThreadingHTTPServer(("127.0.0.1", port), Handler)


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
  api = create_server(runtime, 0, frontend=runtime.directory / "frontend", token=token)
  api_thread = threading.Thread(target=api.serve_forever, daemon=True)
  api_thread.start()
  frontend = runtime.directory / "hmr"
  frontend.mkdir(parents=True, exist_ok=True)
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
    f'<title>Tex Studio — {html.escape(runtime.project.name)}</title></head>'
    '<body><div id="root"></div><script type="module" src="/main.tsx"></script></body></html>')
  config = frontend / "vite.config.mjs"
  allowed = sorted({str(root_dir), *(str(plugin.frontend.parent) for plugin in runtime.plugins.values())})
  config.write_text(f'''import {{ defineConfig }} from {json.dumps(str(root_dir / "node_modules/vite/dist/node/index.js"))};
import react from {json.dumps(str(root_dir / "node_modules/@vitejs/plugin-react/dist/index.js"))};
export default defineConfig({{
  plugins: [react(), {{ name: "tex-studio-token", transformIndexHtml(html) {{ return html; }} }}],
  server: {{ host: "127.0.0.1", port: {port}, strictPort: false, fs: {{ allow: {json.dumps(allowed)} }},
    proxy: {{ "/api": {{ target: "http://127.0.0.1:{api.server_port}", changeOrigin: true,
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
