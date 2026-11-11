"""HVAC root-network projection composed with neutral drawing and ELK contracts."""

import json
import math
from pathlib import Path

from texsys.evidence import file_digest, write_json
from texsys.packages import TexsysPackage
from texsys.project import inspect_project
from texsys.targets import Artifact, BuildContext, TargetDefinition, TargetResult
from texsys_diagram import DiagramContract, compose, digest, render_drawio
from texsys_diagram_elk import BackendUnavailable
from texsys_hvac_graphics import prepare_layout, render
from texsys_hvac_dynamics import has_physics
from texsys_hvac_dynamics.domain import PhysicsUnresolved, compile_model
from texsys_hvac_dynamics.inputs import physical_projection_inputs, projection_cacheable

def symbols(request: dict) -> dict:
  """Geometry and compatible family resolution belong to the TypeScript catalog."""
  return {"requested": request}


def project(model: dict, source: str, profile: str) -> dict:
  sdl, resources = model["sdl"], model["resources"]
  root_name = sdl["root"]
  component = sdl["components"][root_name]
  if component.get("ports"):
    raise ValueError("Root boundary ports require an explicit boundary projection; this slice supports closed root networks.")
  nodes, edges, source_map, missing, failures = [], [], {}, [root_name] if model.get("unresolved_physics") else [], []
  for name, instance in sorted(component.get("instances", {}).items()):
    family = instance["component"]
    definition = sdl["components"][family]
    if definition.get("instances"):
      raise ValueError(f"Nested subsystem {name} requires an explicit expansion/boundary contract; not supported by this slice.")
    reference = f"{root_name}.{name}"
    identifier = f"node:{reference}"
    behavior = definition.get("behavior")
    parameters = {}
    if behavior is None:
      missing.append(reference)
    else:
      parameters = {**behavior["parameters"], **instance.get("parameters", {})}
      resource = resources.get(behavior["reference"])
      if behavior["state"] == "failed" or any(v["state"] == "failed" for v in parameters.values()):
        failures.append(reference)
      if (behavior["state"] not in {"known", "assumed"} or resource is None or
          any(v["state"] not in {"known", "assumed", "not-applicable"} for v in parameters.values())):
        missing.append(reference)
    ports = []
    signatures = {}
    for port_name, port in sorted(definition.get("ports", {}).items()):
      port_id = f"port:{reference}.{port_name}"
      signatures[port_name] = {k: port[k] for k in ("kind", "direction")}
      ports.append({"id": port_id, "semantic_ref": f"{reference}.{port_name}", "name": port_name,
                    **signatures[port_name], "side": "", "x": 0, "y": 0})
      source_map[port_id] = {"file": source, "path": f"components.{family}.ports.{port_name}"}
    nodes.append({"id": identifier, "semantic_ref": reference, "component": family,
                  "label": name, "ports": ports, "width": 0, "height": 0,
                  "behavior": behavior,
                  "parameters": parameters, "page": "page:1",
                  "symbol_request": {"semantic_ref": reference, "family": family, "model": family,
                                     "graphic_kind": resources.get(behavior["reference"], {}).get("kind") if behavior else None,
                                     "configuration_digest": digest(parameters), "ports": signatures,
                                     "view": "hvac:system-schematic"}})
    source_map[identifier] = {"file": source, "path": f"components.{root_name}.instances.{name}"}
  if not nodes:
    raise ValueError("System schematic requires at least one root instance.")
  connections = component.get("connections", [])
  edge_ids = {}
  seen = set()
  for index, connection in enumerate(connections):
    pair = (connection["from"], connection["to"])
    if pair in seen:
      raise ValueError(f"Duplicate connection has no independent semantic identity: {pair}")
    seen.add(pair)
    source_instance, source_port = pair[0].split(".")
    target_instance, target_port = pair[1].split(".")
    a = sdl["components"][component["instances"][source_instance]["component"]]["ports"][source_port]
    b = sdl["components"][component["instances"][target_instance]["component"]]["ports"][target_port]
    semantic_ref = f"{root_name}:{pair[0]}~{pair[1]}"
    identifier = f"edge:{semantic_ref}"
    if "id" in connection:
      edge_ids[connection["id"]] = identifier
    edges.append({"id": identifier, "semantic_ref": semantic_ref, "source": f"port:{root_name}.{pair[0]}",
                  "target": f"port:{root_name}.{pair[1]}", "medium": a["kind"],
                  "directed": a["direction"] == "out" and b["direction"] == "in", "page": "page:1"})
    source_map[identifier] = {"file": source, "path": f"components.{root_name}.connections[{index}]"}
  edges.sort(key=lambda e: e["id"])
  document = {"schema_version": 1, "target": "hvac:system-schematic", "profile": profile,
          "view": model["view"], "source": source, "model_digest": model.get("physical_model_digest") or digest(sdl),
          "title": f"{root_name} · 暖通" + ("系统图" if missing or model["view"] == "system" else "原理图"),
          "deliverable": "system-diagram" if missing or model["view"] == "system" else "schematic",
          "behavior_state": "failed" if failures else "missing" if missing else "defined",
          "spatial_state": "connectivity-only", "missing_behavior": sorted(missing), "failed_behavior": sorted(failures),
          "spatial_basis": "No SDL Placement in the current JSON subset; drawing coordinates are nonphysical.",
          "nodes": nodes, "edges": edges, "groups": [], "annotations": [], "source_map": source_map,
          "media": {kind: {"chilled-water": "#1769aa", "cooling-water": "#b45309", "air": "#087f5b"}.get(kind, "#475569")
                    for kind in sorted({p["kind"] for n in nodes for p in n["ports"]})},
          "behavior_resources": resources,
          "view_contract": {"boundary": root_name, "expansion": "root-leaf-instances", "filter": "all",
                            "pages": "single", "physical_annotations": False, "behavior_annotations": not missing and model["view"] == "schematic"},
          "expected_projection": {"nodes": sorted(n["semantic_ref"] for n in nodes),
                                  "ports": sorted((p["id"], p["semantic_ref"], p["kind"], p["direction"])
                                                  for n in nodes for p in n["ports"]),
                                  "edges": sorted((e["semantic_ref"], e["source"], e["target"]) for e in edges)}}
  if "nets" in component:
    networks = []
    for name, net in sorted(component["nets"].items()):
      reference = f"{root_name}.{name}"
      identifier = f"net:{reference}"
      networks.append({"id": identifier, "semantic_ref": reference, "kind": net["kind"],
                       "edges": sorted(edge_ids[member] for member in net["connections"])})
      source_map[identifier] = {"file": source, "path": f"components.{root_name}.nets.{name}"}
    document["nets"] = networks
  return document


