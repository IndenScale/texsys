"""Studio contracts: input authority, isolated jobs, discovery and local HTTP access."""

import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from texsys.dependencies import add_registry_dependency, lock_project
from texsys.evidence import write_json
from texsys.project import ProjectError
from texsys.registry import publish_package
from texsys.studio import StudioPlugin, discover_plugins
from texsys.studio.runtime import Conflict, StudioRuntime, git_context, revision
from texsys.studio.server import create_server
from texsys_studio_hvac import plugin

from preview_records import preview_record, save_record

ROOT = Path(__file__).parents[1]


@pytest.fixture
def station(tmp_path):
  root = tmp_path / "station"
  shutil.copytree(ROOT / "examples/hvac-dynamic-station", root, ignore=shutil.ignore_patterns("build"))
  (root / "build").mkdir()
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))
  lock_project(root)
  return root


@pytest.fixture
def runtime(station):
  runtime = StudioRuntime(station)
  yield runtime
  runtime.close()


def load(root):
  return json.loads((root / "src/system.sdl.json").read_text())


def edit_request(runtime, value=23):
  return {"plugin": "texsys:hvac", "revision": revision(runtime.root), "operation": {
    "kind": "set-parameter", "instance": "CHWP-01", "parameter": "shutoff_head_m",
    "binding": {"value": value, "unit": "m", "state": "assumed", "source": "Studio parameter contract test"}}}


def pump_name(root):
  source = load(root)
  return next(name for name, instance in source["components"][source["root"]]["instances"].items()
              if instance["component"] == "ChilledWaterPump")


def request_for(runtime, **kwargs):
  request = edit_request(runtime, **kwargs)
  request["operation"]["instance"] = pump_name(runtime.root)
  return request


def wait_for_job(runtime, identity, timeout=60):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    job = next(job for job in runtime.state()["jobs"] if job["id"] == identity)
    if job["state"] != "running":
      return job
    time.sleep(.1)
  pytest.fail("Studio task exceeded its timeout")


def test_plugin_discovery_rejects_duplicate_and_incompatible_contributions(tmp_path):
  class Entry:
    def __init__(self, name, value):
      self.name, self.value = name, value

    def load(self):
      return self.value

  class Management(StudioPlugin):
    manifest = {**plugin.manifest, "id": "texsys:project", "name": "Project", "discipline": "项目管理"}
    frontend = Path(__file__)

  other = Management()
  discovered, errors = discover_plugins([Entry("hvac", plugin), Entry("project", other)])
  assert set(discovered) == {"texsys:hvac", "texsys:project"}
  assert errors == []
  discovered, errors = discover_plugins([Entry("a", plugin), Entry("b", plugin)])
  assert discovered == {} and "Duplicate" in errors[0]["message"]
  incompatible = Management()
  incompatible.manifest = {**other.manifest, "api_version": 99}
  discovered, errors = discover_plugins([Entry("future", incompatible)])
  assert discovered == {} and "Incompatible" in errors[0]["message"]


def test_empty_host_is_a_valid_explicit_state(station):
  runtime = StudioRuntime(station, plugins={})
  state = runtime.state()
  assert state["plugins"] == [] and state["data"] == {} and state["jobs"] == []


def test_external_build_publication_changes_studio_listing_revision(station):
  runtime = StudioRuntime(station, plugins={})
  initial = runtime.state()["project"]
  report = station / "build" / "build.json"
  write_json(report, {"run_id": "first"})
  first = runtime.state()["project"]
  assert first["build_revision"] != initial["build_revision"]
  assert first["revision"] == initial["revision"]
  assert runtime.state()["project"]["build_revision"] == first["build_revision"]

  write_json(report, {"run_id": "other"})
  second = runtime.state()["project"]
  assert second["build_revision"] != first["build_revision"]
  assert second["revision"] == first["revision"]


