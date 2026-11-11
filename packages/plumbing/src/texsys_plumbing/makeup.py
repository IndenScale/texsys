"""Treated makeup-water supply, storage and shared discharge budgets."""

from dataclasses import dataclass
import math

from scipy.optimize import linprog


_EPS = 1e-8


def _finite(value, *, positive=False):
  return type(value) in {int, float} and math.isfinite(value) and (value > 0 if positive else value >= 0)


@dataclass(frozen=True)
class WaterSource:
  capacity_m3_h: float
  tds_mg_l: float

  def __post_init__(self):
    if not _finite(self.capacity_m3_h, positive=True) or not _finite(self.tds_mg_l):
      raise ValueError("A water source needs finite withdrawal capacity and source TDS.")


@dataclass(frozen=True)
class Treatment:
  sources: tuple[str, ...]
  capacity_m3_h: float
  recovery: float
  tds_pass_fraction: float
  max_feed_tds_mg_l: float

  def __post_init__(self):
    if (not isinstance(self.sources, (list, tuple)) or not self.sources or len(set(self.sources)) != len(self.sources)
        or any(not isinstance(name, str) or not name.strip() for name in self.sources)
        or not all(_finite(value, positive=True) for value in (self.capacity_m3_h, self.max_feed_tds_mg_l, self.recovery))
        or self.recovery >= 1 or not _finite(self.tds_pass_fraction) or self.tds_pass_fraction > 1):
      raise ValueError("Treatment needs unique sources, capacity, a recovery below one and a valid quality envelope.")


