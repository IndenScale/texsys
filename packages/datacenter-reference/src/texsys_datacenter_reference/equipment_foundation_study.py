"""Screen outdoor foundations with soil contact, discrete foot reactions and strip demands."""

from itertools import product
from math import hypot, isclose, pi

from texsys.evidence import file_digest
from texsys.targets import TargetResult
from texsys_structure.foundation import footing_contact

from .equipment_foundations import MODEL, issue
from .roof import _schedule
from .spatial_design import MODELS, model_report, number, staged_json

STUDY = f"{MODELS}/equipment-foundation-study.json"
G = 9.80665


def _scenario_loads(row, loading, weight):
  dimensions = [b-a for a,b in zip(row["equipment_bounds"]["minimum_m"],row["equipment_bounds"]["maximum_m"],strict=True)]
  wind = [loading["wind_kpa"]*dimensions[i]*dimensions[2] for i in (1,0)]
  envelope = row["equipment_bounds"]
  wind_z = (envelope["minimum_m"][2]+envelope["maximum_m"][2])/2
  eccentric = loading["plan_eccentric_fraction"]
  horizontal = loading["horizontal_acceleration_g"]*weight
  yield "gravity", (0.,0.), 0., row["cog_z_m"]
  for i,sign,twist in product((0,1),(1,-1),(1,-1)):
    shear = [0.,0.]
    shear[i] = sign*wind[i]
    yield f"wind/{'xy'[i]}{sign:+}/ecc{twist:+}", shear, twist*wind[i]*dimensions[1-i]*eccentric, wind_z
    for cross in (1,-1):
      force = [0.,0.]
      force[i],force[1-i] = sign*horizontal,cross*loading["orthogonal_share"]*horizontal
      torsion = twist*horizontal*dimensions[1-i]*eccentric
      yield f"seismic/{'xy'[i]}{sign:+}/cross{cross:+}/ecc{twist:+}", force,torsion,row["cog_z_m"]


def _anchor_reactions(row, anchor, normal, horizontal, torsion, force_z):
  bolts = row["anchors"]
  points = [data["points_m"][1] for data in bolts.values()]
  count = len(points)
  center = [sum(point[i] for point in points)/count for i in (0,1)]
  coordinates = [[point[i]-center[i] for i in (0,1)] for point in points]
  polar_components = [sum(pt[i]**2 for pt in coordinates) for i in (0,1)]
  polar = sum(polar_components)
  above = force_z-row["seat_z_m"]
  # The candidate skid distributes plane-compatible signed foot forces to its registered anchors.
  moments = [value*above for value in horizontal]
  fx,fy = horizontal
  data = {}
  for name,(dx,dy),point in zip(bolts,coordinates,points,strict=True):
    vertical = normal/count + dx*moments[0]/polar_components[0] + dy*moments[1]/polar_components[1]
    shear = (fx/count-torsion*dy/polar,fy/count+torsion*dx/polar)
    uplift = max(0.,-vertical)
    seat_pressure = max(0.,vertical)/anchor["seat_m2"]
    data[name] = {"foot_down_kn": vertical, "tension_kn": uplift, "shear_kn": shear,
                  "seat_pressure_kpa": seat_pressure, "xy_m": point[:2],
                  "interaction": uplift/anchor["tension_kn"]+hypot(*shear)/anchor["shear_kn"],
                  "seat_utilization": seat_pressure/anchor["seat_kpa"]}
  residual = [sum(r["foot_down_kn"] for r in data.values())-normal,
              *[sum(r["shear_kn"][i] for r in data.values())-horizontal[i] for i in (0,1)],
              *[sum(r["foot_down_kn"]*(r["xy_m"][i]-center[i]) for r in data.values())-moments[i] for i in (0,1)],
              sum((r["xy_m"][0]-center[0])*r["shear_kn"][1]-(r["xy_m"][1]-center[1])*r["shear_kn"][0]
                  for r in data.values())-torsion]
  if max(map(abs,residual))>1e-7:
    raise ValueError("Registered bolt reactions do not balance the equipment actions.")
  return data


def _torsion_capacity(contact, normal, friction):
  capacity = []
  for i, size in enumerate(contact.size_m):
    axis = (1,0) if i==0 else (0,1)
    lower,upper = -size/2,size/2
    for _ in range(22):
      median = (lower+upper)/2
      reaction, *_ = contact.resultants((-median,*axis))
      if reaction>normal/2:
        lower = median
      else:
        upper = median
    median = (lower+upper)/2
    reaction, *moments = contact.resultants((-median,*axis))
    whole = contact.resultants()[i+1]
    distance_integral = 2*(moments[i]-median*reaction)-whole+median*normal
    capacity.append(friction*distance_integral)
  return max(capacity)


