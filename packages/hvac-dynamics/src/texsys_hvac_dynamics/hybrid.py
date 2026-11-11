"""Rated-point heat budgets for a two-loop CDU and room air cooling."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class LiquidLoop:
  mass_flow_kg_s: float
  specific_heat_kj_kgk: float
  supply_c: float
  return_c: float

  def __post_init__(self) -> None:
    values = (self.mass_flow_kg_s, self.specific_heat_kj_kgk, self.supply_c, self.return_c)
    if not all(type(value) in {int, float} and math.isfinite(value) for value in values):
      raise ValueError("Liquid loop values must be finite numbers.")
    if min(self.mass_flow_kg_s, self.specific_heat_kj_kgk) <= 0 or self.return_c <= self.supply_c:
      raise ValueError("Liquid loop needs positive flow, heat capacity and temperature rise.")

  @property
  def heat_kw(self) -> float:
    return self.mass_flow_kg_s * self.specific_heat_kj_kgk * (self.return_c - self.supply_c)


@dataclass(frozen=True)
class CduRating:
  facility: LiquidLoop
  technology: LiquidLoop
  nameplate_kw: float
  min_approach_c: float

  def __post_init__(self) -> None:
    if not all(type(value) in {int, float} and math.isfinite(value) and value > 0
               for value in (self.nameplate_kw, self.min_approach_c)):
      raise ValueError("CDU capacity and minimum approach must be positive finite numbers.")

  @property
  def terminal_approach_c(self) -> float:
    return min(self.technology.supply_c - self.facility.supply_c,
               self.technology.return_c - self.facility.return_c)

  @property
  def available_kw(self) -> float:
    if self.terminal_approach_c < self.min_approach_c:
      return 0.0
    return min(self.nameplate_kw, self.facility.heat_kw, self.technology.heat_kw)


def split_it_heat(it_kw: float, liquid_fraction: float) -> tuple[float, float]:
  if type(it_kw) not in {int, float} or not math.isfinite(it_kw) or it_kw <= 0:
    raise ValueError("IT load must be positive finite kW.")
  if type(liquid_fraction) not in {int, float} or not 0 <= liquid_fraction <= 1:
    raise ValueError("IT liquid heat fraction must be between 0 and 1.")
  liquid_kw = it_kw * liquid_fraction
  return liquid_kw, it_kw - liquid_kw
