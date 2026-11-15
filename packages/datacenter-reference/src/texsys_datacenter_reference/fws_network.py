"""Compile and quantify the SDL-bound facility water mains and branches."""

from collections import defaultdict
import csv
import json
import math

from pxr import Gf, Sdf, UsdGeom

from texsys.targets import TargetResult
from texsys_spatial import Space

from .fws_design import _port
from .spatial_design import MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json


MODEL = f"{MODELS}/fws-network.json"
LEGS = ("supply", "return")


def _bindings(layout, system, net):
  assembly = system["components"][system["root"]]
  selected = assembly["nets"][net]
  if selected["kind"] != "fws":
    raise ValueError("The declared SDL network must carry facility water.")
  endpoints = {
    name: ("plants" if module == "cooling-container" else "terminals")
    for name, obj in layout["objects"].items() if (module := obj["module"].split("/")[-2])
    in {"cooling-container", "it-container"}
  }
  results = {"plants": {}, "terminals": {}}
  used = set()
  for name, role in sorted(endpoints.items()):
    port_name = f"{name}.fws_"
    candidates = {
      ("supply", "plants"): (port_name + "supply", "FwsSupplyHeader.in_"),
      ("return", "plants"): ("FwsReturnHeader.out_", port_name + "return"),
      ("supply", "terminals"): ("FwsSupplyHeader.out_", port_name + "supply"),
      ("return", "terminals"): (port_name + "return", "FwsReturnHeader.in_"),
    }
    records = {}
    for leg in LEGS:
      a, b = candidates[leg, role]
      matching = [c for c in assembly["connections"] if c["id"] in selected["connections"]
                  and (c["from"].startswith(a) if a.endswith("_") else c["from"] == a)
                  and (c["to"].startswith(b) if b.endswith("_") else c["to"] == b)]
      if len(matching) != 1:
        raise ValueError(f"{name} needs one {leg} connection on the declared common FWS headers.")
      edge, = matching
      used.add(edge["id"])
      family = "cooling-container" if role == "plants" else "it-container"
      records[leg] = {**edge, "point_m": _port(layout, port_name + leg, family)}
    results[role][name] = records
  if (not all(results.values()) or used != set(selected["connections"])
      or len(used) != len(selected["connections"])):
    raise ValueError("The physical FWS network must resolve every unique SDL connection to all plants and outlets.")
  return results


def _pipe_types(source):
  types = {}
  for name, row in source["pipe_types"].items():
    entry = {key: number(row[key], f"{name}.{key}", zero=key in {"roughness_m", "insulation_m"})
             for key in ("inside_m", "outside_m", "insulation_m", "roughness_m", "max_velocity_m_s")}
    if not entry["roughness_m"] < entry["inside_m"] < entry["outside_m"]:
      raise ValueError(f"{name}: require roughness below bore and bore below outside diameter.")
    types[name] = {**entry, "envelope_m": entry["outside_m"] + 2*entry["insulation_m"]}
  if set(types) != {"header", "spine", "plant", "row", "terminal"}:
    raise ValueError("FWS sizing must declare header, spine, plant, row and terminal materials.")
  return types


