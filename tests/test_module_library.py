import json

from texsys.editing import plan_edit, read_design
from texsys.dependencies import lock_project
from texsys.formal import check_project
from texsys.project import initialize_project
from texsys.studio.design import design_index
from texsys.studio.library_sources import library_index


PUMP = "library/devices/pump.sdl.json"
GROUP = "library/modules/pump-group/pump-group.sdl.json"
STAGE = "library/modules/pump-group/library/modules/stage/stage.sdl.json"
ISOLATION = "library/modules/pump-group/library/modules/stage/library/cells/isolation.sdl.json"


def write_sdl(root, path, name, definition, imports=None):
  file = root / "src" / path
  file.parent.mkdir(parents=True, exist_ok=True)
  document = {"schema_version": 1, "root": name, "components": {name: definition}}
  if imports is not None:
    document["imports"] = imports
  file.write_text(json.dumps(document), encoding="utf-8")
  return file


def make_project(root):
  initialize_project(root)
  binding = {"value": 5, "unit": "kW", "state": "assumed", "source": "test"}
  write_sdl(root, PUMP, "Pump", {"ports": {"outlet": {"kind": "water", "direction": "out"}}})
  write_sdl(root, ISOLATION, "Isolation", {"ports": {"inlet": {"kind": "water", "direction": "in"}}})
  write_sdl(root, STAGE, "Stage", {
    "instances": {"pump": {"component": "SharedPump"}, "isolation": {"component": "LocalIsolation"}},
    "connections": [{"from": "pump.outlet", "to": "isolation.inlet"}],
  }, {"SharedPump": PUMP, "LocalIsolation": ISOLATION})
  write_sdl(root, GROUP, "PumpGroup", {
    "behavior": {"reference": "assets/group.mo", "state": "assumed", "source": "test", "parameters": {"capacity": binding}},
    "instances": {"stage": {"component": "LocalStage"}},
  }, {"LocalStage": STAGE})
  write_sdl(root, "system.sdl.json", "Station", {
    "instances": {
      "PG-01": {"component": "Pumps", "parameters": {"capacity": {**binding, "value": 10}}},
      "PG-02": {"component": "Pumps"},
    },
  }, {"Pumps": GROUP})


def test_private_library_and_recursive_occurrences(tmp_path):
  make_project(tmp_path)
  assert check_project(tmp_path).status == "pass"

  index = design_index(tmp_path, "src")
  definitions = index["definitions"]
  occurrences = definitions[index["root"]]["instances"]
  assert [(item["name"], item["definition"]) for item in occurrences] == [
    ("PG-01", f"src/{GROUP}#PumpGroup"), ("PG-02", f"src/{GROUP}#PumpGroup"),
  ]
  assert occurrences[0]["parameters"]["capacity"]["value"] == 10
  assert occurrences[1]["parameters"] == {}
  assert definitions[f"src/{GROUP}#PumpGroup"]["behavior"]["parameters"]["capacity"]["value"] == 5
  stage = definitions[occurrences[0]["definition"]]["instances"][0]["definition"]
  assert stage == f"src/{STAGE}#Stage"
  assert [item["definition"] for item in definitions[stage]["instances"]] == [
    f"src/{PUMP}#Pump", f"src/{ISOLATION}#Isolation",
  ]
  assert {item["definition"]: item["owner"] for item in index["library"]} == {
    f"src/{PUMP}#Pump": None,
    f"src/{GROUP}#PumpGroup": None,
    f"src/{STAGE}#Stage": f"src/{GROUP}#PumpGroup",
    f"src/{ISOLATION}#Isolation": f"src/{STAGE}#Stage",
  }

  # The structured editing path accepts an existing import alias.
  revision = read_design(tmp_path)["revision"]
  assert plan_edit(tmp_path, {"version": 1, "source": "system.sdl.json", "revision": revision,
                              "operations": [{"op": "add-instance", "id": "PG-03", "component": "Pumps"}]})["status"] == "pass"