class MakeupNetwork:
  """Budget qualified water into a common usable tank with a shared outfall."""

  def __init__(self, sources, treatments, *, storage_m3, initial_m3, initial_tds_mg_l, product_limit_mg_l):
    if (not sources or not treatments or any(set(train.sources) - sources.keys() for train in treatments.values())
        or not _finite(storage_m3, positive=True) or not _finite(initial_m3) or initial_m3 > storage_m3
        or not _finite(product_limit_mg_l, positive=True) or not _finite(initial_tds_mg_l)
        or (initial_m3 > 0 and initial_tds_mg_l > product_limit_mg_l)):
      raise ValueError("Makeup water needs declared sources, qualified initial inventory and usable tank capacity.")
    self.sources, self.treatments = sources, treatments
    self.storage, self.initial = storage_m3, initial_m3
    self.product_limit = product_limit_mg_l

  def _routes(self, unavailable_sources, unavailable_treatments, tds_overrides):
    if (unavailable_sources | tds_overrides.keys()) - self.sources.keys() or unavailable_treatments - self.treatments.keys():
      raise ValueError("Water supply state names an unknown source or treatment train.")
    if not all(map(_finite, tds_overrides.values())):
      raise ValueError("Source TDS overrides must be finite and nonnegative.")
    routes, excluded = [], {}
    for name, treatment in sorted(self.treatments.items()):
      for source in sorted(treatment.sources):
        tds = tds_overrides.get(source, self.sources[source].tds_mg_l)
        product = tds * treatment.tds_pass_fraction
        reasons = []
        if source in unavailable_sources or name in unavailable_treatments:
          reasons.append("unavailable")
        if tds > treatment.max_feed_tds_mg_l:
          reasons.append("feed-tds")
        if product > self.product_limit:
          reasons.append("product-tds")
        if reasons:
          excluded[f"{source}/{name}"] = reasons
        else:
          routes.append((source, name, treatment.recovery, tds, product))
    return routes, excluded

  def _dispatch(self, routes, outfall, makeup, blowdown, fraction=None, *, cap_product=False):
    # The final variable is the supported cooling fraction.
    recoveries = [route[2] for route in routes]
    rows = [[*(int(source == name) for source, *_ in routes), 0] for name in self.sources]
    rhs = [source.capacity_m3_h for source in self.sources.values()]
    rows.extend([*(int(train == name) for _, train, *_ in routes), 0] for name in self.treatments)
    rhs.extend(train.capacity_m3_h for train in self.treatments.values())
    rows.append([*(1 - recovery for recovery in recoveries), blowdown])
    rhs.append(outfall)
    if fraction is None:
      rows.append([*(-value for value in recoveries), makeup])
      rhs.append(0)
    elif cap_product:
      rows.append([*recoveries, -makeup])
      rhs.append(0)
    objective = [*([0] * len(routes)), -1] if fraction is None else [*(-r for r in recoveries), 0]
    limits = [(0, None)] * len(routes) + ([(0, 1)] if fraction is None else [(fraction, fraction)])
    result = linprog(objective, A_ub=rows, b_ub=rhs, bounds=limits, method="highs")
    if not result.success:
      raise ValueError(f"Unable to dispatch makeup supply: {result.message}")
    return float(result.x[-1]), [float(value) for value in result.x[:-1]]

  def simulate(self, intervals):
    if not intervals or intervals[0]["start_h"] != 0:
      raise ValueError("Makeup schedule must begin at hour zero.")
    tank, expected_start = self.initial, 0.0
    rows = []
    for record in intervals:
      start, end = record["start_h"], record["end_h"]
      makeup, blowdown, outfall = (record[key] for key in ("makeup_m3_h", "blowdown_m3_h", "outfall_m3_h"))
      if (not all(map(_finite, (start, end, makeup, blowdown, outfall))) or start != expected_start or end <= start
          or blowdown > makeup):
        raise ValueError("Makeup schedules need contiguous positive intervals and consistent nonnegative flows.")
      expected_start = end
      routes, excluded = self._routes(set(record["unavailable_sources"]), set(record["unavailable_treatments"]),
                                      record.get("tds_overrides_mg_l", {}))
      while start < end:
        if tank <= _EPS:
          fraction, _ = self._dispatch(routes, outfall, makeup, blowdown)
          fraction, feed = self._dispatch(routes, outfall, makeup, blowdown, fraction, cap_product=True)
          # After meeting demand, available production can recharge an empty tank.
          if fraction == 1:
            fraction, feed = self._dispatch(routes, outfall, makeup, blowdown, 1)
        else:
          fraction = min(1., outfall / blowdown) if blowdown else 1.
          fraction, feed = self._dispatch(routes, outfall, makeup, blowdown, fraction,
                                          cap_product=tank >= self.storage - _EPS)
        production = sum(rate * route[2] for rate, route in zip(feed, routes, strict=True))
        consumed = makeup * fraction
        net = production - consumed
        stop = end
        if net < -_EPS:
          stop = min(stop, start + tank / -net)
        elif net > _EPS:
          stop = min(stop, start + (self.storage - tank) / net)
        if stop <= start:
          raise ValueError("Makeup dispatch did not advance the tank state.")
        updated = min(self.storage, max(0., tank + net * (stop - start)))
        raw = sum(feed)
        reject = raw - production
        discharge = blowdown * fraction + reject
        if discharge > outfall + _EPS or abs(updated - tank - net * (stop - start)) > 1e-6:
          raise ValueError("Makeup dispatch failed its water or discharge balance.")
        rows.append({
          "start_h": start, "end_h": stop, "tank_start_m3": tank, "tank_end_m3": updated,
          "wet_duty_fraction": fraction, "makeup_demand_m3_h": makeup, "makeup_used_m3_h": consumed,
          "raw_m3_h": raw, "produced_m3_h": production, "treatment_reject_m3_h": reject,
          "withdrawal_m3_h_by_source": {name: sum(rate for route, rate in zip(routes, feed, strict=True)
                                                if route[0] == name) for name in self.sources},
          "raw_m3_h_by_treatment": {name: sum(rate for route, rate in zip(routes, feed, strict=True)
                                             if route[1] == name) for name in self.treatments},
          "blowdown_m3_h": blowdown * fraction, "total_discharge_m3_h": discharge, "outfall_m3_h": outfall,
          "excluded_routes": excluded,
          "production_routes": {f"{source}/{train}": {"raw_m3_h": rate, "product_m3_h": rate * recovery,
                                                       "feed_tds_mg_l": tds, "product_tds_mg_l": product,
                                                       "reject_tds_mg_l": (tds - recovery * product) / (1 - recovery)}
                                for (source, train, recovery, tds, product), rate in zip(routes, feed, strict=True)
                                if rate > _EPS},
        })
        start, tank = stop, updated
    return rows
