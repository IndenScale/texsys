import json
from pathlib import Path
import shutil
import stat
import zipfile

import pytest
import tomlkit

from texsys.dependencies import (
  add_registry_dependency, apply_dependency_change, dependency_status, lock_project,
  plan_dependency_change, sync_project, update_registry_dependency,
)
from texsys.formal import check_project
from texsys.project import ProjectError, initialize_project
from texsys.registry import (
  HEADER, cache_path, pack_package, package_details, publish_package, read_archive, search_packages,
)


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
  (root / "pyproject.toml").write_text('[project]\nname="example"\nversion="1.0.0"\n')
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


def test_reproducible_pack_and_two_consumers_without_author_sources(tmp_path):
  authors = tmp_path / "authors"
  base = capability(authors / "base", "base")
  equipment = capability(authors / "equipment", "equipment", dependency=base)
  (equipment / "build/secret.txt").write_text("generated")
  (equipment / "reviews").mkdir()
  (equipment / "reviews/private.txt").write_text("project decision")
  source_before = (equipment / "texsys.toml").read_bytes()
  first = pack_package(equipment, tmp_path / "packed")
  second = pack_package(equipment, tmp_path / "packed-again")
  assert Path(first["root_artifact"]).read_bytes() == Path(second["root_artifact"]).read_bytes()
  assert (equipment / "texsys.toml").read_bytes() == source_before
  metadata = read_archive(Path(first["root_artifact"]))
  assert metadata["dependencies"] == {"base": "1.0.0"}
  assert metadata["tests"] == ["tests"]
  names = {item["path"] for item in metadata["files"]}
  assert {"README.md", "pyproject.toml", "tests/test_capability.py"} <= names
  assert not any(name.startswith(("build/", "reviews/")) for name in names)
  registry = tmp_path / "registry"
  published = publish_package(Path(first["root_artifact"]), registry)
  assert len(published["packages"]) == 2
  assert all(item["already_published"] for item in publish_package(equipment, registry)["packages"])
  assert search_packages(registry, "equipment")["packages"][0]["name"] == "equipment"
  shutil.rmtree(authors)
  one, two = consumer(tmp_path / "one"), consumer(tmp_path / "two")
  added = add_registry_dependency(one, "equipment", "1.0.0", registry)
  assert {item.name for item in added.packages} == {"base", "equipment"}
  assert all(item.source == "registry" for item in added.packages)
  original_manifest, original_lock = (one / "texsys.toml").read_bytes(), (one / "texsys.lock").read_bytes()
  assert "authors" not in original_lock.decode()
  assert check_project(one).status == "pass"
  shutil.rmtree(one / "build")
  shutil.rmtree(tmp_path / "cache")
  sync_project(one)
  assert check_project(one).status == "pass"
  assert (one / "texsys.toml").read_bytes() == original_manifest
  assert (one / "texsys.lock").read_bytes() == original_lock
  add_registry_dependency(two, "equipment", "1.0.0", registry)
  assert [item.digest for item in lock_project(one, check=True).packages] == [item.digest for item in lock_project(two, check=True).packages]
  (one / "src/project-note.txt").write_text("one only")
  assert not (two / "src/project-note.txt").exists()
  # No registry is required for a verified cached restore.
  shutil.rmtree(registry)
  shutil.rmtree(one / "build")
  sync_project(one)
  assert check_project(one).status == "pass"
  assert dependency_status(one)["synchronized"]


def test_explicit_upgrade_rollback_and_stale_or_tampered_plan(tmp_path):
  registry = tmp_path / "registry"
  source = capability(tmp_path / "equipment", "equipment")
  publish_package(source, registry)
  root = consumer(tmp_path / "consumer")
  add_registry_dependency(root, "equipment", "1.0.0", registry)
  old_digest = lock_project(root, check=True).packages[0].digest
  data = tomlkit.parse((source / "texsys.toml").read_text())
  data["project"]["version"] = "2.0.0"
  (source / "texsys.toml").write_text(tomlkit.dumps(data))
  (source / "assets/performance.json").write_text('{"nominal":200}\n')
  publish_package(source, registry)
  sync_project(root)
  assert lock_project(root, check=True).packages[0].version == "1.0.0"
  request = {"action": "update", "name": "equipment", "version": "2.0.0", "revision": dependency_status(root)["revision"]}
  preview = plan_dependency_change(root, request)
  assert preview["changes"][0]["before"]["version"] == "1.0.0"
  assert preview["changes"][0]["after"]["version"] == "2.0.0"
  before = (root / "texsys.toml").read_bytes(), (root / "texsys.lock").read_bytes()
  with pytest.raises(ProjectError, match="plan"):
    apply_dependency_change(root, {**request, "version": "1.0.0"}, preview["plan"])
  assert before == ((root / "texsys.toml").read_bytes(), (root / "texsys.lock").read_bytes())
  applied = apply_dependency_change(root, request, preview["plan"])
  assert applied["applied"] and applied["packages"][0]["version"] == "2.0.0"
  with pytest.raises(ProjectError, match="changed"):
    apply_dependency_change(root, request, preview["plan"])
  update_registry_dependency(root, "equipment", "1.0.0")
  assert lock_project(root, check=True).packages[0].digest == old_digest
  removal = {"action": "remove", "name": "equipment"}
  apply_dependency_change(root, removal, plan_dependency_change(root, removal)["plan"])
  assert not dependency_status(root)["packages"]


