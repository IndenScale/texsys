import json
import shutil
import tomllib

import pytest
import tomlkit
from typer.testing import CliRunner

from texsys.cli import app
from texsys.dependencies import dependency_status
from texsys.project import ProjectError, initialize_project
from texsys.registry import package_metadata, publish_package
from texsys.studio.library_sources import library_index, reference_library_entry


def make_package(root, kind="component", exports=None):
  initialize_project(root)
  manifest = root / "texsys.toml"
  data = tomlkit.parse(manifest.read_text())
  data["project"]["version"] = "1.0"
  data["package"] = {"kind": kind, "capabilities": ["models"] if kind == "component" else ["rules", "solver"]}
  if exports is not None:
    data["package"]["exports"] = {"components": exports}
  manifest.write_text(tomlkit.dumps(data))
  for name in ("pump", "hidden"):
    folder = root / "src/library/modules" / name
    folder.mkdir(parents=True)
    (folder / f"{name}.sdl.json").write_text(json.dumps({
      "schema_version": 1, "root": name.title(), "components": {name.title(): {}},
    }))
  (root / "src/library/modules/pump/module.toml").write_text(
    'schema_version = 1\nname = "PumpAssets"\nversion = "7.3"\nentry = "pump.sdl.json"\n[assets]\n'
  )
  return root


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
  monkeypatch.setenv("TEXSYS_CACHE_DIR", str(tmp_path / "cache"))


def test_cli_import_classifies_packages_and_component_selection_preserves_dependency_graph(tmp_path):
  exported = "src/library/modules/pump/pump.sdl.json"
  equipment = make_package(tmp_path / "equipment", exports=[exported])
  rules = make_package(tmp_path / "rules", kind="capability")
  registry = tmp_path / "registry"
  publish_package(equipment, registry)
  publish_package(rules, registry)
  project = tmp_path / "consumer"
  initialize_project(project)
  before = (project / "texsys.toml").read_bytes()
  uninstalled = library_index(project, "src", [str(equipment)])["sources"][0]
  assert not uninstalled["locked"] and "texsys add" in uninstalled["cli_hint"]
  with pytest.raises(ProjectError, match="texsys add"):
    reference_library_entry(project, {"folder": str(equipment), "digest": uninstalled["digest"], "entry": exported + "#Pump"})
  assert before == (project / "texsys.toml").read_bytes()
  runner = CliRunner()
  for name in ("equipment", "rules"):
    result = runner.invoke(app, ["add", name, "--version", "1.0", "--registry", str(registry), "-p", str(project)])
    assert result.exit_code == 0, result.output
  shutil.rmtree(equipment)
  shutil.rmtree(rules)
  # Even explicitly imported SDL from an ordinary package is not a component export.
  (project / "src/system.sdl.json").write_text(json.dumps({
    "schema_version": 1, "root": "Station", "imports": {
      "Pump": "@equipment/library/modules/pump/pump.sdl.json",
      "Hidden": "@equipment/library/modules/hidden/hidden.sdl.json",
      "RuleInternal": "@rules/library/modules/pump/pump.sdl.json",
    }, "components": {"Station": {"instances": {"P1": {"component": "Pump"}}}},
  }))
  index = library_index(project, "src", [])
  assert not index["source_errors"]
  assert [source["name"] for source in index["sources"]] == ["equipment"]
  assert [entry["definition"] for entry in index["library"]] == ["@equipment/library/modules/pump/pump.sdl.json#Pump"]
  definition = index["definitions"][index["library"][0]["definition"]]
  assert definition["module_package"]["version"] == "7.3"
  assert definition["package_identity"]["version"] == "1.0"
  assert definition["package_identity"]["entry"] == exported + "#Pump"
  status = {item["name"]: item for item in dependency_status(project)["packages"]}
  assert status["equipment"]["kind"] == "component"
  assert status["equipment"]["exports"] == {"components": [exported]}
  assert status["rules"]["kind"] == "capability"
  source = index["sources"][0]
  locked = (project / "texsys.lock").read_bytes()
  declared = tomllib.loads((project / "texsys.toml").read_text())["dependencies"]
  reference_library_entry(project, {"folder": source["path"], "digest": source["digest"], "entry": exported + "#Pump"})
  assert (project / "texsys.lock").read_bytes() == locked
  assert tomllib.loads((project / "texsys.toml").read_text())["dependencies"] == declared
  with pytest.raises(ProjectError, match="public exported"):
    reference_library_entry(project, {"folder": source["path"], "digest": source["digest"],
                                      "entry": "src/library/modules/hidden/hidden.sdl.json#Hidden"})
  with pytest.raises(ProjectError, match="contents changed"):
    reference_library_entry(project, {"folder": source["path"], "digest": "sha256:wrong", "entry": exported + "#Pump"})
  assert (project / "texsys.lock").read_bytes() == locked


@pytest.mark.parametrize("exports", [[], [""], "src/library", ["../outside"], ["assets"], ["src/missing"],
                                      ["src/library", "src/library/modules/pump"]])
def test_component_exports_require_explicit_existing_source_paths(tmp_path, exports):
  root = make_package(tmp_path / "equipment", exports=exports)
  with pytest.raises(ProjectError, match="Component export|Component packages"):
    package_metadata(root)


def test_general_package_cannot_claim_component_exports_and_links_are_rejected(tmp_path):
  root = make_package(tmp_path / "rules", kind="capability", exports=["src/library"])
  with pytest.raises(ProjectError, match="Only kind"):
    package_metadata(root)
  component = make_package(tmp_path / "equipment", exports=["src/library"])
  (component / "src/library/linked.sdl.json").symlink_to(root / "src/library/modules/pump/pump.sdl.json")
  with pytest.raises(ProjectError, match="symlinks"):
    package_metadata(component)


def test_component_export_cannot_claim_a_file_the_component_browser_does_not_support(tmp_path):
  root = make_package(tmp_path / "equipment", exports=["src/loose.sdl.json"])
  (root / "src/loose.sdl.json").write_text('{"schema_version": 1, "root": "Loose", "components": {"Loose": {}}}')
  with pytest.raises(ProjectError, match="public library entries"):
    package_metadata(root)
