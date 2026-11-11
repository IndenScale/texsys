"""Package UI transport retains dependency plans, provenance and library visibility."""

from io import BytesIO
import json
import shutil
from types import SimpleNamespace
import tomllib

import pytest

from texsys.dependencies import lock_project
from texsys.project import initialize_project
from texsys.registry import publish_package
from texsys.studio import server
from texsys.studio.library_sources import library_index, reference_library_entry
from texsys.studio.runtime import StudioRuntime
from texsys.project import ProjectError


def _vendor(root, version):
  if not root.exists():
    initialize_project(root)
  (root / "texsys.toml").write_text(
    f'schema_version = 1\n[project]\nname = "vendor"\nversion = "{version}"\n'
    '[layout]\nsource = "src"\nassets = "assets"\nbuild = "build"\n'
    '[package]\nkind = "capability"\ndescription = "Pump models"\n'
    'scope = "Concept package UI fixture"\ncapabilities = ["models"]\n'
    'tests = ["assets/tests.py"]\n')
  devices = root / "src/library/devices"
  devices.mkdir(parents=True, exist_ok=True)
  (devices / "pump.sdl.json").write_text(json.dumps({"schema_version": 1, "root": "Pump", "components": {"Pump": {}}}))
  (devices.parent / "models.json").write_text(json.dumps({"schema_version": 1, "models": {
    "pump-a": {"family": "Pump", "manufacturer": "Fixture", "model": "Pump-A"},
  }}))
  (root / "assets/tests.py").write_text("def test_example():\n  assert True\n")


@pytest.fixture
def package_api(tmp_path, monkeypatch):
  project, vendor, registry = (tmp_path / name for name in ("project", "vendor", "registry"))
  initialize_project(project)
  _vendor(vendor, "1.0")
  publish_package(vendor, registry)
  _vendor(vendor, "2.0")
  publish_package(vendor, registry)
  runtime = StudioRuntime(project, plugins={})
  monkeypatch.setattr(server, "StudioServer", lambda address, handler, terminals: handler)
  handler_type = server.create_server(runtime, frontend=tmp_path, token="package-test")

  def request(body=None, *, authorized=True):
    handler = object.__new__(handler_type)
    handler.server = SimpleNamespace(server_port=8710)
    handler.path = "/api/packages"
    handler.headers = {"Host": "127.0.0.1:8710"}
    if authorized:
      handler.headers["X-Tex-Studio-Token"] = "package-test"
    result = []
    handler._send = lambda code, payload: result.extend([code, payload])
    if body is None:
      handler.do_GET()
    else:
      content = json.dumps(body).encode()
      handler.headers["Content-Length"] = str(len(content))
      handler.rfile = BytesIO(content)
      handler.do_POST()
    return result

  yield project, vendor, registry, request
  runtime.close()


def _install(request, registry):
  status, state = request()
  assert status == 200
  change = {"action": "add", "name": "vendor", "version": "1.0", "registry": str(registry), "revision": state["revision"]}
  status, plan = request({"action": "plan", "request": change})
  assert status == 200
  assert request({"action": "apply", "request": change, "plan": plan["plan"]})[0] == 200
  return change, plan


