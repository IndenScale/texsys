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
  assert len(list((root / "build/drawings/sheets").glob("*.svg"))) == 20
  old_dossier = file_digest(root / "build/review/aidc-discipline-review.json")
  drawing = root / "build/drawings/sheets/fws-network.svg"
  old_route_drawing = file_digest(drawing)

  _check_professional_delivery(root)
  _check_fws_network_delivery(root)
  _check_building_delivery(root)
  report = root / MODEL / "fws-network-study.json"
  before = _read(report)
  path = root / "src/fws-network.json"
  original = path.read_bytes()
  modified = _read(path)
  modified["legs"]["supply"]["bypass"]["east_x_m"] += 2
  write_json(path, modified)
  rerouted, records = _run(root)
  assert rerouted.status == "pass", rerouted.diagnostics
  assert records["dcr:design-checks"]["execution"] == "cached"
  assert records["dcr:layout"]["execution"] == "cached"
  assert records["sheet-it-hall"]["execution"] == "cached"
  assert records["sheet-fws-network"]["execution"] == "executed"
  assert records["review-package"]["status"] == "pass"
  assert file_digest(drawing) != old_route_drawing
  assert file_digest(root / "build/review/aidc-discipline-review.json") != old_dossier
  after = _read(report)
  assert after["route_digest"] != before["route_digest"]
  affected = "main-isolated:S-010"
  assert after["cases"][affected]["common_header_kpa"] > before["cases"][affected]["common_header_kpa"]
  assert records["sheet-fws-isolation"]["execution"] == "executed"

  # Restore exactly the original source bytes to exercise the original cache key.
  path.write_bytes(original)
  restored, records = _run(root)
  assert restored.status == "pass", restored.diagnostics
  assert records["dcr:fws-network-study"]["execution"] == "cached"
  assert _read(report) == before
  _check_load_failures(root, old_dossier)


def _check_fws_network_delivery(root):
  source = root / MODEL / "fws-network.json"
  model = _read(source)
  assert len(model["ports"]["plants"]) == 3
  assert len(model["ports"]["terminals"]) == 16
  assert len(model["segments"]) == 130
  assert len(model["valves"]) == 44
  assert len(model["penetrations"]) == 32
  assert sum(item["length_m"] for item in model["segments"].values()) == pytest.approx(688.79)
  fig = ET.parse(root / "build/drawings/views/fws-system.svg")
  links = {item["id"] for role in model["ports"].values() for owner in role.values() for item in owner.values()}
  symbols = [item.get("data-sdl-link") for item in fig.findall(f".//{SVG}line[@data-sdl-link]")]
  assert set(symbols) == links and len(symbols) == len(links) == 38
  assert {item.get("data-bypass") for item in fig.findall(f".//{SVG}path[@data-bypass]")} == {"supply", "return"}
  isolation = ET.parse(root / "build/drawings/views/fws-isolation.svg")
  assert {item.get("data-valve") for item in isolation.findall(f".//{SVG}path[@data-valve]")} == model["valves"].keys()
  roof = ET.parse(root / "build/drawings/views/fws-roof.svg")
  assert {item.get("data-reservation") for item in roof.findall(f".//{SVG}rect[@data-reservation]")} == (
    model["penetrations"].keys()
  )

  report = _read(root / MODEL / "fws-network-study.json")
  assert report["route_digest"] == file_digest(source)
  assert len(report["cases"]) == 32
  expected_total = 1440 / (998 * 4.18 * 10)
  header, = (key for key, record in model["segments"].items()
             if record["from"] == model["roots"]["supply"] and record["kind"] == "distribution")
  closed = report["cases"][f"main-isolated:{header}"]
  assert closed["closed_valves"] == model["segments"][header]["valves"]
  assert closed["pipes"][header]["isolated"] and closed["pipes"][header]["flow_m3_s"] == 0
  alternate = model["bypass"]["supply"][1]
  assert closed["pipes"][alternate]["flow_m3_s"] == pytest.approx(expected_total)
  assert closed["common_header_kpa"] > report["cases"]["normal"]["common_header_kpa"]
  pipe = model["segments"][alternate]
  spec = pipe["hydraulic_section"]
  speed = expected_total / (pi*spec["inside_m"]**2/4)
  reynolds = 998*speed*spec["inside_m"]/.001
  haaland = (-1.8 * log10((spec["roughness_m"] / (3.7 * spec["inside_m"]))**1.11 + 6.9 / reynolds))**-2
  expected_kpa = (haaland*pipe["length_m"]/spec["inside_m"] + pipe["minor_k"]) * 998*speed**2/2000
  for case in report["cases"].values():
    assert case["status"] == "pass"
    assert len(case["duty"]) == 2 and len(case["terminals"]) == 16
    assert case["continuity_max_m3_s"] < 1e-10
    assert case["loop_residual_max_kpa"] < 1e-7
    assert case["unserved_liquid_kw"] == 0
    pressures = case["node_pressure_relative_to_header_kpa"]
    for key, state in case["pipes"].items():
      physical = model["segments"][key]
      if not state["isolated"]:
        assert pressures[physical["from"]] - pressures[physical["to"]] == pytest.approx(state["loss_kpa"], abs=1e-7)
  assert closed["pipes"][alternate]["loss_kpa"] == pytest.approx(expected_kpa)
  assert any(case["flow_m3_s"] < 0 for case in closed["pipes"].values())
  assert {record["isolated_segment"] for record in report["cases"].values() if record["isolated_segment"]} == (
    {key for key, pipe in model["segments"].items() if pipe.get("valves")}
  )
  assert "CoolContainer1" not in report["cases"]["plant-off:CoolContainer1"]["duty"]
  assert report["cases"]["pump-off:CoolContainer1/Pump1"]["duty"]["CoolContainer1"]["pump"] == "CoolContainer1/Pump2"
  exposure = report["distribution_isolation_exposure"]
  assert exposure[header] == {"terminals": [], "unserved_liquid_kw": 0.}
  assert exposure["S-061"] == {"terminals": ["IT04"], "unserved_liquid_kw": 90.}


