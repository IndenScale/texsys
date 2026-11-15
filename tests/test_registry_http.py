from contextlib import contextmanager
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
from threading import Thread

import pytest
import tomlkit
from typer.testing import CliRunner

from texsys.cli import app
from texsys.dependencies import add_registry_dependency, lock_project, sync_project
from texsys.formal import check_project
from texsys.project import ProjectError, initialize_project
from texsys.registry import parse_registry, publish_package


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
  monkeypatch.setenv("TEXSYS_CACHE_DIR", str(tmp_path / "cache"))


def capability(root, name, version="1.0.0", dependency=None):
  initialize_project(root)
  manifest = root / "texsys.toml"
  data = tomlkit.parse(manifest.read_text())
  data["project"]["name"] = name
  data["project"]["version"] = version
  data["package"] = {"kind": "capability", "description": f"{name} test capability", "scope": "test-only",
                     "capabilities": ["model"], "include": ["README.md", "tests"], "tests": ["tests"]}
  if dependency:
    data["dependencies"] = {dependency.name: {"path": str(dependency), "version": "1.0.0"}}
  manifest.write_text(tomlkit.dumps(data))
  (root / "README.md").write_text("# 测试能力包\n")
  (root / "tests").mkdir()
  (root / "tests/test_capability.py").write_text("def test_example():\n  assert 1 + 1 == 2\n")
  library = root / "src/library/devices"
  library.mkdir(parents=True)
  item = {"schema_version": 1, "root": "Pump", "components": {"Pump": {"ports": {"outlet": {"kind": "water", "direction": "out"}}}}}
  (library / "pump.sdl.json").write_text(json.dumps(item))
  (root / "assets/performance.json").write_text('{"nominal": 100}\n')
  return root


def consumer(root, package="equipment"):
  initialize_project(root)
  document = {"schema_version": 1, "root": "Station", "imports": {"Pump": f"@{package}/library/devices/pump.sdl.json"},
              "components": {"Station": {"instances": {"P-01": {"component": "Pump"}}}}}
  (root / "src/system.sdl.json").write_text(json.dumps(document))
  return root


class _QuietHandler(SimpleHTTPRequestHandler):
  def log_message(self, *args):
    pass


@contextmanager
def serving(registry: Path):
  handler = partial(_QuietHandler, directory=str(registry))
  server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
  thread = Thread(target=server.serve_forever, daemon=True)
  thread.start()
  try:
    yield f"http://127.0.0.1:{server.server_address[1]}"
  finally:
    server.shutdown()
    server.server_close()


def published_closure(tmp_path):
  base = capability(tmp_path / "authors/base", "base")
  equipment = capability(tmp_path / "authors/equipment", "equipment", dependency=base)
  registry = tmp_path / "registry"
  publish_package(equipment, registry)
  return registry


def test_http_registry_end_to_end_with_transitive_closure(tmp_path):
  registry = published_closure(tmp_path)
  with serving(registry) as origin:
    one = consumer(tmp_path / "one")
    added = add_registry_dependency(one, "equipment", "1.0.0", origin)
    assert {item.name for item in added.packages} == {"base", "equipment"}
    assert all(item.source == "registry" and item.registry == origin for item in added.packages)
    lock_text = (one / "texsys.lock").read_text()
    assert f'registry = "{origin}"' in lock_text
    assert check_project(one).status == "pass"
    # The absolute origin makes the lock independent of the consumer location.
    two = consumer(tmp_path / "elsewhere/two")
    add_registry_dependency(two, "equipment", "1.0.0", origin)
    assert (one / "texsys.lock").read_bytes() == (two / "texsys.lock").read_bytes()
    # A fresh cache and no installed tree force a real HTTP fetch.
    shutil.rmtree(one / "build")
    shutil.rmtree(tmp_path / "cache")
    sync_project(one)
    assert check_project(one).status == "pass"
    assert lock_project(one, check=True).packages[0].name == "base"
  # Cached restore works without the registry, exactly like a file registry.
  shutil.rmtree(one / "build")
  sync_project(one)
  assert check_project(one).status == "pass"