def _strip_demands(contact, row, anchors, loading, typical, uniform_weight, retained_weight):
  lengths = contact.size_m
  area = lengths[0]*lengths[1]
  qdead = uniform_weight/area
  diameter = typical["bar_diameter_m"]
  depth = row["pad"]["maximum_m"][2]-row["pad"]["minimum_m"][2]-typical["mat_cover_m"]-1.5*diameter
  capacity = pi*diameter**2/4/row["maximum_bar_spacing_m"]*loading["rebar_stress_mpa"]*1000*.85*depth
  shear_capacity = loading["concrete_shear_kpa"]*depth
  elevation = row["seat_z_m"]-row["pad"]["minimum_m"][2]
  point_loads = [([a-b for a,b in zip(r["xy_m"],row["center_m"],strict=True)],r["foot_down_kn"],
                  [value*elevation for value in r["shear_kn"]])
                 for r in anchors.values()]
  point_loads.append(([0.,0.],retained_weight,[0.,0.]))
  demands = {}
  for i, sign in product((0,1),(-1,1)):
    width = lengths[1-i]
    stop = lengths[i]/2
    axis = (sign,0) if i==0 else (0,sign)
    marks = {0., *[sign*pt[i] for pt,_,_ in point_loads]}
    moment_list, shear_list = [], []
    for mark in sorted(marks):
      soil,*moments = contact.resultants((-mark,*axis))
      moment = sign*moments[i]-mark*soil
      moment -= qdead*width*(stop-mark)**2/2
      moment -= sum(force*(sign*pt[i]-mark)+sign*local[i] for pt,force,local in point_loads if sign*pt[i]>mark)
      cut = min(stop,mark+depth)
      soil_at_shear,*_ = contact.resultants((-cut,*axis))
      applied = sum(force for pt,force,_ in point_loads if sign*pt[i]>cut)
      shear = soil_at_shear-qdead*width*(stop-cut)-applied
      moment_list.append(abs(moment)/width)
      shear_list.append(abs(shear)/width)
    bend = max(moment_list)
    shear = max(shear_list)
    demands[f"{'xy'[i]}{sign:+}"] = {"moment_kn_m_per_m": bend,"shear_kn_per_m":shear,
                                      "moment_ratio":bend/capacity,"shear_ratio":shear/shear_capacity}
  return demands


def _case(row, config, variant, normal, horizontal, torsion, force_z, gravity_factor):
  loading, soil, typical = (config[k] for k in ("loading","soil","typical"))
  anchor = config["anchor_types"][next(iter(row["anchors"].values()))["type"]]
  feet = _anchor_reactions(row,anchor,normal,horizontal,torsion,force_z)
  pad = row["pad"]
  size = tuple(b-a for a,b in zip(pad["minimum_m"][:2],pad["maximum_m"][:2],strict=True))
  base = pad["minimum_m"][2]
  concrete = row["concrete_m3"]*loading["concrete_kn_m3"]*gravity_factor
  retained = row["retained_concrete_m3"]*loading["concrete_kn_m3"]*gravity_factor
  action = normal+concrete+retained
  moments = tuple(sum(foot["foot_down_kn"]*(foot["xy_m"][i]-row["center_m"][i]) for foot in feet.values())+
                  horizontal[i]*(row["seat_z_m"]-base) for i in (0,1))
  contact = footing_contact(size,action,moments)
  torsion_at_base = sum((foot["xy_m"][0]-row["center_m"][0])*foot["shear_kn"][1]-
                        (foot["xy_m"][1]-row["center_m"][1])*foot["shear_kn"][0] for foot in feet.values())
  stable = contact.state in {"full","partial"}
  closure = contact.resultants()
  if stable and any(not isclose(a,b,abs_tol=1e-6) for a,b in zip(closure,(action,*moments),strict=True)):
    raise ValueError("The base pressure integral does not reproduce force and first moments.")
  torsion_cap = _torsion_capacity(contact,action,soil["friction"]) if abs(torsion_at_base)>1e-8 and stable else None
  translation = hypot(*horizontal)/(soil["friction"]*action)
  sliding = translation+(abs(torsion_at_base)/torsion_cap if torsion_cap else 0.)
  strips = _strip_demands(contact,row,feet,loading,typical,concrete,retained) if stable else {}
  contact_fraction = contact.area_m2/(size[0]*size[1])
  checks = {
    "bearing": contact.peak_kpa/soil["bearing_kpa"],
    "sliding_torsion": sliding,
    "contact": soil["minimum_contact_fraction"]/contact_fraction if contact_fraction else None,
    "bolt": max(value["interaction"] for value in feet.values()),
    "seat": max(value["seat_utilization"] for value in feet.values()),
    "mat": max((value["moment_ratio"] for value in strips.values()),default=0.),
    "strip_shear": max((value["shear_ratio"] for value in strips.values()),default=0.),
  }
  return {"vertical_variant": variant, "equipment_normal_kn":normal, "applied_shear_kn":horizontal,
          "applied_torsion_kn_m":torsion,"base_torsion_kn_m":torsion_at_base,
          "foundation_normal_kn":action,"first_moments_kn_m":moments,
          "contact_state":contact.state,"contact_fraction":contact_fraction,"peak_soil_kpa":contact.peak_kpa,
          "soil_integrals":closure,"anchors":feet,"slab":strips,"utilization":checks}


