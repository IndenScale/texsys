"""Run the AIDC delivery graph through a geometry change and failed check."""

import csv
import json
from math import dist, exp, log10, pi, sqrt
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
  assert len(layout["objects"]) == 67
  assert len(layout["nonspatial"]) == 6
  selected = layout["objects"]["IT01"]
  scene = Usd.Stage.Open(str(root / "build/scenes/aidc-coordination.usda"))
  prim = scene.GetPrimAtPath(selected["prim_path"])
  assert prim.GetAttribute("texsys:identity").Get() == "ReferenceDesign.IT01"
  transform = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
  assert tuple(transform.ExtractTranslation()) == pytest.approx((8.3, 7.26, .44))
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
  compiled_sheets = sorted((root / "build/drawings/sheets").glob("*.svg"))
  drawing_numbers = {ET.parse(path).getroot().get("data-drawing-number") for path in compiled_sheets}
  assert len(compiled_sheets) == len(drawing_numbers) == 108 and None not in drawing_numbers
  old_dossier = file_digest(root / "build/review/aidc-discipline-review.json")
  drawing = root / "build/drawings/sheets/fws-network.svg"
  old_route_drawing = file_digest(drawing)
  original_volume = _read(root / MODEL / "fws-thermal-parameters.json")["extracted_volume_m3"]

  _check_professional_delivery(root)
  _check_power_installation(root)
  _check_facility_power(root)
  _check_roof_delivery(root)
  _check_lateral_delivery(root)
  _check_roof_transfer_delivery(root)
  _check_equipment_foundation_delivery(root)
  _check_exit_access_delivery(root)
  _check_emergency_lighting_delivery(root)
  _check_lighting_distribution_delivery(root)
  _check_earthing_delivery(root)
  _check_lightning_delivery(root)
  _check_fuel_delivery(root)
  _check_generator_yard_delivery(root)
  _check_cad_delivery(root)
  _check_storm_delivery(root)
  _check_fws_network_delivery(root)
  _check_building_delivery(root)
  _check_telecom_delivery(root)
  _check_fire_systems_delivery(root)
  _check_transient_delivery(root)
  _check_buffer_delivery(root)
  _check_cw_delivery(root)
  _check_wet_utility_delivery(root)
  _check_pod_delivery(root)
  _check_tcs_delivery(root)
  _check_air_delivery(root)
  report = root / MODEL / "fws-network-study.json"
  before = _read(report)
  path = root / "src/fws-network.json"
  original = path.read_bytes()
  cad_root = root / "build/drawings/cad"
  original_cad = file_digest(cad_root / "facilities-water.dxf")
  modified = _read(path)
  modified["legs"]["supply"]["bypass"]["east_x_m"] += 2
  write_json(path, modified)
  rerouted, records = _run(root)
  assert rerouted.status == "pass", rerouted.diagnostics
  assert records["dcr:design-checks"]["execution"] == "cached"
  assert records["dcr:layout"]["execution"] == "cached"
  assert records["sheet-it-hall"]["execution"] == "cached"
  assert records["sheet-fws-network"]["execution"] == "executed"
  assert records["cad-facilities-water"]["execution"] == "executed"
  assert records["cad-structure-l01"]["execution"] == "cached"
  assert file_digest(cad_root / "facilities-water.dxf") != original_cad
  assert records["review-package"]["status"] == "pass"
  assert file_digest(drawing) != old_route_drawing
  assert file_digest(root / "build/review/aidc-discipline-review.json") != old_dossier
  after = _read(report)
  assert after["route_digest"] != before["route_digest"]
  affected = "main-isolated:S-010"
  assert after["cases"][affected]["common_header_kpa"] > before["cases"][affected]["common_header_kpa"]
  assert records["sheet-fws-isolation"]["execution"] == "executed"
  thermal = _read(root / MODEL / "fws-thermal-parameters.json")
  assert thermal["extracted_volume_m3"] == pytest.approx(original_volume + 4*pi*.16**2/4)
  assert records["dcr:fws-transient"]["execution"] == "executed"

  # Restore exactly the original source bytes to exercise the original cache key.
  path.write_bytes(original)
  restored, records = _run(root)
  assert restored.status == "pass", restored.diagnostics
  assert records["dcr:fws-network-study"]["execution"] == "cached"
  assert file_digest(cad_root / "facilities-water.dxf") == original_cad
  assert _read(report) == before
  _check_load_failures(root, old_dossier)


def _check_cad_delivery(root):
  import ezdxf

  folder = root / "build/drawings/cad"
  views = ("site","architecture-l01","structure-l01","structure-footings","facilities-water","generator-yard")
  documents = {}
  for view in views:
    path = folder / f"{view}.dxf"
    mapping = _read(folder / f"{view}-cad.json")
    assert mapping["dxf_digest"] == file_digest(path)
    doc = ezdxf.readfile(path)
    documents[view] = doc.modelspace()
    assert doc.units == 4 and doc.dxfversion == "AC1027"
    audit = doc.audit()
    assert not audit.has_errors and not audit.has_fixes
    assert len(doc.modelspace()) == mapping["entity_count"]
    for entity in doc.modelspace():
      row = mapping["entity_map"][entity.dxf.handle]
      assert row["layer"] == entity.dxf.layer
      assert [tag.value for tag in entity.get_xdata("TEXSYS_AIDC")] == [row["id"],row["model"],row["pointer"]]
      assert mapping["model_digests"][row["model"]] == file_digest(root / "build" / row["model"])
  dimensions = [d.get_measurement() for d in documents["site"].query("DIMENSION")]
  assert dimensions[:2] == [80_000,56_000]
  assert len(documents["architecture-l01"].query("ARC")) == 8
  assert len(documents["architecture-l01"].query('LWPOLYLINE[layer=="A-IT-POD"]')) == 16
  net = _read(root / MODEL / "fws-network.json")["segments"]
  routes = documents["facilities-water"].query("POLYLINE")
  assert len(routes) == len(net)
  for route in routes:
    key = route.get_xdata("TEXSYS_AIDC")[0].value
    for point,coordinate in zip(route.points(),net[key]["points_m"],strict=True):
      assert tuple(point) == pytest.approx([value*1000 for value in coordinate])


def _check_fws_network_delivery(root):
  source = root / MODEL / "fws-network.json"
  model = _read(source)
  assert len(model["ports"]["plants"]) == 3
  assert len(model["ports"]["terminals"]) == 16
  assert len(model["segments"]) == 133
  assert len(model["valves"]) == 44
  assert len(model["penetrations"]) == 32
  assert sum(item["length_m"] for item in model["segments"].values()) == pytest.approx(683.198)
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
  assert {key: room["floor_area_m2"] for key, room in architecture["rooms"].items()} == pytest.approx({"FC-S": 300.8, "FC-N": 294.4})
  partition = architecture["walls"]["W-DIVIDE"]
  assert partition["minimum_m"] == pytest.approx([8, 15.9, .4])
  assert partition["maximum_m"] == pytest.approx([40, 16.1, 4.9])
  exit_record = architecture["doors"]["D-SW"]
  assert exit_record["ramp_slope"] == pytest.approx(.4/4.8)
  assert exit_record["discharge_m"] == pytest.approx([.5, 11.54, 0])
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
  centreline = (3.76-2.2) + (8.46-5.75) + (8.5-7.81) + (10.5-8.46) + (3.76-3.34)
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
  assert study["loops"]["FC-S"]["cells"]["SD-FC-S-11"]["worst_plan_distance_m"] == pytest.approx(sqrt(4**2+(9.4/4)**2))
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
  lengths = _read(root / MODEL / "fws-network.json")["segments"]
  volume = sum(row["length_m"]*pi*row["hydraulic_section"]["inside_m"]**2/4 for row in lengths.values())
  assert report["extracted_volume_m3"] == pytest.approx(volume)
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
  self_weight = members["G-1-2-B"]["physical_volume_m3"] * 77 / span
  points = [(row["x_m"]-8, row["force_kn"]["service"]) for row in calculation["roof_reactions"]["G-1-2-B"]]
  reaction = self_weight * span/2 + sum(force*(span-location)/span for location, force in points)
  station = girder["moment_at_m"]
  moment = reaction*station-self_weight*station**2/2-sum(force*max(station-location, 0) for location, force in points)
  assert girder["reaction_start_kn"] == pytest.approx(reaction)
  assert girder["moment_kn_m"] == pytest.approx(moment)
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
  facility_faults = _read(root / MODEL / "facility-power-study.json")["fault_cases"]
  for side, path in topology.items():
    plan = ET.parse(root / f"build/drawings/views/power-one-line-{side.lower()}.svg")
    wires = plan.findall(f".//{SVG}polyline[@data-link]")
    assert {wire.get("data-link") for wire in wires} == {edge["id"] for edge in path["edges"]}
    assert len(wires) == len(path["edges"])
    breakers = {symbol.get("data-protection-link") for symbol in plan.findall(f".//{SVG}rect[@data-protection-link]")}
    subjects = [*power[side]["fault_cases"].values(), *(row for row in facility_faults.values() if row["feed_train"] == side)]
    assert breakers == {link for response in subjects for link in response["devices"]}
    assert len(power[side]["fault_cases"]) == 44
    assert set(path["shared_feeds"]) == {"ControlPanel"}
    assert len(path["cell_feeds"]) == 3
    assert all(len(branches) == 2 for branches in path["cell_feeds"].values())
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


