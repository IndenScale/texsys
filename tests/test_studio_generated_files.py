"""Public build documents share ordinary editing; private execution storage does not."""

import json

import pytest
from preview_records import preview_record, save_record
from pxr import Usd, UsdGeom

from texsys.evidence import write_json
from texsys.project import ProjectError, initialize_project
from texsys.studio import files
from texsys.studio.files import file_operation, project_file, project_files, workspace_path
from texsys.studio.preview import ProjectPreview
from texsys.studio.search import replacement, search_project
from texsys.studio.runtime import Conflict


@pytest.fixture
def project(tmp_path):
  initialize_project(tmp_path)
  (tmp_path / "src/source.txt").write_text("SOURCE_MARKER")
  return tmp_path


def drawing(project, path="build/hvac/schematic/system.json", identifier="drawing", **kwargs):
  write_json(project / path, {"title": identifier, "nodes": []})
  return (identifier, path, {"kind": "schematic", "root": "Plant", **kwargs})


def test_explorer_discovers_public_build_files_and_hides_private_storage(project):
  diagram = drawing(project)
  outputs = [diagram]
  for path in ("build/hvac/system.svg", "build/hvac/system.drawio", "build/hvac/report.md"):
    (project / path).write_text("GENERATED_MARKER")
    outputs.append((path.rsplit("/", 1)[-1], path, None))
  for path in ("build/untracked.txt", "build/runs/leak.txt", "build/.cache/leak.txt",
               "build/dependencies/leak.txt", "build/studio/leak.txt"):
    (project / path).parent.mkdir(parents=True, exist_ok=True)
    (project / path).write_text("PRIVATE_MARKER")
    if path != "build/untracked.txt":
      outputs.append((path, path, None))
  preview_record(project, "one", outputs)
  listing = project_files(project, "build")
  generated = [entry for entry in listing["files"] if entry.get("generated")]
  assert {entry["path"] for entry in generated} == {
    diagram[1], "build/hvac/system.svg", "build/hvac/system.drawio", "build/hvac/report.md", "build/build.json", "build/untracked.txt"}
  assert all(not entry.get("readonly") for entry in generated)
  assert {"build", "build/hvac", "build/hvac/schematic"} <= set(listing["directories"])
  assert not any(directory.startswith(("build/runs", "build/.cache", "build/dependencies", "build/studio"))
                 for directory in listing["directories"])
  file = project_file(project, diagram[1], "build")
  assert not file.get("readonly") and file["generated"] and file["previewable"]
  assert json.loads(file["content"])["title"] == "drawing"
  assert file["artifact"]["path"] == diagram[1] and file["artifact"]["id"] == "drawing"
  assert project_file(project, "build/hvac/system.drawio", "build")["content"] == "GENERATED_MARKER"
  assert not project_file(project, "build/build.json", "build").get("readonly")
  assert project_file(project, "build/untracked.txt", "build")["content"] == "PRIVATE_MARKER"
  for forbidden in ("build/runs/one/files/" + diagram[1], "build/dependencies/leak.txt"):
    with pytest.raises(ProjectError):
      project_file(project, forbidden, "build")


def test_files_of_same_kind_bind_by_exact_path_and_mismatched_bytes_stay_visible(project):
  first, second = drawing(project, "build/one.json", "one"), drawing(project, "build/two.json", "two")
  preview_record(project, "one", (first, second))
  assert project_file(project, first[1], "build")["artifact"]["id"] == "one"
  assert project_file(project, second[1], "build")["artifact"]["id"] == "two"
  write_json(project / second[1], {"title": "ACTUAL EDITED FILE"})
  file = project_file(project, second[1], "build")
  assert json.loads(file["content"])["title"] == "ACTUAL EDITED FILE"
  assert "artifact" not in file and file["preview"]["kind"] == "schematic"
  assert not file.get("readonly") and file["generated"]


