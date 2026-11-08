import copy
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest
from typer.testing import CliRunner

from texsys.cli import app
from texsys.execution import build_project
from texsys.formal import lint_project
from texsys_diagram import apply_overlay, check_document, digest, resolve_symbol
CATALOG_VERSION = "test-fixture/1"
CATALOG = {"family:ChilledWaterPump": {"id": "test-pump", "ports": {"inlet": {"kind": "chilled-water", "direction": "in"}, "outlet": {"kind": "chilled-water", "direction": "out"}}}}

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples/hvac-system-schematic"
runner = CliRunner()


@pytest.fixture
def project(tmp_path):
  root = tmp_path / "project"
  shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("build"))
  (root / "build").mkdir()
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))
  assert runner.invoke(app, ["lock", "-p", str(root)]).exit_code == 0
  return root


def load(root, filename="system.json"):
  report = json.loads((root / "build/build.json").read_text())
  view = report["targets"][0]["scope"].get("view", "schematic")
  return json.loads((root / "build/hvac" / view / filename).read_text())


def mutate(root, update):
  path = root / "src/system.sdl.json"
  document = json.loads(path.read_text())
  update(document)
  path.write_text(json.dumps(document, indent=2, sort_keys=True)+"\n")


def test_schematic_has_both_loops_all_ports_and_deterministic_exports(project):
  sources = {p: p.read_bytes() for p in project.rglob("*") if p.is_file() and "build" not in p.parts}
  result = build_project(project)
  assert result.status == "pass"
  document = load(project)
  assert len(document["nodes"]) == 5
  assert len(document["edges"]) == 6
  assert sum(len(n["ports"]) for n in document["nodes"]) == 12
  assert document["deliverable"] == "schematic"
  assert document["spatial_state"] == "connectivity-only"
  assert document["behavior_state"] == "defined"
  assert check_document(document) == []
  assert all(n["symbol"]["semantic_ports_complete"] for n in document["nodes"])
  assert {e["medium"] for e in document["edges"]} == {"chilled-water", "cooling-water"}
  svg = ET.parse(project / "build/hvac/schematic/system.svg")
  assert len(svg.findall(".//{http://www.w3.org/2000/svg}polyline")) == 6
  drawio = ET.parse(project / "build/hvac/schematic/system.drawio")
  cells = {o.attrib["id"]: o for o in drawio.findall(".//UserObject")}
  for node in document["nodes"]:
    assert cells[node["id"]].attrib["semantic_ref"] == node["semantic_ref"]
  for edge in document["edges"]:
    cell = cells[edge["id"]].find("mxCell")
    assert cell.attrib["source"] == edge["source"]
    assert cell.attrib["target"] == edge["target"]
  first = {p.relative_to(project): p.read_bytes() for p in (project / "build").rglob("*") if p.is_file()}
  assert build_project(project).status == "pass"
  assert first == {p.relative_to(project): p.read_bytes() for p in (project / "build").rglob("*") if p.is_file()}
  assert all(p.read_bytes() == data for p, data in sources.items())


def test_build_recreates_network_projection_from_direct_file_edit(project):
  def add_networks(d):
    d["schema_version"] = 2
    root = d["components"][d["root"]]
    for index, connection in enumerate(root["connections"]):
      connection["id"] = f"link-{index}"
    root["nets"] = {
      "CHW": {"kind": "chilled-water", "connections": ["link-0", "link-1", "link-2"]},
      "CW": {"kind": "cooling-water", "connections": ["link-3", "link-4", "link-5"]},
    }

  mutate(project, add_networks)
  shutil.rmtree(project / "build")
  args = ["build", "-p", str(project), "--scope", '{"view":"system"}']
  built = runner.invoke(app, args)
  assert built.exit_code == 0, built.output
  diagram = load(project)
  edges = {edge["id"]: edge for edge in diagram["edges"]}
  assert {net["id"] for net in diagram["nets"]} == {"net:ColdStation.CHW", "net:ColdStation.CW"}
  for net in diagram["nets"]:
    assert len(net["edges"]) == 3
    assert {edges[edge]["medium"] for edge in net["edges"]} == {net["kind"]}
    assert diagram["source_map"][net["id"]]["file"] == "src/system.sdl.json"
  first = (project / "build/hvac/system/system.json").read_bytes()
  shutil.rmtree(project / "build")
  assert runner.invoke(app, args).exit_code == 0
  assert (project / "build/hvac/system/system.json").read_bytes() == first


