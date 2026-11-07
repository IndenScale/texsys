import { t, getLocale } from "../../../../../src/texsys/assets/studio/i18n";
import React, { useEffect, useState } from "react";
import type { Simulation, SimulationVariable } from "./types";

export const scenarioNames: Record<string, string> = {
  "load-step": "负荷阶跃",
  "pump-speed": "泵速调整",
  "pump-trip": "冷冻水泵退出",
};

export function backendName(result: Simulation): string {
  const name = typeof result.backend === "string" ? result.backend : result.backend?.name;
  if (!name) return "";
  return name === "openmodelica"
    ? "OpenModelica"
    : name === "scipy" || name.startsWith("lumped-water-network/")
      ? "SciPy"
      : name;
}

export function variableName(key: string, variable: SimulationVariable): string {
  const object = variable.semantic_ref.split(".").at(-1);
  const role =
    variable.role === "outlet-temperature"
      ? key.includes("#chilled")
        ? t("冷冻侧出口温度")
        : key.includes("#cooling")
          ? t("冷却侧出口温度")
          : t("出口温度")
      : variable.role === "mass-flow"
        ? t("质量流量")
        : t("输入功率");
  const side =
    variable.role === "mass-flow"
      ? key.includes("#chilled")
        ? t("冷冻侧")
        : key.includes("#cooling")
          ? t("冷却侧")
          : ""
      : "";
  const description = [side, role].filter(Boolean).join(getLocale() === "en" ? " " : "");
  return `${object} · ${description} (${variable.unit === "degC" ? "°C" : variable.unit})`;
}

function Plot({
  result,
  variableKey,
  index,
}: {
  result: Simulation;
  variableKey: string;
  index: number;
}) {
  const variable = result.variables[variableKey];
  if (!variable) return null;
  const values = variable.values;
  const limits = result.assertions.filter(
    (item) => item.variable === variableKey && item.value !== undefined,
  );
  const bounds = [...values, ...limits.map((item) => item.value!)];
  const min = bounds.reduce((a, b) => Math.min(a, b), Infinity),
    max = bounds.reduce((a, b) => Math.max(a, b), -Infinity);
  const pad = Math.max((max - min) * 0.15, Math.abs(max) * 0.01, 0.1);
  const low = min - pad,
    high = max + pad;
  const end = result.time_s.at(-1) || 1;
  const x = (time: number) => 60 + (time / end) * 760;
  const y = (value: number) => 125 - ((value - low) / (high - low)) * 100;
  const points = result.time_s.map((time, i) => `${x(time)},${y(values[i])}`).join(" ");
  return (
    <svg viewBox="0 0 900 155" role="img" aria-label={t("动态响应曲线")}>
      {[0, 0.5, 1].map((fraction) => (
        <g key={fraction}>
          <line
            x1="60"
            x2="820"
            y1={25 + fraction * 100}
            y2={25 + fraction * 100}
            stroke="#e0e8ed"
          />
          <text x="49" y={29 + fraction * 100} textAnchor="end" fontSize="10" fill="#718594">
            {(high - fraction * (high - low)).toFixed(1)}
          </text>
        </g>
      ))}
      {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
        <text
          key={fraction}
          x={x(end * fraction)}
          y="145"
          textAnchor="middle"
          fontSize="10"
          fill="#718594"
        >
          {Math.round(end * fraction)} s
        </text>
      ))}
      {result.events.map((event) => (
        <g key={event.time_s}>
          <line
            x1={x(event.time_s)}
            x2={x(event.time_s)}
            y1="25"
            y2="125"
            stroke="#b45309"
            strokeDasharray="4 4"
          />
          <text x={x(event.time_s) + 5} y="18" fontSize="9" fill="#9b641d">
            {t("工况变化")}
          </text>
        </g>
      ))}
      {limits.map((limit, i) => (
        <g
          key={i}
          aria-label={t("约束 {0} {1} {2}", {
            "0": limit.operator,
            "1": limit.value,
            "2": limit.unit,
          })}
        >
          <line
            x1="60"
            x2="820"
            y1={y(limit.value!)}
            y2={y(limit.value!)}
            stroke="#b05742"
            strokeDasharray="5 4"
          />
          <text x="816" y={y(limit.value!) - 5} textAnchor="end" fontSize="10" fill="#a64a35">
            {t("约束")}
            {limit.operator} {limit.value} {limit.unit === "degC" ? "°C" : limit.unit}
          </text>
        </g>
      ))}
      <polyline points={points} fill="none" stroke="#1769aa" strokeWidth="2" />
      <line
        x1={x(result.time_s[index])}
        x2={x(result.time_s[index])}
        y1="25"
        y2="125"
        stroke="#087f72"
      />
      <circle cx={x(result.time_s[index])} cy={y(values[index])} r="4" fill="#087f72" />
      <text x="834" y="30" fontSize="11" fill="#087f72">
        {values[index].toFixed(2)}
      </text>
    </svg>
  );
}

