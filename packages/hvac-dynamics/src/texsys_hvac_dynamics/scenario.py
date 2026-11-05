"""Fixed input signals and experiment configuration, independent of design state."""

from dataclasses import dataclass
import bisect
import math
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from texsys.evidence import canonical_digest
from texsys.formal import _load_json
from .domain import PhysicalModel, PhysicsInputError


@dataclass(frozen=True)
class Signal:
  unit: str
  interpolation: str
  points: tuple[tuple[float, float], ...]
  state: str
  source: str

  def at(self, time: float) -> float:
    index = max(0, bisect.bisect_right([p[0] for p in self.points], time) - 1)
    if self.interpolation == "hold" or index == len(self.points) - 1:
      return self.points[index][1]
    t0, y0 = self.points[index]
    t1, y1 = self.points[index + 1]
    return y0 + (y1 - y0) * (time - t0) / (t1 - t0)


@dataclass(frozen=True)
class Scenario:
  identity: str
  duration_s: float
  sample_interval_s: float
  signals: Mapping[str, Signal]
  initial: dict
  solver: Mapping[str, float | str]
  assertions: tuple[dict, ...]
  digest: str

  def value(self, component: str, name: str, time: float) -> float:
    try:
      return self.signals[f"{component}.{name}"].at(time)
    except KeyError as error:
      raise PhysicsInputError(f"Missing scenario input: {component}.{name}") from error

  @property
  def breakpoints(self):
    return tuple(sorted({0.0, self.duration_s, *(t for signal in self.signals.values() for t, _ in signal.points if 0 < t < self.duration_s)}))


def _number(value, label, *, positive=False):
  if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or positive and value <= 0:
    raise PhysicsInputError(f"{label} must be a finite {'positive ' if positive else ''}number.")
  return float(value)


