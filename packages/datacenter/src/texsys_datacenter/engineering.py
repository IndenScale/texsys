"""Traceable FWS route changes using the existing concept hydraulic evaluators."""

import json
from math import isclose, pi
from pathlib import Path

from texsys.evidence import file_digest
from texsys.project import ProjectError, inspect_project


TARGET = "datacenter:fws-branches"
DESIGN_SCENARIOS = ("initial-normal", "full-normal", "full-supply-a-out")
SCENARIO_CONTRACTS = {
  "initial-normal": {"phase": "initial", "condition": "normal"},
  "full-normal": {"phase": "full-phase", "condition": "normal"},
  "full-supply-a-out": {"phase": "full-phase", "condition": "a-power-out"},
}
FWS_DISTURBANCES = (
  "unavailable_fws_segments", "unavailable_risers", "leak_zones", "unavailable_detectors",
  "stuck_open_valves", "unavailable_fws_pumps", "fws_pump_speed_limits", "unavailable_fws_branch_segments",
)
SCOPE = "概念设施水支管：初期正常、满配正常及满配 A 路失电时的逐机房供冷与已建模几何净距。"
LIMITATIONS = [
  "evaluation 仅分离计算执行与算例回归判定；模型保真度仍为 concept，不代表施工设计或现场验收。",
  "管路、泵曲线、CDU 接口、温差和流量限制采用算例假设，尚未取得厂家曲线及现场标定。",
  "提取长度是当前概念几何中的确定值，输入依据仍为 assumed，不升级为现场实测。",
  "本次接受仅覆盖列明工况的 FWS 供冷与几何检查；TCS、热瞬态、控制、电气、结构承载、消防和施工审查未包含在接受范围。",
  "其他专业影响关系尚未登记；这些专业的既有结论需要复核，不能据此认定不受影响。",
]


def _read(path):
  return json.loads(path.read_text(encoding="utf-8"))


def _source(root):
  project = inspect_project(root)
  if project is None:
    raise ProjectError("Initialize a texsys project before evaluating FWS changes.")
  return root / project.source


def _scenario_definitions(root, source):
  """Bind named review conditions to the actual phase and failure definitions.

  These are the three conditions explicitly selected by this POC, not an
  inference from the fixture's mutable expected-result fields. The A feed and
  unit source are resolved through the modeled heat-rejection power interface.
  """
  building = _read(source / "building.study.json")
  unit = _read(source / "unit.study.json")
  sources = [{"path": (source / name).relative_to(root).as_posix(), "digest": file_digest(source / name)}
             for name in ("building.study.json", "unit.study.json")]
  a_feed = building.get("shared_heat_rejection", {}).get("HeatRejection-A", {}).get("power_feed")
  a_source = building.get("shared_power", {}).get(a_feed, {}).get("unit_source")
  definitions = []
  for identity, contract in SCENARIO_CONTRACTS.items():
    case = building.get("scenarios", {}).get(identity)
    errors = []
    phase = building.get("full_phase") if contract["phase"] == "full-phase" else contract["phase"]
    unit_case = unit.get("scenarios", {}).get(case.get("unit_scenario")) if isinstance(case, dict) else None
    lost_feed = [a_feed] if contract["condition"] == "a-power-out" else []
    lost_source = [a_source] if contract["condition"] == "a-power-out" else []
    if not isinstance(case, dict) or not isinstance(unit_case, dict):
      errors.append("缺少楼级工况或所引用的单元工况。")
    else:
      if phase is None or case.get("phase") != phase:
        errors.append(f"工况应采用已声明阶段 {phase!r}，实际为 {case.get('phase')!r}。")
      if (contract["condition"] == "a-power-out" and (not a_feed or not a_source)
          or case.get("unavailable_power") != lost_feed
          or unit_case.get("unavailable") != lost_source):
        errors.append("实际楼级进线和单元电源故障与声明的正常／A 路失电条件不一致。")
      if case.get("unavailable_heat_rejection") != [] or any(case.get(key) for key in FWS_DISTURBANCES):
        errors.append("该设计工况包含声明范围外的排热或设施水扰动，应另行定义审查范围。")
    definitions.append({"id": identity, "status": "indeterminate" if errors else "pass",
                        "contract": {**contract, "resolved_phase": phase, "unavailable_power": lost_feed,
                                     "unit_unavailable": lost_source},
                        "building_case": case, "unit_case": unit_case,
                        "phase": building.get("phases", {}).get(case.get("phase")) if isinstance(case, dict) else None,
                        "sources": sources, "errors": errors})
  return definitions