def test_failed_target_history_only_binds_when_canonical_bytes_match(project):
  first = drawing(project)
  preview_record(project, "one", (first,))
  preview_record(project, "two", previous="one", status="error")
  file = project_file(project, first[1], "build")
  assert file["artifact"]["run_id"] == "one" and file["artifact"]["fallback"]
  (project / first[1]).write_text("CHANGED AFTER FAILURE")
  file = project_file(project, first[1], "build")
  assert file["content"] == "CHANGED AFTER FAILURE" and "artifact" not in file
  assert file["preview"]["kind"] == "schematic"


def test_successful_target_does_not_associate_older_bytes_with_new_artifact(project):
  first = drawing(project)
  old_bytes = (project / first[1]).read_bytes()
  preview_record(project, "one", (first,))
  write_json(project / first[1], {"title": "new"})
  preview_record(project, "two", (first,), previous="one")
  (project / first[1]).write_bytes(old_bytes)
  file = project_file(project, first[1], "build")
  assert json.loads(file["content"])["title"] == "drawing"
  assert "artifact" not in file and file["preview"]["kind"] == "schematic"


def test_public_outputs_save_rename_delete_and_replace_without_changing_snapshots(project):
  item = drawing(project)
  report = preview_record(project, "one", (item,))
  snapshot = project / report["artifacts"][0]["stored_path"]
  original = snapshot.read_bytes()
  file = project_file(project, item[1], "build")
  updated = file_operation(project, "build", {"action": "save", "path": item[1], "version": file["version"],
                                             "content": '{"title":"changed drawing","nodes":[]}'})
  assert updated["preview"]["kind"] == "schematic" and "artifact" not in updated
  with pytest.raises(Conflict):
    file_operation(project, "build", {"action": "save", "path": item[1], "version": file["version"], "content": "lost"})
  file_operation(project, "build", {"action": "rename", "path": item[1], "target": "build/renamed.json", "version": updated["version"]})
  assert "build/renamed.json" in {entry["path"] for entry in project_files(project, "build")["files"]}
  assert search_project(project, "build", "changed drawing", include="build/**")["matches"][0]["path"] == "build/renamed.json"
  request = {"query": "changed drawing", "replacement": "replaced", "paths": ["build/renamed.json"]}
  plan = replacement(project, "build", request)
  applied = replacement(project, "build", {**request, "apply": True, "snapshot": plan["snapshot"]})
  assert applied["complete"] and applied["applied"] == ["build/renamed.json"]
  renamed = project_file(project, "build/renamed.json", "build")
  assert json.loads(renamed["content"])["title"] == "replaced"
  file_operation(project, "build", {"action": "delete", "path": "build/renamed.json", "version": renamed["version"]})
  assert not (project / "build/renamed.json").exists()
  assert snapshot.read_bytes() == original


def test_unregistered_public_files_and_empty_directories_use_normal_operations(project):
  file_operation(project, "build", {"action": "mkdir", "path": "build/notes"})
  file_operation(project, "build", {"action": "create_text", "path": "build/notes/new.txt", "content": "new file"})
  document = project_file(project, "build/notes/new.txt", "build")
  assert document["content"] == "new file" and not document.get("readonly")
  file_operation(project, "build", {"action": "delete", "path": "build/notes/new.txt", "version": document["version"]})
  file_operation(project, "build", {"action": "rename", "path": "build/notes", "target": "build/renamed"})
  file_operation(project, "build", {"action": "delete", "path": "build/renamed"})


@pytest.mark.parametrize("path", ["build", "build/runs", "build/runs/run/result.txt", "build/dependencies/package.txt",
                                  "build/studio/session.txt", "build/.cache/result.txt", "build/public/.private.txt"])
def test_private_build_paths_reject_all_file_operations(project, path):
  for action in ("create", "create_text", "save", "rename", "delete", "mkdir"):
    with pytest.raises(ProjectError):
      file_operation(project, "build", {"action": action, "path": path, "target": "build/public.txt", "content": "bad", "version": ""})
  with pytest.raises(ProjectError):
    workspace_path(project, path, "build", missing=True)