def test_workbench_uses_verified_named_drawings_with_legacy_job_priority(station):
  output = "build/drawings/schematics/system.json"
  sidecar = "build/drawings/schematics/system.svg"
  topology = "build/drawings/topology/system.json"
  write_json(station / output, {"view": "schematic", "title": "Named schematic"})
  write_json(station / topology, {"view": "system", "title": "Named topology"})
  (station / sidecar).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
  legacy = "build/hvac/schematic/system.json"
  write_json(station / legacy, {"view": "schematic", "title": "Legacy schematic"})
  report = preview_record(station, "named", (
    ("system-data", output, {"kind": "schematic"}),
    ("system-svg", sidecar, None),
    ("system-data", topology, {"kind": "topology"}),
  ))
  report["targets"] = [{"id": "system-schematic", "using": "hvac:system-schematic", "output": output},
                       {"id": "system-topology", "using": "hvac:system-topology", "output": topology}]
  for record in report["artifacts"]:
    record.update(published=True, target="system-topology" if record["path"] == topology else "system-schematic")
  save_record(station, report)

  state = plugin.state(station, [])
  assert state["diagrams"]["schematic"]["document"]["title"] == "Named schematic"
  assert state["diagrams"]["system"]["document"]["title"] == "Named topology"
  assert {item["path"] for item in state["exports"]} == {output, sidecar, topology}

  preview_record(station, "unrelated", previous="named")
  historical = plugin.state(station, [])
  assert historical["diagrams"]["schematic"]["document"]["title"] == "Named schematic"
  assert historical["diagrams"]["schematic"]["current"] is False

  job = station / "build/studio/job"
  job.mkdir(parents=True)
  shutil.copyfile(station / "texsys.toml", job / "texsys.toml")
  write_json(job / legacy, {"view": "schematic", "title": "Job schematic"})
  job_state = plugin.state(station, [{"root": job, "current": True}])
  assert job_state["diagrams"]["schematic"] == {
    "document": {"view": "schematic", "title": "Job schematic"}, "current": True,
  }
  assert job_state["diagrams"]["system"]["document"]["title"] == "Named topology"

  manifest = station / "texsys.toml"
  manifest.write_text(manifest.read_text().replace(output, "../outside.json"))
  report["targets"][0]["output"] = "../outside.json"
  report["artifacts"][0]["path"] = "../outside.json"
  save_record(station, report)
  rejected = plugin.state(station, [])
  assert rejected["diagrams"]["schematic"]["document"]["title"] == "Legacy schematic"
  assert "../outside.json" not in {item["path"] for item in rejected["exports"]}


def test_edit_changes_only_instance_and_undo_restores_inheritance(runtime):
  before = load(runtime.root)
  request = request_for(runtime)
  response = runtime.edit(request)
  after = load(runtime.root)
  root = after["components"][after["root"]]
  assert root["instances"][pump_name(runtime.root)]["parameters"]["shutoff_head_m"]["value"] == 23
  assert after["components"]["ChilledWaterPump"] == before["components"]["ChilledWaterPump"]
  assert revision(runtime.root) != request["revision"]
  runtime.edit({"plugin": "texsys:hvac", "revision": response["revision"], "operation": response["undo"]})
  assert load(runtime.root) == before


@pytest.mark.parametrize("mutate", [
  lambda binding: binding.update(value=-1),
  lambda binding: binding.update(unit="kW"),
  lambda binding: binding.update(value=True),
  lambda binding: binding.update(source=""),
])
def test_rejected_edit_never_mutates_source(runtime, mutate):
  before = (runtime.root / "src/system.sdl.json").read_bytes()
  request = request_for(runtime)
  mutate(request["operation"]["binding"])
  with pytest.raises((ProjectError, ValueError)):
    runtime.edit(request)
  assert (runtime.root / "src/system.sdl.json").read_bytes() == before


def test_external_edit_causes_conflict_and_preserves_external_change(runtime):
  request = request_for(runtime)
  file = runtime.root / "src/system.sdl.json"
  file.write_text(file.read_text() + "\n")
  external = file.read_bytes()
  with pytest.raises(Conflict, match="Source changed"):
    runtime.edit(request)
  assert file.read_bytes() == external


