"""Compile the room TCS hydraulic model from SDL connections and placed ports."""

from dataclasses import asdict
from itertools import pairwise
from math import dist
from pathlib import Path

from texsys.formal import _load_json
from texsys_hvac_dynamics.hybrid import LiquidLoop
from texsys_hvac_dynamics.technology_cooling import RackCircuit, TechnologyCooling

from .bay_layout import rack_plan


def _read(source: Path, name):
  return _load_json((source / f"{name}.study.json").read_text(encoding="utf-8"))


def _rack_layout(source, models):
  _, _, _, plan = rack_plan(_read(source, "floor"))
  air = _read(source, "air-environment")["layout"]
  if plan.keys() != air["rack_fronts"].keys():
    raise ValueError("TCS requires the same rack identities in structural and air layouts.")
  racks = {}
  for rack, placement in plan.items():
    model = models[rack]
    spec = model["specification"]
    envelope = spec["envelope_m"]
    if (model["model"] != "independent-dual-circuit-rack" or tuple(envelope[:2]) != tuple(placement["size_m"])
        or envelope[2] != air["rack_height_m"]):
      raise ValueError(f"{rack}: rack envelope or behavior differs from the declared layout.")
    front = air["rack_fronts"][rack]
    if front not in ("+Y", "-Y"):
      raise ValueError(f"{rack}: TCS requires a rack front on a Y face.")
    turned = front == "+Y"
    ox, oy = placement["origin_m"]
    ports = {}
    for name, interface in spec["interfaces"].items():
      if not name.startswith("tcs_"):
        continue
      x, y, z = interface["position_m"]
      if interface["normal"] != [0, 0, 1]:
        raise ValueError(f"{rack}.{name}: overhead branches require a top port.")
      ports[name] = (ox + (envelope[0] - x if turned else x), oy + (envelope[1] - y if turned else y), z)
    racks[rack] = {"entry": model["entry"], "module_version": model["module_version"], "envelope_m": envelope,
                   "angle_deg": 180 if turned else 0, "origin_m": (ox, oy, 0), "ports_m": ports}
  return racks


def _leg_connections(edges, cdu, racks, leg):
  supply = leg == "supply"
  cdu_port = f"{cdu}.tcs_{leg}"
  oriented = [(key, start, end) if supply else (key, end, start) for key, start, end in edges]
  [(_, _, header)] = [edge for edge in oriented if edge[1] == cdu_port]
  outlets = {}
  for key, start, end in oriented:
    rack, _, port = end.partition(".")
    if start.partition(".")[0] == header.partition(".")[0] and rack in racks:
      if rack in outlets:
        raise ValueError(f"{cdu}/{leg}: multiple SDL connections to {rack}.")
      outlets[rack] = (key, port)
  if outlets.keys() != racks.keys():
    raise ValueError(f"{cdu}/{leg}: a TCS header must connect to every rack.")
  return outlets


def _add_leg(cdu, leg, layout, rack_layout, cdu_layout, outlets, routes, train, pipes, settings):
  key = f"{cdu}/{leg}"
  cdu_port = f"tcs_{leg}"
  start = tuple(cdu_layout["ports_m"][cdu_port])
  if cdu_layout["port_normals"][cdu_port] != [0, -1, 0]:
    raise ValueError(f"{key}: TCS route expects an outward -Y CDU port.")
  rise, spine, tier = (layout[name] for name in ("rise_y_m", "spine_x_m", "tier_z_m"))
  rows = {}
  for rack, (_, port) in outlets.items():
    point = rack_layout[rack]["ports_m"][port]
    if not (0 < point[0] < spine and 0 < point[1] < rise < start[1] and max(point[2], start[2]) < tier):
      raise ValueError(f"{key}: CDU and rack ports must fit under their overhead TCS branch.")
    rows.setdefault(round(point[1], 9), []).append((rack, point))

  def add(name, a, b, points, pipe_type):
    ident = f"{key}/{name}"
    pipe = settings[pipe_type]
    points = tuple(points)
    if leg == "return":
      a, b, points = b, a, points[::-1]
    lengths = [dist(x, y) for x, y in pairwise(points)]
    if not lengths or any(length <= 1e-9 for length in lengths):
      raise ValueError(f"{ident}: TCS pipe length must be positive.")
    length = sum(lengths)
    train["segments"].append({"id": ident, "start": a, "end": b, "leg": leg,
                              "capacity_kg_s": pipe["capacity_kg_s"]})
    pipes[ident] = {"length_m": length, **{name: pipe[name] for name in
                   ("diameter_m", "roughness_m", "max_velocity_m_s")},
                   "minor_loss_coefficient": pipe["minor_loss_coefficient"] + settings["bend_loss"] * (len(points) - 2)}
    routes[ident] = {"points_m": points, "outside_m": pipe["outside_m"], "cdu": cdu, "leg": leg,
                     "nodes": [a, b], "length_m": length}

  head = "Head"
  center = (spine, rise, tier)
  add("header", cdu, head, [start, (start[0], rise, start[2]), (start[0], rise, tier), center], "header")
  for i, (_, terminals) in enumerate(sorted(rows.items(), reverse=True), 1):
    row = f"Row-{i}"
    y = terminals[0][1][1]
    row_point = (spine, y, tier)
    add(f"trunk-{i}", head, row, [center, row_point], "header")
    head, center = row, row_point
    for rack, point in sorted(terminals, key=lambda item: item[1][0], reverse=True):
      tap = f"Tap-{rack}"
      tap_point = (point[0], y, tier)
      add(f"branch-{rack}", row, tap, [row_point, tap_point], "branch")
      add(f"drop-{rack}", tap, rack, [tap_point, point], "drop")
      row, row_point = tap, tap_point
  return {rack: {"connection": connection, "port": port, "point_m": rack_layout[rack]["ports_m"][port]}
          for rack, (connection, port) in outlets.items()}


