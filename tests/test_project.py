from pathlib import Path

import pytest
from typer.testing import CliRunner

from texsys.cli import app

runner = CliRunner()


def test_init_defaults_to_current_directory(
  tmp_path: Path, monkeypatch
) -> None:
  monkeypatch.chdir(tmp_path)

  result = runner.invoke(app, ["init"])

  assert result.exit_code == 0, result.output
  assert (tmp_path / "texsys.toml").is_file()
  assert (tmp_path / "src").is_dir()
  assert (tmp_path / "assets").is_dir()
  assert (tmp_path / "build").is_dir()
  assert (tmp_path / ".gitignore").read_text() == "/build/\n"
  assert f'name = "{tmp_path.name}"' in (tmp_path / "texsys.toml").read_text()


def test_init_accepts_short_path_option(tmp_path: Path) -> None:
  target = tmp_path / "nested" / "demo"

  result = runner.invoke(app, ["init", "-p", str(target)])

  assert result.exit_code == 0, result.output
  assert (target / "texsys.toml").is_file()
  assert 'name = "demo"' in (target / "texsys.toml").read_text()


def test_repeated_init_preserves_manifest(tmp_path: Path) -> None:
  first = runner.invoke(app, ["init", "-p", str(tmp_path)])
  manifest = tmp_path / "texsys.toml"
  original = manifest.read_text()

  second = runner.invoke(app, ["init", "--path", str(tmp_path)])

  assert first.exit_code == 0
  assert second.exit_code == 0, second.output
  assert "No changes needed" in second.output
  assert manifest.read_text() == original


def test_info_defaults_to_current_directory(
  tmp_path: Path, monkeypatch
) -> None:
  monkeypatch.chdir(tmp_path)
  (tmp_path / "pyproject.toml").write_text("[project]\n")
  (tmp_path / "uv.lock").write_text("version = 1\n")
  assert runner.invoke(app, ["init"]).exit_code == 0

  result = runner.invoke(app, ["info"])

  assert result.exit_code == 0, result.output
  assert f"Project: {tmp_path.name}" in result.output
  assert "source: src (present)" in result.output
  assert "assets: assets (present)" in result.output
  assert "build: build (present)" in result.output
  assert "pyproject.toml: present" in result.output
  assert "uv.lock: present" in result.output


def test_info_accepts_long_path_option(tmp_path: Path) -> None:
  assert runner.invoke(app, ["init", "-p", str(tmp_path)]).exit_code == 0

  result = runner.invoke(app, ["info", "--path", str(tmp_path)])

  assert result.exit_code == 0, result.output
  assert f"Root: {tmp_path}" in result.output


def test_info_reports_uninitialized_directory(tmp_path: Path) -> None:
  result = runner.invoke(app, ["info", "-p", str(tmp_path)])

  assert result.exit_code == 0, result.output
  assert "Project: not initialized" in result.output
  assert "texsys.toml (missing)" in result.output


def test_info_rejects_missing_path(tmp_path: Path) -> None:
  result = runner.invoke(app, ["info", "-p", str(tmp_path / "missing")])

  assert result.exit_code == 1
  assert "does not exist" in result.output


def test_info_rejects_invalid_manifest(tmp_path: Path) -> None:
  (tmp_path / "texsys.toml").write_text("schema_version = 999\n")

  result = runner.invoke(app, ["info", "-p", str(tmp_path)])

  assert result.exit_code == 1
  assert "Unsupported or missing schema_version" in result.output


def test_init_rejects_layout_file_conflict(tmp_path: Path) -> None:
  (tmp_path / "src").write_text("not a directory")

  result = runner.invoke(app, ["init", "-p", str(tmp_path)])

  assert result.exit_code == 1
  assert "Layout path 'source' is not a directory" in result.output
  assert not (tmp_path / "texsys.toml").exists()


def test_info_reports_incomplete_layout(tmp_path: Path) -> None:
  assert runner.invoke(app, ["init", "-p", str(tmp_path)]).exit_code == 0
  (tmp_path / "assets").rmdir()

  result = runner.invoke(app, ["info", "-p", str(tmp_path)])

  assert result.exit_code == 1
  assert "assets: assets (missing)" in result.output


def test_init_respects_custom_build_directory(tmp_path: Path) -> None:
  (tmp_path / "texsys.toml").write_text(
    """schema_version = 1

[project]
name = "custom"

[layout]
source = "model"
assets = "usd"
build = "out"
"""
  )

  result = runner.invoke(app, ["init", "-p", str(tmp_path)])

  assert result.exit_code == 0, result.output
  assert (tmp_path / "model").is_dir()
  assert (tmp_path / "usd").is_dir()
  assert (tmp_path / "out").is_dir()
  assert (tmp_path / ".gitignore").read_text() == "/out/\n"


def test_info_rejects_overlapping_layout_roles(tmp_path: Path) -> None:
  (tmp_path / "texsys.toml").write_text(
    """schema_version = 1

[project]
name = "invalid"

[layout]
source = "same"
assets = "./same"
build = "build"
"""
  )

  result = runner.invoke(app, ["info", "-p", str(tmp_path)])

  assert result.exit_code == 1
  assert "must be distinct" in result.output


@pytest.mark.parametrize("build", ["src/generated", "src/../outside"])
def test_init_rejects_nested_or_escaping_layout(tmp_path: Path, build: str) -> None:
  manifest = (
    'schema_version = 1\n[project]\nname = "unsafe"\n'
    f'[layout]\nsource = "src"\nassets = "assets"\nbuild = "{build}"\n'
  )
  (tmp_path / "texsys.toml").write_text(manifest)

  result = runner.invoke(app, ["init", "-p", str(tmp_path)])

  assert result.exit_code == 1
  assert not (tmp_path / "src").exists()


def test_init_rejects_layout_symlink_outside_project(tmp_path: Path) -> None:
  root = tmp_path / "project"
  outside = tmp_path / "outside"
  root.mkdir()
  outside.mkdir()
  (root / "build").symlink_to(outside, target_is_directory=True)

  result = runner.invoke(app, ["init", "-p", str(root)])

  assert result.exit_code == 1
  assert "inside the project root" in result.output
  assert not (root / "texsys.toml").exists()
  assert list(outside.iterdir()) == []
