"""Resolve roof-plane nodal loads through finite collector and brace stiffness."""

from math import hypot, isclose, pi

import numpy as np

from texsys_structure.lateral import LateralMechanism

from .spatial_design import number


def _tributaries(coordinates):
  """Half adjoining intervals for each physical grid station."""
  marks = sorted(set(coordinates))
  boundaries = [marks[0], *((a+b)/2 for a, b in zip(marks, marks[1:])), marks[-1]]
  return {key: b-a for key, a, b in zip(marks, boundaries, boundaries[1:])}


def _nodal_loads(xy, action, weights, edge_fraction):
  fx, fy, moment = action["load_kn"]
  tx, ty = (_tributaries(xy[:, i]) for i in (0, 1))
  extent = np.ptp(xy, axis=0)
  center = (xy.max(axis=0)+xy.min(axis=0))/2
  tributary = np.array([tx[x]*ty[y]/np.prod(extent) for x, y in xy])
  if action["regime"] == "seismic":
    loads = weights[:, None]/weights.sum()*np.array((fx, fy))
  else:
    loads = np.zeros_like(xy)
    for i, total in enumerate((fx, fy)):
      j = 1-i
      transverse = (tx, ty)[j]
      values = xy[:, i]
      windward, leeward = (min(values), max(values)) if total >= 0 else (max(values), min(values))
      loads[:, i] = [total*transverse[point[j]]/extent[j]*
                     (edge_fraction if point[i] == windward else 1-edge_fraction if point[i] == leeward else 0)
                     for point in xy]
  radii = xy-center
  couple = np.stack((-radii[:, 1], radii[:, 0]), axis=1)
  residual = moment-float((couple*loads).sum())
  loads += residual/(tributary @ (radii**2).sum(axis=1))*tributary[:, None]*couple
  if (not np.allclose(loads.sum(axis=0), (fx, fy), atol=1e-8, rtol=0.)
      or not isclose(float((couple*loads).sum()), moment, abs_tol=1e-8)):
    raise ValueError("Distributed facade, roof loads and their torsion do not balance.")
  return loads.reshape(-1), couple


def _equilibrate(bars, springs, spring_rates, load):
  baseline = bars + springs.T @ (spring_rates[:, None]*springs)
  if np.linalg.matrix_rank(baseline) < len(load):
    raise LateralMechanism("roof transfer")
  trial = np.linalg.solve(baseline, load)

  def state(x):
    extension = springs @ x
    tension = spring_rates*np.maximum(extension, 0)
    internal = bars @ x + springs.T @ tension
    potential = float(.5*(x @ internal)-load @ x)
    return potential, internal-load, extension, tension

  for _ in range(100):
    energy, residual, extension, tension = state(trial)
    if np.max(np.abs(residual)) < 1e-8:
      return trial, tension, float(np.max(np.abs(residual)))
    active = extension > 0
    step_matrix = bars + springs[active].T @ (spring_rates[active, None]*springs[active])
    if np.linalg.matrix_rank(step_matrix) < len(load):
      step_matrix = baseline
    direction = np.linalg.solve(step_matrix, -residual)
    slope = float(residual @ direction)
    for cut in range(30):
      factor = 2.**-cut
      move = trial+factor*direction
      if state(move)[0] <= energy+1e-4*factor*slope+1e-13:
        trial = move
        break
    else:
      break
  raise LateralMechanism("active roof and vertical brace assembly")