def prepare_tcs(source, cdus, ratings, models, equipment, net_edges):
  if not (source / "tcs.study.json").is_file():
    return None
  config = _read(source, "tcs")
  if config["schema_version"] != 1 or config["state"] != "assumed" or not config["basis"].strip():
    raise ValueError("TCS requires schema version 1 and an assumed design basis.")
  racks = _rack_layout(source, models)
  if cdus.keys() != config["trains"].keys() or not racks:
    raise ValueError("TCS routes must cover all room CDUs and racks.")
  for name in ("header", "branch", "drop"):
    pipe = config["pipe_types"][name]
    if pipe["outside_m"] <= pipe["diameter_m"]:
      raise ValueError(f"TCS {name} pipe outside diameter must exceed its bore.")
  routes, pipes, trains, banks, outlets = {}, {}, {}, {}, {}
  used_ports = set()
  for cdu, layout in sorted(config["trains"].items()):
    trains[cdu] = {"plant_node": cdu, "segments": [], "terminals": dict(zip(racks, racks, strict=True))}
    banks[cdu] = layout["pump"]
    edges = net_edges[cdus[cdu]["tcs_net"]]
    if len(edges) != 2 * (len(racks) + 1):
      raise ValueError(f"{cdu}: expected paired headers and one SDL connection per rack and leg.")
    connections = {leg: _leg_connections(edges, cdu, racks, leg) for leg in ("supply", "return")}
    ports = {(rack, port) for links in connections.values() for rack, (_, port) in links.items()}
    if len(ports) != 2 * len(racks) or used_ports.intersection(ports):
      raise ValueError("A/B TCS circuits must use separate rack ports.")
    used_ports.update(ports)
    outlets[cdu] = {}
    for leg, connections_by_rack in connections.items():
      outlets[cdu][leg] = _add_leg(cdu, leg, layout[leg], racks, equipment[cdu], connections_by_rack,
                                   routes, trains[cdu], pipes, config["pipe_types"])
  circuits = {}
  for rack in racks:
    parameters = models[rack]["parameters"]

    def parameter(name, unit):
      binding = parameters[name]
      if binding["unit"] != unit or binding["state"] not in ("known", "assumed"):
        raise ValueError(f"{rack}.{name}: expected a resolved parameter in {unit}.")
      return binding["value"]

    circuit = RackCircuit(
      parameter("rated_circuit_flow", "kg/s"), parameter("maximum_circuit_flow", "kg/s"),
      {"manifold_pair": parameter("manifold_pair_drop", "kPa"),
       "quick_disconnect_pair": parameter("quick_disconnect_pair_drop", "kPa"),
       "cold_plate_circuit": parameter("cold_plate_circuit_drop", "kPa")},
      parameter("maximum_inlet_temperature", "degC"), parameter("maximum_return_temperature", "degC"),
    )
    circuits[rack] = asdict(circuit)
  return {"basis": config["basis"], "routes": routes, "outlets": outlets, "rack_layout": racks,
          "trains": trains, "pipes": pipes, "banks": banks, "circuits": circuits, "fluid": config["fluid"],
          "loops": {cdu: asdict(rating.technology) for cdu, rating in ratings.items()}}


def tcs_solver(report):
  return TechnologyCooling(report["trains"], report["pipes"], report["banks"],
                           {key: LiquidLoop(**value) for key, value in report["loops"].items()},
                           {key: RackCircuit(**value) for key, value in report["circuits"].items()},
                           **report["fluid"])
