"""Shared editor and Git boundaries, tested against real files and temporary repositories."""

import subprocess

import pytest

from texsys.project import ProjectError, initialize_project, inspect_project
from texsys.studio.files import (file_operation, project_file, project_files, search_project,
                                 MAX_PREVIEW_BYTES)
from texsys.studio.runtime import Conflict
from texsys.studio.server import run_terminal_command
from texsys.studio.source_control import (source_branches, source_diff, source_operation, source_status,
                                         switch_source_branch)


def git(root, *args):
  return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
  git(tmp_path, "init", "-b", "main")
  git(tmp_path, "config", "user.name", "Studio Test")
  git(tmp_path, "config", "user.email", "studio@example.invalid")
  (tmp_path / ".gitignore").write_text("build/\n")
  (tmp_path / "model.txt").write_text("original\n")
  git(tmp_path, "add", ".")
  git(tmp_path, "commit", "-m", "baseline")
  return tmp_path


def operation(root, action, **kwargs):
  return source_operation(root, "build", {"action": action, "snapshot": source_status(root, "build")["snapshot"], **kwargs})


def test_terminal_preserves_directory_and_git_add_uses_that_directory(repo):
  repo = repo.resolve()
  project = repo / "nested project"
  project.mkdir()
  (project / "local.txt").write_text("project change\n")
  (repo / "model.txt").write_text("repository change\n")

  stage = run_terminal_command(project, {"command": "git add ."})
  assert stage["code"] == 0 and stage["cwd"] == str(project)
  assert git(repo, "diff", "--cached", "--name-only").splitlines() == [b"nested project/local.txt"]
  assert [entry["path"] for entry in source_status(project, "build")["entries"]] == ["local.txt"]

  parent = run_terminal_command(project, {"command": "cd ..", "cwd": stage["cwd"]})
  assert parent["code"] == 0 and parent["cwd"] == str(repo)
  current = run_terminal_command(project, {"command": "pwd", "cwd": parent["cwd"]})
  assert current["stdout"].strip() == str(repo)
  git_status = run_terminal_command(project, {"command": "git status --short", "cwd": parent["cwd"]})
  assert "M model.txt" in git_status["stdout"] and "A  " in git_status["stdout"]

  blocked = run_terminal_command(project, {"command": "cd ..", "cwd": parent["cwd"]})
  assert blocked["code"] == 1 and blocked["directoryBlocked"] and blocked["cwd"] == str(repo)
  returned = run_terminal_command(project, {"command": "cd -", "cwd": parent["cwd"], "previousCwd": stage["cwd"]})
  assert returned["code"] == 0 and returned["cwd"] == str(project)


def test_edit_is_atomic_preserves_bom_and_rejects_external_overwrite(tmp_path):
  file = tmp_path / "model.txt"
  file.write_bytes(b"\xef\xbb\xbffirst\r\n")
  baseline = project_file(tmp_path, "model.txt", "build")
  saved = file_operation(tmp_path, "build", {"action": "save", "path": "model.txt", "version": baseline["version"], "content": "second\r\n"})
  assert file.read_bytes() == b"\xef\xbb\xbfsecond\r\n"
  assert saved["version"] != baseline["version"]
  file.write_text("external\n")
  with pytest.raises(Conflict):
    file_operation(tmp_path, "build", {"action": "save", "path": "model.txt", "version": saved["version"], "content": "lost"})
  assert file.read_text() == "external\n"
  assert not list(tmp_path.glob(".studio-*"))


def test_file_operations_create_rename_delete_and_empty_directories(tmp_path):
  file_operation(tmp_path, "build", {"action": "mkdir", "path": "src"})
  assert project_files(tmp_path, "build")["directories"] == ["src"]
  file_operation(tmp_path, "build", {"action": "create", "path": "src/new.txt"})
  with pytest.raises(Conflict):
    file_operation(tmp_path, "build", {"action": "create", "path": "src/new.txt"})
  baseline = project_file(tmp_path, "src/new.txt", "build")
  file_operation(tmp_path, "build", {"action": "rename", "path": "src/new.txt", "target": "src/renamed.txt", "version": baseline["version"]})
  with pytest.raises(ProjectError, match="空文件夹"):
    file_operation(tmp_path, "build", {"action": "delete", "path": "src"})
  file_operation(tmp_path, "build", {"action": "delete", "path": "src/renamed.txt", "version": baseline["version"]})
  file_operation(tmp_path, "build", {"action": "delete", "path": "src"})
  assert not (tmp_path / "src").exists()