def test_binary_usd_has_scene_identity_without_text_content(project):
  file = project / "build/scene.usdc"
  stage = Usd.Stage.CreateNew(str(file))
  UsdGeom.Cube.Define(stage, "/Cube")
  stage.Save()
  preview_record(project, "one", (("scene", "build/scene.usdc", {"kind": "scene", "root": "Plant"}),))
  document = project_file(project, "build/scene.usdc", "build")
  assert not document["previewable"] and "content" not in document
  assert document["preview"]["kind"] == "scene" and not document.get("readonly")


def test_symlink_and_outside_build_declarations_do_not_expand_file_access(project):
  item = drawing(project)
  report = preview_record(project, "one", (item,))
  (project / item[1]).unlink()
  (project / item[1]).symlink_to(project / "src/source.txt")
  assert item[1] not in {entry["path"] for entry in project_files(project, "build")["files"]}
  with pytest.raises(ProjectError):
    project_file(project, item[1], "build")
  report["artifacts"][0]["path"] = "src/source.txt"
  save_record(project, report)
  source = project_file(project, "src/source.txt", "build")
  assert not source.get("generated") and "artifact" not in source


def test_bad_manifest_does_not_hide_or_disable_public_files(project):
  item = drawing(project)
  preview_record(project, "one", (item,))
  (project / "build/build.json").write_text("{ invalid manifest")
  document = project_file(project, "build/build.json", "build")
  assert document["content"] == "{ invalid manifest" and not document.get("readonly")
  assert json.loads(project_file(project, item[1], "build")["content"])["title"] == "drawing"


def test_file_loading_never_checks_engineering_freshness_or_unrelated_snapshots(project, monkeypatch):
  first, second = drawing(project, "build/one.json", "one"), drawing(project, "build/two.json", "two")
  plain = "build/system-check.json"
  (project / plain).write_text('{"status":"pass"}')
  report = preview_record(project, "one", (first, second, ("check", plain, None)))

  def unexpected_freshness(*_):
    pytest.fail("File access must not run project engineering applicability checks")

  monkeypatch.setattr("texsys.studio.preview._freshness", unexpected_freshness)
  location = ProjectPreview._location
  checked = []

  def checked_location(self, build, record):
    checked.append(record["id"])
    return location(self, build, record)

  monkeypatch.setattr(ProjectPreview, "_location", checked_location)
  assert project_file(project, plain, "build")["content"] == '{"status":"pass"}'
  assert project_file(project, "build/build.json", "build")["previewable"]
  assert checked == []
  document = project_file(project, first[1], "build")
  assert checked == ["one"]
  assert document["artifact"]["current"] is False
  assert document["artifact"]["freshness"] == "unchecked"

  # Ordinary outputs need no immutable snapshot to be opened or listed.
  (project / report["artifacts"][2]["stored_path"]).unlink()
  listing = project_files(project, "build")
  assert plain in {entry["path"] for entry in listing["files"]}
  assert "check" not in checked
  assert all(entry["artifact"]["freshness"] == "unchecked"
             for entry in listing["files"] if "artifact" in entry)


def test_scoped_metadata_keeps_cross_path_supersession_and_invalid_snapshot_fallback(project):
  old = drawing(project, "build/old.json", "drawing")
  preview_record(project, "one", (old,))
  new = drawing(project, "build/new.json", "drawing")
  report = preview_record(project, "two", (new,), previous="one", status="error")
  assert "artifact" not in project_file(project, old[1], "build")
  assert project_file(project, new[1], "build")["artifact"]["run_id"] == "two"

  # A failed target with a broken new snapshot may still expose its old valid
  # publication, but only on the original path and with matching current bytes.
  (project / report["artifacts"][0]["stored_path"]).write_text("corrupt")
  previous = project_file(project, old[1], "build")["artifact"]
  assert previous["run_id"] == "one" and previous["fallback"]
  assert previous["freshness"] == "historical" and previous["current"] is False
  assert "artifact" not in project_file(project, new[1], "build")


