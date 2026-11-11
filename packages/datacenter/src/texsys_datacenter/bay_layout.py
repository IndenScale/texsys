"""Use structural axis references to place the same racks across disciplines."""

from texsys_spatial import AxisGrid


def rack_plan(design):
  grid = AxisGrid(design["frame"], design["axes"]["x"], design["axes"]["y"])
  corner_a, corner_b = (grid.point(*axes) for axes in design["bay"])
  bounds = tuple(b - a for a, b in zip(corner_a, corner_b, strict=True))
  placements = {
    name: {
      "origin_m": tuple(p - a for p, a in zip(grid.point(*placement["anchor"], placement["offset_m"]),
                                             corner_a, strict=True)),
      "size_m": placement["size_m"],
    }
    for name, placement in sorted(design["rack_placements"].items())
  }
  return grid.frame, corner_a, bounds, placements