def test_save_as_creates_complete_text_and_leaves_existing_files_intact(tmp_path):
  destination = tmp_path / "copy.txt"
  request = {"action": "create_text", "path": destination.name, "content": "示例\r\nsecond line"}
  saved = file_operation(tmp_path, "build", request)
  assert saved["content"] == "示例\nsecond line"
  assert saved["version"] == project_file(tmp_path, destination.name, "build")["version"]
  with pytest.raises(Conflict):
    file_operation(tmp_path, "build", {**request, "content": "overwrite"})
  assert destination.read_bytes() == "示例\nsecond line".encode()
  with pytest.raises(ProjectError):
    file_operation(tmp_path, "build", {**request, "path": "too-large.txt", "content": "x" * (MAX_PREVIEW_BYTES + 1)})
  assert not (tmp_path / "too-large.txt").exists()
  with pytest.raises(ProjectError):
    file_operation(tmp_path, "build", {**request, "path": "../outside.txt"})
  assert not list(tmp_path.glob(".studio-*"))


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", "build/runs/output", ".git/config", "node_modules/package", "."])
def test_mutations_cannot_escape_project_or_touch_internal_outputs(tmp_path, path):
  with pytest.raises(ProjectError):
    file_operation(tmp_path, "build", {"action": "create", "path": path})


def test_internal_and_external_symlink_aliases_cannot_be_read_or_written(tmp_path):
  (tmp_path / "source.txt").write_text("safe")
  (tmp_path / "alias").symlink_to(tmp_path / "source.txt")
  with pytest.raises(ProjectError):
    project_file(tmp_path, "alias", "build")
  with pytest.raises(ProjectError):
    file_operation(tmp_path, "build", {"action": "save", "path": "alias", "version": "", "content": "bad"})
  assert (tmp_path / "source.txt").read_text() == "safe"


def test_literal_search_offsets_case_filter_and_skips(tmp_path):
  (tmp_path / "src").mkdir()
  (tmp_path / "src/model.txt").write_text("😀 Pump pump\na+b.a+b\n")
  (tmp_path / "other.txt").write_text("PUMP")
  (tmp_path / "binary.bin").write_bytes(b"pump\0")
  (tmp_path / "large.txt").write_bytes(b"x" * (MAX_PREVIEW_BYTES + 1))
  result = search_project(tmp_path, "build", "pump", include="src/")
  assert [(item["line"], item["column"]) for item in result["matches"]] == [(1, 3), (1, 8)]
  assert len(search_project(tmp_path, "build", "Pump", case_sensitive=True)["matches"]) == 1
  assert len(search_project(tmp_path, "build", "a+b")["matches"]) == 2
  assert search_project(tmp_path, "build", "pump")["skipped"] == 2


def test_search_limits_are_explicit(tmp_path, monkeypatch):
  import texsys.studio.files as files
  monkeypatch.setattr(files, "MAX_SEARCH_MATCHES", 2)
  (tmp_path / "text").write_text("x x x x")
  result = search_project(tmp_path, "build", "x")
  assert len(result["matches"]) == 2 and result["truncated"]


def test_git_working_and_index_diffs_stage_unstage_and_commit(repo):
  (repo / "model.txt").write_text("staged\n")
  status = operation(repo, "stage", path="model.txt")
  assert status["entries"][0]["index"] == "M"
  (repo / "model.txt").write_text("working\n")
  assert "+staged" in source_diff(repo, "build", "model.txt", True)["content"]
  assert "+working" in source_diff(repo, "build", "model.txt", False)["content"]
  operation(repo, "commit", message="only staged content")
  assert git(repo, "show", "HEAD:model.txt") == b"staged\n"
  assert (repo / "model.txt").read_text() == "working\n"
  operation(repo, "stage", path="model.txt")
  operation(repo, "unstage", path="model.txt")
  assert source_status(repo, "build")["entries"][0]["index"] == " "


def test_git_detects_same_status_content_changes_and_rejects_stale_actions(repo):
  file = repo / "model.txt"
  file.write_text("first\n")
  baseline = source_status(repo, "build")
  file.write_text("second\n")
  with pytest.raises(Conflict):
    source_operation(repo, "build", {"action": "stage", "path": "model.txt", "snapshot": baseline["snapshot"]})
  assert git(repo, "diff", "--cached") == b""