def test_missing_behavior_degrades_without_fake_schematic(project):
  mutate(project, lambda d: d["components"]["Chiller"].pop("behavior"))
  assert build_project(project).status == "pass"
  check = load(project, "system-check.json")
  assert check["deliverable"] == "system-diagram"
  assert check["behavior_state"] == "missing"
  assert check["spatial_state"] == "connectivity-only"
  assert "系统图" in load(project)["title"]
  assert build_project(project, profile="engineering").status == "indeterminate"


def test_missing_behavior_resource_is_visible(project):
  (project / "assets/Chiller.mo").unlink()
  assert build_project(project).status == "pass"
  assert load(project)["missing_behavior"] == ["ColdStation.chiller"]


def test_unknown_parameter_and_explicit_failure_have_distinct_states(project):
  def unknown(d):
    p = d["components"]["Chiller"]["behavior"]["parameters"]["cooling_kw"]
    p.pop("value")
    p["state"] = "unknown"
  mutate(project, unknown)
  assert lint_project(project).status == "pass"
  assert build_project(project).status == "pass"
  assert load(project)["behavior_state"] == "missing"
  mutate(project, lambda d: d["components"]["Chiller"]["behavior"]["parameters"]["cooling_kw"].update(state="failed"))
  assert build_project(project).status == "fail"
  assert load(project)["behavior_state"] == "failed"


def test_instance_binding_and_reference_hash_are_projected(project):
  def update(d):
    binding = copy.deepcopy(d["components"]["Chiller"]["behavior"]["parameters"]["cooling_kw"])
    binding.update(value=120, source="Design assumption B")
    d["components"]["ColdStation"]["instances"]["chiller"]["parameters"] = {"cooling_kw": binding}
  mutate(project, update)
  assert build_project(project).status == "pass"
  doc = load(project)
  chiller = next(n for n in doc["nodes"] if n["component"] == "Chiller")
  assert chiller["parameters"]["cooling_kw"]["value"] == 120
  assert chiller["parameters"]["cooling_kw"]["value"] == 120
  assert doc["behavior_resources"]["assets/Chiller.mo"]["digest"].startswith("sha256:")


@pytest.mark.parametrize("change", [
  lambda d: d["components"]["Chiller"]["behavior"]["parameters"]["cooling_kw"].update(value=None),
  lambda d: d["components"]["Chiller"]["behavior"]["parameters"]["cooling_kw"].update(state="unknown"),
  lambda d: d["components"]["Chiller"]["behavior"].update(state=[]),
  lambda d: d["components"]["ColdStation"]["instances"]["chiller"].update(parameters={"invented": {"value": 1, "unit": "kW", "state": "known", "source": "test"}}),
])
def test_invalid_behavior_bindings_fail_static_checks(project, change):
  mutate(project, change)
  assert lint_project(project).status == "fail"


def test_symbol_resolution_does_not_lose_changed_ports():
  request = {"ports": {"inlet": {"kind": "air", "direction": "in"}, "control": {"kind": "signal", "direction": "in"}},
             "family": "ChilledWaterPump", "model": "pump-X", "configuration_digest": "config-A"}
  result = resolve_symbol(request, CATALOG, CATALOG_VERSION)
  assert result["level"] == "neutral"
  assert result["actual"]["ports"] == request["ports"]
  assert any(r["reason"] == "port-signature-mismatch" for r in result["rejected"])
  exact = {"id": "specific-pump", "version": "1", "shape": "pump", "ports": request["ports"]}
  catalog = {**CATALOG, "model:pump-X:config-A": exact}
  assert resolve_symbol(request, catalog, "test")["level"] == "model"
  assert resolve_symbol({**request, "configuration_digest": "config-B"}, catalog, "test")["level"] == "neutral"


def test_tampered_projection_and_routes_are_detected(project):
  build_project(project)
  document = load(project)
  del document["edges"][0]
  assert any(d["rule"] == "D_COMPLETENESS" for d in check_document(document))
  document = load(project)
  document["nodes"][0]["ports"].pop()
  assert any(d["rule"] == "D_COMPLETENESS" for d in check_document(document))
  document = load(project)
  document["edges"][0]["points"][0]["x"] += 10
  assert any(d["rule"] == "D_ROUTE" for d in check_document(document))
  document = load(project)
  document["nodes"][1]["x"] = document["nodes"][0]["x"]
  document["nodes"][1]["y"] = document["nodes"][0]["y"]
  assert any(d["rule"] == "D_OVERLAP" for d in check_document(document))


