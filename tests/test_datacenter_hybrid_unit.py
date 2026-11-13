import json
import math
from pathlib import Path
import shutil

from pxr import Gf, Usd, UsdGeom
import pytest

from project_process import build_project as build
from texsys.dependencies import lock_project
from texsys.usd_brep import _prim_name
from texsys_electrical.protection import Breaker, CircuitSection, FaultSource, Impedance, SourceCondition, check_radial_fault
from texsys_hvac_dynamics.facility_hydraulics import FacilityWaterHydraulics
from texsys_hvac_dynamics.facility_water import FacilityWaterNetwork
from texsys_structure.beam import simple_beam


REPO = Path(__file__).parents[1]
OUTPUT = Path("build/datacenter")


@pytest.fixture
def hybrid_unit(tmp_path):
  root = tmp_path / "hybrid"
  shutil.copytree(REPO / "examples/datacenter-hybrid-unit", root,
                  ignore=shutil.ignore_patterns("build", ".texsys-write.lock"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace("../../packages/", f"{REPO}/packages/"))
  lock_project(root)
  return root


def test_hybrid_unit_closes_power_cooling_and_floor_on_same_racks(hybrid_unit):
  result = build(hybrid_unit)
  assert result.status == "pass", result.diagnostics
  report = json.loads((hybrid_unit / OUTPUT / "unit-study.json").read_text())
  assert report["heat_budget_kw"] == {
    "it": 400, "liquid_it": 300, "air_it": 100, "other_room": 10, "total_air": 110,
  }
  normal, outage, loss, loss_both = (
    report["scenarios"][name] for name in ("normal", "supply-a-out", "cdu-a-out", "both-cdus-out")
  )
  assert normal["liquid_available_kw"] == 660
  assert outage["power"]["paths"]["Rack-01"] == [
    "Utility-B", "Transformer-B", "Switchboard-B", "UPS-B", "Busway-B", "Rack-01",
  ]
  assert outage["active_air_units"] == ["PrecisionAir-B"]
  assert outage["liquid_available_kw"] == loss["liquid_available_kw"] == 330
  assert loss_both["status"] == "fail"
  assert not loss_both["checks"]["liquid-capacity"]
  assert loss_both["checks"]["air-capacity"]

  floor = json.loads((hybrid_unit / OUTPUT / "floor-study.json").read_text())
  assert set(floor["rack_loads"]) == {f"Rack-{i:02d}" for i in range(1, 5)}
  assert floor["rack_loads"]["Rack-01"]["vertical_load_kn"] == pytest.approx(17.652, abs=.001)
  assert floor["phases"]["pour"]["gross_uniform_kn_m2"] > floor["phases"]["service"]["gross_uniform_kn_m2"]
  assert floor["structural_review"] == "pending"
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "deck-slab.usda"))
  root = stage.GetDefaultPrim()
  assert root.GetChild("SteelForm").IsA(UsdGeom.Mesh)
  assert root.GetChild("PouredConcrete").IsA(UsdGeom.Mesh)
  bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(root).ComputeAlignedRange()
  assert tuple(bounds.GetSize()) == pytest.approx((3.6, 8, 0.17))

  building = json.loads((hybrid_unit / OUTPUT / "building-study.json").read_text())
  assert (building["phases"]["initial"]["unit_count"], building["phases"]["initial"]["it_kw"]) == (50, 20000)
  assert (building["phases"]["full"]["unit_count"], building["phases"]["full"]["it_kw"]) == (100, 40000)
  assert building["phases"]["full"]["heat_budget_kw"]["liquid_it"] == 30000
  architecture = json.loads((hybrid_unit / OUTPUT / "architecture-study.json").read_text())
  assert architecture["counts"] == {"rooms": 20, "walls": 68, "doors": 18, "shafts": 2,
                                   "stairs": 2, "wall_sleeves": 37}
  assert architecture["issues"] == []
  assert all(family["entry"].startswith("@architecture/") for family in architecture["families"].values())
  stair = architecture["stair_checks"]["East"]["L04-L03"]
  assert stair["steps"] == 36 and stair["egress_distance_matches"]
  assert stair["riser_m"] == pytest.approx(.175)
  assert stair["same_lane_headroom_m"] == pytest.approx(3.32)
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "architecture-layout.usda"))
  cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
  wall = stage.GetDefaultPrim().GetChild("Walls").GetChild(_prim_name("L03/Hall-Services"))
  solids = [cache.ComputeWorldBound(piece).ComputeAlignedRange() for piece in wall.GetChildren()]
  door = architecture["doors"]["L03/CompartmentDoor-North"]
  sleeve = architecture["wall_sleeves"]["A-L03-supply@L03/Hall-Services#1"]
  assert not any(box.Contains(Gf.Vec3d(*door["center_m"])) for box in solids)
  assert not any(box.Contains(Gf.Vec3d(*sleeve["center_m"])) for box in solids)
  assert any(box.Contains(Gf.Vec3d(49, 20, 15)) for box in solids)
  east = stage.GetDefaultPrim().GetChild("Stairs").GetChild("East")
  steps = east.GetChild(_prim_name("L02-L01")).GetChildren()
  bypass_y = architecture["doors"]["Discharge-East"]["center_m"][1]
  assert len(steps) == 36
  assert all(cache.ComputeWorldBound(step).ComputeAlignedRange().GetMin()[1] > bypass_y + .9 for step in steps)
  equipment = json.loads((hybrid_unit / OUTPUT / "cooling-equipment.json").read_text())
  assert equipment["counts"] == {"placements": 400, "fws_connections": 400}
  assert equipment["modules"]["PrecisionAir-A"]["entry"].startswith("@hvac-equipment/library/modules/")
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "cooling-equipment-layout.usda"))
  assert len(stage.GetDefaultPrim().GetChildren()) == 400
  device = "L04/Unit-01-01/CDU-B"
  prim = stage.GetDefaultPrim().GetChild(_prim_name(device))
  assert prim.GetAttribute("texsys:identity").Get() == device
  support = equipment["placements"][device]["structural_support"]
  assert support["floor_bay"] == "L04/Unit-01-01"
  assert support["load_kn"] == pytest.approx(650 * 9.80665 / 1000)
  assert len(support["contact_ids"]) == 4
  outlet = equipment["placements"][device]["ports_m"]["facility_in"]
  assert outlet == pytest.approx((5.32, 10.4, 22.5))
  tcs = json.loads((hybrid_unit / OUTPUT / "tcs-study.json").read_text())
  assert tcs["counts"] == {"racks": 400, "rack_ports": 1600, "cdu_ports": 400, "pipe_routes": 4400}
  assert tcs["conflicts"] == []
  cases = tcs["cases"]
  room = "L03/Unit-01-01"
  assert cases["initial-normal"]["total_delivered_kw"] == pytest.approx(15000)
  assert cases["full-normal"]["total_delivered_kw"] == pytest.approx(30000)
  assert cases["full-local-rack-isolated"]["shortage_kw_by_rack"] == {f"{room}/Rack-04": 75.0}
  loss = cases["full-local-fws-unavailable"]["shortage_kw_by_rack"]
  assert len(loss) == 100 and all(key.startswith("L03/") and value == 75.0 for key, value in loss.items())
  profile = tcs["profiles"][cases["full-a-power-out"]["profiles_by_room"][room]]
  assert profile["fws_delivered_kw_by_cdu"] == {"CDU-A": 0.0, "CDU-B": 300.0}
  assert profile["unavailable_pumps"] == ["CDU-A-TCS-Pump"]
  circuit = profile["circuits_by_rack"]["Rack-04"]["CDU-B"]
  assert circuit["flow_kg_s"] == pytest.approx(1.875)
  assert circuit["supply_c"] == 30 and circuit["return_c"] == 40
  losses = profile["hydraulics"]["banks"]["CDU-B"]["pressure_components_kpa_by_terminal"]["Rack-04"]
  assert sum(circuit["component_loss_kpa"].values()) == pytest.approx(losses["terminal_equipment"])
  takeover = cases["full-local-a-branch-takeover"]
  dispatch = takeover["dispatch"]
  assert takeover["status"] == "pass" and dispatch["mode"] == "coupled"
  assert dispatch["baseline_unused_fws_kw_by_room"] == {room: 75.0}
  assert dispatch["fws_delivered_kw_by_train"] == pytest.approx({"HeatRejection-A": 29925, "HeatRejection-B": 75})
  joint = dispatch["joint_fws"]
  assert joint["priority"] == "baseline-retention"
  assert joint["cdu_heat_balance_error_kw"] < 1e-6
  assert joint["delivered_kw_by_unit_train"][room] == pytest.approx({"HeatRejection-A": 225, "HeatRejection-B": 75})
  assert joint["delivered_kw_by_unit_train"]["L03/Unit-01-02"] == pytest.approx({"HeatRejection-A": 300, "HeatRejection-B": 0})
  profile = tcs["profiles"][takeover["profiles_by_room"][room]]
  assert set(profile["circuits_by_rack"]["Rack-04"]) == {"CDU-B"}
  blocked = cases["full-local-a-branch-backup-fws-blocked"]
  assert blocked["shortage_kw_by_rack"] == {f"{room}/Rack-04": 75.0}
  assert blocked["dispatch"]["joint_fws"]["blocked_units_by_train_level"] == {"L03/HeatRejection-B": ["Unit-01-01"]}
  derated = cases["full-local-pump-head-limited"]
  assert derated["total_delivered_kw"] == pytest.approx(30000)
  profile = tcs["profiles"][derated["profiles_by_room"][room]]
  assert all(0 < kw < 180 for kw in profile["fws_delivered_kw_by_cdu"].values())
  assert not profile["shortage_kw_by_terminal"]
  severe = cases["full-local-both-pumps-severely-limited"]
  assert set(severe["shortage_kw_by_rack"]) == {f"{room}/Rack-0{i}" for i in range(1, 5)}
  assert sum(severe["shortage_kw_by_rack"].values()) == pytest.approx(93.706, abs=.02)
  assert severe["dispatch"]["joint_fws"]["delivered_kw_by_unit_train"]["L03/Unit-01-02"] == pytest.approx(
    {"HeatRejection-A": 300, "HeatRejection-B": 0})
  constrained = cases["full-limited-fws-and-tcs"]
  joint = constrained["dispatch"]["joint_fws"]
  assert constrained["total_delivered_kw"] == pytest.approx(26307.113, abs=.05)
  assert constrained["total_delivered_kw"] == pytest.approx(joint["delivered_kw_by_train"]["HeatRejection-B"])
  profile = tcs["profiles"][constrained["profiles_by_room"][room]]
  assert profile["hydraulics"]["method"] == "pressure-constrained"
  assert profile["hydraulics"]["banks"]["CDU-B"]["head_margin_kpa"] == pytest.approx(0, abs=.001)
  assert joint["hydraulics"]["banks"]["HeatRejection-B"]["head_margin_kpa"] == pytest.approx(0, abs=.001)
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "tcs-layout.usda"))
  root = stage.GetDefaultPrim()
  assert len(root.GetChild("Racks").GetChildren()) == 400
  assert len(root.GetChild("Pipes").GetChildren()) == 4400
  rack_prim = root.GetChild("Racks").GetChild(_prim_name(f"{room}/Rack-01"))
  port = UsdGeom.Xform(rack_prim.GetChild("Ports").GetChild("tcs_in_a"))
  xyz = port.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
  assert tuple(xyz) == pytest.approx((5.25, 6.34, 16.2))
  controls = json.loads((hybrid_unit / OUTPUT / "controls-study.json").read_text())
  assert controls["binding"]["entry"].startswith("@controls/library/modules/confirmed-changeover/")
  assert controls["mismatched_cases"] == []
  cases = controls["cases"]
  fast = cases["confirmed-fast-transfer"]
  assert fast["status"] == "pass" and fast["rack"] == f"{room}/Rack-04"
  assert fast["sequence"]["isolation_proved_at_s"] == 11
  assert fast["sequence"]["proved_at_s"] == 13 and fast["backup_ready_at_s"] == 19
  assert fast["thermal"]["nodes"]["rack_solid"]["peak_c"] == pytest.approx(57.28, abs=.04)
  assert abs(fast["thermal"]["energy_kj"]["residual"]) < .001
  ramp = next(frame for frame in fast["thermal_intervals"] if frame["start_s"] == 13)
  assert ramp["end_s"] == 19 and ramp["liquid_ceiling_kw"] == pytest.approx([0, 75])
  states = controls["hydraulic_states"]
  held = states[fast["hydraulic_states"]["interlocked"]]
  assert held["shortage_kw_by_terminal"] == {"Rack-04": 75.0}
  assert held["whole_building_delivered_kw"] == pytest.approx(29925)
  restored = states[fast["hydraulic_states"]["transferred"]]
  assert restored["circuits_by_rack"]["Rack-04"]["CDU-B"]["flow_kg_s"] == pytest.approx(1.875)
  assert restored["whole_building_delivered_kw"] == pytest.approx(30000)
  for key in ("return-valve-stuck", "return-feedback-stale"):
    sequence = cases[key]["sequence"]
    assert sequence["lockout"]["reason"] == "isolation-timeout"
    assert not any(event.get("action") == "enable" for event in sequence["trace"])
  assert cases["detector-silent"]["sequence"]["final_state"] == "armed"
  assert cases["detector-quality-bad"]["sequence"]["lockout"]["reason"] == "fault-point-unavailable"
  blocked = cases["backup-fws-path-blocked"]["sequence"]
  assert blocked["lockout"]["reason"] == "proof-timeout"
  assert blocked["proved_at_s"] is None
  assert next(event for event in blocked["physical_events"] if event["kind"] == "backup-flow-probe")["flow_kg_s"] == 0
  lost = cases["backup-flow-lost-after-transfer"]
  assert lost["backup_ready_at_s"] == 19 and not lost["retained_backup_at_end"]
  assert lost["sequence"]["lockout"] == {"at_s": 43, "kind": "lockout", "reason": "backup-proof-lost"}
  late = cases["detected-too-late"]
  assert late["retained_backup_at_end"] and late["status"] == "fail"
  assert late["thermal"]["nodes"]["rack_solid"]["peak_c"] == pytest.approx(72.72, abs=.04)
  failure = building["scenarios"]["full-shared-heat-rejection-out"]
  assert failure["checks"] == {"unit-scenario": True, "shared-power": True, "shared-liquid-heat-rejection": False,
                               "leak-isolation": True}
  assert failure["status"] == "fail"
  cases = building["scenarios"]
  riser = cases["full-riser-a-out"]["fws"]
  assert riser["delivered_kw_by_train"] == pytest.approx({"HeatRejection-A": 0, "HeatRejection-B": 30000})
  assert riser["flow_kg_s_by_segment"]["B-riser-1-return"] == pytest.approx(30000 / 41.8)
  returns = cases["full-l03-returns-out"]["fws"]
  assert returns["shortage_kw_by_terminal"] == pytest.approx({"L03": 7500})
  assert len(returns["units_at_risk"]) == 25
  assert all(identity.startswith("L03/") for identity in returns["units_at_risk"])
  isolated = cases["full-l03-leak-isolated"]
  assert isolated["fws"]["interlocks"]["Leak-L03-A"]["confirmed_closed"] == ["IV-L03-A-R", "IV-L03-A-S"]
  assert isolated["fws"]["available_trains_by_terminal"]["L03"] == ["HeatRejection-B"]
  stuck = cases["full-l03-leak-valve-stuck"]
  assert stuck["checks"]["shared-liquid-heat-rejection"]
  assert not stuck["checks"]["leak-isolation"]
  detector_out = cases["full-l03-leak-detector-out"]["fws"]["interlocks"]["Leak-L03-A"]
  assert not detector_out["detected"]
  assert detector_out["commanded_closed"] == []

  drainage = json.loads((hybrid_unit / OUTPUT / "drainage-study.json").read_text())["scenarios"]
  assert drainage["leak-isolated"]["storage_demand_l_by_receiver"] == {"Collection-L01": 90}
  assert drainage["valve-stuck"]["storage_excess_l"] == {"Collection-L01": 360}
  assert drainage["detector-out"]["released_l"] == {"Leak-L03-A": 540}
  assert drainage["drain-trunk-out"]["disconnected_releases"] == ["Leak-L03-A"]


