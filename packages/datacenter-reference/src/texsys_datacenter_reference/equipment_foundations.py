"""Compile outdoor foundations, anchor setout and reinforcement from installed assets."""

from itertools import pairwise, product
from math import hypot, pi, sqrt

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_structure.concrete import mat_bars

from .fws_network import MODEL as FWS
from .roof import _schedule
from .site_grading import MODEL as TERRAIN, elevation
from .site_storm import terrain_profile
from .spatial_design import MODELS, missing_input, model_report, number, source_path, staged_json, vector
from .structure import _assumptions
from .wet_utilities import MODEL as WET

MODEL = f"{MODELS}/equipment-foundations.json"
LAYOUT = f"{MODELS}/layout.json"
YARD = {"POWER", "COOLING", "STANDBY"}


def issue(issues, code, objects, message):
  issues.append({"rule": f"AIDC_EQUIPMENT_FOUNDATION_{code}", "status": "fail", "objects": objects, "message": message})


def _assets(layout, network, water):
  for name, row in layout["objects"].items():
    if row["zone"] in YARD:
      family = row["module"].split("/")[-2]
      box = {key: row[key] for key in ("minimum_m", "maximum_m")}
      yield name, row["zone"], family, box, box, row["nominal_mass_kg"] if "nominal_mass_kg" in row else None, 0., (
        "locked-module-specification", row["module"]), row["rotation_deg"]
  for name, row in network["buffers"].items():
    yield name, "COOLING", "fws-buffer", row["foundation"], row["body"], row["empty_mass_kg"], (
      row["working_volume_m3"]*1000), ("facility-water-model", f"buffers.{name}"), 0
  tank = water["tank"]
  yield tank["id"], "COOLING", "makeup-tank", tank, tank, None, (
    tank["wetted_area_m2"]*(tank["water_elevation_m"][1]-tank["minimum_m"][2])*1000), (
      "wet-utilities-model", "tank.water_elevation_m"), 0


def _ground(terrain, x0, y0, x1, y1):
  corners = ((x0,y0,0.), (x1,y0,0.), (x1,y1,0.), (x0,y1,0.))
  grades = [z for a,b in zip(corners, (*corners[1:], corners[0]), strict=True)
            for _, z, _ in terrain_profile(terrain, a, b)]
  field = terrain["surface"]
  grades.extend(elevation(terrain, x, y) for x, y in product(field["x_m"], field["y_m"]) if x0<x<x1 and y0<y<y1)
  return [min(grades), max(grades)]


def _coordinates(start, end, inset, count):
  return [start+inset+(end-start-2*inset)*i/(count-1) for i in range(count)]


def _anchor_gaps(bar, center, bolt):
  a,b = bolt["points_m"]
  along = max(0.,abs(a[bar.axis]-center[bar.axis])-bar.length_m/2)
  height = max(a[2]-center[2],center[2]-b[2],0.)
  transverse = abs(a[1-bar.axis]-center[1-bar.axis])
  return along,height,transverse


