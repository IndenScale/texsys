from pathlib import Path

from texsys import build_records
from texsys.evidence import write_json
from texsys.execution import build_project
from texsys.studio.preview import ProjectPreview
from texsys.targets import Artifact, TargetDefinition, TargetResult

from test_targets import FakePackage, copied_project, read_build


def test_failed_rebuild_preserves_open_artifact_and_publishes_independent_branch(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  asset = root / "assets/revision.txt"
  asset.write_text("first")
  public = root / "build/scene.usda"

  def scene(context):
    revision = asset.read_text()
    path = context.build_path / "scene.usda"
    path.write_text(revision)
    if revision == "crash":
      assert public.read_text() == "first"
      raise ValueError("scene generator failed")
    complete = revision != "draft"
    preview = {"kind": "scene", "source_map": {revision: {"file": "src/system.sdl.json"}}}
    return TargetResult("pass", (Artifact("scene", "build/scene.usda", complete=complete, preview=preview),))

  def plan(context):
    write_json(context.build_path / "plan.json", {"revision": asset.read_text()})
    return TargetResult("fail", (Artifact("plan", "build/plan.json"),))

  definitions = (TargetDefinition("demo:scene", "fake", "1", scene),
                 TargetDefinition("demo:plan", "fake", "1", plan))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (FakePackage(definitions),))
  first = read_build(build_project(root, ("demo:scene",)))
  first_record = first["artifacts"][0]
  assert first_record["published"] is True
  assert public.read_text() == "first"

  asset.write_text("draft")
  draft = read_build(build_project(root, ("demo:scene",)))["artifacts"][0]
  assert draft["published"] is False and draft["partial"] is True
  assert (root / draft["stored_path"]).read_text() == "draft"
  assert public.read_text() == "first"
  entry = ProjectPreview(root, "build").file_entries("build/scene.usda")[0]
  assert list(entry["preview"]["source_map"]) == ["first"]
  assert entry["artifact"]["run_id"] == first["run_id"]

  asset.write_text("crash")
  failure = build_project(root, ("demo:scene", "demo:plan"))
  assert failure.status == "error"
  assert read_build(failure)["artifacts"][0]["published"] is True
  assert failure.items == (root / "build/plan.json",)
  assert public.read_text() == "first"
  assert (root / first_record["stored_path"]).read_text() == "first"
  assert list(public.parent.glob(".stage-*")) == []


def test_input_drift_does_not_restore_missing_public_artifact_from_cache(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  revision = root / "assets/revision.txt"
  revision.write_text("first")
  public = root / "build/scene.usda"

  def scene(context):
    (context.build_path / "scene.usda").write_text("published scene")
    return TargetResult("pass", (Artifact("scene", "build/scene.usda"),))

  def drift(context):
    assert not public.exists()
    revision.write_text("changed during build")
    return TargetResult("pass")

  definitions = (TargetDefinition("demo:scene", "fake", "1", scene),
                 TargetDefinition("demo:drift", "fake", "1", drift, dependencies=("demo:scene",)))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (FakePackage(definitions),))
  assert build_project(root, ("demo:scene",)).status == "pass"
  public.unlink()

  report = read_build(build_project(root, ("demo:scene", "demo:drift")))
  assert report["targets"][0]["execution"] == "cached"
  assert report["inputs_consistent"] is False
  assert report["artifacts"][0]["published"] is False
  assert not public.exists()


def test_publication_error_rolls_back_files_and_withholds_dependent_outputs(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  revision = root / "assets/revision.txt"
  revision.write_text("first")
  public = root / "build"

  def scene(context):
    value = revision.read_text()
    (context.build_path / "layer.usda").write_text(value)
    (context.build_path / "scene.usda").write_text(value)
    return TargetResult("pass", (Artifact("layer", "build/layer.usda"), Artifact("scene", "build/scene.usda")))

  def derived(context):
    value = (context.build_path / "scene.usda").read_text()
    (context.build_path / "derived.svg").write_text(value)
    return TargetResult("pass", (Artifact("derived", "build/derived.svg"),))

  definitions = (TargetDefinition("demo:scene", "fake", "1", scene),
                 TargetDefinition("demo:derived", "fake", "1", derived, dependencies=("demo:scene",)))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (FakePackage(definitions),))
  assert build_project(root, ("demo:derived",)).status == "pass"
  revision.write_text("second")
  replace = build_records.os.replace

  def fail_scene(source, destination):
    if Path(destination) == public / "scene.usda" and Path(source).name == "new":
      raise OSError("simulated artifact install failure")
    return replace(source, destination)

  monkeypatch.setattr(build_records.os, "replace", fail_scene)
  report = read_build(build_project(root, ("demo:derived",)))
  assert report["status"] == "error"
  assert report["targets"][0]["diagnostics"][0]["rule"] == "E_PUBLISH"
  assert report["targets"][1]["diagnostics"][0]["rule"] == "E_PUBLISH_BLOCKED"
  assert all(not record["published"] for record in report["artifacts"])
  assert [(public / name).read_text() for name in ("layer.usda", "scene.usda", "derived.svg")] == ["first"] * 3


def test_named_output_is_reserved_even_for_a_separate_legacy_build(tmp_path, monkeypatch):
  root = copied_project(tmp_path)
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text() + '''
[build.targets.floor-plan]
using = "demo:named"
output = "build/plan.svg"
''')

  def named(context):
    (context.build_path / context.output).write_text("named plan")
    return TargetResult("pass", (Artifact("plan", "build/plan.svg"),))

  def legacy(context):
    (context.build_path / "plan.svg").write_text("conflicting legacy plan")
    return TargetResult("pass", (Artifact("plan", "build/plan.svg"),))

  definitions = (TargetDefinition("demo:legacy", "fake", "1", legacy),
                 TargetDefinition("demo:named", "fake", "1", named, named_output=True))
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (FakePackage(definitions),))
  assert build_project(root, ("floor-plan",)).status == "pass"
  result = build_project(root, ("demo:legacy",))
  assert result.status == "error"
  assert "conflicts with another artifact" in read_build(result)["targets"][0]["diagnostics"][0]["message"]
  assert (root / "build/plan.svg").read_text() == "named plan"