def test_standby_follows_power_transfer_energy_and_cooling_by_floor(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:standby-study",)).status == "pass"
  cases = json.loads((hybrid_unit / OUTPUT / "standby-study.json").read_text())["scenarios"]
  transfer = cases["full-transfer"]
  battery, standby = transfer["intervals"]
  load = "L03/Rack-01"
  assert battery["service"]["paths"][load][0] == "L03/Battery-A"
  assert battery["remaining_kwh"]["L03/Battery-A"] == pytest.approx(50 / 3)
  assert battery["remaining_kwh"]["Generator-A"] == 1000
  assert standby["service"]["paths"][load][:2] == ["Generator-A", "Incoming-A"]
  assert standby["fws"]["delivered_kw_by_terminal"]["L03"] == pytest.approx(7500)
  assert transfer["longest_interruption_s"]["it"] == {}
  assert transfer["longest_interruption_s"]["air"] == dict.fromkeys(("L01", "L02", "L03", "L04"), 12)
  assert transfer["longest_interruption_s"]["liquid"] == transfer["longest_interruption_s"]["air"]

  delayed = cases["full-delayed-start"]
  assert [step["start_s"] for step in delayed["intervals"]] == pytest.approx([0, 18, 36, 45])
  assert delayed["intervals"][1]["service"]["paths"][load][0] == "L03/Battery-B"
  assert delayed["longest_interruption_s"]["it"][load] == pytest.approx(9)
  assert delayed["longest_interruption_s"]["liquid"]["L03"] == 45
  assert not delayed["checks"]["it-interruption"]
  assert not delayed["checks"]["liquid-interruption"]

  local = cases["full-l03-transformers-out"]
  assert local["longest_interruption_s"]["liquid"] == {"L03": 45}
  assert local["intervals"][0]["fws"]["shortage_kw_by_terminal"] == pytest.approx({"L03": 7500})
  assert cases["full-generator-a-unavailable"]["intervals"][-1]["service"]["paths"][load][0] == "Generator-B"
  combined = cases["full-generator-and-riser-out"]
  assert combined["checks"]["it-interruption"] and not combined["checks"]["liquid-interruption"]
  assert cases["full-energy-exhaustion"]["intervals"][-1]["remaining_kwh"]["Generator-B"] == 0
  assert load in cases["full-energy-exhaustion"]["intervals"][-1]["unserved_it"]


def test_standby_checks_apparent_capacity_independently(hybrid_unit):
  path = hybrid_unit / "src/standby.study.json"
  design = json.loads(path.read_text())
  design["external_sources"]["Generator-A"].update(kw=59000, kva=59000)
  path.write_text(json.dumps(design))

  assert build(hybrid_unit, targets=("datacenter:standby-study",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "standby-study.json").read_text())
  transfer = report["scenarios"]["full-transfer"]
  assert transfer["checks"]["active-power-capacity"]
  assert not transfer["checks"]["apparent-power-capacity"]
  assert transfer["intervals"][-1]["overloaded_kva"]["Generator-A"] > 0


def test_thermal_ride_through_tracks_restart_power_and_recovery(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:ride-through-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "ride-through-study.json").read_text())
  standby = json.loads((hybrid_unit / OUTPUT / "standby-study.json").read_text())["scenarios"]
  cases = report["cases"]
  steady = cases["grid-steady"]["levels"]["L03"]
  assert {node: result["peak_c"] for node, result in steady["nodes"].items()} == {
    "cold_zone_air": 25, "rack_solid": 54, "tcs_bulk": 35,
  }
  fast = cases["transfer-fast"]["levels"]["L03"]
  assert fast["nodes"]["cold_zone_air"]["peak_c"] == pytest.approx(28.51, abs=.03)
  starts = {frame["start_s"]: frame for frame in fast["thermal_intervals"]}
  assert starts[12]["air_ceiling_kw"] == [0, 0]
  assert starts[16]["circulation"] == [0, .25]
  assert starts[16]["liquid_ceiling_kw"] == [0, 0]
  assert starts[18]["liquid_ceiling_kw"][1] > 0

  late = cases["delayed-start-recovery"]["levels"]["L03"]
  air = late["nodes"]["cold_zone_air"]
  assert air["first_above_s"] == pytest.approx(25.36, abs=.05)
  assert air["recovered_at_s"] == pytest.approx(66.74, abs=.05)
  assert air["seconds_above"] == pytest.approx(air["recovered_at_s"] - air["first_above_s"])
  assert any(frame["start_s"] == 36 and frame["it_kw"] == 0 for frame in late["thermal_intervals"])
  assert cases["local-transformer-outage"]["affected_levels"] == {"L03": ["rack_solid", "cold_zone_air"]}
  no_fws = cases["circulation-without-fws"]["levels"]["L03"]
  assert no_fws["nodes"]["tcs_bulk"]["first_above_s"] == pytest.approx(39.88, abs=.05)
  assert any(frame["circulation"] == [1, 1] for frame in no_fws["thermal_intervals"])
  assert all(frame["liquid_ceiling_kw"] == [0, 0] for frame in no_fws["thermal_intervals"])
  for case in cases.values():
    for snapshot, extra in zip(standby[case["standby_scenario"]]["intervals"], case["fws_pull_down"], strict=True):
      assert all(extra["fws_ceiling_kw_by_level"][level] >= value - 1e-6
                 for level, value in snapshot["fws"]["delivered_kw_by_terminal"].items())
    for floor in case["levels"].values():
      balance = floor["energy_kj"]
      assert balance["heat_input"] == pytest.approx(
        balance["air_removed"] + balance["liquid_removed"] + balance["stored_change"], rel=1e-8
      )


def test_thermal_gate_reacts_to_declared_temperature_limit(hybrid_unit):
  path = hybrid_unit / "src/ride-through.study.json"
  design = json.loads(path.read_text())
  design["nodes"]["cold_zone_air"]["max_c"] = 28
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:ride-through-study",)).status == "fail"
  cases = json.loads((hybrid_unit / OUTPUT / "ride-through-study.json").read_text())["cases"]
  assert cases["grid-steady"]["status"] == "pass"
  assert cases["transfer-fast"]["status"] == "fail"
  assert "cold_zone_air" in cases["transfer-fast"]["affected_levels"]["L03"]


def test_makeup_water_tracks_heat_load_treatment_and_inventory(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:water-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "water-study.json").read_text())
  cases = report["cases"]
  normal = cases["full-normal"]
  duty = normal["intervals"][0]
  half = cases["initial-normal"]["intervals"][0]
  assert duty["makeup_demand_m3_h"] == pytest.approx(49.72, abs=.01)
  assert half["makeup_demand_m3_h"] == pytest.approx(duty["makeup_demand_m3_h"] / 2)
  assert normal["tank_end_m3"] == 300
  for row in normal["intervals"]:
    for balance in row["requested_by_train"].values():
      assert balance["makeup_m3_h"] == pytest.approx(
        balance["evaporation_m3_h"] + balance["drift_m3_h"] + balance["blowdown_m3_h"]
      )
    for route in row["production_routes"].values():
      assert route["raw_m3_h"] * route["feed_tds_mg_l"] == pytest.approx(
        route["product_m3_h"] * route["product_tds_mg_l"]
        + (route["raw_m3_h"] - route["product_m3_h"]) * route["reject_tds_mg_l"]
      )
  assert report["product_tds_limit_mg_l"] == 100
  alternate = cases["single-treatment-reclaimed"]
  assert alternate["status"] == "pass"
  assert all(list(row["production_routes"]) == ["Reclaimed/Treatment-B"] for row in alternate["intervals"])
  assert alternate["intervals"][0]["unpowered_treatments"] == ["Treatment-A"]

  short = cases["two-hour-source-outage"]
  assert short["first_limited_h"] is None and short["tank_end_m3"] == 300
  assert short["intervals"][0]["tank_end_m3"] == pytest.approx(180 - 2 * duty["makeup_demand_m3_h"])
  long = cases["four-hour-source-outage"]
  assert long["first_limited_h"] == pytest.approx(180 / duty["makeup_demand_m3_h"])
  assert long["first_empty_h"] == pytest.approx(long["first_limited_h"])
  limited = long["intervals"][1]
  assert limited["wet_duty_fraction"] == 0
  assert sum(limited["delivered_after_water_kw_by_level"].values()) == pytest.approx(3000)
  assert sum(limited["shortage_after_water_kw_by_level"].values()) == pytest.approx(27000)
  assert long["intervals"][-1]["cooling_shortage_kw"] == pytest.approx(0)
  dirty = cases["reclaimed-quality-excursion"]
  assert dirty["intervals"][0]["excluded_routes"]["Reclaimed/Treatment-B"] == ["feed-tds", "product-tds"]
  restricted = cases["outfall-restricted"]
  assert restricted["first_limited_h"] == pytest.approx(4.663, abs=.005)
  assert restricted["intervals"][-1]["total_discharge_m3_h"] == pytest.approx(15)
  assert restricted["intervals"][-1]["cooling_shortage_kw"] == pytest.approx(10570.43, abs=1)