def test_private_imports_and_recursive_cycles_are_rejected(tmp_path):
  make_project(tmp_path)
  write_sdl(tmp_path, "system.sdl.json", "Station", {
    "instances": {"illegal": {"component": "Hidden"}},
  }, {"Hidden": ISOLATION})
  write_sdl(tmp_path, STAGE, "Stage", {"instances": {"recursive": {"component": "Outer"}}}, {"Outer": GROUP})

  result = check_project(tmp_path)
  assert result.status == "fail"
  assert any(item.rule == "L014" and "private to another module" in item.message for item in result.diagnostics)
  assert any(item.rule == "L015" for item in result.diagnostics)
  index = design_index(tmp_path, "src")
  assert index["definitions"][index["root"]]["instances"][0]["definition"] is None


def test_locked_module_import_preserves_private_scope_and_detects_drift(tmp_path):
  library = tmp_path / "equipment"
  make_project(library)
  (library / "src/system.sdl.json").unlink()
  manifest = library / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('name = "equipment"', 'name = "equipment"\nversion = "0.1.0"'))
  project = tmp_path / "station"
  initialize_project(project)
  manifest = project / "texsys.toml"
  with manifest.open("a") as stream:
    stream.write('\n[dependencies]\nequipment = { path = "../equipment", version = "0.1.0" }\n')
  use = {"instances": {"Group": {"component": "Imported"}}}
  write_sdl(project, "system.sdl.json", "Station", use, {"Imported": f"@equipment/{GROUP}"})
  assert check_project(project).status == "fail"
  lock_project(project)
  assert check_project(project).status == "pass"
  index = library_index(project, "src", [str(library)])
  assert len(index["sources"]) == 1 and index["sources"][0]["locked"]
  group = index["definitions"][index["root"]]["instances"][0]["definition"]
  assert group == f"@equipment/{GROUP}#PumpGroup"
  assert index["definitions"][group]["instances"][0]["definition"] == f"@equipment/{STAGE}#Stage"
  write_sdl(project, "system.sdl.json", "Station", use, {"Imported": f"@equipment/{STAGE}"})
  assert any("private to another module" in item.message for item in check_project(project).diagnostics)
  write_sdl(project, "system.sdl.json", "Station", use, {"Imported": f"@equipment/{GROUP}"})
  pump = library / "src" / PUMP
  pump.write_text(pump.read_text() + "\n")
  assert any(item.file == "texsys.lock" for item in check_project(project).diagnostics)


def test_module_manifest_validates_files_and_appears_in_library_index(tmp_path):
  make_project(tmp_path)
  entry = "library/modules/valve/valve.sdl.json"
  module = write_sdl(tmp_path, entry, "Valve", {"ports": {"inlet": {"kind": "water", "direction": "in"}}})
  asset = module.parent / "assets" / "symbol.svg"
  asset.parent.mkdir()
  asset.write_text('<svg xmlns="http://www.w3.org/2000/svg"><g id="inlet"/></svg>')
  manifest = module.with_name("module.toml")
  contents = (
    'schema_version = 1\nname = "Valve"\nversion = "1.0.0"\nentry = "valve.sdl.json"\n'
    '[assets.symbol]\nkind = "symbol"\npath = "assets/symbol.svg"\nsource = "test"\nfidelity = "diagram"\n'
    '[port_bindings.inlet]\nsymbol = "inlet"\n'
  )
  manifest.write_text(contents)
  assert check_project(tmp_path).status == "pass"
  index = design_index(tmp_path, "src")
  package = index["definitions"][f"src/{entry}#Valve"]["module_package"]
  assert package["version"] == "1.0.0"
  assert package["port_bindings"] == {"inlet": {"symbol": "inlet"}}

  for invalid, message in (
    (contents.replace("assets/symbol.svg", "assets/../module.toml"), "canonical path"),
    (contents.replace("assets/symbol.svg", "assets/missing.svg"), "regular module file"),
    (contents.replace("port_bindings.inlet", "port_bindings.outlet"), "Invalid module port binding"),
    (contents.replace('symbol = "inlet"', 'drawing = "inlet"'), "Invalid target"),
  ):
    manifest.write_text(invalid)
    diagnostics = check_project(tmp_path).diagnostics
    assert any(item.rule == "M001" and message in item.message for item in diagnostics)
    index = design_index(tmp_path, "src")
    assert "module_package" not in index["definitions"][f"src/{entry}#Valve"]
    assert any(message in item["message"] for item in index["diagnostics"])
