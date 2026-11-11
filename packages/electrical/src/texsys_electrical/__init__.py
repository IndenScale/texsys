"""Directed power paths and active-power budgets for concept studies."""

from collections import defaultdict
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
import math


@dataclass(frozen=True)
class PowerResult:
  paths: dict[str, tuple[str, ...]]
  unserved: tuple[str, ...]
  flow_kw: dict[str, float]
  overloaded_kw: dict[str, float]
  energized: tuple[str, ...] = ()


def evaluate_power(
  links: tuple[tuple[str, str], ...],
  sources: set[str],
  capacities_kw: dict[str, float],
  demand_kw: dict[str, float],
  unavailable: set[str],
) -> PowerResult:
  """Resolve live feeds by declared incoming order, then check shared capacities."""
  values = (*capacities_kw.values(), *demand_kw.values())
  if not all(type(value) in {int, float} and math.isfinite(value) and value > 0 for value in values):
    raise ValueError("Power demand and capacities must be positive finite kW values.")
  resolved = trace_power(links, sources, set(capacities_kw), set(demand_kw), unavailable)
  flows = dict.fromkeys(sorted(capacities_kw), 0.0)
  paths = {node: resolved[node] for node in sorted(demand_kw.keys() & resolved.keys())}
  for load, path in paths.items():
    for node in path[:-1]:
      flows[node] += demand_kw[load]
  overloaded = {node: flow - capacities_kw[node] for node, flow in flows.items()
                if flow > capacities_kw[node] + 1e-8}
  return PowerResult(paths, tuple(sorted(demand_kw.keys() - paths.keys())), flows, overloaded, tuple(sorted(resolved)))


def trace_power(links, sources: set[str], distribution: set[str], loads: set[str],
                unavailable: set[str]) -> dict[str, tuple[str, ...]]:
  """Resolve energized paths for distribution buses as well as loaded terminals."""
  nodes = distribution | loads
  if not sources or sources - distribution or sources & loads:
    raise ValueError("Power sources must be explicitly rated and separate from loads.")
  if unavailable - nodes:
    raise ValueError(f"Unknown unavailable power nodes: {sorted(unavailable - nodes)}")
  incoming: dict[str, list[str]] = defaultdict(list)
  for start, end in links:
    if start not in distribution or start in loads or end not in nodes or end in sources:
      raise ValueError(f"Invalid power link {start} -> {end}.")
    if start in incoming[end]:
      raise ValueError(f"Duplicate power link {start} -> {end}.")
    incoming[end].append(start)
  graph = {node: incoming[node] for node in sorted(nodes)}
  try:
    ordered = tuple(TopologicalSorter(graph).static_order())
  except CycleError as error:
    raise ValueError("Power topology must be acyclic.") from error

  resolved: dict[str, tuple[str, ...]] = {}
  for node in ordered:
    if node in unavailable:
      continue
    if node in sources:
      resolved[node] = (node,)
    else:
      for upstream in incoming[node]:
        if upstream in resolved:
          resolved[node] = (*resolved[upstream], node)
          break
  return resolved