def test_makeup_gate_fails_when_usable_inventory_is_insufficient(hybrid_unit):
  path = hybrid_unit / "src/water.study.json"
  design = json.loads(path.read_text())
  design["treated_storage"]["initial_m3"] = 80
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:water-study",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "water-study.json").read_text())
  case = report["cases"]["two-hour-source-outage"]
  assert case["first_limited_h"] == pytest.approx(1.609, abs=.005)
  assert case["status"] == "fail"


def test_protection_grading_checks_interior_of_fault_current_range():
  voltage = math.sqrt(3)
  source = FaultSource(voltage, SourceCondition(Impedance(.5, 0), 1), SourceCondition(Impedance(.125, 0), 1))
  upstream = Breaker(voltage, 10, ((2, 1, 1.2), (8, .0625, .08)))
  primary = Breaker(voltage, 10, ((2, .9, 1), (8, .2, .25)))
  result = check_radial_fault(
    source, voltage,
    (CircuitSection("Upstream", voltage, Impedance(0, 0), upstream),
     CircuitSection("Primary", voltage, Impedance(0, 0), primary)),
    primary_max_clear_s=1, backup_max_clear_s=2, grading_s=.08,
  )
  assert result["current_min_ka"] == pytest.approx(2)
  assert result["current_max_ka"] == pytest.approx(8)
  assert result["grading"]["Upstream"]["fault_current_ka"] == pytest.approx(4)
  assert result["grading"]["Upstream"]["margin_s"] == pytest.approx(-.25)
  assert not result["checks"]["selectivity"]


def test_protection_uses_standby_source_and_inverter_boundary(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:protection-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "protection-study.json").read_text())
  cases = report["cases"]
  grid, generator = cases["grid-cdu-a"], cases["generator-cdu-a"]
  main = "L03/Transformer-A -> L03/Switchboard-A"
  mv = "Incoming-A -> L03/Utility-A"
  assert grid["fault_source"] == "Grid-A"
  assert generator["fault_source"] == "Generator-A"
  assert generator["current_min_ka"] < grid["current_min_ka"]
  assert grid["path_impedance_ohm"][mv]["r"] == pytest.approx(.10 * (.4 / 20)**2)
  assert grid["devices"][mv]["current_max_ka"] == pytest.approx(grid["current_max_ka"] * .4 / 20)
  assert grid["grading"][main]["margin_s"] > .08 > generator["grading"][main]["margin_s"]
  assert [name for name, passed in generator["checks"].items() if not passed] == ["selectivity"]
  assert not cases["grid-main-b-breaking-duty"]["checks"]["interrupting-duty"]
  assert cases["battery-cdu-a-deenergized"]["status"] == "deenergized"

  rack = cases["battery-rack-inverter"]
  assert rack["energized_path"][0] == "L03/Battery-A"
  assert rack["contributing_path"] == ["L03/UPS-A", "L03/Busway-A", "L03/Rack-01"]
  assert rack["fault_source_mode"] == "inverter"
  assert (rack["current_min_ka"], rack["current_max_ka"]) == pytest.approx((.9, 1.5))
  assert rack["devices"][rack["primary"]]["latest_clear_s"] == pytest.approx(.07)
  assert cases["grid-rack-inverter"]["energized_path"][0] == "Grid-A"
  assert cases["grid-rack-inverter"]["contributing_path"] == rack["contributing_path"]
  busway = cases["battery-busway-b-sustain"]
  assert busway["fault_source"] == "L03/UPS-B"
  assert busway["devices"][busway["primary"]]["latest_clear_s"] == pytest.approx(.23)
  assert busway["source_sustain_s"] == .12 and not busway["checks"]["source-sustain"]
  high_pickup = cases["battery-rack-high-pickup"]
  assert not high_pickup["devices"][high_pickup["primary"]]["pickup_pass"]
  assert report["formal_review"] == "pending"


def test_protection_gates_insufficient_breaking_duty_at_representative_unit(hybrid_unit):
  path = hybrid_unit / "src/protection.study.json"
  design = json.loads(path.read_text())
  design["breakers"]["LV-Main-A"]["interrupt_ka"] = 18
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:protection-study",)).status == "fail"
  cases = json.loads((hybrid_unit / OUTPUT / "protection-study.json").read_text())["cases"]
  main = cases["grid-main-a"]
  assert main["status"] == "fail"
  assert main["devices"][main["primary"]]["breaker"] == "LV-Main-A"
  assert main["devices"][main["primary"]]["current_max_ka"] > 18


def test_hybrid_unit_reports_a_lost_return_path(hybrid_unit):
  path = hybrid_unit / "src/system.sdl.json"
  design = json.loads(path.read_text())
  unit = design["components"][design["root"]]
  edge = next(edge for edge in unit["connections"] if edge["from"] == "CDU-B.facility_out")
  unit["connections"].remove(edge)
  unit["nets"]["FWS-B"]["connections"].remove(edge["id"])
  path.write_text(json.dumps(design))

  result = build(hybrid_unit, targets=("datacenter:unit-study",))
  assert result.status == "error"
  assert "incomplete FWS loop" in result.report.read_text()


def test_floor_flags_a_service_opening_under_a_rack(hybrid_unit):
  path = hybrid_unit / "src/floor.study.json"
  design = json.loads(path.read_text())
  design["opening_requests"]["FWS-Penetration"]["origin_m"] = [0.7, 1.4]
  path.write_text(json.dumps(design))

  result = build(hybrid_unit, targets=("datacenter:floor-study",))
  assert result.status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "floor-study.json").read_text())
  assert report["plan_clashes"] == [["Rack-01", "FWS-Penetration"]]


def test_structural_beam_matches_static_response_with_support_load():
  span, ei, uniform, centered = 4, 200, 3, 9
  result = simple_beam(span, ei, uniform, ((2, centered), (4, 7)))
  assert result.reaction_start_kn == pytest.approx((uniform * span + centered) / 2)
  assert result.reaction_end_kn == pytest.approx(result.reaction_start_kn + 7)
  assert result.shear_kn == pytest.approx(result.reaction_start_kn)
  assert result.moment_kn_m == pytest.approx(uniform * span**2 / 8 + centered * span / 4)
  assert result.moment_at_m == pytest.approx(2)
  assert result.deflection_m == pytest.approx(5 * uniform * span**4 / (384 * ei) + centered * span**3 / (48 * ei))
  assert result.deflection_at_m == pytest.approx(2)


def test_structural_beam_partial_pressure_matches_force_and_moment():
  beam = simple_beam(10, 1000, 0, patches=((2, 8, 4),))
  assert beam.reaction_start_kn == beam.reaction_end_kn == pytest.approx(12)
  assert beam.moment_kn_m == pytest.approx(42)
  assert (beam.moment_at_m, beam.deflection_at_m) == pytest.approx((5, 5))
  full = simple_beam(10, 1000, 4)
  patch = simple_beam(10, 1000, 0, patches=((0, 10, 4),))
  assert patch.deflection_m == pytest.approx(full.deflection_m)


def test_frame_transfers_patch_reactions_to_columns_and_foundations(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:frame-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "frame-study.json").read_text())
  full, initial, weak = (report["cases"][key] for key in ("full", "initial", "full-weak-bearing"))
  assert len(full["columns"]) == 455
  assert len(full["foundations"]) == 91
  assert sum(len(floor["members"]) for floor in full["floors"].values()) == 810
  assert report["rack_column_clashes"] == []
  assert initial["occupied_levels"] == ["L01", "L02"]
  assert full["floors"]["L04"]["unit_patch_area_m2"] == pytest.approx(25 * 3.6 * 8)
  assert full["floors"]["L04"]["load_components_kn"]["service"]["infill_kn"] == pytest.approx(5.3 * (60 * 54 - 720))
  unit_floor = json.loads((hybrid_unit / OUTPUT / "floor-study.json").read_text())
  rack_total = sum(rack["vertical_load_kn"] for rack in unit_floor["rack_loads"].values())
  cooling_total = sum(device["vertical_load_kn"] for device in unit_floor["cooling_equipment_loads"].values())
  assert (full["load_balance"]["service"]["column_base_reactions_kn"]
          - initial["load_balance"]["service"]["column_base_reactions_kn"]) == pytest.approx(2 * 25 * (rack_total + cooling_total))
  assert full["load_balance"]["service"]["cooling_equipment_reactions_kn"] == pytest.approx(100 * cooling_total)
  assert initial["load_balance"]["service"]["cooling_equipment_reactions_kn"] == pytest.approx(50 * cooling_total)
  assert sum(footing["cooling_equipment_reaction_kn"]["service"] for footing in full["foundations"].values()) == pytest.approx(
    100 * cooling_total)
  point = next(item for item in full["floors"]["L01"]["members"]["X1A/Y0-Y1"]["secondary_reactions"]
               if item["source"] == "L01/Unit-01-01/Beam-YMid/start")
  assert point["station_m"] == 7
  assert point["load_kn"]["strength"] == pytest.approx(
    unit_floor["structural_screening"]["cases"]["service"]["steel_support_beams"]["Beam-YMid"]["strength"]["reaction_start_kn"]
  )
  for check in full["load_balance"].values():
    assert check["column_base_reactions_kn"] == pytest.approx(check["floor_and_beam_kn"] + check["column_self_weight_kn"])
  assert full["foundations"]["XS/Y4"]["bearing_kpa"] == pytest.approx(194.28, abs=.01)
  assert [name for name, passed in weak["checks"].items() if not passed] == ["bearing"]
  assert weak["foundations"]["XS/Y4"]["bearing_kpa"] == full["foundations"]["XS/Y4"]["bearing_kpa"]

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "frame-layout.usda"))
  root = stage.GetDefaultPrim()
  bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
  primary = root.GetChild("Primary").GetChild(_prim_name("L01/X1A/Y0-Y1"))
  secondary = root.GetChild("Secondary").GetChild(_prim_name("L01/Unit-01-01/Beam-YMid"))
  assert len(root.GetChild("Secondary").GetChildren()) == 400
  assert bounds.ComputeWorldBound(primary).ComputeAlignedRange().GetMax()[2] == pytest.approx(
    bounds.ComputeWorldBound(secondary).ComputeAlignedRange().GetMin()[2]
  )
  slab = root.GetChild("FloorBays").GetChild(_prim_name("L03/Unit-01-01"))
  equipment_beam = root.GetChild("Secondary").GetChild(_prim_name("L03/Unit-01-01/Beam-Y03"))
  slab_bounds = bounds.ComputeWorldBound(slab).ComputeAlignedRange()
  assert slab.IsInstance() and slab.GetChild("SteelForm").IsA(UsdGeom.Mesh)
  assert len(root.GetChild("FloorBays").GetChildren()) == 100
  assert slab_bounds.GetMax()[2] == pytest.approx(14)
  assert slab_bounds.GetMin()[2] == pytest.approx(
    bounds.ComputeWorldBound(equipment_beam).ComputeAlignedRange().GetMax()[2])


