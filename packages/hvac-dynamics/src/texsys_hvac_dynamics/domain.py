"""Immutable engineering snapshots and explicit water-network constitutive data.

The SDL snapshot owns identities, connections and parameter bindings. Versioned
JSON behavior descriptors select supported constitutive models; they contain no
project parameter values and no executable source code.
"""

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Mapping
from types import MappingProxyType

from texsys.evidence import canonical_digest, file_digest
from texsys.formal import _load_json
from texsys.project import inspect_project

KINDS = {"pipe", "pump", "valve", "tank", "load", "tower", "chiller", "junction"}


class PhysicsInputError(ValueError):
  """A deterministic violation of the declared model or scenario contract."""


class PhysicsUnresolved(PhysicsInputError):
  """A structurally legal design does not yet provide executable physical data."""


@dataclass(frozen=True)
class Parameter:
  value: float
  unit: str
  state: str
  source: str


@dataclass(frozen=True)
class Component:
  identity: str
  definition: str
  kind: str
  parameters: Mapping[str, Parameter]
  ports: Mapping[str, str]
  behavior_reference: str
  behavior_digest: str

  def number(self, name: str, unit: str, *, positive=False, nonnegative=False) -> float:
    binding = self.parameters.get(name)
    if binding is None:
      raise PhysicsUnresolved(f"{self.identity}: missing parameter {name}.")
    if binding.unit != unit:
      raise PhysicsInputError(f"{self.identity}.{name}: expected {unit}, got {binding.unit}; implicit conversion is forbidden.")
    if positive and binding.value <= 0 or nonnegative and binding.value < 0:
      raise PhysicsInputError(f"{self.identity}.{name}: outside the permitted range.")
    return binding.value


@dataclass(frozen=True)
class Branch:
  identity: str
  component: str
  circuit: str
  inlet: str
  outlet: str
  medium: str
  volume_m3: float
  wall_capacity_j_k: float
  segments: int


@dataclass(frozen=True)
class PhysicalModel:
  root: str
  components: tuple[Component, ...]
  branches: tuple[Branch, ...]
  junctions: tuple[str, ...]
  port_junctions: Mapping[str, str]
  connections: tuple[tuple[str, str], ...]
  density_kg_m3: float
  cp_j_kg_k: float
  viscosity_pa_s: float
  gravity_m_s2: float
  snapshot_digest: str
  source_digests: tuple[tuple[str, str], ...]
  assumptions: tuple[str, ...]

  @property
  def by_id(self):
    return {component.identity: component for component in self.components}

  def as_dict(self):
    return {"schema_version": 1, "backend_model": "lumped-water-network/1", "root": self.root,
            "snapshot_digest": self.snapshot_digest,
            "sources": [{"path": path, "digest": value} for path, value in self.source_digests],
            "water": {"density_kg_m3": self.density_kg_m3, "cp_j_kg_k": self.cp_j_kg_k,
                      "viscosity_pa_s": self.viscosity_pa_s, "gravity_m_s2": self.gravity_m_s2},
            "components": [{"identity": c.identity, "definition": c.definition, "kind": c.kind,
                            "behavior_reference": c.behavior_reference, "behavior_digest": c.behavior_digest,
                            "parameters": {k: vars(v) for k, v in c.parameters.items()}, "ports": dict(c.ports)} for c in self.components],
            "branches": [vars(branch) for branch in self.branches], "junctions": list(self.junctions),
            "port_junctions": dict(self.port_junctions), "connections": [list(pair) for pair in self.connections],
            "assumptions": list(self.assumptions)}


def validate_component(component):
  """Validate the complete executable contract before publishing a snapshot."""
  c = component
  if c.kind == "junction":
    return
  c.number("elevation_change_m", "m")
  if c.kind == "pipe":
    c.number("roughness_m", "m", nonnegative=True)
    c.number("minor_loss_coefficient", "1", nonnegative=True)
    c.number("heat_loss_ua_w_k", "W/K", nonnegative=True)
  elif c.kind == "valve":
    c.number("kv_m3_h", "m3/h", positive=True)
  else:
    for prefix in ("chilled_", "cooling_") if c.kind == "chiller" else ("",):
      c.number(prefix + "nominal_pressure_drop_pa", "Pa", nonnegative=True)
      c.number(prefix + "nominal_flow_m3_s", "m3/s", positive=True)
  if c.kind == "pump":
    c.number("shutoff_head_m", "m", positive=True)
    c.number("curve_m_per_m6_s2", "s2/m5", positive=True)
    if c.number("check_valve", "1") not in {0, 1}:
      raise PhysicsInputError(f"{c.identity}: check_valve must be 0 or 1.")
    if not 0 < c.number("efficiency", "1") <= 1 or not 0 <= c.number("water_heat_fraction", "1") <= 1:
      raise PhysicsInputError(f"{c.identity}: efficiency must be in (0, 1] and heat fraction in [0, 1].")
  elif c.kind == "chiller":
    for name, unit in (("capacity_kw", "kW"), ("nominal_cop", "1"), ("response_tau_s", "s"), ("minimum_flow_m3_s", "m3/s")):
      c.number(name, unit, positive=True)
  elif c.kind == "tower":
    c.number("ua_w_k", "W/K", positive=True)
  elif c.kind == "tank":
    c.number("heat_loss_ua_w_k", "W/K", nonnegative=True)