def _asset(root: Path, relative: str, allowed: tuple[Path, ...]) -> Path:
  if not isinstance(relative, str):
    raise ValueError("Asset path must be a string.")
  path = (root / relative).resolve()
  if not any(path.is_relative_to(base.resolve()) for base in allowed):
    raise ValueError(f"Asset must be inside the project source/assets directories: {relative}")
  return path


class HvacDiagramPackage(TexsysPackage):
  name = "hvac-diagram"
  version = "0.1.0"

  def targets(self):
    return (TargetDefinition("hvac:system-schematic", self.name, self.version, self.build_schematic,
                             profiles=("review", "engineering"), default_profile="review",
                             default_scope={"view": "schematic"}, input_patterns=physical_projection_inputs,
                             cache=projection_cacheable),
            TargetDefinition("hvac:system-topology", self.name, self.version, self.build_schematic,
                             profiles=("review", "engineering"), default_profile="review",
                             default_scope={"view": "system"}, input_patterns=physical_projection_inputs,
                             cache=projection_cacheable))

  def build_schematic(self, context: BuildContext) -> TargetResult:
    info = inspect_project(context.root)
    source = context.root / info.source
    assets = context.root / info.assets
    path = source / "system.sdl.json"
    if set(context.scope) - {"view", "overlay", "approval", "results"}:
      return TargetResult("fail", diagnostics=({"rule": "D_SCOPE", "status": "fail", "message": "Unknown schematic scope fields."},))
    view = "system" if context.target == "hvac:system-topology" else context.scope.get("view", "schematic")
    if view not in {"system", "schematic"}:
      return TargetResult("fail", diagnostics=({"rule": "D_SCOPE", "status": "fail", "message": "view must be system or schematic."},))
    if not path.is_file():
      return TargetResult("indeterminate", open_items=({"id": "missing-model", "impact": "Requires source/system.sdl.json."},))
    sdl = json.loads(path.read_text(encoding="utf-8"))
    resources = {}
    root_instances = sdl["components"][sdl["root"]].get("instances", {})
    used_definitions = sorted({i["component"] for i in root_instances.values()})
    for component_name in used_definitions:
      component = sdl["components"][component_name]
      behavior = component.get("behavior")
      if behavior:
        reference = behavior["reference"]
        asset = _asset(context.root, reference, (source, assets))
        if asset.is_file():
          resources[reference] = {"path": reference, "digest": file_digest(asset)}
          if asset.suffix == ".json":
            descriptor = json.loads(asset.read_text(encoding="utf-8"))
            if isinstance(descriptor, dict) and isinstance(descriptor.get("kind"), str):
              resources[reference]["kind"] = descriptor["kind"]
    overlay = None
    if "overlay" in context.scope:
      overlay = json.loads(_asset(context.root, context.scope["overlay"], (assets,)).read_text(encoding="utf-8"))
    physical_digest = None
    unresolved_physics = None
    if has_physics(context.root):
      try:
        physical_digest = compile_model(context.root).snapshot_digest
      except PhysicsUnresolved as error:
        unresolved_physics = str(error)
    try:
      document, diagnostics = compose({"sdl": sdl, "resources": resources, "view": view, "physical_model_digest": physical_digest, "unresolved_physics": unresolved_physics},
                                      path.relative_to(context.root).as_posix(), context.profile,
                                      DiagramContract(project, symbols, prepare_layout), overlay)
    except BackendUnavailable as error:
      return TargetResult("indeterminate", open_items=({"id": "diagram-backend", "impact": str(error)},))
    open_items = []
    if document["missing_behavior"]:
      diagnostics.append({"rule": "D_BEHAVIOR", "status": "indeterminate", "message": "Missing/unresolved behavior; downgraded to system diagram.", "objects": document["missing_behavior"]})
    if document["failed_behavior"]:
      diagnostics.append({"rule": "D_BEHAVIOR_FAILED", "status": "fail", "message": "Behavior or parameter has explicit failed state.", "objects": document["failed_behavior"]})
    fallbacks = [{"id": n["id"], "level": n["symbol"]["level"], "reason": n["symbol"]["fallback_reason"]} for n in document["nodes"] if not n["symbol"]["presentation_exact"]]
    results = []
    consumed = []
    result_paths = context.scope.get("results", [])
    if not isinstance(result_paths, list) or not all(isinstance(item, str) for item in result_paths):
      raise ValueError("scope.results must be an array of fixed scenario result paths.")

    def finite(value):
      return type(value) in {int, float} and math.isfinite(value)

    for relative in result_paths:
      result_path = _asset(context.root, relative, (context.build_path,))
      result = json.loads(result_path.read_text(encoding="utf-8"))
      if physical_digest is None or result.get("model_digest") != physical_digest:
        raise ValueError(f"Scenario result belongs to a different or unresolved design snapshot: {relative}")
      scenario_source = result.get("scenario_source", {})
      scenario_path = _asset(context.root, scenario_source.get("path"), (assets,))
      if not scenario_path.is_file() or file_digest(scenario_path) != scenario_source.get("digest"):
        raise ValueError(f"Scenario result is stale: {relative}")
      times = result.get("time_s", [])
      if not isinstance(times, list) or not times or not all(finite(t) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError(f"Scenario result has no valid time series: {relative}")
      variables = result.get("variables")
      if not isinstance(variables, dict) or not variables or any(not isinstance(value, dict) or not isinstance(value.get("values"), list) or len(value["values"]) != len(times) or not all(finite(v) for v in value["values"]) for value in variables.values()):
        raise ValueError(f"Scenario result has inconsistent variable arrays: {relative}")
      results.append(result)
      consumed.append({"path": relative, "digest": file_digest(result_path), "scenario": result.get("scenario"), "model_digest": physical_digest})
    document["scenario_results"] = consumed
    # Profile changes closure policy, not drawing geometry/semantics. A review
    # approval can therefore be used for the same engineering projection.
    review_digest = digest({k: v for k, v in document.items() if k != "profile"})
    visual_review = {"status": "unreviewed", "diagram_digest": review_digest}
    if "approval" in context.scope:
      approval_path = _asset(context.root, context.scope["approval"], (assets,))
      approval = json.loads(approval_path.read_text(encoding="utf-8"))
      if (isinstance(approval, dict) and approval.get("schema_version") == 1 and
          approval.get("diagram_digest") == review_digest and approval.get("decision") == "approved" and
          all(isinstance(approval.get(k), str) and approval[k].strip() for k in ("reviewer", "source"))):
        visual_review.update(status="approved", reviewer=approval["reviewer"], source=approval["source"], approval_digest=file_digest(approval_path))
      else:
        diagnostics.append({"rule": "D_APPROVAL", "status": "fail", "message": "Invalid/stale visual approval; must bind this exact diagram digest."})
    strict = context.profile == "engineering"
    if visual_review["status"] != "approved":
      open_items.append({"id": "visual-review", "impact": "Human visual review has not been recorded; engineering closure is blocked."})
    status = "fail" if any(d["status"] == "fail" for d in diagnostics) else "indeterminate" if strict and (document["missing_behavior"] or open_items) else "pass"
    check = {"schema_version": 1, "status": status, "profile": context.profile, "view": view,
             "deliverable": document["deliverable"], "behavior_state": document["behavior_state"],
             "spatial_state": document["spatial_state"], "diagnostics": diagnostics, "fallbacks": fallbacks,
             "visual_review": visual_review, "open_items": open_items,
             "closure": "review-draft" if not strict else "engineering-diagram",
             "limits": ["No physical layout or construction acceptance.", "Behavior references/bindings are checked; model equations are not solved by this target.", "One page, flat root leaf instances; no folding or cross-page projection.", "Host font glyphs and draw.io editing require visual review."]}
    output = context.build_path / "hvac" / ("topology" if context.target == "hvac:system-topology" else view)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "system.json", document)
    write_json(output / "system-check.json", check)
    rendered = render(document, results)
    (output / "system.svg").write_text(rendered["svg"], encoding="utf-8")
    (output / "system.html").write_text(rendered["html"], encoding="utf-8")
    (output / "system.drawio").write_text(render_drawio(document), encoding="utf-8")
    metadata = {"kind": "topology" if view == "system" else "schematic", "title": document["title"],
                "root": sdl["root"], "source_map": document["source_map"]}
    complete = not (view == "schematic" and document["missing_behavior"])
    return TargetResult(status, tuple(Artifact(identifier, (output / filename).relative_to(context.root).as_posix(), kind,
                                      complete=complete if identifier != "system-check" else True,
                                      preview=metadata if identifier == "system-data" else
                                      {**metadata, "kind": "report", "title": document["title"] + " · 检查"} if identifier == "system-check" else None)
                                     for identifier, filename, kind in (("system-data", "system.json", "authoritative"),
                                       ("system-svg", "system.svg", "authoritative"), ("system-viewer", "system.html", "diagnostic"), ("system-drawio", "system.drawio", "authoritative"),
                                       ("system-check", "system-check.json", "diagnostic"))),
                        tuple(diagnostics), tuple(open_items), {"deliverable": document["deliverable"], "behavior_state": document["behavior_state"],
                        "spatial_state": document["spatial_state"], "backend": document["backend"], "review_digest": review_digest,
                        "diagram_contract": "1", "visual_review": visual_review["status"]})


package = HvacDiagramPackage()
