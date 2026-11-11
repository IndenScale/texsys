"""Path distance and shared clear-width capacity for concept egress networks."""

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import combinations, takewhile
import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


ACCESS = {"access", "door"}
PROTECTED = {"stair", "discharge"}


@dataclass(frozen=True)
class EgressLink:
  start: str
  end: str
  length_m: float
  width_m: float
  kind: str
  exit_group: str | None = None

  def __post_init__(self):
    if (self.start == self.end or self.kind not in ACCESS | PROTECTED
        or (self.kind in PROTECTED) != bool(self.exit_group)
        or not all(type(v) in {int, float} and math.isfinite(v) and v > 0 for v in (self.length_m, self.width_m))):
      raise ValueError("An egress link needs distinct endpoints, positive dimensions and a valid route class.")


def _paths(graph, start):
  pending = [(0., (), start)]
  reached = {}
  while pending:
    distance, path, node = heappop(pending)
    if node in reached:
      continue
    reached[node] = (distance, path)
    for neighbor, edge, length in graph.get(node, []):
      if neighbor not in reached:
        heappush(pending, (distance + length, (*path, edge), neighbor))
  return reached


def evaluate_egress(links, occupants, exits, minimum_width, people_per_m, *, closed_links, closed_exits,
                    width_overrides, max_access_m, min_exits, max_common_access_m):
  """Assign integer occupants to the shortest available qualified path per exit."""
  if (closed_links | width_overrides.keys()) - links.keys() or closed_exits - exits.keys():
    raise ValueError("Egress scenario names an unknown link or exit.")
  if not occupants or any(type(count) is not int or count <= 0 for count in occupants.values()):
    raise ValueError("Occupants must be positive integers at declared origins.")
  if type(min_exits) is not int or min_exits < 1:
    raise ValueError("Required exit count must be a positive integer.")
  terminals = [point for record in exits.values() for point in (*record["entries"], record["outdoor"])]
  nodes = {point for edge in links.values() for point in (edge.start, edge.end)}
  if (not exits or len(set(terminals)) != len(terminals) or set(terminals) - nodes or occupants.keys() - nodes
      or any(edge.exit_group is not None and edge.exit_group not in exits for edge in links.values())):
    raise ValueError("Each exit needs its own declared entry and outdoor nodes, with connected origins.")
  access = defaultdict(list)
  protected_reverse = {name: defaultdict(list) for name in exits}
  capacity, undersized = {}, []
  for name, edge in sorted(links.items()):
    if name in closed_links or edge.exit_group in closed_exits:
      continue
    width = width_overrides.get(name, edge.width_m)
    if type(width) not in {int, float} or not math.isfinite(width) or not 0 < width <= edge.width_m:
      raise ValueError(f"Invalid clear width of egress link {name}.")
    if width < minimum_width[edge.kind]:
      undersized.append(name)
      continue
    capacity[name] = math.floor(width * people_per_m[edge.kind] + 1e-9)
    if edge.kind in ACCESS:
      access[edge.start].append((edge.end, name, edge.length_m))
      access[edge.end].append((edge.start, name, edge.length_m))
    else:
      protected_reverse[edge.exit_group][edge.end].append((edge.start, name, edge.length_m))
  discharge = {name: _paths(protected_reverse[name], record["outdoor"])
               for name, record in sorted(exits.items()) if name not in closed_exits}
  candidates, origin_report = {}, {}
  for origin in sorted(occupants):
    walked = _paths(access, origin)
    reachable = {}
    for group, protected in discharge.items():
      entries = set(exits[group]["entries"]) & walked.keys() & protected.keys()
      if not entries:
        continue
      entry = min(entries, key=lambda node: (walked[node][0], protected[node][0], node))
      distance, path = walked[entry]
      exit_distance, reverse_path = protected[entry]
      route = {"access_m": distance, "total_m": distance + exit_distance, "entry": entry,
               "access_links": path, "links": (*path, *reversed(reverse_path))}
      reachable[group] = route
      if distance <= max_access_m + 1e-8:
        candidates[origin, group] = route
    eligible = [group for group, route in reachable.items() if route["access_m"] <= max_access_m + 1e-8]
    common = min((sum(links[a].length_m for a, _ in takewhile(lambda pair: pair[0] == pair[1], zip(
      reachable[first]["access_links"], reachable[second]["access_links"],
    ))) for first, second in combinations(eligible, 2)), default=None)
    origin_report[origin] = {
      "occupants": occupants[origin], "routes": reachable, "eligible_exits": eligible,
      "nearest_access_m": min((route["access_m"] for route in reachable.values()), default=None),
      "common_access_m": common, "common_access_pass": common is None or common <= max_common_access_m + 1e-8,
      "exit_count_pass": len(eligible) >= min_exits, "assigned": {}, "unassigned": occupants[origin],
    }
  if candidates:
    keys = tuple(candidates)
    edge_sets = [set(candidates[key]["links"]) for key in keys]
    limits = np.array([*occupants.values(), *capacity.values()])
    matrix = np.array([*[[(node == origin) for node, _ in keys] for origin in occupants],
                       *[[edge in path for path in edge_sets] for edge in capacity]], dtype=float)
    distances = np.array([route["total_m"] for route in candidates.values()])
    # One additional assigned person outweighs all distance savings.
    costs = -1 + distances / (max(1., distances.max()) * (sum(occupants.values()) + 1))
    result = milp(costs, integrality=np.ones(len(keys)), bounds=Bounds(0, np.inf),
                  constraints=LinearConstraint(matrix, 0, limits), options={"mip_rel_gap": 0})
    if not result.success:
      raise ValueError(f"Unable to assign egress demand: {result.message}")
    assigned = np.rint(result.x).astype(int)
    if any(matrix @ assigned > limits) or any(assigned < 0):
      raise ValueError("Egress allocation failed its integer capacity check.")
    for (origin, group), count in zip(keys, assigned, strict=True):
      if count:
        origin_report[origin]["assigned"][group] = int(count)
        origin_report[origin]["unassigned"] -= int(count)
    flow = dict(zip(capacity, map(int, matrix[len(occupants):] @ assigned), strict=True))
  else:
    flow = dict.fromkeys(capacity, 0)
  unassigned = sum(item["unassigned"] for item in origin_report.values())
  checks = {"exit-count-and-distance": all(item["exit_count_pass"] for item in origin_report.values()),
            "common-access-distance": all(item["common_access_pass"] for item in origin_report.values()),
            "clear-width": not undersized, "shared-capacity": unassigned == 0}
  return {
    "status": "pass" if all(checks.values()) else "fail", "checks": checks, "origins": origin_report,
    "unassigned": unassigned, "undersized_links": undersized,
    "assigned_by_exit": {group: sum(item["assigned"].get(group, 0) for item in origin_report.values()) for group in exits},
    "link_capacity": capacity, "link_assigned": flow,
    "saturated_links": [name for name in capacity if capacity[name] > 0 and capacity[name] == flow[name]],
  }