def test_immutable_release_and_missing_or_changed_archive_preserve_consumer(tmp_path):
  source = capability(tmp_path / "equipment", "equipment")
  registry = tmp_path / "registry"
  publish_package(source, registry)
  release = package_details(registry, "equipment", "1.0.0")
  archive_before = Path(release["artifact"]).read_bytes()
  (source / "assets/performance.json").write_text('{"nominal":999}\n')
  with pytest.raises(ProjectError, match="Immutable"):
    publish_package(source, registry)
  assert Path(release["artifact"]).read_bytes() == archive_before
  root = consumer(tmp_path / "consumer")
  add_registry_dependency(root, "equipment", "1.0.0", registry)
  before = (root / "texsys.toml").read_bytes(), (root / "texsys.lock").read_bytes()
  installed = root / "build/dependencies/equipment/assets/performance.json"
  installed_before = installed.read_bytes()
  with pytest.raises(ProjectError, match="unavailable"):
    update_registry_dependency(root, "equipment", "9.0.0")
  assert before == ((root / "texsys.toml").read_bytes(), (root / "texsys.lock").read_bytes())
  assert installed.read_bytes() == installed_before
  # A corrupted cache is not silently trusted or overwritten.
  cached = cache_path(release["digest"]) / "assets/performance.json"
  cached.write_text("corrupt")
  with pytest.raises(ProjectError, match="content changed"):
    sync_project(root)
  assert installed.read_bytes() == installed_before
  assert before == ((root / "texsys.toml").read_bytes(), (root / "texsys.lock").read_bytes())
  shutil.rmtree(tmp_path / "cache")
  shutil.rmtree(root / "build")
  Path(release["artifact"]).write_bytes(archive_before[:-10])
  with pytest.raises(ProjectError, match="archive"):
    sync_project(root)
  assert before == ((root / "texsys.toml").read_bytes(), (root / "texsys.lock").read_bytes())


@pytest.mark.parametrize("member", ["../escape", "/absolute", "folder\\escape", "C:/drive", "folder//item"])
def test_archive_rejects_unsafe_member_paths(tmp_path, member):
  file = tmp_path / "malicious.texpkg"
  with zipfile.ZipFile(file, "w") as archive:
    archive.writestr(member, "bad")
  with pytest.raises(ProjectError, match="Unsafe"):
    read_archive(file)
  assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("kind", ["duplicate", "symlink", "size"])
def test_archive_rejects_duplicate_link_and_size(tmp_path, monkeypatch, kind):
  file = tmp_path / "malicious.texpkg"
  with zipfile.ZipFile(file, "w") as archive:
    if kind == "duplicate":
      archive.writestr("Entry", "a")
      archive.writestr("entry", "b")
    elif kind == "symlink":
      info = zipfile.ZipInfo("link")
      info.create_system = 3
      info.external_attr = (stat.S_IFLNK | 0o777) << 16
      archive.writestr(info, "/etc/passwd")
    else:
      archive.writestr("entry", "a" * 10)
      monkeypatch.setattr("texsys.registry.MAX_MEMBER_BYTES", 5)
  with pytest.raises(ProjectError, match="Duplicate|non-regular|limits"):
    read_archive(file)


def test_pack_requires_explicit_capability_and_rejects_private_include_and_missing_tests(tmp_path):
  project = consumer(tmp_path / "project")
  with pytest.raises(ProjectError, match="kind"):
    pack_package(project)
  source = capability(tmp_path / "equipment", "equipment")
  data = tomlkit.parse((source / "texsys.toml").read_text())
  data["package"]["include"] = ["reviews"]
  (source / "reviews").mkdir()
  (source / "reviews/decision.json").write_text("{}")
  (source / "texsys.toml").write_text(tomlkit.dumps(data))
  with pytest.raises(ProjectError, match="include"):
    pack_package(source)
  data["package"]["include"] = ["README.md"]
  (source / "texsys.toml").write_text(tomlkit.dumps(data))
  with pytest.raises(ProjectError, match="test is missing"):
    pack_package(source)


