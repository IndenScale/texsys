import json
from pathlib import Path
import runpy
import shutil

import pytest

from texsys.dependencies import lock_project

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("kind,example,expected", [
  ("bom", "hvac-dynamic-station", "hvac:equipment-bom"),
  ("system", "hvac-dynamic-station", "hvac:system-schematic"),
  ("schematic", "hvac-dynamic-station", "hvac:system-schematic"),
])
def test_python_build_example_generates_requested_deliverable(tmp_path, capsys, kind, example, expected):
  project = tmp_path / "project"
  shutil.copytree(ROOT / "examples" / example, project, ignore=shutil.ignore_patterns("build"))
  (project / "build").mkdir()
  manifest = project / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))
  lock_project(project)
  namespace = runpy.run_path(str(ROOT / "examples/build-deliverables/run.py"))

  code = namespace["main"]([kind, "--project", str(project)])

  output = json.loads(capsys.readouterr().out)
  assert code == 0
  assert output["status"] == "pass"
  assert output["targets"][0]["id"] == expected
  assert output["artifacts"]
  assert all(not artifact["partial"] for artifact in output["artifacts"])
  assert all((project / artifact["path"]).is_file() for artifact in output["artifacts"])
  if kind == "bom":
    assert any(a["id"] == "bom-data" for a in output["artifacts"])
  else:
    document = json.loads((project / "build/hvac" / kind / "system.json").read_text())
    assert document["deliverable"] == ("system-diagram" if kind == "system" else "schematic")


def test_python_transient_example_returns_existing_artifacts(tmp_path, capsys):
  project = tmp_path / "project"
  shutil.copytree(ROOT / "examples/hvac-dynamic-station", project, ignore=shutil.ignore_patterns("build"))
  (project / "build").mkdir()
  manifest = project / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))
  lock_project(project)
  namespace = runpy.run_path(str(ROOT / "examples/build-deliverables/run.py"))

  code = namespace["main"](["transient", "--project", str(project)])

  output = json.loads(capsys.readouterr().out)
  assert code == 0
  assert output["status"] == "pass"
  assert output["kind"] == "transient"
  assert output["artifacts"]
  assert all(Path(path).is_file() for path in output["artifacts"])
  assert output["report"] in output["artifacts"]