def test_file_metadata_tolerates_invalid_artifact_identity(project):
  item = drawing(project)
  report = preview_record(project, "one", (item,))
  report["artifacts"][0]["id"] = ["invalid"]
  save_record(project, report)
  document = project_file(project, item[1], "build")
  assert json.loads(document["content"])["title"] == "drawing"
  assert "artifact" not in document


def test_listing_does_not_follow_directory_aliases_and_hides_nested_private_builds(project):
  output = "outputs/generated"
  for path in ("outputs/generated/public.txt", "outputs/generated/runs/private.txt",
               "outputs/generated/.cache/private.txt", "src/nested/source.txt"):
    file = project / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(path)
  (project / "alias").symlink_to(project / "src", target_is_directory=True)
  (project / "src/alias").symlink_to(project / output, target_is_directory=True)
  result = project_files(project, output)
  paths = {entry["path"] for entry in result["files"]}
  assert "outputs/generated/public.txt" in paths and "src/nested/source.txt" in paths
  assert not any("alias" in path or "private.txt" in path for path in paths)
  assert not any("alias" in path or path.endswith(("runs", ".cache")) for path in result["directories"])


def test_missing_canonical_file_is_not_replaced_with_snapshot_bytes(project):
  item = drawing(project)
  preview_record(project, "one", (item,))
  (project / item[1]).unlink()
  assert item[1] not in {entry["path"] for entry in project_files(project, "build")["files"]}
  with pytest.raises(ProjectError):
    project_file(project, item[1], "build")


def test_generated_parent_directories_respect_explorer_limit(project, monkeypatch):
  (project / "empty").mkdir()
  preview_record(project, "one", (drawing(project),))
  monkeypatch.setattr(files, "MAX_FILES", 5)
  listing = project_files(project, "build")
  assert listing["truncated"]
  assert len(listing["files"]) <= 5 and len(listing["directories"]) <= 5


def test_text_format_edit_uses_the_ordinary_versioned_save(project):
  path = "src/source.txt"
  source = project / path
  source.write_bytes(b"first\r\nsecond\n")
  file = project_file(project, path, "build")
  assert file["textFormat"] == {"encoding": "utf8", "lineEnding": "mixed"}
  assert file["content"] == "first\nsecond\n"

  encoded = file_operation(project, "build", {
    "action": "save", "path": path, "content": file["content"], "version": file["version"], "encoding": "utf8bom",
  })
  assert source.read_bytes() == b"\xef\xbb\xbffirst\r\nsecond\n"
  assert encoded["textFormat"] == {"encoding": "utf8bom", "lineEnding": "mixed"}

  change = {"action": "save", "path": path, "content": encoded["content"], "lineEnding": "LF"}
  with pytest.raises(Conflict):
    file_operation(project, "build", {**change, "version": file["version"]})
  with pytest.raises(ProjectError):
    file_operation(project, "build", {**change, "version": encoded["version"], "lineEnding": "mixed"})
  saved = file_operation(project, "build", {**change, "version": encoded["version"]})
  assert saved["textFormat"] == {"encoding": "utf8bom", "lineEnding": "LF"}
  assert source.read_bytes() == b"\xef\xbb\xbffirst\nsecond\n"

  copy = file_operation(project, "build", {
    "action": "create_text", "path": "src/copy.txt", "content": saved["content"],
    "lineEnding": "CRLF", "encoding": "utf8",
  })
  assert copy["textFormat"] == {"encoding": "utf8", "lineEnding": "CRLF"}
  assert (project / "src/copy.txt").read_bytes() == b"first\r\nsecond\r\n"