def test_nested_project_does_not_show_or_commit_other_project_changes(repo):
  project = repo / "project"
  project.mkdir()
  (project / "source").write_text("new")
  (repo / "model.txt").write_text("outside")
  git(repo, "add", "model.txt")
  status = source_status(project, "build")
  assert status["scope"] == "project" and [entry["path"] for entry in status["entries"]] == ["source"]
  operation(project, "stage", path="source")
  with pytest.raises(ProjectError, match="项目以外"):
    operation(project, "commit", message="must not include outside")
  assert git(repo, "log", "-1", "--format=%s").strip() == b"baseline"
  git(repo, "reset", "HEAD", "--", "model.txt")
  operation(project, "commit", message="project only")
  assert git(repo, "show", "--format=", "--name-only", "HEAD").strip() == b"project/source"


def test_git_unborn_repo_can_stage_and_unstage_without_deleting_file(tmp_path):
  assert not source_status(tmp_path, "build")["available"]
  operation(tmp_path, "init")
  (tmp_path / "new.txt").write_text("keep me")
  operation(tmp_path, "stage", path="new.txt")
  operation(tmp_path, "unstage", path="new.txt")
  assert (tmp_path / "new.txt").read_text() == "keep me"
  assert source_status(tmp_path, "build")["entries"][0]["index"] == "?"


def test_git_renames_deletions_and_literal_pathspecs(repo):
  name = ":(glob)* [设备].txt"
  (repo / name).write_text("new\n")
  operation(repo, "stage", path=name)
  assert name in source_diff(repo, "build", name, True)["content"] or "new" in source_diff(repo, "build", name, True)["content"]
  operation(repo, "commit", message="literal filename")
  git(repo, "mv", "model.txt", "renamed.txt")
  record = next(entry for entry in source_status(repo, "build")["entries"] if entry["path"] == "renamed.txt")
  assert record["original"] == "model.txt"
  assert "original" in source_diff(repo, "build", "renamed.txt", True)["content"]
  operation(repo, "unstage", path="renamed.txt")
  assert (repo / "renamed.txt").exists()
  operation(repo, "stage", path="model.txt")
  assert next(entry for entry in source_status(repo, "build")["entries"] if entry["path"] == "model.txt")["index"] == "D"


def test_git_build_outputs_and_symlinks_are_not_actionable(repo):
  (repo / "build").mkdir()
  (repo / "build/generated.txt").write_text("generated")
  (repo / "alias").symlink_to(repo / "model.txt")
  assert source_status(repo, "build")["entries"] == []
  with pytest.raises(ProjectError):
    operation(repo, "stage", path="alias")


def test_unborn_unstage_preserves_unsaved_worktree_content(tmp_path):
  operation(tmp_path, "init")
  file = tmp_path / "new.txt"
  file.write_text("index")
  operation(tmp_path, "stage", path="new.txt")
  file.write_text("working")
  operation(tmp_path, "unstage", path="new.txt")
  assert file.read_text() == "working"


def test_common_apis_work_without_professional_plugins_and_require_authorization(tmp_path):
  import json
  import re
  import threading
  from urllib.error import HTTPError
  from urllib.request import Request, urlopen
  from texsys.studio.runtime import StudioRuntime
  from texsys.studio.server import create_server
  initialize_project(tmp_path)
  runtime = StudioRuntime(tmp_path, plugins={})
  server = create_server(runtime, port=0, frontend=tmp_path / "build/frontend")
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  url = f"http://127.0.0.1:{server.server_port}"
  page = urlopen(url).read().decode()
  token = re.search(r'name="tex-studio-token" content="([^"]+)"', page)[1]
  def request(path, body=None, headers=None):
    return json.loads(urlopen(Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers if headers is not None else {"X-Tex-Studio-Token": token})).read())
  try:
    assert request("/api/state")["plugins"] == []
    request("/api/file", {"action": "create", "path": "src/note.txt"})
    file = request("/api/file?path=src/note.txt")
    saved = request("/api/file", {"action": "save", "path": "src/note.txt", "version": file["version"], "content": "Pump"})
    assert saved["content"] == "Pump"
    assert request("/api/search?q=pump")["matches"][0]["path"] == "src/note.txt"
    assert not request("/api/scm")["available"]
    request("/api/scm", {"action": "init"})
    assert request("/api/scm")["available"]
    for path, body in [("/api/search?q=pump", None), ("/api/scm", None), ("/api/scm/diff?path=src/note.txt", None),
                       ("/api/file", {"action": "delete", "path": "src/note.txt", "version": saved["version"]}),
                       ("/api/scm", {"action": "stage", "path": "src/note.txt"})]:
      with pytest.raises(HTTPError) as error:
        request(path, body, headers={})
      assert error.value.code == 403
    with pytest.raises(HTTPError) as error:
      request("/api/file", {"action": "save", "path": "src/note.txt", "version": file["version"], "content": "overwrite"})
    assert error.value.code == 409
    assert (tmp_path / "src/note.txt").read_text() == "Pump"
  finally:
    server.shutdown()
    server.server_close()
    thread.join()
    runtime.close()


