"""Route orthogonal service centerlines on declared installation planes."""

from collections import defaultdict
from heapq import heappop, heappush
from itertools import pairwise, product
from math import floor

from texsys_spatial import Space

from .emergency_lighting_study import _footprint_buckets


def point(p):
  return tuple(round(value, 6) for value in p)


def length(a, b):
  return sum(abs(x-y) for x, y in zip(a, b, strict=True))


def carrier(a, b, outer):
  axes = [i for i in range(3) if a[i] != b[i]]
  if len(axes) != 1:
    raise ValueError(f"Lighting carrier must be nonzero and orthogonal: {a} to {b}.")
  (axis,) = axes
  return Space(tuple(min(x,y)-(0 if i==axis else outer/2) for i,(x,y) in enumerate(zip(a,b,strict=True))),
               tuple(max(x,y)+(0 if i==axis else outer/2) for i,(x,y) in enumerate(zip(a,b,strict=True))))


def simplified(points):
  result = []
  for p in map(point,points):
    if result and p == result[-1]:
      continue
    if len(result)>1 and abs(length(result[-2],p)-length(result[-2],result[-1])-length(result[-1],p))<1e-8:
      if sum(x!=y for x,y in zip(result[-2],p,strict=True))==1:
        result.pop()
    result.append(p)
  return result


class Obstructions:
  def __init__(self, volumes):
    self.volumes = volumes
    self.keys = defaultdict(list)
    for key, shape in sorted(volumes.items()):
      for bucket in _footprint_buckets(shape.minimum_m,shape.maximum_m):
        self.keys[bucket].append(key)

  def collisions(self, shape, excluded=()):
    keys = {key for bucket in _footprint_buckets(shape.minimum_m,shape.maximum_m) for key in self.keys[bucket]}
    return sorted(key for key in keys if key not in excluded and shape.intersects(self.volumes[key]))


def _attachments(anchor, stations, radius):
  x0,y0,step,columns,rows,z = stations
  ix,iy = floor((anchor[0]-x0)/step),floor((anchor[1]-y0)/step)
  for i,j in product(range(max(0,ix-radius),min(columns,ix+radius+2)),
                     range(max(0,iy-radius),min(rows,iy+radius+2))):
    node = point((x0+i*step,y0+j*step,z))
    for turn in ((anchor[0],node[1],z),(node[0],anchor[1],z)):
      yield (i,j),simplified((anchor,turn,node))


def _attachment_penalty(path, stations, outer):
  if len(path)<2:
    return 0.
  a,b=path[:2]
  fixed=0 if a[0]==b[0] else 1
  step=stations[2]
  offset=(a[fixed]-stations[fixed])/step
  # Prohibit a long off-grid approach right beside a potential parallel main.
  # A short perpendicular splice or a route with greater offset has priority.
  if 1e-6<abs(offset-round(offset))*step<outer and length(a,b)>outer:
    return 1000.
  return 0.


def plane_routes(origin, ends, boundaries, step, outer, obstructions, *, reserve_risers=False):
  """Use a single shortest-path tree per plane and exact terminal attachments."""
  x0,y0,x1,y1 = boundaries
  columns,rows = floor((x1-x0)/step)+1,floor((y1-y0)/step)+1
  z = origin[2]
  stations = (x0,y0,step,columns,rows,z)
  if columns*rows>30000:
    raise ValueError("The configured conduit plane has more than 30000 routing stations.")

  def xyz(node):
    i,j = node
    return point((x0+i*step,y0+j*step,z))

  def available(path):
    return all(not obstructions.collisions(carrier(a,b,outer)) for a,b in pairwise(path))

  risers=Obstructions({
    name:Space((p[0]-.04,p[1]-.04,z-.03),(p[0]+.04,p[1]+.04,z+.03))
    for name,p in ends.items()
  } if reserve_risers else {})

  def grid_clear(a,b):
    span=carrier(a,b,outer)
    return not (obstructions.collisions(span) or risers.collisions(span))

  def connected(anchor):
    for radius in (1,3):
      nearby = [(node,path) for node,path in _attachments(anchor,stations,radius) if available(path)]
      if nearby:
        return nearby
    return []

  seeds, distance, parents, queue = {},{},{},[]
  entries=connected(origin)
  if entries:
    node,path=min(entries,key=lambda item:(_attachment_penalty(item[1],stations,outer)
        +sum(length(a,b) for a,b in pairwise(item[1])),item))
    cost=sum(length(a,b) for a,b in pairwise(path))
    seeds[node],distance[node],parents[node] = path,cost,None
    heappush(queue,(cost,node))

  options = {key:connected(anchor) for key,anchor in ends.items()}
  # Restrict the search to the polygonal envelope of the anchored region when the
  # declared bounds already describe a single hall corridor.
  while queue:
    cost,node = heappop(queue)
    if cost != distance[node]:
      continue
    i,j = node
    for child in ((i-1,j),(i,j-1),(i,j+1),(i+1,j)):
      p,q=child
      if not 0<=p<columns or not 0<=q<rows or cost+step+1e-8>=distance.get(child,float("inf")):
        continue
      if grid_clear(xyz(node),xyz(child)):
        distance[child],parents[child] = cost+step,node
        heappush(queue,(cost+step,child))
  result = {}
  for key,options_for_end in options.items():
    hits = [(distance[node]+sum(length(a,b) for a,b in pairwise(tail))+_attachment_penalty(tail,stations,outer),node,tail)
            for node,tail in options_for_end if node in distance]
    if not hits:
      continue
    _,node,tail = min(hits)
    steps = [xyz(node)]
    while (parent:=parents[node]) is not None:
      node=parent
      steps.append(xyz(node))
    result[key] = simplified([*seeds[node],*reversed(steps),*reversed(tail)])
  return result


def assembled_carriers(routes):
  """Merge overlapping collinear routes while retaining the circuits using each run."""
  lines = defaultdict(list)
  for item in routes.values():
    for a,b in pairwise(item["points_m"]):
      (axis,) = [i for i in range(3) if a[i]!=b[i]]
      fixed = tuple(value if i!=axis else 0. for i,value in enumerate(a))
      low,high = sorted((a[axis],b[axis]))
      lines[(item["service"],axis,fixed)].append((low,high,item["circuit"]))
  result = {}
  for (service,axis,fixed),spans in sorted(lines.items()):
    stations=sorted({edge for lo,hi,_ in spans for edge in (lo,hi)})
    previous = None
    for lo,hi in pairwise(stations):
      circuits=tuple(sorted({key for a,b,key in spans if a<=lo and hi<=b}))
      if not circuits:
        previous = None
        continue
      if previous and previous["circuits"]==circuits:
        previous["points_m"][1][axis] = hi
        previous["length_m"] = length(*previous["points_m"])
        continue
      a,b = list(fixed),list(fixed)
      a[axis],b[axis] = lo,hi
      previous = {"service":service,"points_m":[a,b],"circuits":circuits,"length_m":length(a,b)}
      result[f"EL-R{len(result)+1:04}"] = previous
  return result
