"""Physical supply ducts, open hot-room return and installation clearances."""

from itertools import combinations, pairwise
from math import dist, isclose

from pxr import Gf, Sdf, Usd, UsdGeom

from texsys.usd_brep import _prim_name
from texsys_hvac_dynamics.air_ducts import DuctSection, duct_record

from .fws_branches import _box_gap
from .tcs import _read
from .unit import _number


def _translate(bounds, origin):
  return tuple((a + delta, b + delta) for (a, b), delta in zip(bounds, origin, strict=True))


def _gap(a, b):
  return dist((0., 0., 0.), tuple(max(0., low - other_high, other_low - high)
                                 for (low, high), (other_low, other_high) in zip(a, b, strict=True)))


def _pipe_bounds(pipe):
  return tuple((min(p[axis] for p in pipe["points_m"]) - pipe["outside_m"] / 2,
                max(p[axis] for p in pipe["points_m"]) + pipe["outside_m"] / 2) for axis in range(3))


def _rack_pairs(racks, cold):
  x_offset, y_offset = racks["origin_m"]
  groups = {}
  for name, rack in racks["rack_loads"].items():
    x, y = rack["origin_m"]
    width, depth = rack["size_m"]
    if not (isclose(y + depth, cold[1], abs_tol=1e-8) or isclose(y, cold[3], abs_tol=1e-8)):
      raise ValueError(f"{name}: the diffuser needs a rack adjoining its aisle.")
    center = round(x_offset + x + width / 2, 9)
    groups.setdefault(center, []).append(name)
  if not groups or any(len(members) != 2 for members in groups.values()):
    raise ValueError("The duct model requires one pair of opposing racks per terminal station.")
  return groups, (cold[0] + x_offset, cold[1] + y_offset, cold[2] + x_offset, cold[3] + y_offset)


def _air_links(unit, name, racks):
  by_rack = {}
  for edge, start, end in unit["air_units"][name]["air_connections"]:
    if start == f"{name}.supply":
      rack, _, port = end.partition(".")
      key = "supply"
    elif end == f"{name}.return":
      rack, _, port = start.partition(".")
      key = "return"
    else:
      raise ValueError(f"{name}: expected a direct SDL supply or return connection to a rack.")
    record = by_rack.setdefault(rack, {})
    if key in record:
      raise ValueError(f"{name}: duplicate rack air connection.")
    record[key] = {"sdl_edge": edge, "rack_port": port}
  if by_rack.keys() != racks.keys() or any(set(value) != {"supply", "return"} for value in by_rack.values()):
    raise ValueError("Physical air routes must cover the declared SDL supply and return connections.")
  return by_rack