def test_editor_normalizes_display_newlines_but_preserves_file_format(tmp_path):
  path = tmp_path / "windows.txt"
  path.write_bytes(b"one\r\ntwo\r\n")
  file = project_file(tmp_path, "windows.txt", "build")
  assert file["content"] == "one\ntwo\n"
  file_operation(tmp_path, "build", {"action": "save", "path": "windows.txt", "version": file["version"], "content": "one\nchanged\n"})
  assert path.read_bytes() == b"one\r\nchanged\r\n"


def test_binary_files_can_be_renamed_and_deleted_without_text_editing(tmp_path):
  (tmp_path / "image.bin").write_bytes(b"\xff\0\xfe")
  file = project_file(tmp_path, "image.bin", "build")
  assert not file["previewable"] and file["version"]
  file_operation(tmp_path, "build", {"action": "rename", "path": "image.bin", "target": "renamed.bin", "version": file["version"]})
  file_operation(tmp_path, "build", {"action": "delete", "path": "renamed.bin", "version": file["version"]})
  assert not (tmp_path / "renamed.bin").exists()


def test_git_diff_is_bounded_and_explicitly_truncated(repo):
  (repo / "model.txt").write_bytes(b"a" * (MAX_PREVIEW_BYTES + 100))
  diff = source_diff(repo, "build", "model.txt")
  assert diff["truncated"] and len(diff["content"].encode()) <= MAX_PREVIEW_BYTES


def test_bulk_git_operations_are_project_scoped(repo):
  project = repo / "project"
  project.mkdir()
  (project / "a").write_text("a")
  (project / "b").write_text("b")
  (repo / "model.txt").write_text("outside")
  operation(project, "stage_all")
  assert git(repo, "diff", "--cached", "--name-only").splitlines() == [b"project/a", b"project/b"]
  operation(project, "unstage_all")
  assert git(repo, "diff", "--cached") == b""
  assert (project / "a").read_text() == "a"


def test_discard_preserves_index_and_deletes_only_selected_untracked_file(repo):
  file = repo / "model.txt"
  file.write_text("staged")
  operation(repo, "stage", path="model.txt")
  file.write_text("working")
  operation(repo, "discard", path="model.txt")
  assert file.read_text() == "staged"
  assert git(repo, "show", ":model.txt") == b"staged"
  (repo / "new").write_text("remove")
  (repo / "keep").write_text("keep")
  operation(repo, "discard", path="new")
  assert not (repo / "new").exists() and (repo / "keep").read_text() == "keep"
  file.unlink()
  operation(repo, "discard", path="model.txt")
  assert file.read_text() == "staged"


def test_history_topology_refs_limit_and_scoped_commit_diff(repo):
  from texsys.studio.source_control import source_history, source_commit_diff
  git(repo, "checkout", "-b", "feature")
  (repo / "feature").write_text("feature")
  git(repo, "add", "feature")
  git(repo, "commit", "-m", "设备 feature")
  git(repo, "checkout", "main")
  (repo / "main").write_text("main")
  git(repo, "add", "main")
  git(repo, "commit", "-m", "main work")
  git(repo, "merge", "--no-ff", "feature", "-m", "merge")
  history = source_history(repo)
  assert len(history["commits"]) == 4
  head = history["commits"][0]
  assert len(head["parents"]) == 2 and "HEAD -> main" in head["refs"]
  assert head["author"] == "Studio Test" and head["date"]
  assert source_history(repo, 1)["truncated"]
  assert "feature" in source_commit_diff(repo, head["id"])["content"]
  project = repo / "nested"
  project.mkdir()
  assert "diff --git" not in source_commit_diff(project, head["id"])["content"]
  with pytest.raises(ProjectError):
    source_commit_diff(repo, "--all")
  with pytest.raises(ProjectError):
    source_history(repo, 999999)


def branch_choice(root, name):
  listing = source_branches(root)
  target = next(item for item in listing["refs"] if item["name"] == name)
  return {"action": "checkout", "ref": target["ref"], "commit": target["commit"], "head": listing["head"]}