def _compile_leg(leg, cfg, ports, pipes, fittings_k):
  prefix = "S" if leg == "supply" else "R"
  spine = number(cfg["spine_x_m"], "spine east coordinate")
  station = number(cfg["plant_header_y_m"], "plant header north coordinate")
  tier = number(cfg["tier_m"], "pipe centreline elevation")
  offset = number(cfg["outlet_offset_m"], "outlet approach")
  nodes, segments, parents, incidence = {}, {}, {}, defaultdict(list)

  def at(label, point):
    key = prefix + "-" + label
    nodes[key] = point
    return key

  def connect(inner, outer, kind, size, *, closing=False):
    a, b = nodes[inner], nodes[outer]
    axis = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if not math.isclose(x, y, abs_tol=1e-9)]
    if len(axis) != 1 or (outer in parents and not closing):
      raise ValueError(f"{inner} to {outer}: require a straight nonzero pipe with a unique routing parent.")
    identity = f"{prefix}-{len(segments) + 1:03}"
    if kind != "bypass":
      parents[outer] = (inner, identity)
    first, last = (inner, outer) if (kind != "plant") == (leg == "supply") else (outer, inner)
    segments[identity] = {"leg": leg, "kind": kind, "type": size, "from": first, "to": last, "inner": inner,
                          "outer": outer, "points_m": [nodes[first], nodes[last]], "axis": axis[0],
                          "length_m": math.dist(a, b), "hydraulic_section": pipes[size]}
    incidence[inner].append(identity)
    incidence[outer].append(identity)
    return identity

  root = at("HEADER", (spine, station, tier))
  bindings = {}
  last = root
  ordered = sorted(ports["plants"].items(), key=lambda item: item[1][leg]["point_m"][0])
  for name, record in ordered:
    x, y, z = record[leg]["point_m"]
    if x <= nodes[last][0] or y <= station or z >= tier:
      raise ValueError(f"{name} must approach its rising plant lead from north of the {leg} header.")
    tap = at(name + "-TEE", (x, station, tier))
    foot = at(name + "-ELBOW", (x, station, z))
    bound = at(name + "-PORT", (x, y, z))
    connect(last, tap, "plant", "header")
    connect(tap, foot, "plant", "plant")
    connect(foot, bound, "plant", "plant")
    bindings[name] = bound
    last = tap
  far_plant = last

  rows = defaultdict(list)
  for name, record in ports["terminals"].items():
    x, y, z = record[leg]["point_m"]
    if x <= spine or y >= station or z >= tier or y <= offset:
      raise ValueError(f"{name} must approach its falling outlet from east of the {leg} spine.")
    rows[y].append((x, name, y, z))
  last = root
  for row_index, (y, values) in enumerate(sorted(rows.items(), reverse=True), 1):
    route_y = y - offset
    takeoff = at(f"ROW{row_index}-TEE", (spine, route_y, tier))
    connect(last, takeoff, "distribution", "spine")
    last = takeoff
    for x, name, port_y, z in sorted(values):
      tap = at(name + "-TEE", (x, route_y, tier))
      elbow = at(name + "-ELBOW", (x, route_y, z))
      port = at(name + "-PORT", (x, port_y, z))
      connect(takeoff, tap, "distribution", "row")
      connect(tap, elbow, "distribution", "terminal")
      connect(elbow, port, "distribution", "terminal")
      takeoff = tap
      bindings[name] = port

  bypass = []
  if "bypass" in cfg:
    route = cfg["bypass"]
    east = number(route["east_x_m"], "bypass east coordinate")
    south = number(route["south_y_m"], "bypass south coordinate")
    if east <= max(x for x, _, _ in nodes.values()) or south >= nodes[last][1]:
      raise ValueError(f"{leg}: route the bypass beyond the outlet and plant extents and south of the final row.")
    corners = [far_plant, at("BYPASS-NE", (east, station, tier)), at("BYPASS-SE", (east, south, tier)),
               at("BYPASS-SW", (spine, south, tier)), last]
    bypass = [connect(a, b, "bypass", "spine", closing=b == last) for a, b in zip(corners, corners[1:])]

  fittings = {}
  for node, attached in incidence.items():
    count = len(attached)
    distinct_axes = {segments[identity]["axis"] for identity in attached}
    if count > 3 or (count == 3 and len(distinct_axes) != 2):
      raise ValueError(f"{node}: expected a tee, elbow, straight connection or port.")
    if count == 3 or count == len(distinct_axes) == 2:
      kind = "tee" if count == 3 else "elbow"
      fittings[node] = {"kind": kind, "k": fittings_k[kind], "point_m": nodes[node], "segments": sorted(attached)}
  for segment in segments.values():
    inner = segment["inner"]
    segment["minor_k"] = (0 if inner == root and segment["kind"] == "plant"
                           else fittings.get(inner, {}).get("k", 0))
    segment.pop("axis")

  def path(bound):
    result = []
    while bound != root:
      bound, pipe = parents[bound]
      result.append(pipe)
    return list(reversed(result))

  return {"nodes": nodes, "segments": segments, "fittings": fittings, "root": root, "bindings": bindings,
          "paths": {name: path(bound) for name, bound in bindings.items()}, "bypass": bypass}