def test_cli_add_and_show_over_http(tmp_path):
  registry = published_closure(tmp_path)
  with serving(registry) as origin:
    project = consumer(tmp_path / "consumer")
    runner = CliRunner()
    added = runner.invoke(app, ["add", "equipment", "--version", "1.0.0", "--registry", origin, "-p", str(project)])
    assert added.exit_code == 0, added.output
    shown = runner.invoke(app, ["package", "show", "equipment", "--version", "1.0.0", "--registry", origin])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["name"] == "equipment"
    assert check_project(project).status == "pass"


def test_conflicting_release_content_rejected(tmp_path):
  registry = published_closure(tmp_path)
  with serving(registry) as origin:
    root = consumer(tmp_path / "consumer")
    add_registry_dependency(root, "equipment", "1.0.0", origin)
    # Same identity, different content, swapped into the served release.
    changed = capability(tmp_path / "changed/equipment", "equipment")
    (changed / "assets/performance.json").write_text('{"nominal": 999}\n')
    other = tmp_path / "other-registry"
    publish_package(changed, other)
    shutil.rmtree(registry / "packages/equipment/1.0.0")
    shutil.copytree(other / "packages/equipment/1.0.0", registry / "packages/equipment/1.0.0")
    shutil.rmtree(tmp_path / "cache")
    shutil.rmtree(root / "build")
    with pytest.raises(ProjectError, match="Locked registry release changed"):
      sync_project(root)


def test_tampered_metadata_rejected(tmp_path):
  registry = published_closure(tmp_path)
  release = registry / "packages/equipment/1.0.0"
  with serving(registry) as origin:
    root = consumer(tmp_path / "consumer")
    add_registry_dependency(root, "equipment", "1.0.0", origin)
    metadata = json.loads((release / "package.json").read_text())
    metadata["digest"] = "sha256:" + "0" * 64
    (release / "package.json").write_text(json.dumps(metadata))
    shutil.rmtree(tmp_path / "cache")
    shutil.rmtree(root / "build")
    with pytest.raises(ProjectError, match="Locked registry release changed"):
      sync_project(root)


def test_archive_swap_rejected(tmp_path):
  registry = published_closure(tmp_path)
  release = registry / "packages/equipment/1.0.0"
  base_release = registry / "packages/base/1.0.0"
  with serving(registry) as origin:
    root = consumer(tmp_path / "consumer")
    add_registry_dependency(root, "equipment", "1.0.0", origin)
    shutil.copyfile(base_release / "artifact.texpkg", release / "artifact.texpkg")
    shutil.rmtree(tmp_path / "cache")
    shutil.rmtree(root / "build")
    with pytest.raises(ProjectError, match="metadata mismatch"):
      sync_project(root)


def test_missing_release_reports_request_failure(tmp_path):
  registry = published_closure(tmp_path)
  with serving(registry) as origin:
    root = consumer(tmp_path / "consumer")
    with pytest.raises(ProjectError, match="Registry request failed"):
      add_registry_dependency(root, "equipment", "9.9.9", origin)


def test_redirect_rejected(tmp_path):
  class RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
      self.send_response(302)
      self.send_header("Location", "http://127.0.0.1:1/elsewhere")
      self.end_headers()

    def log_message(self, *args):
      pass

  server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
  thread = Thread(target=server.serve_forever, daemon=True)
  thread.start()
  try:
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    root = consumer(tmp_path / "consumer")
    with pytest.raises(ProjectError, match="redirects are not allowed"):
      add_registry_dependency(root, "equipment", "1.0.0", origin)
  finally:
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("raw", [
  "ftp://host/registry",
  "http://user:pass@host",
  "http://host/registry?token=1",
  "http://",
])
def test_invalid_origin_rejected(raw):
  with pytest.raises(ProjectError):
    parse_registry(raw)


def test_manifest_with_unsupported_scheme_fails(tmp_path):
  root = consumer(tmp_path / "consumer")
  manifest = tomlkit.parse((root / "texsys.toml").read_text())
  manifest["dependencies"] = {"equipment": {"registry": "ftp://host/registry", "version": "1.0.0"}}
  (root / "texsys.toml").write_text(tomlkit.dumps(manifest))
  with pytest.raises(ProjectError, match="Unsupported registry scheme"):
    lock_project(root)
