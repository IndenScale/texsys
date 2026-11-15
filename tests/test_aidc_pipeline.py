"""Run the AIDC delivery graph through a geometry change and failed check."""

import json
from math import exp, log10, pi, sqrt
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from pxr import Usd, UsdGeom
import pytest

from project_process import build_project
from texsys.dependencies import lock_project
from texsys.evidence import file_digest, write_json
from texsys_datacenter_reference.fws_transient import Circuit


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
  assert len(list((root / "build/drawings/sheets").glob("*.svg"))) == 33
  old_dossier = file_digest(root / "build/review/aidc-discipline-review.json")
  drawing = root / "build/drawings/sheets/fws-network.svg"
  old_route_drawing = file_digest(drawing)

  _check_professional_delivery(root)
  _check_fws_network_delivery(root)
  _check_building_delivery(root)
  _check_telecom_delivery(root)
  _check_fire_systems_delivery(root)
  _check_transient_delivery(root)
  _check_buffer_delivery(root)
  _check_cw_delivery(root)
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
  thermal = _read(root / MODEL / "fws-thermal-parameters.json")
  assert thermal["extracted_volume_m3"] == pytest.approx(7.346129201434385 + 4*pi*.16**2/4)
  assert records["dcr:fws-transient"]["execution"] == "executed"

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
  assert len(model["segments"]) == 133
  assert len(model["valves"]) == 44
  assert len(model["penetrations"]) == 32
  assert sum(item["length_m"] for item in model["segments"].values()) == pytest.approx(686.078)
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


def _check_telecom_delivery(root):
  geometry = root / MODEL / "telecom.json"
  design = _read(geometry)
  assert len(design["allocation"]) == 256
  assert len({row["rack"] for row in design["allocation"].values()}) == 128
  assert len({row["rack_port"] for row in design["allocation"].values()}) == 256
  assert len({row["cabinet_port"] for row in design["allocation"].values()}) == 256
  first, last = (design["allocation"][f"A-IT01-Rack{number:02}"] for number in (1, 8))
  assert (first["first_core"], first["last_core"]) == (1, 2)
  assert (last["first_core"], last["last_core"]) == (15, 16)
  cable = design["planes"]["A"]["cables"]["FO-A-IT01"]
  centreline = (3.76-2.2) + (8.46-5.75) + (10-9.35) + (11.2-8.46) + (3.76-3.34)
  assert cable["routed_m"] == pytest.approx(centreline)
  report = _read(root / MODEL / "telecom-study.json")
  assert report["source_digest"] == file_digest(geometry)
  assert report["cables"]["FO-A-IT01"]["remaining_db"] == pytest.approx(6.5-3-4*.35-2*.1-(centreline*1.1+12)*.4/1000)
  assert report["switching"]["B"]["uplink_demand_gbps"] == 128*40
  assert len(report["scenarios"]) == 145
  assert all(case["pass"] for case in report["scenarios"].values())
  assert report["scenarios"]["off:TA-E02"]["single_racks"] == 128
  assert not report["scenarios"]["off:TA-E02"]["failed_racks"]
  section = _read(root / MODEL / "telecom-coordination.json")
  assert len(section["wall_openings"]) == 4
  assert section["wall_openings"]["OP-TA-S03-W-DIVIDE"]["opening_size_m"] == pytest.approx([.33, .19])
  fig = ET.parse(root / "build/drawings/views/telecom-a.svg")
  assert len(fig.findall(f".//{SVG}rect[@data-opening]")) == 2
  system = ET.parse(root / "build/drawings/views/telecom-system.svg")
  assert {item.get("data-cable") for item in system.findall(f".//{SVG}line[@data-cable]")} == {
    name for plane in design["planes"].values() for name in plane["cables"]
  }
  path = root / "build/scenes/aidc-telecom-coordinated.usda"
  assert str(root) not in path.read_text()
  stage = Usd.Stage.Open(str(path))
  partition = stage.GetPrimAtPath("/AIDC/Architecture/W_DIVIDE")
  assert partition.IsA(UsdGeom.Xform) and len(partition.GetChildren()) == 7
  assert stage.GetPrimAtPath("/AIDC/Telecom/TA_S03").GetAttribute("texsys:identity").Get() == "TA-S03"