def _fan_layout(name, settings, config, sections, unit, floor, pairs, cold, density):
  installed = unit["equipment_layout"][name]
  if installed["port_normals"]["supply"] != [0, 0, 1] or installed["port_normals"]["return"] != [0, -1, 0]:
    raise ValueError(f"{name}: routed air requires upward supply and a front return.")
  inlet_dimensions = installed["installation_air"]["return_opening_m"]
  opening = installed["installation_air"]["supply_opening_m"]
  if tuple(opening) != (sections["throat"].width_m, sections["throat"].height_m):
    raise ValueError(f"{name}: throat section does not match the installed supply opening.")
  start = tuple(installed["ports_m"]["supply"])
  x, y, _ = start
  spine, lead, lower, raise_y, header, tier, terminal = (
    _number(settings[key], key) for key in ("spine_x_m", "lead_z_m", "lower_z_m", "raise_y_m", "header_y_m",
                                           "tier_z_m", "terminal_y_m"))
  stations = sorted(pairs, reverse=True)
  points = tuple(dict.fromkeys((start, (x, y, lead), (spine, y, lead), (spine, y, lower),
                                 (spine, raise_y, lower), (spine, raise_y, tier),
                                 (spine, header, tier), (stations[0], header, tier))))
  routes, common = {}, []
  for index, ((a, b), section, loss) in enumerate(
    zip(pairwise(points), settings["segments"], settings["fitting_loss_coefficients"], strict=True)
  ):
    identity = f"{name}/main-{index+1:02d}"
    routes[identity] = duct_record((a, b), sections[section], _number(loss, "fitting loss", inclusive=True), density)
    common.append(identity)

  branches, outlets = {}, {}
  links = _air_links(unit, name, floor["rack_loads"])
  for index, station in enumerate(stations):
    group = f"Pair-{index + 1:02d}"
    paths = []
    if index:
      extension = f"{name}/{group}/header-extension"
      routes[extension] = duct_record(((stations[index - 1], header, tier), (station, header, tier)),
                                      sections["header"], .15, density)
      paths.append(extension)
    branch = f"{name}/{group}/supply"
    routes[branch] = duct_record(((station, header, tier), (station, terminal, tier)), sections["branch"], .2, density)
    paths.append(branch)
    half_w = sections["branch"].width_m / 2
    depth = .36
    half_d = depth / 2
    z_bottom = tier - sections["branch"].height_m / 2
    diffuser = ((station - half_w, station + half_w), (terminal - half_d, terminal + half_d), (z_bottom, tier))
    if not (cold[0] <= diffuser[0][0] < diffuser[0][1] <= cold[2] and cold[1] <= diffuser[1][0] < diffuser[1][1] <= cold[3]):
      raise ValueError(f"{name}/{group}: diffuser must open into the cold aisle.")
    branches[group] = {"racks": pairs[station], "routes": paths, "diffuser_box_m": diffuser,
                       "resistance": sum(routes[route]["resistance_pa_per_m6_s2"] for route in paths)}
    for i, rack in enumerate(sorted(pairs[station], key=lambda item: floor["rack_loads"][item]["origin_m"][1])):
      side_y = terminal + (-1 if i == 0 else 1) * half_d / 2
      outlets[rack] = {**links[rack], "group": group, "discharge_m": (station, side_y, z_bottom),
                       "rack_facing": "-Y" if i == 0 else "+Y"}

  cabinet_x, cabinet_y, cabinet_z = installed["origin_m"]
  cabinet_w, _, _ = installed["envelope_m"]
  service = ((cabinet_x, cabinet_x + cabinet_w), (cabinet_y - config["front_service_m"], cabinet_y),
             (cabinet_z, cabinet_z + config["front_service_height_m"]))
  return_point = installed["ports_m"]["return"]
  width, height = (_number(value, "return grille opening") for value in inlet_dimensions)
  grille_area = width * height
  return_area = {"point_m": return_point, "width_m": width, "height_m": height, "mode": "open-hot-room",
                 "resistance": density / (2 * grille_area**2) * config["grille_loss_coefficient"]}
  if config["terminal_opening_m2"] * 2 > depth * sections["branch"].width_m + 1e-8:
    raise ValueError("The two diffuser outlets exceed the physical face area.")
  return {"routes": routes, "main": common, "groups": branches, "outlets": outlets, "return": return_area,
          "supply_point_m": start, "front_service_m": service,
          "common_resistance": sum(routes[key]["resistance_pa_per_m6_s2"] for key in common) + return_area["resistance"]}


