"""Discipline-neutral diagram contracts, validation and deterministic exports."""

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Protocol
from urllib.parse import quote
import xml.etree.ElementTree as ET

FONT = "Arial"


def _color(document: dict, medium: str) -> str:
  return document.get("media", {}).get(medium, "#475569")


class DisciplineProjection(Protocol):
  def __call__(self, model: dict, source: str, profile: str) -> dict: ...


class SymbolCatalog(Protocol):
  def __call__(self, request: dict) -> dict: ...


class LayoutEngine(Protocol):
  def __call__(self, document: dict) -> dict: ...


class DiagramRenderer(Protocol):
  def __call__(self, document: dict) -> str: ...


@dataclass(frozen=True)
class DiagramContract:
  projection: DisciplineProjection
  symbols: SymbolCatalog
  layout: LayoutEngine


def digest(value: object) -> str:
  return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                             allow_nan=False).encode()).hexdigest()


def resolve_symbol(request: dict, catalog: dict, version: str) -> dict:
  """Finite resolution chain; every candidate must preserve the full port signature."""
  ports = request["ports"]
  rejected = []
  candidates = [
    ("explicit", request.get("explicit")),
    ("model", f"model:{request.get('model')}:{request.get('configuration_digest')}"),
    ("family", f"family:{request.get('family')}"),
    ("discipline", "discipline:generic"),
  ]
  selected = None
  level = "neutral"
  for candidate_level, key in candidates:
    if key is None:
      continue
    candidate = catalog.get(key)
    if candidate is None:
      rejected.append({"candidate": key, "reason": "not-found"})
    elif any(candidate.get("ports", {}).get(name) != signature for name, signature in ports.items()):
      rejected.append({"candidate": key, "reason": "port-signature-mismatch"})
    else:
      selected, level = candidate, candidate_level
      break
  if selected is None:
    selected = {"id": "neutral:port-contract", "version": "1", "ports": ports,
                "primitives": [{"tag": "rect", "attrs": {"width": "34", "height": "34", "stroke-dasharray": "4 3"}}]}
  return {"requested": request, "actual": selected, "level": level,
          "catalog_version": version, "catalog_digest": digest(catalog),
          "symbol_digest": digest(selected), "presentation_exact": level in {"explicit", "model"},
          "semantic_ports_complete": True, "rejected": rejected,
          "fallback_reason": None if level in {"explicit", "model"} else "No compatible exact model symbol."}


def apply_overlay(document: dict, overlay: dict | None) -> list[dict]:
  """Explicit positions/routes only. Moving a node requires explicit incident routes."""
  if overlay is None:
    return []
  diagnostics = []

  def fail(message):
    diagnostics.append({"rule": "D_OVERLAY", "status": "fail", "message": message})

  if (not isinstance(overlay, dict) or set(overlay) != {"schema_version", "view", "source", "revision", "nodes", "edges"}
      or overlay.get("schema_version") != 1 or overlay.get("view") != document["view"]):
    fail("Overlay schema or view mismatch.")
    return diagnostics
  if not all(isinstance(overlay[k], str) and overlay[k] for k in ("source", "revision")):
    fail("Overlay requires source and revision.")
    return diagnostics
  if not isinstance(overlay["nodes"], dict) or not isinstance(overlay["edges"], dict):
    fail("Overlay nodes and edges must be objects.")
    return diagnostics
  nodes = {n["id"]: n for n in document["nodes"]}
  edges = {e["id"]: e for e in document["edges"]}
  for identifier, position in overlay["nodes"].items():
    if identifier not in nodes or not isinstance(position, dict) or set(position) != {"x", "y"}:
      fail(f"Invalid node reference or non-layout fields: {identifier}")
      continue
    if not all(_finite(v) and v >= 0 for v in position.values()):
      fail(f"Invalid node position: {identifier}")
      continue
    incident = {e["id"] for e in document["edges"] if any(p["id"] in {e["source"], e["target"]} for p in nodes[identifier]["ports"])}
    if not incident <= set(overlay["edges"]):
      fail(f"Moving {identifier} requires explicit routes for all incident edges.")
      continue
    nodes[identifier].update(position)
  for identifier, route in overlay["edges"].items():
    if identifier not in edges or not isinstance(route, dict) or set(route) != {"points"}:
      fail(f"Invalid edge reference or non-layout fields: {identifier}")
      continue
    points = route["points"]
    if (not isinstance(points, list) or len(points) < 2 or
        any(not isinstance(p, dict) or set(p) != {"x", "y"} or not all(_finite(v) and v >= 0 for v in p.values()) for p in points)):
      fail(f"Invalid route: {identifier}")
      continue
    edges[identifier]["points"] = points
    # An overlay route also owns label placement: use its longest segment.
    a, b = max(zip(points, points[1:]), key=lambda pair: abs(pair[0]["x"]-pair[1]["x"])+abs(pair[0]["y"]-pair[1]["y"]))
    edges[identifier]["label_position"] = {"x": (a["x"]+b["x"])/2, "y": (a["y"]+b["y"])/2-20}
  document["overlay"] = {"digest": digest(overlay), "source": overlay["source"], "revision": overlay["revision"]}
  document["overlay_source_map"] = {
    identifier: {"source": overlay["source"], "revision": overlay["revision"], "fields": sorted(fields)}
    for collection in ("nodes", "edges") for identifier, fields in overlay[collection].items()
    if isinstance(fields, dict)
  }
  return diagnostics


