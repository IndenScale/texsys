"""Static HVAC rules kept outside the SDL core."""

import json
from pathlib import Path

from texsys.formal import Diagnostic
from texsys.packages import TexsysPackage
from texsys_hvac_equipment.domain import domain_path, has_domain, load_system


class HvacLinterPackage(TexsysPackage):
  name = "hvac-linter"
  version = "0.1.0"

  def lint(self, root: Path) -> tuple[Diagnostic, ...]:
    if not has_domain(root):
      return ()
    try:
      document = json.loads(domain_path(root).read_text(encoding="utf-8"))
      if isinstance(document, dict) and document.get("schema_version") == 2:
        return ()  # The Target BOM package owns schema v2 validation.
      load_system(root)
    except (OSError, json.JSONDecodeError, ValueError) as error:
      return (Diagnostic("H001", "fail", "src/hvac.json", str(error)),)
    return ()


package = HvacLinterPackage()