def _isolation(segments, config):
  inset = number(config["end_inset_m"], "isolation valve setback")
  length = number(config["body_length_m"], "isolation valve body length")
  clearance = number(config["body_envelope_factor"], "isolation valve envelope multiplier")
  loss = number(config["open_minor_k"], "open isolation valve loss coefficient", zero=True)
  if inset <= length/2 or clearance < 1:
    raise ValueError("Keep the isolation valve body inside the protected pipe and declare its full envelope.")
  valves = {}
  for key, pipe in segments.items():
    if pipe["type"] not in {"header", "spine"}:
      continue
    span = pipe["length_m"]
    if span <= 2*inset + length:
      raise ValueError(f"{key}: the two isolation valves need distinct installation space.")
    a, b = pipe["points_m"]
    axis = next(i for i in range(3) if a[i] != b[i])
    ids = [f"IV-{key}-A", f"IV-{key}-B"]
    for index, tag in enumerate(ids):
      start, stop = (a, b) if index == 0 else (b, a)
      point = [x + (y-x)*inset/span for x, y in zip(start, stop, strict=True)]
      diameter = pipe["hydraulic_section"]["envelope_m"] * clearance
      valves[tag] = {"segment": key, "point_m": point, "normally_open": True, "minor_k": loss,
                     "size_m": [length if i == axis else diameter for i in range(3)]}
    pipe["valves"] = ids
    pipe["minor_k"] += 2*loss
  return valves


def pipe_envelope(record):
  a, b = record["points_m"]
  radius = record["hydraulic_section"]["envelope_m"] / 2
  return Space(tuple(min(x, y) - (radius if x == y else 0) for x, y in zip(a, b, strict=True)),
               tuple(max(x, y) + (radius if x == y else 0) for x, y in zip(a, b, strict=True)))


def add_network_geometry(stage, segments, valves):
  for identity, record in segments.items():
    space = pipe_envelope(record)
    pipe = UsdGeom.Cube.Define(stage, "/AIDC/FWSNetwork/" + identity.replace("-", "_"))
    pipe.CreateSizeAttr(1)
    pipe.AddTranslateOp().Set(Gf.Vec3d(*space.center_m))
    pipe.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*space.size_m))
    color = (0.16, 0.48, 0.78) if record["leg"] == "supply" else (0.75, 0.29, 0.18)
    pipe.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    pipe.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)
  for identity, record in valves.items():
    valve = UsdGeom.Cube.Define(stage, "/AIDC/FWSIsolation/" + identity.replace("-", "_"))
    valve.CreateSizeAttr(1)
    valve.AddTranslateOp().Set(Gf.Vec3d(*record["point_m"]))
    valve.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*record["size_m"]))
    valve.CreateDisplayColorAttr([Gf.Vec3f(.26, .25, .23)])
    valve.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)


def _takeoff(context, data):
  path = context.build_path / "schedules/aidc-fws-pipes.csv"
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("id", "service", "from", "to", "type", "length_m", "bore_mm", "od_mm", "insulation_mm",
                    "start_z_m", "end_z_m", "local_k", "sdl_links"))
    for name, record in sorted(data["segments"].items()):
      spec = record["hydraulic_section"]
      a, b = record["points_m"]
      table.writerow((name, record["leg"], record["from"], record["to"], record["type"],
                      round(record["length_m"], 6), 1000*spec["inside_m"], 1000*spec["outside_m"],
                      1000*spec["insulation_m"], a[2], b[2], record["minor_k"], " ".join(record["sdl_links"])))
  fitting_path = path.with_name("aidc-fws-fittings.csv")
  with fitting_path.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("id", "kind", "x_m", "y_m", "z_m", "local_k", "adjoining_segments", "envelope_mm"))
    for key, record in sorted(data["fittings"].items()):
      table.writerow((key, record["kind"], *record["point_m"], record["k"], " ".join(record["segments"]), ""))
    for key, record in sorted(data["valves"].items()):
      table.writerow((key, "normally-open-isolation-valve", *record["point_m"], record["minor_k"], record["segment"],
                      round(max(record["size_m"])*1000, 2)))
    for key, record in sorted(data["penetrations"].items()):
      table.writerow((key, "roof-opening-reservation", *record["point_m"], "", record["segment"],
                      round(record["square_width_m"]*1000, 2)))
  return artifact(context, "fws-pipes", path), artifact(context, "fws-fittings", fitting_path)