def _bindings(raw, label):
  if not isinstance(raw, dict):
    raise PhysicsInputError(f"{label}: parameters must be an object.")
  result = {}
  for name, value in sorted(raw.items()):
    if not isinstance(value, dict):
      raise PhysicsInputError(f"{label}.{name}: parameter binding must be an object.")
    if value.get("state") == "failed":
      raise PhysicsInputError(f"{label}.{name}: parameter has explicit failed state.")
    if value.get("state") not in {"known", "assumed"}:
      raise PhysicsUnresolved(f"{label}.{name}: executable physics requires a known or assumed binding.")
    number = value.get("value")
    if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number):
      raise PhysicsInputError(f"{label}.{name}: value must be a finite number.")
    if not all(isinstance(value.get(k), str) and value[k].strip() for k in ("unit", "source")):
      raise PhysicsInputError(f"{label}.{name}: unit and source are required.")
    result[name] = Parameter(float(number), value["unit"], value["state"], value["source"])
  return MappingProxyType(result)


def _descriptor(root, reference, source, assets, records):
  if not isinstance(reference, str) or not reference:
    raise PhysicsInputError("Behavior reference must be a non-empty local path.")
  path = (root / reference).resolve()
  if not any(path.is_relative_to(base.resolve()) for base in (source, assets)):
    raise PhysicsInputError(f"Out-of-project behavior descriptor: {reference}")
  if not path.is_file():
    raise PhysicsUnresolved(f"Missing behavior descriptor: {reference}")
  try:
    value = _load_json(path.read_text(encoding="utf-8"))
  except (OSError, ValueError) as error:
    raise PhysicsInputError(f"Invalid physics descriptor {reference}: {error}") from error
  if not isinstance(value, dict) or set(value) != {"schema_version", "kind", "version", "assumptions"}:
    raise PhysicsInputError(f"{reference}: descriptor requires schema_version, kind, version and assumptions.")
  if type(value["schema_version"]) is not int or value["schema_version"] != 1:
    raise PhysicsInputError(f"{reference}: unsupported descriptor version.")
  if not isinstance(value["version"], str) or not value["version"] or not isinstance(value["assumptions"], list) or not all(isinstance(a, str) and a for a in value["assumptions"]):
    raise PhysicsInputError(f"{reference}: version and explicit assumptions are required.")
  records[reference] = file_digest(path)
  return value


