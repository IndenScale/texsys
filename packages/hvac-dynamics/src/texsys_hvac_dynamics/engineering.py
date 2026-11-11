"""Scoped chiller-capacity changes through the existing transient HVAC solver."""

import json
import math
from pathlib import Path

from texsys.evidence import file_digest
from texsys.execution import evaluate_project
from texsys.project import ProjectError, inspect_project

from .domain import PhysicsUnresolved, compile_model
from .scenario import load_scenario


SCENARIO = "load-step"
CAPACITY_POINTER = "/components/Chiller/behavior/parameters/capacity_kw"
SCOPE = "概念冷站正常负荷阶跃：稳态起始，900 s 内在 300 s 从 40 kW 升至 80 kW；负荷出水不超过 12 °C、冷机出水不低于 4 °C，并检查模型守恒。"
LIMITATIONS = [
  "本范围是既有集总水系统概念模型；参数、控制信号与设备曲线尚未通过厂家资料或现场数据校准。",
  "温度极值与首次超限时间来自离散输出采样；首次超限样本不等同于连续轨迹的精确越限时刻。",
  "数值守恒通过仅证明该模型的内部一致性，不构成独立实测或真实设备性能验证。",
  "接受仅覆盖声明的正常负荷阶跃；pump-trip、其他故障、冗余、电气、结构、消防及施工条件未纳入。",
  "修改的是 Chiller 定义的能力假设，可能影响所有继承该定义且未覆盖此参数的实例；不是厂家选型批准。",
]
# These are this POC's explicit operating obligations, independent of filenames
# or regression expectations. Tighter numerical controls are allowed.
NORMAL_SIGNALS = {
  "ColdStation.CH-01.enabled": 1, "ColdStation.CH-01.setpoint_c": 7,
  "ColdStation.P-CHW-01.speed_ratio": 1, "ColdStation.P-CW-01.speed_ratio": 1,
  "ColdStation.V-01.opening": 1, "ColdStation.CT-01.fan_ratio": 1,
  "ColdStation.CT-01.ambient_c": 25, "ColdStation.PIPE-CHW-01.ambient_c": 25,
  "ColdStation.PIPE-CW-01.ambient_c": 25,
}
TEMPERATURE_REQUIREMENTS = (
  ("ColdStation.LOAD-01.outlet_temperature_c", "<=", 12),
  ("ColdStation.CH-01#chilled.outlet_temperature_c", ">=", 4),
)
NUMERICAL_LIMITS = {
  "rtol": 1e-6, "temperature_atol_k": 1e-7, "power_atol_w": 1e-3,
  "max_step_s": 10, "hydraulic_pressure_tolerance_pa": .05,
  "hydraulic_mass_tolerance_kg_s": 1e-7, "initial_residual_tolerance_w": .1,
}


def _read(path):
  return json.loads(path.read_text(encoding="utf-8"))


def _paths(root):
  project = inspect_project(root)
  if project is None:
    raise ProjectError("Initialize a texsys project before evaluating HVAC changes.")
  return root / project.source / "system.sdl.json", root / project.assets / "scenarios/load-step.json"


def _source(root, path, **details):
  return {"path": path.relative_to(root).as_posix(), "digest": file_digest(path), **details}


def _scope_errors(scenario):
  errors = []
  if scenario.duration_s != 900 or scenario.initial["mode"] != "steady-state":
    errors.append("声明范围要求稳态初始化与 900 s 计算时段。")
  load = scenario.signals.get("ColdStation.LOAD-01.load_w")
  steps = []
  if load:
    for point in load.points:
      if not steps or point[1] != steps[-1][1]:
        steps.append(point)
  if load is None or load.interpolation != "hold" or steps != [(0, 40000), (300, 80000)]:
    errors.append("实际负荷不符合在 300 s 从 40 kW 阶跃至 80 kW 的声明条件。")
  for name, value in NORMAL_SIGNALS.items():
    signal = scenario.signals.get(name)
    if signal is None or any(point[1] != value for point in signal.points):
      errors.append(f"正常工况要求 {name} 全程为 {value}。")
  for variable, operator, limit in TEMPERATURE_REQUIREMENTS:
    if not any(a["variable"] == variable and a["operator"] == operator and a["unit"] == "degC"
               and (a["value"] <= limit if operator == "<=" else a["value"] >= limit)
               for a in scenario.assertions):
      errors.append(f"缺少不弱于 {variable} {operator} {limit} degC 的温度约束。")
  if (scenario.sample_interval_s > 5 or scenario.solver["method"] != "BDF"
      or any(scenario.solver[key] > limit for key, limit in NUMERICAL_LIMITS.items())):
    errors.append("数值设置超出此案例已验证的 BDF 精度与采样范围；需要重新核对收敛。")
  return errors