def load_scenario(path: Path, model: PhysicalModel) -> Scenario:
  raw = _load_json(path.read_text(encoding="utf-8"))
  required = {"schema_version", "id", "duration_s", "sample_interval_s", "signals", "initial", "solver", "assertions"}
  if not isinstance(raw, dict) or set(raw) != required or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
    raise PhysicsInputError("Scenario requires the version-1 experiment contract with explicit signals and initial conditions.")
  identity = raw["id"]
  if not isinstance(identity, str) or not identity or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in identity):
    raise PhysicsInputError("Scenario id must be a non-empty portable identifier.")
  duration = _number(raw["duration_s"], "duration_s", positive=True)
  interval = _number(raw["sample_interval_s"], "sample_interval_s", positive=True)
  if interval > duration or duration / interval > 100000:
    raise PhysicsInputError("Sampling must produce between 1 and 100000 intervals.")
  expected = {}
  for component in model.components:
    kind = component.kind
    controls = {"pump": {"speed_ratio": "1"}, "valve": {"opening": "1"}, "load": {"load_w": "W"},
                "tower": {"ambient_c": "degC", "fan_ratio": "1"}, "chiller": {"enabled": "1", "setpoint_c": "degC"},
                "pipe": {"ambient_c": "degC"}, "tank": {"ambient_c": "degC"}}.get(kind, {})
    for name, unit in controls.items():
      expected[f"{component.identity}.{name}"] = unit
  if not isinstance(raw["signals"], dict):
    raise PhysicsInputError("Scenario signals must be an object.")
  if set(raw["signals"]) != set(expected):
    raise PhysicsInputError(f"Scenario signal contract mismatch; missing={sorted(set(expected)-set(raw['signals']))}, unknown={sorted(set(raw['signals'])-set(expected))}.")
  signals = {}
  for name, value in sorted(raw["signals"].items()):
    if not isinstance(value, dict) or set(value) != {"unit", "interpolation", "points", "state", "source"}:
      raise PhysicsInputError(f"{name}: a signal requires unit, interpolation, points, state and source.")
    if value["unit"] != expected[name] or value["interpolation"] not in {"hold", "linear"} or value["state"] not in {"known", "assumed"}:
      raise PhysicsInputError(f"{name}: invalid unit, interpolation or state.")
    if not isinstance(value["source"], str) or not value["source"].strip() or not isinstance(value["points"], list) or not value["points"]:
      raise PhysicsInputError(f"{name}: explicit points and source are required.")
    points = []
    for item in value["points"]:
      if not isinstance(item, list) or len(item) != 2:
        raise PhysicsInputError(f"{name}: each point must be [time_s, value].")
      time, number = _number(item[0], name), _number(item[1], name)
      if not 0 <= time <= duration or points and time <= points[-1][0]:
        raise PhysicsInputError(f"{name}: times must be strictly increasing inside the experiment.")
      key = name.rsplit(".", 1)[1]
      if key in {"speed_ratio", "opening", "fan_ratio", "enabled"} and not 0 <= number <= 1:
        raise PhysicsInputError(f"{name}: fraction must lie in [0, 1].")
      if key == "enabled" and (number not in {0, 1} or value["interpolation"] != "hold"):
        raise PhysicsInputError(f"{name}: enabled is a discrete hold signal with values 0 or 1.")
      if key == "load_w" and number < 0:
        raise PhysicsInputError(f"{name}: cooling load cannot be negative.")
      points.append((time, number))
    if points[0][0] != 0:
      raise PhysicsInputError(f"{name}: initial signal value at t=0 is mandatory.")
    signals[name] = Signal(value["unit"], value["interpolation"], tuple(points), value["state"], value["source"])
  initial = raw["initial"]
  if not isinstance(initial, dict) or initial.get("mode") not in {"steady-state", "specified"}:
    raise PhysicsInputError("Initial mode must be steady-state or specified.")
  if initial["mode"] == "steady-state":
    if set(initial) != {"mode", "temperature_guess_c"} or not isinstance(initial["temperature_guess_c"], dict):
      raise PhysicsInputError("Steady-state initialization requires explicit per-medium temperature guesses.")
    if set(initial["temperature_guess_c"]) != {b.medium for b in model.branches}:
      raise PhysicsInputError("Initial temperature guesses must cover exactly the physical media.")
    for value in initial["temperature_guess_c"].values():
      _number(value, "initial temperature")
  elif set(initial) != {"mode", "states"} or not isinstance(initial["states"], dict):
    raise PhysicsInputError("Specified initialization requires a complete state map.")
  else:
    for value in initial["states"].values():
      _number(value, "initial state")
  defaults = {"method": "BDF", "rtol": 1e-6, "temperature_atol_k": 1e-7, "power_atol_w": 1e-3,
              "max_step_s": 10.0, "hydraulic_pressure_tolerance_pa": 0.05,
              "hydraulic_mass_tolerance_kg_s": 1e-7, "initial_residual_tolerance_w": 0.1}
  if not isinstance(raw["solver"], dict) or set(raw["solver"]) - set(defaults):
    raise PhysicsInputError("Unknown numerical solver options.")
  solver = {**defaults, **raw["solver"]}
  if solver["method"] not in {"BDF", "Radau", "RK45"}:
    raise PhysicsInputError("Supported integration methods are BDF, Radau and RK45.")
  for key in set(solver) - {"method"}:
    solver[key] = _number(solver[key], key, positive=True)
  if not isinstance(raw["assertions"], list) or any(not isinstance(a, dict) or set(a) != {"variable", "operator", "value", "unit"} or a["operator"] not in {"<=", ">="} for a in raw["assertions"]):
    raise PhysicsInputError("Assertions require variable, operator, value and unit.")
  for assertion in raw["assertions"]:
    _number(assertion["value"], "assertion value")
    if not all(isinstance(assertion[k], str) and assertion[k].strip() for k in ("variable", "unit")):
      raise PhysicsInputError("Assertion variable and unit must be non-empty strings.")
  return Scenario(identity, duration, interval, MappingProxyType(signals), initial,
                  MappingProxyType(solver), tuple(raw["assertions"]), canonical_digest(raw))
