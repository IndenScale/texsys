import json
from io import BytesIO
from types import SimpleNamespace

import pytest
import tomllib

from texsys.dependencies import add_dependency, lock_project
from texsys.project import ProjectError, initialize_project
from texsys.studio import server
from texsys.studio.design import design_index
from texsys.studio.library_sources import library_index, reference_library_entry
from texsys.studio.runtime import StudioRuntime


def write_manifest(root, text):
  with (root / "texsys.toml").open("a", encoding="utf-8") as stream:
    stream.write(text)


def test_library_catalog_uses_definition_tags(tmp_path):
  initialize_project(tmp_path)
  file = tmp_path / "src/library/cells/valve.sdl.json"
  file.parent.mkdir(parents=True)
  file.write_text('{"schema_version": 1, "root": "Valve", "components": {"Valve": {}}}')
  write_manifest(tmp_path, '\n[studio.library_tags."src/library/cells/valve.sdl.json#Valve"]\n'
                           'floor = "B1"\nlot = "MEP-01"\ndiscipline = ["HVAC", "HVAC"]\ninvalid = 42\n')
  index = design_index(tmp_path, "src")
  assert index["library"][0]["tags"] == {"discipline": ["HVAC"]}
  assert set(index["tag_errors"]) == {
    f"studio.library_tags.src/library/cells/valve.sdl.json#Valve.{key}" for key in ("floor", "lot", "invalid")
  }


def test_instance_tags_belong_to_resolved_occurrence_paths(tmp_path):
  initialize_project(tmp_path)
  document = {"schema_version": 1, "root": "Station", "components": {
    "Station": {"instances": {"PG-01": {"component": "PumpGroup"}, "PG-02": {"component": "PumpGroup"}}},
    "PumpGroup": {"instances": {"pump": {"component": "Pump"}}},
    "Pump": {},
  }}
  (tmp_path / "src/system.sdl.json").write_text(json.dumps(document))
  write_manifest(tmp_path, '\n[studio.instance_tags."Station.PG-01"]\nfloor = "B1"\nlot = "MEP-01"\n'
                           '\n[studio.instance_tags."Station.PG-02"]\nfloor = "B2"\nlot = "MEP-02"\n'
                           '\n[studio.instance_tags."Station.PG-02.pump"]\nfloor = "B2"\n'
                           '\n[studio.instance_tags."Station.PG-99"]\nfloor = "B1"\n')
  index = design_index(tmp_path, "src")
  assert index["instance_tags"] == {
    "Station.PG-01": {"floor": ["B1"], "lot": ["MEP-01"]},
    "Station.PG-02": {"floor": ["B2"], "lot": ["MEP-02"]},
    "Station.PG-02.pump": {"floor": ["B2"]},
  }
  assert index["tag_errors"] == ["studio.instance_tags.Station.PG-99"]


def create_vendor(root, version="1.0"):
  initialize_project(root)
  manifest = root / "texsys.toml"
  original = manifest.read_text()
  manifest.write_text(original.replace("[project]", f'[project]\nversion = "{version}"'))
  devices = root / "src/library/devices"
  devices.mkdir(parents=True)
  (devices / "pump.sdl.json").write_text('{"schema_version": 1, "root": "Pump", "components": {"Pump": {}}}')
  catalog = devices.parent / "models.json"
  catalog.write_text(json.dumps({"schema_version": 1, "models": {
    "pump-80": {"family": "Chilled water pump", "manufacturer": root.name, "model": "P-80"},
  }}))
  write_manifest(root, '\n[package]\nkind = "component"\n[package.exports]\ncomponents = ["src/library"]\n')
  return catalog


def test_external_library_browse_keeps_sources_distinct_and_does_not_change_project(tmp_path):
  project, vendor_a, vendor_b = (tmp_path / name for name in ("project", "vendor-a", "vendor-b"))
  initialize_project(project)
  create_vendor(vendor_a)
  create_vendor(vendor_b)
  manifest = project / "texsys.toml"
  before = manifest.read_bytes()
  index = library_index(project, "src", ["../vendor-a", str(vendor_a), str(vendor_b), "../missing"])
  assert [(source["name"], source["version"]) for source in index["sources"]] == [
    ("vendor-a", "1.0"), ("vendor-b", "1.0"),
  ]
  assert len({item["definition"] for item in index["library"]}) == 4
  models = [index["definitions"][item["definition"]] for item in index["library"] if item["kind"] == "models"]
  assert [model["model"]["manufacturer"] for model in models] == ["vendor-a", "vendor-b"]
  assert len(index["source_errors"]) == 1
  assert manifest.read_bytes() == before
  assert not (project / "texsys.lock").exists()


