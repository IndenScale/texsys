import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from preview_records import preview_record, save_record

from texsys.evidence import write_json
from texsys.project import ProjectError, initialize_project, inspect_project
from texsys.studio.preview import ProjectPreview
from texsys.studio import server
from texsys.targets import BuildContext
from texsys_hvac_equipment.project_layout import build_project_layout


@pytest.fixture
def project(tmp_path):
  root = tmp_path / "project"
  initialize_project(root)
  write_json(root / "src/system.sdl.json", {"root": "Plant", "components": {"Plant": {}}})
  write_json(root / "build/bom.json", {"entries": [{"design_instance_id": "Plant.Pump"}]})
  return root


def bom_record(project, run="one", **kwargs):
  return preview_record(project, run, (("bom", "build/bom.json", {"kind": "bom", "root": "Plant", "source_map": {
    "Plant.Pump": {"file": "src/system.sdl.json", "path": "components.Plant.instances.Pump"}}}),), **kwargs)


def test_preview_only_reads_manifest_artifacts_and_tracks_content_changes(project):
  preview = ProjectPreview(project, "build")
  assert preview.read()["artifacts"] == []
  report = bom_record(project)
  item = preview.read("Plant")["artifacts"][0]
  assert item["current"] and item["completeness"] == "complete"
  assert preview.read("AnotherRoot")["artifacts"] == []
  assert preview.artifact("one", "test:preview", "bom")["data"]["entries"][0]["design_instance_id"] == "Plant.Pump"
  source = project / "src/system.sdl.json"
  before = source.stat()
  source.write_text(source.read_text() + "\n")
  os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
  item = preview.read()["artifacts"][0]
  assert not item["current"] and item["freshness"] == "inputs-changed"
  (project / "build/bom.json").write_text('{"entries": []}')
  assert preview.artifact("one", "test:preview", "bom")["data"]["entries"]
  assert (project / report["artifacts"][0]["stored_path"]).is_file()


def test_latest_failure_falls_back_but_current_partial_is_retained(project):
  bom_record(project)
  error = {"rule": "E001", "status": "error", "message": "Unavailable input"}
  preview_record(project, "two", previous="one", status="error", diagnostics=(error,))
  preview = ProjectPreview(project, "build")
  result = preview.read()
  item = result["artifacts"][0]
  assert result["build"]["status"] == "error"
  assert item["run_id"] == "one" and item["fallback"] and not item["current"]
  assert item["latest_target"]["diagnostics"] == [error]
  bom_record(project, "three", previous="two", status="indeterminate", complete=False)
  item = preview.read()["artifacts"][0]
  assert item["run_id"] == "three" and item["current"] and not item["fallback"]
  assert item["partial"] and item["completeness"] == "partial"


@pytest.mark.parametrize("selected", [True, False])
def test_unrelated_failure_does_not_revive_successfully_removed_or_unselected_outputs(project, selected):
  bom_record(project)
  report = preview_record(project, "two", previous="one", status="error")
  report["targets"] = [{"id": "other:failed", "status": "error", "execution": "executed"}]
  if selected:
    report["targets"].append({"id": "test:preview", "status": "pass", "execution": "executed"})
  save_record(project, report)
  result = ProjectPreview(project, "build").read()
  assert result["build"]["status"] == "error"
  assert result["artifacts"] == []


def test_corrupt_current_output_can_fall_back_without_reviving_removed_sibling(project):
  previous = bom_record(project)
  previous["artifacts"].append({**previous["artifacts"][0], "id": "removed-bom"})
  save_record(project, previous)
  report = bom_record(project, "two", previous="one")
  report["targets"].append({"id": "other:failed", "status": "error", "execution": "executed"})
  report["status"] = "error"
  save_record(project, report)
  (project / report["artifacts"][0]["stored_path"]).write_text("{}")
  artifacts = ProjectPreview(project, "build").read()["artifacts"]
  assert [(item["id"], item["run_id"]) for item in artifacts] == [("bom", "one")]
  assert artifacts[0]["fallback"] and artifacts[0]["latest_target"]["status"] == "pass"


