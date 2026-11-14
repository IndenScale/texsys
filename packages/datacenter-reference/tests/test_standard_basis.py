"""Verify the design basis against the GB 50174-2017 standards package.

Every cited rule key must resolve in the standards package chapter/appendix
data, citations must match the recorded quote and locator verbatim, and the
project thresholds must equal the structured A-grade limits. Skipped when the
sibling standards workspace is not present (e.g. standalone CI checkout).
"""

import json
from pathlib import Path
import tomllib

import pytest

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "datacenter-reference-design"
STANDARD = EXAMPLE.parents[1].parent / "texsys-gb50174-2017"
RULES_DIR = STANDARD / "src" / "texsys_gb50174_2017"


@pytest.fixture(scope="module")
def basis():
  path = EXAMPLE / "src" / "design-basis.json"
  if not path.is_file():
    pytest.skip("reference design example not present")
  return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rules():
  if not (RULES_DIR / "chapter03.json").is_file():
    pytest.skip("texsys-gb50174-2017 standards workspace not present")
  registry = {}
  for path in sorted(RULES_DIR.glob("chapter*.json")) + sorted(RULES_DIR.glob("appendix_*.json")):
    for key, rule in json.loads(path.read_text(encoding="utf-8")).get("rules", {}).items():
      registry[key] = rule
  return registry


def test_standard_version_matches(basis):
  if not (STANDARD / "texsys.toml").is_file():
    pytest.skip("texsys-gb50174-2017 standards workspace not present")
  manifest = tomllib.loads((STANDARD / "texsys.toml").read_text(encoding="utf-8"))
  assert basis["standard"]["package"] == manifest["project"]["name"]
  assert basis["standard"]["version"] == manifest["project"]["version"]


def test_every_cited_rule_resolves(basis, rules):
  cited = list(basis["grade"]["rules"])
  for requirement in basis["requirements"]:
    cited.extend(requirement["rules"])
  missing = [key for key in cited if key not in rules]
  assert missing == [], missing


def test_citations_match_recorded_text(basis, rules):
  entries = [basis["grade"]] + basis["requirements"]
  for entry in entries:
    citation = entry["citation"]
    assert citation["rule"] in entry["rules"]
    record = rules[citation["rule"]]
    assert citation["quote"] == record["quote"], entry.get("id", "grade")
    assert citation["locator"] == record["locator"], entry.get("id", "grade")
    assert citation["pdf_page"] == record["pdf_page"], entry.get("id", "grade")


def test_thresholds_match_a_grade_limits(basis, rules):
  params = {r["id"]: r.get("parameters", {}) for r in basis["requirements"]}
  assert params["GB-5.1.1"]["inlet_min_c"] == rules["A.environment.inlet-temperature"]["limits"]["temperature_c"][0]
  assert params["GB-5.1.1"]["inlet_max_c"] == rules["A.environment.inlet-temperature"]["limits"]["temperature_c"][1]
  assert params["GB-6.1.3"]["min_clear_height_m"] == rules["6.1.3.height"]["minimum_m"]
  assert params["GB-8.1.7"]["battery_autonomy_min"] == rules["A.power.battery-duration"]["checks"]["A"]["minimum_minutes"]
  assert params["GB-8.1.15"]["min_fuel_hours"] == rules["A.power.fuel-duration"]["checks"]["A"]["minimum_hours"]
  assert params["GB-13.2.1"]["max_fire_resistance_grade"] == rules["13.2.1.fire-resistance-grade"]["maximum_grade"]
  assert params["GB-13.2.4"]["min_wall_fire_hours"] * 60 == rules["13.2.4.wall"]["minimum_minutes"]
  assert params["GB-13.2.4"]["min_slab_fire_hours"] * 60 == rules["13.2.4.slab"]["minimum_minutes"]