def _check_fire_systems_delivery(root):
  model = root / MODEL / "fire-systems.json"
  geometry = _read(model)
  study = _read(root / MODEL / "fire-systems-study.json")
  assert study["source_digest"] == file_digest(model)
  assert len(geometry["pods"]) == 16
  first = geometry["pods"]["IT01"]
  assert first["controller"] == "IT01/FirePanel1"
  assert set(first["power_sdl_links"]) == {"power_a", "power_b"}
  assert len(first["nozzles"]) == len(first["detectors"]) == 2
  assert first["pipes"]["AP-IT01-01"]["length_m"] == pytest.approx(1.07)
  for room, ring in geometry["rings"].items():
    reference = study["loops"][room]
    assert reference["addresses"] == 20
    assert len(reference["cells"]) == 8
    assert len(reference["continuity"]) == len(ring["runs"])+1 == 38
    assert all(row["pass"] for row in reference["continuity"].values())
    assert all(row["line_trouble"] for key, row in reference["continuity"].items() if key != "configured")
  assert study["loops"]["FC-S"]["cells"]["SD-FC-S-11"]["worst_plan_distance_m"] == pytest.approx(sqrt(4**2+(7.9/4)**2))
  cases = study["cause_effect"]["cases"]
  assert len(cases) == 14 and all(row["pass"] for row in cases.values())
  assert [row["t_s"] for row in cases["dual_with_feedback"]["trace"]] == [0, 30]
  assert not cases["abort_at_deadline"]["permission"]
  assert not cases["feedback_revoked"]["permission"]
  assert cases["local_confirmed_with_supervisory_open"]["trouble"]
  assert cases["local_confirmed_with_supervisory_open"]["permission"]
  assert not cases["supervisory_open"]["permission"]
  figure = ET.parse(root / "build/drawings/views/fire-alarm.svg")
  assert len(figure.findall(f".//{SVG}rect[@data-detection-cell]")) == 16
  assert figure.find(f".//{SVG}circle[@data-fire-device='SD-FC-S-11']") is not None
  scene = root / "build/scenes/aidc-fire-systems-coordinated.usda"
  assert str(root) not in scene.read_text()
  stage = Usd.Stage.Open(str(scene))
  assert stage.GetPrimAtPath("/AIDC/FireSystems/Pods/IT01/NZ_IT01_1").GetAttribute("texsys:identity").Get() == "NZ-IT01-1"
  assert stage.GetPrimAtPath("/AIDC/FireSystems/Hall/FACP_FC_S")
  assert stage.GetPrimAtPath("/AIDC/Telecom/TA_S03")


def _check_transient_delivery(root):
  source = root / MODEL / "fws-thermal-parameters.json"
  report = _read(source)
  assert report["route_digest"] == file_digest(root / MODEL / "fws-network.json")
  assert report["extracted_volume_m3"] == pytest.approx(7.346129201434385)
  assert report["extracted_capacity_kj_k"] == pytest.approx(report["extracted_volume_m3"]*998*4.18)
  assert len(report["segments"]) == 133
  assert set(report["buffers"]) == {"BT-01", "BT-02", "BT-03"}
  assert report["buffers"]["BT-01"]["working_volume_m3"] == pytest.approx(pi*.4**2*1.2)
  assert report["buffers"]["BT-01"]["water_capacity_kj_k"] == pytest.approx(pi*.4**2*1.2*998*4.18)
  segment = report["segments"]["S-010"]
  assert segment["water_volume_m3"] == pytest.approx(segment["length_m"]*pi*.16**2/4)
  assert segment["normal_residence_s"] == pytest.approx(segment["water_volume_m3"]/abs(segment["normal_flow_m3_s"]))
  dynamics = _read(root / MODEL / "fws-transient.json")
  assert dynamics["source_digests"][source.relative_to(root / "build").as_posix()] == file_digest(source)
  assert dynamics["equilibrium_max_initial_error_c"] < 1e-7
  assert list(dynamics["scenarios"]) == ["plant-off:CoolContainer1", "plant-off:CoolContainer2"]
  for case in dynamics["scenarios"].values():
    assert len(case["terminals"]) == 16
    assert case["governing_terminal"] == "IT01"
    assert case["standby_plant"] == "CoolContainer3"
    assert case["terminals"]["IT01"]["comparison_peak_c"] == pytest.approx(27.2171, abs=.01)
    assert "BT-03" in case["active_buffers"] and len(case["active_buffers"]) == 2
    assert case["pass"]
    for analysis in ("coarse_energy", "fine_energy"):
      balance = case[analysis]
      assert abs(balance["residual_kj"]) < 1e-5
      assert balance["input_kj"] == 1440*360
      assert balance["stored_change_kj"] == pytest.approx(balance["pipe_stored_change_kj"]+balance["buffer_stored_change_kj"])
  view = ET.parse(root / "build/drawings/views/fws-transient-plan.svg")
  assert len(view.findall(f".//{SVG}rect[@data-peak-c]")) == 16
  assert view.find(f".//{SVG}rect[@data-governing='IT01']") is not None


