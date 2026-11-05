from pathlib import Path

from typer.testing import CliRunner

from texsys.cli import app
from texsys.project import initialize_project

runner = CliRunner()


def make_project(root: Path, name: str, version: str | None = None) -> Path:
  initialize_project(root)
  manifest = root / "texsys.toml"
  text = manifest.read_text()
  if version is not None:
    text = text.replace(f'name = "{name}"', f'name = "{name}"\nversion = "{version}"')
  manifest.write_text(text)
  (root / "src" / "component.txt").write_text(f"{name} {version}\n")
  (root / "assets" / "asset.txt").write_text(f"asset for {name}\n")
  return root


def add_declaration(root: Path, name: str, target: Path, version: str) -> None:
  manifest = root / "texsys.toml"
  declaration = f'{name} = {{ path = "../{target.name}", version = "{version}" }}\n'
  with manifest.open("a") as stream:
    if "[dependencies]" not in manifest.read_text():
      stream.write("\n[dependencies]\n")
    stream.write(declaration)


def test_add_lock_sync_and_remove_transitive_dependency(tmp_path: Path) -> None:
  package_b = make_project(tmp_path / "b", "b", "0.2.0")
  package_a = make_project(tmp_path / "a", "a", "0.1.0")
  add_declaration(package_a, "b", package_b, "0.2.0")
  root = make_project(tmp_path / "consumer", "consumer")

  result = runner.invoke(
    app,
    ["add", str(package_a), "--version", "0.1.0", "-p", str(root)],
  )

  assert result.exit_code == 0, result.output
  lock = (root / "texsys.lock").read_text()
  assert 'name = "a"' in lock
  assert 'name = "b"' in lock
  assert "sha256:" in lock
  assert (root / "build" / "dependencies" / "a" / "src" / "component.txt").is_file()
  assert (root / "build" / "dependencies" / "b" / "assets" / "asset.txt").is_file()
  assert runner.invoke(app, ["lock", "--check", "-p", str(root)]).exit_code == 0

  manifest_before = (root / "texsys.toml").read_bytes()
  lock_before = (root / "texsys.lock").read_bytes()
  sync = runner.invoke(app, ["sync", "-p", str(root)])
  assert sync.exit_code == 0, sync.output
  assert (root / "texsys.toml").read_bytes() == manifest_before
  assert (root / "texsys.lock").read_bytes() == lock_before

  removed = runner.invoke(app, ["remove", "a", "-p", str(root)])
  assert removed.exit_code == 0, removed.output
  assert "[[packages]]" not in (root / "texsys.lock").read_text()
  assert not (root / "build" / "dependencies" / "a").exists()
  assert not (root / "build" / "dependencies" / "b").exists()


def test_failed_add_preserves_manifest_and_lock(tmp_path: Path) -> None:
  package = make_project(tmp_path / "package", "package", "1.0.0")
  root = make_project(tmp_path / "consumer", "consumer")
  original = (root / "texsys.toml").read_bytes()

  result = runner.invoke(
    app,
    ["add", str(package), "--version", "2.0.0", "-p", str(root)],
  )

  assert result.exit_code == 1
  assert "requires version 2.0.0" in result.output
  assert (root / "texsys.toml").read_bytes() == original
  assert not (root / "texsys.lock").exists()
  assert not (root / "build" / "dependencies").exists()


def test_sync_uses_declared_build_directory(tmp_path: Path) -> None:
  package = make_project(tmp_path / "package", "package", "1.0.0")
  root = make_project(tmp_path / "consumer", "consumer")
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('build = "build"', 'build = "out"'))
  (root / "build").rmdir()
  (root / "out").mkdir()

  result = runner.invoke(app, ["add", str(package), "-p", str(root)])

  assert result.exit_code == 0, result.output
  assert (root / "out/dependencies/package/src/component.txt").is_file()
  assert not (root / "build").exists()


def test_sync_rejects_lock_with_omitted_packages(tmp_path: Path) -> None:
  package = make_project(tmp_path / "package", "package", "1.0.0")
  root = make_project(tmp_path / "consumer", "consumer")
  assert runner.invoke(app, ["add", str(package), "-p", str(root)]).exit_code == 0
  lock = root / "texsys.lock"
  lock.write_text(lock.read_text().split("[[packages]]")[0])
  synced = root / "build/dependencies/package/src/component.txt"
  original = synced.read_bytes()

  result = runner.invoke(app, ["sync", "-p", str(root)])

  assert result.exit_code == 1
  assert "dependency graph" in result.output
  assert synced.read_bytes() == original


def test_dependency_cycle_fails_without_lock(tmp_path: Path) -> None:
  package_a = make_project(tmp_path / "a", "a", "1.0.0")
  package_b = make_project(tmp_path / "b", "b", "1.0.0")
  add_declaration(package_a, "b", package_b, "1.0.0")
  add_declaration(package_b, "a", package_a, "1.0.0")
  root = make_project(tmp_path / "consumer", "consumer")

  result = runner.invoke(app, ["add", str(package_a), "-p", str(root)])

  assert result.exit_code == 1
  assert "Dependency cycle detected" in result.output
  assert not (root / "texsys.lock").exists()


def test_sync_rejects_source_content_drift(tmp_path: Path) -> None:
  package = make_project(tmp_path / "package", "package", "1.0.0")
  root = make_project(tmp_path / "consumer", "consumer")
  assert runner.invoke(app, ["add", str(package), "-p", str(root)]).exit_code == 0
  synced = root / "build" / "dependencies" / "package" / "src" / "component.txt"
  original = synced.read_bytes()
  lock_before = (root / "texsys.lock").read_bytes()
  (package / "src" / "component.txt").write_text("changed without relocking\n")

  result = runner.invoke(app, ["sync", "-p", str(root)])

  assert result.exit_code == 1
  assert "Locked content changed" in result.output
  assert synced.read_bytes() == original
  assert (root / "texsys.lock").read_bytes() == lock_before


def test_lock_check_and_sync_reject_stale_manifest(tmp_path: Path) -> None:
  package = make_project(tmp_path / "package", "package", "1.0.0")
  root = make_project(tmp_path / "consumer", "consumer")
  assert runner.invoke(app, ["add", str(package), "-p", str(root)]).exit_code == 0
  lock_before = (root / "texsys.lock").read_bytes()
  add_declaration(root, "other", package, "1.0.0")

  check = runner.invoke(app, ["lock", "--check", "-p", str(root)])
  sync = runner.invoke(app, ["sync", "-p", str(root)])

  assert check.exit_code == 1
  assert sync.exit_code == 1
  assert "does not match" in sync.output
  assert (root / "texsys.lock").read_bytes() == lock_before


def test_sync_requires_an_existing_lock(tmp_path: Path) -> None:
  root = make_project(tmp_path / "consumer", "consumer")

  result = runner.invoke(app, ["sync", "-p", str(root)])

  assert result.exit_code == 1
  assert "Cannot read" in result.output
  assert not (root / "build" / "dependencies").exists()
