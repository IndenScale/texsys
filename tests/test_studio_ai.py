"""Exercise AI routes, a loopback-compatible endpoint and real staged Git inputs."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
import stat
import threading
from types import SimpleNamespace

import pytest

from texsys.project import ProjectError, initialize_project
from texsys.studio import server
from texsys.studio.runtime import StudioRuntime
from texsys.studio.source_control import git, source_status, staged_commit_diff


@pytest.fixture
def studio_api(tmp_path, monkeypatch, request):
  project = tmp_path / "project"
  initialize_project(project)
  git(project, "init", "-b", "main")
  git(project, "config", "user.name", "Studio AI Test")
  git(project, "config", "user.email", "studio-ai@example.invalid")
  model = project / "src" / "note.txt"
  model.write_text("original\nunchanged context\n", encoding="utf-8")
  git(project, "add", "src/note.txt")
  git(project, "commit", "-m", "baseline")
  settings_root = project if getattr(request, "param", False) else tmp_path
  settings = settings_root / "config" / "texsys" / "studio-ai.json"
  monkeypatch.setenv("XDG_CONFIG_HOME", str(settings.parent.parent))
  runtime = StudioRuntime(project, plugins={})
  # Run the real request handler without opening a second Studio listener.
  monkeypatch.setattr(server, "StudioServer", lambda address, handler, terminals: handler)
  handler_class = server.create_server(runtime, port=8710, frontend=tmp_path, token="studio-test-token")

  def request(path, body=None, *, authorized=True):
    handler = object.__new__(handler_class)
    handler.server = SimpleNamespace(server_port=8710)
    handler.path = path
    handler.headers = {"Host": "127.0.0.1:8710"}
    if authorized:
      handler.headers["X-Tex-Studio-Token"] = "studio-test-token"
    result = []
    handler._send = lambda code, payload: result.extend([code, payload])
    if body is None:
      handler.do_GET()
    else:
      raw = json.dumps(body).encode()
      handler.rfile = BytesIO(raw)
      handler.headers["Content-Length"] = str(len(raw))
      handler.do_POST()
    return tuple(result)

  try:
    yield SimpleNamespace(project=project, model=model, settings=settings, request=request)
  finally:
    runtime.close()


@pytest.fixture
def provider():
  def respond():
    return 200, {}, {"choices": [{"finish_reason": "stop", "message": {"content": "fix: update staged parameter"}}]}

  endpoint = SimpleNamespace(requests=[], respond=respond)

  class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
      body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
      endpoint.requests.append((self.path, self.headers.get("Authorization"), body))
      code, headers, payload = endpoint.respond()
      raw = json.dumps(payload).encode()
      self.send_response(code)
      self.send_header("Content-Length", str(len(raw)))
      for name, value in headers.items():
        self.send_header(name, value)
      self.end_headers()
      self.wfile.write(raw)

    def log_message(self, *_):
      pass

  with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as instance:
    endpoint.base_url = f"http://127.0.0.1:{instance.server_port}/v1"
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
      yield endpoint
    finally:
      instance.shutdown()
      thread.join()


def configure(studio, provider, mode="byok", **kwargs):
  code, settings = studio.request("/api/ai/settings", {"mode": mode, "baseUrl": provider.base_url + "/",
                                                     "model": "test-model", **kwargs})
  assert code == 200, settings
  return settings


def generation(studio, settings, **kwargs):
  snapshot = source_status(studio.project, "build")["snapshot"]
  destination = {key: settings[key] for key in ("mode", "baseUrl", "model")}
  return studio.request("/api/ai/commit-message", {"snapshot": snapshot, "scope": "project", "locale": "zh-CN",
                                                   "destination": destination, **kwargs})


def test_byok_drafts_from_staged_patch_and_keeps_credentials_write_only(studio_api, provider):
  studio = studio_api
  assert studio.request("/api/ai/settings")[1]["mode"] == "disabled"
  for path, body in (("/api/ai/settings", None), ("/api/ai/settings", {}), ("/api/ai/test", {}),
                     ("/api/ai/commit-message", {})):
    assert studio.request(path, body, authorized=False)[0] == 403
  key = "test-api-secret"
  settings = configure(studio, provider, apiKey=key)
  assert settings == {"mode": "byok", "baseUrl": provider.base_url, "model": "test-model", "hasApiKey": True}
  assert studio.request("/api/ai/settings") == (200, settings)
  if os.name == "posix":
    assert stat.S_IMODE(studio.settings.stat().st_mode) == 0o600

  studio.model.write_text("staged value\nunchanged context\n", encoding="utf-8")
  git(studio.project, "add", "src/note.txt")
  studio.model.write_text("private unstaged value\nunchanged context\n", encoding="utf-8")
  head = git(studio.project, "rev-parse", "HEAD")
  assert studio.request("/api/ai/test", {}) == (200, {"ok": True})
  assert generation(studio, settings) == (200, {"message": "fix: update staged parameter"})
  test_request, draft_request = provider.requests
  assert test_request[:2] == draft_request[:2] == ("/v1/chat/completions", "Bearer " + key)
  assert test_request[2] == {"model": "test-model", "messages": [{"role": "user", "content": "Connection test. Reply with OK."}]}
  assert "Simplified Chinese" in draft_request[2]["messages"][0]["content"]
  patch = draft_request[2]["messages"][1]["content"]
  assert "+staged value" in patch and "private unstaged value" not in patch and "unchanged context" not in patch
  assert git(studio.project, "rev-parse", "HEAD") == head
  assert git(studio.project, "show", ":src/note.txt") == b"staged value\nunchanged context\n"


def test_byoa_uses_configured_agent_without_key_and_stale_settings_block_transfer(studio_api, provider):
  studio = studio_api
  byok = configure(studio, provider, apiKey="test-api-secret")
  assert configure(studio, provider, model="other-model")["hasApiKey"]
  assert not configure(studio, provider, apiKey="")["hasApiKey"]
  assert configure(studio, provider, apiKey="replacement-test-key")["hasApiKey"]
  assert not configure(studio, provider, baseUrl=provider.base_url + "/agent")["hasApiKey"]
  configure(studio, provider, apiKey="replacement-test-key")
  byoa = configure(studio, provider, mode="byoa", model="external-agent")
  assert not byoa["hasApiKey"]
  studio.model.write_text("agent receives staged diff\n", encoding="utf-8")
  git(studio.project, "add", "src/note.txt")
  assert generation(studio, byok)[0] == 409 and not provider.requests
  assert generation(studio, byoa, locale="en")[0] == 200
  assert provider.requests[0][1] is None
  assert provider.requests[0][2]["model"] == "external-agent"
  assert "English" in provider.requests[0][2]["messages"][0]["content"]
  assert studio.request("/api/ai/settings", {"mode": "disabled"}) == (
    200, {"mode": "disabled", "baseUrl": "", "model": "", "hasApiKey": False})


def test_ai_rejects_missing_stale_or_oversized_stage_and_discards_racing_result(studio_api, provider):
  studio = studio_api
  settings = configure(studio, provider, mode="byoa")
  assert generation(studio, settings)[0] == 400
  stale = source_status(studio.project, "build")["snapshot"]
  studio.model.write_text("x" * (49 * 1024), encoding="utf-8")
  git(studio.project, "add", "src/note.txt")
  assert generation(studio, settings, snapshot=stale)[0] == 409
  assert generation(studio, settings)[0] == 400
  assert not provider.requests
  studio.model.write_text("valid input\n", encoding="utf-8")
  git(studio.project, "add", "src/note.txt")
  respond = provider.respond

  def mutate_index():
    studio.model.write_text("index changed during inference\n", encoding="utf-8")
    git(studio.project, "add", "src/note.txt")
    return respond()

  provider.respond = mutate_index
  assert generation(studio, settings)[0] == 409
  assert len(provider.requests) == 1


def test_ai_provider_failure_and_redirect_do_not_expose_secrets(studio_api, provider):
  studio = studio_api
  key = "test-api-secret"
  configure(studio, provider, apiKey=key)
  provider.respond = lambda: (401, {}, {"error": key})
  code, error = studio.request("/api/ai/test", {})
  assert code == 400 and "401" in error["error"] and key not in error["error"]
  provider.respond = lambda: (307, {"Location": provider.base_url + "/redirected"}, {})
  assert studio.request("/api/ai/test", {})[0] == 400
  assert len(provider.requests) == 2
  for url in ("http://example.com/v1", "https://user:password@example.com/v1", "https://example.com/v1?key=secret"):
    assert studio.request("/api/ai/settings", {"mode": "byok", "baseUrl": url, "model": "example"})[0] == 400


@pytest.mark.parametrize("studio_api", [True], indirect=True)
def test_private_ai_settings_inside_workspace_cannot_be_saved_browsed_or_staged(studio_api, provider):
  studio = studio_api
  code, payload = studio.request("/api/ai/settings", {"mode": "byok", "baseUrl": provider.base_url,
                                                    "model": "test-model", "apiKey": "test-api-secret"})
  assert code == 400 and "仓库之外" in payload["error"]
  assert not studio.settings.exists()
  studio.settings.parent.mkdir(parents=True)
  studio.settings.write_text('{"mode":"disabled","apiKey":"private-existing-value"}', encoding="utf-8")
  studio.settings.chmod(0o600)
  relative = studio.settings.relative_to(studio.project).as_posix()
  assert studio.request("/api/file?path=" + relative)[0] == 400
  uppercase = studio.settings.with_name(studio.settings.name.upper())
  if uppercase.exists():
    assert studio.request("/api/file?path=" + uppercase.relative_to(studio.project).as_posix())[0] == 400
  assert relative not in [item["path"] for item in studio.request("/api/files")[1]["files"]]
  assert relative not in [item["path"] for item in studio.request("/api/scm")[1]["entries"]]
  ordinary = studio.project / "src" / studio.settings.name
  ordinary.write_text("ordinary project file", encoding="utf-8")
  assert studio.request("/api/file?path=src/" + ordinary.name)[1]["content"] == "ordinary project file"


def test_staged_draft_in_nested_project_rejects_outside_stage_and_handles_split_index(studio_api):
  repo = studio_api.project
  nested = repo / "nested"
  nested.mkdir()
  (nested / "note.txt").write_text("new nested content\n", encoding="utf-8")
  git(repo, "update-index", "--split-index")
  git(repo, "add", "nested/note.txt")
  snapshot = source_status(nested, "build")["snapshot"]
  assert "+new nested content" in staged_commit_diff(nested, "build", snapshot)
  studio_api.model.write_text("private outside content\n", encoding="utf-8")
  git(repo, "add", "src/note.txt")
  snapshot = source_status(nested, "build")["snapshot"]
  with pytest.raises(ProjectError, match="项目以外"):
    staged_commit_diff(nested, "build", snapshot)


def test_staged_draft_supports_initial_commit(tmp_path):
  git(tmp_path, "init", "-b", "main")
  (tmp_path / "first.txt").write_text("initial content\n", encoding="utf-8")
  git(tmp_path, "add", "first.txt")
  snapshot = source_status(tmp_path, "build")["snapshot"]
  assert "+initial content" in staged_commit_diff(tmp_path, "build", snapshot)