export function RunPanel({
  runs,
  runIndex,
  setRunIndex,
  index,
  setIndex,
  playing,
  setPlaying,
  selected,
  focus,
  onLocateAssertion,
  onVariableChange,
}: {
  runs: Simulation[];
  runIndex: number;
  setRunIndex: (index: number) => void;
  index: number;
  setIndex: (index: number) => void;
  playing: boolean;
  setPlaying: (playing: boolean) => void;
  selected?: string;
  focus?: { variable: string; token: number };
  onLocateAssertion?: (index: number) => void;
  onVariableChange?: (variable: string) => void;
}) {
  const result = runs[runIndex];
  const candidates = Object.entries(result.variables).filter(([, variable]) =>
    ["outlet-temperature", "mass-flow", "electric-power"].includes(variable.role),
  );
  const [choice, setChoice] = useState(candidates[0]?.[0]);
  useEffect(() => {
    const preferred = candidates.find(
      ([key, variable]) =>
        variable.semantic_ref === selected &&
        variable.role === "outlet-temperature" &&
        !key.includes("#cooling"),
    );
    if (preferred) setChoice(preferred[0]);
  }, [selected, runIndex]);
  useEffect(() => {
    if (focus?.variable && result.variables[focus.variable]) setChoice(focus.variable);
  }, [focus?.variable, focus?.token]);
  const key = result.variables[choice] ? choice : candidates[0]?.[0];
  if (!key) return <section className="run-panel">{t("此工况没有可回放的物理变量。")}</section>;
  return (
    <section className="run-panel" aria-label={t("动态工况与时序回放")}>
      <div className="run-heading">
        <div>
          <strong>{t("动态响应")}</strong>
          <span className={`run-state ${result.status}`}>
            {result.status === "pass"
              ? t("约束检查通过")
              : result.status === "fail"
                ? t("存在约束未满足")
                : t("结果待确认")}
          </span>
        </div>
        <label>
          {t("工况")}
          <select
            aria-label={t("动态工况")}
            value={runIndex}
            onChange={(event) => {
              setRunIndex(Number(event.target.value));
              setIndex(0);
              setPlaying(false);
            }}
          >
            {runs.map((run, i) => (
              <option key={`${run.scenario}:${backendName(run)}:${i}`} value={i}>
                {t(scenarioNames[run.scenario]) ?? run.scenario}
                {backendName(run) ? ` / ${backendName(run)}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("变量")}
          <select
            aria-label={t("显示变量")}
            value={key}
            onChange={(event) => {
              setChoice(event.target.value);
              onVariableChange?.(event.target.value);
            }}
          >
            {candidates.map(([name, variable]) => (
              <option key={name} value={name}>
                {variableName(name, variable)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Plot result={result} variableKey={key} index={index} />
      <div className="playback">
        <button onClick={() => setPlaying(!playing)}>{playing ? t("暂停") : t("播放")}</button>
        <button
          onClick={() => {
            setIndex(0);
            setPlaying(false);
          }}
        >
          {t("回到初始")}
        </button>
        <input
          aria-label={t("仿真时间")}
          type="range"
          min="0"
          max={result.time_s.length - 1}
          step="1"
          value={index}
          onChange={(event) => {
            setIndex(Number(event.target.value));
            setPlaying(false);
          }}
        />
        <output aria-label={t("当前仿真时间")}>{result.time_s[index].toFixed(0)} s</output>
        <button
          onClick={() => {
            const next = result.events.find((event) => event.time_s > result.time_s[index]);
            setIndex(
              next
                ? result.time_s.findIndex((time) => time >= next.time_s)
                : result.time_s.length - 1,
            );
            setPlaying(false);
          }}
        >
          {t("下一事件")}
        </button>
      </div>
      <details className="run-checks" open={onLocateAssertion ? true : undefined}>
        <summary>{t("守恒与性能检查")}</summary>
        <div>
          {t("质量残差")}
          {result.checks.mass_residual_kg_s.toExponential(2)}
          {t("kg/s；能量残差")} {result.checks.energy_residual_w.toExponential(2)}
          {t("W；积分能量误差")} {result.checks.integrated_energy_error_j.toExponential(2)} J。
        </div>
        {result.assertions.map((assertion, i) => (
          <div
            key={i}
            className={`assertion-row ${assertion.status === "fail" ? "failed-check" : ""}`}
          >
            <p>
              {assertion.status === "pass"
                ? t("通过")
                : assertion.status === "fail"
                  ? t("未满足")
                  : t("待确认")}
              ：
              {assertion.variable && result.variables[assertion.variable]
                ? variableName(assertion.variable, result.variables[assertion.variable])
                : (assertion.message ?? assertion.variable)}
              {assertion.actual !== undefined &&
                t("；极值 {0} {1}", {
                  "0": assertion.actual.toFixed(3),
                  "1": assertion.unit === "degC" ? "°C" : (assertion.unit ?? ""),
                })}
              {assertion.value !== undefined &&
                t("；要求 {0} {1} {2}", {
                  "0": assertion.operator,
                  "1": assertion.value,
                  "2": assertion.unit === "degC" ? "°C" : (assertion.unit ?? ""),
                })}
            </p>
            {onLocateAssertion && assertion.variable && result.variables[assertion.variable] && (
              <button
                className="small-button"
                onClick={() => onLocateAssertion(i)}
                aria-label={t("定位约束 {0}", { "0": i + 1 })}
              >
                {t("定位")}
                {assertion.status === "fail" ? t("越限") : t("极值")}
              </button>
            )}
          </div>
        ))}
      </details>
    </section>
  );
}
