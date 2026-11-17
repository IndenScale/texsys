"""Exercise real IFC exchange, source changes, invalid inputs and read-back failures."""

import builtins
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import ifcopenshell
import ifcopenshell.util.element
import pytest

from project_process import build_project
from texsys.dependencies import lock_project
from texsys.evidence import file_digest, write_json
from texsys_datacenter_reference.ifc_backend import IfcWriter, validate_exchange
from texsys_datacenter_reference.ifc_delivery import build_ifc_coordination


REPO = Path(__file__).resolve().parents[1]
OUTPUT = Path("build/ifc/aidc-coordination.ifc")


def _read(path):
  return json.loads(path.read_text())


def _checkout(tmp_path, name="aidc"):
  root = tmp_path/name
  shutil.copytree(REPO/"examples/datacenter-reference-design", root, ignore=shutil.ignore_patterns("build"))
  manifest = root/"texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(root)
  return root


def _build(root):
  result = build_project(root, targets=["ifc-coordination"])
  report = _read(result.report)
  return result, {row["id"]: row for row in report["targets"]}


def test_real_ifc_exchange_rebuilds_geometry_preserves_ids_and_is_reproducible(tmp_path):
  root = _checkout(tmp_path)
  result, targets = _build(root)
  assert result.status == "pass", result.diagnostics
  output = root/OUTPUT
  first_bytes = output.read_bytes()
  sources = _read(output.with_suffix(".sources.json"))
  report = _read(output.with_suffix(".validation.json"))
  assert report["schema"] == "IFC4" and report["diagnostics"] == []
  assert report["geometry_checked"] > 400 and report["connections_checked"] > 150
  assert report["engineering_acceptance"] == "not-assessed"
  model = ifcopenshell.open(output)
  assert len(model.by_type("IfcPipeSegment")) == 133
  assert len(model.by_type("IfcFurniture")) == 128
  assert len(model.by_type("IfcDistributionSystem")) == 2
  assert not model.by_type("IfcBuildingElementProxy")
  assert report["classes"]["IfcUnitaryEquipment"] == 96
  by_identity = {row["identity"]: guid for guid, row in sources["objects"].items()}
  rack = model.by_guid(by_identity["asset/IT01/Rack01"])
  assert ifcopenshell.util.element.get_psets(rack)["Texsys_Source"]["State"] == "assumed"
  assert all(len(port.ConnectedTo)+len(port.ConnectedFrom) == 1 for port in model.by_type("IfcDistributionPort"))
  assert all(row["source"]["digest"].startswith("sha256:") for row in sources["objects"].values())

  result, targets = _build(root)
  assert result.status == "pass" and targets["ifc-coordination"]["execution"] == "cached"
  # Removing both public and cache output forces a real second execution.
  for path in (root/"build").glob(".cache*"):
    if path.is_dir():
      shutil.rmtree(path)
  original_build = root/"build"
  shutil.rmtree(original_build)
  result, targets = _build(root)
  assert result.status == "pass" and targets["ifc-coordination"]["execution"] == "executed"
  assert output.read_bytes() == first_bytes

  route_path = root/"src/fws-network.json"
  route = _read(route_path)
  route["legs"]["supply"]["spine_x_m"] += .02
  write_json(route_path, route)
  result, targets = _build(root)
  assert result.status == "pass", result.diagnostics
  assert targets["dcr:fws-network"]["execution"] == "executed"
  assert targets["dcr:pod-layout"]["execution"] == "cached"
  updated = _read(output.with_suffix(".sources.json"))
  assert {row["identity"]: guid for guid, row in updated["objects"].items()} == by_identity
  assert updated["ifc_digest"] != sources["ifc_digest"]
  assert updated["input_digest"] != sources["input_digest"]
  assert any(row.get("geometry") != updated["objects"][guid].get("geometry")
             for guid, row in sources["objects"].items())
  assert _read(output.with_suffix(".validation.json"))["ifc_digest"] == file_digest(output)

  route["pipe_types"]["header"]["inside_m"] = -1
  write_json(route_path, route)
  result, targets = _build(root)
  assert result.status != "pass" and targets["ifc-coordination"]["execution"] == "blocked"
  assert targets["dcr:fws-network"]["status"] != "pass"