def test_package_api_browse_plan_apply_and_library_discovery(package_api):
  project, vendor, registry, request = package_api
  before = (project / "texsys.toml").read_bytes()
  assert request(authorized=False)[0] == 403
  assert request({"action": "browse", "registry": str(registry)}, authorized=False)[0] == 403
  status, catalog = request({"action": "browse", "registry": "../registry"})
  assert status == 200 and [item["version"] for item in catalog["packages"]] == ["1.0", "2.0"]
  assert (project / "texsys.toml").read_bytes() == before
  assert not (project / "texsys.lock").exists()
  change, plan = _install(request, registry)
  assert plan["changes"][0]["before"] is None
  assert plan["changes"][0]["after"]["source"] == "registry"
  status, state = request()
  assert status == 200 and state["packages"][0]["installed"]
  assert state["packages"][0]["runtime_status"]["status"] == "asset-only"
  assert state["packages"][0]["tests"] == ["assets/tests.py"]
  index = library_index(project, "src", [])
  assert not index["references"] and not index["source_errors"]
  assert {item["kind"] for item in index["library"]} == {"devices", "models"}
  assert index["sources"][0]["origin"] == "registry"
  first_identity = index["sources"][0]["id"]
  locked = lock_project(project, check=True).packages[0]
  assert str(locked.root) not in json.dumps(tomllib.loads((project / "texsys.toml").read_text())["dependencies"])
  with pytest.raises(ProjectError, match="managed by its registry"):
    reference_library_entry(project, {"folder": str(locked.root), "digest": locked.digest,
                                      "entry": "src/library/models.json#pump-a"})
  # No original publisher directory is needed for sync or library browsing.
  shutil.rmtree(vendor)
  shutil.rmtree(project / "build/dependencies")
  assert request({"action": "sync", "revision": state["revision"]})[0] == 200
  assert library_index(project, "src", [])["sources"][0]["id"] == first_identity
  assert request({"action": "apply", "request": change, "plan": plan["plan"]})[0] == 400


def test_package_api_rejects_stale_plan_and_sync_without_changing_lock(package_api):
  project, _, registry, request = package_api
  _install(request, registry)
  _, state = request()
  change = {"action": "update", "name": "vendor", "version": "2.0", "revision": state["revision"]}
  code, plan = request({"action": "plan", "request": change})
  assert code == 200
  assert plan["changes"][0]["before"]["version"] == "1.0"
  assert plan["changes"][0]["after"]["version"] == "2.0"
  with (project / "texsys.toml").open("a") as stream:
    stream.write("\n# External editor changed the manifest\n")
  before = (project / "texsys.lock").read_bytes()
  assert request({"action": "apply", "request": change, "plan": plan["plan"]})[0] == 400
  assert request({"action": "sync", "revision": state["revision"]})[0] == 400
  assert (project / "texsys.lock").read_bytes() == before


def test_package_api_update_remove_and_test_scope(package_api, monkeypatch):
  project, _, registry, request = package_api
  _install(request, registry)
  monkeypatch.setattr("texsys.package_tests.test_capability_package", lambda root, name: {
    "status": "pass", "scope": "package-unit-tests", "engineering_acceptance": "not-evaluated", "name": name,
  })
  code, report = request({"action": "test", "name": "vendor"})
  assert code == 200 and report["engineering_acceptance"] == "not-evaluated"
  assert not (project / "reviews").exists()
  for action, version in (("update", "2.0"), ("remove", None)):
    _, state = request()
    change = {"action": action, "name": "vendor", "revision": state["revision"]}
    if version:
      change["version"] = version
    code, plan = request({"action": "plan", "request": change})
    assert code == 200
    code, applied = request({"action": "apply", "request": change, "plan": plan["plan"]})
    assert code == 200 and applied["applied"]
  assert request()[1]["packages"] == []
  assert library_index(project, "src", [])["library"] == []


def test_registry_library_discovery_deduplicates_existing_sdl_import(package_api):
  project, _, registry, request = package_api
  _install(request, registry)
  (project / "src/system.sdl.json").write_text(json.dumps({
    "schema_version": 1, "root": "Station", "imports": {"Pump": "@vendor/library/devices/pump.sdl.json#Pump"},
    "components": {"Station": {"instances": {"P1": {"component": "Pump"}}}},
  }))
  index = library_index(project, "src", [])
  assert len(index["sources"]) == 1
  assert len([item for item in index["library"] if item["kind"] == "devices"]) == 1
  assert len([item for item in index["library"] if item["kind"] == "models"]) == 1
  assert {item["source"] for item in index["library"] if "source" in item} == {index["sources"][0]["id"]}