def _validate(config):
  for field, values in (("soil",config["soil"]),("loading",config["loading"])):
    for key,value in values.items():
      number(value,f"{field} {key}")
  loading = config["loading"]
  if (max(config["soil"]["friction"],config["soil"]["minimum_contact_fraction"],
          loading["plan_eccentric_fraction"],loading["orthogonal_share"],loading["low_gravity_factor"])>1
      or loading["high_gravity_factor"]<1):
    raise ValueError("The declared gravity bounds or ratios are invalid.")
  for tag, value in config["anchor_types"].items():
    for key in ("tension_kn","shear_kn","seat_m2","seat_kpa"):
      number(value[key],f"{tag} {key}")


def build_equipment_foundation_study(context):
  model = staged_json(context,MODEL)
  config = model["assumptions"]
  _validate(config)
  loading = config["loading"]
  issues, solved = list(model["issues"]), {}
  for id,row in model["foundations"].items():
    cases = {}
    for state,mass in (("empty",row["dry_mass_kg"]),("operating",row["operating_mass_kg"])):
      weight = mass*G/1000
      for mode, force, torque, force_z in _scenario_loads(row,loading,weight):
        for variant in ("low","high"):
          key = f"{state}/{mode}/{variant}"
          factor = loading[f"{variant}_gravity_factor"]
          data = _case(row,config,variant,weight*factor,force,torque,force_z,factor)
          cases[key] = data
          for name,ratio in data["utilization"].items():
            if ratio is None or ratio>1+1e-9:
              message = f"{id}/{key}: {name} is out of the declared limit"
              issue(issues,name.upper(),[id,key],f"{message} ({'no stable contact' if ratio is None else f'{ratio:.3f}'}).")
    solved[id] = cases
  result = model_report(context,"equipment-foundation-study",{
    "model_digest":file_digest(context.build_path/MODEL),"foundations":solved,"issues":issues,
    "coverage":"Rigid skid with signed foot reactions; compression-only rigid footing. Both gravity bounds, "
               "wind and biaxial static acceleration use candidate ratings. Gross mat design uses whole-width "
               "strips. Actual skid supports, concrete breakout, punching, fatigue and dynamic isolation remain open.",
  })
  schedule = _schedule(context,"aidc-equipment-foundation-cases.csv",
                       ("foundation","case","soil_kpa","contact_fraction","max_bolt_kn","bolt_interaction",
                        "sliding_interaction","strip_flexure_ratio","strip_shear_ratio"),
                       ((id,key,row["peak_soil_kpa"],row["contact_fraction"],max(x["tension_kn"] for x in row["anchors"].values()),
                         row["utilization"]["bolt"],row["utilization"]["sliding_torsion"],row["utilization"]["mat"],
                         row["utilization"]["strip_shear"]) for id,cases in solved.items() for key,row in cases.items()))
  return TargetResult(result.status,(*result.artifacts,schedule),result.diagnostics)


def build_equipment_foundation_check(context):
  result = staged_json(context,STUDY)
  return model_report(context,"equipment-foundation-check",{
    "study_digest": file_digest(context.build_path/STUDY),
    "case_count": sum(map(len,result["foundations"].values())), "issues":result["issues"],
  },result["issues"])