def _reinforcement(origin, size, bolts, typical, issues, pad):
  diameter = number(typical["bar_diameter_m"], "mat bar diameter")
  cover = number(typical["mat_cover_m"], "concrete cover")
  spacing = number(typical["bar_spacing_m"], "bar station spacing")
  adjustment = number(typical["maximum_bar_shift_m"], "bar adjustment within approved envelope")
  clear = number(typical["anchor_bar_gap_m"], "minimum clear gap around bolts")
  permitted = number(typical["maximum_as_built_spacing_m"], "maximum actual mat spacing")
  bars, axes = {}, {}
  minimum_gap = float("inf")
  for i, row in enumerate(mat_bars(size, normal=2, faces=2, cover_m=cover, diameter_m=diameter, spacing_m=spacing), 1):
    axis, cross = row.axis, 1-row.axis
    center = [x+y for x,y in zip(row.center, origin, strict=True)]
    z = center[2]
    low = origin[cross]+cover+diameter/2
    high = origin[cross]+size[cross]-cover-diameter/2
    forbidden = []
    for bolt in bolts.values():
      p = bolt["points_m"][0]
      required = (bolt["diameter_m"]+diameter)/2+clear
      along,height,_ = _anchor_gaps(row,center,bolt)
      allowance = required**2-along**2-height**2
      if allowance>0:
        margin = sqrt(allowance)
        forbidden.append((p[cross]-margin, p[cross]+margin))
    initial = center[cross]
    key = f"M{i:03d}"
    available = [v for v in (initial, *(v for pair in forbidden for v in pair))
                 if low-1e-9 <= v <= high+1e-9 and abs(v-initial) <= adjustment+1e-9
                 and all(v<=a+1e-9 or v>=b-1e-9 for a,b in forbidden)]
    if not available:
      issue(issues, "MAT_BOLT_CLASH", [pad, key], f"{pad}/{key}: no permitted adjustment clears the anchorage.")
    else:
      center[cross] = min(available, key=lambda v: (abs(v-initial), v))
    for bolt in bolts.values():
      separation = hypot(*_anchor_gaps(row,center,bolt))-(bolt["diameter_m"]+diameter)/2
      minimum_gap = min(minimum_gap,separation)
    axes.setdefault((axis,z), []).append(center[cross])
    bars[key] = {"axis": axis, "center_m": center, "length_m": row.length_m, "diameter_m": diameter,
                 "shift_m": center[cross]-initial, "face": "bottom" if z-origin[2]<size[2]/2 else "top"}
  max_spacing = 0.
  for values in axes.values():
    deltas = [b-a for a,b in pairwise(sorted(values))]
    max_spacing = max(max_spacing, max(deltas, default=0.))
    if deltas and (min(deltas) < 2*diameter or max(deltas) > permitted+1e-9):
      issue(issues, "MAT_SPACING", [pad], f"{pad}: adjusted bars exceed the permitted spacing or crowd each other.")
  return bars,max_spacing,minimum_gap


def _foundation(record, config, terrain, issues):
  tag, zone, family, feet, asset, published_mass, water, source, rotation = record
  settings = config["families"][family]
  typical = config["typical"]
  anchor = config["anchor_types"][settings["anchor"]]
  pad = f"FD-{tag}"
  x, y, top = feet["minimum_m"]
  u, v, _ = feet["maximum_m"]
  swaps = slice(None, None, -1 if rotation % 180 else 1)
  ex, ey = vector(settings["edge_m"], 2, "pad margins")[swaps]
  margins = config.get("margins_by_equipment_m",{})
  west,south,east,north = vector(margins.get(tag,(ex,ey,ex,ey)),4,"west, south, east and north margins")
  ix, iy = vector(settings["inset_m"], 2, "anchor insets")[swaps]
  nx, ny = settings["pattern"][swaps]
  if (min(west,south,east,north,ix,iy)<=0 or ix*2>=u-x or iy*2>=v-y
      or any(type(n) is not int or not 2<=n<=8 for n in (nx,ny))):
    raise ValueError(f"{pad}: anchor grid does not fit the equipment base.")
  depth = number(settings["depth_m"], "concrete depth")
  bedding = number(typical["bedding_m"], "bedding depth")
  bottom = top-depth
  a,b,c,d = x-west,y-south,u+east,v+north
  inset = top if family != "fws-buffer" else feet["maximum_m"][2]
  minimum = (a,b,bottom)
  size = (c-a,d-b,depth)
  cover = number(typical["mat_cover_m"], "concrete cover")
  diameter = number(anchor["diameter_m"], "nominal bolt diameter")
  embed = number(anchor["embed_m"], "candidate bolt embedment")
  if inset-embed < bottom+cover:
    issue(issues, "ANCHOR_DEPTH", [pad], f"{pad}: anchorage violates the lower cover.")
  terrain_min,terrain_max = _ground(terrain,a,b,c,d)
  if terrain_max>top+number(typical["grade_above_cap_m"], "maximum formation above slab") or bottom>=terrain_min:
    issue(issues, "FORMATION", [pad,tag], f"{pad}: ground levels do not match the slab seat and embedment.")
  dry_mass = number(settings.get("dry_mass_kg",published_mass), tag+" dry mass")
  added = number(settings["added_operating_mass_kg"], "candidate operating increment", zero=True)+water
  grid = product(_coordinates(x,u,ix,nx), _coordinates(y,v,iy,ny))
  bolts = {}
  edge = number(typical["anchor_edge_min_m"], "installation edge distance")
  for index,(px,py) in enumerate(grid,1):
    key = f"A{index:02d}"
    edge_dist = min(px-a,c-px,py-b,d-py)
    if edge_dist < edge:
      issue(issues, "ANCHOR_EDGE", [pad,key], f"{pad}/{key}: insufficient anchor edge distance.")
    bolts[key] = {"points_m": [(px,py,inset-embed),(px,py,inset)],
                   "type": settings["anchor"], "diameter_m": diameter, "embed_m": embed, "edge_distance_m": edge_dist}
  bars,max_spacing,min_gap = _reinforcement(minimum,size,bolts,typical,issues,pad)
  upper_volume = (u-x)*(v-y)*(inset-top) if family=="fws-buffer" else 0.
  volume = size[0]*size[1]*depth
  box = {"minimum_m": minimum, "maximum_m": [c,d,top]}
  return {
    "equipment": tag, "zone": zone, "family": family, "pad": box,
    "bedding": {"minimum_m": [a,b,bottom-bedding], "maximum_m": [c,d,bottom]},
    "equipment_bounds": {"minimum_m": asset["minimum_m"], "maximum_m": asset["maximum_m"]},
    "mass_source": source if "dry_mass_kg" not in settings else ("equipment-foundations.json",f"families.{family}.dry_mass_kg"),
    "liquid_mass_kg": water, "liquid_mass_source": source if water else None,
    "operating_increment_source": ("equipment-foundations.json",f"families.{family}.added_operating_mass_kg"),
    "dry_mass_kg": dry_mass, "operating_mass_kg": dry_mass+added, "center_m": [(a+c)/2,(b+d)/2],
    "cog_z_m": asset["minimum_m"][2]+number(settings["cog_height_ratio"], "CG height ratio")*
               (asset["maximum_m"][2]-asset["minimum_m"][2]), "seat_z_m": inset,
    "ground_range_m": [terrain_min,terrain_max], "concrete_m3": volume,
    "retained_concrete_m3": upper_volume, "bedding_m3": size[0]*size[1]*bedding,
    "anchors": bolts, "bars": bars, "maximum_bar_spacing_m": max_spacing, "minimum_anchor_bar_gap_m":min_gap,
  }