class RoofTruss:
  """Elastic axial steel with pretensioned roof ties and unilateral vertical braces."""

  def __init__(self, model, bracing, elasticity):
    self.model = model
    config = model["assumptions"]
    self.names = tuple(model["nodes"])
    self.xy = np.array([model["nodes"][name] for name in self.names], dtype=float)
    index = {name: i for i, name in enumerate(self.names)}
    self.keys = tuple(model["collectors"])+tuple(model["ties"])
    self.initial = dict.fromkeys(self.keys, 0.)
    self.pretension = number(config["rod_pretension_kn"], "roof tie pretension")
    collection = number(config["edge_collection_share"], "windward facade share")
    if collection > 1:
      raise ValueError("Windward facade share must be in (0, 1].")
    self.windward = collection
    for panel in model["panels"].values():
      a, b, c, _ = (self.xy[index[node]] for node in panel["nodes"])
      length = float(np.linalg.norm(c-a))
      for i, member in enumerate(panel["collectors"]):
        self.initial[member] -= self.pretension*(abs(b[0]-a[0]) if i % 2 == 0 else abs(c[1]-b[1]))/length
    records = model["collectors"] | model["ties"]
    self.rows, self.rates = np.zeros((len(records), 2*len(index))), np.zeros(len(records))
    for row, key in enumerate(self.keys):
      section = records[key]
      a,b = (index[node] for node in section["nodes"])
      direction = self.xy[b]-self.xy[a]
      length = float(np.linalg.norm(direction))
      direction /= length
      self.rows[row, 2*a:2*a+2] = -direction
      self.rows[row, 2*b:2*b+2] = direction
      self.rates[row] = elasticity*section["area_m2"]/length
      if key in model["ties"]:
        self.initial[key] = self.pretension
    nodal = self.rows.T @ np.array([self.initial[key] for key in self.keys])
    if not np.allclose(nodal, 0., atol=1e-8, rtol=0.):
      raise ValueError("Pretension in roof ties and compression in edge members do not balance.")
    self.pretension_residual_kn = float(np.abs(nodal).max())
    self.bars = self.rows.T @ (self.rates[:, None]*self.rows)
    self.bay_info, self.springs, self.spring_rates = [], [], []
    self.height = bracing["upper_joint_m"]-bracing["lower_joint_m"]
    for key, bay in bracing["bays"].items():
      axis = "xy".index(bay["axis"])
      span = bay["span_m"]
      length = hypot(span, self.height)
      projection = span/length
      for rod, sign, end in (("P", 1, 1), ("N", -1, 0)):
        direction = np.zeros(2*len(index))
        node = bay["columns"][end]
        direction[2*index[node]+axis] = sign
        self.bay_info.append((key, f"{key}-{rod}", sign, axis, node, length, projection))
        self.springs.append(direction)
        self.spring_rates.append(elasticity*bracing["area_m2"]*projection**2/length)
    self.springs = np.array(self.springs)
    self.spring_rates = np.array(self.spring_rates)

  def solve(self, action, nodal_weights):
    load, couple = _nodal_loads(self.xy, action, np.array([nodal_weights[name] for name in self.names]), self.windward)
    displacement, reactions, residual = _equilibrate(self.bars, self.springs, self.spring_rates, load)
    members = self.rates*(self.rows @ displacement)
    deformed = displacement.reshape(self.xy.shape)
    summed = (self.springs.T @ reactions).reshape(self.xy.shape)
    shear = summed.sum(axis=0)
    torque = float((summed*couple).sum())
    forces = {}
    bracing = {}
    for (bay, rod, sign, axis, node, length, projection), value in zip(self.bay_info, reactions, strict=True):
      bracing.setdefault(bay, {})[rod] = {"shear_kn": float(sign*value), "tension_kn": float(value/projection),
                                         "axis": axis, "roof_node": node, "diagonal_length_m": length}
    for key, increment in zip(self.keys, members, strict=True):
      forces[key] = {"initial_kn": self.initial[key], "incremental_kn": float(increment),
                     "total_kn": self.initial[key]+float(increment)}
    return {
      "rotation_rad": float((couple*deformed).sum()/(couple**2).sum()),
      "max_corner_drift_ratio": float(np.linalg.norm(deformed, axis=1).max())/self.height,
      "shear_kn": shear.tolist(), "torsion_kn_m": torque,
      "equilibrium_residual": {"force_kn": (shear-np.array(action["load_kn"][:2])).tolist(),
                               "torsion_kn_m": torque-action["load_kn"][2],
                               "max_node_kn": residual},
      "node_displacement_m": {key: row.tolist() for key, row in zip(self.names, deformed, strict=True)},
      "node_loads_kn": {key: row.tolist() for key, row in zip(self.names, load.reshape(self.xy.shape), strict=True)},
      "node_brace_shear_kn": {key: row.tolist() for key, row in zip(self.names, summed, strict=True)},
      "bays": bracing, "roof_members": forces,
    }