def test_requested_library_source_maps_exact_folder_with_existing_dependencies(tmp_path):
  project, installed, external, rules = (tmp_path / name for name in ("project", "installed", "external", "rules"))
  initialize_project(project)
  create_vendor(installed)
  create_vendor(external)
  initialize_project(rules)
  write_manifest(rules, '\n[package]\nkind = "capability"\n')
  add_dependency(project, installed, "1.0")
  before = (project / "texsys.toml").read_bytes(), (project / "texsys.lock").read_bytes()
  requested = ["../external", str(installed), "../missing", str(rules), str(external)]
  index = library_index(project, "src", requested)
  sources = {item["name"]: item["id"] for item in index["sources"]}
  assert [item["name"] for item in index["sources"]] == ["installed", "external"]
  assert index["requested_sources"] == [
    {"folder": "../external", "source_id": sources["external"]},
    {"folder": str(installed), "source_id": sources["installed"]},
    {"folder": str(external), "source_id": sources["external"]},
  ]
  assert len(index["source_errors"]) == 2
  assert before == ((project / "texsys.toml").read_bytes(), (project / "texsys.lock").read_bytes())


def test_library_selection_requires_cli_import_and_never_updates_dependencies(tmp_path):
  project, vendor = tmp_path / "project", tmp_path / "vendor"
  initialize_project(project)
  create_vendor(vendor)
  old = library_index(project, "src", [str(vendor)])["sources"][0]
  assert not old["locked"] and "texsys add" in old["cli_hint"]
  request = {"folder": str(vendor), "digest": old["digest"], "entry": "src/library/models.json#pump-80"}
  before = (project / "texsys.toml").read_bytes()
  with pytest.raises(ProjectError, match="texsys add"):
    reference_library_entry(project, request)
  assert (project / "texsys.toml").read_bytes() == before
  assert not (project / "texsys.lock").exists()

  add_dependency(project, vendor, "1.0")
  locked = (project / "texsys.lock").read_bytes()
  declarations = tomllib.loads((project / "texsys.toml").read_text())["dependencies"]
  reference_library_entry(project, request)
  selected = (project / "texsys.toml").read_bytes()
  reference_library_entry(project, request)
  assert selected == (project / "texsys.toml").read_bytes()
  assert (project / "texsys.lock").read_bytes() == locked
  assert tomllib.loads(selected.decode())["dependencies"] == declarations
  lock_project(project, check=True)
  index = library_index(project, "src", [str(vendor)])
  assert len(index["sources"]) == 1 and index["sources"][0]["locked"]
  assert len(index["references"]) == 1 and index["references"][0]["state"] == "current"

  manifest = vendor / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('version = "1.0"', 'version = "2.0"'))
  with pytest.raises(ProjectError):
    reference_library_entry(project, request)
  assert (project / "texsys.lock").read_bytes() == locked
  add_dependency(project, vendor, "2.0")
  locked = (project / "texsys.lock").read_bytes()
  index = library_index(project, "src", [])
  assert index["references"][0]["state"] == "needs-review"
  request["digest"] = index["sources"][0]["digest"]
  with pytest.raises(ProjectError, match="Review the project component references"):
    reference_library_entry(project, request)
  request["previous_digest"] = old["digest"]
  reference_library_entry(project, request)
  assert (project / "texsys.lock").read_bytes() == locked
  assert library_index(project, "src", [])["references"][0]["state"] == "current"


def test_library_api_requires_session_and_exposes_locked_reference(tmp_path, monkeypatch):
  project, vendor = tmp_path / "project", tmp_path / "vendor"
  initialize_project(project)
  create_vendor(vendor)
  runtime = StudioRuntime(project, plugins={})
  monkeypatch.setattr(server, "StudioServer", lambda address, handler, terminals: handler)
  handler_type = server.create_server(runtime, frontend=tmp_path, token="catalog-test")

  def request(body=None, authorized=True):
    handler = object.__new__(handler_type)
    handler.server = SimpleNamespace(server_port=8710)
    handler.headers = {"Host": "127.0.0.1:8710"}
    if authorized:
      handler.headers["X-Tex-Studio-Token"] = "catalog-test"
    result = []
    handler._send = lambda code, payload: result.extend([code, payload])
    if body is None:
      handler.path = "/api/design-index"
      handler.do_GET()
    else:
      handler.path = "/api/library"
      payload = json.dumps(body).encode()
      handler.headers["Content-Length"] = str(len(payload))
      handler.rfile = BytesIO(payload)
      handler.do_POST()
    return result

  try:
    operation = {"action": "index", "folders": [str(vendor)]}
    assert request(operation, authorized=False)[0] == 403
    status, index = request(operation)
    assert status == 200 and not index["references"]
    add_dependency(project, vendor, "1.0")
    status, record = request({"action": "reference", "folder": str(vendor),
                              "digest": index["sources"][0]["digest"], "entry": "src/library/models.json#pump-80"})
    assert status == 200 and record["version"] == "1.0"
    status, index = request()
    assert status == 200 and index["sources"][0]["locked"]
  finally:
    runtime.close()