def _finite(value) -> bool:
  return not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)


def _intersects(a: dict, b: dict, box: dict) -> bool:
  """True when an orthogonal segment enters a node's interior."""
  left, top = box["x"] + 0.01, box["y"] + 0.01
  right, bottom = box["x"] + box["width"] - 0.01, box["y"] + box["height"] - 0.01
  if abs(a["x"] - b["x"]) < 0.01:
    return left < a["x"] < right and max(min(a["y"], b["y"]), top) < min(max(a["y"], b["y"]), bottom)
  return top < a["y"] < bottom and max(min(a["x"], b["x"]), left) < min(max(a["x"], b["x"]), right)


def check_document(document: dict) -> list[dict]:
  diagnostics = []

  def fail(rule, message):
    diagnostics.append({"rule": rule, "status": "fail", "message": message})

  nodes = document["nodes"]
  objects = [*nodes, *document["edges"], *(p for n in nodes for p in n["ports"])]
  identifiers = [o["id"] for o in objects]
  if len(set(identifiers)) != len(identifiers):
    fail("D_IDENTITY", "Duplicate diagram identity.")
  actual_nodes = sorted(n["semantic_ref"] for n in nodes)
  actual_ports = sorted((p["id"], p["semantic_ref"], p["kind"], p["direction"]) for n in nodes for p in n["ports"])
  actual_edges = sorted((e["semantic_ref"], e["source"], e["target"]) for e in document["edges"])
  expected = document["expected_projection"]
  if (actual_nodes != expected["nodes"] or actual_edges != [tuple(e) for e in expected["edges"]]
      or actual_ports != [tuple(p) for p in expected["ports"]]):
    fail("D_COMPLETENESS", "Diagram does not preserve the declared source projection.")
  ports = {p["id"]: (n, p) for n in nodes for p in n["ports"]}
  for n in nodes:
    if not all(_finite(n.get(k)) and n[k] >= 0 for k in ("x", "y", "width", "height")):
      fail("D_GEOMETRY", f"Non-finite/negative node geometry: {n['id']}")
      return diagnostics
    for p in n["ports"]:
      if not all(_finite(p.get(k)) for k in ("x", "y")) or not (0 <= p["x"] <= n["width"] and 0 <= p["y"] <= n["height"]):
        fail("D_PORT", f"Invalid port geometry: {p['id']}")
  for i, a in enumerate(nodes):
    for b in nodes[i+1:]:
      if a["x"] < b["x"]+b["width"] and b["x"] < a["x"]+a["width"] and a["y"] < b["y"]+b["height"] and b["y"] < a["y"]+a["height"]:
        fail("D_OVERLAP", f"Overlapping nodes: {a['id']}, {b['id']}")
  for edge in document["edges"]:
    if edge["source"] not in ports or edge["target"] not in ports:
      fail("D_ENDPOINT", f"Unresolved edge endpoint: {edge['id']}")
      continue
    source, target = ports[edge["source"]][1], ports[edge["target"]][1]
    if source["kind"] != target["kind"] or source["kind"] != edge["medium"]:
      fail("D_MEDIUM", f"Port media differ: {edge['id']}")
    points = edge.get("points", [])
    if len(points) < 2 or any(not all(_finite(p.get(k)) for k in ("x", "y")) for p in points):
      fail("D_ROUTE", f"Missing/non-finite route: {edge['id']}")
      continue
    for point, endpoint in ((points[0], edge["source"]), (points[-1], edge["target"])):
      n, p = ports[endpoint]
      if abs(point["x"]-n["x"]-p["x"]) > 0.02 or abs(point["y"]-n["y"]-p["y"]) > 0.02:
        fail("D_ROUTE", f"Route does not terminate on semantic port: {endpoint}")
    for a, b in zip(points, points[1:]):
      if abs(a["x"]-b["x"]) > 0.02 and abs(a["y"]-b["y"]) > 0.02:
        fail("D_ROUTE", f"Non-orthogonal route: {edge['id']}")
      elif any(_intersects(a, b, n) for n in nodes):
        fail("D_OCCLUSION", f"Route enters a node interior: {edge['id']}")
  return diagnostics


