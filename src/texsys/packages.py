"""Discover and invoke domain packages without importing domain concepts."""

from importlib import metadata
from pathlib import Path
from typing import Any

from texsys.formal import Diagnostic


class TexsysPackage:
  name = "unnamed"
  version = "0.0.0"

  def lint(self, root: Path) -> tuple[Diagnostic, ...]:
    return ()

  def format(self, root: Path, check: bool = False, diff: bool = False) -> Any | None:
    return None

  def targets(self) -> tuple[Any, ...]:
    return ()

  def build(self, root: Path) -> tuple[str, ...]:
    """Deprecated package-wide entry point; selected targets use targets()."""
    return ()

  def test(self, root: Path) -> tuple[Any, ...]:
    return ()

  def simulate(self, root: Path, kind: str) -> Any | None:
    return None

  def evaluate(self, root: Path, scenario: Path, backend: str) -> Any | None:
    """Evaluate an explicit behavior scenario; None means this package does not own it."""
    return None


def installed_packages() -> tuple[TexsysPackage, ...]:
  packages: list[TexsysPackage] = []
  names: set[str] = set()
  entry_points = metadata.entry_points(group="texsys.packages")
  for entry_point in sorted(entry_points, key=lambda item: item.name):
    package = entry_point.load()
    if not isinstance(package, TexsysPackage):
      raise TypeError(f"Invalid texsys package entry point: {entry_point.name}")
    if package.name in names:
      raise ValueError(f"Duplicate texsys package: {package.name}")
    names.add(package.name)
    packages.append(package)
  return tuple(packages)