def build_network(context):
  path = source_path(context, "fws-network.json")
  if not path.is_file():
    return missing_input(context, path, "fws-network")
  config = read_versioned(path)
  if config.get("state") != "assumed" or not isinstance(config.get("basis"), str) or not config["basis"].strip():
    raise ValueError("FWS geometry needs an explicit assumed engineering basis.")
  layout = staged_json(context, f"{MODELS}/layout.json")
  site = staged_json(context, f"{MODELS}/site.json")
  system = json.loads(path.with_name("system.sdl.json").read_text(encoding="utf-8"))
  ports = _bindings(layout, system, config["net"])
  pipes = _pipe_types(config)
  coefficients = {key: number(config["minor_k"][key], key + " loss coefficient", zero=True)
                  for key in ("elbow", "tee")}
  legs = {leg: _compile_leg(leg, config["legs"][leg], ports, pipes, coefficients) for leg in LEGS}
  segments = {name: item for leg in legs.values() for name, item in leg["segments"].items()}
  fittings = {name: item for leg in legs.values() for name, item in leg["fittings"].items()}
  nodes = {name: point for leg in legs.values() for name, point in leg["nodes"].items()}
  paths = {role: {item: {leg: legs[leg]["paths"][item] for leg in LEGS} for item in entries}
           for role, entries in ports.items()}
  bypass = {leg: data["bypass"] for leg, data in legs.items()}
  reverse = defaultdict(list)
  for role, entries in paths.items():
    for item, routes in entries.items():
      for leg, route in routes.items():
        for record in route:
          reverse[record].append(ports[role][item][leg]["id"])
  for leg, alternate in bypass.items():
    for key in alternate:
      reverse[key] = [owner[leg]["id"] for entries in ports.values() for owner in entries.values()]
  valves = _isolation(segments, config["isolation_valves"]) if "isolation_valves" in config else {}
  xmin, ymin, xmax, ymax = site["boundary_m"]
  for key, segment in segments.items():
    segment["sdl_links"] = sorted(reverse[key])
    envelope = pipe_envelope(segment)
    if (not reverse[key] or envelope.minimum_m[0] < xmin or envelope.minimum_m[1] < ymin
        or envelope.maximum_m[0] > xmax or envelope.maximum_m[1] > ymax):
      raise ValueError(f"{key}: every FWS pipe must have a served endpoint and fit the site.")
  levels = staged_json(context, f"{MODELS}/levels.json")
  terminal_zone = config["terminal_zone"]
  zone = levels["zones"][terminal_zone]
  roof = zone["maximum_m"][2]
  extra = number(config["opening_allowance_m"], "opening allowance on each side")
  penetrations = {}
  for terminal, routes in paths["terminals"].items():
    if layout["objects"][terminal]["zone"] != terminal_zone:
      raise ValueError(f"{terminal}: no roof opening model for its assigned zone.")
    x0, y0, _ = zone["minimum_m"]
    x1, y1, _ = zone["maximum_m"]
    for leg, route in routes.items():
      for key in route:
        a, b = segments[key]["points_m"]
        if (a[0] == b[0] and a[1] == b[1] and min(a[2], b[2]) < roof < max(a[2], b[2])
            and x0 <= a[0] <= x1 and y0 <= a[1] <= y1):
          identity = f"{terminal}-{leg}-ROOF"
          if identity in penetrations:
            raise ValueError(f"Repeated roof penetration along {terminal} {leg}.")
          penetrations[identity] = {"segment": key, "point_m": [a[0], a[1], roof],
                                    "square_width_m": segments[key]["hydraulic_section"]["envelope_m"] + 2*extra}
      if f"{terminal}-{leg}-ROOF" not in penetrations:
        raise ValueError(f"{terminal} {leg} is missing an approach through the declared roof datum.")
  data = {"state": "assumed", "basis": config["basis"], "frame": layout["frame"], "nodes": nodes,
          "segments": segments, "fittings": fittings, "penetrations": penetrations, "ports": ports, "paths": paths,
          "valves": valves, "bypass": bypass,
          "roots": {leg: item["root"] for leg, item in legs.items()}, "terminal_zone": terminal_zone}
  result = model_report(context, "fws-network", data)
  return TargetResult(result.status, (*result.artifacts, *_takeoff(context, data)), result.diagnostics)
