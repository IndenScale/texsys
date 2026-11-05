"""Generate the deterministic 120-instance end-to-end fixture."""

import json
from pathlib import Path

INSTANCE_COUNT = 120


def main() -> None:
  instances = {
    f"pump-{index:03d}": {"component": "Pump"}
    for index in range(INSTANCE_COUNT)
  }
  connections = [
    {
      "from": f"pump-{index:03d}.outlet",
      "to": f"pump-{index + 1:03d}.inlet",
    }
    for index in range(INSTANCE_COUNT - 1)
  ]
  document = {
    "schema_version": 1,
    "root": "LargeLoop",
    "components": {
      "Pump": {
        "ports": {
          "inlet": {"kind": "water", "direction": "in"},
          "outlet": {"kind": "water", "direction": "out"},
        }
      },
      "LargeLoop": {
        "instances": instances,
        "connections": connections,
      },
    },
  }
  target = Path(__file__).parent / "src" / "system.sdl.json"
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(
    json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
  )


if __name__ == "__main__":
  main()
