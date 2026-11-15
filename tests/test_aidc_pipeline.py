"""Run the AIDC delivery graph through a geometry change and failed check."""

import json
from math import log10, pi, sqrt
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from pxr import Usd, UsdGeom
import pytest

from project_process import build_project
from texsys.dependencies import lock_project
from texsys.evidence import file_digest, write_json


REPO = Path(__file__).resolve().parents[1]
MODEL = Path("build/datacenter-reference/spatial")
SVG = "{http://www.w3.org/2000/svg}"


def _read(path):
  return json.loads(path.read_text())


def _checkout(tmp_path):
  sample = REPO / "examples/datacenter-reference-design"
  root = tmp_path / "aidc"
  shutil.copytree(sample, root, ignore=shutil.ignore_patterns("build"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(root)
  return root


def _run(root, *targets):
  result = build_project(root, targets=list(targets))
  return result, {record["id"]: record for record in _read(result.report)["targets"]}


def test_aidc_deliverables_bind_to_the_same_geometry_and_recompute_the_fws_head(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/fws-hydraulics.json"
  inputs = _read(path)
  inputs["head_available_kpa"] = 54.5  # Tighten the design limit to demonstrate a geometric failure.
  write_json(path, inputs)
  baseline, _ = _run(root)
  assert baseline.status == "pass", baseline.diagnostics

  site = _read(root / MODEL / "site.json")
  assert site["area_m2"] == 4480
  layout = _read(root / MODEL / "layout.json")
  assert len(layout["objects"]) == 65
  assert len(layout["nonspatial"]) == 7
  selected = layout["objects"]["IT01"]
  scene = Usd.Stage.Open(str(root / "build/scenes/aidc-coordination.usda"))
  prim = scene.GetPrimAtPath(selected["prim_path"])
  assert prim.GetAttribute("texsys:identity").Get() == "ReferenceDesign.IT01"
  transform = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
  assert tuple(transform.ExtractTranslation()) == pytest.approx((9, 8.8, .44))
  it = ET.parse(root / "build/drawings/views/it-hall.svg")
  rectangles = it.findall(f".//{SVG}rect[@data-instance]")
  assert len(rectangles) == 16
  rectangle = it.find(f".//{SVG}rect[@data-instance='IT01']")
  assert float(rectangle.get("x")) == pytest.approx(selected["minimum_m"][0])
  assert float(rectangle.get("y")) == pytest.approx(-selected["maximum_m"][1])

  sheet = ET.parse(root / "build/drawings/sheets/it-hall.svg")
  image = sheet.find(f".//{SVG}image")
  assert float(image.get("width")) == pytest.approx(215)
  dossier = _read(root / "build/review/aidc-discipline-review.json")
  assert dossier["evidence_complete"]
  assert dossier["input_digest"] == _read(baseline.report)["input_digest"]
  assert dossier["engineering_acceptance"] == "pending-responsible-review"
  assert len(list((root / "build/drawings/sheets").glob("*.svg"))) == 14
  old_dossier = file_digest(root / "build/review/aidc-discipline-review.json")
  old_route_drawing = file_digest(root / "build/drawings/sheets/fws.svg")

  _check_professional_delivery(root)
  report = root / MODEL / "fws-hydraulics.json"
  before = _read(report)
  assert before["terminal_design_it_kw"] == 120
  assert before["liquid_heat_kw"] == pytest.approx(90)
  assert before["energy_balance_kw"] == pytest.approx(90)
  density, diameter, viscosity = inputs["density_kg_m3"], inputs["inside_diameter_m"], inputs["viscosity_pa_s"]
  speed = before["flow_m3_s"] / (pi * diameter**2 / 4)
  reynolds = density * speed * diameter / viscosity
  haaland = (-1.8 * log10((inputs["roughness_m"] / (3.7 * diameter))**1.11 + 6.9 / reynolds))**-2
  independent_kpa = 20 + (haaland * (46.98857142857143 + 47.23142857142858) / diameter + 14 * 0.9) * (
    density * speed**2 / 2000
  )
  assert before["required_kpa"] == pytest.approx(independent_kpa)
  original_route = _read(root / "src/fws-routing.json")
  modified = _read(root / "src/fws-routing.json")
  modified["legs"]["supply"]["tier_m"] += 2
  write_json(root / "src/fws-routing.json", modified)
  rerouted, records = _run(root)
  assert rerouted.status == "fail"
  assert records["dcr:design-checks"]["execution"] == "cached"
  assert records["dcr:layout"]["execution"] == "cached"
  assert records["sheet-it-hall"]["execution"] == "cached"
  assert records["sheet-fws"]["status"] == "pass"
  assert records["review-package"]["execution"] == "blocked"
  assert file_digest(root / "build/drawings/sheets/fws.svg") != old_route_drawing
  assert file_digest(root / "build/review/aidc-discipline-review.json") == old_dossier
  after = _read(report)
  assert after["route_evidence"]["digest"] != before["route_evidence"]["digest"]
  assert after["legs"]["supply"]["extracted_length_m"] - before["legs"]["supply"]["extracted_length_m"] == pytest.approx(4)
  assert before["head_margin_kpa"] > 0 > after["head_margin_kpa"]
  assert "AIDC_FWS_HEAD" in rerouted.diagnostics

  # Restore exactly the original source bytes to exercise the original cache key.
  original = REPO / "examples/datacenter-reference-design/src/fws-routing.json"
  assert _read(original) == original_route
  (root / "src/fws-routing.json").write_bytes(original.read_bytes())
  restored, records = _run(root)
  assert restored.status == "pass", restored.diagnostics
  assert records["dcr:fws-hydraulics"]["execution"] == "cached"
  assert _read(report) == before
  _check_load_failures(root, old_dossier)


def _check_professional_delivery(root):
  frame = _read(root / MODEL / "structure.json")
  calculation = _read(root / MODEL / "structure-gravity.json")
  members = frame["members"]
  assert len(members) == 52
  assert members["G-1-2-B"]["supports"] == ["C-1-B", "C-2-B"]
  girder = calculation["checks"]["G-1-2-B"]["response"]["service"]
  span = 8
  udl = 1.4 * 8 + members["G-1-2-B"]["physical_volume_m3"] * 77 / span
  assert girder["moment_kn_m"] == pytest.approx(udl * span**2 / 8)
  assert girder["reaction_start_kn"] == pytest.approx(udl * span / 2)
  assert calculation["equilibrium"]["service"]["residual_kn"] == pytest.approx(0, abs=1e-9)
  pad = calculation["checks"]["P-1-B"]
  assert pad["peak_bearing_kpa"] == pytest.approx((pad["service_column_kn"] + pad["own_weight_kn"]) / 1.7**2)
  drawing = ET.parse(root / "build/drawings/views/structure-section-b.svg")
  assert drawing.find(f".//{SVG}rect[@data-member='P-1-B']") is not None
  scene = root / "build/scenes/aidc-coordinated-detailed.usda"
  assert str(root) not in scene.read_text()
  stage = Usd.Stage.Open(str(scene))
  assert not stage.GetPrimAtPath("/AIDC/Objects/Frame01")
  assert stage.GetPrimAtPath("/AIDC/Structure/C_1_B").GetAttribute("texsys:identity").Get() == "C-1-B"
  assert stage.GetPrimAtPath("/AIDC/FWS/Supply")

  topology = _read(root / MODEL / "power-topology.json")["trains"]
  power = _read(root / MODEL / "power-study.json")["trains"]
  for side, path in topology.items():
    plan = ET.parse(root / f"build/drawings/views/power-one-line-{side.lower()}.svg")
    wires = plan.findall(f".//{SVG}polyline[@data-link]")
    assert {wire.get("data-link") for wire in wires} == {edge["id"] for edge in path["edges"]}
    assert len(wires) == len(path["edges"])
    breakers = {symbol.get("data-protection-link") for symbol in plan.findall(f".//{SVG}rect[@data-protection-link]")}
    assert breakers == {link for response in power[side]["fault_cases"].values() for link in response["devices"]}
    assert len(power[side]["fault_cases"]) == 44
    assert len(path["shared_feeds"]) == 7
    assert power[side]["critical_kw"] == 2144
    assert power[side]["input_kw"] == pytest.approx(2144/.96 + 497 + 40)
    assert power[side]["duty"][f"Busway{side}1"]["required"] == pytest.approx(536 / (sqrt(3)*.4*.9))
    assert power[side]["autonomy_min"] == pytest.approx(600*60/2144)
    transformer_z = .06 * .4**2 / 3.15
    winding = transformer_z * complex(1, 7) / sqrt(50)
    utility = complex(.06+.015, .30+.06) * (.4/10)**2
    assert power[side]["fault_cases"]["utility:main"]["current_max_ka"] == pytest.approx(
      1.05*.4/(sqrt(3)*abs(winding+utility))
    )


def _check_load_failures(root, old_dossier):
  loads = root / "src/structure-loads.json"
  structure = _read(loads)
  structure["roof_live_kpa"] = 15
  write_json(loads, structure)
  settings = root / "src/electrical-study.json"
  electrical = _read(settings)
  electrical["breakers"]["terminal"]["interrupt_ka"] = 4
  write_json(settings, electrical)
  failed, records = _run(root)
  assert failed.status == "fail"
  for check in ("dcr:structure-gravity", "dcr:power-study"):
    assert records[check]["status"] == "fail"
  for sheet in ("sheet-structure-l01", "sheet-power-one-line-a", "sheet-fws"):
    assert records[sheet]["execution"] == "cached"
  assert records["review-package"]["execution"] == "blocked"
  assert "AIDC_GRAVITY_BENDING" in failed.diagnostics
  assert "AIDC_ELECTRICAL_FAULT" in failed.diagnostics
  assert file_digest(root / "build/review/aidc-discipline-review.json") == old_dossier


def test_aidc_coordination_identifies_a_pipe_overlapping_the_frame(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/fws-routing.json"
  config = _read(path)
  config["legs"]["supply"]["port_clearance_m"] = .7
  write_json(path, config)
  result, records = _run(root, "dcr:coordination-model", "sheet-fws")
  assert result.status == "fail"
  assert records["dcr:structure-model"]["status"] == "pass"
  assert records["sheet-fws"]["status"] == "pass"
  assert "AIDC_FWS_FRAME_CLASH" in result.diagnostics
  assert "G-1-2-A" in result.diagnostics


def test_aidc_geometry_rejects_missing_and_clashing_placements(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/coordination.json"
  design = _read(path)
  it02 = design["placements"].pop("IT02")
  write_json(path, design)
  result, records = _run(root, "sheet-it-hall", "sheet-parcel")
  assert result.status == "indeterminate"
  assert records["dcr:layout"]["open_items"][0]["id"] == "unbound-instances"
  assert records["sheet-it-hall"]["execution"] == "blocked"
  assert records["sheet-parcel"]["status"] == "pass"
  design["placements"]["IT02"] = {**it02, **design["placements"]["IT01"]}
  write_json(path, design)
  result, records = _run(root, "sheet-it-hall", "sheet-parcel")
  assert result.status == "fail"
  assert records["sheet-it-hall"]["execution"] == "blocked"
  assert records["sheet-parcel"]["execution"] == "cached"
  assert "AIDC_ASSET_CLASH" in result.diagnostics


def test_aidc_sheet_refuses_scale_that_does_not_fit_the_paper(tmp_path):
  root = _checkout(tmp_path)
  sheets = root / "assets/sheets"
  layout, title = sheets / "parcel.sheet.json", sheets / "parcel.title.json"
  sheet_doc, metadata = _read(layout), _read(title)
  sheet_doc["views"]["main"]["scale"] = metadata["scale"] = "1:100"
  write_json(layout, sheet_doc)
  write_json(title, metadata)
  result, records = _run(root, "sheet-parcel")
  assert result.status == "error"
  assert records["plan-parcel"]["status"] == "pass"
  assert "view at 1:100 exceeds its paper region" in result.diagnostics
  assert not (root / "build/drawings/sheets/parcel.svg").exists()
