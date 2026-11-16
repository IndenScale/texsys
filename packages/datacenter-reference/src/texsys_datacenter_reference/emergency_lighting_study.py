"""Check maintained direct lighting and individual-battery runtime at the installed fixtures."""

from collections import defaultdict
from itertools import combinations, pairwise, product
from math import ceil, dist, floor, hypot

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_spatial import Space

from .architecture import MODEL as ARCH, space
from .cw_network import MODEL as CW, _segments as _cooling_segments
from .equipment_foundation_coordination import MODEL as FOUNDATIONS, _occupied
from .exit_access import MODEL as ACCESS
from .exit_access_checks import _rail_obstacles
from .facility_power import MODEL as FACILITY_POWER
from .fire_egress import MODEL as EGRESS, _swept
from .fire_systems import MODEL as FIRE
from .fire_systems_coordination import _body, _obstacles, _system_objects
from .fws_network import MODEL as FWS
from .power_routing import MODEL as POWER
from .emergency_lighting import MODEL, _interpolate
from .roof import _schedule
from .site_grading import MODEL as TERRAIN
from .site_storm import MODEL as STORM, _envelope as _storm_envelope
from .spatial_design import MODELS, model_report, number, staged_json
from .structure import MODEL as STRUCTURE
from .telecom import MODEL as TELECOM
from .wet_utilities import MODEL as WET

STUDY = f"{MODELS}/emergency-lighting-study.json"
_BUCKET = 4.0


def _issue(issues, rule, keys, message):
  issues.append({"rule": f"AIDC_EMERGENCY_LIGHTING_{rule}", "status": "fail", "objects": list(keys), "message": message})


def _fractions(length, pitch):
  count = max(1, ceil(length/pitch))
  return tuple(i/count for i in range(count+1))


def _quad(a, b, c, d, pitch):
  stations = _fractions(max(dist(a,b),dist(d,c)), pitch)
  across = _fractions(max(dist(a,d),dist(b,c)), pitch)
  return {
    tuple(round(value, 7) for value in _interpolate(_interpolate(a, b, t), _interpolate(d, c, t), u))
    for t, u in product(stations, across)
  }


def _rectangle(bounds, z):
  x0, y0, x1, y1 = bounds
  return ((x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z))


def _strip(a, b, width, end_width=None):
  x, y = (v-u for u, v in zip(a[:2], b[:2], strict=True))
  length = hypot(x, y)
  if length < 1e-8:
    return ()
  j, k = -y/length/2, x/length/2
  far = width if end_width is None else end_width
  return [(p[0]+span*j, p[1]+span*k, p[2]) for p, span in ((a,width),(b,far),(b,-far),(a,-width))]


def _sampling(arch, egress, access, pitch):
  zones = []
  volumes = {}
  indoor_width = arch["egress"]["minimum_clear_width_m"]
  for key, room in sorted(arch["rooms"].items()):
    samples = set()
    x0, _, z = room["minimum_m"]
    x1 = room["maximum_m"][0]
    y = room["aisle_y_m"]
    samples.update(_quad(*_strip((x0, y, z), (x1, y, z), indoor_width), pitch))
    for name, row in sorted(egress["routes"].items()):
      if row["compartment"] == key:
        point = row["paths"][0]["access_m"]
        cross = (point[0], y, point[2])
        samples.update(tuple(round(v, 7) for v in _interpolate(point, cross, t))
                       for t in _fractions(dist(point, cross), pitch))
        shape = _swept(point, cross, indoor_width, egress["limits"]["minimum_clear_height_m"])
        if shape:
          volumes[f"HALL-{name}"] = shape
    zones.append((key, "hall", samples))
    volumes[key] = _swept((x0, y, z), (x1, y, z), indoor_width, egress["limits"]["minimum_clear_height_m"])
  for key, row in sorted(access["ramps"].items()):
    samples = set()
    stations=row["walking_stations_m"]
    change = row["leaf_sweep_forward_m"]+.08
    upper_length=dist(stations[0],stations[1])
    if change>=upper_length:
      raise ValueError(f"{key}: insufficient upper landing after the door swing.")
    end = _interpolate(stations[0],stations[1],change/upper_length)
    near = max(.001,row["door_open_clear_m"]-.025)
    samples.update(_quad(*_strip(stations[0],end,near),pitch))
    samples.update(_quad(*_strip(end,stations[1],near,row["width_m"]),pitch))
    for a,b in pairwise(stations[1:]):
      samples.update(_quad(*_strip(a,b,row["width_m"]),pitch))
    for index, (a, b) in enumerate(pairwise(stations), 1):
      volumes[f"{key}-{index}"] = _swept(a, b, row["width_m"], egress["limits"]["minimum_clear_height_m"])
    zones.append((key, "ramp", samples))
  for key, row in sorted(access["walkways"].items()):
    samples = _quad(*row["corners_m"], pitch)
    zones.append((key, "path", samples))
    volumes[key] = _swept(*row["points_m"], row["width_m"], egress["limits"]["minimum_clear_height_m"])
  for key, row in sorted(access["junctions"].items()):
    x, y, z = row["center_m"]
    half = row["width_m"]/2
    corners = _rectangle((x-half, y-half, x+half, y+half), z)
    zones.append((f"TURN-{key}", "path", _quad(*corners, pitch)))
    volumes[f"TURN-{key}"] = Space(corners[0], (corners[2][0], corners[2][1], z+egress["limits"]["minimum_clear_height_m"]))
  for key, row in sorted(access["meeting_areas"].items()):
    corners = _rectangle(row["extent_m"], row["elevation_m"])
    zones.append((key, "assembly", _quad(*corners, pitch)))
    volumes[key] = Space(corners[0], (corners[2][0], corners[2][1], corners[2][2]+egress["limits"]["minimum_clear_height_m"]))
  return zones, volumes


