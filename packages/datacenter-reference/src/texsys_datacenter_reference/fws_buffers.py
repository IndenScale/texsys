"""Compile inline cooling plant buffers with real connection and clearance geometry."""

import csv
import math

from pxr import Gf, Sdf, UsdGeom

from texsys_spatial import Space

from .spatial_design import artifact, number


def _dimensions(config):
  keys = ("setback_from_plant_port_m", "bore_m", "wall_m", "insulation_m", "shell_height_m",
          "operating_fill_m", "shell_bottom_above_grade_m", "pad_width_m", "pad_height_m",
          "east_service_clearance_m", "empty_mass_kg", "rated_drop_kpa", "reference_flow_l_s",
          "max_flow_l_s", "minimum_working_volume_m3")
  dims = {key: number(config[key], key, zero=key in {"insulation_m", "rated_drop_kpa"}) for key in keys}
  if (dims["operating_fill_m"] >= dims["shell_height_m"]-2*dims["wall_m"]
      or dims["pad_height_m"] >= dims["shell_bottom_above_grade_m"]):
    raise ValueError("The installed buffer needs a shell freeboard and a pedestal above the pad.")
  if dims["reference_flow_l_s"] > dims["max_flow_l_s"]:
    raise ValueError("The reference pressure-drop flow must be within the buffer flow range.")
  return dims


def _xyz(x0, y0, z0, x1, y1, z1):
  space = Space((x0, y0, z0), (x1, y1, z1))
  return {"minimum_m": space.minimum_m, "maximum_m": space.maximum_m}


def install_buffers(config, zones, layout, ports, node, segments, paths):
  if config is None:
    return {}
  d = _dimensions(config)
  zone = zones[config["zone"]]
  region = Space(tuple(zone["minimum_m"]), tuple(zone["maximum_m"]))
  radius = d["bore_m"]/2+d["wall_m"]+d["insulation_m"]
  pad = d["pad_width_m"]/2
  if pad <= radius:
    raise ValueError("The complete vessel envelope must fit on its pad.")
  volumes = {}
  for index, (plant, records) in enumerate(sorted(ports["plants"].items()), 1):
    if layout["objects"][plant]["zone"] != config["zone"]:
      raise ValueError(f"{plant}: place its buffer in the declared plant zone.")
    route = paths["plants"][plant]["supply"]
    label = f"BT-{index:02}"
    lead = route[-1]
    pipe = segments[lead]
    port, elbow = pipe["points_m"]
    x, y, connection_z = port
    center_y = y-d["setback_from_plant_port_m"]
    south, north = center_y-radius, center_y+radius
    grade = zone["minimum_m"][2]
    plinth = grade+d["pad_height_m"]
    bottom = grade+d["shell_bottom_above_grade_m"]
    top = bottom+d["shell_height_m"]
    waterline = bottom+d["wall_m"]+d["operating_fill_m"]
    spring = pipe["hydraulic_section"]["envelope_m"]/2
    if not (elbow[1] < south < north < y and bottom+spring < connection_z < waterline-spring):
      raise ValueError(f"{label}: the shell and flanges must fit between the plant port and rising pipe.")
    inlet, outlet = f"S-{label}-IN", f"S-{label}-OUT"
    node[inlet], node[outlet] = (x, north, connection_z), (x, south, connection_z)
    front, back = f"{lead}-IN", lead
    segments[front] = {**pipe, "to": inlet, "inner": inlet, "points_m": [port, node[inlet]], "length_m": y-north,
                       "minor_k": 0.}
    segments[back] = {**pipe, "from": outlet, "outer": outlet, "points_m": [node[outlet], elbow], "length_m": south-elbow[1]}
    route.extend((label, front))
    body = _xyz(x-radius, south, bottom, x+radius, north, top)
    foundation = _xyz(x-pad, center_y-pad, grade, x+pad, center_y+pad, plinth)
    pedestal = _xyz(x-radius*.68, center_y-radius*.68, plinth, x+radius*.68, center_y+radius*.68, bottom)
    service = _xyz(x+radius, south, plinth, x+radius+d["east_service_clearance_m"], north, top+.5)
    for key, shape in (("vessel", body), ("pad", foundation), ("service", service)):
      if not region.contains_space(Space(tuple(shape["minimum_m"]), tuple(shape["maximum_m"]))):
        raise ValueError(f"{label}: {key} does not fit the declared plant zone.")
    volume = math.pi*(d["bore_m"]/2)**2*d["operating_fill_m"]
    volumes[label] = {"leg": "supply", "kind": "buffer", "plant": plant, "from": inlet, "to": outlet,
                      "points_m": [node[inlet], node[outlet]], "lead_in": front, "lead_out": back,
                      "body": body, "foundation": foundation, "pedestal": pedestal, "service_clearance": service,
                      "center_m": (x, center_y, (bottom+top)/2), "outside_diameter_m": 2*radius, "bore_m": d["bore_m"],
                      "operating_fill_m": d["operating_fill_m"], "waterline_m": waterline,
                      "working_volume_m3": volume, "minimum_volume_m3": d["minimum_working_volume_m3"],
                      "empty_mass_kg": d["empty_mass_kg"], "rated_drop_kpa": d["rated_drop_kpa"],
                      "reference_flow_m3_s": d["reference_flow_l_s"]/1000, "max_flow_m3_s": d["max_flow_l_s"]/1000,
                      "sdl_supply_link": records["supply"]["id"]}
  return volumes


