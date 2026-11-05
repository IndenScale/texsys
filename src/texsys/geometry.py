"""Geometry-kernel abstractions and analytic installation-event checks."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Box:
  identity: str
  origin: tuple[float, float, float]
  size: tuple[float, float, float]

  def __post_init__(self) -> None:
    if any(value <= 0 for value in self.size):
      raise ValueError(f"Box dimensions must be positive: {self.identity}")

  @property
  def maximum(self) -> tuple[float, float, float]:
    return tuple(self.origin[index] + self.size[index] for index in range(3))

  @property
  def volume(self) -> float:
    return self.size[0] * self.size[1] * self.size[2]


class GeometryKernel(Protocol):
  def create_box(
    self,
    identity: str,
    origin: tuple[float, float, float],
    size: tuple[float, float, float],
  ) -> Box: ...

  def volume(self, solid: Box) -> float: ...

  def intersects(self, first: Box, second: Box, clearance: float = 0.0) -> bool: ...

  def write_usd(self, solids: tuple[Box, ...], target: Path, asset: Path) -> None: ...


class AnalyticBoxKernel:
  """Exact axis-aligned box operations; USD BRep export is a separate adapter."""

  def create_box(
    self,
    identity: str,
    origin: tuple[float, float, float],
    size: tuple[float, float, float],
  ) -> Box:
    return Box(identity, origin, size)

  def volume(self, solid: Box) -> float:
    return solid.volume

  def intersects(self, first: Box, second: Box, clearance: float = 0.0) -> bool:
    for axis in range(3):
      if (
        first.maximum[axis] + clearance <= second.origin[axis]
        or second.maximum[axis] + clearance <= first.origin[axis]
      ):
        return False
    return True

  def write_usd(self, solids: tuple[Box, ...], target: Path, asset: Path) -> None:
    from texsys.usd_brep import write_box_scene

    write_box_scene(solids, target, asset)


@dataclass(frozen=True)
class InstallationEvent:
  action: str
  identity: str


@dataclass(frozen=True)
class GeometryStimulus:
  solids: tuple[Box, ...]
  events: tuple[InstallationEvent, ...]
  clearance: float = 0.0


@dataclass(frozen=True)
class Collision:
  event_index: int
  first: str
  second: str


@dataclass(frozen=True)
class GeometrySolution:
  status: str
  collisions: tuple[Collision, ...]
  visited: tuple[str, ...]
  diagnostics: tuple[str, ...] = ()

  def as_dict(self) -> dict[str, Any]:
    return {
      "status": self.status,
      "collisions": [
        {"event_index": item.event_index, "first": item.first, "second": item.second}
        for item in self.collisions
      ],
      "visited": list(self.visited),
      "diagnostics": list(self.diagnostics),
    }


class GeometryEventSimulator:
  kind = "event"
  version = "analytic-box-events/0.1.0"

  def __init__(self, kernel: GeometryKernel) -> None:
    self.kernel = kernel

  def solve(self, stimulus: GeometryStimulus) -> GeometrySolution:
    solids = {solid.identity: solid for solid in stimulus.solids}
    placed: dict[str, Box] = {}
    collisions: list[Collision] = []
    visited: list[str] = []
    for index, event in enumerate(stimulus.events):
      if event.action != "install" or event.identity not in solids:
        return GeometrySolution(
          "error", (), tuple(visited), (f"Unsupported installation event: {event}",)
        )
      if event.identity in placed:
        return GeometrySolution(
          "error", (), tuple(visited), (f"Duplicate installation: {event.identity}",)
        )
      solid = solids[event.identity]
      for other in placed.values():
        if self.kernel.intersects(solid, other, stimulus.clearance):
          collisions.append(Collision(index, solid.identity, other.identity))
      placed[event.identity] = solid
      visited.append(event.identity)
    status = "solved" if len(visited) == len(solids) else "indeterminate"
    return GeometrySolution(status, tuple(collisions), tuple(visited))
