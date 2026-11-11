"""Coupled rigid footings and elastic ties in a straight run of pads."""

from dataclasses import dataclass
from itertools import pairwise
from math import copysign, isfinite, sqrt

import numpy as np

from .foundation import FootingContact, _clip, _gram, _rectangle


@dataclass(frozen=True)
class TiedPad:
  station_m: float
  size_m: tuple[float, float]
  subgrade_kn_m3: float
  shear_stiffness_kn_m: float
  friction_coefficient: float


@dataclass(frozen=True)
class FoundationTie:
  axial_rigidity_kn: float
  flexural_rigidity_kn_m2: float
  height_m: float
  weight_kn_m: float


class TieEquilibriumError(ValueError):
  """The contact and tie system did not admit a converged static response."""


def _beam_matrices(a, b, tie):
  span = b.station_m - a.station_m - (a.size_m[0] + b.size_m[0]) / 2
  if span <= 0:
    raise ValueError("Tie beams require distinct footing faces and a positive clear span.")
  rigidity = tie.flexural_rigidity_kn_m2 / span ** 3
  length = span
  bending = rigidity * np.array((
    (12, 6 * length, -12, 6 * length),
    (6 * length, 4 * length ** 2, -6 * length, 2 * length ** 2),
    (-12, -6 * length, 12, -6 * length),
    (6 * length, 2 * length ** 2, -6 * length, 4 * length ** 2),
  ))
  mapping = np.zeros((4, 8))
  mapping[0, 1:3] = (1, a.size_m[0] / 2)
  mapping[1, 2] = 1
  mapping[2, 5:7] = (1, -b.size_m[0] / 2)
  mapping[3, 6] = 1
  axial = np.array((-1., 0., -tie.height_m, 0., 1., 0., tie.height_m, 0.))
  weight = tie.weight_kn_m
  load = weight * np.array((span / 2, span ** 2 / 12, span / 2, -span ** 2 / 12))
  return span, bending, mapping, axial, load


