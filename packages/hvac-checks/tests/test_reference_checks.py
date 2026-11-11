"""Portable capacity and heat-balance references, independent of project fixtures."""

import pytest

from texsys.solver import Stimulus
from texsys_hvac_checks import DesignCheckSolver
from texsys_hvac_equipment.domain import Equipment, HvacSystem


def system(load):
  return HvacSystem(
    (Equipment("CH-01", "chiller", "analytic-reference", 100, 12, (0, 0, 0), (1, 1, 1)),),
    ("CH-01",), 0,
    {"cooling_load_kw": load, "ventilation_m3_s": 2, "air_density_kg_m3": 1.2,
     "air_cp_kj_kgk": 1, "ambient_temperature_c": 20, "relative_humidity": 1,
     "surface_temperature_c": 25},
    {"min_capacity_margin_kw": 0, "max_room_temperature_c": 30, "min_dew_point_margin_c": 0},
  )


@pytest.mark.parametrize("load,margin", [(80, 20), (120, -20)])
def test_capacity_margin_preserves_a_shortage(load, margin):
  result = DesignCheckSolver().solve(Stimulus(system(load)))
  assert result.values["capacity_margin_kw"] == pytest.approx(margin)


def test_ventilation_heat_balance_and_saturated_dew_point():
  result = DesignCheckSolver().solve(Stimulus(system(80)))
  assert result.values["temperature_rise_c"] == pytest.approx(5)
  assert result.values["room_temperature_c"] == pytest.approx(25)
  # Saturated air has dew point equal to its dry-bulb temperature.
  assert result.values["dew_point_c"] == pytest.approx(20)
  assert result.values["dew_point_margin_c"] == pytest.approx(5)