def test_unknown_parameter_state_can_be_saved_without_inventing_value(runtime):
  request = request_for(runtime)
  request["operation"]["binding"] = {"unit": "m", "state": "unknown", "source": "Awaiting manufacturer confirmation"}
  runtime.edit(request)
  source = load(runtime.root)
  binding = source["components"][source["root"]]["instances"][pump_name(runtime.root)]["parameters"]["shutoff_head_m"]
  assert binding["state"] == "unknown" and "value" not in binding


def test_background_check_uses_copy_and_marks_result_stale_after_edit(runtime):
  request = {"plugin": "texsys:hvac", "revision": revision(runtime.root), "action": "check", "options": {}}
  job = runtime.submit(request)
  with pytest.raises(Conflict):
    runtime.submit(request)
  runtime.edit(request_for(runtime, value=24))
  finished = wait_for_job(runtime, job["id"])
  assert finished["state"] == "completed" and finished["result"]["status"] == "pass"
  assert finished["current"] is False
  copied = load(runtime.directory / "jobs" / job["id"] / "project")
  assert copied != load(runtime.root)
  restarted = StudioRuntime(runtime.root)
  assert restarted.state()["jobs"][0]["id"] == job["id"]
  report = restarted.state()["data"]["texsys:hvac"]["reports"][0]
  assert report["current"] is False


def test_same_ide_executes_two_project_solver_versions_without_host_imports(tmp_path, monkeypatch):
  monkeypatch.setenv("TEXSYS_CACHE_DIR", str(tmp_path / "cache"))
  registry = tmp_path / "registry"
  projects = []
  for version in ("0.1.0", "0.1.1"):
    author = tmp_path / f"author-{version}"
    shutil.copytree(ROOT / "packages/hvac-dynamics", author,
                    ignore=shutil.ignore_patterns("build", "__pycache__", "*.egg-info"))
    for relative in ("texsys.toml", "pyproject.toml", "src/texsys_hvac_dynamics/__init__.py"):
      file = author / relative
      file.write_text(file.read_text().replace('version = "0.1.0"', f'version = "{version}"'))
    publish_package(author, registry)
    shutil.rmtree(author)
    root = tmp_path / f"project-{version}"
    shutil.copytree(ROOT / "examples/hvac-dynamic-station", root,
                    ignore=shutil.ignore_patterns("build", "texsys.toml", "texsys.lock"))
    (root / "texsys.toml").write_text(f'''schema_version = 1
[project]
name = "project-{version}"
[layout]
source = "src"
assets = "assets"
build = "build"
''')
    add_registry_dependency(root, "hvac-dynamics", version, registry)
    projects.append(root)
  snapshots = []
  for root, version in zip(projects, ("0.1.0", "0.1.1")):
    runtime = StudioRuntime(root)
    try:
      assert [item["id"] for item in runtime.state()["plugins"]] == ["texsys:hvac"]
      # Editing validates through the project's checker in another process.
      runtime.edit(request_for(runtime, value=24))
      job = runtime.submit({"plugin": "texsys:hvac", "revision": revision(root), "action": "check", "options": {}})
      finished = wait_for_job(runtime, job["id"])
      assert finished["state"] == "completed", finished
      assert finished["result"]["status"] == "pass", finished
      executed = finished["result"]["runtime"]
      assert [(item["name"], item["version"]) for item in executed] == [("hvac-dynamics", version)]
      snapshots.append(executed[0]["digest"])
    finally:
      runtime.close()
  assert snapshots[0] != snapshots[1]


def test_cancellation_and_interrupted_record_are_explicit(runtime):
  job = runtime.submit({"plugin": "texsys:hvac", "revision": revision(runtime.root), "action": "simulate", "options": {"scenario": "all"}})
  runtime.cancel(job["id"])
  assert runtime.state()["jobs"][0]["state"] == "cancelled"
  file = runtime.directory / "jobs" / job["id"] / "job.json"
  record = json.loads(file.read_text())
  record["state"] = "running"
  write_json(file, record)
  restarted = StudioRuntime(runtime.root)
  assert restarted.state()["jobs"][0]["state"] == "interrupted"


