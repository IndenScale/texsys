"""Design-basis checks against an assembled reference design.

Each check cites the GB 50174-2017 clause recorded in the project design basis.
Parameters are conceptual (assumed); passing checks do not constitute engineering
acceptance of a real project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from texsys.formal import resolve_sdl_imports
from texsys.library import library_entries
from texsys.sdl_catalog import SdlCatalog, read_sdl_catalog


@dataclass
class Instance:
  name: str
  family: str
  parameters: dict[str, Any]
  specification: dict[str, Any]
  ports: dict[str, Any]
  definition: dict[str, Any]
  document: str
  children: dict[str, "Instance"] = field(default_factory=dict)


@dataclass
class Design:
  basis: dict[str, Any]
  placement: dict[str, Any]
  instances: dict[str, Instance]
  connections: list[dict[str, Any]]
  nets: dict[str, Any]
  diagnostics: list[dict[str, Any]]


def _module_specification(catalog: SdlCatalog, document: str) -> dict[str, Any]:
  manifest = catalog.modules.get(document)
  if not manifest:
    return {}
  assets = manifest.get("assets", {})
  specification = assets.get("specification", {})
  path = specification.get("path")
  if not path:
    return {}
  target = catalog.files[document].parent / path
  if not target.is_file():
    return {}
  return json.loads(target.read_text(encoding="utf-8"))


def _resolve_instances(catalog: SdlCatalog, document: str, definition: dict[str, Any],
                       instances: dict[str, Any]) -> dict[str, Instance]:
  entries = library_entries(set(catalog.documents))
  imports, _ = resolve_sdl_imports(catalog.documents, entries, document)
  source_doc = catalog.documents[document]
  resolved: dict[str, Instance] = {}
  for name, binding in instances.items():
    component = binding["component"]
    if component in imports:
      target, root_name = imports[component]
      target_doc = catalog.documents[target]
      child_def = target_doc["components"][root_name]
      family = Path(target).parent.name
      specification = _module_specification(catalog, target)
      child_document = target
    else:
      child_def = source_doc["components"].get(component, {})
      family = component
      specification = {}
      child_document = document
    parameters = dict(child_def.get("behavior", {}).get("parameters", {}))
    parameters.update(binding.get("parameters", {}))
    instance = Instance(
      name=name, family=family, parameters=parameters, specification=specification,
      ports=child_def.get("ports", {}), definition=child_def, document=child_document)
    if "instances" in child_def:
      instance.children = _resolve_instances(catalog, child_document, child_def, child_def["instances"])
    resolved[name] = instance
  return resolved


def load_design(source: Path, root: Path) -> Design:
  catalog = read_sdl_catalog(source, root)
  diagnostics = [{"rule": d.rule, "status": d.status, "file": d.file, "message": d.message}
                 for d in catalog.diagnostics]
  document = "system.sdl.json"
  if document not in catalog.documents:
    raise ValueError("Reference design requires src/system.sdl.json.")
  data = catalog.documents[document]
  root_name = data["root"]
  composite = data["components"][root_name]
  instances = _resolve_instances(catalog, document, composite, composite.get("instances", {}))
  basis = json.loads((source / "design-basis.json").read_text(encoding="utf-8"))
  placement_path = source / "placement.study.json"
  placement = json.loads(placement_path.read_text(encoding="utf-8")) if placement_path.is_file() else {}
  return Design(basis=basis, placement=placement, instances=instances,
                connections=composite.get("connections", []), nets=composite.get("nets", {}),
                diagnostics=diagnostics)


def _param(instance: Instance, name: str, default: float = 0.0) -> float:
  binding = instance.parameters.get(name)
  if isinstance(binding, dict):
    return float(binding.get("value", default))
  return default


def _leaves(instances: dict[str, Instance]) -> list[Instance]:
  result: list[Instance] = []
  for instance in instances.values():
    if instance.children:
      result.extend(_leaves(instance.children))
    else:
      result.append(instance)
  return result


def _all(instances: dict[str, Instance]) -> list[Instance]:
  result: list[Instance] = []
  for instance in instances.values():
    result.append(instance)
    result.extend(_all(instance.children))
  return result


def _upstream_sources(design: Design, start: str, port: str) -> set[str]:
  """Trace ac-power upstream across composite boundaries to source families."""
  source_families = {"utility-feed", "diesel-generator", "generator-container"}
  sources: set[str] = set()
  seen: set[tuple[int, str]] = set()

  def walk(instances: dict[str, Instance], connections: list[dict[str, Any]],
           node: str, top: str, parent: tuple | None) -> None:
    key = (id(connections), node)
    if key in seen:
      return
    seen.add(key)
    instance_name, port_name = node.split(".", 1)
    instance = instances.get(instance_name)
    if instance is None:
      return
    root_name = instance_name if parent is None else top
    if instance.family in source_families:
      sources.add(root_name)
    for connection in connections:
      if connection["to"] == node:
        walk(instances, connections, connection["from"], root_name, parent)
    boundary = instance.specification.get("boundary", {})
    if instance.children and port_name in boundary:
      for internal in boundary[port_name]:
        walk(instance.children, instance.definition.get("connections", []),
             internal, root_name, (instances, connections, instance_name, parent))
    if parent is not None:
      parent_instances, parent_connections, parent_name, grandparent = parent
      parent_instance = parent_instances[parent_name]
      for external, targets in parent_instance.specification.get("boundary", {}).items():
        if node in targets:
          walk(parent_instances, parent_connections, f"{parent_name}.{external}", root_name, grandparent)
    port = instance.ports.get(port_name, {})
    if not instance.children and port.get("direction") == "out":
      for name, definition in instance.ports.items():
        if definition.get("direction") == "in" and definition.get("kind") == port.get("kind"):
          walk(instances, connections, f"{instance_name}.{name}", root_name, parent)

  walk(design.instances, design.connections, f"{start}.{port}", start, None)
  return sources


def evaluate_design_checks(source: Path, root: Path) -> dict[str, Any]:
  design = load_design(source, root)
  basis = design.basis
  requirements = {r["id"]: r for r in basis.get("requirements", [])}
  scale = basis.get("scale", {})
  it_load = float(scale.get("it_load_kw", 0))
  checks: list[dict[str, Any]] = []

  def add(check_id: str, status: str, message: str, **evidence: Any) -> None:
    requirement = requirements.get(check_id, {})
    checks.append({
      "id": check_id, "clause": requirement.get("clause", ""),
      "title": requirement.get("title", check_id), "status": status,
      "message": message, "evidence": evidence,
    })

  containers = [i for i in design.instances.values() if i.family == "it-container"]
  power_paths = [i for i in design.instances.values() if i.family == "power-container"]
  gensets = [i for i in design.instances.values() if i.family == "generator-container"]
  frames = [i for i in design.instances.values() if i.family == "steel-frame-bay"]
  walls = [i for i in design.instances.values() if i.family == "fire-partition-wall"]
  utilities = [i for i in design.instances.values() if i.family == "utility-feed"]
  facility_kw = float(design.placement.get("facility_load_kw", it_load * 1.25))
  power_factor = 0.9

  # GB-3.2.1 / GB-8.1.7: 2N power, each path carries full IT load at 0.9 pf,
  # battery energy covers the declared autonomy at full IT load.
  path_kw = [round(_param(p, "rated_kva") * power_factor, 1) for p in power_paths]
  connected = all(
    any(link["to"] == f"{container.name}.power_a" for link in design.connections)
    and any(link["to"] == f"{container.name}.power_b" for link in design.connections)
    for container in containers)
  independent = True
  for container in containers:
    a = _upstream_sources(design, container.name, "power_a")
    b = _upstream_sources(design, container.name, "power_b")
    if not a or not b or a & b:
      independent = False
  capacity_ok = len(path_kw) >= 2 and all(kw >= it_load for kw in path_kw)
  autonomy_min = float(requirements.get("GB-8.1.7", {}).get("parameters", {}).get("battery_autonomy_min", 15))
  battery_kwh = [sum(_param(b, "energy_kwh") for b in p.children.values()
                     if b.family == "battery-cabinet") for p in power_paths]
  autonomy = [round(kwh / it_load * 60, 1) if it_load else 0.0 for kwh in battery_kwh]
  battery_ok = bool(autonomy) and all(a >= autonomy_min for a in autonomy)
  ok = connected and independent and capacity_ok
  add("GB-3.2.1", "pass" if ok else "fail",
      f"{len(power_paths)} 路配电，单路 0.9 功率因数可用 {path_kw} kW，IT 负荷 {it_load} kW；"
      f"双路连接={'是' if connected else '否'}，上游独立={'是' if independent else '否'}。",
      path_capacity_kw=path_kw, it_load_kw=it_load, dual_connected=connected, independent=independent)
  add("GB-8.1.7", "pass" if capacity_ok and battery_ok and len(power_paths) >= 2 else "fail",
      f"UPS 2N：每路可用 {path_kw} kW；电池 {battery_kwh} kWh，后备 {autonomy} min ≥ {autonomy_min} min。"
      if capacity_ok and battery_ok else "UPS 容量、路数或电池后备不足。",
      redundancy="2N", paths=len(power_paths), battery_kwh=battery_kwh,
      autonomy_min=autonomy, required_autonomy_min=autonomy_min)

  # GB-8.1.1: two independent utility feeds.
  ok = len(utilities) >= 2
  add("GB-8.1.1", "pass" if ok else "fail",
      f"市电进线 {len(utilities)} 路。", utility_count=len(utilities))

  # GB-7.5.2: cooling N+1 with capacity factor.
  factor = float(requirements.get("GB-7.5.2", {}).get("parameters", {}).get("capacity_factor", 1.2))
  chillers = [i for i in _all(design.instances) if i.family == "water-cooled-chiller"]
  unit_kw = max((_param(c, "cooling_kw") for c in chillers), default=0.0)
  n = len(chillers)
  available = (n - 1) * unit_kw if n >= 2 else 0.0
  required = it_load * factor
  ok = n >= 2 and available >= required
  add("GB-7.5.2", "pass" if ok else "fail",
      f"冷水机组 {n} 台 × {unit_kw} kW，N+1 可用 {available} kW ≥ 需求 {required} kW。"
      if ok else f"冷却容量不足：N+1 可用 {available} kW < 需求 {required} kW。",
      chiller_count=n, unit_kw=unit_kw, available_kw=available, required_kw=required)

  # GB-8.1.15: fuel storage hours at design load, N+1 gensets running.
  min_hours = float(requirements.get("GB-8.1.15", {}).get("parameters", {}).get("min_fuel_hours", 12))
  genset_leaves = [i for i in _all(design.instances) if i.family == "diesel-generator"]
  prime_kw = max((_param(g, "prime_kw") for g in genset_leaves), default=0.0)
  running = max(len(genset_leaves) - 1, 1)
  fraction = min(facility_kw / (running * prime_kw), 1.0) if prime_kw else 0.0
  consumption = sum(_param(g, "fuel_consumption_lph") for g in genset_leaves[:running]) \
      * (0.3 + 0.7 * fraction)
  storage_l = sum(_param(g, "fuel_storage_l") for g in gensets)
  hours = round(storage_l / consumption, 1) if consumption else 0.0
  ok = bool(gensets) and hours >= min_hours
  add("GB-8.1.15", "pass" if ok else "fail",
      f"储油 {storage_l} L，N+1 运行 {running} 台、负荷率 {fraction:.2f}，"
      f"油耗约 {consumption:.0f} L/h，可供 {hours} h ≥ {min_hours} h。"
      if ok else f"储油时长 {hours} h 不足 {min_hours} h。",
      fuel_storage_l=storage_l, consumption_lph=round(consumption, 1),
      fuel_hours=hours, required_hours=min_hours)

  # GB-13.2.4: fire separation walls and slab.
  min_wall = float(requirements.get("GB-13.2.4", {}).get("parameters", {}).get("min_wall_fire_hours", 2.0))
  min_slab = float(requirements.get("GB-13.2.4", {}).get("parameters", {}).get("min_slab_fire_hours", 1.5))
  wall_hours = [_param(w, "fire_resistance_hours") for w in walls]
  slab_hours = float(design.placement.get("slab_fire_resistance_hours", 0))
  ok = bool(walls) and all(h >= min_wall for h in wall_hours) and slab_hours >= min_slab
  add("GB-13.2.4", "pass" if ok else "fail",
      f"防火隔墙 {len(walls)} 道，耐火 {wall_hours} h；楼板 {slab_hours} h。",
      wall_hours=wall_hours, slab_hours=slab_hours, min_wall_hours=min_wall, min_slab_hours=min_slab)

  # GB-13.2.1: building fire resistance grade (grade number must not exceed 2).
  max_grade = float(requirements.get("GB-13.2.1", {}).get("parameters", {}).get("max_fire_resistance_grade", 2))
  grades = [_param(i, "fire_resistance_grade",
                   float(i.specification.get("properties", {}).get("fire", {}).get("fire_resistance_grade", 0)))
            for i in frames]
  ok = bool(frames) and all(0 < g <= max_grade for g in grades)
  add("GB-13.2.1", "pass" if ok else "fail",
      f"钢框架耐火等级 {grades}，不低于二级（级数 ≤ {max_grade:g}）。" if ok else "钢框架耐火等级低于二级或缺失。",
      fire_resistance_grades=grades, max_grade=max_grade)

  # GB-6.1.3: clear height.
  min_height = float(requirements.get("GB-6.1.3", {}).get("parameters", {}).get("min_clear_height_m", 3.0))
  heights = [_param(f, "clear_height_m") for f in frames]
  ok = bool(frames) and all(h >= min_height for h in heights)
  add("GB-6.1.3", "pass" if ok else "fail",
      f"框架净高 {heights} m ≥ {min_height} m。" if ok else "净高不足。",
      clear_heights_m=heights)

  # GB-13.1.2: suppression in every IT container.
  suppressed = all(
    c.specification.get("properties", {}).get("fire", {}).get("suppression")
    for c in containers)
  ok = bool(containers) and suppressed
  add("GB-13.1.2", "pass" if ok else "fail",
      f"{len(containers)} 个 IT 方舱均设气体灭火。" if ok else "存在未设灭火的 IT 方舱。",
      container_count=len(containers))

  # GB-5.1.1: inlet temperature band.
  params = requirements.get("GB-5.1.1", {}).get("parameters", {})
  design_inlet = float(params.get("design_inlet_c", 0))
  ok = float(params.get("inlet_min_c", 18)) <= design_inlet <= float(params.get("inlet_max_c", 27))
  add("GB-5.1.1", "pass" if ok else "fail",
      f"设计进风温度 {design_inlet} °C 在 18–27 °C 带内。" if ok else "进风温度越限。",
      design_inlet_c=design_inlet)

  # GB-4.2.1: functional zones cover all top-level instances.
  zones = design.placement.get("zones", {})
  assigned = {name: zone for zone, members in zones.items() for name in members}
  unassigned = [name for name in design.instances if name not in assigned]
  host_ok = all(assigned.get(c.name) == "host" for c in containers)
  ok = not unassigned and host_ok
  add("GB-4.2.1", "pass" if ok else "fail",
      f"功能分区覆盖 {len(assigned)}/{len(design.instances)} 个实例；IT 方舱均在主机房区。"
      if ok else f"未分区实例：{unassigned}；IT 方舱分区正确={host_ok}。",
      unassigned=unassigned)

  # GB-A.power: transformers and N+1 gensets cover the declared facility load.
  transformers = [i for i in _all(design.instances) if i.family == "dry-type-transformer"]
  tx_kva = [_param(t, "rated_kva") for t in transformers]
  tx_ok = len(tx_kva) >= 2 and all(kva * power_factor >= facility_kw for kva in tx_kva)
  genset_ok = bool(genset_leaves) and running * prime_kw >= facility_kw
  ok = tx_ok and genset_ok
  add("GB-A.power", "pass" if ok else "fail",
      f"变压器 {tx_kva} kVA（0.9 功率因数可用 {[round(k * power_factor) for k in tx_kva]} kW）；"
      f"发电机 N+1 可用 {running * prime_kw:.0f} kW；设施负荷 {facility_kw} kW。",
      transformer_kva=tx_kva, genset_available_kw=running * prime_kw,
      facility_load_kw=facility_kw, facility_load_basis=design.placement.get("facility_load_breakdown", {}))

  failed = [c for c in checks if c["status"] != "pass"]
  return {
    "schema_version": 1,
    "status": "fail" if failed or design.diagnostics else "pass",
    "grade": basis.get("grade", {}).get("value"),
    "standard": basis.get("standard", {}).get("name"),
    "checks": checks,
    "assembly_diagnostics": design.diagnostics,
    "fidelity": "concept",
    "boundary": "脱敏概念参数；检查通过不构成真实工程接受。",
  }