def shapes(row):
  x, y, z = row["point_m"]
  if row["sign"]:
    # Exit signs are readable from the corridor, with their long side across the route.
    return {"Body": _body((x, y, z), (.11, .45, .20))}
  result = {"Body": _body((x, y, z+.04), (.24, .20, .08))}
  if pole := row["pole"]:
    px, py, base = pole["base_m"]
    result["Pedestal"] = Space((px-.13, py-.13, base-pole["depth_m"]), (px+.13, py+.13, base+.035))
    result["Post"] = Space((px-.045, py-.045, base+.035), (px+.045, py+.045, z+.055))
    result["Arm"] = Space((min(px,x)-.02, min(py,y)-.02, z+.015), (max(px,x)+.02, max(py,y)+.02, z+.055))
  return result


def _opaque(context, arch, access):
  volumes = _obstacles(context, arch, reserved_clearance=False)
  service, _ = _system_objects(staged_json(context, FIRE))
  volumes.update({key: shape for key, (shape, _) in service.items()})
  volumes.update(_rail_obstacles(access))
  volumes.update({key: _occupied(row) for key, row in staged_json(context, FOUNDATIONS)["foundations"].items()})
  for train in staged_json(context,POWER)["trains"].values():
    volumes.update({key:space(row["envelope"]) for key,row in train["segments"].items()})
  volumes.update({key:space(row["envelope"]) for key,row in staged_json(context,FACILITY_POWER)["segments"].items()})
  volumes.update({f"{key}/{number}":shape for key,number,shape in _cooling_segments(staged_json(context,CW))})
  for path in (STORM,WET):
    for key,row in staged_json(context,path)["runs"].items():
      points = row["points_m"]
      radius = row["pipe"]["outside_m"]/2
      for index,(a,b) in enumerate(pairwise(points),1):
        if a[:2]!=b[:2]:
          volumes[f"{key}/{index}"] = _storm_envelope(a,b,radius)
  for key,row in staged_json(context,TERRAIN)["inlets"].items():
    x,y = row["xy_m"]
    volumes[key] = Space((x-.22,y-.22,row["invert_m"]),(x+.22,y+.22,row["rim_m"]+.09))
  for key, row in access["ramps"].items():
    for name, leaf in row["leaves"].items():
      a = leaf["hinge_xy_m"]
      b = leaf["arc_xy_m"][-1 if row["available"] else 0]
      thick = row["leaf_thickness_m"]/2
      base = row["inside_m"][2]
      volumes[f"{key}/{name}"] = Space((min(a[0],b[0])-thick, min(a[1],b[1])-thick, base),
                                     (max(a[0],b[0])+thick, max(a[1],b[1])+thick, base+row["leaf_height_m"]))
  return volumes


def _footprint_buckets(a, b):
  pairs = [range(floor(min(u,v)/_BUCKET), floor(max(u,v)/_BUCKET)+1) for u,v in zip(a[:2], b[:2], strict=True)]
  return product(*pairs)


def _ray_hits(source, receiver, box):
  entry, exit = 1e-7, 1-1e-7
  for a, b, lo, hi in zip(source, receiver, box.minimum_m, box.maximum_m, strict=True):
    delta = b-a
    if abs(delta) < 1e-10:
      if a <= lo or a >= hi:
        return False
      continue
    low, high = sorted(((lo-a)/delta, (hi-a)/delta))
    entry, exit = max(entry, low), min(exit, high)
    if exit <= entry:
      return False
  return True


def _unobstructed(source, receiver, index, obstacles, exclude=()):
  candidates = {key for bucket in _footprint_buckets(source, receiver) for key in index.get(bucket, ())}
  return all(key in exclude or not _ray_hits(source, receiver, obstacles[key]) for key in candidates)


