"""HVAC compatibility names for the neutral Target BOM comparison."""

from typing import Any

from texsys.targets import BuildContext, TargetResult
from texsys_bom.compare import build_comparison as build_neutral_comparison
from texsys_bom.compare import compare_documents as compare_neutral_documents


def compare_documents(
  baseline: dict[str, Any],
  candidate: dict[str, Any],
  baseline_digest: str,
  candidate_digest: str,
) -> dict[str, Any]:
  result = compare_neutral_documents(baseline, candidate, baseline_digest, candidate_digest)
  result["target"] = "hvac:equipment-bom-diff"
  return result


def build_comparison(context: BuildContext) -> TargetResult:
  return build_neutral_comparison(context, output_directory="hvac")


__all__ = ["build_comparison", "compare_documents"]