def test_frame_rejects_unbound_secondary_beam_end(hybrid_unit):
  path = hybrid_unit / "src/frame.study.json"
  design = json.loads(path.read_text())
  design["grid"]["x"]["X1A"] = 4.1
  path.write_text(json.dumps(design))
  result = build(hybrid_unit, targets=("datacenter:frame-study",))
  assert result.status == "error"
  assert "beam ends must land on adjacent main girder lines" in result.report.read_text()


def test_floor_traces_strip_reactions_to_steel_beam_ends(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:floor-study",)).status == "pass"
  floor = json.loads((hybrid_unit / OUTPUT / "floor-study.json").read_text())
  screening = floor["structural_screening"]
  shored, unshored, service = (screening["cases"][name] for name in ("pour-shored", "pour-unshored", "service"))
  assert screening["phase_resistance"] == {"pour": "permanent-form", "service": "reinforced-concrete-only"}
  assert floor["deck_definition"]["entry"].startswith("@structure/library/modules/w-form-concrete-slab/")
  assert shored["status"] == service["status"] == "pass"
  assert unshored["status"] == "fail"
  assert unshored["max_utilization"]["deflection"] > 4
  assert shored["strength_reactions_kn_by_support"]["Shore-Y01"] > 0
  assert service["temporary_supports"] == []
  assert service["openings_pending"]["FWS-Penetration"] == {
    "span_indexes": [1], "strip_indexes": [4], "support_lines": [],
  }
  assert sum(p["kn"] for p in screening["point_loads"]["service"].values()) == pytest.approx(
    sum(rack["vertical_load_kn"] for rack in floor["rack_loads"].values())
  )
  assert len(service["equipment_contacts"]) == 16 and service["checks"]["equipment-contacts"]
  assert set(service["cooling_beam_shares_kn"]) == {"Beam-Y02", "Beam-Y03"}
  assert len(service["strength_loads"]["points"]) == 32
  assert "CDU-A/FL" not in screening["cases"]["service-empty"]["strength_loads"]["points"]
  width, length = floor["bay_m"]
  for case in (shored, unshored, service):
    loads = case["strength_loads"]
    assert sum(case["strength_reactions_kn_by_support"].values()) == pytest.approx(
      loads["uniform_kn_m2"] * width * length + sum(p["kn"] for p in loads["points"].values())
    )
    for name, beam in case["steel_support_beams"].items():
      factored_weight = beam["self_weight_kn_m"] * width * case["load_factors"]["strength"]["deck_and_concrete"]
      response = beam["strength"]
      assert response["reaction_start_kn"] + response["reaction_end_kn"] == pytest.approx(
        case["strength_reactions_kn_by_support"][name] + factored_weight
      )
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "floor-assembly.usda"))
  root = stage.GetDefaultPrim()
  assert root.GetChild("DeckSlab").GetChild("PouredConcrete").IsA(UsdGeom.Mesh)
  members = {prim.GetCustomDataByKey("identity"): prim for prim in root.GetChildren()
             if prim.HasCustomDataKey("identity")}
  assert members.keys() == service["steel_support_beams"].keys()
  box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(members["Beam-YMid"])
  bounds = box.ComputeAlignedRange()
  assert tuple(bounds.GetSize()) == pytest.approx((3.6, .15, .3))
  assert bounds.GetMidpoint()[1] == pytest.approx(3)
  assert bounds.GetMax()[2] == pytest.approx(0)


def test_floor_rejects_an_inadequate_steel_support_even_when_slab_passes(hybrid_unit):
  path = hybrid_unit / "src/floor.study.json"
  design = json.loads(path.read_text())
  design["structural_screening"]["steel_support_beams"]["types"]["H300-example"]["moment_kn_m"] = 32
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:floor-study",)).status == "fail"
  cases = json.loads((hybrid_unit / OUTPUT / "floor-study.json").read_text())["structural_screening"]["cases"]
  assert cases["service"]["checks"]["moment"]
  assert not cases["service"]["checks"]["steel-beam-moment"]


def test_floor_detects_cooling_foot_punching_even_when_global_strips_pass(hybrid_unit):
  path = hybrid_unit / "src/floor.study.json"
  design = json.loads(path.read_text())
  design["structural_screening"]["equipment_contacts"]["punching_limit_kpa"] = 30
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:floor-study",)).status == "fail"
  cases = json.loads((hybrid_unit / OUTPUT / "floor-study.json").read_text())["structural_screening"]["cases"]
  case = cases["service"]
  assert case["checks"]["moment"] and case["checks"]["steel-beam-moment"]
  assert not case["checks"]["equipment-contacts"]
  contact = case["equipment_contacts"]["CDU-A/FL"]
  assert contact["checks"]["bearing"] and not contact["checks"]["punching"]
  assert cases["service-empty"]["status"] == "pass"


def test_fire_coordination_derives_crossings_from_existing_service_networks(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:fire-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "fire-study.json").read_text())
  crossings = report["penetrations"]["crossings"]
  assert len(report["compartments"]) == 8
  assert set(report["shaft_enclosures"]) == {"FWS-Riser-A", "FWS-Riser-B"}
  assert len(report["barriers"]) == 39
  assert len(crossings) == 51
  assert len(report["route_sources"]["fws.study.json"]) == 32
  assert set(report["route_sources"]["drainage.study.json"]) == {"Drain-L03-A", "Drain-Trunk"}
  assert {case["barrier"] for case in crossings.values() if case["route"] == "B-L03-supply"} == {
    "L03/FWS-Riser-B/West", "L03/Hall-Services",
  }
  assert {case["barrier"] for case in crossings.values() if case["route"] == "Drain-Trunk"} == {
    "L02/Slab", "L03/Slab",
  }
  assert crossings["A-riser-3-supply@L03/Slab#1"]["annular_space_mm"] == pytest.approx([30, 30])
  assert report["formal_review"] == "pending"
  assert set(report["structural_opening_templates_pending"]) == {"FWS-Penetration"}
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "fire-layout.usda"))
  markers = stage.GetDefaultPrim().GetChild("Crossings").GetChildren()
  assert {marker.GetCustomDataByKey("identity") for marker in markers} == crossings.keys()
  assert all(marker.GetCustomDataByKey("status") == "pass" and marker.IsA(UsdGeom.Sphere) for marker in markers)


def test_fire_coordination_flags_missing_shifted_and_inapplicable_selections(hybrid_unit):
  path = hybrid_unit / "src/fire.study.json"
  design = json.loads(path.read_text())
  omitted = "A-L03-supply@L03/Hall-Services#1"
  wrong_host = "B-riser-3-supply@L03/Slab#1"
  moved = "Drain-Trunk@L02/Slab#1"
  short_insulation = "B-L03-supply@L03/FWS-Riser-B/West#1"
  orphaned = "B-L03-supply@L03/FWS-Riser-A/East#1"
  del design["selections"][omitted]
  design["selections"][wrong_host]["system"] = "FWS-Wall-Demo"
  design["selections"][moved]["center_m"][0] += .045
  design["firestop_systems"]["Short-Insulation"] = {
    **design["firestop_systems"]["FWS-Shaft-Demo"], "rating_min": {"integrity": 180, "insulation": 90},
  }
  design["selections"][short_insulation]["system"] = "Short-Insulation"
  design["selections"][orphaned] = design["selections"][short_insulation].copy()
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:fire-study",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "fire-study.json").read_text())
  result = report["penetrations"]
  crossings = result["crossings"]
  assert set(result["failed_crossings"]) == {omitted, wrong_host, moved, short_insulation}
  assert crossings[omitted]["failed_checks"] == ["selection"]
  assert "host" in crossings[wrong_host]["failed_checks"]
  assert crossings[moved]["failed_checks"] == ["annular-space"]
  assert crossings[short_insulation]["failed_checks"] == ["rating"]
  assert result["unused_selections"] == [orphaned]


def test_firestop_host_thickness_tracks_floor_geometry(hybrid_unit):
  path = hybrid_unit / "src/floor.study.json"
  design = json.loads(path.read_text())
  design["deck"]["slab_top_m"] = .16
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:fire-study",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "fire-study.json").read_text())
  assert report["barriers"]["L03/Slab"]["thickness_mm"] == pytest.approx(98.8)
  crossings = report["penetrations"]["crossings"]
  assert crossings["A-riser-3-supply@L03/Slab#1"]["failed_checks"] == ["thickness"]
  assert crossings["A-L03-supply@L03/Hall-Services#1"]["status"] == "pass"


def test_egress_uses_rated_doors_and_accumulates_discharge_capacity(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:egress-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "egress-study.json").read_text())
  assert report["occupants"] == 248
  assert report["invalid_links"] == {}
  assert len(report["doors"]) == 18
  assert report["doors"]["L03/CompartmentDoor-North"]["host"] == "L03/Hall-Services"
  normal = report["cases"]["normal"]
  assert len(normal["origins"]) == 108
  assert all(len(origin["eligible_exits"]) == 2 for origin in normal["origins"].values())
  assert max(origin["nearest_access_m"] for origin in normal["origins"].values()) == pytest.approx(65.3)
  assert max(origin["common_access_m"] for origin in normal["origins"].values()) == pytest.approx(8.65)
  for group, count in normal["assigned_by_exit"].items():
    assert normal["link_assigned"][f"Discharge-{group}"] == count
  upper = normal["origins"]["L04/Unit-03-03"]["routes"]["East"]
  assert len([link for link in upper["links"] if link.startswith("Stair-East/")]) == 3
  assert upper["links"][-1] == "Discharge-East"
  assert upper["total_m"] > upper["access_m"]
  cases = report["cases"]
  assert cases["west-exit-out"]["assigned_by_exit"] == {"East": 248, "West": 0}
  assert cases["east-exit-out"]["assigned_by_exit"] == {"East": 0, "West": 248}
  narrow = cases["west-out-east-discharge-restricted"]
  assert narrow["checks"]["exit-count-and-distance"] and not narrow["checks"]["shared-capacity"]
  assert narrow["link_capacity"]["Discharge-East"] == narrow["link_assigned"]["Discharge-East"] == 180
  assert narrow["unassigned"] == 68
  local = cases["level-three-east-stair-door-closed"]
  assert {key.split("/")[0] for key, origin in local["origins"].items() if not origin["exit_count_pass"]} == {"L03"}
  assert local["unassigned"] == 0
  assert not cases["common-access-limit-reduced"]["checks"]["common-access-distance"]
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "egress-layout.usda"))
  root = stage.GetDefaultPrim()
  assert root.GetChild("StairEnvelope_East").IsA(UsdGeom.Cube)
  assert root.GetChild(_prim_name("Discharge-East")).IsA(UsdGeom.BasisCurves)


def test_egress_rejects_incompatible_compartment_door(hybrid_unit):
  path = hybrid_unit / "src/egress.study.json"
  design = json.loads(path.read_text())
  design["door_types"]["compartment"]["rating_min"]["insulation"] = 90
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:egress-study",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "egress-study.json").read_text())
  assert report["invalid_links"]["L03/CompartmentDoor-North"] == ["door-rating"]
  assert "L03/CompartmentDoor-North" not in report["cases"]["normal"]["link_capacity"]


def test_architectural_door_move_updates_the_clear_opening_and_egress_link(hybrid_unit):
  path = hybrid_unit / "src/architecture.study.json"
  design = json.loads(path.read_text())
  design["compartment_door_y_m"]["North"] = 50.6
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:architecture-study",)).status == "pass"
  architecture = json.loads((hybrid_unit / OUTPUT / "architecture-study.json").read_text())
  egress = json.loads((hybrid_unit / OUTPUT / "egress-study.json").read_text())
  name = "L03/CompartmentDoor-North"
  opening = architecture["doors"][name]
  assert opening["center_m"][1] == egress["doors"][name]["point_m"][1] == 50.6
  assert architecture["stairs"]["East"]["entry_y_m"] == 50.6
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "architecture-layout.usda"))
  prim = stage.GetDefaultPrim().GetChild("Doors").GetChild(_prim_name(name))
  assert prim.GetCustomDataByKey("host") == opening["host"]
  assert tuple(UsdGeom.BasisCurves(prim).GetPointsAttr().Get()[0]) == pytest.approx((49, 50.1, 14))


