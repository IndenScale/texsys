"""HVAC-named entry point for the neutral Target BOM checker."""

from texsys_bom.checker import run_checker


def main() -> None:
  run_checker("hvac-check-bom/0.1.0")


if __name__ == "__main__":
  main()
