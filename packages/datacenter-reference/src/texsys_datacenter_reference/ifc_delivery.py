"""Project-owned IFC coordination projection from existing AIDC build models."""

from collections import defaultdict
import json
import math

from texsys.evidence import canonical_digest, file_digest, write_json
from texsys.project import inspect_project
from texsys.targets import TargetResult

from .checks import load_design
from .ifc_backend import IfcWriter, validate_exchange
from .pod_layout import _world_space
from .spatial_design import MODELS, _world, artifact, read_versioned, source_path, staged_json


FAMILIES = {
  "busway": "IfcCableSegment", "condenser-water-pump": "IfcPump", "cooling-tower": "IfcCoolingTower",
  "facility-aux-transfer": "IfcElectricDistributionBoard", "generator-command-panel": "IfcController",
  "fire-partition-wall": "IfcWall", "raised-access-floor": "IfcSlab", "steel-frame-bay": "IfcElementAssembly",
  "cooling-container": "IfcElementAssembly", "generator-container": "IfcElementAssembly",
  "it-container": "IfcElementAssembly", "power-container": "IfcElementAssembly",
  "liquid-cooled-rack": "IfcFurniture", "precision-air-unit": "IfcUnitaryEquipment",
  "coolant-distribution-unit": "IfcUnitaryEquipment", "FirePanel": "IfcController",
  "PowerFeed": "IfcElectricDistributionBoard", "RackPdu": "IfcElectricDistributionBoard",
}
PREDEFINED = {"precision-air-unit": "AIRCONDITIONINGUNIT",
              "RackPdu": "DISTRIBUTIONBOARD", "steel-frame-bay": "RIGID_FRAME"}
LIMITATIONS = [
  "Synthetic assumed coordination model; not construction approval, an MVD certification or engineering acceptance.",
  "Top-level assemblies and IT-pod equipment use declared envelopes; other assembly internals are not expanded.",
  "Only the facility-water network is exchanged as systems and connected ports; other professional networks are excluded.",
  "Pipes use physical outside/bore diameters; insulation is a property, not a separate solid. Junction fittings are topology-only.",
  "Architectural shell, structural member detailing, valves, insulation, supports and buffer foundations are excluded.",
  "Source terminal nodes without a mating object remain explicit open boundaries; no fictitious caps or connections are added.",
  "External viewer acceptance, IFC-to-Revit editability and bidirectional updates require separate verification.",
]


def _pointer(*parts):
  return "/" + "/".join(str(x).replace("~", "~0").replace("/", "~1") for x in parts)


def _problem(context, output, status, code, message):
  report = output.with_suffix(".validation.json")
  diagnostic = {"code": code, "message": message}
  write_json(report, {"schema_version": 1, "status": status, "diagnostics": [diagnostic], "limitations": LIMITATIONS})
  return TargetResult(status, (artifact(context, "ifc-validation", report, kind="diagnostic"),),
                      diagnostics=(diagnostic,) if status != "indeterminate" else (),
                      open_items=({"id": code, "impact": message},) if status == "indeterminate" else ())