def test_grounding_traces_individual_equipment_and_separates_contact_failures(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:grounding-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "grounding-study.json").read_text())
  assert report["terminal_count"] == 800
  assert report["modules"]["joint"]["definition"] == "electrical:flat-strip-rebar-compression"
  assert report["modules"]["beam"]["definition"] == "structure:reinforced-concrete-beam"
  assert report["formal_review"] == "pending"
  cases = report["cases"]
  rack = "L03/Unit-01-01/Rack-01"
  assert cases["normal"]["counts"] == {"pass": 800, "disconnected": 0, "over-budget": 0}
  nominal = cases["normal"]["terminals"][rack]
  assert nominal["reachable_references"] == ["Foundation-A/Reference", "Foundation-B/Reference"]
  assert nominal["path_mohm"] == pytest.approx(
    sum(report["conductors_and_joints"][bond]["resistance_mohm"] for bond in nominal["bonds"])
  )
  fallback = cases["joint-a-open"]["terminals"][rack]
  assert fallback["reachable_references"] == ["Foundation-B/Reference"]
  assert "Foundation-B/Compression" in fallback["bonds"]
  assert cases["cage-a-open"]["terminals"][rack]["reachable_references"] == ["Foundation-B/Reference"]
  assert cases["both-joints-open"]["counts"]["disconnected"] == 800
  assert cases["joint-b-degraded-with-a-open"]["counts"] == {"pass": 0, "disconnected": 0, "over-budget": 800}
  assert cases["rack-spur-open"]["disconnected"] == [rack]
  assert report["conductors_and_joints"]["Riser-A/L03-L04"]["resistance_mohm"] == pytest.approx(1000 * .15 * 7 / 160)

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "grounding-layout.usda"))
  branch = "/Grounding/Foundations/A"
  frame = UsdGeom.XformCache(Usd.TimeCode.Default())

  def location(prim):
    return tuple(frame.GetLocalToWorldTransform(prim).ExtractTranslation())

  rebar_port = stage.GetPrimAtPath(f"{branch}/Reinforcement/Ports/rebar_bond_point")
  joint_port = stage.GetPrimAtPath(f"{branch}/Compression/Ports/rebar")
  assert location(joint_port) == pytest.approx(location(rebar_port))
  strip = stage.GetPrimAtPath(f"/Grounding/SteelStrips/{_prim_name('Foundation-A/Downlead')}")
  last = strip.GetChildren()[-1]
  tape_port = stage.GetPrimAtPath(f"{branch}/Compression/Ports/tape")
  assert location(last.GetChild("Ports").GetChild("end_b")) == pytest.approx(location(tape_port))


def test_grounding_rejects_a_missing_compression_die_record(hybrid_unit):
  path = hybrid_unit / "src/grounding.study.json"
  design = json.loads(path.read_text())
  del design["foundations"]["A"]["die"]
  path.write_text(json.dumps(design))
  result = build(hybrid_unit, targets=("datacenter:grounding-study",))
  assert result.status == "error"
  assert "explicit tool and die records" in result.report.read_text()


def test_building_flags_shared_feed_overload_even_when_unit_passes(hybrid_unit):
  path = hybrid_unit / "src/building.study.json"
  design = json.loads(path.read_text())
  design["shared_power"]["Incoming-B"]["capacity_kw"] = 57000
  path.write_text(json.dumps(design))

  result = build(hybrid_unit)
  assert result.status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "building-study.json").read_text())
  case = report["scenarios"]["full-supply-a-out"]
  assert case["checks"]["unit-scenario"]
  assert not case["checks"]["shared-power"]
  assert case["shared_power"]["Incoming-B"] == {"demand_kw": 58000, "available_kw": 57000}


def test_building_limits_fws_by_shared_return_segment(hybrid_unit):
  path = hybrid_unit / "src/fws.study.json"
  design = json.loads(path.read_text())
  pipes = design["trains"]["HeatRejection-B"]["segments"]
  pipe = next(pipe for pipe in pipes if pipe["id"] == "B-riser-1-return")
  pipe["capacity_kg_s"] = 500
  path.write_text(json.dumps(design))

  assert build(hybrid_unit).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "building-study.json").read_text())
  outage = report["scenarios"]["full-supply-a-out"]
  assert outage["shared_liquid_available_kw"]["HeatRejection-B"] == 31000
  assert outage["fws"]["delivered_kw_by_train"]["HeatRejection-B"] == pytest.approx(500 * 41.8)
  assert not outage["checks"]["shared-liquid-heat-rejection"]


def test_hydraulic_dispatch_matches_single_loop_pressure_balance():
  network = FacilityWaterNetwork({
    "Train": {"plant_node": "Plant", "terminals": {"Floor": "Load"}, "segments": [
      {"id": "Supply", "start": "Plant", "end": "Load", "leg": "supply", "capacity_kg_s": 1200},
      {"id": "Return", "start": "Load", "end": "Plant", "leg": "return", "capacity_kg_s": 1200},
    ]},
  })
  short_pipe = {"length_m": 1e-6, "diameter_m": 1, "roughness_m": 0,
                "minor_loss_coefficient": 0, "max_velocity_m_s": 2}
  hydraulics = FacilityWaterHydraulics(network, {
    "density_kg_m3": 1000, "viscosity_pa_s": .001, "pipes": dict.fromkeys(network.segments, short_pipe),
    "banks": {
      "Train": {"pumps": ["P1"], "max_running": 1, "shutoff_head_m": 20, "curve_m_per_m6_s2": 10,
                "max_flow_m3_s_each": 1.2, "efficiency": .8, "reserve_kpa": 5,
                "common_loss": {"rated_flow_m3_s": 1, "rated_drop_kpa": 0},
                "terminal_losses": {"Floor": {"rated_flow_m3_s": 1, "rated_drop_kpa": 100}}},
    },
  })
  result = network.allocate({"Floor": 46000}, {"Train": 46000}, {"Train": 46000}, {"Train": 40}, set(),
                            hydraulics=hydraulics)
  expected_flow = ((1000 * 9.80665 * 20 - 5000) / (100000 + 1000 * 9.80665 * 10))**.5
  assert result["hydraulics"]["method"] == "pressure-constrained"
  assert result["flow_kg_s_by_segment"] == pytest.approx(dict.fromkeys(network.segments, expected_flow * 1000))
  assert result["delivered_kw_by_terminal"]["Floor"] == pytest.approx(expected_flow * 40000)


def test_building_dispatches_water_under_pump_and_head_constraints(hybrid_unit):
  result = build(hybrid_unit)
  assert result.status == "pass", result.diagnostics
  cases = json.loads((hybrid_unit / OUTPUT / "building-study.json").read_text())["scenarios"]
  fallback = cases["full-supply-a-out"]["fws"]
  bank = fallback["hydraulics"]["banks"]["HeatRejection-B"]
  assert bank["critical_terminal"] == "L04"
  assert bank["dispatch_pumps"] == ["FWS-P-B1", "FWS-P-B2"]
  assert bank["flow_m3_s"] == pytest.approx(30000 / (997 * 41.8))
  assert bank["estimated_input_kw"] == pytest.approx(bank["flow_m3_s"] * bank["estimated_pump_head_kpa"] / .8)
  assert bank["balancing_kpa_by_terminal"]["L01"] > bank["balancing_kpa_by_terminal"]["L04"]
  assert sum(bank["pressure_components_kpa_by_terminal"]["L04"].values()) == pytest.approx(bank["required_kpa"])

  degraded = cases["full-supply-a-out-b-head-derated"]["fws"]
  assert degraded["hydraulics"]["method"] == "pressure-constrained"
  assert degraded["hydraulics"]["rated_capacity_dispatch_kw"] == 30000
  assert degraded["hydraulics"]["limited_kw"] == pytest.approx(3661.2, abs=1)
  assert degraded["hydraulics"]["banks"]["HeatRejection-B"]["head_margin_kpa"] == pytest.approx(0, abs=.0001)
  rerouted = cases["full-a-head-derated"]["fws"]
  assert rerouted["delivered_kw_by_train"]["HeatRejection-B"] > 0
  assert not rerouted["shortage_kw_by_terminal"]

  one_lost = cases["full-supply-a-out-one-b-pump-lost"]["fws"]
  assert one_lost["hydraulics"]["banks"]["HeatRejection-B"]["dispatch_pumps"] == ["FWS-P-B2", "FWS-P-B3"]
  two_lost = cases["full-supply-a-out-two-b-pumps-lost"]["fws"]
  assert two_lost["delivered_kw_by_train"]["HeatRejection-B"] == pytest.approx(.54 * 997 * 41.8)
  assert two_lost["hydraulics"]["banks"]["HeatRejection-B"]["dispatch_pumps"] == ["FWS-P-B3"]

  standby = json.loads((hybrid_unit / OUTPUT / "standby-study.json").read_text())["scenarios"]
  combined = standby["full-generator-and-b-pumps-out"]
  assert combined["checks"]["it-interruption"] and not combined["checks"]["liquid-interruption"]
  assert combined["intervals"][-1]["fws"]["delivered_kw_by_train"]["HeatRejection-B"] == pytest.approx(.54 * 997 * 41.8)


def test_lateral_frame_balances_a_single_storey_with_eccentric_x_force():
  from texsys_structure.lateral import BraceBay, solve_braced_frame

  bays = {
    "south": BraceBay("x", (0, 0), (4, 0), .01, 200e6),
    "north": BraceBay("x", (0, 4), (4, 4), .01, 200e6),
    "west": BraceBay("y", (0, 0), (0, 4), .01, 200e6),
  }
  storey = solve_braced_frame({"Roof": 3}, 0, ((0, 4), (0, 4)), bays, {"Roof": (100, 0, 100)})["Roof"]
  # The 3-4-5 diagonal resolves 75 kN at the south bay and 25 kN at the north bay.
  assert [storey["bays"][name]["shear_kn"] for name in bays] == pytest.approx([75, 25, 0])
  assert storey["bays"]["south"]["tension_kn"] == pytest.approx(93.75)
  assert storey["bays"]["south"]["compression_at_end_kn"] == pytest.approx(56.25)
  assert storey["rotation_rad"] == pytest.approx((75 - 25) / (200e6 * .01 / 5 * (.8 ** 2) * 4))


def test_lateral_study_detects_loss_of_restraint_and_foundation_uplift(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:lateral-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "lateral-study.json").read_text())
  cases = report["cases"]
  assert report["door_conflicts"] == []
  assert len(report["bays"]) == 10
  wind = cases["full-wind-x"]
  assert wind["storeys"]["L01"]["shear_kn"] == pytest.approx([.4 * 54 * 28, 0])
  assert wind["equilibrium"]["overturning_from_reactions_kn_m"] == pytest.approx([.4 * 54 * 28 * 15.5, 0])
  assert wind["equilibrium"]["base_shear_residual_kn"] == pytest.approx([0, 0], abs=1e-8)
  assert cases["full-wind-one-bay-out"]["governing"]["brace-tension"]["demand_kn"] > wind["governing"]["brace-tension"]["demand_kn"]
  assert cases["full-wind-x-restraint-lost"]["checks"] == {"stability": False}
  stress = cases["initial-wind-overturning"]
  assert not stress["checks"]["foundation-uplift"]
  assert not stress["checks"]["foundation-bearing"]
  assert stress["governing"]["foundation-uplift"]["demand_kn"] > 700

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "lateral-layout.usda"))
  root = stage.GetDefaultPrim()
  assert len(root.GetChildren()) == 10
  first = root.GetChild(_prim_name("X-South-1")).GetChild("L01_1")
  assert first.IsA(UsdGeom.Cube)
  matrix = UsdGeom.XformCache().GetLocalToWorldTransform(first)

  assert tuple(matrix.Transform(Gf.Vec3d(0, 0, -.5))) == pytest.approx([7.6, 0, -1.5])
  assert tuple(matrix.Transform(Gf.Vec3d(0, 0, .5))) == pytest.approx([13, 0, 0])