def test_switch_local_branch_in_parent_repo_preserves_git_changes(repo):
  root = repo / "nested project"
  initialize_project(root)
  git(repo, "add", "nested project")
  git(repo, "commit", "-m", "add project")
  project = inspect_project(root)
  git(repo, "switch", "-c", "feature")
  (repo / "model.txt").write_text("branch content\n")
  git(repo, "commit", "-am", "change outside the project")
  git(repo, "switch", "main")
  git(repo, "branch", "same-commit")
  assert [item["name"] for item in source_branches(root)["refs"] if item["current"]] == ["main"]

  choice = branch_choice(root, "feature")
  old_snapshot = source_status(root, project.build)["snapshot"]
  switch_source_branch(project, branch_choice(root, "same-commit"))
  assert source_status(root, project.build)["snapshot"] != old_snapshot
  with pytest.raises(Conflict, match="当前 Git 分支"):
    switch_source_branch(project, choice)

  choice = branch_choice(root, "feature")
  (repo / "model.txt").write_text("uncommitted content\n")
  with pytest.raises(ProjectError, match="would be overwritten"):
    switch_source_branch(project, choice)
  assert (repo / "model.txt").read_text() == "uncommitted content\n"
  assert git(repo, "branch", "--show-current").strip() == b"same-commit"

  git(repo, "restore", "model.txt")
  switch_source_branch(project, choice)
  assert (repo / "model.txt").read_text() == "branch content\n"
  assert git(repo, "branch", "--show-current").strip() == b"feature"


def test_switch_local_branch_validates_target_and_project_manifest(repo):
  git(repo, "branch", "without-project")
  initialize_project(repo)
  git(repo, "add", "texsys.toml")
  git(repo, "commit", "-m", "add project")
  project = inspect_project(repo)
  with pytest.raises(ProjectError, match="缺少当前项目的清单"):
    switch_source_branch(project, branch_choice(repo, "without-project"))

  git(repo, "switch", "-c", "other-layout")
  manifest = repo / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('build = "build"', 'build = "output"'))
  git(repo, "commit", "-am", "move build directory")
  git(repo, "switch", "main")
  with pytest.raises(ProjectError, match="目录布局"):
    switch_source_branch(project, branch_choice(repo, "other-layout"))

  git(repo, "branch", "moving-target")
  choice = branch_choice(repo, "moving-target")
  git(repo, "branch", "-f", "moving-target", "other-layout")
  with pytest.raises(Conflict, match="目标引用"):
    switch_source_branch(project, choice)
  assert git(repo, "branch", "--show-current").strip() == b"main"


def test_checkout_picker_creates_branches_tracks_remotes_and_detaches_at_refs(repo):
  initialize_project(repo)
  git(repo, "add", "texsys.toml")
  git(repo, "commit", "-m", "add project")
  project = inspect_project(repo)
  base = git(repo, "rev-parse", "HEAD").decode().strip()
  git(repo, "tag", "-a", "v1", "-m", "release")
  (repo / "model.txt").write_text("current commit\n")
  git(repo, "commit", "-am", "current design")
  head = git(repo, "rev-parse", "HEAD").decode().strip()
  git(repo, "remote", "add", "origin", str(repo))
  git(repo, "update-ref", "refs/remotes/origin/feature", base)
  git(repo, "update-ref", "refs/remotes/origin/main", head)
  git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/feature")

  listing = source_branches(repo)
  refs = {item["name"]: item for item in listing["refs"]}
  assert "origin/HEAD" not in refs
  assert refs["v1"]["kind"] == "tag"
  assert (refs["v1"]["commit"], refs["v1"]["author"], refs["v1"]["subject"]) == (base, "Studio Test", "add project")
  assert refs["v1"]["time"] > 0 and refs["origin/feature"]["kind"] == "remote"
  assert refs["origin/feature"]["github"] is None
  git(repo, "remote", "set-url", "origin", "git@github.com:Example/design.git")
  refs = {item["name"]: item for item in source_branches(repo)["refs"]}
  assert refs["origin/feature"]["github"] == {"repository": "Example/design", "ref": "feature"}
  assert refs["v1"]["github"] == {"repository": "Example/design", "ref": "v1"}

  create = {"action": "create", "head": listing["head"]}
  with pytest.raises(ProjectError, match="分支名称"):
    switch_source_branch(project, {**create, "name": "--force"})
  assert switch_source_branch(project, {**create, "name": "design/current"}) == {
    "name": "design/current", "commit": head}
  from_tag = {**branch_choice(repo, "v1"), "action": "create", "name": "design/from-tag"}
  assert switch_source_branch(project, from_tag) == {"name": "design/from-tag", "commit": base}

  assert switch_source_branch(project, branch_choice(repo, "origin/feature")) == {"name": "feature", "commit": base}
  assert git(repo, "rev-parse", "--symbolic-full-name", "@{upstream}").strip() == b"refs/remotes/origin/feature"
  git(repo, "switch", "main")
  assert switch_source_branch(project, branch_choice(repo, "origin/feature"))["name"] == "feature"
  collision = {**branch_choice(repo, "origin/main"), "action": "track", "name": "tracked-main"}
  assert switch_source_branch(project, collision) == {"name": "tracked-main", "commit": head}
  assert git(repo, "rev-parse", "--symbolic-full-name", "@{upstream}").strip() == b"refs/remotes/origin/main"

  assert switch_source_branch(project, branch_choice(repo, "v1")) == {"name": "", "commit": base}
  detach = {"action": "detach", "head": source_branches(repo)["head"], "revision": head[:12]}
  assert switch_source_branch(project, detach) == {"name": "", "commit": head}
  assert git(repo, "rev-parse", "HEAD").decode().strip() == head