def add_buffer_geometry(stage, records):
  count = 32
  for name, row in records.items():
    path = "/AIDC/FWSBuffers/" + name.replace("-", "_")
    body = UsdGeom.Mesh.Define(stage, path)
    x, y, _ = row["center_m"]
    r = row["outside_diameter_m"]/2
    points = [Gf.Vec3f(x+r*math.cos(2*math.pi*i/count), y+r*math.sin(2*math.pi*i/count), z)
              for z in (row["body"]["minimum_m"][2], row["body"]["maximum_m"][2]) for i in range(count)]
    faces = [[i, (i+1)%count, (i+1)%count+count, i+count] for i in range(count)]
    faces.extend((list(reversed(range(count))), list(range(count, 2*count))))
    body.CreatePointsAttr(points)
    body.CreateFaceVertexCountsAttr([len(face) for face in faces])
    body.CreateFaceVertexIndicesAttr([value for face in faces for value in face])
    body.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    body.CreateDisplayColorAttr([Gf.Vec3f(.33, .50, .67)])
    body.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(name)
    for key in ("foundation", "pedestal"):
      record = row[key]
      shape = Space(tuple(record["minimum_m"]), tuple(record["maximum_m"]))
      prim = UsdGeom.Cube.Define(stage, f"{path}_{key}")
      prim.CreateSizeAttr(1)
      prim.AddTranslateOp().Set(Gf.Vec3d(*shape.center_m))
      prim.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*shape.size_m))
      prim.GetPrim().CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(f"{name}-{key}")


def buffer_schedule(context, records, density, design_duty):
  output = context.build_path / "schedules/aidc-fws-buffers.csv"
  output.parent.mkdir(parents=True, exist_ok=True)
  with output.open("w", encoding="utf-8", newline="") as stream:
    table = csv.writer(stream)
    table.writerow(("buffer", "plant", "x_m", "y_m", "inlet_z_m", "outlet_z_m", "outside_mm", "working_fill_mm", "working_volume_l",
                    "wet_mass_kg", "max_case_flow_l_s", "max_case_drop_kpa", "source_sdl_link"))
    for name, row in sorted(records.items()):
      peak = max((case[name]["flow_m3_s"] for case in design_duty.values()), default=0.)
      drop = max((case[name]["loss_kpa"] for case in design_duty.values()), default=0.)
      table.writerow((name, row["plant"], *[round(x, 3) for x in row["center_m"][:2]], row["points_m"][0][2], row["points_m"][1][2],
                      round(1000*row["outside_diameter_m"], 1), round(1000*row["operating_fill_m"], 1), round(1000*row["working_volume_m3"], 3),
                      round(row["empty_mass_kg"]+density*row["working_volume_m3"], 2), round(1000*peak, 3), round(drop, 3), row["sdl_supply_link"]))
  return artifact(context, "fws-buffer-schedule", output)