def test_failed_engineering_scenario_is_completed_execution(runtime):
  before = (runtime.root / "src/system.sdl.json").read_bytes()
  job = runtime.submit({"plugin": "texsys:hvac", "revision": revision(runtime.root), "action": "simulate", "options": {"scenario": "pump-trip"}})
  finished = wait_for_job(runtime, job["id"])
  assert finished["state"] == "completed", finished
  assert finished["result"]["status"] == "fail"
  state = runtime.state()["data"]["texsys:hvac"]
  assert state["runs"][0]["current"] is True
  assert state["runs"][0]["result"]["status"] == "fail"
  assert state["diagrams"]["schematic"]["current"] is True
  assert (runtime.root / "src/system.sdl.json").read_bytes() == before
  # A historical output from the same backend must not duplicate the current run,
  # even when backend metadata (versions/options/digests) differs.
  historical = copy.deepcopy(state["runs"][0]["result"])
  historical["backend"]["implementation_digest"] = "sha256:older-implementation"
  write_json(runtime.root / "build/tests/scenarios/pump-trip/scipy/results.json", historical)
  assert len(runtime.state()["data"]["texsys:hvac"]["runs"]) == 1


def test_latest_model_check_does_not_hide_current_drawing_review_items(station):
  build_origin = station / "build/studio/jobs/build/project"
  check_origin = station / "build/studio/jobs/check/project"
  for origin in (build_origin, check_origin):
    origin.mkdir(parents=True)
    shutil.copyfile(station / "texsys.toml", origin / "texsys.toml")
  write_json(build_origin / "build/build.json", {"status": "pass", "targets": [{
    "status": "pass", "open_items": [{"id": "visual-review", "impact": "Review required"}]}]})
  write_json(check_origin / "build/studio-check.json", {"status": "pass", "diagnostics": []})
  reports = plugin.state(station, [{"root": check_origin, "current": True}, {"root": build_origin, "current": True}])["reports"]
  assert {item["kind"] for item in reports} == {"studio-check.json", "build.json"}
  drawing = next(item for item in reports if item["kind"] == "build.json")
  assert drawing["current"] is True
  assert drawing["data"]["targets"][0]["open_items"][0]["id"] == "visual-review"


def test_local_api_requires_session_and_rejects_cross_origin_and_escape(runtime, tmp_path):
  frontend = tmp_path / "frontend"
  frontend.mkdir()
  server = create_server(runtime, port=0, frontend=frontend)
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  url = f"http://127.0.0.1:{server.server_port}"
  try:
    with urlopen(url) as response:
      page = response.read().decode()
      host_policy = response.headers["Content-Security-Policy"]
    token = re.search(r'name="tex-studio-token" content="([^"]+)"', page)[1]
    assert "script-src 'self'" in host_policy
    assert (f"frame-src {url}/api/html-preview "
            f"http://localhost:{server.server_port}/api/html-preview") in host_policy
    with urlopen(url + "/api/html-preview?content=ignored") as response:
      shell = response.read().decode()
      shell_policy = response.headers["Content-Security-Policy"]
    assert "texsys:html-preview-ready" in shell and token not in shell and "ignored" not in shell
    assert "sandbox allow-scripts" in shell_policy and "allow-same-origin" not in shell_policy
    assert "connect-src 'none'" in shell_policy and "frame-src 'none'" in shell_policy
    assert f"frame-ancestors {url} http://localhost:{server.server_port}" in shell_policy
    with pytest.raises(HTTPError) as error:
      urlopen(url + "/api/state")
    assert error.value.code == 403
    headers = {"X-Tex-Studio-Token": token}
    state = json.loads(urlopen(Request(url + "/api/state", headers=headers)).read())
    assert state["plugins"][0]["id"] == "texsys:hvac"
    files = json.loads(urlopen(Request(url + "/api/files", headers=headers)).read())
    assert "texsys.toml" in {file["path"] for file in files["files"]}
    preview = json.loads(urlopen(Request(url + "/api/file?path=texsys.toml", headers=headers)).read())
    assert preview["previewable"] is True
    assert preview["content"] == (runtime.root / "texsys.toml").read_text()
    with pytest.raises(HTTPError) as file_error:
      urlopen(url + "/api/files")
    assert file_error.value.code == 403
    with pytest.raises(HTTPError) as file_error:
      urlopen(Request(url + "/api/file?path=../outside.txt", headers=headers))
    assert file_error.value.code == 400
    with pytest.raises(HTTPError) as error:
      urlopen(Request(url + "/api/jobs", data=b"{}", headers={**headers, "Origin": "https://untrusted.example"}))
    assert error.value.code == 403
    with pytest.raises(HTTPError) as error:
      urlopen(Request(url + "/api/artifact?path=src/system.sdl.json", headers=headers))
    assert error.value.code == 404
    with pytest.raises(HTTPError) as error:
      urlopen(Request(url + "/api/jobs", data=b"[]", headers=headers))
    assert error.value.code == 400
  finally:
    server.shutdown()
    server.server_close()
    thread.join()


