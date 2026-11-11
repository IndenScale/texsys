"""Radial three-phase fault envelopes and time-current grading."""

from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise
import math


def _positive(value) -> bool:
  return type(value) in {int, float} and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class Impedance:
  r_ohm: float
  x_ohm: float

  def __post_init__(self):
    if any(type(value) not in {int, float} or not math.isfinite(value) or value < 0
           for value in (self.r_ohm, self.x_ohm)):
      raise ValueError("Positive-sequence impedance needs finite nonnegative R and X.")

  def referred(self, from_kv, to_kv) -> complex:
    return complex(self.r_ohm, self.x_ohm) * (to_kv / from_kv)**2


@dataclass(frozen=True)
class SourceCondition:
  impedance: Impedance
  voltage_factor: float
  current_limit_ka: float | None = None

  def __post_init__(self):
    if not _positive(self.voltage_factor) or (self.current_limit_ka is not None and not _positive(self.current_limit_ka)):
      raise ValueError("Fault voltage factor and any declared current limit must be positive.")


@dataclass(frozen=True)
class FaultSource:
  voltage_kv: float
  minimum: SourceCondition
  maximum: SourceCondition
  sustain_s: float | None = None

  def __post_init__(self):
    if not _positive(self.voltage_kv) or (self.sustain_s is not None and not _positive(self.sustain_s)):
      raise ValueError("Fault source voltage and any declared sustain duration must be positive.")


@dataclass(frozen=True)
class Breaker:
  voltage_kv: float
  interrupt_ka: float
  # Current, earliest operation and latest clearing, in kA and seconds.
  curve: tuple[tuple[float, float, float], ...]

  def __post_init__(self):
    if (not _positive(self.voltage_kv) or not _positive(self.interrupt_ka) or len(self.curve) < 2
        or any(len(row) != 3 or not all(map(_positive, row)) or row[1] > row[2] for row in self.curve)
        or any(a[0] >= b[0] or a[1] < b[1] or a[2] < b[2] for a, b in pairwise(self.curve))):
      raise ValueError("Protection curves need increasing currents and ordered nonincreasing time bands.")

  def _line(self, log_current, factor, column):
    knots = [math.log(row[0] / factor) for row in self.curve]
    i = min(max(bisect_right(knots, log_current) - 1, 0), len(knots) - 2)
    first, last = math.log(self.curve[i][column]), math.log(self.curve[i + 1][column])
    slope = (last - first) / (knots[i + 1] - knots[i])
    return first - slope * knots[i], slope

  def _latest(self, current_ka) -> float:
    log_current = math.log(current_ka)
    a, b = self._line(log_current, 1, 2)
    return math.exp(a + b * log_current)


@dataclass(frozen=True)
class CircuitSection:
  identity: str
  reference_kv: float
  impedance: Impedance
  breaker: Breaker | None = None

  def __post_init__(self):
    if not self.identity.strip() or not _positive(self.reference_kv):
      raise ValueError("Fault path sections need an identity and an impedance reference voltage.")


def _fault_current(source, condition, fault_kv, path_impedance):
  total = path_impedance + condition.impedance.referred(source.voltage_kv, fault_kv)
  if abs(total) == 0:
    raise ValueError("The fault path must have nonzero total source and circuit impedance.")
  prospective = condition.voltage_factor * fault_kv / (math.sqrt(3) * abs(total))
  limit = condition.current_limit_ka
  if limit is not None:
    limit *= source.voltage_kv / fault_kv
  return min(prospective, limit) if limit is not None else prospective, total, prospective


def _duty(breaker, fault_kv, minimum, maximum):
  factor = fault_kv / breaker.voltage_kv
  low, high = minimum * factor, maximum * factor
  covered = breaker.curve[0][0] <= low <= high <= breaker.curve[-1][0]
  return {"voltage_kv": breaker.voltage_kv, "current_min_ka": low, "current_max_ka": high,
          "interrupt_ka": breaker.interrupt_ka, "interrupt_pass": high <= breaker.interrupt_ka,
          "pickup_pass": low >= breaker.curve[0][0], "curve_covered": covered,
          "latest_clear_s": breaker._latest(low) if covered else None}


