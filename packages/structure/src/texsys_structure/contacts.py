"""Screen concentrated bearing contacts on a slab's intact local section."""

from math import isfinite

from .openings import PlanRect


def screen_slab_contact(*, x, y, size_m, load_kn, slab, holes, effective_depth_m,
                        bearing_limit_kpa, punching_limit_kpa):
  width, length = size_m
  if not all(type(value) in (int, float) and isfinite(value) and value > 0 for value in
             (width, length, load_kn, effective_depth_m, bearing_limit_kpa, punching_limit_kpa)):
    raise ValueError("Slab contacts need positive finite loads, contact dimensions and screening limits.")
  bearing = PlanRect(x - width / 2, y - length / 2, x + width / 2, y + length / 2)
  perimeter = bearing.inset(-effective_depth_m / 2)
  intact = slab.contains(perimeter) and not any(hole.overlaps(perimeter) for hole in holes)
  pressure = load_kn / (width * length)
  perimeter_m = 2 * (width + length + 2 * effective_depth_m)
  stress = load_kn / (perimeter_m * effective_depth_m) if intact else None
  checks = {"bearing": pressure <= bearing_limit_kpa, "intact-punching-perimeter": intact,
            "punching": stress is not None and stress <= punching_limit_kpa}
  return {"contact_xyxy_m": bearing.xyxy, "punching_xyxy_m": perimeter.xyxy,
          "factored_load_kn": load_kn, "bearing_kpa": pressure, "punching_kpa": stress,
          "punching_perimeter_m": perimeter_m, "checks": checks,
          "status": "pass" if all(checks.values()) else "fail"}
