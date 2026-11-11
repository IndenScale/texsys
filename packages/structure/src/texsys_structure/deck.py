"""Geometry and material quantities for a ribbed permanent form and poured slab."""

from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise
import math
from pathlib import Path

from .openings import PlanRect, slab_regions


@dataclass(frozen=True)
class DeckSlab:
  """One-way rib profile in X/Z repeated across the width and extruded along Y."""

  profile_m: tuple[tuple[float, float], ...]
  thickness_m: float
  panels: int
  span_m: float
  slab_top_m: float

  def __post_init__(self):
    values = (self.thickness_m, self.span_m, self.slab_top_m)
    if not all(type(value) in {int, float} and math.isfinite(value) and value > 0 for value in values):
      raise ValueError("Deck thickness, span and slab top must be positive and finite.")
    if type(self.panels) is not int or not 1 <= self.panels <= 1000:
      raise ValueError("Deck panels must be an integer from 1 to 1000.")
    if not 2 <= len(self.profile_m) <= 100 or any(
      len(point) != 2 or not all(type(value) in {int, float} and math.isfinite(value) and value >= 0
                                for value in point) for point in self.profile_m
    ):
      raise ValueError("Deck profile needs finite nonnegative X/Z coordinates.")
    if (self.profile_m[0][0] != 0 or self.profile_m[0][1] != self.profile_m[-1][1]
        or any(a[0] >= b[0] for a, b in pairwise(self.profile_m))):
      raise ValueError("Deck profile must start at X=0, be ordered, and have matching edge elevations.")
    if max(z for _, z in self.profile_m) + self.thickness_m >= self.slab_top_m:
      raise ValueError("The poured slab needs positive depth above the deck.")

  @property
  def width_m(self) -> float:
    return self.profile_m[-1][0] * self.panels

  def volume_m3(self) -> dict[str, float]:
    concrete_section = sum(
      (b[0] - a[0]) * (self.slab_top_m - self.thickness_m - (a[1] + b[1]) / 2)
      for a, b in pairwise(self.profile_m)
    )
    return {"steel": self.width_m * self.span_m * self.thickness_m,
            "concrete": concrete_section * self.panels * self.span_m}

  def _profile_regions(self, openings):
    pitch = self.profile_m[-1][0]
    profile = (*((x + panel * pitch, z) for panel in range(self.panels) for x, z in self.profile_m[:-1]),
               (self.width_m, self.profile_m[-1][1]))
    stations = tuple(x for x, _ in profile)

    def at(x):
      index = min(bisect_right(stations, x) - 1, len(stations) - 2)
      a, b = profile[index:index + 2]
      return x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])

    bounds = PlanRect(0, 0, self.width_m, self.span_m)
    if any(not bounds.contains(opening) for opening in openings):
      raise ValueError("Deck opening must lie within the slab footprint.")
    for region in slab_regions(bounds, openings):
      section = (at(region.left), *(point for point in profile if region.left < point[0] < region.right), at(region.right))
      yield region, section

  def net_volume_m3(self, openings: tuple[PlanRect, ...]) -> dict[str, float]:
    steel, concrete = 0., 0.
    for region, section in self._profile_regions(openings):
      span = region.top - region.bottom
      steel += (region.right - region.left) * span * self.thickness_m
      concrete += sum((b[0] - a[0]) * (self.slab_top_m - self.thickness_m - (a[1] + b[1]) / 2)
                      for a, b in pairwise(section)) * span
    return {"steel": steel, "concrete": concrete}

  def write_usd(self, path: Path, openings: tuple[PlanRect, ...] = (), *, module_geometry=False) -> None:
    from pxr import Gf, Usd, UsdGeom

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = UsdGeom.Xform.Define(stage, "/DeckSlab")
    stage.SetDefaultPrim(root.GetPrim())
    root.GetPrim().SetCustomDataByKey("scope", "concept-permanent-form")
    parent = "/DeckSlab"
    if module_geometry:
      parent += "/Geometry"
      UsdGeom.Scope.Define(stage, parent)
    regions = tuple(self._profile_regions(openings))
    if not regions:
      raise ValueError("Deck openings remove the entire slab.")
    for name, concrete, color, opacity in (
      ("SteelForm", False, (0.28, 0.38, 0.49), 1.0),
      ("PouredConcrete", True, (0.63, 0.61, 0.55), 0.45),
    ):
      points, faces = [], []
      for region, section in regions:
        start = len(points)
        for y in (region.bottom, region.top):
          for x, z in section:
            a, b = (z + self.thickness_m, self.slab_top_m) if concrete else (z, z + self.thickness_m)
            points.extend((Gf.Vec3f(x, y, a), Gf.Vec3f(x, y, b)))
        far = 2 * len(section)
        local_faces = [(0, 1, far + 1, far), (far - 2, 2 * far - 2, 2 * far - 1, far - 1)]
        for i in range(0, far - 2, 2):
          local_faces.extend(((i, i + 2, i + 3, i + 1),
                              (far + i + 1, far + i + 3, far + i + 2, far + i),
                              (i, far + i, far + i + 2, i + 2),
                              (i + 1, i + 3, far + i + 3, far + i + 1)))
        faces.extend(tuple(start + index for index in face) for face in local_faces)
      mesh = UsdGeom.Mesh.Define(stage, f"{parent}/{name}")
      mesh.CreateSubdivisionSchemeAttr("none")
      mesh.CreatePointsAttr(points)
      mesh.CreateFaceVertexCountsAttr([4] * len(faces))
      mesh.CreateFaceVertexIndicesAttr([index for face in faces for index in face])
      mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
      mesh.CreateDisplayOpacityAttr([opacity])
    if not stage.GetRootLayer().Export(str(path)):
      raise OSError(f"Unable to export {path}.")