def build_equipment_foundations(context):
  source = source_path(context, "equipment-foundations.json")
  if not source.is_file():
    return missing_input(context, source, "equipment-foundations")
  config = _assumptions(source)
  paths = (LAYOUT, FWS, WET, TERRAIN)
  layout, network, water, terrain = (staged_json(context, name) for name in paths)
  issues, pads = [], {}
  for record in _assets(layout,network,water):
    key = "FD-"+record[0]
    pads[key] = _foundation(record,config,terrain,issues)
  if not pads:
    raise ValueError("A candidate outdoor installation requires modeled equipment foundations.")
  if unknown := set(config.get("margins_by_equipment_m",{}))-{row["equipment"] for row in pads.values()}:
    raise ValueError(f"Unknown equipment pad margin overrides: {', '.join(sorted(unknown))}.")
  result = model_report(context,"equipment-foundations", {
    "source_digests": {name: file_digest(context.build_path/name) for name in paths}, "assumptions": config,
    "foundations": pads, "issues": issues, "frame": layout["frame"],
  })
  tables = (
    _schedule(context,"aidc-equipment-foundations.csv",
              ("foundation","equipment","family","zone","x_m","y_m","top_m","concrete_m3","bedding_m3","dry_kg","wet_kg"),
              ((id,row["equipment"],row["family"],row["zone"],*row["center_m"],row["pad"]["maximum_m"][2],
                row["concrete_m3"],row["bedding_m3"],row["dry_mass_kg"],row["operating_mass_kg"])
               for id,row in pads.items())),
    _schedule(context,"aidc-equipment-anchors.csv",("foundation","anchor","size","x_m","y_m","tip_m","head_m","edge_m"),
              ((id,key,bolt["type"],*bolt["points_m"][0],bolt["points_m"][1][2],bolt["edge_distance_m"])
               for id,row in pads.items() for key,bolt in row["anchors"].items())),
    _schedule(context,"aidc-equipment-rebar.csv",
              ("foundation","bar","face","axis","x_m","y_m","z_m","length_m","diameter_m","shift_m","steel_kg"),
              ((id,key,bar["face"],"XY"[bar["axis"]],*bar["center_m"],bar["length_m"],bar["diameter_m"],bar["shift_m"],
                7850*pi*bar["diameter_m"]**2/4*bar["length_m"])
               for id,row in pads.items() for key,bar in row["bars"].items())),
  )
  return TargetResult(result.status,(*result.artifacts,*tables),result.diagnostics)
