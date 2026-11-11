import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from pxr import Usd, UsdGeom
import pytest

from texsys.api import build
from texsys.dependencies import lock_project, sync_project
from texsys.project import initialize_project
from texsys.studio.library_sources import library_index, reference_library_entry
from texsys.usd_brep import _prim_name


REPO = Path(__file__).parents[1]
OUTPUT = Path("build/hvac/air-room")


@pytest.fixture
def air_room(tmp_path):
  project = tmp_path / "room"
  shutil.copytree(REPO / "examples/hvac-air-cooled-room", project, ignore=shutil.ignore_patterns("build"))
  (project / "build").mkdir()
  manifest = project / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(project)
  return project


def test_air_room_builds_dimensioned_scene_and_deterministic_evidence(air_room):
  run = build(air_room)
  assert run.status == "pass"
  output = air_room / OUTPUT
  report = json.loads((output / "checks.json").read_text())
  assert report["counts"] == {
    "cooling_units": 2, "floor_panels": 272, "floor_slots_used_for_downflow_openings": 8,
    "pedestals": 313, "racks": 16, "solid_panels": 256, "steel_beams": 7,
    "steel_columns": 6, "supply_grilles": 16,
  }
  checks = {item["id"]: item for item in report["checks"]}
  assert checks["clear-aisle-ColdAisle"]["actual_m"] == 1.2
  assert checks["air-budget-AC-01-out"]["active"] == ["AC-02"]
  assert checks["air-budget-AC-01-out"]["total_load_kw"] == 50
  assert checks["air-budget-AC-01-out"]["required_airflow_m3_s"] == pytest.approx(4.1418)
  stage = Usd.Stage.Open(str(output / "room.usda"))
  assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
  assert UsdGeom.GetStageMetersPerUnit(stage) == 1
  root = stage.GetDefaultPrim()
  assert len(root.GetChild("Steel").GetChildren()) == 13
  assert len(root.GetChild("Grilles").GetChildren()) == 16
  assert len(root.GetChild("PenetrationFrames").GetChildren()) == 2
  assert len(root.GetChild("Racks").GetChildren()) == 16
  assert root.GetChild("Zones").GetChild("SupplyPlenum").GetCustomDataByKey("maxMeters")[2] == .45
  assert len(root.GetChild("Axes").GetChildren()) == 6
  assert ET.parse(output / "floor-plan.svg").getroot().tag == "{http://www.w3.org/2000/svg}svg"
  gallery = Usd.Stage.Open(str(output / "module-library.usda"))
  assert not gallery.GetCompositionErrors()
  packages = gallery.GetDefaultPrim().GetChildren()
  assert len(packages) == 21
  assert {prim.GetCustomDataByKey("texsys:definition"): prim.GetCustomDataByKey("texsys:version")
          for prim in packages} == report["module_packages"]
  assert "structure:library/modules/steel-support/steel-support.sdl.json" in report["module_packages"]
  assert "structure:library/modules/w-form-concrete-slab/w-form-concrete-slab.sdl.json" in report["module_packages"]
  assert all(prim.GetChild("Geometry").GetChildren() for prim in packages)
  rack_a = root.GetChild("Racks").GetChild(_prim_name("RA-01")).GetChild("Equipment")
  rack_b = root.GetChild("Racks").GetChild(_prim_name("RB-01")).GetChild("Equipment")
  assert {child.GetAttribute("texsys:identity").Get() for child in rack_a.GetChildren()} == {"PDU-A", "SW-A", "SRV-A"}
  assert {child.GetAttribute("texsys:identity").Get() for child in rack_b.GetChildren()} == {"PDU-B", "SW-B"}
  for cable, a, b in (
    ("SM-01", (rack_a, "SW-A", "fiber"), (rack_b, "SW-B", "fiber")),
    ("CAT6A-01", (rack_a, "SW-A", "copper"), (rack_a, "SRV-A", "management")),
  ):
    path = root.GetChild("CableRoutes").GetChild(_prim_name(cable))
    points = UsdGeom.BasisCurves(path).GetPointsAttr().Get()
    for endpoint, (rack, device, port) in zip((points[0], points[-1]), (a, b)):
      anchor = rack.GetChild(_prim_name(device)).GetChild("Ports").GetChild(port)
      location = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
      assert tuple(endpoint) == pytest.approx(tuple(location))
    assert path.GetCustomDataByKey("texsys:routeLengthM") > 0
  snapshot = {path.name: path.read_bytes() for path in (*run.items, run.report)}
  second = build(air_room)
  assert second.status == "pass"
  assert {path.name: path.read_bytes() for path in (*second.items, second.report)} == snapshot


