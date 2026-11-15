"""Compile the reference hall frame and check its declared gravity load path."""

import csv
from dataclasses import asdict
from itertools import pairwise, product
import math

from pxr import Gf, Sdf, Tf, Usd, UsdGeom

from texsys.evidence import file_digest
from texsys.geometry import Box
from texsys.targets import TargetResult
from texsys_spatial import AxisGrid, Space
from texsys_structure import ISection
from texsys_structure.beam import simple_beam
from texsys_structure.foundation import footing_contact

from .spatial_design import (
  MODELS, artifact, missing_input, model_report, number, read_versioned, source_path, staged_json, vector,
)


MODEL = f"{MODELS}/structure.json"


def _assumptions(path):
  data = read_versioned(path)
  if data.get("state") != "assumed" or not isinstance(data.get("basis"), str) or not data["basis"].strip():
    raise ValueError(f"{path.name}: expected an explicit assumed engineering basis.")
  return data


def _member(kind, start, end, parts, **fields):
  return {"kind": kind, "start_m": start, "end_m": end, "length_m": math.dist(start, end),
          "parts": [asdict(part) for part in parts],
          "physical_volume_m3": sum(part.volume for part in parts), **fields}


def _sections(settings):
  return {key: ISection(**settings["sections_m"][key]) for key in ("column", "girder", "tie")}


def _model(context, settings):
  levels = staged_json(context, f"{MODELS}/levels.json")
  axes = staged_json(context, f"{MODELS}/grid.json")
  zone = levels["zones"][settings["zone"]]
  xmin, ymin, base = zone["minimum_m"]
  xmax, ymax, roof = zone["maximum_m"]
  grid = AxisGrid(axes["frame"], {k: x for k, x in axes["x"].items() if xmin <= x <= xmax},
                  {k: y for k, y in axes["y"].items() if ymin <= y <= ymax})
  if len(grid.x) < 2 or len(grid.y) < 2 or (
      min(grid.x.values()), min(grid.y.values()), max(grid.x.values()), max(grid.y.values())
  ) != (xmin, ymin, xmax, ymax):
    raise ValueError("Every hall structural boundary must land on the project grid.")
  shapes = _sections(settings)
  depth = max(shapes[k].depth for k in ("girder", "tie"))
  column_top = roof - depth
  pad = vector(settings["pad"]["size_m"], 3, "pad dimensions")
  plinth = vector(settings["pad"]["plinth_m"], 2, "plinth footprint")
  plate = vector(settings["base_plate_m"], 3, "base plate dimensions")
  bottom = vector([settings["pad"]["bottom_m"]], 1, "pad bottom elevation")[0]
  if (min(*pad, *plinth, *plate) <= 0 or bottom + pad[2] >= base or base + plate[2] >= column_top
      or any(a > b or b > c for a, b, c in zip(plate[:2], plinth, pad[:2], strict=True))):
    raise ValueError("Footing, plinth, plate and column elevations or footprints do not stack.")
  if plate[0] < shapes["column"].width or plate[1] < shapes["column"].depth:
    raise ValueError("The column must fit its base plate.")
  if any(b - a <= size for lines, size in ((grid.x, pad[0]), (grid.y, pad[1]))
         for a, b in pairwise(lines.values())):
    raise ValueError("The declared footing footprints overlap.")
  elevation = levels["levels_m"]["YARD"]
  if bottom + pad[2] >= elevation:
    raise ValueError("The declared pad must be below the yard datum.")
  members = {}
  column_parts = shapes["column"]
  for ax, ay in product(grid.x, grid.y):
    x, y = grid.point(ax, ay)
    suffix = f"{ax}-{ay}"
    footing = Box("Pad", (x - pad[0]/2, y - pad[1]/2, bottom), pad)
    plinth_part = Box("Plinth", (x - plinth[0]/2, y - plinth[1]/2, bottom + pad[2]),
                     (*plinth, base - bottom - pad[2]))
    plate_part = Box("Plate", (x - plate[0]/2, y - plate[1]/2, base), plate)
    members[f"P-{suffix}"] = _member("foundation", (x, y, bottom), (x, y, base), (footing, plinth_part),
                                     axes=[ax, ay])
    parts = (*column_parts.column((x, y), base + plate[2], column_top), plate_part)
    members[f"C-{suffix}"] = _member("column", (x, y, base), (x, y, column_top), parts,
                                     axes=[ax, ay], pad=f"P-{suffix}")
  y_values = list(grid.y.values())
  strips = [ymin, *((a + b) / 2 for a, b in pairwise(y_values)), ymax]
  for index, ay in enumerate(grid.y):
    y = grid.y[ay]
    for first, last in pairwise(grid.x):
      a, b = grid.x[first], grid.x[last]
      members[f"G-{first}-{last}-{ay}"] = _member(
        "girder", (a, y, roof), (b, y, roof), shapes["girder"].beam(a, b, y, roof, along_x=True),
        supports=[f"C-{first}-{ay}", f"C-{last}-{ay}"], tributary_width_m=strips[index+1] - strips[index],
      )
  for ax in grid.x:
    x = grid.x[ax]
    for first, last in pairwise(grid.y):
      a, b = grid.y[first], grid.y[last]
      clearance = shapes["girder"].width / 2
      members[f"T-{ax}-{first}-{last}"] = _member(
        "tie", (x, a, roof), (x, b, roof), shapes["tie"].beam(a+clearance, b-clearance, x, roof, along_x=False),
        supports=[f"C-{ax}-{first}", f"C-{ax}-{last}"], tributary_width_m=0,
      )
  if shapes["tie"].depth != shapes["girder"].depth:
    raise ValueError("This gravity detail requires girders and ties of equal depth at shared column heads.")
  layout = staged_json(context, f"{MODELS}/layout.json")
  physical = {name: Space(rec["minimum_m"], rec["maximum_m"]) for name, rec in layout["objects"].items()
              if rec["zone"] == settings["zone"] and rec["discipline"] == "it"}
  conflicts = []
  for name, member in members.items():
    if member["kind"] == "foundation":
      continue
    for part in member["parts"]:
      maximum = [a+b for a, b in zip(part["origin"], part["size"], strict=True)]
      section = Space(part["origin"], maximum)
      conflicts.extend({"rule": "AIDC_FRAME_CLASH", "status": "fail", "objects": [name, equipment],
                        "message": f"{name} {part['identity']} intersects {equipment}."}
                       for equipment, envelope in physical.items() if section.intersects(envelope))
  return grid, members, conflicts


