"""Conservative continuous contact bounds for a candidate rolling sphere."""

from itertools import product
from math import acos, ceil, pi, radians, sin

import numpy as np

from .lightning_roof import issue, services
from .roof import roof_surfaces
from .spatial_design import number, vector


def _grid(low,high,pitch):
  n = max(1,ceil((high-low)/pitch))
  return np.linspace(low,high,n+1)


def _tiles(tag, rectangle, pitch, top, slope=0.):
  x0,y0,x1,y1 = rectangle
  X,Y = _grid(x0,x1,pitch),_grid(y0,y1,pitch)
  return [{"host":tag,"xy_m":[float(a),float(b),float(c),float(d)],
           "point_m":[float((a+c)/2),float((b+d)/2),float(top((b+d)/2))],
           "point_radius_m":float(np.linalg.norm([c-a,d-b,(d-b)*slope])/2)}
          for (a,c),(b,d) in product(zip(X,X[1:]),zip(Y,Y[1:]))]


def candidate_tiles(roof,power,water,settings):
  x0,y0,x1,y1 = roof["outline_xy_m"]
  pitch = number(settings["sample_pitch_m"],"保护范围面片尺度")
  if not .15<=pitch<=2:
    raise ValueError("保护范围面片尺度应位于 0.15 至 2 m。")
  candidates = {}
  slope = roof["section"]["slope"]
  ridge = roof["section"]["ridge_y_m"]
  for tag,rect in (("ROOF-S",[x0,y0,x1,ridge]),("ROOF-N",[x0,ridge,x1,y1])):
    candidates[tag] = _tiles(tag,rect,pitch,lambda y:roof_surfaces(roof,y)[-1],slope)
  frame = vector(settings["service_frame_xy_m"],4,"屋盖外露机电覆盖边界")
  for key,volume in sorted(services(power,water).items()):
    lo,hi = volume.minimum_m,volume.maximum_m
    if hi[2]<settings["minimum_service_z_m"]:
      continue
    box = [max(lo[0],frame[0]),max(lo[1],frame[1]),min(hi[0],frame[2]),min(hi[1],frame[3])]
    if box[0]<box[2] and box[1]<box[3]:
      candidates[key] = _tiles(key,box,pitch,lambda _,top=hi[2]:top)
  return candidates


def _directions(theta_max,delta):
  rings = max(1,ceil(theta_max/delta))
  azimuths = ceil(2*pi/delta)
  zenith = np.linspace(0,theta_max,rings+1)
  azimuth = np.linspace(0,2*pi,azimuths,endpoint=False)
  p,a = np.meshgrid(zenith,azimuth,indexing="ij")
  dirs = np.column_stack((np.sin(p).ravel()*np.cos(a).ravel(),
                          np.sin(p).ravel()*np.sin(a).ravel(),np.cos(p).ravel()))
  # A path of half a polar interval and then half an azimuth interval bounds
  # the chord between any direction and its nearest pair of sampled angles.
  error = theta_max/(2*rings)+sin(theta_max)*pi/azimuths
  return dirs,error


def bounded_contacts(points, half_diagonals, tips, radius, theta, step):
  """Return minimum interception margin over all allowed contact directions.

  A sphere touching point p at center p+R*u hits tip t if, with v=t-p,
  v·u-|v|²/(2R)>0. The direction and tile error allowances bound variation
  over their full continuous domains. Positive values certify interception.
  """
  if len(tips)<2:
    raise ValueError("接闪覆盖需要至少两处候选接闪点。")
  directions,angular = _directions(theta,step)
  normal, one_out = [], []
  for start in range(0,len(points),48):
    delta = tips[None,:,:]-points[start:start+48,None,:]
    squared = np.einsum("bmi,bmi->bm",delta,delta)
    norm = np.sqrt(squared)
    patch = half_diagonals[start:start+48,None]
    error = norm*angular+patch*(1+norm/radius)+patch*patch/(2*radius)
    scores = np.einsum("bmi,di->bmd",delta,directions,optimize=True)-(squared/(2*radius)+error)[:,:,None]
    best = np.partition(scores,kth=len(tips)-2,axis=1)[:,-2:,:]
    normal.extend(np.min(best[:,1,:],axis=1).tolist())
    one_out.extend(np.min(best[:,0,:],axis=1).tolist())
  return normal,one_out,len(directions)


def evaluate_coverage(roof,power,water,terrain,rods,settings,issues):
  radius = number(settings["rolling_sphere_m"],"候选滚球半径")
  step = number(settings["angle_step_deg"],"角度间距")
  if not .3<=step<=5:
    raise ValueError("角度间距应位于 0.3 至 5 度。")
  floor = settings["assumed_surrounding_ground_floor_m"]
  if floor>min(min(row) for row in terrain["surface"]["proposed_m"]):
    issue(issues,"GROUND_BOUND",["AIDC-01"],"候选地面下界高于当前场坪已知点。")
  candidates = candidate_tiles(roof,power,water,settings)
  flat = [tile for group in candidates.values() for tile in group]
  points = np.array([tile["point_m"] for tile in flat])
  uncertainty = np.array([tile["point_radius_m"] for tile in flat])
  tips = np.array([row["tip_m"] for row in rods.values()])
  top = max(points[:,2]+uncertainty)
  if not floor<top<floor+radius:
    raise ValueError("屋盖与周边地面下界相差应小于候选滚球半径。")
  theta = acos((floor+radius-top)/radius)
  margin,alternate,samples = bounded_contacts(points,uncertainty,tips,radius,theta,radians(step))
  for tile,value,reduced in zip(flat,margin,alternate,strict=True):
    tile["margin_m"],tile["one_mast_removed_margin_m"] = value,reduced
  coverage = {}
  for tag,tiles in candidates.items():
    worst = min(tiles,key=lambda row:row["margin_m"])
    failed = sum(row["margin_m"]<=0 for row in tiles)
    coverage[tag] = {"tiles":len(tiles),"not_certified":failed,
                     "not_certified_one_mast_out":sum(row["one_mast_removed_margin_m"]<=0 for row in tiles),
                     "minimum_margin_m":worst["margin_m"],"controlling_position_m":worst["point_m"]}
    if failed:
      issue(issues,"ROLLING_SPHERE",[tag],f"{tag} 有 {failed} 块表面无法证明被接闪杆覆盖。")
  return {"sphere_radius_m":radius,"ground_floor_m":floor,"service_volume_selection_xy_m":settings["service_frame_xy_m"],
          "direction_count":samples,"maximum_contact_angle_deg":theta*180/pi,"targets":coverage,"tiles":flat}