def test_air_room_reports_obstructed_aisle_and_insufficient_backup_airflow(air_room):
  layout_path = air_room / "src/room.layout.json"
  layout = json.loads(layout_path.read_text())
  layout["cooling_units"]["AC-01"]["origin_m"] = [3.6, 6.0]
  layout_path.write_text(json.dumps(layout))
  model_path = air_room / "src/system.sdl.json"
  model = json.loads(model_path.read_text())
  component = model["components"]["DownflowCRAC"]
  airflow = component["behavior"]["parameters"]["supply_airflow_m3_s"]
  model["components"]["AirCooledRoom"]["instances"]["AC-02"]["parameters"] = {
    "supply_airflow_m3_s": {**airflow, "value": 3.0},
  }
  model_path.write_text(json.dumps(model))
  result = build(air_room)
  assert result.status == "fail"
  report = json.loads((air_room / OUTPUT / "checks.json").read_text())
  checks = {item["id"]: item for item in report["checks"]}
  assert checks["clear-aisle-HotNorth"]["obstructions"] == ["AC-01"]
  assert checks["clear-aisle-HotNorth"]["status"] == "fail"
  assert checks["air-budget-normal"]["status"] == "pass"
  assert checks["air-budget-AC-01-out"]["status"] == "fail"
  assert checks["air-budget-AC-02-out"]["status"] == "pass"
  assert all(item["partial"] for item in json.loads(result.report.read_text())["artifacts"])


def test_air_room_is_a_versioned_library_source_with_syncable_module_assets(air_room, tmp_path):
  consumer = tmp_path / "consumer"
  initialize_project(consumer)
  index = library_index(consumer, "src", [str(air_room), str(REPO / "packages/structure")])
  assert not index["diagnostics"] and not index["source_errors"]
  assert len(index["library"]) == 21
  assert all(item["kind"] == "modules" for item in index["library"])
  source = next(item for item in index["sources"] if item["name"] == "hvac-air-cooled-room")
  entry = "src/library/modules/rack-42u/rack-42u.sdl.json#Rack42U"
  reference_library_entry(consumer, {"folder": str(air_room), "digest": source["digest"], "entry": entry})
  lock_project(consumer, check=True)
  sync_project(consumer)
  asset = consumer / "build/dependencies/hvac-air-cooled-room/src/library/modules/rack-42u/assets/geometry.usda"
  assert asset.read_bytes() == (air_room / "src/library/modules/rack-42u/assets/geometry.usda").read_bytes()
  structure = next(item for item in index["sources"] if item["name"] == "structure")
  structural_entries = [item["definition"] for item in index["library"] if item["source"] == structure["id"]]
  assert len(structural_entries) == 9
  assert any(entry.endswith("steel-support.sdl.json#SteelSupport") for entry in structural_entries)
  assert any(entry.endswith("w-form-concrete-slab.sdl.json#WFormConcreteSlab") for entry in structural_entries)
  steel_asset = consumer / "build/dependencies/structure/src/library/modules/steel-support/assets/geometry.usda"
  assert steel_asset.read_bytes() == (REPO / "packages/structure/src/library/modules/steel-support/assets/geometry.usda").read_bytes()


def test_changing_grid_axis_moves_bound_racks_and_steel(air_room):
  layout_path = air_room / "src/room.layout.json"
  layout = json.loads(layout_path.read_text())
  layout["axes"]["x"]["B"] = 6.3
  layout_path.write_text(json.dumps(layout))

  # The fixed cold-aisle bounds remain independently authored and flag the moved racks.
  result = build(air_room)
  assert result.status == "fail"
  report = json.loads((air_room / OUTPUT / "checks.json").read_text())
  checks = {check["id"]: check for check in report["checks"]}
  assert checks["rack-fronts-face-cold-aisle"]["status"] == "fail"
  stage = Usd.Stage.Open(str(air_room / OUTPUT / "room.usda"))
  root = stage.GetDefaultPrim()
  plate = root.GetChild("Steel").GetChild(_prim_name("COL-B-1")).GetChild("BasePlate")
  rack = root.GetChild("Racks").GetChild(_prim_name("RA-01"))
  assert UsdGeom.Xformable(plate).ComputeLocalToWorldTransform(0).ExtractTranslation()[0] == pytest.approx(6.3)
  assert UsdGeom.Xformable(rack).ComputeLocalToWorldTransform(0).ExtractTranslation()[0] == pytest.approx(4.5)


def test_air_room_rejects_invalid_module_anchor_and_overlapping_rack_slots(air_room):
  manifest = air_room / "src/library/modules/rack-42u/module.toml"
  original = manifest.read_text()
  manifest.write_text(original.replace("/Module/Ports/front", "/Module/Ports/missing"))
  result = build(air_room)
  assert result.status == "error"
  assert "unresolved geometry or drawing anchor for port front" in result.report.read_text()

  manifest.write_text(original)
  layout_path = air_room / "src/room.layout.json"
  layout = json.loads(layout_path.read_text())
  layout["rack_equipment"]["SRV-A"]["u"] = 29
  layout_path.write_text(json.dumps(layout))
  result = build(air_room)
  assert result.status == "error"
  assert "overlapping rack U positions" in result.report.read_text()
