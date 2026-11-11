import React, { useEffect, useId, useState } from "react";
import { ScenePreview } from "./ScenePreview";
import type { WorkbenchProps } from "./sdk";
import { Empty, Icon } from "./ui";
import { t } from "./i18n";
import {
  artifactKey,
  artifactQuery,
  displayValue,
  previewArtifacts,
  record,
  records,
  safePrimitive,
  sourceReference,
  sameArtifact,
  type PreviewArtifact,
  type PreviewIndex,
  type PreviewKind,
  type PreviewPayload,
  type PreviewSource,
} from "./project-preview-model";
import "./project-preview.css";

type Selection = { semanticRef: string; id?: string };
const assessment: Record<string, string> = {
  pass: "通过",
  fail: "未通过",
  blocked: "无法判定",
  unknown: "未判定",
  missing: "缺失输入",
  partial: "部分结果",
  error: "执行失败",
  success: "执行完成",
  satisfied: "满足",
  indeterminate: "无法判定",
  executed: "已运行",
  cached: "复用缓存",
};
const executionLabels: Record<string, string> = {
  executed: "已运行",
  cached: "复用缓存",
  blocked: "已阻断",
};

export function BuildSummary({ build }: { build: NonNullable<PreviewIndex["build"]> }) {
  const targets = records(build.targets);
  const diagnostics = records(build.diagnostics);
  return (
    <details className="project-preview-build-summary" open>
      <summary>
        <span>
          {t("本次构建")} · {t(assessment[build.status] ?? build.status)}
        </span>
        <span className="project-preview-readonly">
          {t(build.current ? "当前" : "已过期")} · {build.run_id}
        </span>
      </summary>
      <div className="project-preview-build-content">
        {targets.length > 0 && (
          <table className="project-preview-table">
            <thead>
              <tr>
                <th>{t("目标")}</th>
                <th>{t("目标判定")}</th>
                <th>{t("执行")}</th>
                <th>{t("诊断与阻断原因")}</th>
              </tr>
            </thead>
            <tbody>
              {targets.map((target, index) => {
                const targetDiagnostics = records(target.diagnostics);
                const blockedBy = Array.isArray(target.blocked_by) ? target.blocked_by : [];
                return (
                  <tr key={String(target.id ?? index)}>
                    <td>{displayValue(target.id)}</td>
                    <td>
                      {t(
                        assessment[String(target.status ?? "unknown")] ??
                          String(target.status ?? "未判定"),
                      )}
                    </td>
                    <td>
                      {t(
                        executionLabels[String(target.execution)] ?? displayValue(target.execution),
                      )}
                    </td>
                    <td>
                      {blockedBy.length > 0 && (
                        <div>
                          {t("阻断依赖")}：{blockedBy.map(displayValue).join(", ")}
                        </div>
                      )}
                      {targetDiagnostics.map((item, diagnosticIndex) => (
                        <div key={diagnosticIndex}>
                          {typeof item.rule === "string" && (
                            <span className="project-preview-readonly">{item.rule} · </span>
                          )}
                          {displayValue(item.message ?? item.details ?? item)}
                        </div>
                      ))}
                      {!blockedBy.length && !targetDiagnostics.length && "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {diagnostics.length > 0 && (
          <ul>
            {diagnostics.map((item, index) => (
              <li key={index}>{displayValue(item.message ?? item.rule ?? item)}</li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}

export function ArtifactStatus({ artifact }: { artifact: PreviewArtifact }) {
  return (
    <div className="project-preview-status">
      <span data-state={artifact.current && !artifact.fallback ? "current" : "stale"}>
        {t(artifact.fallback ? "历史产物 · 已过期" : artifact.current ? "当前" : "已过期")}
      </span>
      {(artifact.partial || artifact.complete === false || artifact.completeness === "partial") && (
        <span>{t("部分产物")}</span>
      )}
      <span>
        {t(artifact.fallback ? "历史目标判定" : "目标判定")} ·{" "}
        {t(assessment[artifact.status ?? "unknown"] ?? artifact.status ?? "未判定")}
      </span>
      {artifact.fallback && artifact.latest_target && (
        <span>
          {t("本次目标判定")} ·{" "}
          {t(assessment[artifact.latest_target.status] ?? artifact.latest_target.status)}
        </span>
      )}
      <span title={artifact.run_id}>
        {t("来源运行")} {artifact.run_id}
      </span>
      {artifact.execution && (
        <span>
          {t("执行")} · {t(executionLabels[artifact.execution] ?? artifact.execution)}
        </span>
      )}
    </div>
  );
}
export function ProjectPreview({
  api,
  root,
  kind,
  initialObject,
  openSource,
}: {
  api: WorkbenchProps["api"];
  root: string;
  kind: PreviewKind;
  initialObject?: string;
  openSource: (source: PreviewSource) => void;
}) {
  const [index, setIndex] = useState<PreviewIndex>();
  const [indexError, setIndexError] = useState("");
  const [chosen, setChosen] = useState("");
  const [selection, setSelection] = useState<Selection>({ semanticRef: initialObject ?? "" });
  const [payload, setPayload] = useState<PreviewPayload>();
  const [readError, setReadError] = useState("");
  const selectorId = useId();
  useEffect(() => {
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api<PreviewIndex>(`/api/preview?root=${encodeURIComponent(root)}`);
        if (!closed) {
          setIndex(value);
          setIndexError("");
        }
      } catch (failure) {
        if (!closed) setIndexError(String(failure));
      } finally {
        if (!closed) timer = setTimeout(() => void poll(), 2000);
      }
    };
    void poll();
    return () => {
      closed = true;
      clearTimeout(timer);
    };
  }, [api, root]);
  useEffect(() => setSelection({ semanticRef: initialObject ?? "" }), [initialObject, root]);
  const artifacts = previewArtifacts(index, kind, root);
  const artifact = artifacts.find((item) => artifactKey(item) === chosen) ?? artifacts[0];
  const query = artifact ? artifactQuery(artifact) : "";
  const digest = artifact?.digest;
  const available = artifact?.available;
  useEffect(() => {
    let closed = false;
    setPayload(undefined);
    setReadError("");
    if (query && available && kind !== "layout") {
      void api<PreviewPayload>(`/api/preview/artifact?${query}`).then(
        (value) => {
          if (!closed) setPayload(value);
        },
        (failure) => {
          if (!closed) setReadError(String(failure));
        },
      );
    }
    return () => {
      closed = true;
    };
  }, [api, query, digest, available, kind]);
  const displayedPayload = sameArtifact(payload?.artifact, artifact) ? payload : undefined;
  const source =
    sourceReference(displayedPayload?.data, selection.semanticRef, selection.id) ??
    sourceReference(artifact?.preview, selection.semanticRef, selection.id);
  const diagnostics = artifact?.latest_target?.diagnostics ?? artifact?.diagnostics ?? [];
  const open = (source: PreviewSource) =>
    openSource(artifact?.current ? source : { ...source, line: undefined, column: undefined });
  return (
    <section className="project-preview" aria-label={t("项目构建预览")}>
      {indexError && (
        <p className="project-preview-warning" role="alert">
          {t("无法更新构建状态")} · {indexError}
        </p>
      )}
      {kind === "results" && index?.build && <BuildSummary build={index.build} />}
      {!artifact ? (
        <Empty title={t(index ? "尚无关联构建产物" : "正在读取构建产物")}>
          {index && (
            <>
              <p>{t("在项目终端运行构建，完成后这里会自动刷新。")}</p>
              <code>texsys build</code>
              {index.build && (
                <p>
                  {t("最近构建")} {index.build.run_id} ·{" "}
                  {t(assessment[index.build.status] ?? index.build.status)}
                </p>
              )}
              {!!index.diagnostics.length && (
                <details>
                  <summary>{t("构建诊断")}</summary>
                  <pre>{JSON.stringify(index.diagnostics, null, 2)}</pre>
                </details>
              )}
            </>
          )}
        </Empty>
      ) : (
        <>
          <header className="project-preview-toolbar">
            {artifacts.length > 1 ? (
              <>
                <label htmlFor={selectorId}>{t("构建产物")}</label>
                <select
                  id={selectorId}
                  value={artifactKey(artifact)}
                  onChange={(event) => setChosen(event.target.value)}
                >
                  {artifacts.map((item) => (
                    <option key={artifactKey(item)} value={artifactKey(item)}>
                      {item.title ?? item.preview?.title ?? item.target} · {item.target}
                    </option>
                  ))}
                </select>
              </>
            ) : (
              <span title={artifact.path}>
                {artifact.title ?? artifact.preview?.title ?? artifact.target}
              </span>
            )}
            <span className="project-preview-readonly">{t("只读预览")}</span>
          </header>
          <ArtifactStatus artifact={artifact} />
          {!!diagnostics.length && (
            <details className="project-preview-diagnostics">
              <summary>
                {t("构建诊断")} ({diagnostics.length})
              </summary>
              <ul>
                {records(diagnostics).map((item, index) => (
                  <li key={index}>{displayValue(item.message ?? item.rule ?? item)}</li>
                ))}
              </ul>
            </details>
          )}
          {!artifact.current && (
            <div className="project-preview-warning" role="status">
              {t("输入已变化或本次目标未生成新产物。此结果仅供参考；运行 texsys build 更新。")}
            </div>
          )}
          {!artifact.available ? (
            <Empty title={t("产物无法读取")}>
              {artifact.error ?? t("构建未生成可读产物。请在终端运行 texsys build 查看诊断。")}
            </Empty>
          ) : readError ? (
            <Empty title={t("产物读取失败")}>{readError}</Empty>
          ) : kind === "layout" ? (
            <ScenePreview
              api={api}
              active
              artifact={artifact}
              selected={selection.semanticRef}
              onSelect={(semanticRef) => setSelection({ semanticRef })}
              openSource={open}
            />
          ) : !displayedPayload ? (
            <Empty title={t("正在读取产物")} />
          ) : (
            <>
              {displayedPayload.format === "json" && kind === "bom" ? (
                <BomPreview
                  data={displayedPayload.data}
                  selection={selection}
                  select={setSelection}
                />
              ) : displayedPayload.format === "json" &&
                (kind === "topology" || kind === "schematic") ? (
                <DiagramPreview
                  data={displayedPayload.data}
                  selection={selection}
                  select={setSelection}
                />
              ) : (
                <ResultPreview payload={displayedPayload} select={setSelection} />
              )}
              {selection.semanticRef && (
                <footer className="project-preview-selection">
                  <Icon name="cube" size={15} />
                  <span title={source?.path}>{selection.semanticRef}</span>
                  {source ? (
                    <button
                      onClick={() => open(source)}
                      title={`${source.file}${source.path ? ` · ${source.path}` : ""}`}
                    >
                      <Icon name="files" size={14} />
                      {t("定位源码")}
                    </button>
                  ) : (
                    <span className="project-preview-readonly">{t("此对象没有源码映射")}</span>
                  )}
                </footer>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function BomPreview({
  data,
  selection,
  select,
}: {
  data: unknown;
  selection: Selection;
  select: (value: Selection) => void;
}) {
  const entries = records(record(data).entries);
  if (!entries.length) return <Empty title={t("此物料清单没有条目")} />;
  return (
    <div className="project-preview-scroll">
      <table className="project-preview-table">
        <thead>
          <tr>
            <th>{t("设计实例")}</th>
            <th>{t("族")}</th>
            <th>{t("数量")}</th>
            <th>{t("型号与选型")}</th>
            <th>{t("规格与依据")}</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, index) => {
            const identity = String(entry.design_instance_id ?? entry.entry_id ?? index);
            const selectionData = record(entry.selection);
            return (
              <tr
                key={String(entry.entry_id ?? index)}
                aria-selected={selection.semanticRef === identity}
              >
                <td>
                  <button
                    onClick={() =>
                      select({ semanticRef: identity, id: String(entry.entry_id ?? identity) })
                    }
                  >
                    {identity}
                  </button>
                </td>
                <td>{displayValue(entry.family)}</td>
                <td>
                  {displayValue(entry.quantity)} {displayValue(entry.unit)}
                </td>
                <td>
                  {selectionData.model
                    ? displayValue(selectionData.model)
                    : selectionData.status === "unselected"
                      ? t("未选型")
                      : displayValue(selectionData.status)}
                </td>
                <td>
                  <details>
                    <summary>
                      {t("{0} 项规格", { "0": Object.keys(record(entry.specifications)).length })}
                    </summary>
                    {Object.entries(record(entry.specifications)).map(([name, value]) => {
                      const spec = record(value);
                      return (
                        <div key={name} title={displayValue(spec.basis)}>
                          {name}: {displayValue(spec.value)} {String(spec.unit ?? "")}{" "}
                          <span className="project-preview-readonly">
                            {String(spec.status ?? "")}
                          </span>
                        </div>
                      );
                    })}
                  </details>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type DiagramPoint = { x: number; y: number };
function point(value: unknown, fallback: DiagramPoint): DiagramPoint {
  const item = record(value);
  return {
    x: Number.isFinite(item.x) ? Number(item.x) : fallback.x,
    y: Number.isFinite(item.y) ? Number(item.y) : fallback.y,
  };
}
function DiagramPreview({
  data,
  selection,
  select,
}: {
  data: unknown;
  selection: Selection;
  select: (value: Selection) => void;
}) {
  const diagram = record(data);
  const pages = records(diagram.pages);
  const [pageId, setPageId] = useState("");
  const [zoom, setZoom] = useState(1);
  const page = pages.find((item) => item.id === pageId) ?? pages[0];
  const width = Number(page?.width) || 1000,
    height = Number(page?.height) || 600;
  const nodes = records(diagram.nodes).filter(
    (item) => !page || !item.page || item.page === page.id,
  );
  const edges = records(diagram.edges).filter(
    (item) => !page || !item.page || item.page === page.id,
  );
  if (!nodes.length && !edges.length) return <Empty title={t("此产物没有可预览的图元")} />;
  return (
    <div className="project-preview-diagram">
      <div className="project-preview-diagram-tools">
        {pages.length > 1 && (
          <select
            aria-label={t("图纸")}
            value={String(page.id)}
            onChange={(event) => setPageId(event.target.value)}
          >
            {pages.map((item) => (
              <option key={String(item.id)} value={String(item.id)}>
                {displayValue(item.title ?? item.id)}
              </option>
            ))}
          </select>
        )}
        <span>{t("选择图元可定位源码")}</span>
        <button
          aria-label={t("缩小")}
          onClick={() => setZoom((value) => Math.max(0.5, value / 1.25))}
        >
          −
        </button>
        <button
          aria-label={t("放大")}
          onClick={() => setZoom((value) => Math.min(4, value * 1.25))}
        >
          +
        </button>
        <button onClick={() => setZoom(1)}>{t("适合窗口")}</button>
      </div>
      <div className="project-preview-drawing">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: `${zoom * 100}%`, minHeight: 280 }}
          aria-label={displayValue(diagram.title)}
          role="group"
        >
          {edges.map((edge, index) => {
            const id = String(edge.id ?? index),
              semanticRef = String(edge.semantic_ref ?? id);
            return (
              <g
                key={id}
                role="button"
                tabIndex={0}
                aria-label={semanticRef}
                aria-pressed={selection.semanticRef === semanticRef}
                onClick={() => select({ semanticRef, id })}
                onKeyDown={(event) => {
                  if (["Enter", " "].includes(event.key)) {
                    event.preventDefault();
                    select({ semanticRef, id });
                  }
                }}
              >
                <polyline
                  points={records(edge.points)
                    .map((p) => `${Number(p.x)},${Number(p.y)}`)
                    .join(" ")}
                  className="project-preview-edge-hit"
                />
                <polyline
                  points={records(edge.points)
                    .map((p) => `${Number(p.x)},${Number(p.y)}`)
                    .join(" ")}
                  className="project-preview-edge"
                  data-selected={selection.semanticRef === semanticRef}
                />
                <title>
                  {semanticRef} · {displayValue(edge.medium)}
                </title>
              </g>
            );
          })}
          {nodes.map((node, index) => {
            const id = String(node.id ?? index),
              semanticRef = String(node.semantic_ref ?? id);
            const graphic = record(node.graphic),
              actual = record(record(node.symbol).actual);
            const primitives = records(graphic.primitives ?? actual.primitives)
              .map(safePrimitive)
              .filter((item) => item !== undefined);
            const nodeWidth = Number(node.width) || 140,
              nodeHeight = Number(node.height) || 60;
            const anchor = point(graphic.label_anchor ?? actual.label_anchor, {
              x: nodeWidth / 2,
              y: nodeHeight / 2,
            });
            const annotation = point(graphic.annotation_anchor, {
              x: nodeWidth / 2,
              y: nodeHeight + 16,
            });
            return (
              <g
                key={id}
                transform={`translate(${Number(node.x) || 0},${Number(node.y) || 0})`}
                className="project-preview-node"
                data-selected={selection.semanticRef === semanticRef}
                role="button"
                tabIndex={0}
                aria-label={semanticRef}
                aria-pressed={selection.semanticRef === semanticRef}
                onClick={() => select({ semanticRef, id })}
                onKeyDown={(event) => {
                  if (["Enter", " "].includes(event.key)) {
                    event.preventDefault();
                    select({ semanticRef, id });
                  }
                }}
              >
                <rect
                  className="project-preview-node-hit"
                  x={-5}
                  y={-5}
                  width={nodeWidth + 10}
                  height={nodeHeight + 10}
                  rx={3}
                />
                <g className="project-preview-symbol">
                  {primitives.length ? (
                    primitives.map((primitive, i) =>
                      React.createElement(primitive.tag, { ...primitive.attrs, key: i }),
                    )
                  ) : (
                    <rect width={nodeWidth} height={nodeHeight} rx={4} />
                  )}
                </g>
                <text x={anchor.x} y={anchor.y} textAnchor="middle">
                  {displayValue(node.label ?? semanticRef)}
                </text>
                {Array.isArray(node.engineering_labels) &&
                  node.engineering_labels.map((label, i) => (
                    <text
                      className="project-preview-annotation"
                      key={i}
                      x={annotation.x}
                      y={annotation.y + i * 14}
                      textAnchor="middle"
                    >
                      {displayValue(label)}
                    </text>
                  ))}
                <title>
                  {semanticRef} · {displayValue(node.component)}
                </title>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function ResultPreview({
  payload,
  select,
}: {
  payload: PreviewPayload;
  select: (value: Selection) => void;
}) {
  const data = record(payload.data);
  const rows = records(data.assertions ?? data.checks ?? data.findings ?? data.diagnostics);
  // SVG text is shown as source, never injected into the workbench's DOM.
  return (
    <div className="project-preview-scroll project-preview-results">
      {rows.length > 0 && (
        <table className="project-preview-table">
          <thead>
            <tr>
              <th>{t("检查")}</th>
              <th>{t("判定")}</th>
              <th>{t("结果")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                <td>
                  {row.semantic_ref ? (
                    <button onClick={() => select({ semanticRef: String(row.semantic_ref) })}>
                      {displayValue(row.semantic_ref)}
                    </button>
                  ) : (
                    displayValue(row.name ?? row.id ?? row.variable ?? row.code ?? index + 1)
                  )}
                </td>
                <td>
                  {t(assessment[String(row.status ?? "unknown")] ?? String(row.status ?? "未判定"))}
                </td>
                <td>{displayValue(row.message ?? row.actual ?? row.value ?? row.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <details open={!rows.length}>
        <summary>{t(payload.format === "json" ? "结果数据" : "产物内容")}</summary>
        <pre>
          {typeof payload.data === "string" ? payload.data : JSON.stringify(payload.data, null, 2)}
        </pre>
      </details>
    </div>
  );
}
