"""Run the AIDC delivery graph through a geometry change and failed check."""

import json
from math import log10, pi
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
  assert len(list((root / "build/drawings/sheets").glob("*.svg"))) == 10
  old_dossier = file_digest(root / "build/review/aidc-discipline-review.json")
  old_route_drawing = file_digest(root / "build/drawings/sheets/fws.svg")

  report = root / MODEL / "fws-hydraulics.json"
  before = _read(report)
  assert before["terminal_design_it_kw"] == 120
  assert before["liquid_heat_kw"] == pytest.approx(90)
  assert before["energy_balance_kw"] == pytest.approx(90)
  density, diameter, viscosity = inputs["density_kg_m3"], inputs["inside_diameter_m"], inputs["viscosity_pa_s"]
  speed = before["flow_m3_s"] / (pi * diameter**2 / 4)
  reynolds = density * speed * diameter / viscosity
  haaland = (-1.8 * log10((inputs["roughness_m"] / (3.7 * diameter))**1.11 + 6.9 / reynolds))**-2
  independent_kpa = 20 + (haaland * (47.28857142857143 + 47.23142857142858) / diameter + 14 * 0.9) * (
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
