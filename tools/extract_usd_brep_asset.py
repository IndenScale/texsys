"""Extract the reviewed unit BrepArray example without rewriting its data."""

import argparse
from pathlib import Path

ROOT = Path(__file__).parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
  "source",
  type=Path,
  nargs="?",
  default=ROOT / "refs" / "openusd" / "usd-brep-proposal.md",
  help="Local upstream proposal; raw reference documents are not versioned.",
)
SOURCE = parser.parse_args().source
TARGET = ROOT / "src" / "texsys" / "assets" / "unit_brep_cube.usda"

text = SOURCE.read_text(encoding="utf-8")
section = text.index("**CubeFaceMaterials.usda**")
start = text.index("#usda 1.0", section)
end = text.index("\n```", start)
body = text[start:end].rstrip() + "\n"
header, remainder = body.split("\n", 1)
provenance = (
  "# Source: OpenUSD usd_brep proposal, CubeFaceMaterials.usda.\n"
  "# https://github.com/PixarAnimationStudios/OpenUSD-proposals\n"
  "# Upstream proposal license: Apache License 2.0.\n"
)
TARGET.parent.mkdir(parents=True, exist_ok=True)
TARGET.write_text(header + "\n" + provenance + remainder, encoding="utf-8")