def _local_geometry(fans, unit, floor, config, room_size, height):
  ducts = [(name, route, box) for name, fan in fans.items() for route, data in fan["routes"].items()
           for box in data["boxes_m"]]
  ducts += [(name, f"{name}/{group}/Diffuser", item["diffuser_box_m"])
            for name, fan in fans.items() for group, item in fan["groups"].items()]
  equipment = {name: tuple((offset, offset + size) for offset, size in zip(record["origin_m"], record["envelope_m"], strict=True))
               for name, record in unit["equipment_layout"].items()}
  for name, rack in floor["rack_loads"].items():
    offset = [a + b for a, b in zip(rack["origin_m"], floor["origin_m"], strict=True)]
    equipment[name] = (*tuple((start, start + size) for start, size in zip(offset, rack["size_m"], strict=True)),
                       (0., unit["tcs_model"]["rack_layout"][name]["envelope_m"][2]))
  technology = unit["tcs_model"]["routes"]
  clearance = config["minimum_clearance_m"]
  issues = []
  for name, identity, box in ducts:
    if any(a < -1e-8 or b > limit + 1e-8 for (a, b), limit in zip(box, (*room_size, height), strict=True)):
      issues.append({"check": "room-envelope", "duct": identity})
    if box[2][0] < config["minimum_overhead_m"] and not identity.endswith("main-01"):
      issues.append({"check": "overhead-clearance", "duct": identity})
    for other, envelope in equipment.items():
      if other != name and _gap(box, envelope) < clearance:
        issues.append({"check": "duct-equipment-clearance", "duct": identity, "other": other})
    for other, pipe in technology.items():
      if _box_gap(pipe, box) < clearance:
        issues.append({"check": "duct-tcs-clearance", "duct": identity, "other": other})
  for (owner, first, a), (other, second, b) in combinations(ducts, 2):
    if owner != other and _gap(a, b) < clearance:
      issues.append({"check": "independent-duct-clearance", "duct": first, "other": second})
  for name, fan in fans.items():
    service = fan["front_service_m"]
    if any(a < -1e-8 or b > limit + 1e-8 for (a, b), limit in zip(service, (*room_size, height), strict=True)):
      issues.append({"check": "maintenance-room-envelope", "unit": name})
    for _, duct, bounds in ducts:
      if not duct.endswith("/FrontService") and _gap(service, bounds) < clearance:
        issues.append({"check": "maintenance-duct-clearance", "unit": name, "other": duct})
    for other, bounds in equipment.items():
      if other != name and _gap(service, bounds) < clearance:
        issues.append({"check": "maintenance-equipment-clearance", "unit": name, "other": other})
    for other, pipe in technology.items():
      if _box_gap(pipe, service) < clearance:
        issues.append({"check": "maintenance-tcs-clearance", "unit": name, "other": other})
    ducts.append((name, f"{name}/FrontService", service))
  return ducts, issues


def _check_building(fans, geometry, building, frame, fws, installed, clearance):
  zones = {key: value for key, value in building["floor_zones"].items() if value["role"] == "unit"}
  pipes = {level: [(key, pipe, _pipe_bounds(pipe)) for key, pipe in fws["routes"].items() if pipe["level"] == level]
           for level in building["levels_m"]}
  shape = frame["sections"]["column"]
  elevations = sorted(frame["levels_m"].values())
  downstand = frame["unit_floor_deck_depth_m"] + max(section["depth"] for section in frame["sections"].values())
  downstand += max((item["section_m"]["depth"] for item in frame["secondary_beams"].values()), default=0.)
  columns = {f"{i}/{j}": ((x - shape["width"] / 2, x + shape["width"] / 2),
                           (y - shape["depth"] / 2, y + shape["depth"] / 2),
                           (frame["column_base_m"], max(frame["levels_m"].values())))
             for i, x in frame["grid"]["x"].items() for j, y in frame["grid"]["y"].items()}
  issues, placements = [], {}
  for level, elevation in building["levels_m"].items():
    soffit = next(height for height in elevations if height > elevation) - downstand
    for zone, layout in zones.items():
      origin = (*layout["origin_m"], elevation)
      room = _translate(((0., layout["size_m"][0]), (0., layout["size_m"][1]), (0., building["unit_height_m"])), origin)
      local_columns = [(key, box) for key, box in columns.items() if _gap(room, box) < clearance]
      local_pipes = [(key, pipe) for key, pipe, bounds in pipes[level] if _gap(room, bounds) < clearance]
      location = f"{level}/{zone}"
      for _, duct, bounds in geometry:
        actual = _translate(bounds, origin)
        if actual[2][1] > soffit - clearance:
          issues.append({"check": "floor-or-beam-clearance", "room": location, "duct": duct})
        for key, column in local_columns:
          if _gap(actual, column) < clearance:
            issues.append({"check": "frame-column-clearance", "room": location, "duct": duct, "other": key})
        for key, pipe in local_pipes:
          if _box_gap(pipe, actual) < clearance:
            issues.append({"check": "fws-clearance", "room": location, "duct": duct, "other": key})
      for name, fan in fans.items():
        identity = f"{location}/{name}"
        record = installed["placements"][identity]
        for key, point in (("supply", fan["supply_point_m"]), ("return", fan["return"]["point_m"])):
          if dist(record["ports_m"][key], tuple(a + b for a, b in zip(point, origin, strict=True))) > 1e-8:
            raise ValueError(f"{identity}: air path does not mate with the installed {key} port.")
        placements[identity] = origin
  return placements, issues


