"""Steady evaporative heat rejection and cooling tower water balance."""

from dataclasses import dataclass
import math


def _positive(value):
  return type(value) in {int, float} and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class TowerWater:
  rejection_per_cooling: float
  wet_fraction: float
  latent_fraction: float
  water_density_kg_m3: float
  latent_heat_kj_kg: float
  specific_heat_kj_kgk: float
  range_k: float
  drift_fraction: float
  cycles: float
  basin_tds_limit_mg_l: float

  def __post_init__(self):
    if not all(map(_positive, (self.rejection_per_cooling, self.latent_fraction, self.water_density_kg_m3,
                              self.latent_heat_kj_kg, self.specific_heat_kj_kgk, self.range_k,
                              self.cycles, self.basin_tds_limit_mg_l))):
      raise ValueError("Tower water balance needs positive finite ratings.")
    if (self.rejection_per_cooling < 1 or self.cycles <= 1 or self.latent_fraction > 1
        or not all(type(value) in {int, float} and 0 <= value <= 1 for value in
                   (self.wet_fraction, self.drift_fraction))):
      raise ValueError("Invalid rejection multiplier, wet/latent/drift fraction or cycles of concentration.")

  @property
  def makeup_tds_limit_mg_l(self):
    return self.basin_tds_limit_mg_l / self.cycles

  def balance(self, cooling_kw):
    if not (type(cooling_kw) in {int, float} and math.isfinite(cooling_kw) and cooling_kw >= 0):
      raise ValueError("Cooling duty must be finite and nonnegative.")
    rejected = cooling_kw * self.rejection_per_cooling
    wet = rejected * self.wet_fraction
    evaporation = 3600 * wet * self.latent_fraction / (self.water_density_kg_m3 * self.latent_heat_kj_kg)
    recirculation = 3600 * wet / (self.water_density_kg_m3 * self.specific_heat_kj_kgk * self.range_k)
    drift = recirculation * self.drift_fraction
    blowdown = max(0, evaporation / (self.cycles - 1) - drift)
    return {
      "cooling_kw": cooling_kw, "rejected_kw": rejected, "wet_rejected_kw": wet,
      "recirculation_m3_h": recirculation, "evaporation_m3_h": evaporation,
      "drift_m3_h": drift, "blowdown_m3_h": blowdown, "makeup_m3_h": evaporation + drift + blowdown,
    }