def _check_power_installation(root):
  routes = _read(root / MODEL / "power-routing.json")
  coordination = _read(root / MODEL / "power-coordination.json")
  installation = _read(root / MODEL / "power-installation.json")
  fault = _read(root / MODEL / "power-study.json")
  assert coordination["runs_checked"] == 54
  assert not coordination["issues"]
  assert len(coordination["penetrations"]) == 32
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-power-coordinated.usda"))
  roof = Usd.Stage.Open(str(root / "build/scenes/aidc-power-roof.usda"))
  assert roof.GetPrimAtPath("/AIDC/Equipment/BuswayA1")
  assert roof.GetPrimAtPath("/AIDC/Equipment/IT01")
  for side in ("A", "B"):
    train = routes["trains"][side]
    assert len(train["busways"]) == 4
    view = ET.parse(root / f"build/drawings/views/power-roof-{side.lower()}.svg")
    assert len(view.findall(f".//{SVG}rect[@data-busway]")) == 4
    assert len(view.findall(f".//{SVG}circle[@data-route]")) == 17
    case, = (row for row in installation["cases"].values() if row["operating_train"] == side)
    last_bus = f"Busway{side}4"
    row = train["busways"][last_bus]
    meter = sum(train["segments"][key]["length_m"] for key in row["segments"])
    assert row["length_m"] == pytest.approx(meter)
    terminal = row["terminal_paths"]["IT16"]
    first, last = train["segments"][terminal["segment"]]["points_m"]
    assert first[:2] == last[:2] == pytest.approx([39.32, 21.83 if side == "A" else 25])
    plant = 536000 / (sqrt(3)*400*.9)
    r = .072*meter / 2000
    x = .080*meter / 2000
    impedance = fault["trains"][side]["fault_cases"]["battery:IT16"]["path_impedance_ohm"]
    assert impedance[row["link"]] == pytest.approx({"r": r, "x": x})
    assert impedance[terminal["link"]]["r"] == pytest.approx(.095*31.02/1000 + .150*(first[2]-last[2])/1000)
    assert case["busways"][last_bus]["feed_drop_v"] == pytest.approx(sqrt(3)*plant*(r*.9 + x*sqrt(1-.9**2)))
    for scene in (stage, roof):
      prim = scene.GetPrimAtPath(f"/AIDC/PowerRoutes/{side}/{side}_IT16")
      assert list(prim.GetAttribute("texsys:sdlLinks").Get()) == [terminal["link"]]
      assert scene.GetPrimAtPath(f"/AIDC/PowerRoutes/{side}/{side}_IT16_Building")


def _check_facility_power(root):
  topology = _read(root / MODEL / "power-topology.json")
  installation = _read(root / MODEL / "facility-power.json")
  fit = _read(root / MODEL / "facility-power-coordination.json")
  study = _read(root / MODEL / "facility-power-study.json")
  assert not fit["issues"] and not study["issues"]
  assert len(installation["circuits"]) == 15
  assert set(installation["circuits"]["link-109"]["from"].split(".")) == {"CoolContainer1", "pump_power_out"}
  assert min(row["clearance_m"] for row in fit["ab_crossings"]) >= .25
  assert min(fit["burial_cover_m"].values()) >= .56
  assert len(study["states"]) == 78
  assert len(study["fault_cases"]) == 66
  side = topology["trains"]["B"]
  feeder = side["facility_feeds"]["CoolContainer1"]
  circuit = installation["circuits"][feeder]
  expected = .095*circuit["length_m"]/2/1000
  fault = study["fault_cases"]["B:utility:transfer:link-109"]
  assert fault["path_impedance_ohm"][feeder]["r"] == pytest.approx(expected)
  assert fault["checks"]["selectivity"]
  assert side["cell_feeds"]["CoolContainer1"]["CwPump1"] in fault["path_impedance_ohm"]
  good = study["states"]["A:train-only:normal"]
  assert good["all_cooling_kw"] == pytest.approx(1440)
  assert good["plant_kw"] == pytest.approx(sum(row["demand_kw"] for row in good["running_cells"].values()))
  assert set(study["states"]["A:cell-transfer-off:plant-off:CoolContainer1"]["running_cells"]) == {
    "CoolContainer2", "CoolContainer3"
  }
  command = study["states"]["B:common-aux-off:normal"]
  assert command["all_cooling_kw"] == pytest.approx(1440)
  assert not command["common_command_available"]
  assert not command["generator_restart_credited"]
  scene = Usd.Stage.Open(str(root / "build/scenes/aidc-facility-power-coordinated.usda"))
  geometry = scene.GetPrimAtPath("/AIDC/FacilityPower/LOCAL/LOCAL_CwPump1_05")
  assert list(geometry.GetAttribute("texsys:sdlLinks").Get()) == ["link-109"]


def test_aidc_facility_motor_protection_and_failed_gate_keep_views(tmp_path):
  root = _checkout(tmp_path)
  config = root / "src/facility-power.json"
  values = _read(config)
  values["study"]["switches"]["motor"]["interrupt_ka"] = 10
  write_json(config, values)
  run, targets = _run(root, "dcr:facility-power-check", "sheet-facility-power-continuity", "sheet-facility-power-cells")
  assert run.status == "fail"
  assert any(row["rule"] == "AIDC_FACILITY_POWER_PROTECTION" for row in targets["dcr:facility-power-check"]["diagnostics"])
  assert targets["sheet-facility-power-continuity"]["status"] == "pass"
  assert targets["sheet-facility-power-cells"]["status"] == "pass"


def _check_lateral_delivery(root):
  frame = _read(root / MODEL / "structure.json")
  model = _read(root / MODEL / "lateral-bracing.json")
  fit = _read(root / MODEL / "lateral-coordination.json")
  study = _read(root / MODEL / "lateral-study.json")
  assert len(model["bays"]) == 8
  assert fit["status"] == "pass" and fit["rods_checked"] == 16
  assert len(study["cases"]) == 32
  assert frame["members"]["P-1-A"]["parts"][0]["size"] == pytest.approx([2.6, 2.6, .60])
  assert frame["members"]["P-1-B"]["parts"][0]["size"] == pytest.approx([1.7, 1.7, .48])
  case = study["cases"]["wind-strength/x+1/ecc+1"]
  force, cross, _ = case["load_kn"]
  height = study["action_elevation_m"]-model["lower_joint_m"]
  assert case["overturning_couple_kn_m"] == pytest.approx([height*force, height*cross])
  assert sum(row["shear_kn"][0] for row in case["foundations"].values()) == pytest.approx(force)
  for pad in case["foundations"].values():
    assert pad["soil_resultants"] == pytest.approx([pad["pad_normal_kn"], *pad["first_moments_kn_m"]])
  for file, prim in (("aidc-braced-frame.usda", "/Frame/Bracing/VB_A_12/P"),
                     ("aidc-lateral-coordinated.usda", "/AIDC/StructureBracing/VB_A_12/P")):
    stage = Usd.Stage.Open(str(root / "build/scenes" / file))
    assert stage.GetPrimAtPath(prim).GetAttribute("texsys:collector").Get() == "G-1-2-A"
  sheets = root / "build/drawings"
  drawing = ET.parse(sheets / "views/lateral-elevations.svg")
  assert len(drawing.findall(f".//{SVG}line[@data-rod]")) == fit["rods_checked"]
  package = _read(root / "build/review/aidc-discipline-review.json")
  paths = {row["file"] for row in package["disciplines"]["structure"]["artifacts"]}
  assert "build/drawings/sheets/lateral-foundations.svg" in paths


def _check_roof_transfer_delivery(root):
  geometry = _read(root / MODEL / "roof-transfer.json")
  fit = _read(root / MODEL / "roof-transfer-coordination.json")
  roof = _read(root / MODEL / "roof-assembly.json")
  gravity = _read(root / MODEL / "structure-gravity.json")
  study = _read(root / MODEL / "lateral-study.json")
  assert len(geometry["ties"]) == 16
  assert len(geometry["collectors"]) == 22
  assert fit["minimum_tie_separation_m"] == pytest.approx(.058)
  diameter_backfeed = sum(tie["free_length_m"]*tie["area_m2"]*77 for tie in geometry["ties"].values())
  hardware = 2*.08*len(geometry["ties"])
  assert sum(gravity["roof_transfer_weight_by_node_kn"].values()) == pytest.approx(diameter_backfeed+hardware)
  assert study["pretension_equilibrium_residual_kn"] < 1e-10
  for case in study["cases"].values():
    response = case["brace_storey"]
    assert response["equilibrium_residual"]["max_node_kn"] < 1e-8
    assert sum(row[0] for row in response["node_loads_kn"].values()) == pytest.approx(case["load_kn"][0])
    assert {row["role"] for row in case["roof_members"].values()} == {"collector", "roof-tie"}
    assert all(row["total_kn"] > row["tension_floor_kn"] for row in case["roof_members"].values()
               if row["role"] == "roof-tie")
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-roof-transfer.usda"))
  rod = stage.GetPrimAtPath("/Frame/RoofTransfer/RF_1_2_A_B/lower")
  assert list(rod.GetAttribute("texsys:columns").Get()) == ["C-2-A", "C-1-B"]
  view = ET.parse(root / "build/drawings/views/roof-transfer-plan.svg")
  assert len(view.findall(f".//{SVG}circle[@data-roof-opening]")) == len(roof["openings"])


def test_aidc_roof_transfer_rejects_riser_clash_and_loss_of_retained_tension(tmp_path):
  root = _checkout(tmp_path)
  config = root / "src/roof-transfer.json"
  original = _read(config)
  crossed = {**original, "plan_offset_m": {"upper": 0, "lower": 0}}
  write_json(config, crossed)
  result, rows = _run(root, "dcr:roof-transfer-coordination", "sheet-roof-transfer-plan")
  assert result.status == "fail"
  assert any(issue["objects"] == ["HB-1-2-A-B-L", "A-IT01"]
             for issue in rows["dcr:roof-transfer-coordination"]["diagnostics"])
  assert rows["sheet-roof-transfer-plan"]["status"] == "pass"
  write_json(config, {**original, "rod_pretension_kn": 6})
  result, rows = _run(root, "dcr:lateral-check", "sheet-roof-transfer-demands")
  assert result.status == "fail"
  assert {issue["rule"] for issue in rows["dcr:lateral-check"]["diagnostics"]} == {"AIDC_ROOF_TRANSFER_TENSION"}
  assert rows["sheet-roof-transfer-demands"]["status"] == "pass"