def test_second_project_has_separate_ifc_namespace_and_source_changes_do_not_leak(tmp_path):
  first = _checkout(tmp_path, "first")
  second = _checkout(tmp_path, "second")
  config = _read(second/"src/ifc.json")
  config.update(project_id="urn:texsys:ifc:second-project", name="Second project")
  write_json(second/"src/ifc.json", config)
  for root in (first, second):
    result, _ = _build(root)
    assert result.status == "pass", result.diagnostics
  a = _read((first/OUTPUT).with_suffix(".sources.json"))
  b = _read((second/OUTPUT).with_suffix(".sources.json"))
  assert {row["identity"] for row in a["objects"].values()} == {row["identity"] for row in b["objects"].values()}
  assert set(a["objects"]).isdisjoint(b["objects"])
  assert _read(first/"src/ifc.json")["project_id"] != config["project_id"]


@pytest.mark.parametrize("case,expected", [("missing", "indeterminate"), ("schema", "fail"), ("namespace", "fail"),
                                          ("unknown-option", "fail"), ("scope", "fail"), ("backend", "indeterminate")])
def test_unavailable_or_unsupported_exchange_never_pretends_to_write_ifc(tmp_path, monkeypatch, case, expected):
  write_json(tmp_path/"src/ifc.json", {"schema_version": 1, "schema": "IFC4", "project_id": "urn:test", "name": "test"})
  (tmp_path/"texsys.toml").write_text('schema_version=1\n[project]\nname="test"\n[layout]\nsource="src"\nassets="assets"\nbuild="build"\n')
  config_path = tmp_path/"src/ifc.json"
  if case == "missing":
    config_path.unlink()
  elif case != "backend":
    config = _read(config_path)
    if case == "schema":
      config["schema"] = "IFC2X3"
    elif case == "namespace":
      config["project_id"] = ""
    elif case == "unknown-option":
      config["invent_missing_data"] = True
    write_json(config_path, config)
  if case == "backend":
    actual = builtins.__import__

    def unavailable(name, *args, **kwargs):
      if name == "ifcopenshell":
        raise ImportError("test backend unavailable")
      return actual(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
  context = SimpleNamespace(root=tmp_path, build_path=tmp_path/"build", output=None,
                            scope={"zone": "unknown"} if case == "scope" else {}, profile="coordination")
  result = build_ifc_coordination(context)
  assert result.status == expected
  assert not (tmp_path/OUTPUT).exists()
  assert _read((tmp_path/OUTPUT).with_suffix(".validation.json"))["status"] == expected


@pytest.mark.parametrize("corruption,code", [("geometry", "IFC_GEOMETRY"), ("schema", "IFC_SCHEMA"),
                                           ("property", "IFC_PROPERTIES"), ("hierarchy", "IFC_HIERARCHY")])
def test_independent_geometry_and_schema_checks_detect_corrupted_output(tmp_path, corruption, code):
  writer = IfcWriter(ifcopenshell, "urn:test:geometry", "Geometry test")
  source = {"file": "src/test.json", "pointer": "/pump", "digest": "sha256:"+"a"*64}
  pump = writer.box("IfcPump", "pump", [0, 0, 0], [1, 2, 3], source)
  writer.properties(pump, "pump", {"State": "assumed"})
  writer.contain("pump", writer.building, [pump])
  output = tmp_path/"test.ifc"
  writer.file.write(str(output))
  assert validate_exchange(output, writer)["status"] == "pass"
  model = ifcopenshell.open(output)
  if corruption == "geometry":
    model.by_guid(pump.GlobalId).Representation.Representations[0].Items[0].Depth = 4.
  elif corruption == "schema":
    model.header.file_name.organization = ()
  elif corruption == "property":
    model.by_type("IfcPropertySingleValue")[0].NominalValue = model.create_entity("IfcText", "known")
  else:
    model.by_type("IfcRelContainedInSpatialStructure")[0].RelatingStructure = model.by_guid(writer.site.GlobalId)
  model.write(str(output))
  report = validate_exchange(output, writer)
  assert report["status"] == "fail" and any(item["code"] == code for item in report["diagnostics"])


@pytest.mark.parametrize("rotation,origin,minimum,maximum", [
  (90, (4, 5, 1), (2, 5, 1), (4, 6, 4)),
  (180, (4, 5, 1), (3, 3, 1), (4, 5, 4)),
  (270, (4, 5, 1), (4, 4, 1), (6, 5, 4)),
])
def test_exchange_respects_world_coordinates_and_rotation(tmp_path, rotation, origin, minimum, maximum):
  writer = IfcWriter(ifcopenshell, "urn:test:rotated", "Rotated equipment")
  product = writer.box("IfcPump", "rotated", minimum, maximum, {}, origin=origin, rotation=rotation)
  writer.contain("equipment", writer.building, [product])
  path = tmp_path/"rotated.ifc"
  writer.file.write(str(path))
  assert validate_exchange(path, writer)["status"] == "pass"