def test_overlay_preserves_explicit_positions_and_routes_and_rejects_semantics(project):
  build_project(project)
  document = load(project)
  node = document["nodes"][0]
  overlay = {"schema_version": 1, "view": "schematic", "source": "Reviewer layout", "revision": "1",
             "nodes": {node["id"]: {k: node[k] for k in ("x", "y")}},
             "edges": {e["id"]: {"points": e["points"]} for e in document["edges"]}}
  assert apply_overlay(document, overlay) == []
  assert check_document(document) == []
  overlay["nodes"][node["id"]]["semantic_ref"] = "forged"
  assert apply_overlay(document, overlay)[0]["rule"] == "D_OVERLAY"
  del overlay["nodes"][node["id"]]["semantic_ref"]
  overlay["nodes"]["missing"] = {"x": 0, "y": 0}
  assert apply_overlay(document, overlay)[0]["rule"] == "D_OVERLAY"


def test_overlay_translation_is_preserved_on_rebuild(project):
  build_project(project)
  document = load(project)
  overlay = {"schema_version": 1, "view": "schematic", "source": "Test layout", "revision": "2",
             "nodes": {n["id"]: {"x": n["x"]+100, "y": n["y"]+100} for n in document["nodes"]},
             "edges": {e["id"]: {"points": [{"x": p["x"]+100, "y": p["y"]+100} for p in e["points"]]} for e in document["edges"]}}
  (project / "assets/overlay.json").write_text(json.dumps(overlay))
  for _ in range(2):
    assert build_project(project, scope={"overlay": "assets/overlay.json"}).status == "pass"
    actual = load(project)
    assert actual["overlay"]["digest"] == digest(overlay)
    for node in actual["nodes"]:
      assert {k: node[k] for k in ("x", "y")} == overlay["nodes"][node["id"]]


def test_engineering_requires_exact_approval_and_invalidates_on_change(project):
  assert build_project(project, profile="engineering").status == "indeterminate"
  check = load(project, "system-check.json")
  approval = {"schema_version": 1, "diagram_digest": check["visual_review"]["diagram_digest"],
              "decision": "approved", "reviewer": "Test fixture only", "source": "Automated test; not a human approval"}
  (project / "assets/approval.json").write_text(json.dumps(approval))
  assert build_project(project, profile="review").status == "pass"
  assert load(project, "system-check.json")["visual_review"]["diagram_digest"] == approval["diagram_digest"]
  assert build_project(project, profile="engineering", scope={"approval": "assets/approval.json"}).status == "pass"
  mutate(project, lambda d: d["components"]["Chiller"]["behavior"]["parameters"]["cooling_kw"].update(value=101))
  assert build_project(project, profile="engineering", scope={"approval": "assets/approval.json"}).status == "fail"
  assert any(d["rule"] == "D_APPROVAL" for d in load(project, "system-check.json")["diagnostics"])


def test_missing_backend_is_indeterminate_not_silent_fallback(project, monkeypatch):
  monkeypatch.setenv("TEXSYS_ELK_ROOT", str(project / "absent-backend"))
  assert build_project(project).status == "indeterminate"
  report = json.loads((project / "build/build.json").read_text())
  assert report["targets"][0]["open_items"][0]["id"] == "diagram-backend"
  assert report["artifacts"] == []


def test_system_view_uses_same_network_and_invalid_scope_is_rejected(project):
  assert build_project(project, scope={"view": "system"}).status == "pass"
  assert load(project)["deliverable"] == "system-diagram"
  assert len(load(project)["edges"]) == 6
  assert build_project(project, scope={"include": "chiller"}).status == "fail"


def test_nested_subsystems_are_rejected_instead_of_silently_flattened(project):
  mutate(project, lambda d: d["components"]["Chiller"].update(instances={"internal": {"component": "CoolingLoad"}}))
  assert build_project(project).status == "error"
  report = json.loads((project / "build/build.json").read_text())
  assert "expansion/boundary" in report["targets"][0]["diagnostics"][0]["message"]