def _check_buffer_delivery(root):
  route = _read(root / MODEL / "fws-network.json")
  buffer = route["buffers"]["BT-01"]
  assert buffer["sdl_supply_link"] == route["ports"]["plants"]["CoolContainer1"]["supply"]["id"]
  assert route["segments"][buffer["lead_in"]]["to"] == buffer["from"]
  assert route["segments"][buffer["lead_out"]]["from"] == buffer["to"]
  expected = 3*(.0172593991811374/.020)**2
  report = _read(root / MODEL / "fws-network-study.json")
  case = report["cases"]["normal"]
  assert case["buffers"]["BT-01"]["loss_kpa"] == pytest.approx(expected)
  assert case["duty"]["CoolContainer1"]["buffer_kpa"] == pytest.approx(expected)
  assert case["node_pressure_relative_to_header_kpa"][buffer["from"]] - case["node_pressure_relative_to_header_kpa"][buffer["to"]] == pytest.approx(expected)
  svg = ET.parse(root / "build/drawings/views/fws-buffers.svg")
  assert len(svg.findall(f".//{SVG}circle[@data-buffer]")) == 3
  scene = Usd.Stage.Open(str(root / "build/scenes/aidc-fire-systems-coordinated.usda"))
  assert scene.GetPrimAtPath("/AIDC/FWSBuffers/BT_01").IsA(UsdGeom.Mesh)
  assert scene.GetPrimAtPath("/AIDC/FWSNetwork/S_003_IN")


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


def test_aidc_telecom_reroutes_and_rejects_lost_diversity_and_optical_budget(tmp_path):
  root = _checkout(tmp_path)
  assert _run(root)[0].status == "pass"
  baseline = _read(root / MODEL / "telecom-study.json")
  geometry = root / "src/telecom.json"
  source = _read(geometry)
  source["planes"]["A"]["offset_m"][0] -= 1
  write_json(geometry, source)
  changed, statuses = _run(root)
  assert changed.status == "pass"
  assert statuses["sheet-it-hall"]["execution"] == "cached"
  assert statuses["sheet-telecom-a"]["execution"] == "executed"
  rerouted = _read(root / MODEL / "telecom-study.json")
  a, b = rerouted["cables"]["FO-A-IT01"], baseline["cables"]["FO-A-IT01"]
  assert a["basis_length_m"] - b["basis_length_m"] == pytest.approx(1.1)
  assert b["remaining_db"] - a["remaining_db"] == pytest.approx(1.1*.4/1000)
  assert rerouted["cables"]["FO-B-IT01"] == baseline["cables"]["FO-B-IT01"]
  input_file = root / "src/telecom-study.json"
  input_data = _read(input_file)
  input_data["unavailable_components"] = ["TA-E02"]
  write_json(input_file, input_data)
  failed, statuses = _run(root)
  assert failed.status == "fail" and statuses["review-package"]["execution"] == "blocked"
  assert statuses["sheet-telecom-a"]["execution"] == "cached"
  result = _read(root / MODEL / "telecom-study.json")
  assert result["scenarios"]["normal"]["single_racks"] == 128
  assert len(result["scenarios"]["off:TB-E02"]["failed_racks"]) == 128
  input_data["unavailable_components"] = []
  input_data["optics"]["connector_loss_db"] = .86
  write_json(input_file, input_data)
  failed, statuses = _run(root)
  assert failed.status == "fail" and statuses["review-package"]["execution"] == "blocked"
  assert "AIDC_TELECOM_LOSS" in failed.diagnostics


