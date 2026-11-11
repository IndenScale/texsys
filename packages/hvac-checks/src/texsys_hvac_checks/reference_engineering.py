"""Conditional arithmetic over a frozen report transcription, never field performance."""

import math
from pathlib import Path, PurePosixPath
import re

from texsys.evidence import file_digest
from texsys.formal import VALUE_STATES, _load_json
from texsys.project import inspect_project


STUDY = "reference-capacity.study.json"
FACTS = {
  "chiller_count": ("1", True), "per_chiller_capacity_kw": ("kW", False),
  "air_load_count": ("1", True), "per_air_load_kw": ("kW", False),
  "liquid_load_count": ("1", True), "per_liquid_load_kw": ("kW", False),
  "reported_total_capacity_kw": ("kW", False),
}
INPUTS = {
  "air_load_fraction": ("reference.air-load-fraction", "风冷负载同时运行比例假设"),
  "liquid_load_fraction": ("reference.liquid-load-fraction", "液冷负载同时运行比例假设"),
  "available_chillers": ("reference.available-chillers", "可用冷机台数假设"),
}
LIMITATIONS = [
  "只核对冻结快照的摘要、来源登记与数值关系；没有验证原 PDF 的内容、签名、真实性或原件可获得性。",
  "报告中的声明配置不是设备性能实测。账面制冷能力不含环境、工况、效率、控制及传热约束的修正。",
  "负载设备能力合计不表示实际负荷或同时运行承诺；负荷比例与可用台数都是显式条件假设。",
  "未运行物理性能求解器，未取得独立现场数据；不能以算术通过接受真实工程设计或设备选型。",
  "本参考审查未映射至原有 generic SDL 布局模型，不覆盖管网、控制、冗余、施工或其他专业。",
]


class ReferenceInputError(ValueError):
  def __init__(self, code, message):
    super().__init__(message)
    self.code = code


def _require(condition, message, code="REFERENCE_INPUT_INVALID"):
  if not condition:
    raise ReferenceInputError(code, message)


def _text(value):
  return isinstance(value, str) and bool(value.strip())


def _number(value):
  try:
    return type(value) in (int, float) and math.isfinite(value)
  except OverflowError:
    return False


def _sha256(value, *, prefix_required=False):
  _require(isinstance(value, str), "来源摘要必须是 SHA-256 字符串。", "REFERENCE_SOURCE_INVALID")
  pattern = r"sha256:[0-9a-f]{64}" if prefix_required else r"(?:sha256:)?[0-9a-f]{64}"
  _require(re.fullmatch(pattern, value) is not None, "来源摘要必须包含完整的 SHA-256 十六进制值。", "REFERENCE_SOURCE_INVALID")
  return value if value.startswith("sha256:") else f"sha256:{value}"


def _read(path):
  value = _load_json(path.read_text(encoding="utf-8"))
  _require(isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 1,
           f"{path.name} 必须是 schema_version 1 的对象。")
  return value


def _value(binding, label, unit, *, source=True):
  _require(isinstance(binding, dict), f"缺少必需绑定：{label}。", "REFERENCE_UNRESOLVED")
  _require(binding.get("unit") == unit, f"{label} 的单位必须是 {unit}。")
  if source:
    _require(_text(binding.get("source")), f"{label} 缺少明确来源。", "REFERENCE_SOURCE_INVALID")
  state = binding.get("state")
  _require(isinstance(state, str) and state in VALUE_STATES, f"{label} 的值状态无效。")
  if state not in {"known", "assumed"}:
    _require("value" not in binding, f"{label} 的 {state} 状态不得携带数值。")
    raise ReferenceInputError("REFERENCE_UNRESOLVED", f"{label} 为 {state}，不补默认值、不计算数值结论。")
  _require(_number(binding.get("value")), f"{label} 必须具有有限数值，布尔值不作为数值。")
  return binding["value"]