def test_empty_history_and_unborn_bulk_unstage(tmp_path):
  from texsys.studio.source_control import source_history
  operation(tmp_path, "init")
  assert source_history(tmp_path)["commits"] == []
  (tmp_path / "new").write_text("keep")
  operation(tmp_path, "stage_all")
  operation(tmp_path, "unstage_all")
  assert (tmp_path / "new").read_text() == "keep"


def test_scm_api_repository_scope_is_explicit_and_cannot_choose_arbitrary_root(repo):
  import json
  import re
  import threading
  from urllib.error import HTTPError
  from urllib.request import Request, urlopen
  from texsys.studio.runtime import StudioRuntime
  from texsys.studio.server import create_server
  project = repo / "project"
  project.mkdir()
  initialize_project(project)
  git(repo, "add", "project")
  git(repo, "commit", "-m", "add project")
  git(repo, "branch", "feature")
  (repo / "model.txt").write_text("outside")
  runtime = StudioRuntime(project, plugins={})
  server = create_server(runtime, port=0, frontend=project / "build/frontend")
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  url = f"http://127.0.0.1:{server.server_port}"
  token = re.search(r'name="tex-studio-token" content="([^"]+)"', urlopen(url).read().decode())[1]
  def request(path, body=None):
    return json.loads(urlopen(Request(url + path, data=json.dumps(body).encode() if body else None,
                                    headers={"X-Tex-Studio-Token": token})).read())
  try:
    scoped = request("/api/scm")
    assert "model.txt" not in [entry["path"] for entry in scoped["entries"]]
    whole = request("/api/scm?scope=repository")
    assert whole["scope"] == "." and whole["project_prefix"] == "project"
    assert "model.txt" in [entry["path"] for entry in whole["entries"]]
    with pytest.raises(HTTPError):
      request("/api/scm?scope=/tmp")
    staged = request("/api/scm", {"action": "stage", "scope": "repository", "path": "model.txt", "snapshot": whole["snapshot"]})
    assert next(entry for entry in staged["entries"] if entry["path"] == "model.txt")["index"] == "M"
    assert "+outside" in request("/api/scm/diff?scope=repository&path=model.txt&staged=true")["content"]
    assert request("/api/scm/history")["commits"][0]["subject"] == "add project"
    listing = request("/api/scm/branches")
    feature = next(item for item in listing["refs"] if item["name"] == "feature")
    selected = {"action": "checkout", "ref": feature["ref"], "commit": feature["commit"], "head": listing["head"]}
    assert request("/api/scm/branches", selected)["name"] == "feature"
    assert request("/api/state")["project"]["branch"] == {"kind": "branch", "name": "feature"}
    assert git(repo, "show", ":model.txt") == b"outside"
    for path in ("/api/scm/branches", "/api/scm/history",
                 "/api/scm/commit?id=" + git(repo, "rev-parse", "HEAD").decode().strip()):
      with pytest.raises(HTTPError) as error:
        urlopen(url + path)
      assert error.value.code == 403
  finally:
    server.shutdown()
    server.server_close()
    thread.join()
    runtime.close()