def test_aidc_telecom_rejects_tray_near_the_column(tmp_path):
  root = _checkout(tmp_path)
  source = root / "src/telecom.json"
  config = _read(source)
  config["planes"]["A"]["side_inset_m"] = .26
  write_json(source, config)
  failed, statuses = _run(root, "dcr:telecom-coordination", "dcr:telecom-study")
  assert failed.status == "fail"
  assert statuses["dcr:telecom-study"]["status"] == "pass"
  assert "AIDC_TELECOM_CLASH" in failed.diagnostics
  assert "C-1-B" in failed.diagnostics
  assert not (root / "build/scenes/aidc-telecom-coordinated.usda").exists()


def test_aidc_fire_systems_reject_missing_coverage_and_loop_breaks(tmp_path):
  root = _checkout(tmp_path)
  design = root / "src/fire-systems.json"
  original = design.read_bytes()
  model = _read(design)
  target = "SD-FC-S-11"
  model["omitted_hall_detectors"] = [target]
  write_json(design, model)
  result, statuses = _run(root)
  assert result.status == "fail" and statuses["review-package"]["execution"] == "blocked"
  assert statuses["sheet-fire-alarm"]["status"] == "pass"
  shape = ET.parse(root / "build/drawings/views/fire-alarm.svg")
  assert shape.find(f".//{SVG}rect[@data-detection-cell='{target}']").get("class") == "removed"
  assert shape.find(f".//{SVG}circle[@data-fire-device='{target}']") is None
  design.write_bytes(original)
  ring = _read(root / MODEL / "fire-systems.json")["rings"]["FC-S"]
  cut = [key for key, row in ring["runs"].items() if target in row["nodes"]]
  assert len(cut) == 2
  checks = root / "src/fire-systems-study.json"
  study = _read(checks)
  study["unavailable_ring_runs"] = cut
  write_json(checks, study)
  result, statuses = _run(root, "dcr:fire-systems-study", "sheet-fire-alarm")
  assert result.status == "fail" and statuses["sheet-fire-alarm"]["status"] == "pass"
  loop = _read(root / MODEL / "fire-systems-study.json")["loops"]["FC-S"]["continuity"]["configured"]
  assert loop["line_trouble"] and loop["missing_addresses"] == [target]