def solve_tied_footings(pads, ties, actions):
  """Solve aligned pads in local coordinates (along, transverse).

  Actions are (H along, N downward, two first moments, H transverse).
  The along-direction soil reaction is capped by Coulomb friction, with
  transverse shear consuming part of that capacity. Soil below beams is
  omitted. Beam depth offsets couple horizontal force and footing rotation.
  """
  names = tuple(sorted(pads, key=lambda name: pads[name].station_m))
  pairs = tuple(pairwise(names))
  if (len(pads) < 2 or set(ties) != set(pairs) or set(actions) != set(pads)
      or any(len(values) != 5 or not all(map(isfinite, values)) for values in actions.values())
      or any(not isfinite(pad.station_m) or len(pad.size_m) != 2
             or not 0 < pad.friction_coefficient <= 1
             or any(not isfinite(value) or value <= 0 for value in (*pad.size_m, pad.subgrade_kn_m3, pad.shear_stiffness_kn_m))
             for pad in pads.values())
      or any(not all(map(isfinite, (tie.axial_rigidity_kn, tie.flexural_rigidity_kn_m2, tie.height_m, tie.weight_kn_m)))
             or min(tie.axial_rigidity_kn, tie.flexural_rigidity_kn_m2) <= 0 or min(tie.height_m, tie.weight_kn_m) < 0
             for tie in ties.values())):
    raise ValueError("Tied footings need a straight ordered chain with positive stiffness and finite actions.")

  count = len(names)
  elastic = np.zeros((4 * count, 4 * count))
  applied = np.array([value for name in names for value in actions[name][:4]], dtype=float)
  beam_models = {}
  rectangles = [_rectangle(*(value / 2 for value in pads[name].size_m)) for name in names]
  for i, pair in enumerate(pairs):
    tie = ties[pair]
    span, bending, mapping, axial, load = _beam_matrices(*(pads[name] for name in pair), tie)
    stiffness = tie.axial_rigidity_kn / span
    block = slice(4 * i, 4 * i + 8)
    elastic[block, block] += mapping.T @ bending @ mapping + stiffness * np.outer(axial, axial)
    applied[block] += mapping.T @ load
    beam_models[pair] = (block, span, bending, mapping, axial, stiffness, load)

  normal_total = float(applied[1::4].sum())
  shear_total = abs(float(applied[::4].sum()))
  if normal_total <= 0:
    raise TieEquilibriumError("The run has no net compressive action to mobilize ground contact.")
  friction_upper = max(pad.friction_coefficient for pad in pads.values()) * normal_total
  if shear_total > friction_upper + 1e-8:
    raise TieEquilibriumError(
      f"Run shear {shear_total:.3f} kN exceeds the group friction upper bound {friction_upper:.3f} kN."
    )

  def evaluate(displacement):
    tangent = elastic.copy()
    residual = elastic @ displacement - applied
    shear_results = {}
    for i, (name, rectangle) in enumerate(zip(names, rectangles, strict=True)):
      pad = pads[name]
      u = 4 * i
      idx = slice(4 * i + 1, 4 * i + 4)
      rotation = displacement[idx]
      stiffness = pad.subgrade_kn_m3 * _gram(_clip(rectangle, rotation))
      tangent[idx, idx] += stiffness
      soil = stiffness @ rotation
      residual[idx] += soil
      trial = float(pad.shear_stiffness_kn_m * displacement[u])
      friction = pad.friction_coefficient * max(0., soil[0])
      transverse = actions[name][4]
      cap = sqrt(max(0., friction ** 2 - transverse ** 2))
      if abs(trial) <= cap:
        shear = trial
        tangent[u, u] += pad.shear_stiffness_kn_m
      else:
        shear = copysign(cap, trial)
        if cap > 0:
          tangent[u, idx] += copysign(friction / cap, trial) * pad.friction_coefficient * stiffness[0]
      residual[u] += shear
      shear_results[name] = (shear, max(0., (abs(trial) - cap) / pad.shear_stiffness_kn_m))
    return residual, tangent, shear_results

  displacement = np.zeros_like(applied)
  tolerance = max(1., float(np.abs(applied).max())) * 1e-9
  for _ in range(60):
    residual, tangent, shears = evaluate(displacement)
    error = float(np.linalg.norm(residual))
    if error <= tolerance:
      break
    try:
      increment = np.linalg.solve(tangent, residual)
    except np.linalg.LinAlgError as error:
      raise TieEquilibriumError("Tied footings lost static restraint.") from error
    for power in range(25):
      candidate = displacement - increment / (2 ** power)
      next_residual, _, _ = evaluate(candidate)
      if np.linalg.norm(next_residual) < error:
        displacement = candidate
        break
    else:
      raise TieEquilibriumError("Tied footing contact iteration stalled.")
  else:
    raise TieEquilibriumError("Tied footing contact iteration did not converge.")

  pad_results, beam_results = {}, {}
  for i, (name, rectangle) in enumerate(zip(names, rectangles, strict=True)):
    pad = pads[name]
    along, centre, slope_along, slope_across = displacement[4 * i:4 * i + 4]
    pressure = tuple(float(value * pad.subgrade_kn_m3) for value in (centre, slope_along, slope_across))
    polygon = tuple(_clip(rectangle, pressure)) if any(pressure) else ()
    state = "detached" if len(polygon) < 3 else "full" if tuple(rectangle) == polygon else "partial"
    contact = FootingContact(state, pad.size_m, pressure, polygon)
    pad_results[name] = {
      "contact": contact, "centre_settlement_m": float(centre), "along_displacement_m": float(along),
      "along_soil_reaction_kn": shears[name][0], "mobilized_slip_m": shears[name][1], "tie_end_actions": [],
    }

  for pair, (block, span, bending, mapping, axial, stiffness, load) in beam_models.items():
    tie = ties[pair]
    positions = (pads[pair[0]].size_m[0] / 2, -pads[pair[1]].size_m[0] / 2)
    internal = bending @ (mapping @ displacement[block]) - load
    tension = float(stiffness * (axial @ displacement[block]))
    q = tie.weight_kn_m
    stations = [0., span]
    if q > 0 and 0 < -internal[0] / q < span:
      stations.append(-internal[0] / q)
    moments = [-internal[1] + internal[0] * station + q * station ** 2 / 2 for station in stations]
    ends = {}
    for i, name in enumerate(pair):
      horizontal = tension if i == 0 else -tension
      end = {
        "along_position_m": positions[i], "along_force_kn": horizontal,
        "down_force_kn": float(-internal[2 * i]),
        "along_first_moment_kn_m": float(-internal[2 * i + 1] + horizontal * tie.height_m),
      }
      ends[name] = end
      pad_results[name]["tie_end_actions"].append(end)
    beam_results[pair] = {
      "clear_span_m": span, "weight_kn": q * span, "tension_kn": tension,
      "max_shear_kn": float(max(abs(internal[0]), abs(internal[2]))),
      "max_moment_kn_m": float(max(map(abs, moments))), "ends": ends,
    }
  return {"pads": pad_results, "beams": beam_results, "max_residual_kn_or_kn_m": float(np.abs(residual).max())}