def test_lateral_study_rejects_a_brace_across_stair_doors(hybrid_unit):
  path = hybrid_unit / "src/lateral.study.json"
  design = json.loads(path.read_text())
  design["brace"]["bays"]["Y-West-1"]["between"] = ["Y1", "Y2"]
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:lateral-study",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "lateral-study.json").read_text())
  assert len(report["door_conflicts"]) == 4
  assert {"brace": "Y-West-1", "door": "L01/StairDoor-West"} in report["door_conflicts"]


def test_diaphragm_slip_transfers_a_balanced_uniform_load():
  from texsys_structure.diaphragm import AxialMember, Diaphragm, MembranePanel

  points = {"a": (0, 0), "b": (2, 0), "c": (2, 1), "d": (0, 1)}
  holes = ((.25, .25, .75, .75), (.5, .25, 1., .75))
  diaphragm = Diaphragm(
    points, {"panel": MembranePanel(tuple(points), .1, holes)},
    {"edge": AxialMember(("a", "b"), 10000)}, dict.fromkeys(points, 200), 20e6, .2,
  )
  result = diaphragm.solve(dict.fromkeys(points, (5, 0)), dict.fromkeys(points, (-5, 0)))
  assert result["panels"]["panel"]["net_area_m2"] == pytest.approx(2 - .375)
  assert result["panels"]["panel"]["max_abs_shear_kn_m"] == pytest.approx(0, abs=1e-9)
  assert result["axial_kn"]["edge"] == pytest.approx(0, abs=1e-9)
  for connection in result["connections"].values():
    assert connection["to_steel_kn"] == pytest.approx([5, 0])
    assert connection["slip_m"] == pytest.approx(.025)
  with pytest.raises(ValueError, match="force and moment"):
    diaphragm.solve({"a": (5, 0)}, {"d": (-5, 0)})


def test_diaphragm_resolves_each_brace_end_and_deducts_openings(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:diaphragm-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "diaphragm-study.json").read_text())
  lateral = json.loads((hybrid_unit / OUTPUT / "lateral-study.json").read_text())
  assert len(report["nodes_xy_m"]) == 91
  assert report["opening_connection_conflicts"] == []
  cases = report["cases"]
  wind = cases["full-wind-x"]
  assert wind["status"] == "pass"
  assert report["opening_basis"] == "slab-openings.json"
  assert sum(panel["net_area_m2"] for panel in wind["levels"]["L01"]["panels"].values()) == pytest.approx(3240)
  assert sum(panel["net_area_m2"] for panel in wind["levels"]["L02"]["panels"].values()) == pytest.approx(3220.2496)
  assert sum(panel["net_area_m2"] for panel in wind["levels"]["Roof"]["panels"].values()) == pytest.approx(3240)
  assert report["layout"]["L01"]["openings_xyxy_m"] == {}
  assert "brace-bay-collector" in report["layout"]["L01"]["members"]["X1B-X2A/Y0"]["roles"]
  floor = wind["levels"]["L01"]
  joints = floor["brace_joints"]["X-South-1"]
  brace = lateral["cases"]["full-wind-x"]["storeys"]
  assert joints["above"] == {"node": "X1B/Y0", "force_kn": pytest.approx(brace["L02"]["bays"]["X-South-1"]["shear_kn"])}
  assert joints["below"] == {"node": "X2A/Y0", "force_kn": pytest.approx(-brace["L01"]["bays"]["X-South-1"]["shear_kn"])}
  assert floor["max_nodal_residual_kn"] < 1e-7
  assert floor["input_moment_residual_kn_m"] == pytest.approx(0, abs=1e-8)
  for axis in (0, 1):
    assert sum(item["to_steel_kn"][axis] for item in floor["connections"].values()) == pytest.approx(
      sum(item[axis] for item in floor["slab_actions_kn"].values()), abs=1e-8
    )
  weak = cases["full-wind-weak-connection"]
  assert [key for key, passed in weak["checks"].items() if not passed] == ["slab-to-steel"]
  assert weak["governing"]["slab-to-steel"]["demand"] == wind["governing"]["slab-to-steel"]["demand"]
  assert cases["full-wind-x-restraint-lost"]["checks"] == {"lateral-stability": False}
  assert not cases["initial-wind-overturning"]["checks"]["brace-joint"]

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "diaphragm-layout.usda"))
  level = stage.GetDefaultPrim().GetChild("L01")
  assert len(level.GetChildren()) == 162 + 91
  assert level.GetChild(f"Connection_{_prim_name('X2A/Y0')}").IsA(UsdGeom.Cube)


def test_footing_compression_only_contact_matches_eccentric_resultants():
  from texsys_structure.foundation import footing_contact

  centered = footing_contact((2, 3), 1200, (0, 0))
  assert centered.state == "full"
  assert centered.area_m2 == pytest.approx(6)
  assert centered.peak_kpa == pytest.approx(200)
  edge = footing_contact((2, 3), 1000, (400, 0))
  assert edge.state == "partial"
  assert edge.area_m2 == pytest.approx(3 * 3 * (1 - .4))
  assert edge.peak_kpa == pytest.approx(2 * 1000 / edge.area_m2)
  assert edge.resultants() == pytest.approx((1000, 400, 0), abs=1e-7)
  for actions in ((400, 400), (999, 1499), (-999, 1400), (999, -200)):
    corner = footing_contact((2, 3), 1000, actions)
    assert corner.state == "partial"
    assert corner.resultants() == pytest.approx((1000, *actions), abs=1e-7)
  assert footing_contact((2, 3), 1000, (1001, 0)).state == "overturned"
  assert footing_contact((2, 3), -100, (0, 0)).state == "detached"


def test_footing_sections_integrate_net_reaction_outside_critical_sections():
  from texsys_structure.foundation import footing_contact, footing_sections

  contact = footing_contact((4, 4), 1600, (0, 0))
  result = footing_sections(contact, (1, 1), .5, 10)
  for edge in result["edges"].values():
    assert edge["flexure_kn_m_per_m"] == pytest.approx(90 * 1.5 ** 2 / 2)
    assert edge["one_way_shear_kn_per_m"] == pytest.approx(90 * 1.)
  assert result["punching_kn"] == pytest.approx(90 * (16 - 1.5 ** 2))
  assert result["punching_perimeter_m"] == pytest.approx(6)


def test_foundations_screen_sliding_and_overturning_beyond_average_bearing(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:foundation-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "foundation-study.json").read_text())
  frame = json.loads((hybrid_unit / OUTPUT / "frame-study.json").read_text())
  cases = report["cases"]
  assert report["design_screening"]["status"] == "revision-required"
  assert report["design_screening"]["failed_cases"] == report["seismic_matrix"]["cases"]
  assert "full-equivalent-seismic-x" in report["design_screening"]["failed_cases"]
  assert len(cases["full-gravity"]["footings"]) == 91
  base = cases["full-gravity"]["footings"]["XS/Y4"]
  assert base["service"]["peak_pressure_kpa"] == pytest.approx(frame["cases"]["full"]["foundations"]["XS/Y4"]["bearing_kpa"])
  soft = cases["full-local-soft-ground"]
  assert not soft["checks"]["settlement"]
  assert not soft["checks"]["differential"]
  assert soft["checks"]["bearing"]
  assert soft["footings"]["XS/Y4"]["service"]["peak_settlement_m"] == pytest.approx(.03886, abs=.00001)
  seismic = cases["full-equivalent-seismic-x"]
  assert seismic["checks"]["bearing"]
  assert not seismic["checks"]["sliding"]
  assert not seismic["checks"]["strength-contact"]
  assert seismic["governing"]["sliding"]["id"] == "X4A/Y6"
  footing = seismic["footings"]["X4A/Y6"]
  assert footing["service"]["soil_sliding_demand_kn"] == pytest.approx(175.16, abs=.01)
  assert footing["service"]["soil_sliding_resistance_kn"] == pytest.approx(119.42, abs=.01)
  assert footing["service"]["unbalanced_resultants"] == pytest.approx([0, 0, 0], abs=1e-7)
  assert footing["strength"]["uplift"]["contact"]["state"] == "overturned"
  assert footing["strength"]["uplift"]["per_anchor_tension_kn"] > 0
  assert cases["initial-wind-overturning"]["footings"]["X0/Y3"]["service"]["state"] == "detached"

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "foundation-layout.usda"))
  root = stage.GetDefaultPrim()
  assert len(root.GetChildren()) == 91
  assembly = root.GetChild(_prim_name("X4A/Y6"))
  assert len([child for child in assembly.GetChildren() if child.IsA(UsdGeom.Cylinder)]) == 4
  cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
  concrete = cache.ComputeWorldBound(assembly.GetChild("Concrete")).ComputeAlignedRange()
  plate = cache.ComputeWorldBound(assembly.GetChild("BasePlate")).ComputeAlignedRange()
  assert concrete.GetMax()[2] == pytest.approx(plate.GetMin()[2])
  assert plate.GetMax()[2] == pytest.approx(report["column_base_m"])


def test_tied_footing_redistributes_force_at_friction_limit():
  from texsys_structure.foundation_ties import FoundationTie, TiedPad, TieEquilibriumError, solve_tied_footings

  pads = {"A": TiedPad(0, (2, 2), 10000, 1000, .25), "B": TiedPad(3, (2, 2), 10000, 1000, .9)}
  ties = {("A", "B"): FoundationTie(1e6, 1000, 0, 0)}
  actions = {"A": (150, 200, 0, 0, 30), "B": (0, 200, 0, 0, 0)}
  result = solve_tied_footings(pads, ties, actions)
  a, b = (result["pads"][key] for key in pads)
  assert a["along_soil_reaction_kn"] == pytest.approx(math.sqrt(50 ** 2 - 30 ** 2))
  assert b["along_soil_reaction_kn"] == pytest.approx(110)
  assert a["contact"].resultants() == pytest.approx([200, 0, 0], abs=1e-8)
  assert a["mobilized_slip_m"] == pytest.approx(.11011 - .04)
  assert b["mobilized_slip_m"] == 0
  assert result["beams"][("A", "B")]["tension_kn"] == pytest.approx(-110)
  with pytest.raises(TieEquilibriumError, match="group friction upper bound"):
    solve_tied_footings(pads, ties, {**actions, "A": (400, 200, 0, 0, 30)})


def test_footing_sections_include_the_outboard_tie_end_action():
  from texsys_structure.foundation import footing_contact, footing_sections

  contact = footing_contact((4, 4), 1600, (0, 0))
  plain = footing_sections(contact, (1, 1), .5, 10)
  tied = footing_sections(contact, (1, 1), .5, 10, ((2, 0, 10, 8, 0),))
  assert tied["edges"]["x-"] == plain["edges"]["x-"]
  assert tied["edges"]["x+"]["flexure_kn_m_per_m"] == pytest.approx(101.25 - (10 * 1.5 + 8) / 4)
  assert tied["edges"]["x+"]["one_way_shear_kn_per_m"] == pytest.approx(90 - 10 / 4)
  assert tied["punching_kn"] == pytest.approx(plain["punching_kn"] - 10)


def test_tied_foundation_candidate_checks_same_building_actions(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:foundation-ties",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "foundation-ties.json").read_text())
  base = json.loads((hybrid_unit / OUTPUT / "foundation-study.json").read_text())
  assert report["independent_design_screening"]["status"] == "revision-required"
  assert report["design_screening"]["status"] == "pass"
  assert report["cases"].keys() == base["cases"].keys()
  assert len(report["ties"]) == 10
  seismic = report["cases"]["full-equivalent-seismic-x"]
  assert seismic["independent_status"] == "fail"
  assert seismic["status"] == "pass"
  footing = seismic["footings"]["X4A/Y6"]
  assert footing["service"]["soil_sliding_demand_kn"] == pytest.approx(87.37, abs=.02)
  reduced = footing["strength"]["uplift"]
  assert reduced["contact"]["state"] == "partial"
  assert reduced["soil_sliding_demand_kn"] == pytest.approx(reduced["soil_sliding_resistance_kn"], abs=1e-8)
  assert reduced["mobilized_slip_m"] == pytest.approx(.006759, abs=.000001)
  assert reduced["contact"]["unbalanced_resultants"] == pytest.approx((0, 0, 0), abs=1e-7)
  beam = seismic["beams"]["X3B/Y6--X4A/Y6"]["uplift"]
  assert beam["tension_kn"] == pytest.approx(-143.61, abs=.02)
  assert sum(action["down_force_kn"] for action in beam["ends"].values()) == pytest.approx(beam["weight_kn"])
  assert seismic["footings"]["XS/Y4"] == base["cases"]["full-equivalent-seismic-x"]["footings"]["XS/Y4"]
  assert report["cases"]["full-local-soft-ground"]["status"] == "fail"
  assert report["cases"]["initial-wind-overturning"]["status"] == "fail"
  assert report["cases"]["initial-wind-y"]["checks"]["strength-sliding"]

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "foundation-ties.usda"))
  root = stage.GetDefaultPrim()
  key = "X3B/Y6--X4A/Y6"
  beam_prim = root.GetChild(f"Tie_{_prim_name(key)}")
  bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"])
  geometry = bbox.ComputeWorldBound(beam_prim).ComputeAlignedRange()
  assert geometry.GetMin()[0] == pytest.approx(27.2)
  assert geometry.GetMax()[0] == pytest.approx(29.4)