def _check_equipment_foundation_delivery(root):
  model = _read(root / MODEL / "equipment-foundations.json")
  geometry = model["foundations"]
  fit = _read(root / MODEL / "equipment-foundation-coordination.json")
  analysis = _read(root / MODEL / "equipment-foundation-study.json")
  assert len(geometry) == 19
  assert sum(len(row["anchors"]) for row in geometry.values()) == 96
  assert min(fit["minimum_clearance_m"].values()) >= .055
  assert fit["minimum_pad_gap_m"] == pytest.approx(.05)
  assert min(row["minimum_anchor_bar_gap_m"] for row in geometry.values()) >= .012 - 1e-9
  assert sum(map(len, analysis["foundations"].values())) == 1900
  pad = geometry["FD-GenContainerA"]
  assert (pad["dry_mass_kg"], pad["operating_mass_kg"]) == (40000, 44000)
  state = analysis["foundations"]["FD-GenContainerA"]["operating/seismic/x+1/cross+1/ecc+1/high"]
  assert sum(row["foot_down_kn"] for row in state["anchors"].values()) == pytest.approx(state["equipment_normal_kn"])
  assert state["soil_integrals"] == pytest.approx([state["foundation_normal_kn"], *state["first_moments_kn_m"]])
  cushion = geometry["FD-BT-01"]
  assert cushion["operating_mass_kg"]-cushion["dry_mass_kg"] == pytest.approx(603.185789489)
  scene = Usd.Stage.Open(str(root / "build/scenes/aidc-equipment-foundations.usda"))
  prim = scene.GetPrimAtPath("/AIDC/EquipmentFoundations/FD_GenContainerA/Concrete")
  assert prim.GetAttribute("texsys:equipment").Get() == "GenContainerA"
  plan = ET.parse(root / "build/drawings/views/equipment-foundation-power.svg")
  assert plan.find(f".//{SVG}circle[@data-anchor='FD-PowerPathB/A01']") is not None


def test_aidc_equipment_foundation_gates_utility_space_and_anchor_demand(tmp_path):
  root = _checkout(tmp_path)
  source = root / "src/equipment-foundations.json"
  base = _read(source)
  write_json(source, {**base, "margins_by_equipment_m": {}})
  failed, targets = _run(root, "dcr:equipment-foundation-coordination", "sheet-equipment-foundation-power")
  assert failed.status == "fail"
  assert any(row["objects"] == ["FD-PowerPathB", "B-ENTRY-02"]
             for row in targets["dcr:equipment-foundation-coordination"]["diagnostics"])
  assert targets["sheet-equipment-foundation-power"]["status"] == "pass"
  base["anchor_types"]["M30"]["shear_kn"] = 4
  write_json(source, base)
  failed, targets = _run(root, "dcr:equipment-foundation-check", "sheet-equipment-foundation-checks")
  assert failed.status == "fail"
  assert {row["rule"] for row in targets["dcr:equipment-foundation-check"]["diagnostics"]} == {"AIDC_EQUIPMENT_FOUNDATION_BOLT"}
  assert targets["sheet-equipment-foundation-checks"]["status"] == "pass"


def _check_exit_access_delivery(root):
  geometry = _read(root / MODEL / "exit-access.json")
  check = _read(root / MODEL / "exit-access-study.json")
  assert check["occupancy"] == 40
  assert sum(map(len, check["paths"].values())) == 32
  assert check["hardware"]["D-NW"]["open_width_m"] == pytest.approx(1.455)
  assert check["hardware"]["D-NW"]["standoff_m"] == pytest.approx(.0095)
  assert all(area["area_m2"] > 20 for area in check["assembly"].values())
  for branches in check["paths"].values():
    assert {row["assembly"] for row in branches.values()} == {"AP-W", "AP-E"}
    assert all(row["indoor_pass"] and row["supported_pavement"] and not row["blockers"]
               for row in branches.values())
  points = geometry["paths"]["D-SW"]["points_m"]
  assert sum(dist(a,b) for a,b in zip(points,points[1:])) == pytest.approx(check["exits"]["D-SW"]["site_distance_m"])
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-exit-access-coordinated.usda"))
  door = stage.GetPrimAtPath("/AIDC/ExitAccess/Ramps/D_NW/Leaf_L1")
  assert door.GetAttribute("texsys:identity").Get() == "EA-D-NW-L1"


def _check_emergency_lighting_delivery(root):
  model = _read(root / MODEL / "emergency-lighting.json")
  study = _read(root / MODEL / "emergency-lighting-study.json")
  assert not study["issues"]
  assert len(study["zones"]) == 16
  assert len(study["signs"]) == 8 and all(row["clear_sightline"] for row in study["signs"].values())
  assert sum(row["samples"] for row in study["zones"].values()) > 1500
  for region, rows in study["points"].items():
    assert len(rows) == study["zones"][region]["samples"]
    for row in rows:
      assert row["intact_lx"] == pytest.approx(row["off_A_lx"]+row["off_B_lx"])
      assert min(row["off_A_lx"],row["off_B_lx"]) >= study["zones"][region]["minimum_required_lx"]
  lamp = study["battery_families"]["outdoor"]
  assert lamp["runtime_min"] == pytest.approx((29*.8*.77*.87)/9*60)
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-emergency-lighting-coordinated.usda"))
  prim = stage.GetPrimAtPath("/AIDC/EmergencyLighting/EL_D_NW_01/Body")
  assert prim.GetAttribute("texsys:identity").Get() == "EL-D-NW-01"
  x, y, z = model["fixtures"]["EL-D-NW-01"]["point_m"]
  assert tuple(UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()) == pytest.approx((x,y,z+.04))


def _check_lighting_distribution_delivery(root):
  network = _read(root / MODEL / "lighting-distribution.json")
  report = _read(root / MODEL / "lighting-distribution-study.json")
  lights = _read(root / MODEL / "emergency-lighting.json")
  assert not network["issues"] and not report["issues"]
  assert set(network["routes"]) == lights["fixtures"].keys() | {
      "EL-FEED", "EL-SIGNAL-FACP-FC-N", "EL-SIGNAL-FACP-FC-S"}
  feed = network["routes"]["EL-FEED"]
  assert network["supply"]["port"] == "AuxFeed.out_lighting"
  assert set(network["supply"]["input_links"]) == {"A", "B"}
  assert feed["points_m"][0] == network["supply"]["point_m"]
  assert feed["points_m"][-1] == network["cabinet"]["feeder_terminal_m"]
  assert 5 < feed["length_m"] < 15
  assert network["minimum_cover_m"] > .50
  assert set(network["sleeves"]) == {"EL-FS-N","EL-FS-S","EL-PS-N","EL-PS-S"}
  assert len(report["branches"]) == 8
  assert sum(len(row["points"]) for row in report["branches"].values()) == 92
  assert report["normal_distribution"]["connected_charge_w"] == 896
  assert report["normal_distribution"]["maximum_fill"] <= .40
  assert report["emergency_cases"]["alarm-with-A-contact-weld"]["energized_battery_groups"] == ["B"]
  assert report["emergency_cases"]["normal-feeder-loss"]["pickup_s"] == pytest.approx(.80)
  assert report["emergency_cases"]["fire-n"]["pickup_s"] == pytest.approx(1.55)
  assert [row["charge_command"] for row in report["reset_timeline"]] == [True,False,False,False,False,False,True]
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-lighting-distribution-coordinated.usda"))
  board = stage.GetPrimAtPath("/AIDC/LightingDistribution/Board/Cabinet")
  assert board.GetAttribute("texsys:identity").Get() == "EL-MDB"
  assert len(stage.GetPrimAtPath("/AIDC/Architecture/W_over_D_NW").GetChildren()) == 4


def _check_earthing_delivery(root):
  model = _read(root / MODEL / "earthing.json")
  study = _read(root / MODEL / "earth-fault-study.json")
  config = model["assumptions"]
  assert len(model["rods"]) == 10
  assert len(model["steel_terminals"]) == 12
  assert len(study["cases"]) == 332 and all(row["status"] == "pass" for row in study["cases"].values())
  assert sum(row["length_m"] for row in model["links"].values() if row["kind"]=="ring") == pytest.approx(125)
  assert study["electrodes"]["cases"]["all-connected"]["resistance_ohm"] == pytest.approx(2.0868, abs=.0001)
  assert all(not case["disconnected"] for case in study["continuity"]["one_ring_segment_open"].values())
  key = "IT:A:battery:IT01"
  fault = study["cases"][key]
  assert fault["pe"] == ["PE-BuswayA1", "PE-BuswayA1-IT01", "PE-A-IT01"]
  expected_mohm = sum(
    config["metal"]["copper_hot_ohm_mm2_m"]*1000*model["pe_runs"][key]["length_m"]/model["pe_runs"][key]["section_mm2"]
    for key in fault["pe"]
  )+4*config["metal"]["terminal_contact_mohm"]
  assert fault["pe_hot_mohm"] == pytest.approx(expected_mohm)
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-earthing-coordinated.usda"))
  rod = stage.GetPrimAtPath("/AIDC/ProtectiveEarthing/Electrodes/ER_07")
  assert rod.GetAttribute("texsys:identity").Get() == "ER-07"
  assert stage.GetPrimAtPath("/AIDC/LightingDistribution/Board/Cabinet").IsValid()
  for view,number in (("site","020"),("hall","021"),("section","022"),("electrodes","023"),("fault","024")):
    svg = ET.parse(root / f"build/drawings/sheets/earthing-{view}.svg")
    assert svg.getroot().get("data-drawing-number") == f"AIDC-E-{number}"