def _check_building_delivery(root):
  architecture = _read(root / MODEL / "architecture.json")
  assert {key: room["floor_area_m2"] for key, room in architecture["rooms"].items()} == pytest.approx({"FC-S": 252.8, "FC-N": 246.4})
  partition = architecture["walls"]["W-DIVIDE"]
  assert partition["minimum_m"] == pytest.approx([8, 15.9, .4])
  assert partition["maximum_m"] == pytest.approx([40, 16.1, 4.9])
  exit_record = architecture["doors"]["D-SW"]
  assert exit_record["ramp_slope"] == pytest.approx(.4/4.8)
  assert exit_record["discharge_m"] == pytest.approx([.5, 11.8, 0])
  shape = ET.parse(root / "build/drawings/views/architecture-l01.svg")
  assert {item.get("data-door") for item in shape.findall(f".//{SVG}path[@data-door]")} == architecture["doors"].keys()
  egress = _read(root / MODEL / "egress-model.json")
  assert not egress["issues"] and len(egress["routes"]) == 16
  assert egress["architecture_digest"] == file_digest(root / MODEL / "architecture.json")
  assert egress["routes"]["IT01"]["usable_exits"] == ["D-SW", "D-SE"]
  assert egress["routes"]["IT01"]["nearest_exit_m"] == pytest.approx(4.6)
  path = egress["routes"]["IT01"]["paths"][0]
  assert path["access_to_grade_m"] == pytest.approx(4.6 + .2 + 2*1.25 + sqrt(4.8**2 + .4**2))
  report = _read(root / MODEL / "fire-egress.json")
  assert report["status"] == "pass" and report["checked_routes"] == 32
  assert report["nearest_exit_max_m"] == pytest.approx(12.6)
  reserved = _read(root / MODEL / "building-coordination.json")["roof_firestop_reservations"]
  assert set(reserved) == _read(root / MODEL / "fws-network.json")["penetrations"].keys()
  assert {row["compartment"] for row in reserved.values()} == {"FC-S", "FC-N"}
  assert {row["assembly_state"] for row in reserved.values()} == {"pending-firestop-selection"}
  scene = root / "build/scenes/aidc-building-coordinated.usda"
  assert str(root) not in scene.read_text()
  stage = Usd.Stage.Open(str(scene))
  for name in ("/AIDC/Architecture/W_DIVIDE", "/AIDC/Architecture/Wall01", "/AIDC/FWSIsolation/IV_S_010_A", "/AIDC/Structure/C_1_B"):
    assert stage.GetPrimAtPath(name)
  assert not stage.GetPrimAtPath("/AIDC/Objects/Wall01")


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
  scene = root / "build/scenes/aidc-fws-network-coordinated.usda"
  assert str(root) not in scene.read_text()
  stage = Usd.Stage.Open(str(scene))
  assert not stage.GetPrimAtPath("/AIDC/Objects/Frame01")
  assert stage.GetPrimAtPath("/AIDC/Structure/C_1_B").GetAttribute("texsys:identity").Get() == "C-1-B"
  assert stage.GetPrimAtPath("/AIDC/FWSNetwork/S_001")
  assert stage.GetPrimAtPath("/AIDC/FWSIsolation/IV_S_010_A").GetAttribute("texsys:identity").Get() == "IV-S-010-A"

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
  for sheet in ("sheet-structure-l01", "sheet-power-one-line-a", "sheet-fws-network"):
    assert records[sheet]["execution"] == "cached"
  assert records["review-package"]["execution"] == "blocked"
  assert "AIDC_GRAVITY_BENDING" in failed.diagnostics
  assert "AIDC_ELECTRICAL_FAULT" in failed.diagnostics
  assert file_digest(root / "build/review/aidc-discipline-review.json") == old_dossier


