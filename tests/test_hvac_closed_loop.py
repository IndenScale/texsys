import json
from pathlib import Path
import shutil

import pytest
from pxr import Usd
from typer.testing import CliRunner

from texsys.cli import app
from texsys.packages import installed_packages

runner = CliRunner()
ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "hvac-cold-station"
PACKAGES = ROOT / "packages"


def copied_project(tmp_path: Path) -> Path:
  target = tmp_path / "hvac-cold-station"
  shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("build"))
  (target / "build").mkdir()
  manifest = target / "texsys.toml"
  manifest.write_text(
    manifest.read_text(encoding="utf-8").replace('"../../packages/', f'"{PACKAGES}/'),
    encoding="utf-8",
  )
  assert runner.invoke(app, ["lock", "-p", str(target)]).exit_code == 0
  assert runner.invoke(app, ["sync", "-p", str(target)]).exit_code == 0
  return target


def build_snapshot(root: Path) -> dict[str, bytes]:
  return {
    path.relative_to(root).as_posix(): path.read_bytes()
    for path in sorted((root / "build").rglob("*"))
    if path.is_file() and "dependencies" not in path.relative_to(root).parts
  }


def update_domain(root: Path, update) -> None:
  path = root / "src" / "hvac.json"
  document = json.loads(path.read_text(encoding="utf-8"))
  update(document)
  path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_all_hvac_packages_are_discoverable() -> None:
  packages = {package.name: package.version for package in installed_packages()}

  assert packages == {
    "bom": "0.1.0",
    "hvac-dynamics": "0.1.0",
    "hvac-equipment": "0.1.0",
    "hvac-formatter": "0.1.0",
    "hvac-linter": "0.1.0",
    "hvac-checks": "0.1.0",
  }


def test_hvac_closed_loop_is_real_and_deterministic(tmp_path: Path) -> None:
  root = copied_project(tmp_path)
  original_sources = {
    path.name: path.read_bytes() for path in (root / "src").glob("*.json")
  }

  lint = runner.invoke(app, ["lint", "--json", "-p", str(root)])
  assert lint.exit_code == 0, lint.output
  assert json.loads(lint.output)["status"] == "pass"

  first_build = runner.invoke(app, ["build", "-p", str(root)])
  assert first_build.exit_code == 0, first_build.output
  scene_path = root / "build" / "usd" / "layout.usda"
  asset_path = root / "build" / "usd" / "assets" / "unit_brep_cube.usda"
  checks_path = root / "build/hvac/design-checks.json"
  assert scene_path.is_file()
  assert checks_path.is_file()

  asset_stage = Usd.Stage.Open(str(asset_path))
  assert asset_stage.GetPrimAtPath("/World/CubeMats").GetTypeName() == "BrepArray"
  stage = Usd.Stage.Open(str(scene_path))
  identities = {
    prim.GetAttribute("texsys:identity").Get()
    for prim in stage.Traverse()
    if prim.HasAttribute("texsys:identity")
  }
  domain = json.loads((root / "src" / "hvac.json").read_text(encoding="utf-8"))
  assert identities == set(domain["equipment"])
  assert json.loads(checks_path.read_text())["status"] == "pass"

  behavior = runner.invoke(app, ["test", "-p", str(root)])
  assert behavior.exit_code == 0, behavior.output
  test_report = json.loads((root / "build" / "tests" / "results.json").read_text())
  values = test_report["results"][0]["solution"]["values"]
  assert values["capacity_margin_kw"] == pytest.approx(20.0)
  assert values["room_temperature_c"] == pytest.approx(28.727634194831012)
  assert values["dew_point_margin_c"] == pytest.approx(1.2946426956394923)
  assert test_report["results"][0]["solution"]["solver"] == "hvac-design-checks/1"

  simulation = runner.invoke(app, ["sim", "--kind", "event", "-p", str(root)])
  assert simulation.exit_code == 0, simulation.output
  event_report = json.loads((root / "build" / "simulations" / "event.json").read_text())
  assert event_report["results"][0]["solution"]["collisions"] == []
  assert event_report["results"][0]["assertions"][0]["status"] == "pass"

  first_snapshot = build_snapshot(root)
  second_build = runner.invoke(app, ["build", "-p", str(root)])
  assert second_build.exit_code == 0, second_build.output
  assert runner.invoke(app, ["test", "-p", str(root)]).exit_code == 0
  assert runner.invoke(app, ["sim", "-p", str(root)]).exit_code == 0
  assert build_snapshot(root) == first_snapshot
  assert original_sources == {
    path.name: path.read_bytes() for path in (root / "src").glob("*.json")
  }


def test_overload_solves_but_fails_capacity_assertion(tmp_path: Path) -> None:
  root = copied_project(tmp_path)

  def overload(document) -> None:
    document["conditions"]["cooling_load_kw"] = 120.0

  update_domain(root, overload)
  result = runner.invoke(app, ["test", "-p", str(root)])

  assert result.exit_code == 1, result.output
  report = json.loads((root / "build" / "tests" / "results.json").read_text())
  behavior = report["results"][0]
  assert behavior["solution"]["status"] == "solved"
  assert behavior["solution"]["values"]["capacity_margin_kw"] == -20.0
  assert behavior["status"] == "fail"


def test_overlapping_equipment_fails_independent_collision_assertion(tmp_path: Path) -> None:
  root = copied_project(tmp_path)

  def overlap(document) -> None:
    document["equipment"]["chilled-pump"]["origin"] = [1.0, 0.0, 0.0]

  update_domain(root, overlap)
  result = runner.invoke(app, ["sim", "--kind", "event", "-p", str(root)])

  assert result.exit_code == 1, result.output
  report = json.loads((root / "build" / "simulations" / "event.json").read_text())
  simulation = report["results"][0]
  assert simulation["solution"]["status"] == "solved"
  assert simulation["solution"]["collisions"] == [
    {"event_index": 1, "first": "chilled-pump", "second": "chiller"}
  ]
  assert simulation["assertions"][0]["status"] == "fail"


def test_field_simulation_is_explicitly_unimplemented(tmp_path: Path) -> None:
  root = copied_project(tmp_path)

  result = runner.invoke(app, ["sim", "--kind", "field", "-p", str(root)])

  assert result.exit_code == 2
  assert "not implemented" in result.output
  assert not (root / "build" / "simulations" / "field.json").exists()