def test_project_git_context_preserves_checkout_identity(tmp_path):
  assert git_context(tmp_path) == {"kind": "unversioned"}
  def git(*args):
    return subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, text=True, check=True).stdout.strip()
  git("init", "--initial-branch=design")
  assert git_context(tmp_path) == {"kind": "branch", "name": "design"}
  git("-c", "user.name=Studio Test", "-c", "user.email=studio@example.invalid", "commit", "--allow-empty", "-m", "fixture")
  commit = git("rev-parse", "HEAD")
  git("checkout", "--detach", commit)
  assert git_context(tmp_path) == {"kind": "detached", "name": git("rev-parse", "--short", "HEAD")}
  assert git("rev-parse", "HEAD") == commit


def test_file_listing_includes_public_outputs_but_skips_caches_and_links(tmp_path):
  from texsys.studio.files import project_files
  root = tmp_path / "project"
  for directory in ("src", "out/nested", ".git", "node_modules/pkg"):
    (root / directory).mkdir(parents=True)
  for relative in ("README.md", "src/model.json", "out/nested/result.json", ".git/config", "node_modules/pkg/index.js"):
    (root / relative).write_text("test")
  outside = tmp_path / "outside.txt"
  outside.write_text("outside")
  (root / "link.txt").symlink_to(outside)
  listing = project_files(root, "out")
  assert {file["path"] for file in listing["files"]} == {"README.md", "src/model.json", "out/nested/result.json"}
  assert listing["truncated"] is False


def test_file_preview_enforces_scope_and_reports_binary_and_size_limits(tmp_path):
  from texsys.studio.files import project_file, MAX_PREVIEW_BYTES
  root = tmp_path / "project"
  root.mkdir()
  (root / "model.json").write_text('{"label":"冷水机组"}')
  assert project_file(root, "model.json", "build")["content"] == '{"label":"冷水机组"}'
  (root / "binary.bin").write_bytes(b"\0binary")
  assert project_file(root, "binary.bin", "build")["previewable"] is False
  (root / "large.txt").write_bytes(b"x" * (MAX_PREVIEW_BYTES + 1))
  assert project_file(root, "large.txt", "build")["previewable"] is False
  outside = tmp_path / "outside.txt"
  outside.write_text("outside")
  (root / "alias").symlink_to(outside)
  (root / "build").mkdir()
  (root / "build/result.txt").write_text("output")
  assert project_file(root, "build/result.txt", "build")["content"] == "output"
  for path in ("../outside.txt", str(outside), "alias", "build/runs/result.txt", ".git/config"):
    with pytest.raises(ProjectError):
      project_file(root, path, "build")


def test_hmr_keeps_consuming_build_output_after_startup(station, monkeypatch, capsys):
  import io
  from texsys.studio.server import serve_hmr

  drained = threading.Event()

  class Output:
    def __init__(self):
      self.stream = io.StringIO("Vite ready\nLocal: http://127.0.0.1:8710/\n" + "hmr update\n" * 10000)

    def readline(self):
      return self.stream.readline()

    def __iter__(self):
      yield from self.stream
      drained.set()

  class Process:
    stdout = Output()

    def poll(self):
      return None

    def wait(self, timeout=None):
      assert drained.wait(2), "Vite output must be drained while the development server is running"
      return 0

    def terminate(self):
      pass

  monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process())
  serve_hmr(station)
  assert capsys.readouterr().out.count("hmr update") == 10000