def test_aidc_coordination_identifies_a_pipe_overlapping_the_frame(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/fws-network.json"
  config = _read(path)
  config["legs"]["supply"]["outlet_offset_m"] = .7
  write_json(path, config)
  result, records = _run(root, "dcr:fws-network-coordination", "sheet-fws-network")
  assert result.status == "fail"
  assert records["dcr:structure-model"]["status"] == "pass"
  assert records["sheet-fws-network"]["status"] == "pass"
  assert "AIDC_FWS_NETWORK_CLASH" in result.diagnostics
  assert "S-025 envelope intersects G-1-2-B/BottomFlange" in result.diagnostics


def test_aidc_fws_cannot_pass_when_two_plants_are_unavailable(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/fws-network-study.json"
  config = _read(path)
  config["unavailable_plants"] = ["CoolContainer1", "CoolContainer2"]
  write_json(path, config)
  result, records = _run(root, "review-package")
  assert result.status == "fail"
  assert records["sheet-fws-network"]["status"] == "pass"
  assert records["review-package"]["execution"] == "blocked"
  study = _read(root / MODEL / "fws-network-study.json")
  normal = study["cases"]["normal"]
  assert not normal["checks"]["dispatch"]
  assert list(normal["duty"]) == ["CoolContainer3"]
  assert not normal["duty"]["CoolContainer3"]["checks"]["cooling-rating"]
  assert study["cases"]["plant-off:CoolContainer3"]["checks"] == {"dispatch": False}


def test_aidc_fws_rejects_single_main_isolation_when_the_bypass_is_removed(tmp_path):
  root = _checkout(tmp_path)
  route = root / "src/fws-network.json"
  source = _read(route)
  del source["legs"]["supply"]["bypass"]
  write_json(route, source)
  result, records = _run(root, "review-package")
  assert result.status == "fail"
  assert records["sheet-fws-isolation"]["status"] == "pass"
  assert records["dcr:fws-network-coordination"]["status"] == "pass"
  assert records["review-package"]["execution"] == "blocked"
  study = _read(root / MODEL / "fws-network-study.json")
  assert study["cases"]["normal"]["status"] == "pass"
  outage = study["cases"]["main-isolated:S-010"]
  assert outage["status"] == "fail"
  assert outage["unserved_liquid_kw"] == 1440
  assert len(outage["unserved_terminals"]) == 16
  assert outage["checks"] == {"connected-duty": False, "valve-pair": True}


def test_aidc_egress_recalculates_ramp_and_marks_a_closed_exit_on_the_sheet(tmp_path):
  root = _checkout(tmp_path)
  assert _run(root, "dcr:fire-egress", "sheet-fire-egress")[0].status == "pass"
  source = root / "src/architecture.json"
  design = _read(source)
  design["ramp"]["run_length_m"] += .1
  write_json(source, design)
  rerouted, statuses = _run(root, "dcr:fire-egress", "sheet-fire-egress")
  assert rerouted.status == "pass"
  assert statuses["dcr:structure-model"]["execution"] == "cached"
  assert _read(root / MODEL / "architecture.json")["doors"]["D-SW"]["ramp_slope"] == pytest.approx(.4/4.9)
  design["doors"]["D-SW"]["available"] = False
  write_json(source, design)
  failed, statuses = _run(root, "review-package")
  assert failed.status == "fail"
  assert statuses["sheet-fire-egress"]["status"] == "pass"
  assert statuses["review-package"]["execution"] == "blocked"
  egress = _read(root / MODEL / "egress-model.json")
  assert egress["routes"]["IT01"]["usable_exits"] == ["D-SE"]
  assert egress["routes"]["IT09"]["usable_exits"] == ["D-NW", "D-NE"]
  figure = ET.parse(root / "build/drawings/views/fire-egress.svg")
  assert figure.find(f".//{SVG}line[@data-door='D-SW']").get("class") == "blocked"


def test_aidc_egress_uses_current_equipment_envelopes(tmp_path):
  root = _checkout(tmp_path)
  source = root / "src/coordination.json"
  design = _read(source)
  design["placements"]["IT01"]["offset_m"][1] += .2
  write_json(source, design)
  failed, statuses = _run(root, "dcr:fire-egress", "sheet-fire-egress")
  assert failed.status == "fail"
  assert statuses["dcr:layout"]["status"] == "pass"
  assert statuses["sheet-fire-egress"]["status"] == "pass"
  egress = _read(root / MODEL / "egress-model.json")
  assert egress["routes"]["IT01"]["usable_exits"] == []
  assert "IT01" in egress["routes"]["IT02"]["paths"][0]["blockers"]


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
