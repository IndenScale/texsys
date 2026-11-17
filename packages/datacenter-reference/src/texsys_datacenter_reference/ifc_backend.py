"""Deterministic IFC4 writing and read-back checks for declared coordination data."""

from collections import Counter
import math
from uuid import NAMESPACE_URL, uuid5


class IfcWriter:
  """Keep exchange geometry and identity explicit; never infer engineering semantics."""

  def __init__(self, backend, project_id, name):
    self.backend = backend
    self.file = backend.file(schema="IFC4")
    self.namespace = uuid5(NAMESPACE_URL, project_id)
    self.objects = {}
    self.sources = {}
    self.connections = []
    self.property_expectations = {}
    self.relationships = {}
    self.file.header.file_name.name = "texsys-coordination.ifc"
    # Wall-clock metadata belongs in build.json, not deterministic design output.
    self.file.header.file_name.time_stamp = "2000-01-01T00:00:00"
    self.file.header.file_name.author = ("texsys",)
    self.file.header.file_name.organization = ("texsys",)
    self.file.header.file_name.preprocessor_version = "texsys-ifc-coordination/1"
    self.file.header.file_name.originating_system = "texsys"
    self.file.header.file_name.authorization = ""
    self.file.header.file_description.description = ("Concept coordination; not an MVD certification or engineering acceptance",)
    self.context = self.file.create_entity(
      "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3,
      Precision=1e-6, WorldCoordinateSystem=self.axis((0, 0, 0)), TrueNorth=self.direction((0, 1)))
    self.body = self.file.create_entity(
      "IfcGeometricRepresentationSubContext", ContextIdentifier="Body", ContextType="Model",
      ParentContext=self.context, TargetView="MODEL_VIEW")
    units = self.file.create_entity("IfcUnitAssignment", Units=[
      self.file.create_entity("IfcSIUnit", UnitType=kind, Name=unit)
      for kind, unit in (("LENGTHUNIT", "METRE"), ("AREAUNIT", "SQUARE_METRE"), ("VOLUMEUNIT", "CUBIC_METRE"))
    ])
    self.project = self.root("IfcProject", "project", Name=name, RepresentationContexts=[self.context], UnitsInContext=units)
    self.site = self.root("IfcSite", "site", Name="Site", ObjectPlacement=self.placement((0, 0, 0)))
    self.building = self.root("IfcBuilding", "building", Name="Coordination building", ObjectPlacement=self.placement((0, 0, 0)))
    self.aggregate("project/site", self.project, [self.site])
    self.aggregate("site/building", self.site, [self.building])

  def root(self, kind, identity, **fields):
    return self.file.create_entity(kind, GlobalId=self.backend.guid.compress(uuid5(self.namespace, identity).hex), **fields)

  def point(self, value):
    return self.file.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in value))

  def direction(self, value):
    return self.file.create_entity("IfcDirection", DirectionRatios=tuple(float(x) for x in value))

  def axis(self, origin, z=(0, 0, 1), x=(1, 0, 0)):
    return self.file.create_entity("IfcAxis2Placement3D", Location=self.point(origin),
                                   Axis=self.direction(z), RefDirection=self.direction(x))

  def placement(self, origin, z=(0, 0, 1), x=(1, 0, 0)):
    return self.file.create_entity("IfcLocalPlacement", RelativePlacement=self.axis(origin, z, x))

  def aggregate(self, identity, parent, children):
    relation = self.root("IfcRelAggregates", "aggregate/"+identity, RelatingObject=parent, RelatedObjects=children)
    self.relationships[relation.GlobalId] = ("RelatingObject", parent.GlobalId, "RelatedObjects", [p.GlobalId for p in children])
    return relation

  def properties(self, product, identity, values):
    props = []
    for key, value in sorted(values.items()):
      if value is None:
        continue
      kind = "IfcBoolean" if type(value) is bool else "IfcReal" if type(value) in (int, float) else "IfcText"
      props.append(self.file.create_entity("IfcPropertySingleValue", Name=key,
                                          NominalValue=self.file.create_entity(kind, value)))
    if props:
      pset = self.root("IfcPropertySet", "pset/"+identity, Name="Texsys_Source", HasProperties=props)
      self.root("IfcRelDefinesByProperties", "properties/"+identity, RelatedObjects=[product], RelatingPropertyDefinition=pset)
      self.property_expectations[product.GlobalId] = {key: value for key, value in values.items() if value is not None}

  def mark(self, identity, product, source, **details):
    if identity in self.objects:
      raise ValueError(f"Duplicate IFC source identity: {identity}")
    self.objects[identity] = product
    self.sources[product.GlobalId] = {"identity": identity, "ifc_class": product.is_a(), "source": source, **details}
    return product

  def contain(self, identity, parent, children):
    if children:
      relation = self.root("IfcRelContainedInSpatialStructure", "contain/"+identity,
                           RelatingStructure=parent, RelatedElements=children)
      self.relationships[relation.GlobalId] = ("RelatingStructure", parent.GlobalId, "RelatedElements", [p.GlobalId for p in children])

  def storey(self, name, elevation, source):
    product = self.root("IfcBuildingStorey", "level/"+name, Name=name,
                        Elevation=float(elevation), ObjectPlacement=self.placement((0, 0, elevation)))
    self.aggregate("level/"+name, self.building, [product])
    return self.mark("level/"+name, product, source)

  def box(self, kind, identity, minimum, maximum, source, *, origin=None, rotation=0, **fields):
    if (len(minimum) != 3 or len(maximum) != 3 or any(
        type(a) not in (int, float) or type(b) not in (int, float) or not math.isfinite(a+b) or a >= b
        for a, b in zip(minimum, maximum, strict=True))):
      raise ValueError(f"{identity}: positive finite IFC envelope required.")
    dimensions = [b-a for a, b in zip(minimum, maximum, strict=True)]
    if rotation not in (0, 90, 180, 270):
      raise ValueError(f"{identity}: unsupported envelope rotation.")
    if rotation in (90, 270):
      dimensions[0], dimensions[1] = dimensions[1], dimensions[0]
    dx, dy, dz = dimensions
    profile = self.file.create_entity(
      "IfcRectangleProfileDef", ProfileType="AREA", XDim=dx, YDim=dy,
      Position=self.file.create_entity("IfcAxis2Placement2D", Location=self.point((dx/2, dy/2))))
    solid = self.file.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=self.axis((0, 0, 0)),
                                    ExtrudedDirection=self.direction((0, 0, 1)), Depth=dz)
    angle = math.radians(rotation)
    product = self.root(kind, identity, Name=identity, **fields,
                        ObjectPlacement=self.placement(origin or minimum, x=(math.cos(angle), math.sin(angle), 0)),
                        Representation=self.representation(solid))
    return self.mark(identity, product, source, geometry={"kind": "envelope", "minimum_m": list(minimum),
                                                         "maximum_m": list(maximum)})

  def representation(self, solid):
    shape = self.file.create_entity("IfcShapeRepresentation", ContextOfItems=self.body,
                                    RepresentationIdentifier="Body", RepresentationType="SweptSolid", Items=[solid])
    return self.file.create_entity("IfcProductDefinitionShape", Representations=[shape])

  def pipe(self, identity, row, source):
    a, b = row["points_m"]
    length = math.dist(a, b)
    if not math.isfinite(length) or length <= 0 or abs(length-row["length_m"]) > 1e-6:
      raise ValueError(f"{identity}: extracted pipe length disagrees with geometry.")
    z = tuple((v-u)/length for u, v in zip(a, b, strict=True))
    x = (0, 1, 0) if abs(z[0]) > .9 else (1, 0, 0)
    section = row["hydraulic_section"]
    inside, outside = section["inside_m"], section["outside_m"]
    if not 0 < inside < outside or not math.isfinite(inside+outside):
      raise ValueError(f"{identity}: invalid pipe diameters.")
    profile = self.file.create_entity("IfcCircleHollowProfileDef", ProfileType="AREA",
                                      Radius=outside/2, WallThickness=(outside-inside)/2)
    solid = self.file.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=self.axis((0, 0, 0)),
                                    ExtrudedDirection=self.direction((0, 0, 1)), Depth=length)
    product = self.root("IfcPipeSegment", identity, Name=identity, PredefinedType="RIGIDSEGMENT",
                        ObjectPlacement=self.placement(a, z=z, x=x), Representation=self.representation(solid))
    self.mark(identity, product, source, geometry={"kind": "pipe", "points_m": [list(a), list(b)],
                                                  "outside_m": outside, "inside_m": inside, "length_m": length})
    self.properties(product, identity, {"State": "assumed", "Length_m": length, "InsideDiameter_m": inside,
                                        "OutsideDiameter_m": outside, "Insulation_m": section["insulation_m"],
                                        "Leg": row["leg"], "SDLConnections": ",".join(row["sdl_links"])})
    return product

  def port(self, identity, owner, point, source, flow="SOURCEANDSINK"):
    port = self.root("IfcDistributionPort", identity, Name=identity, FlowDirection=flow,
                     PredefinedType="PIPE", SystemType="CHILLEDWATER", ObjectPlacement=self.placement(point))
    self.root("IfcRelNests", "nest/"+identity, RelatingObject=owner, RelatedObjects=[port])
    return self.mark(identity, port, source, owner=owner.GlobalId, point_m=list(point))

  def connect(self, first, second):
    key = "/".join(sorted((first.GlobalId, second.GlobalId)))
    relation = self.root("IfcRelConnectsPorts", "connect/"+key, RelatingPort=first, RelatedPort=second)
    self.connections.append({"guid": relation.GlobalId, "from": first.GlobalId, "to": second.GlobalId})

  def system(self, identity, products, source):
    system = self.root("IfcDistributionSystem", "system/"+identity, Name=identity, PredefinedType="CHILLEDWATER")
    self.root("IfcRelAssignsToGroup", "system-members/"+identity, RelatedObjects=products, RelatingGroup=system)
    return self.mark("system/"+identity, system, source, members=sorted(p.GlobalId for p in products))