def test_manifest_and_lock_rollback_when_install_copy_fails(tmp_path, monkeypatch):
  source = capability(tmp_path / "equipment", "equipment")
  registry = tmp_path / "registry"
  publish_package(source, registry)
  root = consumer(tmp_path / "consumer")
  before = (root / "texsys.toml").read_bytes()

  def fail(*args):
    raise ProjectError("injected copy failure")

  monkeypatch.setattr("texsys.dependencies._copy_package", fail)
  with pytest.raises(ProjectError, match="injected"):
    add_registry_dependency(root, "equipment", "1.0.0", registry)
  assert (root / "texsys.toml").read_bytes() == before
  assert not (root / "texsys.lock").exists()
  assert not (root / "build/dependencies").exists()


def test_archive_inventory_tampering_is_rejected(tmp_path):
  source = capability(tmp_path / "equipment", "equipment")
  archive = Path(pack_package(source, tmp_path / "packed")["root_artifact"])
  modified = tmp_path / "modified.texpkg"
  with zipfile.ZipFile(archive) as original, zipfile.ZipFile(modified, "w") as output:
    for item in original.infolist():
      content = original.read(item.filename)
      if item.filename == HEADER:
        header = json.loads(content)
        header["files"].pop()
        content = json.dumps(header).encode()
      output.writestr(item, content)
  with pytest.raises(ProjectError, match="inventory"):
    read_archive(modified)


def test_registry_graph_lock_tampering_and_relocated_workspace(tmp_path, monkeypatch):
  workspace = tmp_path / "workspace"
  dependency = capability(tmp_path / "authors/base", "base")
  source = capability(tmp_path / "authors/equipment", "equipment", dependency=dependency)
  registry = workspace / "registry"
  publish_package(source, registry)
  root = consumer(workspace / "consumer")
  add_registry_dependency(root, "equipment", "1.0.0", registry)
  original = (root / "texsys.lock").read_bytes()
  data = tomlkit.parse(original.decode())
  data["packages"] = [item for item in data["packages"] if item["name"] != "base"]
  (root / "texsys.lock").write_text(tomlkit.dumps(data))
  with pytest.raises(ProjectError, match="dependency graph"):
    sync_project(root)
  (root / "texsys.lock").write_bytes(original)
  destination = tmp_path / "relocated"
  workspace.rename(destination)
  moved = destination / "consumer"
  shutil.rmtree(tmp_path / "authors")
  shutil.rmtree(moved / "build")
  monkeypatch.setenv("TEXSYS_CACHE_DIR", str(tmp_path / "another-cache"))
  sync_project(moved)
  assert (moved / "texsys.lock").read_bytes() == original
  assert check_project(moved).status == "pass"


def test_recursive_publish_failure_rolls_back_new_releases(tmp_path, monkeypatch):
  base = capability(tmp_path / "authors/base", "base")
  source = capability(tmp_path / "authors/equipment", "equipment", dependency=base)
  archive = Path(pack_package(source, tmp_path / "archives")["root_artifact"])
  registry = tmp_path / "registry"
  original = shutil.copyfile
  copies = []

  def fail_second_release(before, after, *args, **kwargs):
    if Path(after).name == "artifact.texpkg":
      copies.append(str(before))
      if len(copies) == 2:
        raise OSError("injected registry storage failure")
    return original(before, after, *args, **kwargs)

  monkeypatch.setattr("texsys.registry.shutil.copyfile", fail_second_release)
  with pytest.raises(ProjectError, match="storage failure"):
    publish_package(archive, registry)
  assert not (registry / "packages/base/1.0.0").exists()
  assert not (registry / "packages/equipment/1.0.0").exists()
  assert not (registry / ".texsys-registry.lock").exists()


def test_sync_revision_is_checked_under_write_lock_without_mutating_dependencies(tmp_path, monkeypatch):
  source = capability(tmp_path / "equipment", "equipment")
  registry = tmp_path / "registry"
  publish_package(source, registry)
  root = consumer(tmp_path / "consumer")
  add_registry_dependency(root, "equipment", "1.0.0", registry)
  revision = dependency_status(root)["revision"]
  sync_project(root, expected_revision=revision)
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text() + "\n# concurrent edit\n")
  before = manifest.read_bytes(), (root / "texsys.lock").read_bytes()
  installed = root / "build/dependencies/equipment"
  content = {str(file.relative_to(installed)): file.read_bytes() for file in installed.rglob("*") if file.is_file()}
  from texsys import dependencies
  original_revision = dependencies._dependency_revision
  checked_under_lock = []

  def observed_revision(inputs):
    checked_under_lock.append((root / ".texsys-write.lock").is_dir())
    return original_revision(inputs)

  monkeypatch.setattr(dependencies, "_dependency_revision", observed_revision)
  with pytest.raises(ProjectError, match="state changed"):
    sync_project(root, expected_revision=revision)
  assert checked_under_lock == [True]
  assert before == (manifest.read_bytes(), (root / "texsys.lock").read_bytes())
  assert content == {str(file.relative_to(installed)): file.read_bytes() for file in installed.rglob("*") if file.is_file()}