def test_tampered_artifact_falls_back_and_explicit_read_rejects_it(project):
  bom_record(project)
  report = bom_record(project, "two", previous="one")
  (project / report["artifacts"][0]["stored_path"]).write_text("{}")
  preview = ProjectPreview(project, "build")
  assert preview.read()["artifacts"][0]["run_id"] == "one"
  with pytest.raises(ProjectError, match="digest"):
    preview.artifact("two", "test:preview", "bom")


@pytest.mark.parametrize("location", ["src/system.sdl.json", "../secret.json", "/tmp/secret.json"])
def test_artifact_boundary_is_enforced_even_with_valid_record_digest(project, location):
  report = bom_record(project)
  report["artifacts"][0]["stored_path"] = location
  save_record(project, report)
  preview = ProjectPreview(project, "build")
  assert not preview.read()["artifacts"][0]["available"]
  with pytest.raises(ProjectError):
    preview.artifact("one", "test:preview", "bom")


def test_manifest_checksum_symlinks_and_unknown_identifiers_are_rejected(project):
  report = bom_record(project)
  preview = ProjectPreview(project, "build")
  with pytest.raises(ProjectError, match="missing or ambiguous"):
    preview.artifact("one", "test:preview", "unknown")
  with pytest.raises(ProjectError, match="Invalid preview build run"):
    preview.artifact("../one", "test:preview", "bom")
  stored = project / report["artifacts"][0]["stored_path"]
  stored.unlink()
  stored.symlink_to(project / "build/bom.json")
  with pytest.raises(ProjectError, match="symbolic links"):
    preview.artifact("one", "test:preview", "bom")
  report["status"] = "changed-without-checksum"
  write_json(project / "build/runs/one/build.json", report)
  with pytest.raises(ProjectError, match="manifest digest"):
    preview.artifact("one", "test:preview", "bom")


def test_preview_endpoints_require_session_and_have_no_write_operation(project, monkeypatch):
  bom_record(project)
  monkeypatch.setattr(server, "StudioServer", lambda address, handler, terminals: handler)
  runtime = SimpleNamespace(root=project, project=inspect_project(project))
  handler_type = server.create_server(runtime, frontend=project / "build", token="preview-test")

  def get(path, authorized=True):
    handler = object.__new__(handler_type)
    handler.server = SimpleNamespace(server_port=8710)
    handler.path = path
    handler.headers = {"Host": "127.0.0.1:8710", **({"X-Tex-Studio-Token": "preview-test"} if authorized else {})}
    response = []
    handler._send = lambda code, payload: response.extend([code, payload])
    handler.do_GET()
    return response

  assert get("/api/preview", False)[0] == 403
  assert get("/api/preview?root=Plant")[1]["artifacts"][0]["id"] == "bom"
  assert get("/api/preview/artifact?run=one&target=test%3Apreview&id=bom")[1]["format"] == "json"
  assert get("/api/preview/artifact?path=src/system.sdl.json")[0] == 400


def test_layout_uses_explicit_identity_and_omits_missing_geometry(tmp_path):
  root = tmp_path / "project"
  initialize_project(root)
  example = Path(__file__).parents[1] / "examples/hvac-dynamic-station"
  for file in ("src/system.sdl.json", "assets/layout.json"):
    (root / file).write_bytes((example / file).read_bytes())
  context = BuildContext(root, "hvac:project-layout", None, {}, root / "build", root / "build/scratch")
  result = build_project_layout(context)
  scene = result.artifacts[0]
  assert result.status == "pass" and scene.complete
  assert len(scene.preview["source_map"]) == 8
  assert scene.preview["source_map"]["ColdStation.CH-01"]["geometry"]["file"] == "assets/layout.json"
  layout = json.loads((root / "assets/layout.json").read_text())
  del layout["objects"]["CH-01"]
  write_json(root / "assets/layout.json", layout)
  partial = build_project_layout(context)
  assert partial.status == "indeterminate" and not partial.artifacts[0].complete
  assert partial.diagnostics[0]["objects"] == ["ColdStation.CH-01"]
  data = json.loads((root / "build/hvac/layout/layout.json").read_text())
  assert len(data["objects"]) == 7 and data["missing_objects"] == ["ColdStation.CH-01"]
  layout["objects"]["unknown"] = layout["objects"]["CT-01"]
  write_json(root / "assets/layout.json", layout)
  with pytest.raises(ValueError, match="existing SDL"):
    build_project_layout(context)
