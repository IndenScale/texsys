"""Library structure references: manifests, assets, ports and boundary maps."""

import json
from pathlib import Path

import pytest

MODULES = Path(__file__).resolve().parents[1] / "src" / "library" / "modules"


def module_dirs():
  return sorted(p for p in MODULES.iterdir() if p.is_dir())


@pytest.mark.parametrize("directory", module_dirs(), ids=lambda p: p.name)
def test_module_assets_and_ports(directory):
  import tomllib

  manifest = tomllib.loads((directory / "module.toml").read_text(encoding="utf-8"))
  entry = directory / manifest["entry"]
  document = json.loads(entry.read_text(encoding="utf-8"))
  root = document["components"][document["root"]]
  for asset in manifest["assets"].values():
    assert (directory / asset["path"]).is_file(), asset["path"]
    assert asset["source"]
    assert asset["fidelity"]
  bindings = manifest.get("port_bindings", {})
  assert set(bindings) == set(root.get("ports", {}))
  specification = json.loads((directory / "assets/specification.json").read_text(encoding="utf-8"))
  assert specification["state"] == "assumed"
  assert set(specification["interfaces"]) == set(root.get("ports", {}))
  behavior = json.loads((directory / "assets/behavior.json").read_text(encoding="utf-8"))
  assert behavior["limitations"]
  for name, binding in root.get("behavior", {}).get("parameters", {}).items():
    assert binding["state"] == "assumed"
    assert binding["source"]


@pytest.mark.parametrize("directory", module_dirs(), ids=lambda p: p.name)
def test_composite_boundary_maps(directory):
  document = json.loads((directory / f"{directory.name}.sdl.json").read_text(encoding="utf-8"))
  root = document["components"][document["root"]]
  if "instances" not in root:
    pytest.skip("not a composite")
  specification = json.loads((directory / "assets/specification.json").read_text(encoding="utf-8"))
  instances = root["instances"]
  components = document["components"]
  for port, targets in specification.get("boundary", {}).items():
    assert port in root["ports"], port
    for target in targets:
      instance_name, port_name = target.split(".")
      assert instance_name in instances, target
      definition = components.get(instances[instance_name]["component"])
      if definition is not None:
        assert port_name in definition.get("ports", {}), target
  connection_ids = [c["id"] for c in root.get("connections", [])]
  assert len(connection_ids) == len(set(connection_ids))
  for net in root.get("nets", {}).values():
    assert set(net["connections"]) <= set(connection_ids)
