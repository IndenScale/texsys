import { t } from "../../../../../src/texsys/assets/studio/i18n";
import type { Simulation } from "./types";

/** The first sampled violation, not an interpolated physical crossing time. */
export function assertionLocation(
  result: Simulation,
  assertion: Simulation["assertions"][number],
): { index: number; reason: string } {
  const values = assertion.variable ? result.variables[assertion.variable]?.values : undefined;
  if (!values?.length) return { index: 0, reason: t("初始时刻") };
  const limit = assertion.value;
  const satisfies: Record<string, (a: number, b: number) => boolean> = {
    "<=": (a, b) => a <= b,
    "<": (a, b) => a < b,
    ">=": (a, b) => a >= b,
    ">": (a, b) => a > b,
    "==": (a, b) => a === b,
    "!=": (a, b) => a !== b,
  };
  const compare = satisfies[assertion.operator ?? ""];
  if (assertion.status === "fail" && limit !== undefined && compare) {
    const index = values.findIndex(
      (value, i) => i < result.time_s.length && Number.isFinite(value) && !compare(value, limit),
    );
    if (index >= 0) return { index, reason: t("首次越限采样点") };
  }
  if (assertion.actual !== undefined) {
    let index = 0,
      distance = Infinity;
    values.forEach((value, i) => {
      if (i >= result.time_s.length) return;
      const next = Math.abs(value - assertion.actual!);
      if (next < distance) {
        index = i;
        distance = next;
      }
    });
    return { index, reason: t("极值采样点") };
  }
  return { index: 0, reason: t("初始时刻") };
}
