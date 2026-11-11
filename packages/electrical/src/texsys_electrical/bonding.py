"""Continuity and conservative single-path resistance of a bonding network."""

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
import math


def _positive(value):
  return type(value) in {int, float} and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class Bond:
  start: str
  end: str
  resistance_mohm: float

  def __post_init__(self):
    if not self.start.strip() or not self.end.strip() or self.start == self.end or not _positive(self.resistance_mohm):
      raise ValueError("A bond needs distinct terminals and positive finite resistance.")


def conductor_resistance_mohm(length_m: float, cross_section_mm2: float, resistivity_ohm_mm2_m: float) -> float:
  if not all(map(_positive, (length_m, cross_section_mm2, resistivity_ohm_mm2_m))):
    raise ValueError("Conductor length, cross section and resistivity must be positive and finite.")
  return 1000 * resistivity_ohm_mm2_m * length_m / cross_section_mm2


class BondNetwork:
  def __init__(self, nodes: set[str], bonds: dict[str, Bond], references: set[str]):
    if not references or references - nodes or any(not name.strip() for name in nodes | bonds.keys()):
      raise ValueError("Bond network needs named nodes, links and declared reference terminals.")
    if any({bond.start, bond.end} - nodes for bond in bonds.values()):
      raise ValueError("Every bond must connect declared terminals.")
    self.nodes, self.bonds, self.references = nodes.copy(), bonds.copy(), references.copy()

  def evaluate(self, terminals: set[str], unavailable: set[str], max_path_mohm: float) -> dict:
    if not terminals or terminals - self.nodes or terminals & self.references or not _positive(max_path_mohm):
      raise ValueError("Bond checks need declared load terminals and a positive path resistance budget.")
    if unavailable - self.bonds.keys():
      raise ValueError(f"Unknown unavailable bonds: {sorted(unavailable - self.bonds.keys())}.")
    adjacent = defaultdict(list)
    for name, bond in sorted(self.bonds.items()):
      if name not in unavailable:
        adjacent[bond.start].append((bond.end, name, bond.resistance_mohm))
        adjacent[bond.end].append((bond.start, name, bond.resistance_mohm))
    by_reference = {reference: _shortest_paths(adjacent, reference) for reference in sorted(self.references)}
    results = {}
    for terminal in sorted(terminals):
      reachable = [reference for reference, paths in by_reference.items() if terminal in paths]
      if not reachable:
        results[terminal] = {"status": "disconnected", "reachable_references": [], "path_mohm": None, "bonds": []}
        continue
      resistance, reference = min((by_reference[ref][terminal][0], ref) for ref in reachable)
      current, path = terminal, []
      while current != reference:
        _, current, bond = by_reference[reference][current]
        path.append(bond)
      results[terminal] = {
        "status": "pass" if resistance <= max_path_mohm else "over-budget",
        "reachable_references": reachable, "selected_reference": reference, "path_mohm": resistance, "bonds": path,
      }
    return {
      "terminals": results, "unavailable_bonds": sorted(unavailable),
      "disconnected": [name for name, item in results.items() if item["status"] == "disconnected"],
      "over_budget": [name for name, item in results.items() if item["status"] == "over-budget"],
    }


def _shortest_paths(adjacent, reference):
  paths = {reference: (0.0, "", "")}
  pending = [(0.0, reference)]
  while pending:
    resistance, node = heappop(pending)
    if resistance != paths[node][0]:
      continue
    for target, bond, edge_resistance in adjacent[node]:
      candidate = resistance + edge_resistance
      if target not in paths or candidate < paths[target][0]:
        paths[target] = (candidate, node, bond)
        heappush(pending, (candidate, target))
  return paths