def _parameters(settings):
  photometry = {}
  for key, row in settings["photometry"].items():
    values = {name: number(row[name], f"{key} {name}", zero=name=="exponent") for name in
              ("peak_cd", "exponent", "maintained_factor", "end_factor")}
    if max(values["maintained_factor"], values["end_factor"]) > 1:
      raise ValueError(f"{key}: maintenance and end of discharge factors must be at most one.")
    photometry[key] = values
  return photometry


def _power(fixtures, config, required, issues):
  family_result = {}
  for family, data in config["battery"].items():
    battery = {key: number(data[key], f"{family} battery {key}") for key in (
      "nominal_wh", "input_w", "charge_w", "aging_factor", "temperature_factor", "usable_factor")}
    if max(battery[key] for key in ("aging_factor", "temperature_factor", "usable_factor")) > 1:
      raise ValueError(f"{family}: battery derating factors must be at most one.")
    usable = battery["nominal_wh"]*battery["aging_factor"]*battery["temperature_factor"]*battery["usable_factor"]
    family_result[family] = {**battery, "usable_wh": usable, "runtime_min": usable/battery["input_w"]*60,
                             "required_wh": battery["input_w"]*required/60}
  branches = defaultdict(lambda: {"fixtures": [], "charge_w": 0.0, "emergency_w": 0.0})
  for name, row in fixtures.items():
    load = family_result[row["family"]]
    branch = branches[row["normal_charge_branch"]]
    branch["fixtures"].append(name)
    branch["charge_w"] += load["charge_w"]
    branch["emergency_w"] += load["input_w"]
  for family, result in family_result.items():
    if result["runtime_min"]+1e-9 < required and any(row["family"]==family for row in fixtures.values()):
      _issue(issues, "AUTONOMY", [family],
             f"{family}: derated runtime {result['runtime_min']:.2f} min is below {required:g} min.")
  return family_result, dict(branches)


def _validate_installation(fixtures, opaque, reserved, site, issues):
  x0,y0,x1,y1 = site["boundary_m"]
  envelopes = {key:shapes(row) for key,row in sorted(fixtures.items())}
  obstacles = opaque|reserved
  for key, parts in envelopes.items():
    for part, envelope in parts.items():
      a,b,c = envelope.minimum_m
      x,y,z = envelope.maximum_m
      if not x0<=a<x<=x1 or not y0<=b<y<=y1:
        _issue(issues, "PARCEL", [key, part], f"{key}/{part}: installed lighting extends outside the parcel.")
      blockers = sorted(name for name, volume in obstacles.items() if envelope.intersects(volume))
      if blockers:
        _issue(issues, "CLASH", [key, part, *blockers], f"{key}/{part}: intersects {', '.join(blockers)}.")
  for (first,a),(second,b) in combinations(envelopes.items(),2):
    if any(one.intersects(other) for one,other in product(a.values(),b.values())):
      _issue(issues, "FIXTURE_CLASH", [first,second], f"{first}: fixture assembly intersects {second}.")


def direct_lux(source, receiver, peak_cd, exponent, factor):
  """At a horizontal receiving plane, E = I(θ) cos(θ) / r²."""
  distance = dist(source, receiver)
  cosine = (source[2]-receiver[2])/distance if distance else 0.
  return factor*peak_cd*cosine**(exponent+1)/distance**2 if cosine > 0. else 0.


def _inspect_lighting(zones, fixtures, photometry, family, settings, index, opaque, issues):
  required = number(settings["required_minutes"], "required operating minutes")
  reach = number(settings["maximum_ray_m"], "maximum optical reach")
  lamps = [(key,row) for key,row in sorted(fixtures.items()) if not row["sign"] and
           family[row["family"]]["runtime_min"]+1e-9 >= required]
  report, points = {}, {}
  for name, kind, samples in zones:
    threshold = number(settings["minimum_lux"][kind], f"{kind} minimum lux")
    observations = []
    for receiver in sorted(samples):
      groups = dict.fromkeys(("A", "B"), 0.0)
      for key, row in lamps:
        source = row["point_m"]
        if dist(source, receiver) > reach or not _unobstructed(source, receiver, index, opaque, (key,)):
          continue
        optic = photometry[row["family"]]
        groups[row["group"]] += direct_lux(source, receiver, optic["peak_cd"], optic["exponent"],
                                           optic["end_factor"]*optic["maintained_factor"])
      observations.append({"point_m": receiver, "intact_lx": groups["A"]+groups["B"],
                           "off_A_lx": groups["B"], "off_B_lx": groups["A"]})
    report[name] = {"kind": kind, "samples": len(samples), "minimum_required_lx": threshold}
    for field in ("intact_lx", "off_A_lx", "off_B_lx"):
      result = min(observations, key=lambda point: point[field])
      report[name][field] = {"value_lx": result[field], "point_m": result["point_m"],
                             "below_threshold": sum(p[field]+1e-9<threshold for p in observations)}
      if result[field]+1e-9 < threshold:
        _issue(issues, "ILLUMINANCE", [name, field],
               f"{name}/{field}: {result[field]:.3f} lx is below {threshold:g} lx at {result['point_m']}.")
    points[name] = observations
  return report, points


