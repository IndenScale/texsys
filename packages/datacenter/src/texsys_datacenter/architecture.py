"""Bind reusable architectural families to the building's placed room and level identities."""

from dataclasses import asdict
from itertools import pairwise

from texsys.formal import _load_json, resolve_sdl_imports
from texsys.library import library_entries
from texsys.sdl_catalog import read_sdl_catalog
from texsys_architecture import Opening, Shaft, Space, WallPanel, switchback

from .unit import _identifiers, _number


def read_architecture(source):
  path = source / "architecture.study.json"
  if not path.is_file():
    return None
  data = _load_json(path.read_text(encoding="utf-8"))
  if data["schema_version"] != 1 or data["state"] != "assumed" or not data["basis"].strip():
    raise ValueError("Architecture requires an assumed version 1 study.")
  return data


def _families(root, source):
  catalog = read_sdl_catalog(source, root)
  filename = "architecture.sdl.json"
  if catalog.diagnostics:
    raise ValueError("Architecture requires a valid locked SDL catalog.")
  aliases, errors = resolve_sdl_imports(catalog.documents, library_entries(set(catalog.documents)), filename)
  if errors:
    raise ValueError(f"Unresolved architecture families: {errors}.")
  doc = catalog.documents[filename]
  families = {}
  generators = {"room": "Space", "wall": "WallPanel", "door": "Opening", "shaft": "Shaft", "stair": "switchback"}
  for item in doc["components"][doc["root"]]["instances"].values():
    entry, name = aliases[item["component"]]
    manifest = catalog.modules[entry]
    [asset] = [record["path"] for record in manifest["assets"].values() if record["kind"] == "specification"]
    spec = _load_json((catalog.files[entry].parent / asset).read_text(encoding="utf-8"))
    if (not entry.startswith("@architecture/") or spec["kind"] in families or spec["coordinate_unit"] != "m"
        or spec["state"] != "assumed" or spec["implementation"] != f"texsys_architecture.{generators.get(spec['kind'])}"):
      raise ValueError("Architecture families must be distinct locked concept modules in meters.")
    families[spec["kind"]] = {"entry": entry, "root": name, "version": manifest["version"]}
  if families.keys() != generators.keys():
    raise ValueError("Architecture requires room, wall, door, shaft and stair families.")
  return families


