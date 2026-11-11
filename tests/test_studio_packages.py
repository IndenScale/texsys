"""Retired dependency endpoints do not expose or change the project."""

from io import BytesIO
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from texsys.cli import app
from texsys.project import initialize_project
from texsys.studio import server
from texsys.studio.runtime import StudioRuntime


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_retired_package_endpoint_is_unknown_and_preserves_project(tmp_path, monkeypatch, installed, method):
  project = tmp_path / "project"
  initialize_project(project)
  if installed:
    vendor = tmp_path / "vendor"
    initialize_project(vendor)
    manifest = vendor / "texsys.toml"
    manifest.write_text(manifest.read_text().replace("[project]", '[project]\nversion = "1.0"'))
    added = CliRunner().invoke(app, ["add", str(vendor), "-p", str(project)])
    assert added.exit_code == 0, added.output
  runtime = StudioRuntime(project, plugins={})
  monkeypatch.setattr(server, "StudioServer", lambda address, handler, terminals: handler)
  handler_type = server.create_server(runtime, frontend=tmp_path, token="package-test")

  def files():
    return {path.relative_to(project).as_posix(): path.read_bytes()
            for path in project.rglob("*") if path.is_file()}

  def request(authorized):
    handler = object.__new__(handler_type)
    handler.server = SimpleNamespace(server_port=8710)
    handler.path = "/api/packages"
    content = json.dumps({"action": "sync"}).encode()
    handler.headers = {"Host": "127.0.0.1:8710", "Content-Length": str(len(content))}
    if authorized:
      handler.headers["X-Tex-Studio-Token"] = "package-test"
    handler.rfile = BytesIO(content)
    result = []
    handler._send = lambda code, payload: result.extend([code, payload])
    getattr(handler, "do_" + method)()
    return result

  try:
    before = files()
    assert request(False)[0] == 403
    assert request(True) == [404, {"error": "Unknown API route."}]
    assert files() == before
  finally:
    runtime.close()