def assess_roof_members(model, bracing, gravity, solved, case):
  """Screen candidate rod, beam and pin/collector demands."""
  config = model["assumptions"]
  upper_limit = number(config["joint_candidate_axial_kn"], "roof rod end axial resistance")
  combined_axial = number(config["collector_screen_axial_kn"], "collector member axial reference")
  retained = number(config["minimum_retained_tension_kn"], "roof rod retained tension")
  euler_factor = number(config["collector_euler_fraction"], "Euler compression screening fraction")
  joint_moment = number(config["collector_joint_candidate_moment_kn_m"], "roof transfer joint moment")
  net = number(config["rod_net_area_fraction"], "thread net to gross area ratio")
  stress = number(config["rod_candidate_stress_mpa"], "roof rod candidate stress")*1000
  if max(net, euler_factor) > 1:
    raise ValueError("Roof tie net area and Euler compression fractions must not exceed 1.")
  transfers = {}
  limit_z = model["collector_elevation_m"]
  floor_z = bracing["upper_joint_m"]
  load_z = model["action_elevation_m"]
  for node, force in solved["node_loads_kn"].items():
    support = solved["node_brace_shear_kn"][node]
    transfers[node] = {"load_kn": hypot(*force), "vertical_brace_shear_kn": hypot(*support),
                       "moment_kn_m": hypot(*force)*abs(load_z-limit_z)+hypot(*support)*abs(limit_z-floor_z)}
  members, issues = {}, []

  def failure(rule, name, detail):
    issues.append({"rule": f"AIDC_ROOF_TRANSFER_{rule}", "status": "fail", "objects": [case, name],
                   "message": f"{case}: {name} {detail}."})

  for key, row in solved["roof_members"].items():
    force = row["total_kn"]
    if key in model["ties"]:
      tie = model["ties"][key]
      capacity = min(tie["area_m2"]*net*stress, upper_limit)
      members[key] = {**row, "role": "roof-tie", "utilization": max(force, 0.)/capacity,
                      "tension_floor_kn": retained, "capacity_kn": capacity}
      if force < retained:
        failure("TENSION", key, f"retained tension {force:.2f} kN below candidate {retained:.2f} kN")
      if force > capacity:
        failure("ROD", key, f"axial force {force:.2f} kN above candidate {capacity:.2f} kN")
      for node in tie["nodes"]:
        lever = hypot(tie["points_m"][0][2]-limit_z, tie["plan_offset_m"])
        transfers[node]["moment_kn_m"] += abs(force)*lever
    else:
      member = model["collectors"][key]
      modulus = gravity["loads"]["elastic_modulus_gpa"]*1e6
      euler = pi**2*modulus*member["minor_inertia_m4"]/member["length_m"]**2
      capacity = min(combined_axial, euler*euler_factor) if force < 0 else combined_axial
      bending = gravity["checks"][key]["utilization"]["bending"]
      interaction = abs(force)/capacity+bending
      members[key] = {**row, "role": "collector", "utilization": interaction, "gravity_bending": bending,
                      "capacity_kn": capacity, "pinned_euler_kn": euler}
      if interaction > 1:
        failure("COLLECTOR", key, f"candidate axial and gravity bending interaction {interaction:.3f}")
  for node, transfer in transfers.items():
    moment = transfer["moment_kn_m"]
    transfer["moment_limit_kn_m"] = joint_moment
    if moment > joint_moment:
      failure("JOINT", node, f"combined eccentric moment envelope {moment:.2f} kN m")
  return members, transfers, issues