def _load(root):
  project = inspect_project(root)
  _require(project is not None, "参考审查需要已初始化的项目。")
  study_path = root / project.source / STUDY
  _require(study_path.resolve() == study_path, "参考研究文件不得使用符号链接。", "REFERENCE_PATH_INVALID")
  study = _read(study_path)
  reference = study.get("reference")
  _require(isinstance(reference, dict), "研究文件缺少 reference 来源绑定。", "REFERENCE_SOURCE_INVALID")
  name = reference.get("path")
  _require(_text(name) and "\\" not in name and "\0" not in name, "快照路径必须是项目 assets 内的 JSON 文件。", "REFERENCE_PATH_INVALID")
  relative = PurePosixPath(name)
  _require(not relative.is_absolute() and ".." not in relative.parts and relative.as_posix() == name,
           "快照路径不得越出项目 assets。", "REFERENCE_PATH_INVALID")
  path = root / name
  _require(path.is_relative_to(root / project.assets) and path.resolve() == path and path.suffix == ".json" and path.is_file(),
           "快照必须是项目 assets 内现存的非符号链接 JSON 文件。", "REFERENCE_PATH_INVALID")
  expected = _sha256(reference.get("digest"), prefix_required=True)
  actual = file_digest(path)
  _require(expected == actual, f"冻结来源快照摘要不匹配：声明 {expected}，当前 {actual}；拒绝使用变化后的数值。", "REFERENCE_DIGEST_MISMATCH")
  snapshot = _read(path)
  _require(_text(snapshot.get("id")), "快照缺少稳定身份。", "REFERENCE_SOURCE_INVALID")
  unresolved = snapshot.get("unresolved")
  _require(isinstance(unresolved, list) and all(_text(item) for item in unresolved), "快照 unresolved 必须列出非空说明字符串。")
  sources = snapshot.get("sources")
  _require(isinstance(sources, list) and bool(sources), "快照必须登记来源及完整 SHA-256 摘要。", "REFERENCE_SOURCE_INVALID")
  source_index = {}
  for item in sources:
    _require(isinstance(item, dict) and _text(item.get("id")) and item["id"] not in source_index,
             "快照来源必须有唯一非空 id。", "REFERENCE_SOURCE_INVALID")
    source_index[item["id"]] = {"id": item["id"], "sha256": _sha256(item.get("sha256"))}
  configuration = snapshot.get("configuration")
  _require(isinstance(configuration, dict), "快照缺少 configuration。", "REFERENCE_UNRESOLVED")
  values, fact_sources = {}, {}
  for key, (unit, integer) in FACTS.items():
    fact = configuration.get(key)
    value = _value(fact, key, unit, source=False)
    _require(_text(fact.get("source_id")) and fact["source_id"] in source_index
             and _text(fact.get("location")) and _text(fact.get("meaning")),
             f"{key} 必须引用已登记的 source_id、原文位置和含义。", "REFERENCE_SOURCE_INVALID")
    _require(value > 0 and (not integer or int(value) == value), f"{key} 必须为正{'整数' if integer else '数'}。")
    values[key] = value
    fact_sources[key] = {"pointer": f"/configuration/{key}", "source_id": fact["source_id"],
                         "source_sha256": source_index[fact["source_id"]]["sha256"],
                         "location": fact["location"], "meaning": fact["meaning"], "state": fact["state"]}
  assumptions = study.get("assumptions")
  _require(isinstance(assumptions, dict), "研究文件缺少 assumptions。", "REFERENCE_UNRESOLVED")
  assumed = {key: _value(assumptions.get(key), key, "1") for key in INPUTS}
  for key in ("air_load_fraction", "liquid_load_fraction"):
    _require(0 <= assumed[key] <= 1, f"{key} 必须处于 0 到 1。")
  available = assumed["available_chillers"]
  _require(0 <= available <= values["chiller_count"] and int(available) == available,
           "available_chillers 必须为不超过来源声明台数的非负整数。")
  requirements = study.get("requirements")
  _require(isinstance(requirements, dict), "研究文件缺少 requirements。", "REFERENCE_UNRESOLVED")
  minimum = _value(requirements.get("min_margin_kw"), "min_margin_kw", "kW")
  _require(minimum >= 0, "min_margin_kw 不得低于 0 kW；允许更严格的要求。")
  return {"study": study, "study_path": study_path.relative_to(root).as_posix(), "study_digest": file_digest(study_path),
          "snapshot": snapshot, "snapshot_path": name, "snapshot_digest": actual,
          "facts": values, "fact_sources": fact_sources, "assumptions": assumed, "minimum": minimum}