def _network(writer, network, equipment, source):
  """Expand actual graph junctions so every IFC port has at most one mate."""
  nodes = defaultdict(list)
  members = {leg: [] for leg in ("supply", "return")}
  pipe_products = []
  for key, row in sorted(network["segments"].items()):
    identity = "fws/pipe/"+key
    product = writer.pipe(identity, row, source("fws-network", "segments", key))
    pipe_products.append(product)
    members[row["leg"]].append(product)
    for end, flow in (("from", "SINK"), ("to", "SOURCE")):
      point = network["nodes"][row[end]]
      port = writer.port(identity+"/"+end, product, point, source("fws-network", "segments", key, end), flow)
      nodes[row[end]].append(port)
  for key, row in sorted(network["buffers"].items()):
    identity = "fws/buffer/"+key
    product = writer.box("IfcTank", identity, row["body"]["minimum_m"], row["body"]["maximum_m"],
                         source("fws-network", "buffers", key), PredefinedType="NOTDEFINED")
    writer.properties(product, identity, {"State": "assumed", "Geometry": "vessel envelope",
                                          "WorkingVolume_m3": row["working_volume_m3"]})
    members[row["leg"]].append(product)
    pipe_products.append(product)
    for end, flow in (("from", "SINK"), ("to", "SOURCE")):
      nodes[row[end]].append(writer.port(identity+"/"+end, product, network["nodes"][row[end]],
                                         source("fws-network", "buffers", key, end), flow))
  for role, objects in sorted(network["ports"].items()):
    for name, legs in sorted(objects.items()):
      owner = equipment[name]
      for leg, row in sorted(legs.items()):
        matches = [key for key, point in network["nodes"].items() if math.dist(point, row["point_m"]) < 1e-7]
        if len(matches) != 1:
          raise ValueError(f"{name}.{leg}: IFC endpoint must match exactly one compiled FWS node.")
        flow = "SOURCE" if (role == "plants") == (leg == "supply") else "SINK"
        port = writer.port(f"asset/{name}/fws_{leg}", owner, row["point_m"],
                           source("fws-network", "ports", role, name, leg), flow)
        nodes[matches[0]].append(port)
        members[leg].append(owner)
  junctions, boundaries = [], []
  for node, ports in sorted(nodes.items()):
    if len(ports) == 1:
      boundaries.append({"node": node, "port": ports[0].GlobalId, "point_m": network["nodes"][node]})
    elif len(ports) == 2:
      writer.connect(*ports)
    else:
      identity = "fws/junction/"+node
      junction = writer.root("IfcPipeFitting", identity, Name=identity, PredefinedType="JUNCTION",
                             ObjectPlacement=writer.placement(network["nodes"][node]))
      writer.mark(identity, junction, source("fws-network", "nodes", node), representation="topology-only")
      writer.properties(junction, identity, {"State": "assumed", "Geometry": "topology-only junction"})
      junctions.append(junction)
      leg = "supply" if node.startswith("S-") else "return"
      members[leg].append(junction)
      for port in sorted(ports, key=lambda x: x.Name):
        mate = writer.port(identity+"/"+port.Name, junction, network["nodes"][node], source("fws-network", "nodes", node))
        writer.connect(port, mate)
  writer.contain("fws", writer.building, pipe_products+junctions)
  for leg, products in members.items():
    writer.system("FWS-"+leg, sorted(set(products), key=lambda x: x.GlobalId), source("fws-network", "roots", leg))
  return boundaries