def _shortage_evidence(identity, scenario, case, route_case, branches, config, density, sources):
  """Explain active constraints from dispatch evidence, without rerunning a solver."""
  level, unit = identity.split("/", 1)
  hydraulic = case["fws"]["hydraulics"]
  constraints, pumps = [], []
  for train, delivered in route_case["delivered_kw_by_unit_train"].get(identity, {}).items():
    if delivered <= 1e-8:
      continue
    floor_path = route_case["floor_paths"][f"{level}/{train}"]
    for route_id in branches["unit_paths"][f"{level}/{train}"][unit]:
      route = branches["routes"][route_id]
      pipe = config["pipe_types"][route["kind"]]
      flow = floor_path["segment_flow_m3_s"][route_id.split("/", 1)[1]]
      velocity = flow / (pi * route["inside_m"] ** 2 / 4)
      for kind, actual, limit, unit_name in (
        ("velocity", velocity, pipe["max_velocity_m_s"], "m/s"),
        ("mass-flow", flow * density, pipe["capacity_kg_s"], "kg/s"),
      ):
        if actual >= limit or isclose(actual, limit, rel_tol=1e-6, abs_tol=1e-8):
          constraints.append({"kind": kind, "object_ref": route_id, "actual": actual,
                              "limit": limit, "unit": unit_name, "inside_m": route["inside_m"],
                              "flow_m3_s": flow, "sources": sources,
                              "report": "datacenter/fws-branches.json", "scenario": scenario})
    bank = hydraulic["banks"][train]
    margin = bank["head_margin_kpa"]
    pumps.append({"object_ref": train, "head_margin_kpa": margin,
                  "required_kpa": bank["required_kpa"], "pump_curve_limit_kpa": bank["pump_curve_limit_kpa"],
                  "available_dispatch_pumps": bank["available_dispatch_pumps"],
                  "max_speed_ratio": bank["max_speed_ratio"], "critical_terminal": bank["critical_terminal"],
                  "report": "datacenter/building-study.json", "scenario": scenario})
    if margin is not None and margin <= 1e-6:
      constraints.append({"kind": "pump-head", "object_ref": train, "actual": bank["required_kpa"],
                          "limit": bank["pump_curve_limit_kpa"], "unit": "kPa",
                          "report": "datacenter/building-study.json", "scenario": scenario})
  messages = []
  for kind, label in (("velocity", "流速"), ("mass-flow", "质量流量")):
    reached = [item for item in constraints if item["kind"] == kind]
    if reached:
      values = "、".join(f"{limit:g}" for limit in sorted({item["limit"] for item in reached}))
      messages.append(f"供冷路径中 {len(reached)} 个管段达到{label}上限 {values} {reached[0]['unit']}")
  if any(item["kind"] == "pump-head" for item in constraints):
    messages.append("求解器报告泵组已达到可用泵与转速上限对应的扬程极限")
  margins = [item["head_margin_kpa"] for item in pumps if item["head_margin_kpa"] is not None]
  if margins and min(margins) > 1e-6:
    messages.append(f"按可用泵与转速上限，参与供冷泵组的最小扬程余量为 {min(margins):.2f} kPa")
  if not constraints:
    messages.insert(0, "当前配流不能满足需求；本条证据尚未定位到该机房路径的生效约束")
  return {"hydraulic_constraints": constraints, "pump_head": pumps, "dispatch_method": hydraulic["method"]}, "；".join(messages) + "。"