def _evidence(data, facts=(), assumptions=(), *, requirement=False):
  inputs = [{"path": data["study_path"], "digest": data["study_digest"], "pointer": f"/assumptions/{key}",
             "state": data["study"]["assumptions"][key]["state"], "description": data["study"]["assumptions"][key]["source"]}
            for key in assumptions]
  if requirement:
    inputs.append({"path": data["study_path"], "digest": data["study_digest"], "pointer": "/requirements/min_margin_kw",
                   "binding": data["study"]["requirements"]["min_margin_kw"]})
  return {"path": data["snapshot_path"], "digest": data["snapshot_digest"],
          "snapshot": {"path": data["snapshot_path"], "digest": data["snapshot_digest"]},
          "facts": [data["fact_sources"][key] for key in facts], "inputs": inputs,
          "original_source_content_verified": False}


class ReferenceCapacityAdapter:
  id = "hvac:reference-capacity"
  name = "真实资料容量审查"
  version = "1"
  description = "在冻结报告配置与显式负荷假设下核对账面容量；保留真实性能、同时运行和现场验证尚未确认的结论。"

  def supports(self, root: Path):
    project = inspect_project(root)
    return project is not None and (root / project.source / STUDY).is_file()

  def inputs(self, root: Path):
    try:
      data = _load(root)
    except (ReferenceInputError, OSError, ValueError, TypeError):
      return []  # Invalid or unresolved source bindings require an explicit source-aware repair.
    return [{"id": identity, "label": label, "file": data["study_path"], "pointer": f"/assumptions/{key}/value",
             "value": data["assumptions"][key], "unit": "1", "state": data["study"]["assumptions"][key]["state"],
             "source": data["study"]["assumptions"][key]["source"],
             "object_ref": f"reference:{data['snapshot']['id']}#assumptions/{key}",
             "min": 0, "max": data["facts"]["chiller_count"] if key == "available_chillers" else 1}
            for key, (identity, label) in INPUTS.items()]

  def impact(self, root: Path, changes: list[dict]):
    data = _load(root)
    for change in changes:
      if change["input_id"] == "reference.available-chillers":
        candidate = change["after"]
        _require(_number(candidate) and 0 <= candidate <= data["facts"]["chiller_count"] and int(candidate) == candidate,
                 "available_chillers 候选值必须为不超过来源声明台数的非负整数。")
    return [{"id": "reference:conditional-capacity", "label": "重算条件负荷、可用账面制冷与余量",
             "object_ref": f"reference:{data['snapshot']['id']}", "inputs": [item["input_id"] for item in changes],
             "checks": ["reference:reported-total", "reference:conditional-load", "reference:available-capacity", "reference:capacity-margin"],
             "evidence": _evidence(data, FACTS, INPUTS, requirement=True),
             "message": "依照冻结源配置、显式假设与不低于 0 kW 的余量要求重算；项目输入变化使旧证据失效。"},
            {"id": "reference:unverified-performance", "label": "实际性能与同时运行条件仍未确认",
             "object_ref": f"reference:{data['snapshot']['id']}", "coverage": "unregistered",
             "message": "不推断原 generic SDL 布局对应关系，不表示真实性能求解或现场数据已验证。"}]

  def evaluate(self, root: Path):
    base = {"solver_execution": "not-run", "analysis_method": "0D conditional capacity arithmetic",
            "verification": "not-run", "fidelity": "declared-configuration-with-assumptions", "targets": [],
            "limitations": list(LIMITATIONS), "source_content_verified": False,
            "scope": "冻结验收报告声明配置的条件容量算术审查；不形成真实工程接受。"}
    unverified = {"id": "reference:field-verification", "label": "实际性能、真实同时运行与独立现场数据",
                  "object_ref": "reference:unverified-field-performance", "status": "indeterminate",
                  "message": "上述条件始终未确认；声明配置的算术关系不能替代性能实测或真实工程签核。"}
    try:
      data = _load(root)
      fact, assumed = data["facts"], data["assumptions"]
      installed = fact["chiller_count"] * fact["per_chiller_capacity_kw"]
      capability = fact["air_load_count"] * fact["per_air_load_kw"] + fact["liquid_load_count"] * fact["per_liquid_load_kw"]
      demand = fact["air_load_count"] * fact["per_air_load_kw"] * assumed["air_load_fraction"] + fact["liquid_load_count"] * fact["per_liquid_load_kw"] * assumed["liquid_load_fraction"]
      available = assumed["available_chillers"] * fact["per_chiller_capacity_kw"]
      margin = available - demand
      _require(all(_number(value) for value in (installed, capability, demand, available, margin)), "容量算术结果超出有限数值范围。")
    except (ReferenceInputError, OSError, ValueError, TypeError) as error:
      code = error.code if isinstance(error, ReferenceInputError) else "REFERENCE_INPUT_INVALID"
      return {**base, "arithmetic_execution": "not-run", "assessment": "indeterminate", "extraction": [],
              "diagnostics": [{"code": code, "message": str(error)}], "checks": [
                {"id": "reference:snapshot", "label": "冻结来源快照与输入可用性", "status": "indeterminate",
                 "object_ref": "reference:unresolved-snapshot", "message": str(error)}, unverified]}
    identity = f"reference:{data['snapshot']['id']}"
    load_facts = ("air_load_count", "per_air_load_kw", "liquid_load_count", "per_liquid_load_kw")
    evidence = {
      "capability": _evidence(data, load_facts),
      "demand": _evidence(data, load_facts, ("air_load_fraction", "liquid_load_fraction")),
      "available": _evidence(data, ("chiller_count", "per_chiller_capacity_kw"), ("available_chillers",)),
      "margin": _evidence(data, FACTS, INPUTS, requirement=True),
    }
    checks = [
      {"id": "reference:snapshot", "label": "冻结来源快照可用性", "status": "pass", "object_ref": identity,
       "message": "全文件摘要及必需来源登记一致；仅验证冻结快照关系，没有核验原 PDF 内容。", "evidence": _evidence(data, FACTS)},
      {"id": "reference:reported-total", "label": "声明冷机台数与单机制冷能力合计", "object_ref": identity,
       "status": "pass" if math.isclose(installed, fact["reported_total_capacity_kw"], rel_tol=1e-9, abs_tol=1e-9) else "fail",
       "actual": installed, "limit": fact["reported_total_capacity_kw"], "unit": "kW", "operator": "==",
       "message": "核对源内台数×单机声明能力与报告声明总能力，不是独立性能验证。",
       "evidence": _evidence(data, ("chiller_count", "per_chiller_capacity_kw", "reported_total_capacity_kw"))},
      {"id": "reference:conditional-load", "label": "假设条件下的负荷能力合计", "object_ref": identity, "status": "pass",
       "actual": demand, "unit": "kW", "message": "风冷能力合计×风冷比例 + 液冷能力合计×液冷比例；不是实际负荷测量。", "evidence": evidence["demand"]},
      {"id": "reference:available-capacity", "label": "假设可用台数下的账面制冷能力", "object_ref": identity, "status": "pass",
       "actual": available, "unit": "kW", "message": "假设可用台数×单机声明能力；没有性能降额或运行实测。", "evidence": evidence["available"]},
      {"id": "reference:capacity-margin", "label": "条件容量余量", "object_ref": identity,
       "status": "pass" if margin >= data["minimum"] else "fail", "actual": margin, "limit": data["minimum"], "unit": "kW", "operator": ">=",
       "message": "可用账面制冷能力减去条件负荷能力合计；数值满足也不能消除未确认的工程条件。", "evidence": evidence["margin"]},
      {**unverified, "object_ref": identity, "evidence": {"unresolved": data["snapshot"]["unresolved"]}},
    ]
    extraction = [{"id": f"reference:{key}", "label": label, "object_ref": identity, "value": value, "unit": "kW",
                   "state": "assumed", "source": evidence[source]}
                  for key, label, value, source in (
                    ("declared-load-capability", "声明负载设备能力合计（非同时运行承诺）", capability, "capability"),
                    ("conditional-load", "条件负荷能力合计", demand, "demand"),
                    ("available-capacity", "可用账面制冷能力", available, "available"),
                    ("capacity-margin", "条件容量余量", margin, "margin"))]
    return {**base, "arithmetic_execution": "completed", "assessment": "fail" if any(item["status"] == "fail" for item in checks) else "indeterminate",
            "checks": checks, "extraction": extraction, "diagnostics": [],
            "reference_snapshot": {"id": data["snapshot"]["id"], "path": data["snapshot_path"], "digest": data["snapshot_digest"],
                                   "content": data["snapshot"]}, "study_scope": data["study"].get("scope")}