class HvacCapacityChangeAdapter:
  id = "hvac:capacity-change"
  name = "冷机能力变更与瞬态验证"
  version = "1"
  description = "修改定义级冷机能力，复算正常负荷阶跃，审查温度轨迹、守恒与明确范围内的接受记录。"

  def supports(self, root: Path) -> bool:
    try:
      source, scenario = _paths(root)
      sdl = _read(source)
      return (scenario.is_file() and sdl.get("root") == "ColdStation"
              and sdl["components"]["ColdStation"]["instances"]["CH-01"]["component"] == "Chiller"
              and "Chiller" in sdl["components"])
    except (ProjectError, OSError, ValueError, KeyError, TypeError):
      return False

  def inputs(self, root: Path) -> list[dict]:
    source, _ = _paths(root)
    binding = _read(source)["components"]["Chiller"].get("behavior", {}).get("parameters", {}).get("capacity_kw", {})
    value = binding.get("value")
    if type(value) not in (int, float) or not math.isfinite(value):
      return []  # Missing/unknown values must be resolved in source, never defaulted.
    file = source.relative_to(root).as_posix()
    return [{"id": "hvac.chiller-capacity", "label": "Chiller 定义制冷能力", "file": file,
             "pointer": CAPACITY_POINTER + "/value", "value": value, "unit": "kW", "min": 0,
             "state": binding.get("state"), "object_ref": f"{file}#Chiller",
             "design": {"definition": f"{file}#Chiller"},
             "source": _source(root, source, pointer=CAPACITY_POINTER, state=binding.get("state"),
                               description=binding.get("source")),
             "description": "修改定义级假设；所有未覆盖 capacity_kw 的 Chiller 实例共同受影响。求解要求能力严格大于零。"}]

  def evaluate(self, root: Path) -> dict:
    source, scenario_path = _paths(root)
    source_file = source.relative_to(root).as_posix()
    base = {"limitations": list(LIMITATIONS), "declared_scope": SCOPE, "fidelity": "concept",
            "targets": [], "scenarios": [SCENARIO], "verification": "not-run",
            "other_scenarios": [{"id": name, "assessment": "not-assessed", "reason": "不在本次接受范围；未运行。"}
                                for name in ("pump-speed", "pump-trip")]}
    try:
      model = compile_model(root)
    except PhysicsUnresolved as error:
      return {**base, "assessment": "indeterminate", "solver_execution": "not-run", "scope_verified": False,
              "scope": "模型输入未定，求解器未运行；不能接受正常负荷阶跃结论。", "extraction": [],
              "diagnostics": [{"code": "H_UNRESOLVED", "message": str(error)}],
              "checks": [{"id": "hvac:model-inputs", "label": "可执行物理参数", "status": "indeterminate",
                          "object_ref": "ColdStation", "design": {"definition": f"{source_file}#ColdStation"},
                          "message": str(error), "evidence": _source(root, source)}]}
    scenario = load_scenario(scenario_path, model)
    errors = _scope_errors(scenario)
    execution = evaluate_project(root, scenario_path, backend="scipy")
    report = _read(execution.report)
    if report["status"] == "error":
      messages = "; ".join(item["message"] for item in report.get("diagnostics", []))
      raise ProjectError(f"HVAC transient solver did not complete: {messages}. See {execution.report}.")
    if "variables" not in report:
      raise ProjectError(f"HVAC transient solver produced no trajectory. See {execution.report}.")
    sdl = _read(source)
    designs = {f"{model.root}.{name}": {"occurrence": [name]}
               for name in sdl["components"][model.root]["instances"]}
    designs[model.root] = {"definition": f"{source_file}#{model.root}"}
    scenario_source = _source(root, scenario_path)
    result_source = _source(root, execution.report, state="assumed", description="概念模型的分析派生量；不是现场实测。")
    checks = [{"id": "hvac:scenario-scope", "label": "实际工况与声明范围一致", "object_ref": model.root,
               "design": designs[model.root], "scenario": SCENARIO, "status": "indeterminate" if errors else "pass",
               "message": "；".join(errors) if errors else "负荷阶跃、正常控制、温度约束与数值精度处于声明范围。",
               "evidence": {"source": scenario_source, "declaration": _read(scenario_path), "errors": errors,
                            "numerical_profile": dict(scenario.solver)}}]
    extraction = []
    for component in model.components:
      if component.kind == "chiller":
        binding = component.parameters["capacity_kw"]
        name = designs[component.identity]["occurrence"][0]
        instance = sdl["components"][model.root]["instances"][name]
        pointer = (f"/components/{model.root}/instances/{name}/parameters/capacity_kw"
                   if "capacity_kw" in instance.get("parameters", {})
                   else f"/components/{component.definition}/behavior/parameters/capacity_kw")
        extraction.append({"id": f"capacity:{component.identity}", "label": "实例实际编译制冷能力",
                           "object_ref": component.identity, "design": designs[component.identity],
                           "value": binding.value, "unit": binding.unit, "state": binding.state,
                           "source": _source(root, source, pointer=pointer, state=binding.state, description=binding.source)})
    for index, assertion in enumerate(report["assertions"]):
      variable = report["variables"].get(assertion.get("variable"))
      reference = variable["semantic_ref"] if variable else model.root
      check = {"id": f"hvac:assertion:{assertion.get('variable', assertion.get('rule', 'unknown'))}:{index}",
               "label": "负荷出水温度上限" if assertion.get("variable") == TEMPERATURE_REQUIREMENTS[0][0]
                        else "冷机出水温度下限" if assertion.get("variable") == TEMPERATURE_REQUIREMENTS[1][0]
                        else assertion.get("rule", "附加工况约束"),
               "object_ref": reference, "scenario": SCENARIO, "status": assertion["status"],
               "evidence": {"assertion": assertion, "source": scenario_source, "report": result_source}}
      if reference in designs:
        check["design"] = designs[reference]
      if "message" in assertion:
        check["message"] = assertion["message"]
      if variable is not None and "actual" in assertion:
        times, values = report["time_s"], variable["values"]
        compare = (lambda value: value > assertion["value"]) if assertion["operator"] == "<=" else (lambda value: value < assertion["value"])
        first = next((i for i, value in enumerate(values) if compare(value)), None)
        peak = values.index(assertion["actual"])
        check.update(actual=assertion["actual"], limit=assertion["value"], unit=assertion["unit"], operator=assertion["operator"])
        check["evidence"].update(variable=assertion["variable"], peak_time_s=times[peak],
                                 first_violation_time_s=times[first] if first is not None else None,
                                 time_s=times, values=values)
        check["message"] = f"采样极值 {assertion['actual']:.4f} {assertion['unit']}，出现在 {times[peak]:g} s。"
        if first == 0:
          check["message"] += "初始采样已超限。"
        elif first is not None:
          check["message"] += f"首次超限样本为 {times[first]:g} s；精确越限时刻介于相邻采样之间。"
        extraction.append({"id": f"extreme:{assertion['variable']}:{index}", "label": check["label"] + "：采样极值",
                           "object_ref": reference, **({"design": designs[reference]} if reference in designs else {}),
                           "value": assertion["actual"], "unit": assertion["unit"], "state": "assumed",
                           "source": result_source, "details": {"time_s": times[peak], "variable": assertion["variable"],
                              "model_digest": report["model_digest"], "scenario_digest": report["scenario_digest"]}})
      checks.append(check)
    numerical = [
      ("mass_residual_kg_s", "质量守恒残差", scenario.solver["hydraulic_mass_tolerance_kg_s"], "kg/s", "<="),
      ("pressure_residual_pa", "压力平衡残差", scenario.solver["hydraulic_pressure_tolerance_pa"], "Pa", "<="),
      ("energy_residual_w", "瞬时能量守恒残差", .1, "W", "<"),
      ("integrated_energy_error_j", "积分能量守恒误差", 1., "J", "<"),
    ]
    for identity, label, limit, unit, operator in numerical:
      actual = report["checks"][identity]
      passed = actual < limit if operator == "<" else actual <= limit
      checks.append({"id": f"hvac:{identity}", "label": label, "object_ref": model.root, "design": designs[model.root],
                     "scenario": SCENARIO, "status": "pass" if passed else "fail", "actual": actual,
                     "limit": limit, "unit": unit, "operator": operator,
                     "evidence": {"source": result_source, "numerical_tolerance": True},
                     "message": "当前概念模型的数值一致性检查；不代表厂家或现场验证。"})
    initialization = report["initialization"]
    initial_residual = initialization.get("maximum_residual_w")
    checks.append({"id": "hvac:initialization", "label": "稳态初始化残差", "object_ref": model.root,
                   "design": designs[model.root], "scenario": SCENARIO,
                   "status": initialization["status"] if initial_residual is not None else "indeterminate",
                   "actual": initial_residual, "limit": scenario.solver["initial_residual_tolerance_w"],
                   "unit": "W", "operator": "<=", "evidence": initialization})
    verdicts = {item["status"] for item in checks} | {report["status"]}
    assessment = "fail" if "fail" in verdicts else "indeterminate" if "indeterminate" in verdicts else "pass"
    return {**base, "assessment": assessment, "solver_execution": "completed", "scope_verified": not errors,
            "scope": SCOPE if not errors else "工况范围未成立：实际条件与正常负荷阶跃声明不一致，不能据此接受原范围。",
            "checks": checks, "extraction": extraction, "model_digest": report["model_digest"],
            "scenario_digest": report["scenario_digest"], "source_digests": report["source_digests"],
            "scenario_source": scenario_source, "backend": report["backend"], "events": report["events"],
            "assumptions": report["assumptions"], "artifacts": [path.relative_to(root).as_posix() for path in execution.items]}