def test_seismic_matrix_propagates_signed_orthogonal_loads_to_the_foundations(hybrid_unit):
  from itertools import product

  assert build(hybrid_unit, targets=("datacenter:seismic-envelope",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "seismic-envelope.json").read_text())
  lateral = json.loads((hybrid_unit / OUTPUT / "lateral-study.json").read_text())
  slab = json.loads((hybrid_unit / OUTPUT / "diaphragm-study.json").read_text())
  assert (report["case_count"], report["uniaxial_count"], report["bidirectional_count"]) == (24, 8, 16)
  coverage = set()
  for case in report["cases"].values():
    primary, *secondary = case["components"]
    coverage.add((primary["axis"], primary["sign"], math.copysign(1, primary["eccentricity_m"]),
                  secondary[0]["sign"] if secondary else 0))
    if secondary:
      assert primary["axis"] != secondary[0]["axis"]
      assert secondary[0]["scale"] == .3
      assert secondary[0]["eccentricity_m"] == 0
  assert coverage == set(product("xy", (-1, 1), (-1, 1), (-1, 0, 1)))

  key = "full-seismic-y-pos-epos-x-neg"
  reference = lateral["cases"]["full-seismic-y-pos-epos"]
  combined = lateral["cases"][key]
  assert combined["level_actions_fx_fy_mz"]["L03"][1] == reference["level_actions_fx_fy_mz"]["L03"][1]
  assert combined["level_actions_fx_fy_mz"]["L03"][0] == pytest.approx(
    .3 * lateral["cases"]["full-equivalent-seismic-x"]["level_actions_fx_fy_mz"]["L03"][0]
  )
  assert combined["storeys"]["L01"]["shear_kn"] == pytest.approx(report["cases"][key]["base_actions_fx_fy_mz"][:2])
  assert combined["equilibrium"]["base_shear_residual_kn"] == pytest.approx([0, 0], abs=1e-7)
  floor = slab["cases"][key]["levels"]["L03"]
  loads = floor["slab_actions_kn"]
  centre_x, centre_y = 30, 27
  points = slab["nodes_xy_m"]
  fx = sum(value[0] for value in loads.values())
  fy = sum(value[1] for value in loads.values())
  mz = sum((points[node][0] - centre_x) * value[1] - (points[node][1] - centre_y) * value[0]
           for node, value in loads.items())
  assert (fx, fy, mz) == pytest.approx(combined["level_actions_fx_fy_mz"]["L03"])
  assert report["screening"]["tied_candidate_status"] == "pass"
  assert report["screening"]["failures_by_stage"]["independent"] == sorted(report["cases"])
  assert report["cases"]["full-equivalent-seismic-x"]["contacts_regained"]
  assert report["governing"]["tied_mobilized_slip_m"]["value"] == pytest.approx(.008603, abs=.000001)


def test_slab_opening_register_separates_shaft_sleeves_and_main_slab_cuts(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:slab-openings",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "slab-openings.json").read_text())
  assert report["routing_status"] == "bound"
  assert report["unbound_unit_reservations"] == []
  assert len(report["retired_unit_reservations"]) == 100
  assert report["conflicts"] == []
  assert len(report["crossings"]) == 14
  assert sum(item["host"] == "shaft-closure" for item in report["crossings"].values()) == 12
  level = report["levels"]["L02"]
  assert len(level["cuts"]) == 3
  shaft = "A-riser-2-supply@L02/Slab#1"
  assert shaft in level["crossings"] and shaft not in level["cuts"]
  assert report["crossings"][shaft]["shaft"] == "FWS-Riser-A"
  assert level["cuts"]["Drain-Trunk@L02/Slab#1"]["role"] == "main-slab-sleeve"
  assert "Unit-01-01/FWS-Penetration" not in level["cuts"]

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "slab-openings-layout.usda"))
  slab = stage.GetDefaultPrim().GetChild("L02")
  assert len(slab.GetChildren()) == 3
  assert slab.GetChild(_prim_name("L02/FWS-Riser-A/Closure")).IsA(UsdGeom.Mesh)

  unit = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "deck-openings-detail.usda"))
  deck = UsdGeom.Mesh(unit.GetDefaultPrim().GetChild("PouredConcrete"))
  points = deck.GetPointsAttr().Get()
  indices = deck.GetFaceVertexIndicesAttr().Get()
  top_area = 0.
  for i in range(0, len(indices), 4):
    corners = [points[index] for index in indices[i:i + 4]]
    if all(abs(point[2] - .17) < 1e-6 for point in corners):
      a, b, c, d = corners
      top_area += abs((b - a).GetCross(d - a)[2])
  assert top_area == pytest.approx(3.6 * 8 - .3 * .4, abs=1e-5)
  volumes = report["unit_deck_detail"]
  assert volumes["gross_volume_m3"]["steel"] - volumes["net_volume_m3"]["steel"] == pytest.approx(.3 * .4 * .0012)


def test_slab_openings_reject_repeated_tie_beam_intersection(hybrid_unit):
  config = hybrid_unit / "src/slab-openings.study.json"
  policy = json.loads(config.read_text())
  policy["unit_fws_opening_policy"] = "retain"
  config.write_text(json.dumps(policy))
  path = hybrid_unit / "src/floor.study.json"
  design = json.loads(path.read_text())
  design["opening_requests"]["FWS-Penetration"]["origin_m"][1] = 4.8
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:slab-openings",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "slab-openings.json").read_text())
  assert len(report["conflicts"]) == 100
  assert {
    "level": "L01", "opening": "Unit-01-01/FWS-Penetration", "check": "steel-clearance", "members": ["X1A-X1B/Y1"],
  } in report["conflicts"]


def test_slab_openings_check_actual_closure_edge_after_steel_inset(hybrid_unit):
  path = hybrid_unit / "src/slab-openings.study.json"
  design = json.loads(path.read_text())
  design["shaft_aperture_inset_m"] = .2
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:slab-openings",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "slab-openings.json").read_text())
  key = "A-riser-2-supply@L02/Slab#1"
  assert report["crossings"][key]["firestop_status"] == "pass"
  assert {"level": "L02", "opening": key, "check": "sleeve-host-edge-clearance", "host": "FWS-Riser-A"} in report["conflicts"]


def test_fws_branches_bind_each_cdu_and_feed_back_into_pump_dispatch(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:fws-branches",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "fws-branches.json").read_text())
  building = json.loads((hybrid_unit / OUTPUT / "building-study.json").read_text())
  assert len(report["routes"]) == 896
  assert report["structural_floor_crossings"] == []
  assert report["conflicts"] == []
  assert sum(len(units) for trains in report["outlets"].values() for units in trains.values()) == 200
  train = "HeatRejection-B"
  unit = "Unit-01-01"
  ports = report["outlets"]["L04"][train][unit]
  assert ports["supply"]["port"] == "facility_in" and ports["return"]["port"] == "facility_out"
  assert ports["supply"]["device"] == ports["return"]["device"] == "CDU-B"
  route_ids = report["unit_paths"][f"L04/{train}"][unit]
  supply = [report["routes"][name] for name in route_ids if report["routes"][name]["leg"] == "supply"]
  assert supply[0]["upstream_segment"] == "B-L04-supply"
  assert supply[-1]["points_m"][-1] == ports["supply"]["point_m"]

  fallback = report["cases"]["full-supply-a-out"]
  assert fallback["total_delivered_kw"] == pytest.approx(30000)
  assert len(fallback["delivered_kw_by_unit_train"]) == 100
  assert fallback["delivered_kw_by_unit_train"][f"L04/{unit}"][train] == pytest.approx(300)
  detail = fallback["floor_paths"][f"L04/{train}"]
  assert detail["unit_flow_m3_s"] == pytest.approx(300 / (997 * 41.8))
  assert detail["critical_loss_kpa"] == pytest.approx(24.8351, abs=.001)
  dispatch = building["scenarios"]["full-supply-a-out"]["fws"]["hydraulics"]["banks"][train]
  assert detail["critical_loss_kpa"] == pytest.approx(
    dispatch["pressure_components_kpa_by_terminal"]["L04"]["floor_distribution"]
  )
  assert report["cases"]["initial-normal"]["total_delivered_kw"] == pytest.approx(15000)

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "fws-branches-layout.usda"))
  assert len(stage.GetDefaultPrim().GetChildren()) == 896


def test_precision_air_rating_follows_sdl_instance_parameter(hybrid_unit):
  path = hybrid_unit / "src/system.sdl.json"
  design = json.loads(path.read_text())
  instances = design["components"][design["root"]]["instances"]
  instances["PrecisionAir-A"]["parameters"] = {
    "sensible_capacity": {"value": 135, "unit": "kW", "state": "assumed", "source": "rating override test"},
  }
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:unit-study",)).status == "pass"
  report = json.loads((hybrid_unit / OUTPUT / "unit-study.json").read_text())
  assert report["air_units"]["PrecisionAir-A"]["capacity_kw"] == 135
  assert report["air_units"]["PrecisionAir-B"]["capacity_kw"] == 140


def test_tcs_inlet_limit_follows_rack_module_parameter(hybrid_unit):
  path = hybrid_unit / "src/system.sdl.json"
  design = json.loads(path.read_text())
  instances = design["components"][design["root"]]["instances"]
  instances["Rack-04"]["parameters"] = {
    "maximum_inlet_temperature": {"value": 29, "unit": "degC", "state": "assumed", "source": "rack limit test"},
  }
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:unit-study",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "unit-study.json").read_text())
  normal = report["scenarios"]["normal"]
  assert normal["liquid_available_kw"] == 660
  assert not normal["checks"]["liquid-capacity"]
  assert normal["tcs"]["shortage_kw_by_terminal"] == {"Rack-04": 75.0}


def test_tcs_detects_same_tier_crossing(hybrid_unit):
  path = hybrid_unit / "src/tcs.study.json"
  config = json.loads(path.read_text())
  config["trains"]["CDU-B"]["supply"]["tier_z_m"] = config["trains"]["CDU-A"]["supply"]["tier_z_m"]
  path.write_text(json.dumps(config))
  assert build(hybrid_unit, targets=("datacenter:tcs-study",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "tcs-study.json").read_text())
  assert any(item["check"] == "tcs-clearance" for item in report["conflicts"])


def test_fws_branch_rating_caps_upstream_allocation(hybrid_unit):
  path = hybrid_unit / "src/fws-branches.study.json"
  design = json.loads(path.read_text())
  design["pipe_types"]["drop"]["capacity_kg_s"] = 4
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:building-study",)).status == "fail"
  building = json.loads((hybrid_unit / OUTPUT / "building-study.json").read_text())
  fws = building["scenarios"]["full-supply-a-out"]["fws"]
  assert fws["delivered_kw_by_train"]["HeatRejection-B"] == pytest.approx(100 * 4 * 41.8)


def test_fws_branch_geometry_rejects_overlapping_a_b_spines(hybrid_unit):
  path = hybrid_unit / "src/fws-branches.study.json"
  design = json.loads(path.read_text())
  design["trains"]["HeatRejection-B"]["legs"]["supply"]["overhead_m"] = 3.35
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:fws-branches",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "fws-branches.json").read_text())
  assert any(item["check"] == "branch-clearance" and "HeatRejection-A" in item["route"]
             and "HeatRejection-B" in item["other"] for item in report["conflicts"])


