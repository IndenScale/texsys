import json
from io import BytesIO
from types import SimpleNamespace

import pytest

from texsys.dependencies import lock_project
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


def test_library_reference_pins_reviewed_entry_and_updates_only_explicitly(tmp_path):
  project, vendor = tmp_path / "project", tmp_path / "vendor"
  initialize_project(project)
  catalog = create_vendor(vendor)
  old = library_index(project, "src", [str(vendor)])["sources"][0]
  request = {"folder": str(vendor), "digest": old["digest"], "entry": "src/library/models.json#pump-80"}
  reference_library_entry(project, request)
  reference_library_entry(project, request)
  lock_project(project, check=True)
  index = library_index(project, "src", [])
  assert index["sources"][0]["locked"]
  assert len(index["references"]) == 1

  manifest = vendor / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('version = "1.0"', 'version = "2.0"'))
  with pytest.raises(ProjectError, match="contents changed"):
    reference_library_entry(project, request)
  latest = library_index(project, "src", [str(vendor)])["sources"][0]
  before = (project / "texsys.lock").read_bytes()
  request["digest"] = latest["digest"]
  with pytest.raises(ProjectError, match="dependency 'vendor' changed"):
    reference_library_entry(project, request)
  assert (project / "texsys.lock").read_bytes() == before

  request["previous_digest"] = old["digest"]
  catalog.write_text('{"schema_version": 1, "models": {}}')
  request["digest"] = library_index(project, "src", [str(vendor)])["sources"][0]["digest"]
  with pytest.raises(ProjectError, match="public library entry"):
    reference_library_entry(project, request)
  assert (project / "texsys.lock").read_bytes() == before

  catalog.write_text(json.dumps({"schema_version": 1, "models": {
    "pump-80": {"family": "Pump", "manufacturer": "vendor", "model": "P-80"},
  }}))
  request["digest"] = library_index(project, "src", [str(vendor)])["sources"][0]["digest"]
  reference_library_entry(project, request)
  lock_project(project, check=True)
  index = library_index(project, "src", [])
  assert [(source["version"], source["locked"]) for source in index["sources"]] == [("2.0", True)]
  assert [record["digest"] for record in index["references"]] == [request["digest"]]


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
    status, record = request({"action": "reference", "folder": str(vendor),
                              "digest": index["sources"][0]["digest"], "entry": "src/library/models.json#pump-80"})
    assert status == 200 and record["version"] == "1.0"
    status, index = request()
    assert status == 200 and index["sources"][0]["locked"]
  finally:
    runtime.close()