def test_aidc_fire_systems_reject_missing_feedback_and_tray_conflict(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/fire-systems-study.json"
  config = _read(path)
  config["release_permissives"].remove("damper_closed")
  write_json(path, config)
  result, statuses = _run(root)
  assert result.status == "fail" and statuses["review-package"]["execution"] == "blocked"
  assert statuses["sheet-fire-cause-effect"]["execution"] == "blocked"
  cause = _read(root / MODEL / "fire-systems-study.json")["cause_effect"]["cases"]["no_damper_closed"]
  assert cause["permission"] and not cause["expected_permission"] and not cause["pass"]
  geometry = root / "src/fire-systems.json"
  design = _read(geometry)
  design["loop"]["panel_offset_y_m"] = 1.08
  write_json(geometry, design)
  result, statuses = _run(root, "dcr:fire-systems-coordination")
  assert result.status == "fail" and statuses["dcr:telecom-coordination"]["status"] == "pass"
  assert "FA-FC-S-27 envelope overlaps TA-R02-01" in result.diagnostics


def test_fws_transport_matches_a_single_loop_with_known_transit_and_buffer():
  route = {"nodes": ["S-P-PORT", "S-IT01-PORT", "R-IT01-PORT", "R-P-PORT"], "segments": {
    "supply": {"leg": "supply", "from": "S-P-PORT", "to": "S-IT01-PORT"},
    "return": {"leg": "return", "from": "R-IT01-PORT", "to": "R-P-PORT"},
  }}
  flow, volume = .001, .005
  parameters = {"fluid": {"density_kg_m3": 1000, "cp_kj_kg_k": 4, "delta_t_k": 10},
                "segments": {name: {"water_volume_m3": volume} for name in route["segments"]}}
  case = {"terminals": {"IT01": {"flow_m3_s": flow, "heat_kw": 40}},
          "duty": {"P": {"flow_m3_s": flow, "cooling_available_kw": 80}},
          "pipes": {name: {"flow_m3_s": flow} for name in route["segments"]}}
  settings = {"supply_setpoint_c": 20, "standby_supply_c": 24, "standby_return_c": 30,
              "standby_delay_s": 12, "standby_ramp_s": 2, "duration_s": 16}
  model = Circuit(route, parameters, case, case, settings)
  t, temperature, balance = model.run(settings, "P", .05)
  assert t[99] == pytest.approx(4.975) and temperature[99, 0] == pytest.approx(20)
  assert t[100] == pytest.approx(5.025) and temperature[100, 0] == pytest.approx(30)
  assert abs(balance["residual_kj"]) < 1e-8
  route["nodes"].append("S-B-OUT")
  route["segments"]["supply"]["from"] = "S-B-OUT"
  route["buffers"] = {"BT": {"from": "S-P-PORT", "to": "S-B-OUT"}}
  parameters["buffers"] = {"BT": {"working_volume_m3": .002}}
  case["buffers"] = {"BT": {"flow_m3_s": flow}}
  inactive = {**case, "buffers": {"BT": {"flow_m3_s": 0.}}}
  model = Circuit(route, parameters, case, inactive, settings)
  t, temperature, balance = model.run(settings, "P", .05)
  assert t[120] == pytest.approx(6.025)
  # The parcel took five seconds through the supply pipe after the buffer outlet.
  expected_average = 30 + (24-30)*exp(-1/2)*(1-exp(-.05/2))/(.05/2)
  assert temperature[120, 0] == pytest.approx(expected_average)
  assert abs(balance["residual_kj"]) < 1e-8


def test_aidc_transient_blocks_package_but_draws_temperature_exceedances(tmp_path):
  root = _checkout(tmp_path)
  assert _run(root)[0].status == "pass"
  path = root / "src/fws-transient.json"
  config = _read(path)
  config["standby_delay_s"] = 48
  write_json(path, config)
  result, records = _run(root)
  assert result.status == "fail"
  assert records["dcr:fws-thermal-parameters"]["execution"] == "cached"
  assert records["dcr:fws-network-study"]["execution"] == "cached"
  assert records["sheet-fws-transient-plan"]["status"] == "pass"
  assert records["review-package"]["execution"] == "blocked"
  study = _read(root / MODEL / "fws-transient.json")
  assert study["engineering_status"] == "fail"
  governing = study["scenarios"]["plant-off:CoolContainer1"]["terminals"]["IT01"]
  assert governing["comparison_peak_c"] > 28.5
  assert not governing["checks"]["temperature"]
  assert governing["first_above_limit_s"] > 0
  fig = ET.parse(root / "build/drawings/views/fws-transient-plan.svg")
  assert fig.find(f".//{SVG}rect[@data-pod='IT01']").get("class") == "pod-fail"


def test_aidc_buffer_rejects_undersizing_excess_pressure_and_pad_clash(tmp_path):
  root = _checkout(tmp_path)
  route = root / "src/fws-network.json"
  baseline = _read(route)
  reduced = {**baseline, "buffers": {**baseline["buffers"], "bore_m": .68}}
  write_json(route, reduced)
  result, records = _run(root)
  assert result.status == "fail" and records["review-package"]["execution"] == "blocked"
  assert records["dcr:fws-network-study"]["status"] == "pass"
  assert records["sheet-fws-buffers"]["status"] == "pass"
  assert "AIDC_FWS_BUFFER_VOLUME" in result.diagnostics
  trans = _read(root / MODEL / "fws-transient.json")
  assert trans["buffers"]["BT-01"]["working_volume_m3"] == pytest.approx(pi*.34**2*1.2)
  resistive = {**baseline, "buffers": {**baseline["buffers"], "rated_drop_kpa": 300}}
  write_json(route, resistive)
  failed, records = _run(root, "dcr:fws-network-study", "dcr:fws-transient-check")
  assert failed.status == "fail" and records["dcr:fws-transient-check"]["execution"] == "blocked"
  assert "plant-duty" in failed.diagnostics
  clash = {**baseline, "buffers": {**baseline["buffers"], "setback_from_plant_port_m": .48}}
  write_json(route, clash)
  failed, _ = _run(root, "dcr:fws-network-coordination")
  assert failed.status == "fail"
  assert "BT-01/foundation envelope intersects CoolContainer1" in failed.diagnostics


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


def _check_cw_delivery(root):
  network = _read(root / MODEL / "cw-network.json")
  layout = _read(root / MODEL / "layout.json")["objects"]
  system = _read(root / "src/system.sdl.json")
  assert set(network["routes"]) == set(system["components"][system["root"]]["nets"]["Cw"]["connections"])
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-cooling-water-coordinated.usda"))
  transforms = UsdGeom.XformCache()
  for key, row in network["routes"].items():
    for name, point in ((row["from"], row["points_m"][0]), (row["to"], row["points_m"][-1])):
      equipment, port = name.split(".")
      assert point == pytest.approx(layout[equipment]["ports_m"][port])
      flange = stage.GetPrimAtPath(f"{layout[equipment]['prim_path']}/Asset/Ports/{port}")
      assert tuple(transforms.GetLocalToWorldTransform(flange).ExtractTranslation()) == pytest.approx(point, abs=1e-6)
    mesh = UsdGeom.Mesh.Get(stage, f"/AIDC/CoolingWater/{key.replace('-', '_')}")
    assert mesh.GetPrim().GetAttribute("texsys:identity").Get() == key
    assert len(mesh.GetPointsAttr().Get()) == 16*len(row["points_m"])
  assert not _read(root / MODEL / "cw-coordination.json")["issues"]
  drawing = ET.parse(root / "build/drawings/views/cw-plan.svg")
  assert {element.get("data-connection") for element in drawing.iter() if element.get("data-connection")} == set(network["routes"])
  sheet = ET.parse(root / "build/drawings/sheets/cw-section.svg")
  assert sheet.getroot().get("data-drawing-number") == "AIDC-M-013"
  assert float(sheet.find(f".//{SVG}image").get("width")) == pytest.approx(266.4)

  fws = _read(root / MODEL / "fws-network-study.json")
  study = _read(root / MODEL / "cw-study.json")
  assert len(study["cases"]) == len(fws["cases"])+6 == 38
  assert study["engineering_result"] == "pass"
  for name, case in study["cases"].items():
    dispatch = fws["cases"][case["fws_dispatch"]]
    assert {key: row["evaporator_kw"] for key, row in case["duty"].items()} == {
      key: row["heat_kw"] for key, row in dispatch["duty"].items()
    }
    if name.startswith("cw-"):
      assert len(case["unavailable"]) == 1
      assert len(case["duty"]) == 2
      assert all(case["unavailable"][0] not in (network["cells"][cell]["pump"], network["cells"][cell]["tower"])
                 for cell in case["duty"])
  plant, key = "CoolContainer1", "link-055"
  hydraulics = study["hydraulics"][plant]
  properties = study["parameters"]["fluid"]
  density, viscosity = properties["density_kg_m3"], properties["viscosity_pa_s"]
  q = 260/3600
  pipe, route = network["pipe"], network["routes"][key]
  diameter = pipe["inside_m"]
  velocity = q / (pi*diameter**2/4)
  reynolds = density*velocity*diameter/viscosity
  factor = 1/(-1.8*log10((pipe["roughness_m"]/(3.7*diameter))**1.11 + 6.9/reynolds))**2
  loss = (factor*route["length_m"]/diameter + route["elbows"]*pipe["elbow_k"])*density*velocity**2/2000
  assert hydraulics["loss_by_object_kpa"][key] == pytest.approx(loss)
  assert hydraulics["required_kpa"] == pytest.approx(120.176062)
  normal = study["cases"]["normal"]["duty"][plant]
  rejection = 720 + 720/5.5 + 120.176062*q
  assert normal["rejection_kw"] == pytest.approx(rejection)
  assert (normal["hot_c"]-normal["cold_c"])*properties["cp_j_kg_k"]*density*q == pytest.approx(1000*rejection)


def test_cw_route_and_operating_changes_recompute_and_block_review(tmp_path):
  root = _checkout(tmp_path)
  baseline, _ = _run(root)
  assert baseline.status == "pass", baseline.diagnostics
  network = root / "src/cw-network.json"
  conditions = root / "src/cw-study.json"
  report = root / MODEL / "cw-study.json"
  drawing = root / "build/drawings/sheets/cw-section.svg"
  initial, initial_drawing = _read(report), file_digest(drawing)

  cfg = _read(network)
  original = network.read_bytes()
  for point in cfg["waypoints_from_plant_origin_m"]["hot"]:
    if point[2] == 2.35:
      point[2] = 2.55
  write_json(network, cfg)
  result, targets = _run(root)
  assert result.status == "pass"
  assert targets["dcr:fws-network-study"]["execution"] == "cached"
  assert targets["sheet-it-hall"]["execution"] == "cached"
  assert targets["sheet-cw-section"]["execution"] == "executed"
  assert initial_drawing != file_digest(drawing)
  routed = _read(report)
  assert routed["route_digest"] != initial["route_digest"]
  assert routed["hydraulics"]["CoolContainer1"]["required_kpa"] > initial["hydraulics"]["CoolContainer1"]["required_kpa"]

  for point in cfg["waypoints_from_plant_origin_m"]["hot"]:
    if point[2] == 2.55:
      point[2] = 3
  write_json(network, cfg)
  result, targets = _run(root)
  assert result.status == "fail"
  assert targets["sheet-cw-plan"]["status"] == "pass"
  assert targets["dcr:cw-coordination"]["status"] == "fail"
  assert targets["review-package"]["execution"] == "blocked"
  assert any(row["objects"] == ["link-055/5", "link-056/4"] for row in _read(root / MODEL / "cw-coordination.json")["issues"])

  network.write_bytes(original)
  settings = _read(conditions)
  original_study = conditions.read_bytes()
  settings["unavailable"] = ["CwPump1"]
  write_json(conditions, settings)
  result, targets = _run(root)
  assert result.status == "fail"
  assert targets["dcr:cw-check"]["status"] == "fail"
  assert targets["dcr:cw-coordination"]["status"] == "pass"
  assert targets["sheet-cw-system"]["status"] == "pass"
  assert _read(report)["cases"]["normal"]["failed_checks"] == [["CoolContainer1", "equipment-available"]]
  assert _read(report)["cases"]["cw-pump-off:CwPump1"]["status"] == "pass"

  conditions.write_bytes(original_study)
  original_cfg = _read(network)
  original_cfg["pipe"]["inside_m"] = .12
  write_json(network, original_cfg)
  result, targets = _run(root)
  assert result.status == "fail"
  assert targets["dcr:cw-study"]["status"] == "pass"
  assert targets["sheet-cw-system"]["status"] == "pass"
  assert targets["review-package"]["execution"] == "blocked"
  assert ["CoolContainer1", "pump-pressure-reserve"] in _read(report)["cases"]["normal"]["failed_checks"]
  assert ["CoolContainer1", "velocity"] in _read(report)["cases"]["normal"]["failed_checks"]

  network.unlink()
  result, targets = _run(root, "dcr:cw-network", "sheet-it-hall")
  assert result.status == "indeterminate"
  assert targets["dcr:cw-network"]["open_items"][0]["id"] == "missing-source"
  assert targets["sheet-it-hall"]["status"] == "pass"
  network.write_bytes(original)
  result, targets = _run(root)
  assert result.status == "pass"
  assert targets["review-package"]["execution"] == "cached"
