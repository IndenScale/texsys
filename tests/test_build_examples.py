import json
from pathlib import Path
import subprocess
import sys
import shutil

import pytest

from texsys.dependencies import lock_project

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("kind,example,expected", [
  ("bom", "hvac-dynamic-station", "hvac:equipment-bom"),
  ("system", "hvac-dynamic-station", "hvac:system-schematic"),
  ("schematic", "hvac-dynamic-station", "hvac:system-schematic"),
])
def test_python_build_example_generates_requested_deliverable(tmp_path, kind, example, expected):
  project = tmp_path / "project"
  shutil.copytree(ROOT / "examples" / example, project, ignore=shutil.ignore_patterns("build"))
  (project / "build").mkdir()
  manifest = project / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))
  lock_project(project)
  result = subprocess.run([sys.executable, "-B", str(ROOT / "examples/build-deliverables/run.py"),
                           kind, "--project", str(project)], capture_output=True, text=True, check=False)
  code = result.returncode
  output = json.loads(result.stdout)
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


def test_python_transient_example_returns_existing_artifacts(tmp_path):
  project = tmp_path / "project"
  shutil.copytree(ROOT / "examples/hvac-dynamic-station", project, ignore=shutil.ignore_patterns("build"))
  (project / "build").mkdir()
  manifest = project / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))
  lock_project(project)
  result = subprocess.run([sys.executable, "-B", str(ROOT / "examples/build-deliverables/run.py"),
                           "transient", "--project", str(project)], capture_output=True, text=True, check=False)
  code = result.returncode
  output = json.loads(result.stdout)
  assert code == 0
  assert output["status"] == "pass"
  assert output["kind"] == "transient"
  assert output["artifacts"]
  assert all(Path(path).is_file() for path in output["artifacts"])
  assert output["report"] in output["artifacts"]


def test_real_cli_reuses_targets_and_rebuilds_only_changed_layout(tmp_path):
  project = tmp_path / "project"
  shutil.copytree(ROOT / "examples/hvac-dynamic-station", project, ignore=shutil.ignore_patterns("build", "texsys.lock"))
  manifest = project / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))

  def cli(command):
    return subprocess.run([sys.executable, "-B", "-m", "texsys", command, "-p", str(project)],
                          capture_output=True, text=True, check=False)

  def build():
    process = cli("build")
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads((project / "build/build.json").read_text())

  assert cli("lock").returncode == 0
  first = build()
  assert all(target["execution"] == "executed" for target in first["targets"])
  drawings = {item["path"] for item in first["artifacts"] if item["id"] == "system-data" and item["published"]}
  assert drawings == {"build/drawings/schematics/system.json", "build/drawings/topology/system.json"}
  original_scene = next(item for item in first["artifacts"] if (item.get("preview") or {}).get("kind") == "scene")
  original_bytes = (project / original_scene["stored_path"]).read_bytes()
  second = build()
  assert all(target["execution"] == "cached" for target in second["targets"])
  assert second["previous_run_id"] == first["run_id"]
  layout = project / "assets/layout.json"
  document = json.loads(layout.read_text())
  document["objects"]["CH-01"]["origin"][0] += 0.5
  layout.write_text(json.dumps(document))
  third = build()
  assert {target["id"]: target["execution"] for target in third["targets"]} == {
    "hvac:equipment-bom": "cached", "hvac:physical-model": "cached", "system-schematic": "cached",
    "system-topology": "cached", "hvac:project-layout": "executed",
    "drawing-system": "cached", "drawing-notes": "cached",
  }
  assert (project / original_scene["stored_path"]).read_bytes() == original_bytes
  assert (project / original_scene["path"]).read_bytes() != original_bytes
  document["objects"]["CH-01"]["size"][0] = -1
  layout.write_text(json.dumps(document))
  failed = cli("build")
  assert failed.returncode != 0
  report = json.loads((project / "build/build.json").read_text())
  records = {target["id"]: target for target in report["targets"]}
  assert records["hvac:project-layout"]["status"] != "pass"
  assert records["hvac:equipment-bom"]["status"] == records["system-topology"]["status"] == "pass"
  assert records["hvac:equipment-bom"]["execution"] == records["system-topology"]["execution"] == "cached"
