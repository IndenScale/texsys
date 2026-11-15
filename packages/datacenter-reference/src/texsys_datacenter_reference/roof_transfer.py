"""Compile the roof-level axial load path and its installation coordination."""

from itertools import combinations
from math import dist, pi

from texsys.evidence import file_digest

from .architecture import MODEL as ARCH, space
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _obstacles, _system_objects
from .lateral_bracing import MODEL as BRACING, _hits, _point, _rod_distance
from .power_routing import MODEL as POWER
from .roof import MODEL as ROOF
from .roof_coordination import _installed
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json, vector
from .structure import MODEL as FRAME, _assumptions, _sections

MODEL = f"{MODELS}/roof-transfer.json"


def _corners(panel):
  _, left, right, south, north = panel.split("-")
  return (f"C-{left}-{south}", f"C-{right}-{south}", f"C-{right}-{north}", f"C-{left}-{north}")


def _edge_index(collectors):
  return {frozenset(row["nodes"]): name for name, row in collectors.items()}


def _collector(member, shape):
  web_depth = shape.depth-2*shape.flange
  minor = (2*shape.flange*shape.width**3+web_depth*shape.web**3)/12
  return {"nodes": member["supports"], "length_m": member["length_m"], "area_m2": shape.area_m2,
          "minor_inertia_m4": minor}


def build_roof_transfer(context):
  source = source_path(context, "roof-transfer.json")
  if not source.is_file():
    return missing_input(context, source, "roof-transfer")
  settings = _assumptions(source)
  roof, frame = (staged_json(context, path) for path in (ROOF, FRAME))
  members = frame["members"]
  shapes = _sections(frame["settings"])
  nodes = {key: row["start_m"][:2] for key, row in members.items() if row["kind"] == "column"}
  collectors = {key: _collector(row, shapes[row["kind"]])
                for key, row in members.items() if row["kind"] in {"girder", "tie"}}
  edges = _edge_index(collectors)
  top = next(row["end_m"][2] for row in members.values() if row["kind"] == "girder")
  levels = {name: top-number(settings["below_beam_top_m"][name], f"{name} offset")
            for name in ("upper", "lower")}
  diameter = number(settings["diameter_m"], "roof tie diameter")
  setback = number(settings["joint_setback_m"], "pin-to-free-rod set back")
  clearance = number(settings["installation_clearance_m"], "roof installation allowance")
  area = pi*diameter**2/4
  if (levels["upper"] + diameter/2 + clearance >= top
      or levels["lower"] - diameter/2 - clearance <= top-shapes["girder"].depth
      or levels["upper"]-levels["lower"] < diameter+2*clearance):
    raise ValueError("Roof tie planes must fit in the beam depth with a separated installation envelope.")
  selection = settings["panels"]
  if not isinstance(selection, list) or len(selection) != len(set(selection)) or not set(selection) <= roof["panels"].keys():
    raise ValueError("Roof load path requires a list of distinct existing roof panel IDs.")
  panels, ties = {}, {}
  for key in selection:
    corners = _corners(key)
    panel_edges = [edges[frozenset((a, b))] for a, b in zip(corners, (*corners[1:], corners[0]), strict=True)]
    panels[key] = {"nodes": corners, "collectors": panel_edges}
    for role, index in (("upper", (0, 2)), ("lower", (1, 3))):
      ends = [corners[i] for i in index]
      a, b = [(*nodes[node], levels[role]) for node in ends]
      length = dist(a, b)
      if length <= 2*setback:
        raise ValueError(f"{key}: insufficient span for the end connection reservation.")
      offset, = vector([settings["plan_offset_m"][role]], 1, f"{role} in-plane eccentricity")
      if abs(offset) >= setback/2:
        raise ValueError(f"{key}: in-plane eccentricity exceeds the assigned end connection reservation.")
      ex, ey = (-offset*(b[1]-a[1])/length, offset*(b[0]-a[0])/length)
      a, b = ((x+ex, y+ey, z) for x, y, z in (a, b))
      p, q = _point(a, b, setback/length), _point(b, a, setback/length)
      name = key.removeprefix("RF-")
      tie = f"HB-{name}-{'U' if role == 'upper' else 'L'}"
      ties[tie] = {"panel": key, "nodes": ends, "ideal_length_m": length, "free_length_m": dist(p, q),
                   "points_m": [p, q], "area_m2": area, "role": role, "plan_offset_m": offset}
  reaction_elevation = roof["section"]["deck_base_m"]
  return model_report(context, "roof-transfer", {
    "assumptions": settings, "levels_m": levels, "action_elevation_m": reaction_elevation,
    "collector_elevation_m": top-shapes["girder"].depth/2, "collector_depth_m": shapes["girder"].depth,
    "input_digests": {path: file_digest(context.build_path/path) for path in (ROOF, FRAME)},
    "nodes": nodes, "collectors": collectors, "ties": ties, "panels": panels,
  })


def build_roof_transfer_coordination(context):
  path, architecture, power, bracing, roof = (
    staged_json(context, name) for name in (MODEL, ARCH, POWER, BRACING, ROOF))
  blockers = _obstacles(context, architecture)
  blockers.update({f"Roof/{key}": shape for key, shape in _installed(roof)})
  blockers.update({name: shape for name, (shape, _) in _system_objects(staged_json(context, FIRE))[0].items()})
  blockers.update({name: space(row["envelope"]) for train in power["trains"].values()
                   for name, row in train["segments"].items()})
  vertical = {name: rod["points_m"] for bay in bracing["bays"].values() for name, rod in bay["rods"].items()}
  issues, gaps = [], []
  diameter = path["assumptions"]["diameter_m"]
  clearance = path["assumptions"]["installation_clearance_m"]
  radius = diameter/2+clearance
  v_clearance = bracing["assumptions"]["installation_clearance_m"]
  v_diameter = bracing["assumptions"]["rod_diameter_m"]

  def clash(name, other):
    issues.append({"rule": "AIDC_ROOF_TIE_CLASH", "status": "fail", "objects": [name, other],
                   "message": f"{name}: installation allowance intersects {other}."})

  for name, row in path["ties"].items():
    a, b = row["points_m"]
    for key, shape in blockers.items():
      if _hits(a, b, shape, radius):
        clash(name, key)
    for key, line in vertical.items():
      gap = _rod_distance(row["points_m"], line)-(diameter+v_diameter)/2
      if gap+1e-9 < clearance+v_clearance:
        clash(name, key)
  for (a, first), (b, last) in combinations(path["ties"].items(), 2):
    gap = _rod_distance(first["points_m"], last["points_m"])-diameter
    gaps.append(gap)
    if gap+1e-9 < 2*clearance:
      clash(a, b)
  return model_report(context, "roof-transfer-coordination", {
    "path_digest": file_digest(context.build_path/MODEL), "members_checked": len(path["ties"]),
    "obstacles_checked": len(blockers)+len(vertical), "minimum_tie_separation_m": min(gaps, default=None),
    "coverage": "Free roof tie lengths versus installed envelopes and vertical brace rods. Pin and gusset "
                "hardware fall inside the declared setbacks and remain subject to connection design.",
    "issues": issues,
  }, issues)