def compile_architecture(root, source, config, frame, zones, levels, floor, unit_height):
  families = _families(root, source)
  width, depth = floor
  ordered = sorted(levels.items(), key=lambda item: item[1])
  top = _number(config["top_m"], "architecture top", minimum=ordered[-1][1] + unit_height, inclusive=True)
  partition = _number(config["partition_x_m"], "compartment wall x")
  if partition >= width:
    raise ValueError("The compartment wall must be within the floor.")
  bottom = ordered[0][1]
  stores = {key: (z, upper) for (key, z), (_, upper) in pairwise([*ordered, ("Roof", top)])}
  thickness = config["wall_thickness_m"]
  compartment_doors = config["compartment_door_y_m"]
  if not 0 < compartment_doors["South"] < compartment_doors["North"] < depth:
    raise ValueError("Compartment doors must be ordered along the wall.")
  rooms, walls, doors, shafts, stairs = {}, {}, {}, {}, {}

  def room(identity, role, xy, z, zone=None):
    x, y, sx, sy = xy
    rooms[identity] = {**asdict(Space((x, y, z[0]), (x + sx, y + sy, z[1]))), "role": role,
                       "zone": zone, "definition": families["room"]["entry"]}

  def add_panel(identity, role, panel):
    walls[identity] = {**asdict(panel), "role": role, "definition": families["wall"]["entry"]}

  def wall(identity, role, axis, station, extent, z, assembly):
    add_panel(identity, role, WallPanel(axis, station, (extent, z), thickness[assembly]))

  def door(identity, host, type_, center_y):
    panel = walls[host]
    clear, height = config["door_sizes_m"][type_]
    base = panel["extent_m"][1][0]
    cut = Opening((center_y - clear / 2, center_y + clear / 2), (base, base + height))
    wall_shape = WallPanel(**{key: panel[key] for key in ("axis", "station_m", "extent_m", "thickness_m")})
    if not wall_shape.contains(cut):
      raise ValueError(f"{identity}: door opening lies outside its host wall.")
    doors[identity] = {"host": host, "type": type_, "clear_width_m": clear, "height_m": height,
                       "center_m": (panel["station_m"], center_y, base + height / 2),
                       "opening": asdict(cut), "definition": families["door"]["entry"]}

  for level, vertical in stores.items():
    room(f"{level}/Hall", "it-hall", (0, 0, partition, depth), vertical)
    room(f"{level}/Services", "service-compartment", (partition, 0, width - partition, depth), vertical)
    host = f"{level}/Hall-Services"
    wall(host, "partition", 0, partition, (0, depth), vertical, "partition")
    for end, y in sorted(compartment_doors.items()):
      door(f"{level}/CompartmentDoor-{end}", host, "compartment", y)
    for name, zone in sorted(zones.items()):
      x, y = zone["origin_m"]
      sx, sy = zone["size_m"]
      role = zone["role"]
      if role == "service-riser":
        shaft_space = Space((x, y, bottom), (x + sx, y + sy, top))
        hosts = []
        for face, panel in Shaft(shaft_space).wall_panels(*vertical, thickness["shaft"]).items():
          host = f"{level}/{name}/{face}"
          add_panel(host, "shaft", panel)
          hosts.append(host)
        shaft = shafts.setdefault(name, {
          **asdict(shaft_space),
          "wall_hosts_by_level": {}, "definition": families["shaft"]["entry"],
        })
        shaft["wall_hosts_by_level"][level] = hosts
      elif role != "unit":
        room(f"{level}/{name}", role, (x, y, sx, sy), vertical, name)

  if config["stairs"].keys() != {"West", "East"}:
    raise ValueError("Architecture needs the west and east attached stairs for this building.")
  for name, tower in sorted(config["stairs"].items()):
    sign = -1 if name == "West" else 1
    facade = 0. if name == "West" else width
    y = (compartment_doors[tower["entry_at_compartment_door"]] if "entry_at_compartment_door" in tower
         else tower["entry_y_m"])
    if "entry_at_compartment_door" in tower and "entry_y_m" in tower:
      raise ValueError("A stair entry must have one architectural location.")
    projection = _number(tower["projection_m"], "stair projection")
    half = _number(tower["half_depth_m"], "stair half depth")
    outer = facade + sign * projection
    xmin, xmax = sorted((facade, outer))
    if not 0 <= y - half < y + half <= depth:
      raise ValueError(f"{name}: stair enclosure must align to the façade.")
    stair_width = tower["clear_width_m"]
    bypass_y = y - half + thickness["stair"] / 2 + stair_width / 2
    if bypass_y + stair_width / 2 >= y - stair_width / 2:
      raise ValueError(f"{name}: ground-floor discharge must bypass the ascending flight.")
    stairs[name] = {"envelope": asdict(Space((xmin, y - half, bottom), (xmax, y + half, top))),
                    "entry_y_m": y, "projection_m": projection, "half_depth_m": half,
                    "clear_width_m": stair_width, "definition": families["stair"]["entry"],
                    "stories": {}}
    for level, vertical in stores.items():
      entry = f"{level}/StairWall-{name}"
      wall(entry, "stair-entry", 0, facade, (y - half, y + half), vertical, "stair")
      door(f"{level}/StairDoor-{name}", entry, "stair", y)
      outer_host = f"StairDischargeWall-{name}" if level == ordered[0][0] else f"{level}/StairOuterWall-{name}"
      wall(outer_host, "stair-discharge" if level == ordered[0][0] else "stair-enclosure",
           0, outer, (y - half, y + half), vertical, "stair")
      if level == ordered[0][0]:
        door(f"Discharge-{name}", outer_host, "discharge", bypass_y)
      for face, station in (("South", y - half), ("North", y + half)):
        wall(f"{level}/Stair-{name}/{face}", "stair-enclosure", 1, station, (xmin, xmax), vertical, "stair")
    for (lower, low), (upper, high) in pairwise(ordered):
      assembly = switchback(wall=facade, sign=sign, entry_y=y, bottom=low, top=high, projection=projection,
                            half_depth=half, clear_width=tower["clear_width_m"],
                            wall_thickness_m=thickness["stair"], **config["flight"])
      stairs[name]["stories"][f"{upper}-{lower}"] = {
        **assembly, "steps": [asdict(item) for item in assembly["steps"]],
        "landings": [asdict(item) for item in assembly["landings"]],
      }
    inset = thickness["stair"] / 2
    entry_x = facade + sign * inset
    landing_end = facade + sign * (inset + config["flight"]["landing_m"])
    stairs[name]["discharge_turn_x_m"] = (entry_x + landing_end) / 2
    low_y = bypass_y - stair_width / 2
    ground = bottom - config["flight"]["stair_thickness_m"]
    a, b = sorted((entry_x, landing_end))
    c, d = sorted((landing_end, outer))
    stairs[name]["discharge_landings"] = [
      asdict(Space((a, low_y, ground), (b, y - stair_width / 2, bottom))),
      asdict(Space((c, low_y, ground), (d, bypass_y + stair_width / 2, bottom))),
    ]
  declared_packages = config.get("work_packages", {})
  if not isinstance(declared_packages, dict):
    raise ValueError("Architecture work_packages must map package IDs to room IDs.")
  work_packages = {}
  for package, members in sorted(declared_packages.items()):
    selected = _identifiers(members, f"work_packages.{package}")
    if not package.strip() or not selected or selected - rooms.keys():
      raise ValueError(f"Architecture work package {package!r} requires known room IDs.")
    work_packages[package] = sorted(selected)
  return {"schema_version": 1, "state": "assumed", "basis": config["basis"], "frame": frame,
          "top_m": top, "partition_x_m": partition, "compartment_door_y_m": compartment_doors,
          "families": families, "rooms": rooms, "walls": walls, "doors": doors, "shafts": shafts, "stairs": stairs,
          "work_packages": work_packages}