def _grading(primary, upstream, fault_kv, minimum, maximum):
  primary_factor, upstream_factor = fault_kv / primary.voltage_kv, fault_kv / upstream.voltage_kv
  if maximum * upstream_factor < upstream.curve[0][0]:
    return "below-pickup", None, None
  if maximum * upstream_factor > upstream.curve[-1][0]:
    return "outside-curve", None, None
  low = min(maximum, max(minimum, upstream.curve[0][0] / upstream_factor))
  knots = {math.log(low), math.log(maximum)}
  for device, factor in ((primary, primary_factor), (upstream, upstream_factor)):
    knots.update(math.log(current / factor) for current, *_ in device.curve if low < current / factor < maximum)
  stations = sorted(knots)
  candidates = []
  for a, b in pairwise(stations):
    middle = (a + b) / 2
    u, m = upstream._line(middle, upstream_factor, 1)
    d, n = primary._line(middle, primary_factor, 2)
    candidates.extend((math.exp(u + m * x) - math.exp(d + n * x), x) for x in (a, b))
    # The difference of two power-law segments can have an interior extremum.
    if m < 0 and n < 0 and m != n:
      x = (math.log(-n) - math.log(-m) + d - u) / (m - n)
      if a < x < b:
        candidates.append((math.exp(u + m * x) - math.exp(d + n * x), x))
  if not candidates:
    x = stations[0]
    u, m = upstream._line(x, upstream_factor, 1)
    d, n = primary._line(x, primary_factor, 2)
    candidates.append((math.exp(u + m * x) - math.exp(d + n * x), x))
  margin, at = min(candidates)
  return "evaluated", margin, math.exp(at)


def check_radial_fault(
  source: FaultSource, fault_kv: float, sections: tuple[CircuitSection, ...], *,
  primary_max_clear_s: float, backup_max_clear_s: float | None, grading_s: float,
) -> dict:
  """Check one energized radial path against declared three-phase fault conditions."""
  if (not sections or len({section.identity for section in sections}) != len(sections)
      or not all(map(_positive, (fault_kv, primary_max_clear_s, grading_s)))
      or (backup_max_clear_s is not None and not _positive(backup_max_clear_s))):
    raise ValueError("A radial fault needs a path and positive voltage and clearing requirements.")
  impedance = {section.identity: section.impedance.referred(section.reference_kv, fault_kv) for section in sections}
  path_impedance = sum(impedance.values())
  minimum, z_min, prospective_min = _fault_current(source, source.minimum, fault_kv, path_impedance)
  maximum, z_max, prospective_max = _fault_current(source, source.maximum, fault_kv, path_impedance)
  if minimum > maximum:
    raise ValueError("The declared minimum fault current exceeds the maximum on this path.")
  devices = {section.identity: section.breaker for section in reversed(sections) if section.breaker is not None}
  duties = {name: _duty(device, fault_kv, minimum, maximum) for name, device in devices.items()}
  primary = sections[-1].identity if sections[-1].breaker else None
  upstream = [name for name in devices if name != primary]
  primary_time = duties[primary]["latest_clear_s"] if primary is not None else None
  backup = upstream[0] if upstream else None
  backup_time = duties[backup]["latest_clear_s"] if backup is not None else None
  grading = {}
  if primary_time is not None:
    for name in upstream:
      state, margin, current = _grading(devices[primary], devices[name], fault_kv, minimum, maximum)
      grading[name] = {"state": state, "margin_s": margin, "fault_current_ka": current,
                       "pass": state == "below-pickup" or (margin is not None and margin >= grading_s)}
  checks = {
    "interrupting-duty": bool(devices) and all(item["interrupt_pass"] for item in duties.values()),
    "primary-pickup-and-clearing": primary_time is not None and primary_time <= primary_max_clear_s,
    "selectivity": primary_time is not None and all(item["pass"] for item in grading.values()),
    "backup-clearing": backup_max_clear_s is None or (backup_time is not None and backup_time <= backup_max_clear_s),
    "source-sustain": source.sustain_s is None or (
      primary_time is not None and primary_time <= source.sustain_s
      and (backup_max_clear_s is None or (backup_time is not None and backup_time <= source.sustain_s))
    ),
  }
  return {
    "status": "pass" if all(checks.values()) else "fail", "checks": checks,
    "fault_voltage_kv": fault_kv, "current_min_ka": minimum, "current_max_ka": maximum,
    "voltage_source_current_min_ka": prospective_min, "voltage_source_current_max_ka": prospective_max,
    "path_impedance_ohm": {name: {"r": z.real, "x": z.imag} for name, z in impedance.items()},
    "total_impedance_ohm": {"minimum_current": {"r": z_min.real, "x": z_min.imag},
                            "maximum_current": {"r": z_max.real, "x": z_max.imag}},
    "primary": primary, "backup": backup, "devices": duties, "grading": grading,
    "source_sustain_s": source.sustain_s,
    "requirements_s": {"primary_max_clear": primary_max_clear_s, "backup_max_clear": backup_max_clear_s,
                       "grading": grading_s},
  }
