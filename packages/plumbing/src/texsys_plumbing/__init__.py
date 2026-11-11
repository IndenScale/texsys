"""Peak conveyance and retained-volume budgets for liquid collection."""

from dataclasses import dataclass
import math


def _positive(value):
  return type(value) in {int, float} and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class Release:
  node: str
  flow_l_s: float
  duration_s: float

  def __post_init__(self):
    if not all(map(_positive, (self.flow_l_s, self.duration_s))):
      raise ValueError("Liquid releases need positive finite flow and duration.")


class CollectionNetwork:
  """One-way drains with one receiving path per source and no pump-out credit."""

  def __init__(self, segments: dict, receivers_l: dict[str, float]):
    if not receivers_l or not all(map(_positive, receivers_l.values())):
      raise ValueError("Collection receivers need positive finite usable storage.")
    self.receivers_l = receivers_l
    self.capacity = {}
    edges = {}
    for identity, segment in segments.items():
      start, end, capacity = segment["start"], segment["end"], segment["capacity_l_s"]
      if not all(isinstance(name, str) and name.strip() for name in (identity, start, end)):
        raise ValueError("Drain segment and endpoint identities must be nonempty.")
      if start in edges or start in receivers_l or start == end or not _positive(capacity):
        raise ValueError(f"Drain segment {identity} needs a unique upstream node and positive capacity.")
      edges[start] = end, identity
      self.capacity[identity] = capacity
    self.paths = {receiver: (receiver, ()) for receiver in receivers_l}
    pending = dict(edges)
    while pending:
      attached = [node for node, (target, _) in pending.items() if target in self.paths]
      if not attached:
        raise ValueError("Each drain segment must lead to a declared receiver without cycles.")
      for node in attached:
        target, identity = pending.pop(node)
        receiver, downstream = self.paths[target]
        self.paths[node] = receiver, (identity, *downstream)

  def evaluate(self, releases: dict[str, Release], unavailable: set[str]) -> dict:
    if unavailable - self.capacity.keys():
      raise ValueError(f"Unknown unavailable drain segments: {sorted(unavailable - self.capacity.keys())}.")
    peaks = dict.fromkeys(self.capacity, 0.0)
    volumes = dict.fromkeys(self.receivers_l, 0.0)
    disconnected = []
    for name, release in sorted(releases.items()):
      if release.node not in self.paths:
        raise ValueError(f"Release {name} refers to an unknown drainage inlet.")
      receiver, path = self.paths[release.node]
      if unavailable.intersection(path):
        disconnected.append(name)
        continue
      for identity in path:
        peaks[identity] += release.flow_l_s
      volumes[receiver] += release.flow_l_s * release.duration_s
    return {
      "peak_demand_l_s_by_segment": peaks,
      "storage_demand_l_by_receiver": volumes,
      "disconnected_releases": disconnected,
      "conveyance_excess_l_s": {identity: peak - self.capacity[identity] for identity, peak in peaks.items()
                               if peak - self.capacity[identity] > 1e-8},
      "storage_excess_l": {identity: volume - self.receivers_l[identity] for identity, volume in volumes.items()
                           if volume - self.receivers_l[identity] > 1e-8},
    }