def evaluate_air_distribution(source, unit, floor, building, frame, fws, installed):
  config = _read(source, "air-distribution")
  if config["schema_version"] != 1 or config["state"] != "assumed" or not config["basis"].strip():
    raise ValueError("Air distribution requires an assumed version 1 basis.")
  if config["routes"].keys() != unit["air_units"].keys():
    raise ValueError("Every precision air unit needs one physical supply and return.")
  if len({building["frame"], frame["frame"], fws["frame"], installed["frame"]}) != 1:
    raise ValueError("Air distribution needs common building, frame and water coordinates.")
  density = unit["room_air"]["density_kg_m3"]
  for key in ("minimum_clearance_m", "minimum_overhead_m", "front_service_m", "front_service_height_m",
              "grille_loss_coefficient", "terminal_opening_m2"):
    _number(config[key], key)
  pairs, cold = _rack_pairs(floor, _read(source, "air-environment")["layout"]["cold_aisle_xyxy_m"])
  sections = {name: DuctSection(**data) for name, data in config["sections"].items()}
  fans = {name: _fan_layout(name, settings, config, sections, unit, floor, pairs, cold, density)
          for name, settings in sorted(config["routes"].items())}
  room_size = [min(zone["size_m"][axis] for zone in building["floor_zones"].values() if zone["role"] == "unit")
               for axis in range(2)]
  geometry, issues = _local_geometry(fans, unit, floor, config, room_size, building["unit_height_m"])
  placements, conflicts = _check_building(fans, geometry, building, frame, fws, installed, config["minimum_clearance_m"])
  issues.extend(conflicts)
  return {"schema_version": 1, "state": "assumed", "basis": config["basis"], "frame": building["frame"],
          "status": "fail" if issues else "pass", "conflicts": issues, "fans": fans, "placements": placements,
          "terminal_opening_m2": config["terminal_opening_m2"],
          "counts": {"units": len(placements), "diffuser_boxes": sum(len(fans[key.rsplit("/", 1)[1]]["groups"]) for key in placements),
                     "bound_air_ports": 2 * len(placements)}}


def write_air_distribution_usd(report, path):
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, "Z")
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/AirDistribution").GetPrim())

  def box(parent, key, bounds, color, opacity=1.):
    cube = UsdGeom.Cube.Define(stage, f"{parent}/{_prim_name(key)}")
    cube.CreateSizeAttr(1)
    cube.AddTranslateOp().Set(Gf.Vec3d(*((a + b) / 2 for a, b in bounds)))
    cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*(b - a for a, b in bounds)))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    cube.CreateDisplayOpacityAttr([opacity])
    return cube.GetPrim()

  for identity, origin in sorted(report["placements"].items()):
    name = identity.rsplit("/", 1)[1]
    fan = report["fans"][name]
    parent = f"/AirDistribution/{_prim_name(identity)}"
    node = UsdGeom.Xform.Define(stage, parent)
    node.AddTranslateOp().Set(Gf.Vec3d(*origin))
    node.GetPrim().SetCustomDataByKey("identity", identity)
    for route, value in fan["routes"].items():
      for index, bounds in enumerate(value["boxes_m"]):
        box(parent, f"{route}/{index}", bounds, (.32, .57, .73))
    for group, value in fan["groups"].items():
      box(parent, f"{group}/Diffuser", value["diffuser_box_m"], (.21, .71, .81))
    grille = fan["return"]
    x, y, z = grille["point_m"]
    w, h = grille["width_m"] / 2, grille["height_m"] / 2
    box(parent, "ReturnGrille", ((x - w, x + w), (y, y + .008), (z - h, z + h)), (.41, .37, .31))
    box(parent, "FrontService", fan["front_service_m"], (.82, .65, .29), .11)
    for rack, outlet in fan["outlets"].items():
      port = UsdGeom.Xform.Define(stage, f"{parent}/{_prim_name(rack + '/Outlet')}")
      port.AddTranslateOp().Set(Gf.Vec3d(*outlet["discharge_m"]))
      port.GetPrim().CreateAttribute("texsys:sdlConnection", Sdf.ValueTypeNames.String).Set(outlet["supply"]["sdl_edge"])
  if not stage.GetRootLayer().Export(str(path)):
    raise OSError(f"Unable to export {path}.")