def _deliver_members(context, members):
  stage = Usd.Stage.CreateInMemory()
  UsdGeom.SetStageMetersPerUnit(stage, 1)
  UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Frame").GetPrim())
  csv_path = context.build_path / "schedules/aidc-structure.csv"
  csv_path.parent.mkdir(parents=True, exist_ok=True)
  with csv_path.open("w", encoding="utf-8", newline="") as stream:
    schedule = csv.writer(stream)
    schedule.writerow(("member", "kind", "centerline_length_m", "physical_volume_m3", "supports",
                       "start_x_m", "start_y_m", "start_z_m", "end_x_m", "end_y_m", "end_z_m"))
    for identity, member in sorted(members.items()):
      prefix = "/Frame/" + Tf.MakeValidIdentifier(identity)
      prim = UsdGeom.Scope.Define(stage, prefix).GetPrim()
      prim.CreateAttribute("texsys:identity", Sdf.ValueTypeNames.String).Set(identity)
      material = (0.57, 0.61, 0.64) if member["kind"] == "foundation" else (0.22, 0.38, 0.56)
      for part in member["parts"]:
        cube = UsdGeom.Cube.Define(stage, f"{prefix}/{part['identity']}")
        cube.CreateSizeAttr(1)
        cube.AddTranslateOp().Set(Gf.Vec3d(*(a+b/2 for a, b in zip(part["origin"], part["size"], strict=True))))
        cube.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*part["size"]))
        cube.CreateDisplayColorAttr([Gf.Vec3f(*material)])
      schedule.writerow((identity, member["kind"], round(member["length_m"], 6),
                         round(member["physical_volume_m3"], 6), " ".join(member.get("supports", [])),
                         *member["start_m"], *member["end_m"]))
  scene = context.build_path / "scenes/aidc-structure.usda"
  scene.parent.mkdir(parents=True, exist_ok=True)
  if not stage.GetRootLayer().Export(str(scene)):
    raise OSError(f"Could not export {scene.name}.")
  return (artifact(context, "member-schedule", csv_path), artifact(context, "structure-scene", scene))


def build_structure(context):
  path = source_path(context, "structure.json")
  if not path.is_file():
    return missing_input(context, path, "structure")
  settings = _assumptions(path)
  grid, members, conflicts = _model(context, settings)
  report = model_report(context, "structure", {"frame": grid.frame, "x": dict(grid.x), "y": dict(grid.y),
                                                 "settings": settings, "members": members}, conflicts)
  return TargetResult(report.status, (*report.artifacts, *_deliver_members(context, members)), report.diagnostics)