def compile_model(path: Path) -> PhysicalModel:
  root = path.expanduser().resolve()
  info = inspect_project(root)
  if info is None:
    raise PhysicsInputError("An initialized engineering project is required.")
  source, assets = root / info.source, root / info.assets
  sdl_path = source / "system.sdl.json"
  sdl = _load_json(sdl_path.read_text(encoding="utf-8"))
  root_name = sdl["root"]
  system = sdl["components"][root_name]
  records = {sdl_path.relative_to(root).as_posix(): file_digest(sdl_path)}
  behavior = system.get("behavior", {})
  descriptor = _descriptor(root, behavior.get("reference"), source, assets, records)
  if descriptor["kind"] != "water-network":
    raise PhysicsInputError("Root behavior must select a water-network model.")
  if behavior.get("state") == "failed":
    raise PhysicsInputError("Root behavior has explicit failed state.")
  if behavior.get("state") not in {"known", "assumed"}:
    raise PhysicsUnresolved("Root behavior requires a known/assumed water-network model.")
  water = Component(root_name, root_name, "water-network", _bindings(behavior.get("parameters"), root_name), MappingProxyType({}), behavior["reference"], records[behavior["reference"]])
  components = []
  assumptions = list(descriptor["assumptions"])
  for name, instance in sorted(system.get("instances", {}).items()):
    definition = sdl["components"][instance["component"]]
    identity = f"{root_name}.{name}"
    if definition.get("instances"):
      raise PhysicsInputError(f"{identity}: recursive physical-network expansion requires an explicit boundary contract.")
    behavior = definition.get("behavior", {})
    descriptor = _descriptor(root, behavior.get("reference"), source, assets, records)
    if descriptor["kind"] not in KINDS:
      raise PhysicsInputError(f"{identity}: unsupported constitutive model.")
    if behavior.get("state") == "failed":
      raise PhysicsInputError(f"{identity}: constitutive model has explicit failed state.")
    if behavior.get("state") not in {"known", "assumed"}:
      raise PhysicsUnresolved(f"{identity}: unresolved constitutive model.")
    parameters = _bindings({**behavior.get("parameters", {}), **instance.get("parameters", {})}, identity)
    ports = MappingProxyType({p: v["kind"] for p, v in sorted(definition.get("ports", {}).items())})
    if not ports or any(medium not in {"chilled-water", "cooling-water"} for medium in ports.values()):
      raise PhysicsInputError(f"{identity}: only typed liquid-water ports are supported by this backend.")
    components.append(Component(identity, instance["component"], descriptor["kind"], parameters, ports, behavior["reference"], records[behavior["reference"]]))
    assumptions.extend(descriptor["assumptions"])
  if not components:
    raise PhysicsInputError("A physical network must contain components.")
  parent = {f"{c.identity}.{p}": f"{c.identity}.{p}" for c in components for p in c.ports}

  def find(p):
    if p not in parent:
      raise PhysicsInputError(f"Unknown physical port: {p}")
    while parent[p] != p:
      p = parent[p]
    return p

  def union(a, b):
    a, b = find(a), find(b)
    parent[max(a, b)] = min(a, b)

  port_kinds = {f"{c.identity}.{p}": kind for c in components for p, kind in c.ports.items()}
  for connection in system.get("connections", []):
    a, b = f"{root_name}.{connection['from']}", f"{root_name}.{connection['to']}"
    if a not in port_kinds or b not in port_kinds or port_kinds[a] != port_kinds[b]:
      raise PhysicsInputError("Connections must join existing ports of the same medium.")
    union(a, b)
  for component in components:
    validate_component(component)
    if component.kind == "junction":
      members = [f"{component.identity}.{p}" for p in component.ports]
      if len(set(component.ports.values())) != 1:
        raise PhysicsInputError(f"{component.identity}: a junction cannot join different media.")
      for member in members[1:]:
        union(members[0], member)
  port_junctions = {p: find(p) for p in sorted(parent)}
  branches = []
  for component in components:
    if component.kind == "junction":
      continue
    circuits = ("chilled", "cooling") if component.kind == "chiller" else ("water",)
    expected = {f"{c}-{direction}" for c in circuits for direction in ("in", "out")} if component.kind == "chiller" else {"inlet", "outlet"}
    if set(component.ports) != expected:
      raise PhysicsInputError(f"{component.identity}: constitutive port contract mismatch.")
    for circuit in circuits:
      a, b = (f"{circuit}-in", f"{circuit}-out") if circuit != "water" else ("inlet", "outlet")
      if component.ports[a] != component.ports[b]:
        raise PhysicsInputError(f"{component.identity}: branch media differ.")
      if component.kind == "pipe":
        diameter = component.number("diameter_m", "m", positive=True)
        length = component.number("length_m", "m", positive=True)
        volume = math.pi * diameter**2 * length / 4
        segments_value = component.number("segments", "1", positive=True)
        if not segments_value.is_integer() or segments_value > 100:
          raise PhysicsInputError(f"{component.identity}: segments must be an integer within [1, 100].")
        segments = int(segments_value)
        wall = component.number("wall_capacity_j_k", "J/K", nonnegative=True)
      else:
        volume = component.number(f"{circuit}_volume_m3" if component.kind == "chiller" else "volume_m3", "m3", positive=True)
        segments, wall = 1, 0.0
      branches.append(Branch(component.identity + (f"#{circuit}" if circuit != "water" else ""), component.identity, circuit,
                             f"{component.identity}.{a}", f"{component.identity}.{b}", component.ports[a], volume, wall, segments))
  return PhysicalModel(root_name, tuple(components), tuple(branches), tuple(sorted(set(port_junctions.values()))),
                       MappingProxyType(port_junctions), tuple((f"{root_name}.{c['from']}", f"{root_name}.{c['to']}") for c in system.get("connections", [])), water.number("density_kg_m3", "kg/m3", positive=True),
                       water.number("cp_j_kg_k", "J/(kg.K)", positive=True), water.number("viscosity_pa_s", "Pa.s", positive=True),
                       water.number("gravity_m_s2", "m/s2", positive=True), canonical_digest({"sdl": sdl, "resources": records}),
                       tuple(sorted(records.items())), tuple(sorted(set(assumptions))))
