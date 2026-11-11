"""Authorize a backup only after every isolation point is confirmed."""

from dataclasses import dataclass
from math import isfinite


def _time(value):
  if type(value) not in (int, float) or not isfinite(value) or value < 0:
    raise ValueError("Control times must be finite and nonnegative.")
  return value


@dataclass(frozen=True)
class Observation:
  point: str
  value: bool
  sample_s: float
  quality: str = "good"

  def __post_init__(self):
    _time(self.sample_s)
    if not self.point or type(self.value) is not bool or self.quality not in ("good", "bad", "stale"):
      raise ValueError("Invalid binary observation or quality.")


class ConfirmedChangeover:
  """Process observations, emit commands and fail closed on missing proof."""

  def __init__(self, fault_point, isolation, enable_target, proof_point, isolation_timeout_s, proof_timeout_s):
    isolation = dict(isolation)
    points = {fault_point, proof_point, *isolation.values()}
    targets = {*isolation, enable_target}
    if (not isolation or len(points) != len(isolation) + 2 or len(targets) != len(isolation) + 1
        or any(not isinstance(name, str) or not name.strip() for name in points | targets)
        or not _time(isolation_timeout_s) or not _time(proof_timeout_s)):
      raise ValueError("Changeover needs distinct points, targets and positive timeouts.")
    self.fault_point, self.proof_point, self.enable_target = fault_point, proof_point, enable_target
    self.isolation = isolation
    self.isolation_timeout_s, self.proof_timeout_s = isolation_timeout_s, proof_timeout_s
    self.state = "armed"
    self.now_s = 0.
    self.deadline_s = None
    self.close_command_s = None
    self.enable_command_s = None
    self.confirmed = set()
    self.latest_samples = {}
    self.trace = []

  def _emit(self, kind, **detail):
    self.trace.append({"at_s": self.now_s, "kind": kind, **detail})

  def _command(self, action, target):
    self._emit("command", action=action, target=target)

  def _lockout(self, reason):
    if self.state in ("starting", "proved"):
      self._command("disable", self.enable_target)
    self.state = "lockout"
    self.deadline_s = None
    self._emit("lockout", reason=reason)

  def advance(self, now_s):
    if _time(now_s) < self.now_s:
      raise ValueError("Control time must advance monotonically.")
    if self.deadline_s is not None and now_s >= self.deadline_s:
      self.now_s = self.deadline_s
      self._lockout("isolation-timeout" if self.state == "isolating" else "proof-timeout")
    self.now_s = now_s

  def receive(self, now_s, observation):
    point, value, sample_s, quality = observation.point, observation.value, observation.sample_s, observation.quality
    if sample_s > now_s or point not in {self.fault_point, self.proof_point, *self.isolation.values()}:
      raise ValueError("Observation refers to an unknown point or a future sample.")
    self.advance(now_s)
    self._emit("observation", point=point, value=value, sample_s=sample_s, quality=quality)
    if quality == "good":
      if sample_s <= self.latest_samples.get(point, -1.):
        self._emit("ignored", point=point, reason="superseded-sample")
        return
      self.latest_samples[point] = sample_s
    if self.state == "armed" and point == self.fault_point:
      if quality != "good":
        self._lockout("fault-point-unavailable")
      elif value:
        self.state = "isolating"
        self.close_command_s = now_s
        self.deadline_s = now_s + self.isolation_timeout_s
        for actuator in self.isolation:
          self._command("close", actuator)
      return

    if self.state not in ("isolating", "starting", "proved"):
      return
    if point == self.fault_point:
      return
    command_s = self.enable_command_s if point == self.proof_point else self.close_command_s
    if command_s is None:
      return
    if sample_s <= command_s:
      if quality != "good" and self.state in ("starting", "proved"):
        self._lockout("stale-safety-observation")
      return
    if point in self.isolation.values():
      if quality == "good" and value:
        self.confirmed.add(point)
        if self.state == "isolating" and self.confirmed == set(self.isolation.values()):
          self.state = "starting"
          self.enable_command_s = now_s
          self.deadline_s = now_s + self.proof_timeout_s
          self._command("enable", self.enable_target)
      else:
        self.confirmed.discard(point)
        if self.state in ("starting", "proved"):
          self._lockout("isolation-proof-lost")
    elif point == self.proof_point:
      if self.state == "starting" and quality == "good" and value:
        self.state = "proved"
        self.deadline_s = None
        self._emit("backup-proved", point=point)
      elif self.state == "proved" and (quality != "good" or not value):
        self._lockout("backup-proof-lost")