def validate_exchange(path, writer, tolerance=1e-5):
  """Reopen serialized output, check schema, topology and actual OCC geometry."""
  import ifcopenshell.geom
  import ifcopenshell.validate
  import ifcopenshell.util.element
  import ifcopenshell.util.placement
  import ifcopenshell.util.shape

  model = writer.backend.open(str(path))
  logger = ifcopenshell.validate.json_logger()
  ifcopenshell.validate.validate(str(path), logger, express_rules=True)
  issues = [{"code": "IFC_SCHEMA", "message": str(item.get("message"))} for item in logger.statements
            if item.get("level") in {"error", "critical"}]
  roots = model.by_type("IfcRoot")
  if len({item.GlobalId for item in roots}) != len(roots):
    issues.append({"code": "IFC_DUPLICATE_GUID", "message": "IFC root identities are not unique."})
  settings = ifcopenshell.geom.settings()
  settings.set(settings.USE_WORLD_COORDS, True)
  settings.set("mesher-linear-deflection", .001)
  settings.set("mesher-angular-deflection", .1)
  geometry_checked = 0
  for guid, record in writer.sources.items():
    product = model.by_guid(guid)
    if product.is_a() != record["ifc_class"]:
      issues.append({"code": "IFC_CLASS", "object": record["identity"], "message": "Source class was not retained."})
    if "geometry" not in record:
      continue
    try:
      shape = ifcopenshell.geom.create_shape(settings, product)
      vertices = list(zip(*[iter(shape.geometry.verts)]*3, strict=True))
      if not vertices or not shape.geometry.faces:
        raise ValueError("No evaluated solid geometry.")
      minimum = [min(p[i] for p in vertices) for i in range(3)]
      maximum = [max(p[i] for p in vertices) for i in range(3)]
      geometry = record["geometry"]
      if geometry["kind"] == "envelope":
        expected = (geometry["minimum_m"], geometry["maximum_m"])
      else:
        a, b = geometry["points_m"]
        solid = product.Representation.Representations[0].Items[0]
        matrix = (ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement)
                  @ ifcopenshell.util.placement.get_axis2placement(solid.Position))
        actual_a = matrix[:3, 3]
        actual_b = actual_a+matrix[:3, 2]*solid.Depth
        profile = solid.SweptArea
        if (math.dist(actual_a, a) > tolerance or math.dist(actual_b, b) > tolerance
            or abs(profile.Radius-geometry["outside_m"]/2) > tolerance
            or abs(profile.WallThickness-(geometry["outside_m"]-geometry["inside_m"])/2) > tolerance
            or profile.Position is not None):
          raise ValueError("Read-back pipe placement, bore, wall or length differs from its source.")
        expected_volume = math.pi/4*(geometry["outside_m"]**2-geometry["inside_m"]**2)*geometry["length_m"]
        if not math.isclose(ifcopenshell.util.shape.get_volume(shape.geometry), expected_volume, rel_tol=.005, abs_tol=1e-8):
          raise ValueError("Evaluated pipe volume differs from its independent annular-volume reference.")
        radius = geometry["outside_m"]/2
        expected = ([min(a[i], b[i])-(radius if a[i] == b[i] else 0) for i in range(3)],
                    [max(a[i], b[i])+(radius if a[i] == b[i] else 0) for i in range(3)])
      bound_tolerance = .001 if geometry["kind"] == "pipe" else tolerance
      if any(abs(a-b) > bound_tolerance for actual, target in zip((minimum, maximum), expected, strict=True)
             for a, b in zip(actual, target, strict=True)):
        raise ValueError("Read-back geometry differs from the compiled source envelope.")
      geometry_checked += 1
    except Exception as error:
      issues.append({"code": "IFC_GEOMETRY", "object": record["identity"], "message": str(error)})
  connections = {item.GlobalId: (item.RelatingPort.GlobalId, item.RelatedPort.GlobalId)
                 for item in model.by_type("IfcRelConnectsPorts")}
  expected = {item["guid"]: (item["from"], item["to"]) for item in writer.connections}
  if connections != expected:
    issues.append({"code": "IFC_CONNECTIONS", "message": "Read-back port connections differ from the source graph."})
  for guid, expected in writer.property_expectations.items():
    actual = ifcopenshell.util.element.get_psets(model.by_guid(guid)).get("Texsys_Source", {})
    actual.pop("id", None)
    if set(actual) != set(expected) or any(
        not math.isclose(actual[key], value, rel_tol=1e-12, abs_tol=1e-9)
        if type(value) in (int, float) and type(actual.get(key)) in (int, float)
        else actual.get(key) != value for key, value in expected.items()):
      issues.append({"code": "IFC_PROPERTIES", "object": guid, "message": "Required source properties changed or disappeared."})
  for guid, (parent_attr, parent, children_attr, children) in writer.relationships.items():
    relationship = model.by_guid(guid)
    if (getattr(relationship, parent_attr).GlobalId != parent
        or sorted(p.GlobalId for p in getattr(relationship, children_attr)) != sorted(children)):
      issues.append({"code": "IFC_HIERARCHY", "object": guid, "message": "Assembly or spatial relationship changed."})
  for guid, record in writer.sources.items():
    product = model.by_guid(guid)
    if "owner" in record:
      owners = [rel.RelatingObject.GlobalId for rel in product.Nests]
      location = ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement)[:3, 3]
      if owners != [record["owner"]] or math.dist(location, record["point_m"]) > tolerance:
        issues.append({"code": "IFC_PORT", "object": record["identity"], "message": "Port owner or position changed."})
    if "members" in record:
      members = sorted(item.GlobalId for rel in product.IsGroupedBy for item in rel.RelatedObjects)
      if members != record["members"]:
        issues.append({"code": "IFC_SYSTEM", "object": record["identity"], "message": "System membership changed."})
  return {"status": "fail" if issues else "pass", "schema": model.schema,
          "backend": writer.backend.version, "geometry_checked": geometry_checked,
          "mapped_objects": len(writer.sources), "connections_checked": len(connections),
          "properties_checked": len(writer.property_expectations), "relationships_checked": len(writer.relationships),
          "geometry_tolerance_m": tolerance, "pipe_mesh_deflection_m": .001, "pipe_volume_relative_tolerance": .005,
          "classes": dict(sorted(Counter(row["ifc_class"] for row in writer.sources.values()).items())),
          "diagnostics": issues}