def test_fws_branch_failures_redispatch_without_masking_local_shortages(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:fws-branches", "datacenter:ride-through-study")).status == "pass"
  building = json.loads((hybrid_unit / OUTPUT / "building-study.json").read_text())["scenarios"]
  branches = json.loads((hybrid_unit / OUTPUT / "fws-branches.json").read_text())["cases"]
  standby = json.loads((hybrid_unit / OUTPUT / "standby-study.json").read_text())["scenarios"]
  thermal = json.loads((hybrid_unit / OUTPUT / "ride-through-study.json").read_text())["cases"]
  affected = "L03/Unit-01-01"
  a, b = "HeatRejection-A", "HeatRejection-B"

  single = building["full-l03-one-a-drop-out"]
  assert single["status"] == "pass"
  assert single["fws"]["available_trains_by_unit"][affected] == [b]
  assert single["fws"]["delivered_kw_by_unit_train"][affected] == {a: 0., b: 300.}
  assert single["fws"]["shortage_kw_by_unit"] == {}
  pipes = branches["full-l03-one-a-drop-out"]["floor_paths"]
  assert pipes[f"L03/{a}"]["flow_m3_s_by_unit"]["Unit-01-01"] == 0
  assert pipes[f"L03/{b}"]["flow_m3_s_by_unit"]["Unit-01-01"] == pytest.approx(300 / (997 * 41.8))
  for train in (a, b):
    bank = single["fws"]["hydraulics"]["banks"][train]
    assert sum(bank["pressure_components_kpa_by_terminal"]["L03"].values()) == pytest.approx(
      bank["required_kpa_by_terminal"]["L03"]
    )

  row = building["full-l03-shared-a-row-out"]
  row_units = [f"Unit-03-{index:02d}" for index in range(1, 6)]
  assert row["fws"]["blocked_units_by_train_level"] == {f"L03/{a}": row_units}
  assert sum(row["fws"]["delivered_kw_by_unit_train"][f"L03/{unit}"][b] for unit in row_units) == pytest.approx(1500)
  assert row["status"] == "pass"

  for name in ("full-l03-one-ab-drop-out", "full-a-source-and-b-drop-out"):
    outage = building[name]
    assert outage["status"] == "fail"
    assert outage["fws"]["shortage_kw_by_unit"] == {affected: 300.}
    assert outage["fws"]["shortage_kw_by_terminal"] == {"L03": 300.}
    assert outage["fws"]["units_at_risk"] == [affected]
    assert branches[name]["total_delivered_kw"] == pytest.approx(29700)

  derated = building["full-a-source-and-b-drop-out-derated"]["fws"]
  assert derated["hydraulics"]["method"] == "pressure-constrained"
  assert derated["shortage_kw_by_unit"][affected] == 300.
  assert derated["delivered_kw_by_train"][b] == pytest.approx(26285.4, abs=1)
  assert derated["hydraulics"]["banks"][b]["head_margin_kpa"] >= -.0001

  assert standby["full-l03-one-a-drop-out"]["longest_liquid_interruption_s_by_unit"] == {}
  assert standby["full-l03-one-ab-drop-out"]["longest_liquid_interruption_s_by_unit"] == {affected: 60}
  assert thermal["local-a-branch-out"]["affected_units"] == {}
  local = thermal["local-ab-branch-out"]
  assert local["affected_units"] == {affected: ["tcs_bulk"]}
  affected_group, healthy_group = local["unit_groups"]["L03"]
  assert affected_group["units"] == [affected]
  assert len(healthy_group["units"]) == 24
  assert healthy_group["result"]["nodes"]["tcs_bulk"]["peak_c"] == 35
  assert affected_group["result"]["nodes"]["tcs_bulk"]["first_above_s"] == pytest.approx(26.83, abs=.05)
  assert local["levels"]["L03"]["nodes"]["tcs_bulk"]["peak_c"] == pytest.approx(44.15, abs=.05)


def test_room_airflow_conserves_heat_with_bypass_and_hot_recirculation():
  from texsys_hvac_dynamics.room_airflow import RackAirflow, SupplyAirflow, room_airflow

  racks = {"Rack-A": RackAirflow(2, 8), "Rack-B": RackAirflow(2, 8)}
  basis = {"density_kg_m3": 1, "cp_kj_kgk": 1, "supply_c": 20, "other_room_kw": 0, "dew_point_c": 10}
  result = room_airflow(racks, {"Air": SupplyAirflow(4, 16, 4, {"Rack-A": 1}, 1)}, **basis)
  assert result["captured_m3_s"] == pytest.approx(2)
  assert result["bypass_m3_s"] == pytest.approx(2)
  assert result["mixed_return_c"] == pytest.approx(24)
  assert result["mixed_hot_c"] == pytest.approx(28)
  assert result["racks"]["Rack-A"]["inlet_c"] == pytest.approx(20)
  assert result["racks"]["Rack-B"]["inlet_c"] == pytest.approx(28)
  assert result["energy_residual_kw"] == pytest.approx(0, abs=1e-9)
  assert result["hot_pool_residual_kw"] == pytest.approx(0, abs=1e-9)

  capped = room_airflow(racks, {"Air": SupplyAirflow(8, 16, 4, {"Rack-A": 1}, 1)}, **basis)
  assert capped["fans"]["Air"]["flow_m3_s"] == pytest.approx(4)
  over_capacity = room_airflow(racks, {"Air": SupplyAirflow(4, 15, 4, {"Rack-A": 1}, 1)}, **basis)
  assert not over_capacity["maintains_supply_setpoint"]
  assert over_capacity["racks"]["Rack-A"]["inlet_c"] is None


def test_air_environment_distinguishes_rack_hotspots_from_room_return(hybrid_unit):
  assert build(hybrid_unit, targets=("datacenter:air-environment",)).status == "pass"
  distribution = json.loads((hybrid_unit / OUTPUT / "air-distribution.json").read_text())
  installed = json.loads((hybrid_unit / OUTPUT / "cooling-equipment.json").read_text())
  assert distribution["conflicts"] == []
  assert distribution["counts"] == {"units": 200, "diffuser_boxes": 400, "bound_air_ports": 400}
  report = json.loads((hybrid_unit / OUTPUT / "air-environment.json").read_text())
  assert report["plan_conflicts"] == []
  assert report["air_distribution_basis"] == "routed-ducts-and-open-hot-room-return"
  assert report["design_screening"]["status"] == "pass"
  cases = report["cases"]
  assert (cases["initial-normal"]["units"], cases["initial-normal"]["racks"]) == (50, 200)
  assert (cases["full-normal"]["units"], cases["full-normal"]["racks"]) == (100, 400)
  assert cases["full-a-power-out"]["worst_inlet"]["c"] == pytest.approx(24.95, abs=.01)
  assert list(cases["full-a-power-out"]["profiles"]["typical"]["fans"]) == ["PrecisionAir-B"]
  assert cases["full-local-one-crac-out"]["status"] == "pass"

  identity = "L03/Unit-01-01"
  placement = f"{identity}/PrecisionAir-B"
  fan = distribution["fans"]["PrecisionAir-B"]
  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "air-distribution-layout.usda"))
  node = stage.GetDefaultPrim().GetChild(_prim_name(placement))
  grille = node.GetChild("ReturnGrille")
  rack_outlet = node.GetChild(_prim_name("Rack-04/Outlet"))
  assert rack_outlet.GetAttribute("texsys:sdlConnection").Get() == fan["outlets"]["Rack-04"]["supply"]["sdl_edge"]
  assert fan["return"]["mode"] == "open-hot-room"
  bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(grille).ComputeAlignedRange()
  x, y, z = installed["placements"][placement]["ports_m"]["return"]
  assert (bounds.GetMidpoint()[0], bounds.GetMin()[1], bounds.GetMidpoint()[2]) == pytest.approx((x, y, z))
  fan_result = cases["full-a-power-out"]["profiles"]["typical"]["fans"]["PrecisionAir-B"]
  group = fan["outlets"]["Rack-04"]["group"]
  branch_flow = fan_result["branch_flow_m3_s"]["Rack-04"]
  terminal = json.loads((hybrid_unit / "src/air-environment.study.json").read_text())["supplies"]["PrecisionAir-B"]
  unit = json.loads((hybrid_unit / OUTPUT / "unit-study.json").read_text())
  terminal_loss = unit["room_air"]["density_kg_m3"] * terminal["terminal_loss_coefficient"]["Rack-04"] / 2
  terminal_loss *= (branch_flow / distribution["terminal_opening_m2"])**2
  assert fan_result["required_static_pa"] == pytest.approx(
    fan_result["common_loss_pa"] + fan_result["shared_branch_loss_pa"][group] + terminal_loss
  )
  assert fan_result["required_static_pa"] <= fan_result["static_limit_pa"]
  main = fan["main"][0]
  assert fan_result["duct_velocity_m_s"][main] * fan["routes"][main]["area_m2"] == pytest.approx(fan_result["flow_m3_s"])

  starved = cases["full-local-rack-starved"]
  profile = starved["profiles"][identity]
  assert starved["failed_units"] == {identity: ["rack-environment"]}
  assert profile["checks"]["supply-setpoint"] and profile["checks"]["mixed-return"]
  assert profile["mixed_return_c"] == pytest.approx(28.14, abs=.01)
  assert starved["worst_inlet"] == {"c": pytest.approx(34.56, abs=.01), "rack": "Rack-04", "unit": identity}
  assert profile["racks"]["Rack-04"]["captured_supply_m3_s"] == 0
  assert profile["rack_failures"] == {"Rack-04": ["inlet-temperature"]}
  assert profile["hot_pool_residual_kw"] == pytest.approx(0, abs=1e-9)
  assert cases["full-local-containment-open"]["worst_inlet"]["c"] == pytest.approx(29.02, abs=.01)
  humid = cases["full-local-humid"]["profiles"]["L04/Unit-02-01"]
  assert humid["surface_margin_c"] == -1
  assert not humid["checks"]["dew-point"]
  assert not humid["checks"]["condensation-margin"]
  stopped = cases["full-local-both-cracs-out"]
  assert len(stopped["unresolved_rack_inlets"]) == 4
  assert stopped["profiles"][identity]["mixed_return_c"] is None

  stage = Usd.Stage.Open(str(hybrid_unit / OUTPUT / "air-environment-layout.usda"))
  room = stage.GetDefaultPrim().GetChild(_prim_name(identity))
  hot_rack = room.GetChild(_prim_name("Rack-04"))
  assert hot_rack.IsA(UsdGeom.Cube)
  assert hot_rack.GetCustomDataByKey("inletC") == pytest.approx(34.56, abs=.01)
  bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(hot_rack).ComputeAlignedRange()
  assert tuple(bounds.GetMin()) == pytest.approx((5.8, 7.5, 14))


def test_air_distribution_rejects_conflicting_ab_routes(hybrid_unit):
  path = hybrid_unit / "src/air-distribution.study.json"
  design = json.loads(path.read_text())
  design["routes"]["PrecisionAir-B"]["raise_y_m"] = design["routes"]["PrecisionAir-A"]["header_y_m"]
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:air-distribution",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "air-distribution.json").read_text())
  assert any(
    issue["check"] == "independent-duct-clearance"
    and issue["duct"].startswith("PrecisionAir-A/")
    and issue["other"].startswith("PrecisionAir-B/")
    for issue in report["conflicts"]
  )


def test_air_environment_rejects_reversed_rack_orientation(hybrid_unit):
  path = hybrid_unit / "src/air-environment.study.json"
  design = json.loads(path.read_text())
  design["layout"]["rack_fronts"]["Rack-01"] = "-Y"
  path.write_text(json.dumps(design))
  assert build(hybrid_unit, targets=("datacenter:air-environment",)).status == "fail"
  report = json.loads((hybrid_unit / OUTPUT / "air-environment.json").read_text())
  assert {"rack": "Rack-01", "check": "front-to-cold-aisle"} in report["plan_conflicts"]