def build_emergency_lighting_study(context):
  model, arch, egress, access, site = (staged_json(context, path) for path in
                                       (MODEL, ARCH, EGRESS, ACCESS, f"{MODELS}/site.json"))
  config = model["assumptions"]
  settings = config["criteria"]
  pitch = number(settings["sample_spacing_m"], "emergency lighting sample pitch")
  required = number(settings["required_minutes"], "required fixture endurance")
  if pitch < .1:
    raise ValueError("The emergency lighting calculation supports a grid spacing of at least 0.1 m.")
  fixtures = model["fixtures"]
  zones, reserved = _sampling(arch, egress, access, pitch)
  opaque = _opaque(context, arch, access)
  issues = []
  family, branches = _power(fixtures, config, required, issues)
  _validate_installation(fixtures, opaque, reserved, site, issues)
  # The model uses actual opaque volumes for optical screening. Source luminaire
  # bodies sit above the emission plane, and the view starts at that plane.
  index = defaultdict(set)
  for key, box in opaque.items():
    for bucket in _footprint_buckets(box.minimum_m, box.maximum_m):
      index[bucket].add(key)
  mode, samples = _inspect_lighting(zones, fixtures, _parameters(config), family, settings, index, opaque, issues)
  signs = {}
  for key,row in sorted(fixtures.items()):
    if not row["sign"]:
      continue
    door = row["sign"]["destination"]
    _,center,base = arch["doors"][door]["inside_m"]
    x = row["point_m"][0]
    inward = row["sign"]["face_xy"][0]
    probes = []
    for distance,lateral in product((2,4,6),(-.33,0.,.33)):
      viewer = (x+distance*inward, center+lateral, base+1.4)
      visible = _unobstructed(row["point_m"], viewer, index, opaque)
      probes.append({"viewer_m":viewer,"distance_m":distance,"lateral_m":lateral,"visible":visible})
    visible = all(row["visible"] for row in probes if row["lateral_m"]==0.)
    signs[key] = {"destination":door,"probes":probes,"clear_sightline":visible}
    if not visible:
      _issue(issues, "SIGN_SIGHTLINE", [key], f"{key}: a central sightline at 2, 4 or 6 m is obstructed.")
  result = model_report(context, "emergency-lighting-study", {
    "input_digests": {path:file_digest(context.build_path/path) for path in
                      (MODEL,ARCH,EGRESS,ACCESS,FIRE,FOUNDATIONS,STRUCTURE,FWS,TELECOM,POWER,FACILITY_POWER,CW,
                       TERRAIN,STORM,WET,f"{MODELS}/layout.json",f"{MODELS}/site.json")},
    "required_minutes": required, "sample_pitch_m": pitch, "fixtures": len(fixtures),
    "battery_families":family, "charge_branches":branches, "zones":mode, "points":samples, "signs":signs, "issues":issues,
    "coverage": "Maintained direct photometry at end of required battery duration on a ground-level sample grid. "
                "Candidate photometric families and individual batteries. Bounded overall A/B group screening, "
                "opaque-envelope shadows and selected exit-sign sightlines. Side-view probes are reported separately. "
                "Physical upstream charge-distribution "
                "routing, certified IES/LDT and emergency control acceptance require separate evidence.",
  })
  table = _schedule(context, "aidc-emergency-lighting-checks.csv",
                    ("zone","case","samples","minimum_lx","required_lx","below_required","x_m","y_m","z_m"),
                    ((key, case, row["samples"], row[case]["value_lx"], row["minimum_required_lx"],
                      row[case]["below_threshold"], *row[case]["point_m"]) for key,row in mode.items()
                      for case in ("intact_lx","off_A_lx","off_B_lx")))
  loads = _schedule(context, "aidc-emergency-lighting-loads.csv",
                    ("branch","fixture_count","connected_charge_w","local_emergency_w"),
                    ((key,len(row["fixtures"]),row["charge_w"],row["emergency_w"]) for key,row in sorted(branches.items())))
  return TargetResult(result.status,(*result.artifacts,table,loads),result.diagnostics)


def build_emergency_lighting_check(context):
  study = staged_json(context, STUDY)
  return model_report(context, "emergency-lighting-check", {
    "study_digest": file_digest(context.build_path/STUDY), "issues": study["issues"],
    "points":sum(row["samples"] for row in study["zones"].values()), "zones":len(study["zones"]),
  }, study["issues"])