class FwsChangeAdapter:
  id = "datacenter:fws-change"
  name = "设施水管路变更与验证"
  version = "1"

  def supports(self, root: Path) -> bool:
    project = inspect_project(root)
    return project is not None and all((root / project.source / name).is_file() for name in (
      "fws-branches.study.json", "fws.study.json", "building.study.json", "system.sdl.json",
      "floor.study.json", "frame.study.json", "fire.study.json", "drainage.study.json",
    ))

  def inputs(self, root: Path) -> list[dict]:
    source = _source(root)
    file = (source / "fws-branches.study.json").relative_to(root).as_posix()
    config = _read(root / file)
    layout = _read(source / "building.study.json")["layout"]
    last_origin = layout["origin_m"][0] + (layout["count"][0] - 1) * layout["pitch_m"][0]
    result = [{
      "id": "fws.spine-x", "label": "楼层主配水管 X 坐标", "file": file,
      "pointer": "/spine_x_m", "unit": "m", "object_ref": f"{file}#/spine_x_m",
      "value": config["spine_x_m"], "min": last_origin + .001,
      "max": layout["floor_m"][0] - .001, "state": config["state"],
      "description": "改变全部楼层供回水主配水管位置；实际净距与接管可达性在求值时检查。",
    }]
    labels = {"spine": "主配水管", "row": "机柜列配水管", "drop": "CDU 接管"}
    for kind, label in labels.items():
      pipe = config["pipe_types"][kind]
      pointer = f"/pipe_types/{kind}/diameter_m"
      result.append({
        "id": f"fws.{kind}-diameter", "label": f"{label}内径", "file": file,
        "pointer": pointer, "unit": "m", "object_ref": f"{file}#{pointer}",
        "value": pipe["diameter_m"], "min": .01, "max": pipe["outside_m"] - .000001,
        "state": config["state"],
        "description": f"影响全部楼层同族管段；外包络直径保持 {pipe['outside_m']} m，实际管材规格仍需选型。",
      })
    return result

  def impact(self, root: Path, changes: list[dict]) -> list[dict]:
    return [
      {"id": "fws-route-extraction", "label": "重新提取管路几何与水力参数",
       "object_ref": "FWS/floor-branches", "inputs": [item["input_id"] for item in changes],
       "targets": [TARGET], "message": "同一管段身份重新计算中心线长度、内径和弯头损失，再进入设施水泵组配流。"},
      {"id": "fws-delivery", "label": "复核三个设计工况的逐机房供冷",
       "object_ref": "FWS/cooling-delivery", "scenarios": list(DESIGN_SCENARIOS),
       "targets": [TARGET], "message": "配流受管段流速和泵扬程约束；总量满足仍需逐机房检查，避免局部缺冷被汇总掩盖。"},
      {"id": "fws-geometry", "label": "复核管路与已建模结构的净距",
       "object_ref": "FWS/clearance", "targets": [TARGET],
       "message": "检查 A/B 管路、已有设施水及排液路由、钢柱、楼板和梁底净距。"},
      {"id": "unregistered-disciplines", "label": "其他专业待复核", "coverage": "unregistered",
       "object_ref": "Building", "targets": [],
       "message": "TCS、热响应、控制、电气、结构承载、消防和施工影响尚未纳入本 adapter；不得视为不受影响。"},
    ]

  def evaluate(self, root: Path) -> dict:
    # The evaluation profile continues after a valid negative design conclusion.
    # Invalid inputs and solver errors still stop the ordinary build runner.
    from texsys.execution import build_project

    execution = build_project(root, targets=(TARGET,), profile="evaluation")
    build_report = _read(execution.report)
    if execution.status != "pass":
      raise ProjectError(f"FWS evaluation did not complete: {execution.status}. See {execution.report}.")
    project = inspect_project(root)
    directory = root / project.build / "datacenter"
    building, branches, unit, floor, frame = (
      _read(directory / name) for name in (
        "building-study.json", "fws-branches.json", "unit-study.json", "floor-study.json", "frame-study.json",
      )
    )
    source = _source(root)
    source_file = (source / "fws-branches.study.json").relative_to(root).as_posix()
    config = _read(root / source_file)
    source_evidence = {"path": source_file, "digest": file_digest(root / source_file), "state": config["state"]}
    fws_file = source / "fws.study.json"
    density = _read(fws_file)["hydraulics"]["density_kg_m3"]
    hydraulic_sources = [source_evidence, {"path": fws_file.relative_to(root).as_posix(), "digest": file_digest(fws_file)}]
    checks, extraction = [], []
    scenario_definitions = _scenario_definitions(root, source)
    for definition in scenario_definitions:
      checks.append({"id": f"fws:scenario-scope:{definition['id']}", "label": "设计工况与声明范围一致",
                     "object_ref": f"{source.relative_to(root).as_posix()}/building.study.json#/scenarios/{definition['id']}",
                     "scenario": definition["id"], "status": definition["status"], "evidence": definition,
                     "message": "；".join(definition["errors"]) if definition["errors"] else "阶段、进线故障和单元电源故障与声明范围一致。"})
    geometry_issues = [
      *branches["conflicts"],
      *({"object": pair, "check": "floor-plan-overlap"} for pair in floor["plan_clashes"]),
      *({**item, "check": "rack-column-overlap"} for item in frame["rack_column_clashes"]),
      *({**item, "check": "cooling-column-overlap"} for item in frame["cooling_column_clashes"]),
    ]
    checks.append({"id": "fws:geometry", "label": "已建模管路与结构净距", "object_ref": "FWS/clearance",
                   "status": "fail" if geometry_issues else "pass", "actual": len(geometry_issues),
                   "limit": 0, "operator": "<=", "unit": "conflicts", "scenario": "geometry", "evidence": geometry_issues})
    for scenario in DESIGN_SCENARIOS:
      case = building["scenarios"].get(scenario)
      route_case = branches["cases"].get(scenario)
      if case is None or route_case is None:
        checks.append({"id": f"fws:{scenario}", "label": "缺少声明的设计工况", "object_ref": "FWS",
                       "scenario": scenario, "status": "indeterminate"})
        continue
      phase = building["phases"][case["phase"]]
      expected = {f"{level}/{name}" for level in phase["levels"]
                  for name, zone in building["floor_zones"].items() if zone["role"] == "unit"}
      demand = unit["heat_budget_kw"]["liquid_it"]
      deliveries = route_case["delivered_kw_by_unit_train"]
      for identity in sorted(expected):
        value = sum(deliveries.get(identity, {}).values())
        evidence = {"report": "datacenter/fws-branches.json", "case": scenario,
                    "delivered_kw_by_train": deliveries.get(identity, {})}
        message = {}
        if value + 1e-6 < demand:
          hydraulic_evidence, explanation = _shortage_evidence(
            identity, scenario, case, route_case, branches, config, density, hydraulic_sources)
          evidence.update(hydraulic_evidence)
          message["message"] = explanation
        checks.append({"id": f"fws:{scenario}:{identity}", "label": "机房液侧供冷需求",
                       "object_ref": identity, "scenario": scenario,
                       "status": "pass" if value + 1e-6 >= demand else "fail",
                       "actual": value, "limit": demand, "unit": "kW", "operator": ">=",
                       "margin": value - demand,
                       "evidence": evidence, **message})
      for identity, result in sorted(route_case["floor_paths"].items()):
        extraction.append({"id": f"pressure:{scenario}:{identity}", "label": "楼层配水最不利回路压损",
                           "object_ref": f"{identity}/{result['critical_unit']}", "scenario": scenario,
                           "value": result["critical_loss_kpa"], "unit": "kPa", "state": config["state"],
                           "source": source_evidence, "details": {"critical_unit": result["critical_unit"],
                              "delivered_kw": result["delivered_kw"], "unit_flow_m3_s": result["unit_flow_m3_s"]}})
    for identity, route in sorted(branches["routes"].items()):
      pipe = config["pipe_types"][route["kind"]]
      extraction.append({"id": f"length:{identity}", "label": "管段中心线长度", "object_ref": identity,
                         "value": route["hydraulic_length_m"], "unit": "m", "state": config["state"],
                         "source": source_evidence, "details": {"inside_m": route["inside_m"],
                            "outside_m": route["outside_m"], "bends": len(route["points_m"]) - 2,
                            "max_velocity_m_s": pipe["max_velocity_m_s"], "capacity_kg_s": pipe["capacity_kg_s"],
                            "minor_loss_coefficient": pipe["minor_loss_coefficient"]
                            + config["elbow_loss_coefficient"] * (len(route["points_m"]) - 2)}})
    verdicts = {item["status"] for item in checks}
    assessment = "fail" if "fail" in verdicts else "indeterminate" if "indeterminate" in verdicts else "pass"
    verification = "fail" if any(item["details"].get("verification") == "fail"
                                  for item in build_report["targets"]) else "pass"
    scope_verified = all(item["status"] == "pass" for item in scenario_definitions)
    return {"assessment": assessment, "execution": "completed", "verification": verification,
            "checks": checks, "extraction": extraction, "limitations": list(LIMITATIONS),
            "scope": SCOPE if scope_verified else "工况范围未成立：实际工况定义与声明条件不一致，不能据此接受初期／满配／A 路失电结论。",
            "declared_scope": SCOPE, "scope_verified": scope_verified, "scenario_definitions": scenario_definitions,
            "fidelity": "concept", "scenarios": list(DESIGN_SCENARIOS),
            "targets": [item["id"] for item in build_report["targets"]],
            "artifacts": build_report["artifacts"],
            "upstream_diagnostics": [{"target": item["id"], "diagnostics": item["diagnostics"],
                                      "open_items": item["open_items"]}
                                     for item in build_report["targets"]
                                     if item["diagnostics"] or item["open_items"]],
            "outside_scope_assessments": {
              "note": "保留上游结构计算结果供复核；不纳入本次设施水接受范围。",
              "floor": (floor.get("structural_screening") or {}).get("cases", {}),
              "frame": frame["cases"],
            },
            "other_scenarios": [{"id": key, "assessment": value["status"], "expected": value["expected"]}
                                for key, value in building["scenarios"].items() if key not in DESIGN_SCENARIOS]}