def compose(model: dict, source: str, profile: str, contract: DiagramContract,
            overlay: dict | None = None) -> tuple[dict, list[dict]]:
  document = contract.projection(model, source, profile)
  for node in document["nodes"]:
    node["symbol"] = contract.symbols(node.pop("symbol_request"))
  document["backend"] = contract.layout(document)
  diagnostics = apply_overlay(document, overlay)
  diagnostics.extend(check_document(document))
  width = max((n["x"]+n["width"] for n in document["nodes"]), default=400)
  height = max((n["y"]+n["height"] for n in document["nodes"]), default=200)
  for e in document["edges"]:
    width = max(width, *(p["x"] for p in e["points"]), e["label_position"]["x"]+len(e["medium"])*8)
    height = max(height, *(p["y"] for p in e["points"]))
  document["pages"] = [{"id": "page:1", "title": document["title"], "width": math.ceil(width+60), "height": math.ceil(height+60)}]
  document.setdefault("presentation", {}).update({"font": FONT, "font_policy": "system-sans-serif; host glyphs require visual review", "number_precision": 3})
  return document, diagnostics


def _number(value) -> str:
  return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _state_lines(document: dict) -> list[str]:
  return [document["title"], "概念设计 / 待工程审核 · 图面位置不代表物理布置",
          f"行为：{document['behavior_state']}；空间：{document['spatial_state']}"]