def test_aidc_earthing_recalculates_and_keeps_diagnostic_drawings(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/earthing.json"
  config = _read(path)
  check, sheet = "dcr:earth-fault-check","sheet-earthing-electrodes"
  result, _ = _run(root,check,sheet)
  assert result.status == "pass", result.diagnostics
  study = root / MODEL / "earth-fault-study.json"
  original = _read(study)
  drawing = root / "build/drawings/sheets/earthing-electrodes.svg"
  original_drawing = file_digest(drawing)
  config["electrodes"]["top_xy_m"][1][0] += 1
  write_json(path,config)
  result, status = _run(root,check,sheet)
  assert result.status == "pass", result.diagnostics
  assert status["dcr:earthing"]["status"] == "pass"
  current = _read(study)
  case = "all-connected"
  assert current["electrodes"]["cases"][case]["resistance_ohm"] != original["electrodes"]["cases"][case]["resistance_ohm"]
  assert current["cases"] == original["cases"]
  assert file_digest(drawing) != original_drawing

  config["fault"]["unavailable_pe"] = ["PE-A-IT01"]
  write_json(path,config)
  result, status = _run(root,check,"sheet-earthing-fault")
  assert result.status == "fail" and status["sheet-earthing-fault"]["status"] == "pass"
  failed = _read(study)["cases"]
  assert {key for key,row in failed.items() if row["status"]=="fail"} == {"IT:A:battery:IT01","IT:A:inverter:IT01"}
  assert failed["IT:A:battery:IT01"]["failed"] == ["metallic-path"]

  path.unlink()
  result, status = _run(root,check)
  assert result.status != "pass"
  assert status["dcr:earthing"]["status"] == "indeterminate"


def _check_lightning_delivery(root):
  from texsys.studio.scene import _geometry

  network = _read(root / MODEL / "lightning-roof.json")
  study = _read(root / MODEL / "lightning-study.json")
  gravity = _read(root / MODEL / "structure-gravity.json")
  assert len(network["masts"]) == len(study["continuity"]["downleads"]) == 12
  assert len(network["links"]) == 22
  assert max(study["continuity"]["perimeter_pitch_m"].values()) == pytest.approx(9.5)
  assert len(study["continuity"]["cases"]) == 13
  assert all(not row["disconnected"] for row in study["continuity"]["cases"].values())
  assert len(study["coverage"]["targets"]) == 138
  assert min(row["minimum_margin_m"] for row in study["coverage"]["targets"].values()) > 0
  assert sum(row["mass_kg"] for row in network["masts"].values())*.00980665 == pytest.approx(
    sum(gravity["lightning_mast_weight_by_node_kn"].values()))
  assert gravity["roof_dead_with_transfer_kpa"] < .8
  assert len(study["surge_protection"]) == 4
  assert study["surge_protection"]["SPD-A-TRANSFER"]["installed_protection_kv"] == pytest.approx(1.6+.4*.6*3.)
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-lightning-coordinated.usda"))
  assert stage.GetPrimAtPath("/AIDC/Lightning/AirTerminals/AT_3_C/Mast").GetAttribute("texsys:identity").Get() == "AT-3-C"
  assert stage.GetPrimAtPath("/AIDC/ProtectiveEarthing/Electrodes/ER_07").IsValid()
  assert stage.GetPrimAtPath("/AIDC/Lightning/Surge/SPD_A_TRANSFER/Body").IsValid()
  air = UsdGeom.Imageable(stage.GetPrimAtPath("/AIDC/Air"))
  assert air.ComputeVisibility() == UsdGeom.Tokens.invisible
  assert any(row["name"]=="AT-3-C" for row in _geometry(stage,{}))
  for view,no in (("roof",25),("coverage",26),("section",27),("surge",28),("checks",29)):
    svg = ET.parse(root / f"build/drawings/sheets/lightning-{view}.svg")
    assert svg.getroot().get("data-drawing-number") == f"AIDC-E-{no:03}"


def test_aidc_lightning_height_and_spd_changes_recompute_affected_outputs(tmp_path):
  root = _checkout(tmp_path)
  check, drawing, gravity = "dcr:lightning-check","sheet-lightning-coverage","dcr:structure-gravity"
  result, _ = _run(root,check,drawing,gravity)
  assert result.status == "pass", result.diagnostics
  mast_path = root / "src/lightning-roof.json"
  original = _read(mast_path)
  config = _read(mast_path)
  report = root / MODEL / "structure-gravity.json"
  original_weight = _read(report)["lightning_mast_weight_by_node_kn"]["C-1-C"]
  config["roof"]["mast_height_m"]["1-C"] = 2
  write_json(mast_path,config)
  result, status = _run(root,check,drawing,gravity)
  assert result.status == "fail" and status[drawing]["status"] == "pass"
  assert "AIDC_LIGHTNING_ROLLING_SPHERE" in {row["rule"] for row in status[check]["diagnostics"]}
  assert _read(report)["lightning_mast_weight_by_node_kn"]["C-1-C"] < original_weight

  write_json(mast_path,original)
  rest = root / "src/lightning.json"
  surge = _read(rest)
  surge["surge"]["local_lead_m"]["transfer"] = [.16,.43]
  write_json(rest,surge)
  result, status = _run(root,check,"sheet-lightning-surge",gravity)
  assert result.status == "fail" and status["sheet-lightning-surge"]["status"] == "pass"
  assert {"SPD-A-TRANSFER","SPD-B-TRANSFER"} <= {row["objects"][0] for row in status[check]["diagnostics"]}
  assert _read(report)["lightning_mast_weight_by_node_kn"]["C-1-C"] == pytest.approx(original_weight)


def test_rolling_sphere_contact_bound_matches_vertical_analytic_solution():
  import numpy as np

  from texsys_datacenter_reference.lightning_coverage import bounded_contacts

  nominal,alternative,_ = bounded_contacts(np.zeros((1,3)),np.zeros(1),np.array([[0.,0.,2.],[0.,0.,1.]]),20,0,.05)
  assert nominal == pytest.approx([2-4/40])
  assert alternative == pytest.approx([1-1/40])


def _check_generator_yard_delivery(root):
  yard = _read(root / MODEL / "generator-yard.json")
  check = _read(root / MODEL / "generator-yard-check.json")
  assert check["model_digest"] == file_digest(root / MODEL / "generator-yard.json")
  assert check["status"] == "pass" and check["selection_status"] == "conditional"
  assert len(yard["equipment"]) == len(yard["power_and_fuel_cases"]) == 4
  assert all(not row["fits_axis_aligned_skids"] for row in yard["original_host_fit"].values())
  for offline, case in yard["power_and_fuel_cases"].items():
    assert case["running"] != offline and case["running"].split("/")[0] == offline.split("/")[0]
    assert case["active_margin_kw"] == pytest.approx(4)
    assert case["sensitivity_margin_kw"] == pytest.approx(-24.8)
    assert case["sensitivity_status"] == "fail"
    assert case["original_storage_h_on_same_duty"] == pytest.approx(11.175566)
    assert case["endurance_h"] == pytest.approx(case["usable_l"] / 718)
    assert case["gross_circulation_lph"] == case["net_return_lph"] + case["rated_burn_lph"]
  assert yard["checks"]["RETENTION_longer"]["free_m3"] > 8.5
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-generator-yard-alternative.usda"))
  assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
  plan = ET.parse(root / "build/drawings/views/generator-yard-plan.svg")
  rect = plan.find(f".//{SVG}rect[@data-geometry='indicative_skid']")
  asset = rect.get("data-object")
  box = yard["equipment"][asset]["indicative_skid"]
  assert float(rect.get("x")) == pytest.approx(box["minimum_m"][0])
  assert float(rect.get("width")) == pytest.approx(box["maximum_m"][0] - box["minimum_m"][0])
  slug = asset.replace("/", "_")
  skid = stage.GetPrimAtPath(f"/AIDC/GeneratorYard/Equipment/{slug}/indicative_skid")
  assert skid.GetAttribute("texsys:identity").Get() == asset


def _check_fuel_delivery(root):
  model = _read(root / MODEL / "fuel-system.json")
  study = _read(root / MODEL / "fuel-study.json")
  check = _read(root / MODEL / "fuel-check.json")
  assert check["status"] == "pass"
  assert set(model["tanks"]) == {"TK-A","TK-B"}
  assert len(model["day_tanks"]) == 4
  assert len(model["runs"]) == 30
  tank = model["tanks"]["TK-A"]
  assert tank["nominal_l"] == pytest.approx(pi*.8**2*4.48*1000)
  assert tank["usable_l"] == pytest.approx(8024.0566249)
  for side,cases in study["independent_storage"].items():
    assert len(cases) == 2
    for case in cases.values():
      assert case["status"]=="pass" and case["endurance_h"] == pytest.approx(tank["usable_l"]/620)
      assert case["engines_running"][0].startswith(f"GenContainer{side}/")
  assert all(abs(row["balance_residual_l"])<1e-9 for row in study["changeover"].values())
  assert all(row["status"]=="pass" for row in study["overflow"].values())
  water = study["containment"]["cases"]["longer"]
  assert water["basis_spill_m3"] == pytest.approx(10)
  assert water["free_storage_m3"] == pytest.approx(.7662667)
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-fuel-coordinated.usda"))
  assert stage.GetPrimAtPath("/AIDC/Fuel/Storage/TK_A/Shell").GetAttribute("texsys:identity").Get() == "TK-A"
  assert stage.GetPrimAtPath("/AIDC/Fuel/DayTanks/DT_B2").IsValid()
  assert stage.GetPrimAtPath("/AIDC/Storm/OilVault/Floor").IsValid()
  for key,no in (("yard","M-022"),("indoor","M-023"),("sections","M-024"),("system","M-025"),("checks","M-026"),
                 ("containment","F-009")):
    svg = ET.parse(root / f"build/drawings/sheets/fuel-{key}.svg")
    assert svg.getroot().get("data-drawing-number") == f"AIDC-{no}"


def test_aidc_fuel_rejects_short_reserve_and_local_pump_loss(tmp_path):
  root = _checkout(tmp_path)
  source = root / "src/fuel-system.json"
  original = _read(source)
  gate, sheet = "dcr:fuel-check","sheet-fuel-checks"
  result,_ = _run(root,gate,sheet)
  assert result.status == "pass", result.diagnostics
  report = root / MODEL / "fuel-study.json"
  baseline = _read(report)
  baseline_sheet = file_digest(root/"build/drawings/sheets/fuel-checks.svg")

  shorter = _read(source)
  shorter["storage"]["inner_length_m"] = 3.9
  write_json(source,shorter)
  result,targets = _run(root,gate,sheet)
  assert result.status == "fail" and targets[sheet]["status"] == "pass"
  assert {row["rule"] for row in targets[gate]["diagnostics"]} >= {
    "AIDC_FUEL_TANK_CAPACITY","AIDC_FUEL_STORAGE_RECONCILIATION","AIDC_FUEL_INDEPENDENT_RESERVE"}
  assert file_digest(root/"build/drawings/sheets/fuel-checks.svg") != baseline_sheet

  original["hydraulics"]["unavailable_pumps"] = ["P-A-1","P-A-2"]
  write_json(source,original)
  result,targets = _run(root,gate,"sheet-fuel-system")
  assert result.status == "fail" and targets["sheet-fuel-system"]["status"] == "pass"
  broken = _read(report)
  assert broken["changeover"]["A"]["status"]=="fail"
  assert broken["changeover"]["B"]==baseline["changeover"]["B"]
  assert all(row["status"]=="pass" for key,row in broken["transfer"].items() if key.startswith("B:"))


def test_fuel_storage_segment_area_matches_half_cylinder():
  from texsys_datacenter_reference.fuel_system import _area

  assert _area(.8,.8) == pytest.approx(pi*.8**2/2)
  assert _area(.8,.3)+_area(.8,1.3) == pytest.approx(pi*.8**2)


def test_aidc_lighting_distribution_rejects_double_weld_and_undersized_protection(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/lighting-distribution.json"
  base = _read(path)
  gate = "dcr:lighting-distribution-check"
  sequence = "sheet-lighting-distribution-sequence"
  single = "sheet-lighting-distribution-single-line"
  edited = {**base,"controls":{**base["controls"],"forced_welded_groups":["A","B"]}}
  write_json(path,edited)
  result, targets = _run(root,gate,sequence)
  assert result.status == "fail" and targets[sequence]["status"] == "pass"
  assert {row["rule"] for row in targets[gate]["diagnostics"]} == {"AIDC_LIGHTING_DISTRIBUTION_EMERGENCY_RESPONSE"}
  base["electrical"]["feeder"]["breaking_ka"] = .1
  write_json(path,base)
  result, targets = _run(root,gate,single)
  assert result.status == "fail" and targets[single]["status"] == "pass"
  assert {row["rule"] for row in targets[gate]["diagnostics"]} == {"AIDC_LIGHTING_DISTRIBUTION_PROTECTION"}


def test_aidc_emergency_lighting_keeps_drawings_for_failed_battery_and_lux(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/emergency-lighting.json"
  original = _read(path)
  target = "dcr:emergency-lighting-check"
  drawing = "sheet-emergency-lighting-checks"
  original["battery"]["outdoor"]["nominal_wh"] = 19
  write_json(path, original)
  result, targets = _run(root, target, drawing)
  assert result.status == "fail" and targets[drawing]["status"] == "pass"
  assert {item["rule"] for item in targets[target]["diagnostics"]} >= {
      "AIDC_EMERGENCY_LIGHTING_AUTONOMY", "AIDC_EMERGENCY_LIGHTING_ILLUMINANCE"}
  original["battery"]["outdoor"]["nominal_wh"] = 29
  original["photometry"]["hall"]["peak_cd"] = 150
  write_json(path, original)
  result, targets = _run(root, target, drawing)
  assert result.status == "fail" and targets[drawing]["status"] == "pass"
  assert {item["rule"] for item in targets[target]["diagnostics"]} == {"AIDC_EMERGENCY_LIGHTING_ILLUMINANCE"}


def test_emergency_lighting_direct_illuminance_has_expected_projection():
  from texsys_datacenter_reference.emergency_lighting_study import direct_lux

  assert direct_lux((0,0,3), (0,0,0), 100, 1, .8) == pytest.approx(80/9)
  assert direct_lux((0,0,3), (4,0,0), 100, 1, .8) == pytest.approx(80*(3/5)**2/25)
  assert direct_lux((0,0,3), (0,0,4), 100, 1, .8) == 0


def test_aidc_exit_access_rejects_rail_encroachment_and_small_assembly_area(tmp_path):
  root = _checkout(tmp_path)
  config = root / "src/exit-access.json"
  before = _read(config)
  write_json(config, {**before, "ramp": {**before["ramp"], "rail_lower_extension_m": .55}})
  failed, targets = _run(root, "dcr:exit-access-check", "sheet-exit-access-section", "sheet-evacuation-site-plan")
  assert failed.status == "fail"
  assert any(row["rule"] == "AIDC_EXIT_ACCESS_ROUTE_CLASH"
             for row in targets["dcr:exit-access-check"]["diagnostics"])
  assert targets["sheet-evacuation-site-plan"]["status"] == "pass"
  before["meeting_areas"]["AP-W"]["extent_m"][2] = 5.
  write_json(config,before)
  failed, targets = _run(root, "dcr:exit-access-check", "sheet-evacuation-site-verification")
  assert failed.status == "fail"
  assert {row["rule"] for row in targets["dcr:exit-access-check"]["diagnostics"]} == {"AIDC_EXIT_ACCESS_ASSEMBLY_CAPACITY"}
  assert targets["sheet-evacuation-site-verification"]["status"] == "pass"


def test_aidc_lateral_reacts_to_mass_and_rejects_directional_mechanism(tmp_path):
  root = _checkout(tmp_path)
  requested = ("dcr:lateral-check", "sheet-lateral-elevations", "sheet-lateral-foundations")
  run, _ = _run(root, *requested)
  assert run.status == "pass", run.diagnostics
  study = root / MODEL / "lateral-study.json"
  first = _read(study)
  actions = root / "src/lateral-actions.json"
  config = _read(actions)
  config["additional_roof_mass_kn"] += 15
  write_json(actions, config)
  run, targets = _run(root, *requested)
  assert run.status == "pass", run.diagnostics
  second = _read(study)
  case = "seismic/x+1/ecc+1/y+1"
  assert second["cases"][case]["load_kn"][0] - first["cases"][case]["load_kn"][0] == pytest.approx(.16*15)
  assert second["cases"]["wind-service/x+1/ecc+1"]["load_kn"] == first["cases"]["wind-service/x+1/ecc+1"]["load_kn"]
  assert targets["dcr:lateral-bracing"]["execution"] == "cached"
  assert targets["sheet-lateral-elevations"]["execution"] == "cached"
  assert targets["sheet-lateral-foundations"]["execution"] == "executed"

  bracing = root / "src/lateral-bracing.json"
  config = _read(bracing)
  config["bays"] = {name: row for name, row in config["bays"].items() if row["from_axes"][1] == row["to_axes"][1]}
  write_json(bracing, config)
  run, targets = _run(root, *requested)
  assert run.status == "fail"
  assert {issue["rule"] for issue in targets["dcr:lateral-check"]["diagnostics"]} == {"AIDC_LATERAL_MECHANISM"}
  assert targets["sheet-lateral-elevations"]["status"] == "pass"
  assert targets["sheet-lateral-foundations"]["status"] == "pass"


def _check_roof_delivery(root):
  roof = _read(root / MODEL / "roof-assembly.json")
  actions = _read(root / MODEL / "roof-actions.json")
  gravity = _read(root / MODEL / "structure-gravity.json")
  drainage = _read(root / MODEL / "roof-drainage.json")
  assert len(roof["panels"]) == 8
  assert len(roof["purlins"]) == 64
  assert len(roof["openings"]) == 64
  assert roof["openings"]["IT01-return-ROOF"]["point_m"][2] == pytest.approx(5.3414)
  assert roof["openings"]["A-IT01"]["compartment"] == "FC-S"
  assert sum(row["net_area_m2"] for row in roof["panels"].values()) == pytest.approx(32.44*19.44-32*(.14**2+.18**2))
  assert actions["checks"] == "pass"
  assert actions["equilibrium"]["service"]["delivered_kn"] == pytest.approx(gravity["equilibrium"]["service"]["roof_kn"])
  assert gravity["equilibrium"]["service"]["residual_kn"] == pytest.approx(0)
  assert len(actions["girder_actions"]["G-1-2-B"]) == 16
  assert drainage["outlets"]["RW-S-W"]["design_flow_l_s"] == pytest.approx(155*32.44*19.44/(4*3600))
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-roof-assembly.usda"))
  for key in ("IT01-return-ROOF", "A-IT01"):
    row = roof["openings"][key]
    tag = row["panel"].replace("-", "_")
    px, py, _ = row["point_m"]
    for layer in ("deck", "insulation", "membrane"):
      panel = UsdGeom.Mesh.Get(stage, f"/AIDC/Roof/Panels/{tag}/{layer}")
      points = panel.GetPointsAttr().Get()
      indices = panel.GetFaceVertexIndicesAttr().Get()
      for index in range(0, len(indices), 4):
        xs, ys, _ = zip(*(points[item] for item in indices[index:index+4]), strict=True)
        assert not (min(xs) < px < max(xs) and min(ys) < py < max(ys))
    assert stage.GetPrimAtPath(f"/AIDC/Roof/Curbs/{key.replace('-', '_')}")
  assert stage.GetPrimAtPath("/AIDC/Roof/Rainwater/RW_S_W/Leader")
  water = ET.parse(root / "build/drawings/views/fws-section.svg")
  power = ET.parse(root / "build/drawings/views/power-pod-section.svg")
  row = roof["openings"]["IT01-return-ROOF"]
  sleeve = water.find(f".//{SVG}rect[@data-pipe='{row['run']}']")
  assert -float(sleeve.get("y")) == pytest.approx(row["curb_top_m"], abs=1e-6)
  assert power.find(f".//{SVG}rect[@data-host='building_weather_surface_m']") is not None


def _check_storm_delivery(root):
  grade = _read(root / MODEL / "site-grading.json")
  model = _read(root / MODEL / "site-storm.json")
  test = _read(root / MODEL / "site-storm-study.json")
  roof = _read(root / MODEL / "roof-drainage.json")
  assert not grade["issues"] and not model["issues"] and not test["issues"]
  assert grade["reserved_areas_m2"]["ponding"] == 0
  assert sum(row["area_m2"] for row in grade["catchments"].values()) + sum(
    grade["reserved_areas_m2"].values()
  ) == pytest.approx(80*56)
  earth = grade["earthwork"]
  assert earth["fill_m3"]-earth["cut_m3"] == pytest.approx(earth["signed_m3"])
  assert len(model["runs"]) == 9
  assert model["nodes"]["ST-OUTFALL"]["point_m"][:2] == [80, 10]
  assert model["detention"]["working_capacity_m3"] == pytest.approx(12*8*1.75)
  assert model["roof_shoes"]["RW-S-W"]["points_m"][0] == roof["outlets"]["RW-S-W"]["handoff_m"]
  assert min(model["covers_m"].values()) >= .60
  drainage = test["cases"]["design"]
  assert drainage["roof_total_l_s"] == pytest.approx(roof["total_runoff_l_s"])
  assert drainage["run_peak_l_s"]["ST-M03"] == pytest.approx(
    155*sum(row["effective_area_m2"] for row in grade["catchments"].values())/3600 + roof["total_runoff_l_s"]
  )
  detention = drainage["detention"]
  assert detention["initial_storage_m3"]+detention["inflow_volume_m3"] == pytest.approx(
    detention["released_volume_m3"]+detention["final_storage_m3"]
  )
  head = detention["peak_water_level_m"]-(-3.95+.10)
  assert detention["peak_release_l_s"] == pytest.approx(1000*.62*pi*.15**2/4*sqrt(2*9.81*head))
  assert list(model["catchment_paths"]["RW-N-W"])[0] == "ST-N01"
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-site-storm-underground.usda"))
  assert stage.GetPrimAtPath("/AIDC/Storm/Detention/Floor")
  assert stage.GetPrimAtPath("/AIDC/Storm/OilVault/Floor")
  assert stage.GetPrimAtPath("/AIDC/Storm/FlowControl")


def test_aidc_storm_checks_storage_change_and_submerged_boundary(tmp_path):
  root = _checkout(tmp_path)
  config = root / "src/site-storm.json"
  data = _read(config)
  data["detention"]["working_depth_m"] = 1.10
  data["outfall"]["tailwater_m"] = -4.20
  write_json(config, data)
  result, status = _run(root, "dcr:site-storm-check", "sheet-site-storm-hydrograph", "sheet-site-storm-profile")
  assert result.status == "fail"
  reasons = {row["rule"] for row in status["dcr:site-storm-check"]["diagnostics"]}
  assert {"AIDC_STORM_STORAGE", "AIDC_STORM_TAILWATER"} <= reasons
  for chart in ("sheet-site-storm-hydrograph", "sheet-site-storm-profile"):
    assert status[chart]["status"] == "pass"


def test_aidc_roof_geometry_recomputes_gravity_and_overload_retains_drainage_plan(tmp_path):
  root = _checkout(tmp_path)
  src = root / "src/roof.json"
  values = _read(src)
  original, _ = _run(root, "dcr:structure-gravity")
  assert original.status == "pass"
  baseline = _read(root / MODEL / "structure-gravity.json")
  values["slope"] = .012
  values["drainage"]["rainfall_mm_h"] = 360
  write_json(src, values)
  failed, rows = _run(root, "dcr:structure-gravity", "dcr:roof-drainage-check", "sheet-roof-rain")
  assert failed.status == "fail"
  built = _read(root / MODEL / "structure-gravity.json")
  assert built["roof_actions_digest"] != baseline["roof_actions_digest"]
  assert built["equilibrium"]["service"]["roof_kn"] < baseline["equilibrium"]["service"]["roof_kn"]
  assert rows["dcr:roof-drainage-check"]["status"] == "fail"
  assert rows["sheet-roof-rain"]["status"] == "pass"


def test_aidc_power_routing_exposes_a_water_clash_and_derated_feeders(tmp_path):
  root = _checkout(tmp_path)
  path = root / "src/power-routing.json"
  config = _read(path)
  config["trains"]["A"]["yard_elbow_y_m"] = 28.5
  config["cables"]["feed"]["installation_factor"] = .61
  write_json(path, config)
  failed, records = _run(root, "dcr:power-coordination", "dcr:power-installation", "sheet-power-roof-a")
  assert failed.status == "fail"
  clashes = records["dcr:power-coordination"]["diagnostics"]
  assert any(row["rule"] == "AIDC_POWER_SERVICE_CLASH" and row["objects"] == ["A-P2", "R-001"] for row in clashes)
  faults = records["dcr:power-installation"]["diagnostics"]
  assert sum(row["rule"] == "AIDC_POWER_FEED_AMPACITY" for row in faults) == 8
  assert records["dcr:power-study"]["status"] == "pass"
  assert records["sheet-power-roof-a"]["status"] == "pass"


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
  design["loop"]["panel_offset_y_m"] = 1.37
  design["loop"]["side_inset_m"] = .48
  write_json(geometry, design)
  result, statuses = _run(root, "dcr:fire-systems-coordination")
  assert result.status == "fail" and statuses["dcr:telecom-coordination"]["status"] == "pass"
  assert "envelope overlaps TA-R02-01" in result.diagnostics


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


def _check_wet_utility_delivery(root):
  model = _read(root / MODEL / "wet-utilities.json")
  runs, tank = model["runs"], model["tank"]
  sheet = root / "build/schedules/aidc-wet-utility-pipes.csv"
  with sheet.open(newline="") as stream:
    assert [row["id"] for row in csv.DictReader(stream)] == sorted(runs)
  sdl = _read(root / "src/system.sdl.json")
  nets = sdl["components"][sdl["root"]]["nets"]
  assert {runs[f"MU-{tower}"]["sdl_link"] for tower in model["towers"]} == set(nets["Makeup"]["connections"])
  assert {runs[f"DR-{tower}"]["sdl_link"] for tower in model["towers"]} == set(nets["TowerDrain"]["connections"])
  assert {runs[f"NOZ-{tower}"]["sdl_link"] for tower in model["towers"]} == set(nets["TowerDrain"]["connections"])
  assert tank["usable_volume_m3"] == pytest.approx(2.85*(2-.25))
  assert model["outfall"]["point_m"] == pytest.approx([80, 44.5, -1.798])
  assert all(row["gap_m"] >= .05 for row in model["receivers"].values())
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-wet-utilities-coordinated.usda"))
  assert stage.GetPrimAtPath("/AIDC/CoolingWater/link_055")
  for key in runs:
    path = f"/AIDC/WetUtilities/{key.replace('-', '_')}"
    assert stage.GetPrimAtPath(path).GetAttribute("texsys:identity").Get() == key
  coordination = _read(root / MODEL / "wet-utility-coordination.json")
  assert len(coordination["crossings"]) == 3
  low = min(row["vertical_clearance_m"] for row in coordination["crossings"])
  expected_low = 1.27-(44.5-43.35+.0483/2)*.015-.072/2-.8-.0483/2-.08/2
  assert low == pytest.approx(expected_low)
  assert coordination["minimum_cover_m"] == pytest.approx(.8-.0483/2)
  plan = ET.parse(root / "build/drawings/views/wet-plan.svg")
  assert {node.get("data-run") for node in plan.findall(f".//{SVG}g[@data-run]")} == set(runs)
  profile = ET.parse(root / "build/drawings/views/wet-profile.svg")
  tags = {node.get("data-run") for node in profile.findall(f".//{SVG}g[@data-run]")}
  assert tags == {key for key in runs if key.startswith("DR-M")}
  study = _read(root / MODEL / "wet-utility-study.json")
  cw = _read(root / MODEL / "cw-study.json")
  assert set(study["cases"]) == set(cw["cases"])
  normal = study["cases"]["normal"]
  duty = sum(row["rejection_kw"] for row in cw["cases"]["normal"]["duty"].values())
  evaporation = duty*.85*3600/(997*2430)
  assert normal["makeup_m3_h"] == pytest.approx(evaporation*4/3)
  assert normal["outage_end_remaining_m3"] == pytest.approx(tank["usable_volume_m3"]-1.5*evaporation*4/3)
  assert normal["basin_tds_mg_l"] == pytest.approx(4*210)
  assert normal["gravity_capacity_m3_h"]["DR-M03"] == pytest.approx(pi*.104**2/8*(.104/4)**(2/3)*.1/.011*3600)
  assert normal["discharge_flow_by_run_m3_h"]["DR-M03"] == pytest.approx(normal["blowdown_m3_h"])


def test_site_wet_utility_profile_and_reserve_failures_retain_drawings(tmp_path):
  root = _checkout(tmp_path)
  baseline, _ = _run(root)
  assert baseline.status == "pass", baseline.diagnostics
  path = root / "src/wet-utilities.json"
  original = path.read_bytes()
  model = root / MODEL / "wet-utilities.json"
  geometry = _read(model)
  sheet = root / "build/drawings/sheets/wet-profile.svg"
  original_profile = file_digest(sheet)
  source = _read(path)
  source["drain"]["west_junction_invert_m"] -= .07
  write_json(path, source)
  built, records = _run(root)
  assert built.status == "pass"
  assert records["dcr:cw-study"]["execution"] == "cached"
  assert records["sheet-wet-profile"]["execution"] == "executed"
  assert records["sheet-cw-system"]["execution"] == "cached"
  assert _read(model)["outfall"]["point_m"][2] == pytest.approx(geometry["outfall"]["point_m"][2]-.07)
  assert file_digest(sheet) != original_profile

  source["drain"]["west_junction_invert_m"] = -1.14
  write_json(path, source)
  failed, records = _run(root)
  assert failed.status == "fail"
  assert records["dcr:wet-utility-coordination"]["diagnostics"][0]["rule"] == "AIDC_WET_CROSSING"
  assert records["sheet-wet-plan"]["status"] == "pass"
  assert records["review-package"]["execution"] == "blocked"

  path.write_bytes(original)
  source = _read(path)
  source["supply"]["high_water_m"] = 1.65
  write_json(path, source)
  failed, records = _run(root)
  assert failed.status == "fail"
  assert records["dcr:wet-utility-coordination"]["status"] == "pass"
  study = _read(root / MODEL / "wet-utility-study.json")
  assert study["cases"]["normal"]["checks"]["bounded-city-outage"] is False
  assert study["cases"]["normal"]["outage_end_remaining_m3"] < 0
  assert records["sheet-wet-system"]["status"] == "pass"
  assert records["review-package"]["execution"] == "blocked"
  path.write_bytes(original)

  path = root / "src/wet-utility-study.json"
  limits = _read(path)
  original = path.read_bytes()
  limits["city_tds_mg_l"] = 280
  write_json(path, limits)
  failed, records = _run(root)
  assert failed.status == "fail"
  study = _read(root / MODEL / "wet-utility-study.json")
  assert study["cases"]["normal"]["basin_tds_mg_l"] == pytest.approx(1120)
  assert not study["cases"]["normal"]["checks"]["discharge-tds"]
  assert records["sheet-wet-profile"]["execution"] == "cached"
  path.write_bytes(original)

  restored, records = _run(root)
  assert restored.status == "pass"
  assert records["review-package"]["execution"] == "cached"


def _check_pod_delivery(root):
  layout = _read(root / MODEL / "pod-layout.json")
  first = layout["pods"]["IT01"]
  equipment = first["local_equipment"]
  assert len(layout["pods"]) == 16 and len(equipment) == 19
  assert first["size_m"] == pytest.approx((7.4, 3.68, 2.9))
  assert first["aisle"]["maximum_m"][1]-first["aisle"]["minimum_m"][1] == pytest.approx(1.12)
  assert equipment["Rack01"]["dimensions_m"] == pytest.approx((.8, 1.2, 2.2))
  assert equipment["Rack01"]["ports_m"]["tcs_in_a"] == pytest.approx((.11+.8-.15, .08+1.2-.16, 2.2))
  assert equipment["Rack05"]["ports_m"]["tcs_in_a"] == pytest.approx((.11+.15, 2.4+.16, 2.2))
  routes = _read(root / MODEL / "egress-model.json")["routes"]
  assert routes["IT01"]["paths"][0]["pod_door"] == "D-N"
  assert routes["IT05"]["paths"][0]["pod_door"] == "D-S"
  assert routes["IT01"]["paths"][0]["access_m"][:2] == pytest.approx(first["doors"]["D-N"]["world_m"][:2])

  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-pods-coordinated.usda"))
  assert not stage.GetPrimAtPath("/AIDC/Objects/IT01/Asset/Geometry/Envelope")
  cabinet = stage.GetPrimAtPath("/AIDC/ITPods/IT01/Equipment/Rack01/Asset/Geometry/Cabinet")
  world = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(cabinet).ComputeAlignedRange()
  assert tuple(world.GetMin()) == pytest.approx((8.41, 7.34, .44))
  assert tuple(world.GetMax()) == pytest.approx((9.21, 8.54, 2.64))
  assert stage.GetPrimAtPath("/AIDC/FireSystems/Pods/IT01/NZ_IT01_1")
  assert stage.GetPrimAtPath("/AIDC/WetUtilities/MH_EAST")
  coordination = _read(root / MODEL / "pod-coordination.json")
  assert coordination["equipment_count"] == 304 and not coordination["issues"]
  plan = ET.parse(root / "build/drawings/views/pod-plan.svg")
  assert {tag.get("data-equipment") for tag in plan.findall(f".//{SVG}rect[@data-equipment]")} == set(equipment)
  section = ET.parse(root / "build/drawings/views/pod-section.svg")
  assert {tag.get("data-equipment") for tag in section.findall(f".//{SVG}rect[@data-equipment]")} == {"Rack01", "Rack05"}
  detail = ET.parse(root / "build/drawings/sheets/pod-plan.svg")
  assert float(detail.find(f".//{SVG}image").get("width")) == pytest.approx(802/3)


def test_pod_installation_changes_recompile_detail_and_reject_access_encroachment(tmp_path):
  root = _checkout(tmp_path)
  base, _ = _run(root)
  assert base.status == "pass", base.diagnostics
  source = root / "src/pod-layout.json"
  original = source.read_bytes()
  section = root / "build/drawings/sheets/pod-section.svg"
  prior = file_digest(section)
  config = _read(source)
  config["installation_overrides"] = {"IT01": {"Rack01": {"minimum_m": [.09, .08, 0]}}}
  write_json(source, config)
  changed, statuses = _run(root)
  assert changed.status == "pass", changed.diagnostics
  assert file_digest(section) != prior
  assert statuses["dcr:pod-coordination"]["execution"] == "executed"
  assert statuses["sheet-it-hall"]["execution"] == "cached"
  assert statuses["dcr:fws-network-study"]["execution"] == "cached"

  config["installation_overrides"]["IT01"]["Rack01"]["minimum_m"][1] = .30
  write_json(source, config)
  failure, statuses = _run(root)
  assert failure.status == "fail"
  assert statuses["dcr:pod-coordination"]["status"] == "fail"
  assert statuses["sheet-pod-section"]["status"] == "pass"
  assert statuses["review-package"]["execution"] == "blocked"
  issues = _read(root / MODEL / "pod-coordination.json")["issues"]
  assert any(issue["rule"] == "AIDC_POD_ACCESS" and issue["objects"] == ["IT01", "Rack01", "AISLE"] for issue in issues)
  source.write_bytes(original)
  restored, statuses = _run(root)
  assert restored.status == "pass"
  assert statuses["review-package"]["execution"] == "cached"


def _check_tcs_delivery(root):
  road = root / MODEL / "tcs-routing.json"
  routing = _read(road)
  study = _read(root / MODEL / "tcs-study.json")
  coordination = _read(root / MODEL / "tcs-coordination.json")
  geometry = _read(root / MODEL / "pod-layout.json")["pods"]["IT01"]["local_equipment"]
  assert len(routing["pods"]) == coordination["routed_pods"] == 16
  assert not coordination["issues"] and study["routing_digest"] == file_digest(road)
  sdl = _read(REPO / "packages/datacenter-reference/src/library/modules/it-container/it-container.sdl.json")
  for side, system in routing["pods"]["IT01"].items():
    records = study["sides"]["IT01"][side]
    delivered = records["required_duty"]
    assert len(system["paths"]["supply"]) == len(system["paths"]["return"]) == 8
    assert {pipe["sdl_link"] for pipe in system["pipes"].values() if pipe["sdl_link"]} == set(
      sdl["components"]["ItContainer"]["nets"][f"Tcs{side}"]["connections"])
    assert records["fluid_volume_m3"] == pytest.approx(sum(
      p["length_m"]*pi*routing["materials"][p["material"]]["bore_m"]**2/4 for p in system["pipes"].values()))
    name = f"Rack01.tcs_in_{side.lower()}"
    last = system["paths"]["supply"]["Rack01"][-1]
    assert system["pipes"][last]["nodes"][1] == name
    assert system["pipes"][last]["points_m"][1] == pytest.approx(geometry["Rack01"]["ports_m"][name.split(".")[1]])
    for leg, paths in system["paths"].items():
      for path in paths.values():
        direction = path if leg == "supply" else path[::-1]
        assert all(system["pipes"][a]["nodes"][1] == system["pipes"][b]["nodes"][0]
                   for a, b in zip(direction, direction[1:]))
    f = delivered["racks"]["Rack01"]
    weight = (90/8)/(4.18*6)
    assert f["flow_kg_s"] == pytest.approx(weight)
    assert f["equipment_drop_kpa"] == pytest.approx((6+12+30)*(weight/1.875)**2)
    assert delivered["cdu_required_kpa"] > max(row["installed_pipe_kpa"] for row in delivered["racks"].values())
    for pipe, row in delivered["pipes"].items():
      assert row["residence_seconds"] == pytest.approx(system["pipes"][pipe]["fluid_volume_m3"]/row["flow_m3_s"])
  assert len(study["cases"]) == 38
  assert all(not row["failed_pods"] for row in study["cases"].values())
  normal = study["cases"]["normal"]["pods"]["IT01"]
  assert normal["available_side"] == "A" and normal["capacity_kw"] == pytest.approx(90)
  loss = study["cases"]["fws-peak:plant-off:CoolContainer1:local-cdu-off:A"]["pods"]["IT01"]
  assert loss["available_side"] == "B" and not loss["unserved_kw"]
  transient = _read(root / MODEL / "fws-transient.json")
  primary = transient["scenarios"]["plant-off:CoolContainer1"]["terminals"]["IT01"]["comparison_peak_c"]
  assert loss["racks"]["Rack01"]["tcs_supply_c"] == pytest.approx(primary+3)
  assert loss["racks"]["Rack01"]["tcs_return_c"] == pytest.approx(primary+9)
  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-it-pod-tcs.usda"))
  pipe = stage.GetPrimAtPath("/AIDC/TCS/IT01/A_S_Rack01")
  assert pipe.GetAttribute("texsys:identity").Get() == "IT01/A-S-Rack01"
  plan = ET.parse(root / "build/drawings/views/tcs-a.svg")
  assert {view.get("data-pipe") for view in plan.findall(f".//{SVG}g[@data-pipe]")} == routing["pods"]["IT01"]["A"]["pipes"].keys()
  detail = ET.parse(root / "build/drawings/views/tcs-section.svg")
  assert len(detail.findall(f".//{SVG}circle[@data-pipe]")) == 8


def test_tcs_recomputes_head_and_rejects_collisions_and_joint_thermal_duty(tmp_path):
  root = _checkout(tmp_path)
  result, _ = _run(root)
  assert result.status == "pass", result.diagnostics
  route = root / "src/tcs-routing.json"
  original = route.read_bytes()
  model = root / MODEL / "tcs-study.json"
  before = _read(model)
  config = _read(route)
  config["sides"]["A"]["supply"]["crossover_x_m"] += .10
  write_json(route, config)
  result, records = _run(root)
  assert result.status == "pass", result.diagnostics
  assert records["sheet-tcs-a"]["execution"] == "executed"
  assert records["dcr:fws-transient"]["execution"] == "cached"
  assert _read(model)["sides"]["IT01"]["A"]["required_duty"]["cdu_required_kpa"] > (
    before["sides"]["IT01"]["A"]["required_duty"]["cdu_required_kpa"])

  config["sides"]["A"]["supply"]["tier_m"] = config["sides"]["A"]["return"]["tier_m"]
  write_json(route, config)
  result, records = _run(root)
  assert result.status == "fail" and records["review-package"]["execution"] == "blocked"
  assert records["sheet-tcs-a"]["status"] == "pass"
  assert "AIDC_TCS_PIPE_CLASH" in result.diagnostics

  route.write_bytes(original)
  design = root / "src/tcs-study.json"
  basis = _read(design)
  original = design.read_bytes()
  basis["cdu_head_kpa_at_reference_flow"], basis["cdu_shutoff_kpa"] = 20, 30
  write_json(design, basis)
  result, records = _run(root)
  assert result.status == "fail" and records["dcr:tcs-coordination"]["status"] == "pass"
  assert records["sheet-tcs-system"]["status"] == "pass"
  scenario = _read(model)["cases"]["normal"]["pods"]["IT01"]
  assert "pump-head" in scenario["limiting_checks"] and scenario["unserved_kw"] > 10
  assert 0 < scenario["racks"]["Rack01"]["flow_m3_s"] < .0004495

  basis = _read(root / "src/tcs-study.json")
  basis["cdu_head_kpa_at_reference_flow"], basis["cdu_shutoff_kpa"] = 66, 115
  basis["cdu_approach_allowance_k"] = 6
  write_json(design, basis)
  result, records = _run(root)
  assert result.status == "fail" and records["sheet-tcs-system"]["status"] == "pass"
  cases = _read(model)["cases"]
  assert not cases["normal"]["failed_pods"]
  assert "rack-temperature" in cases["fws-peak:plant-off:CoolContainer1:local-cdu-off:A"]["pods"]["IT01"]["limiting_checks"]

  design.write_bytes(original)
  result, records = _run(root)
  assert result.status == "pass" and records["review-package"]["execution"] == "cached"


def _check_air_delivery(root):
  source = root / MODEL / "air-routing.json"
  layout = _read(source)
  study = _read(root / MODEL / "air-study.json")
  coordination = _read(root / MODEL / "air-coordination.json")
  assert len(layout["pods"]) == coordination["pods_checked"] == 16
  assert coordination["installation_volumes"] == 320
  assert not coordination["issues"] and study["air_digest"] == file_digest(source)
  assert study["tcs_digest"] == file_digest(root / MODEL / "tcs-study.json")
  first = layout["pods"]["IT01"]
  assert len(first["racks"]) == 8 and len(first["units"]) == 4
  assert first["binding"]["A"]["Rack05"]["unit"] == "PaA1"
  assert first["binding"]["B"]["Rack04"]["unit"] == "PaB2"
  cable = _read(REPO / "packages/datacenter-reference/src/library/modules/it-container/it-container.sdl.json")
  for side, entries in first["binding"].items():
    assert {row[leg] for row in entries.values() for leg in ("supply_sdl", "return_sdl")} == set(
      cable["components"]["ItContainer"]["nets"][f"Air{side}"]["connections"])
  shaft = first["racks"]["Rack01"]
  assert shaft["gap_m"] == pytest.approx(.064)
  assert shaft["flow_area_m2"] == pytest.approx(.064*(.8-.048))
  assert shaft["upflow_length_m"] == pytest.approx(2.32-1.1)
  assert len(study["cases"]) == 11 and all(not records["failed_pods"] for records in study["cases"].values())
  normal = study["cases"]["normal"]["pods"]["IT01"]
  assert normal["active_units"] == ["PaA1", "PaA2"]
  assert normal["sensible_load_kw"] == pytest.approx(120-90+3)
  assert normal["supplied_m3_s"] == pytest.approx(2*.64*.15*15)
  hot = 21.5+normal["sensible_load_kw"]/(normal["captured_m3_s"]*1.2*1.006)
  first_rack = normal["racks"]["Rack01"]
  assert first_rack["inlet_c"] == pytest.approx(21.5+(hot-21.5)*first_rack["recirculated_fraction"])
  assert abs(normal["energy_residual_kw"]) < 1e-9
  assert abs(normal["hot_pool_residual_kw"]) < 1e-9
  bus = study["cases"]["electrical-train-off:A"]
  assert bus["tcs_case"] == "local-cdu-off:A"
  assert bus["pods"]["IT01"]["active_units"] == ["PaB1", "PaB2"]
  assert bus["pods"]["IT01"]["pipe_dew_margin_k"] == pytest.approx(27-12)
  assert study["cases"]["unit-off:PaA1"]["pods"]["IT01"]["active_units"] == ["PaB1", "PaA2"]

  stage = Usd.Stage.Open(str(root / "build/scenes/aidc-it-pod-air.usda"))
  vent = stage.GetPrimAtPath("/AIDC/Air/IT01/Rack01_EX")
  assert vent.GetAttribute("texsys:identity").Get() == "IT01/Rack01-EX"
  assert stage.GetPrimAtPath("/AIDC/TCS/IT01/A_S_Rack01")
  plan = ET.parse(root / "build/drawings/views/air-plan.svg")
  assert {tag.get("data-air-unit") for tag in plan.findall(f".//{SVG}g[@data-air-unit]")} == set(first["units"])
  environment = ET.parse(root / "build/drawings/views/air-environment.svg")
  for tag in environment.findall(f".//{SVG}rect[@data-rack]"):
    expected = bus["pods"]["IT01"]["racks"][tag.get("data-rack")]["inlet_c"]
    assert float(tag.get("data-temperature")) == pytest.approx(expected, abs=1e-6)


def test_air_geometry_and_spare_changes_recompile_without_hiding_failed_environment(tmp_path):
  root = _checkout(tmp_path)
  result, _ = _run(root)
  assert result.status == "pass", result.diagnostics
  route = root / "src/air-routing.json"
  previous = route.read_bytes()
  source = _read(route)
  model = root / MODEL / "air-study.json"
  normal = _read(model)["cases"]["normal"]["pods"]["IT01"]
  source["supply"]["duct_width_m"] = .68
  write_json(route, source)
  result, records = _run(root)
  assert result.status == "pass", result.diagnostics
  assert records["dcr:tcs-study"]["execution"] == "cached"
  assert records["sheet-air-environment"]["execution"] == "executed"
  changed = _read(model)["cases"]["normal"]["pods"]["IT01"]
  assert changed["supplied_m3_s"] > normal["supplied_m3_s"]
  assert changed["racks"]["Rack01"]["inlet_c"] < normal["racks"]["Rack01"]["inlet_c"]

  source["supply"]["duct_width_m"] = .32
  write_json(route, source)
  result, records = _run(root)
  assert result.status == "fail" and records["review-package"]["execution"] == "blocked"
  assert records["dcr:air-coordination"]["status"] == "pass"
  assert records["sheet-air-environment"]["status"] == "pass"
  branch = _read(model)["cases"]["normal"]["pods"]["IT01"]
  assert any(not row["checks"]["inlet-temperature"] for row in branch["racks"].values())
  route.write_bytes(previous)

  placement = root / "src/pod-layout.json"
  original = placement.read_bytes()
  posed = _read(placement)
  posed["installation_overrides"] = {"IT01": {"Rack01": {"minimum_m": [.11, .055, 0]}}}
  write_json(placement, posed)
  result, records = _run(root)
  assert result.status == "fail"
  assert records["dcr:pod-coordination"]["status"] == "pass"
  assert "AIDC_AIR_REAR_GAP" in result.diagnostics
  assert records["sheet-air-section"]["status"] == "pass"
  placement.write_bytes(original)

  design = root / "src/air-study.json"
  original = design.read_bytes()
  settings = _read(design)
  settings["unavailable_units"] = ["IT01/PaA1", "IT01/PaB1"]
  write_json(design, settings)
  result, records = _run(root)
  assert result.status == "fail" and records["sheet-air-environment"]["status"] == "pass"
  broken = _read(model)["cases"]["normal"]["pods"]["IT01"]
  assert not broken["checks"]["spare-coverage"]
  assert broken["racks"]["Rack01"]["directed_supply_m3_s"] == 0
  design.write_bytes(original)

  result, records = _run(root)
  assert result.status == "pass" and records["review-package"]["execution"] == "cached"
