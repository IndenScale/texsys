import React, { useCallback, useEffect, useState } from "react";
import type { WorkbenchProps } from "./sdk";
import { t } from "./i18n";
import "./engineering.css";

type Input = {
  id: string;
  label: string;
  file: string;
  pointer: string;
  unit: string;
  object_ref: string;
  value: number;
  min?: number;
  max?: number;
};
type Check = {
  id: string;
  label: string;
  object_ref: string;
  status: string;
  actual?: number;
  limit?: number;
  operator?: string;
  unit?: string;
  scenario?: string;
  message?: string;
};
type Extraction = {
  id: string;
  label: string;
  object_ref: string;
  value: unknown;
  unit: string;
  source: unknown;
  state?: string;
  scenario?: string;
  details?: unknown;
};
type Run = {
  run_id: string;
  revision: string;
  evidence_digest: string;
  created_at?: string;
  execution: string;
  current: boolean;
  freshness: string;
  acceptance: unknown;
  diagnostics?: { code?: string; message: string }[];
  result: {
    assessment: string;
    checks: Check[];
    extraction: Extraction[];
    limitations: string[];
    targets: string[];
    [key: string]: unknown;
  };
};
type EngineeringState = {
  available: boolean;
  revision: string;
  selected: { id: string; name: string; version: string } | null;
  inputs: Input[];
  runs: Run[];
  latest: Run | null;
  reviews: unknown[];
  dependency_error?: string;
  diagnostics?: { message: string }[];
};
type ChangeRequest = {
  adapter: string;
  revision: string;
  changes: { input_id: string; value: number }[];
};
type Plan = {
  status: string;
  revision: string;
  plan: string;
  diff: string;
  changes: { input_id: string; before: number; after: number; unit: string; label: string }[];
  impacts: unknown[];
  coverage: string;
};

const statuses: Record<string, string> = {
  pass: "满足",
  fail: "不满足",
  indeterminate: "无法判定",
  error: "执行错误",
  completed: "计算完成",
  current: "适用于当前输入",
  stale: "输入已变化，待重算",
  accepted: "已记录接受",
  unreviewed: "待审查",
  "not-accepted": "待审查",
  "needs-review": "受变更影响，待复核",
  "inputs-changed": "输入已变化，待重算",
  "tools-changed": "计算工具已变化，待重算",
  "dependency-invalid": "工程依赖需要核实",
  "adapter-changed": "验证方法已变化，待重算",
  assumed: "假设",
  known: "已知",
};
function label(value: string) {
  return t(statuses[value] ?? value);
}
function number(value: unknown) {
  return typeof value === "number"
    ? value.toLocaleString(undefined, { maximumFractionDigits: 4 })
    : value == null
      ? "—"
      : String(value);
}
function acceptance(run: Run) {
  const value = run.acceptance;
  if (typeof value === "string") return label(value);
  if (value && typeof value === "object" && "status" in value) return label(String(value.status));
  return t("待审查");
}