def build_ifc_coordination(context):
  output = context.build_path / (context.output or "ifc/aidc-coordination.ifc")
  if output.suffix.lower() != ".ifc":
    raise ValueError("IFC coordination output must use the .ifc extension.")
  config_path = source_path(context, "ifc.json")
  if not config_path.is_file():
    return _problem(context, output, "indeterminate", "IFC_MISSING_CONFIG", "Required src/ifc.json is missing.")
  config = read_versioned(config_path)
  if (set(config) != {"schema_version", "schema", "project_id", "name"} or config.get("schema") != "IFC4"
      or any(not isinstance(config.get(key), str) or not config[key].strip() for key in ("project_id", "name"))
      or context.scope):
    return _problem(context, output, "fail", "IFC_CONTRACT",
                    "Declare IFC4, a stable nonempty project_id and name; this first coordination target has no scope filters.")
  try:
    import ifcopenshell
  except ImportError as error:
    return _problem(context, output, "indeterminate", "IFC_BACKEND_UNAVAILABLE", str(error))
  layout, levels, network, pods = [staged_json(context, f"{MODELS}/{name}.json")
                                   for name in ("layout", "levels", "fws-network", "pod-layout")]
  design = load_design(config_path.parent, context.root)
  root_name = json.loads(source_path(context, "system.sdl.json").read_text())["root"]
  project = inspect_project(context.root)
  models = {}

  def source(model, *pointer):
    path = context.build_path / MODELS / (model+".json")
    if model not in models:
      models[model] = file_digest(path)
    return {"file": f"{project.build}/{MODELS}/{model}.json", "digest": models[model], "pointer": _pointer(*pointer)}

  writer = IfcWriter(ifcopenshell, config["project_id"], config["name"])
  storeys = {key: writer.storey(key, levels["levels_m"][key], source("levels", "levels_m", key))
             for key in sorted({row["level"] for row in levels["zones"].values()})}
  spaces = {}
  for key, row in sorted(levels["zones"].items()):
    space = writer.box("IfcSpace", "zone/"+key, row["minimum_m"], row["maximum_m"], source("levels", "zones", key),
                       PredefinedType="NOTDEFINED")
    writer.properties(space, "zone/"+key, {"State": "assumed", "Role": "functional planning zone",
                                          "Geometry": "planning envelope, not a surveyed room enclosure"})
    writer.aggregate("zone/"+key, storeys[row["level"]], [space])
    spaces[key] = space
  equipment, contained = {}, defaultdict(list)
  for key, row in sorted(layout["objects"].items()):
    identity = "asset/"+key
    instance = design.instances[key]
    kind = FAMILIES.get(instance.family)
    if kind is None:
      return _problem(context, output, "fail", "IFC_UNMAPPED_FAMILY", f"No IFC mapping for {key}: {instance.family}.")
    if key in pods["pods"]:
      angle = math.radians(row["rotation_deg"])
      product = writer.root(kind, identity, Name=key, PredefinedType="USERDEFINED", ObjectType=instance.family,
                            ObjectPlacement=writer.placement(row["origin_m"], x=(math.cos(angle), math.sin(angle), 0)))
      writer.mark(identity, product, source("layout", "objects", key), representation="expanded-assembly")
      children = []
      for child, part in sorted(pods["pods"][key]["local_equipment"].items()):
        child_kind = FAMILIES.get(part["family"])
        if child_kind is None:
          return _problem(context, output, "fail", "IFC_UNMAPPED_FAMILY", f"No IFC mapping for {key}/{child}.")
        shape = _world_space(part, row)
        child_id = identity+"/"+child
        local_origin = list(part["minimum_m"])
        if part["rotation_deg"] == 180:
          local_origin[0] += part["dimensions_m"][0]
          local_origin[1] += part["dimensions_m"][1]
        child_product = writer.box(child_kind, child_id, shape["minimum_m"], shape["maximum_m"],
                                   source("pod-layout", "pods", key, "local_equipment", child),
                                   origin=_world(row["origin_m"], row["rotation_deg"], local_origin),
                                   rotation=(row["rotation_deg"]+part["rotation_deg"]) % 360,
                                   PredefinedType=PREDEFINED.get(part["family"], "USERDEFINED"), ObjectType=part["family"])
        writer.properties(child_product, child_id, {"State": "assumed", "Family": part["family"], "Module": part["document"]})
        children.append(child_product)
      writer.aggregate(identity, product, children)
    else:
      product = writer.box(kind, identity, row["minimum_m"], row["maximum_m"], source("layout", "objects", key),
                           origin=row["origin_m"], rotation=row["rotation_deg"],
                           PredefinedType=PREDEFINED.get(instance.family, "USERDEFINED"), ObjectType=instance.family)
    writer.sources[product.GlobalId]["engineering_source"] = {
      "file": f"{project.source}/system.sdl.json", "digest": file_digest(source_path(context, "system.sdl.json")),
      "pointer": _pointer("components", root_name, "instances", key)}
    writer.properties(product, identity, {"State": "assumed", "Family": instance.family, "Zone": row["zone"],
                                          "Discipline": row["discipline"], "Module": row["module"],
                                          "ParametersJSON": json.dumps(instance.parameters, ensure_ascii=False, sort_keys=True)})
    equipment[key] = product
    contained[row["zone"]].append(product)
  for zone, products in sorted(contained.items()):
    writer.contain(zone, spaces[zone], products)
  boundaries = _network(writer, network, equipment, source)
  output.parent.mkdir(parents=True, exist_ok=True)
  writer.file.write(str(output))
  report = validate_exchange(output, writer)
  report.update({"schema_version": 1, "profile": context.profile, "project_id": config["project_id"],
                 "input_digest": canonical_digest(context.inputs), "ifc_digest": file_digest(output),
                 "engineering_acceptance": "not-assessed", "open_boundaries": boundaries, "limitations": LIMITATIONS})
  mapping = output.with_suffix(".sources.json")
  validation = output.with_suffix(".validation.json")
  write_json(mapping, {"schema_version": 1, "project_id": config["project_id"], "ifc_digest": file_digest(output),
                       "input_digest": canonical_digest(context.inputs), "objects": writer.sources,
                       "connections": writer.connections, "limitations": LIMITATIONS})
  write_json(validation, report)
  return TargetResult(report["status"], (artifact(context, "ifc-coordination", output),
                      artifact(context, "ifc-sources", mapping), artifact(context, "ifc-validation", validation, kind="diagnostic")),
                      diagnostics=tuple(report["diagnostics"]), details={"geometry_checked": report["geometry_checked"],
                      "mapped_objects": report["mapped_objects"], "connections_checked": report["connections_checked"],
                      "engineering_acceptance": "not-assessed"})