def _gravity_check(model, loads, roof):
  members = model["members"]
  sections = _sections(model["settings"])
  steel = number(loads["steel_kn_m3"], "steel unit weight")
  concrete = number(loads["concrete_kn_m3"], "concrete unit weight")
  elastic_modulus = number(loads["elastic_modulus_gpa"], "elastic modulus") * 1e6
  factors = {k: number(loads["strength_factors"][k], k + " factor") for k in ("permanent", "variable")}
  combinations = {"service": (1, 1), "strength": tuple(factors.values())}
  moment_stress = number(loads["bending_limit_mpa"], "bending stress limit") * 1000
  shear_stress = number(loads["web_shear_limit_mpa"], "web shear stress limit") * 1000
  axial_limit = number(loads["column_axial_limit_kn"], "column axial rating")
  bearing_limit = number(loads["bearing_limit_kpa"], "service soil bearing limit")
  slenderness = number(loads["deflection_ratio"], "service span to deflection ratio")
  columns = {key: dict.fromkeys(combinations, 0.) for key, val in members.items() if val["kind"] == "column"}
  checks, diagnostics = {}, []
  steel_weight = dict.fromkeys(combinations, 0.)
  girder_actions = roof["girder_actions"]
  if set(girder_actions) != {key for key, row in members.items() if row["kind"] == "girder"}:
    raise ValueError("Roof reactions must be accounted for on all declared primary girders.")
  for key, member in members.items():
    kind = member["kind"]
    if kind not in {"girder", "tie"}:
      continue
    length, shape = member["length_m"], sections[kind]
    own = member["physical_volume_m3"] * steel
    response = {}
    for case, (permanent, _) in combinations.items():
      points = tuple((action["x_m"]-member["start_m"][0], action["force_kn"][case])
                     for action in girder_actions.get(key, ()))
      beam = simple_beam(length, elastic_modulus * shape.major_inertia_m4, own * permanent / length, points)
      response[case] = asdict(beam)
      first, last = member["supports"]
      columns[first][case] += beam.reaction_start_kn
      columns[last][case] += beam.reaction_end_kn
      steel_weight[case] += own * permanent
    strength, service = response["strength"], response["service"]
    utilization = {"bending": strength["moment_kn_m"] / (moment_stress * shape.major_inertia_m4 / (shape.depth/2)),
                   "web_shear": strength["shear_kn"] / (shear_stress * shape.web * (shape.depth - 2*shape.flange)),
                   "deflection": service["deflection_m"] / (length / slenderness)}
    checks[key] = {"kind": kind, "response": response, "utilization": utilization}
  for key, forces in columns.items():
    own = members[key]["physical_volume_m3"] * steel
    for case, (permanent, _) in combinations.items():
      steel_weight[case] += own * permanent
      forces[case] += own * permanent
    checks[key] = {"kind": "column", "base_kn": forces, "utilization": {"axial": forces["strength"] / axial_limit}}
    pad = members[key]["pad"]
    pad_weight = members[pad]["physical_volume_m3"] * concrete
    area = members[pad]["parts"][0]["size"][:2]
    contact = footing_contact(area, forces["service"] + pad_weight, (0, 0))
    checks[pad] = {"kind": "foundation", "service_column_kn": forces["service"], "own_weight_kn": pad_weight,
                   "service_contact_kn": contact.resultants()[0], "peak_bearing_kpa": contact.peak_kpa,
                   "utilization": {"bearing": contact.peak_kpa / bearing_limit}}
  for key, check in checks.items():
    for rule, value in check["utilization"].items():
      if value > 1:
        diagnostics.append({"rule": "AIDC_GRAVITY_" + rule.upper(), "status": "fail", "objects": [key],
                            "message": f"{key}: {rule} utilization {value:.3f} exceeds 1."})
  balances = {}
  for case in combinations:
    roof_load = roof["equilibrium"][case]["delivered_kn"]
    applied = roof_load + steel_weight[case]
    reactions = sum(force[case] for force in columns.values())
    if not math.isclose(applied, reactions, abs_tol=1e-7):
      raise ValueError(f"Gravity equilibrium does not close for {case}.")
    balances[case] = {"roof_kn": roof_load, "steel_kn": steel_weight[case],
                      "column_reactions_kn": reactions, "residual_kn": applied - reactions}
  return checks, balances, diagnostics


def build_gravity(context):
  path = source_path(context, "structure-loads.json")
  if not path.is_file():
    return missing_input(context, path, "structure-gravity")
  loads = _assumptions(path)
  model = staged_json(context, MODEL)
  roof_model = f"{MODELS}/roof-actions.json"
  roof = staged_json(context, roof_model)
  checks, equilibrium, failures = _gravity_check(model, loads, roof)
  return model_report(context, "structure-gravity", {
    "model_digest": file_digest(context.build_path / MODEL), "loads": loads, "checks": checks,
    "roof_actions_digest": file_digest(context.build_path / roof_model), "roof_reactions": roof["girder_actions"],
    "equilibrium": equilibrium,
    "method": "Elastic steel beams loaded by extracted purlin point reactions, axial columns and concentric footing bearing.",
  }, failures)