export function EngineeringDocument({
  api,
  refresh,
  revision,
  openFile,
  hasUnsaved,
}: {
  api: WorkbenchProps["api"];
  refresh: WorkbenchProps["refresh"];
  revision: string;
  openFile: (file: string) => void;
  hasUnsaved: boolean;
}) {
  const [data, setData] = useState<EngineeringState>();
  const [values, setValues] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<Plan>();
  const [request, setRequest] = useState<ChangeRequest>();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [selectedRun, setSelectedRun] = useState<string>();
  const [actor, setActor] = useState("");
  const [rationale, setRationale] = useState("");
  const [checkMode, setCheckMode] = useState("all");
  const [visibleChecks, setVisibleChecks] = useState(10);
  const [visibleExtraction, setVisibleExtraction] = useState(10);
  const [query, setQuery] = useState("");
  const load = useCallback(async () => {
    const next = await api<EngineeringState>("/api/engineering");
    setData(next);
    setValues(
      Object.fromEntries((next.inputs ?? []).map((input) => [input.id, String(input.value)])),
    );
    return next;
  }, [api]);
  useEffect(() => {
    setPlan(undefined);
    setRequest(undefined);
    void load().catch((failure) => setError(String(failure)));
  }, [load, revision]);

  async function perform(name: string, action: () => Promise<void>) {
    setBusy(name);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (failure) {
      setError(String(failure));
    } finally {
      setBusy("");
    }
  }
  async function preview() {
    if (!data?.selected) return;
    const changes = data.inputs
      .filter((input) => Number(values[input.id]) !== input.value)
      .map((input) => ({ input_id: input.id, value: Number(values[input.id]) }));
    if (!changes.length) throw new Error(t("请先修改一个参数。"));
    if (
      data.inputs.some(
        (input) => !values[input.id]?.trim() || !Number.isFinite(Number(values[input.id])),
      )
    )
      throw new Error(t("参数必须是有限数值。"));
    const next = { adapter: data.selected.id, revision: data.revision, changes };
    const result = await api<Plan>("/api/engineering/plan", next);
    if (result.status !== "pass") throw new Error(t("输入已变化，请刷新并重新预览。"));
    setRequest(next);
    setPlan(result);
  }
  async function apply() {
    if (!plan || !request) return;
    const result = await api<{ status: string }>("/api/engineering/apply", {
      request,
      plan: plan.plan,
    });
    if (result.status !== "pass") throw new Error(t("计划已过期，请刷新并重新预览。"));
    setPlan(undefined);
    setRequest(undefined);
    await load();
    await refresh();
    setMessage(t("变更已应用。历史结论保留，请重算以更新证据。"));
  }
  async function run() {
    if (!data?.selected) return;
    const result = await api<Run>("/api/engineering/run", { adapter: data.selected.id });
    setSelectedRun(result.run_id);
    setVisibleChecks(10);
    setVisibleExtraction(10);
    await load();
    await refresh();
    setMessage(t("计算记录已保存，请查看工程判定与适用条件。"));
  }
  const selected = data?.runs.find((item) => item.run_id === selectedRun) ?? data?.latest;
  const previous =
    selected && data?.runs[data.runs.findIndex((item) => item.run_id === selected.run_id) + 1];
  const previousChecks = new Map(previous?.result.checks?.map((check) => [check.id, check]) ?? []);
  const previousExtraction = new Map(
    previous?.result.extraction?.map((item) => [item.id, item]) ?? [],
  );
  const changed = data?.inputs.some((input) => values[input.id] !== String(input.value));
  const canAccept =
    selected?.current &&
    selected.execution === "completed" &&
    selected.result.assessment === "pass";
  const checks = (selected?.result.checks ?? []).filter(
    (check) =>
      (checkMode === "all" || check.status !== "pass") &&
      `${check.label} ${check.object_ref} ${check.scenario ?? ""}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const extraction = (selected?.result.extraction ?? []).filter((item) =>
    `${item.label} ${item.object_ref}`.toLowerCase().includes(query.toLowerCase()),
  );
  async function accept() {
    if (!data?.selected || !selected) return;
    await api("/api/engineering/accept", {
      adapter: data.selected.id,
      run_id: selected.run_id,
      actor,
      rationale,
    });
    await load();
    await refresh();
    setMessage(t("已记录本次证据的接受范围与理由。"));
    setRationale("");
  }

  return (
    <section className="engineering-document" aria-label={t("工程变更")}>
      <header className="engineering-header">
        <div>
          <h1>{t("工程变更")}</h1>
          <p>{data?.selected?.name ?? t("从设计修改到可审查的工程证据")}</p>
        </div>
        <div className="engineering-actions">
          <button
            disabled={!!busy}
            onClick={() =>
              void perform("refresh", async () => {
                setPlan(undefined);
                await load();
              })
            }
          >
            {t("刷新")}
          </button>
          {data?.available && (
            <button
              className="engineering-primary"
              disabled={!!busy || hasUnsaved || !!changed}
              title={hasUnsaved || changed ? t("请先保存或应用待修改内容。") : undefined}
              onClick={() => void perform("run", run)}
            >
              {busy === "run"
                ? t("正在计算…")
                : data.latest
                  ? t("重算并更新证据")
                  : t("计算当前基线")}
            </button>
          )}
        </div>
      </header>
      {error && (
        <p className="engineering-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="engineering-message" role="status">
          {message}
        </p>
      )}
      {data?.dependency_error && (
        <p className="engineering-error" role="alert">
          {data.dependency_error}
        </p>
      )}
      {data?.diagnostics?.map((item, index) => (
        <p className="engineering-error" key={index}>
          {item.message}
        </p>
      ))}
      {hasUnsaved && (
        <p className="engineering-message">
          {t("工作区有未保存文件。请先保存，再预览和应用工程变更。")}
        </p>
      )}
      {!data ? (
        <p>{t("读取中")}</p>
      ) : !data.available ? (
        <p className="engineering-empty">
          {t(
            "当前项目尚未提供工程变更适配器。数据中心混合冷却示例提供管路变更、影响分析、重算与证据审查。",
          )}
        </p>
      ) : (
        <div className="engineering-layout">
          <aside className="engineering-inputs">
            <h2>{t("设计输入")}</h2>
            <p className="engineering-muted">{t("预览影响后应用修改；计算使用已保存的设计。")}</p>
            {data.inputs.map((input) => (
              <label className="engineering-field" key={input.id}>
                <span>
                  {input.label} <small>{input.unit}</small>
                </span>
                <input
                  type="number"
                  step="any"
                  aria-label={input.label}
                  value={values[input.id] ?? ""}
                  min={input.min}
                  max={input.max}
                  disabled={!!busy}
                  onChange={(event) => {
                    setValues({ ...values, [input.id]: event.target.value });
                    setPlan(undefined);
                    setRequest(undefined);
                  }}
                />
                <small>
                  {t("已保存")}: {number(input.value)} {input.unit}
                </small>
                <button
                  type="button"
                  className="engineering-source"
                  title={input.pointer}
                  onClick={() => openFile(input.file)}
                >
                  {input.file}
                </button>
              </label>
            ))}
            <div className="engineering-actions">
              <button
                className="engineering-primary"
                disabled={!!busy || hasUnsaved || !changed}
                onClick={() => void perform("plan", preview)}
              >
                {t("预览变更影响")}
              </button>
              {changed && (
                <button
                  disabled={!!busy}
                  onClick={() => {
                    setValues(
                      Object.fromEntries(
                        data.inputs.map((input) => [input.id, String(input.value)]),
                      ),
                    );
                    setPlan(undefined);
                  }}
                >
                  {t("撤回未应用修改")}
                </button>
              )}
            </div>
            <h2>{t("计算历史")}</h2>
            {!data.runs.length && (
              <p className="engineering-muted">
                {t("尚无计算记录。先计算当前基线，再比较设计变化。")}
              </p>
            )}
            <ol className="engineering-history">
              {data.runs.map((item, index) => (
                <li key={item.run_id}>
                  <button
                    aria-current={selected?.run_id === item.run_id ? "true" : undefined}
                    onClick={() => {
                      setSelectedRun(item.run_id);
                      setVisibleChecks(10);
                      setVisibleExtraction(10);
                    }}
                  >
                    <span>
                      {t("计算")} {data.runs.length - index} · {label(item.result.assessment)}
                    </span>
                    <small>
                      {item.current ? t("当前输入") : t("历史输入")} · {acceptance(item)}
                    </small>
                  </button>
                </li>
              ))}
            </ol>
          </aside>
          <main className="engineering-evidence">
            {plan && (
              <section className="engineering-plan" aria-label={t("本次变更影响")}>
                <h2>{t("本次变更影响")}</h2>
                <ul>
                  {plan.changes.map((change) => (
                    <li key={change.input_id}>
                      {change.label}: {number(change.before)} → {number(change.after)} {change.unit}
                    </li>
                  ))}
                </ul>
                {plan.impacts.map((impact, index) => (
                  <Impact key={index} value={impact} />
                ))}
                <p className="engineering-muted">
                  {t(
                    "下列关系来自当前适配器；项目输入变化会保守地使历史证据失效，未覆盖的专业仍需复核。",
                  )}
                </p>
                <details>
                  <summary>{t("查看源文件差异")}</summary>
                  <pre>{plan.diff}</pre>
                </details>
                <button
                  className="engineering-primary"
                  disabled={!!busy || hasUnsaved}
                  onClick={() => void perform("apply", apply)}
                >
                  {t("应用已预览的变更")}
                </button>
              </section>
            )}
            {selected ? (
              <>
                <div className="engineering-status" aria-label={t("证据状态")}>
                  <div>
                    <small>{t("运行")}</small>
                    <span>{label(selected.execution)}</span>
                  </div>
                  <div data-status={selected.result.assessment}>
                    <small>{t("工程判定")}</small>
                    <span>{label(selected.result.assessment)}</span>
                  </div>
                  <div data-status={selected.current ? "current" : "stale"}>
                    <small>{t("适用性")}</small>
                    <span>{label(selected.freshness)}</span>
                  </div>
                  <div>
                    <small>{t("人工审查")}</small>
                    <span>{acceptance(selected)}</span>
                  </div>
                </div>
                {(selected.execution === "error" || !!selected.diagnostics?.length) && (
                  <section
                    className="engineering-error engineering-run-diagnostics"
                    role="alert"
                    aria-label={t("运行诊断")}
                  >
                    <h2>{t("运行诊断")}</h2>
                    {selected.execution === "error" && (
                      <p>{t("本次计算存在执行错误，不能作为接受依据。请修复后重算。")}</p>
                    )}
                    <ul>
                      {selected.diagnostics?.map((item, index) => (
                        <li key={index}>
                          {item.message}
                          {item.code && <small>{item.code}</small>}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                <h2>{t("要求与计算结果")}</h2>
                {previous && (
                  <p className="engineering-muted">
                    {t("变化量与前一次计算比较；历史值仅用于对照。")}
                  </p>
                )}
                <div className="engineering-actions">
                  <input
                    aria-label={t("筛选对象与工况")}
                    placeholder={t("筛选对象与工况")}
                    value={query}
                    onChange={(event) => {
                      setQuery(event.target.value);
                      setVisibleChecks(10);
                      setVisibleExtraction(10);
                    }}
                  />
                  <select
                    aria-label={t("判定筛选")}
                    value={checkMode}
                    onChange={(event) => {
                      setCheckMode(event.target.value);
                      setVisibleChecks(10);
                    }}
                  >
                    <option value="all">{t("全部判据")}</option>
                    <option value="open">{t("未满足或未判定")}</option>
                  </select>
                  <span className="engineering-muted">
                    {t("显示")} {Math.min(visibleChecks, checks.length)} / {checks.length}
                  </span>
                </div>
                <div className="engineering-table">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("要求 / 工况")}</th>
                        <th>{t("判定")}</th>
                        <th>{t("计算值")}</th>
                        <th>{t("要求值")}</th>
                        <th>{t("较前次变化")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {checks.slice(0, visibleChecks).map((check) => {
                        const before = previousChecks.get(check.id)?.actual;
                        const delta =
                          typeof before === "number" && typeof check.actual === "number"
                            ? check.actual - before
                            : undefined;
                        return (
                          <tr key={check.id} data-status={check.status}>
                            <td>
                              {check.label}
                              <small>
                                {check.scenario} · {check.object_ref}
                              </small>
                              {check.message && <small>{check.message}</small>}
                            </td>
                            <td>{label(check.status)}</td>
                            <td>
                              {number(check.actual)} {check.unit}
                            </td>
                            <td>
                              {check.operator} {number(check.limit)} {check.unit}
                            </td>
                            <td>
                              {number(delta)} {delta == null ? "" : check.unit}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {checks.length > visibleChecks && (
                  <button onClick={() => setVisibleChecks((count) => count + 50)}>
                    {t("显示更多判据")}
                  </button>
                )}
                <details className="engineering-extraction" key={selected.run_id}>
                  <summary>
                    {t("几何提取与物理反馈")} · {extraction.length} {t("项")}
                  </summary>
                  <p className="engineering-muted">
                    {t("显示")} {Math.min(visibleExtraction, extraction.length)} /{" "}
                    {extraction.length}
                  </p>
                  <div className="engineering-table">
                    <table>
                      <thead>
                        <tr>
                          <th>{t("提取量")}</th>
                          <th>{t("当前记录")}</th>
                          <th>{t("较前次变化")}</th>
                          <th>{t("来源")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {extraction.slice(0, visibleExtraction).map((item, index) => {
                          const before = previousExtraction.get(item.id);
                          const sameUnit = before?.unit === item.unit;
                          const delta =
                            sameUnit &&
                            typeof before?.value === "number" &&
                            typeof item.value === "number"
                              ? item.value - before.value
                              : undefined;
                          return (
                            <tr key={item.id ?? index}>
                              <td>
                                {item.label}
                                <small>
                                  {[item.scenario, item.object_ref].filter(Boolean).join(" · ")}
                                </small>
                              </td>
                              <td>
                                {number(item.value)} {item.unit}
                                <small>{item.state ? label(item.state) : ""}</small>
                              </td>
                              <td>
                                {before && !sameUnit ? t("单位已变化") : number(delta)}{" "}
                                {delta == null ? "" : item.unit}
                              </td>
                              <td>
                                <ExtractionSource item={item} openFile={openFile} />
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  {extraction.length > visibleExtraction && (
                    <button onClick={() => setVisibleExtraction((count) => count + 50)}>
                      {t("显示更多提取量")}
                    </button>
                  )}
                </details>
                <section className="engineering-review">
                  <h2>{t("适用范围与审查")}</h2>
                  <ul>
                    {selected.result.limitations?.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                  <p className="engineering-muted">
                    {t("接受仅关联这份证据声明的范围；后续输入变化需要复核。")}
                  </p>
                  <label>
                    {t("审查人")}
                    <input
                      value={actor}
                      onChange={(event) => setActor(event.target.value)}
                      disabled={!!busy}
                    />
                  </label>
                  <label>
                    {t("接受理由与范围")}
                    <textarea
                      rows={3}
                      value={rationale}
                      onChange={(event) => setRationale(event.target.value)}
                      disabled={!!busy}
                    />
                  </label>
                  <button
                    className="engineering-primary"
                    disabled={
                      !!busy ||
                      !canAccept ||
                      !actor.trim() ||
                      !rationale.trim() ||
                      hasUnsaved ||
                      !!changed
                    }
                    onClick={() => void perform("accept", accept)}
                  >
                    {t("记录本次概念验证的接受")}
                  </button>
                  {!canAccept && (
                    <p className="engineering-muted">
                      {t("只有适用于当前输入且满足所列判据的证据可以接受。")}
                    </p>
                  )}
                </section>
                <EvidenceDetails title={t("版本与完整证据")} value={selected} />
                {!!data.reviews.length && (
                  <EvidenceDetails title={t("接受记录")} value={data.reviews} />
                )}
              </>
            ) : (
              !plan && (
                <div className="engineering-empty">
                  <h2>{t("建立可比较的起点")}</h2>
                  <p>
                    {t(
                      "计算当前基线，保存供冷要求、几何提取和泵组配流结果。随后修改管路，在同一处查看影响、重算、修正与接受。",
                    )}
                  </p>
                </div>
              )
            )}
          </main>
        </div>
      )}
    </section>
  );
}

function ExtractionSource({
  item,
  openFile,
}: {
  item: Extraction;
  openFile: (file: string) => void;
}) {
  const source = item.source;
  const path =
    typeof source === "string"
      ? source
      : source && typeof source === "object" && "path" in source && typeof source.path === "string"
        ? source.path
        : undefined;
  return (
    <div className="engineering-extraction-source">
      {path ? (
        <button type="button" className="engineering-source" onClick={() => openFile(path)}>
          {path}
        </button>
      ) : (
        <span>{t("未提供源文件位置")}</span>
      )}
      {source != null && (
        <details>
          <summary>{t("来源摘要与提取依据")}</summary>
          <pre>{JSON.stringify({ source, details: item.details }, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function EvidenceDetails({ title, value }: { title: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{title}</summary>
      {open && <pre>{JSON.stringify(value, null, 2)}</pre>}
    </details>
  );
}

function Impact({ value }: { value: unknown }) {
  if (typeof value === "string") return <p>{value}</p>;
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  return (
    <div className="engineering-impact">
      <span>
        {String(item.label ?? item.reason ?? item.message ?? item.description ?? item.id ?? "")}
      </span>
      <details>
        <summary>{t("影响依据")}</summary>
        <pre>{JSON.stringify(item, null, 2)}</pre>
      </details>
    </div>
  );
}