def render_drawio(document: dict) -> str:
  """Export shared resolved primitives and ports; never reconstruct equipment geometry."""
  page = document["pages"][0]
  header = 92
  root = ET.Element("mxfile", {"host": "texsys", "version": "1", "compressed": "false"})
  diagram = ET.SubElement(root, "diagram", {"id": "page:1", "name": document["title"]})
  graph = ET.SubElement(diagram, "mxGraphModel", {"page": "1", "pageWidth": str(page["width"]), "pageHeight": str(page["height"]+header)})
  cells = ET.SubElement(graph, "root")
  ET.SubElement(cells, "mxCell", {"id": "0"})
  ET.SubElement(cells, "mxCell", {"id": "1", "parent": "0"})
  banner = ET.SubElement(cells, "mxCell", {"id": "banner", "value": "\n".join(_state_lines(document)), "vertex": "1", "parent": "1", "style": "text;html=0;align=left;fontFamily=Arial;fontSize=13;"})
  ET.SubElement(banner, "mxGeometry", {"x": "24", "y": "10", "width": str(page["width"]-48), "height": "72", "as": "geometry"})
  for node in document["nodes"]:
    symbol = node["symbol"]
    graphic = node["graphic"]
    obj = ET.SubElement(cells, "UserObject", {"id": node["id"], "label": node["label"], "semantic_ref": node["semantic_ref"], "source": document["source"], "symbol": symbol["actual"]["id"], "fallback": symbol["level"], "presentation_exact": str(symbol["presentation_exact"]).lower()})
    cell = ET.SubElement(obj, "mxCell", {"vertex": "1", "parent": "1", "style": "html=0;noLabel=1;fillColor=none;strokeColor=none;"})
    ET.SubElement(cell, "mxGeometry", {"x": _number(node["x"]), "y": _number(node["y"]+header), "width": str(node["width"]), "height": str(node["height"]), "as": "geometry"})
    icon = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": f"0 0 {node['width']} {node['height']}", "stroke": "#173247", "color": "#173247", "fill": "none", "stroke-width": "1.6", "stroke-linejoin": "round", "stroke-linecap": "round"})
    for primitive in graphic["primitives"]:
      ET.SubElement(icon, primitive["tag"], {k: str(v) for k, v in primitive["attrs"].items()})
    anchor = graphic["label_anchor"]
    ET.SubElement(icon, "text", {"x": str(anchor["x"]), "y": str(anchor["y"]), "text-anchor": "middle", "stroke": "none", "fill": "#173247", "font-family": "Arial", "font-size": "13"}).text = node["label"]
    if document["deliverable"] == "schematic":
      anchor = graphic["annotation_anchor"]
      ET.SubElement(icon, "text", {"x": str(anchor["x"]), "y": str(anchor["y"]), "text-anchor": "middle", "stroke": "none", "fill": "#5b6f7e", "font-family": "Arial", "font-size": "10"}).text = "　".join(node.get("engineering_labels", []))
    icon_cell = ET.SubElement(cells, "mxCell", {"id": node["id"]+":symbol", "vertex": "1", "parent": node["id"], "style": "shape=image;image=data:image/svg+xml,"+quote(ET.tostring(icon, encoding="unicode"), safe="")+";"})
    ET.SubElement(icon_cell, "mxGeometry", {"x": "0", "y": "0", "width": str(node["width"]), "height": str(node["height"]), "as": "geometry"})
    for port in node["ports"]:
      obj = ET.SubElement(cells, "UserObject", {"id": port["id"], "label": "", "semantic_ref": port["semantic_ref"], "medium": port["kind"], "direction": port["direction"]})
      cell = ET.SubElement(obj, "mxCell", {"vertex": "1", "connectable": "1", "parent": node["id"], "style": "shape=ellipse;html=0;resizable=0;fillColor="+_color(document, port["kind"])+";strokeColor=none;"})
      geo = ET.SubElement(cell, "mxGeometry", {"x": _number(port["x"]/node["width"]), "y": _number(port["y"]/node["height"]), "width": "3", "height": "3", "relative": "1", "as": "geometry"})
      ET.SubElement(geo, "mxPoint", {"x": "-1.5", "y": "-1.5", "as": "offset"})
  for edge in document["edges"]:
    obj = ET.SubElement(cells, "UserObject", {"id": edge["id"], "label": "", "semantic_ref": edge["semantic_ref"], "source_ref": edge["source"], "target_ref": edge["target"]})
    cell = ET.SubElement(obj, "mxCell", {"edge": "1", "parent": "1", "source": edge["source"], "target": edge["target"], "style": "html=0;rounded=0;noEdgeStyle=1;exitX=0.5;exitY=0.5;exitPerimeter=0;entryX=0.5;entryY=0.5;entryPerimeter=0;strokeColor="+_color(document, edge["medium"])+";endArrow="+("open" if edge["directed"] else "none")+";"})
    geo = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    array = ET.SubElement(geo, "Array", {"as": "points"})
    for point in edge["points"][1:-1]:
      ET.SubElement(array, "mxPoint", {"x": _number(point["x"]), "y": _number(point["y"]+header)})
  ET.indent(root)
  return ET.tostring(root, encoding="unicode") + "\n"
