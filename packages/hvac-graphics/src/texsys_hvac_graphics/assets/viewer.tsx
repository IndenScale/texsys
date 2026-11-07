import { t, useLocale } from "../../../../../src/texsys/assets/studio/i18n";
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { DiagramSvg, EngineeringSymbol, parameterTitles } from "./graphics";
import { engineeringLabels, resolveGraphic } from "./symbols";
import { RunPanel } from "./simulation";
import type { Diagram, Simulation } from "./types";

type Payload = { document: Diagram; results?: Simulation[] };
const payload = JSON.parse(document.getElementById("texsys-data")!.textContent!) as Payload;
const names: Record<string, string> = {
  Chiller: "冷水机组",
  ChilledWaterPump: "冷冻水泵",
  CoolingWaterPump: "冷却水泵",
  CoolingTower: "冷却塔",
  CoolingLoad: "负荷换热器",
  Pipe: "管段",
  Valve: "阀门",
  Tank: "水箱",
};

function App() {
  useLocale();
  const [selected, setSelected] = useState<string | undefined>(payload.document.nodes[0]?.id);
  const [tab, setTab] = useState("diagram");
  const [debug, setDebug] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [stretch, setStretch] = useState(1);
  const [detail, setDetail] = useState(payload.document.view);
  const runs = payload.results ?? [];
  const [runIndex, setRunIndex] = useState(0);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const run = runs[runIndex];
  const temperatures: Record<string, number> = {};
  const flows: Record<string, number> = {};
  if (run)
    for (const [key, variable] of Object.entries(run.variables)) {
      if (variable.role === "fluid-temperature" && !key.includes("#cooling"))
        temperatures[variable.semantic_ref] = variable.values[frame];
      if (variable.role === "connection-flow")
        flows[variable.semantic_ref] = variable.values[frame];
    }
  useEffect(() => {
    if (!playing || !run) return;
    const timer = window.setInterval(
      () =>
        setFrame((previous) => {
          if (previous >= run.time_s.length - 1) {
            setPlaying(false);
            return previous;
          }
          return previous + 1;
        }),
      120,
    );
    return () => window.clearInterval(timer);
  }, [playing, runIndex]);
  const node = payload.document.nodes.find((item) => item.id === selected);
  const geometry = node ? resolveGraphic(node, detail, stretch) : undefined;
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(undefined);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  return (
    <div className="workbench">
      <header className="app-header">
        <strong>TexSys</strong>
        <span>{t("暖通系统设计")}</span>
        <nav aria-label={t("工作视图")}>
          <button className={tab === "diagram" ? "active" : ""} onClick={() => setTab("diagram")}>
            {t("系统图纸")}
          </button>
          <button className={tab === "symbols" ? "active" : ""} onClick={() => setTab("symbols")}>
            {t("工程图元")}
          </button>
        </nav>
        <span className="draft-state">{t("概念设计 · 待审核")}</span>
      </header>
      <div className="workspace-heading">
        <div>
          <h1>{tab === "diagram" ? payload.document.title : t("参数化工程图元")}</h1>
          <p>
            {tab === "diagram"
              ? t("沿管路查看系统，选择设备检查参数与依据。")
              : t("轮廓、接管与标注锚点由同一几何函数计算。")}
          </p>
        </div>
        <div className="toolbar">
          <label>
            <input
              type="checkbox"
              checked={debug}
              onChange={(event) => setDebug(event.target.checked)}
            />
            {t("显示诊断标记")}
          </label>
          <button onClick={() => setZoom(Math.max(0.4, zoom - 0.2))} aria-label={t("缩小")}>
            −
          </button>
          <output>{Math.round(zoom * 100)}%</output>
          <button onClick={() => setZoom(Math.min(2, zoom + 0.2))} aria-label={t("放大")}>
            ＋
          </button>
          <button
            onClick={() => {
              setZoom(1);
              setSelected(undefined);
            }}
          >
            {t("适合图面")}
          </button>
        </div>
      </div>
      <main className="workspace">
        <section
          className="drawing-area"
          aria-label={tab === "diagram" ? t("系统图纸画布") : t("图元预览画布")}
        >
          {tab === "diagram" ? (
            <>
              <div
                className="drawing-sheet"
                style={{
                  width: `${100 * zoom}%`,
                  height: `calc((100vh - ${run ? 500 : 260}px) * ${zoom})`,
                  minHeight: run ? "290px" : undefined,
                  minWidth: "600px",
                }}
              >
                <DiagramSvg
                  document={payload.document}
                  selected={selected}
                  onSelect={setSelected}
                  debug={debug}
                  temperatures={run ? temperatures : undefined}
                  flows={run ? flows : undefined}
                />
              </div>
              {run && (
                <RunPanel
                  runs={runs}
                  runIndex={runIndex}
                  setRunIndex={setRunIndex}
                  index={frame}
                  setIndex={setFrame}
                  playing={playing}
                  setPlaying={setPlaying}
                  selected={node?.semantic_ref}
                />
              )}
            </>
          ) : (
            <div className="symbol-gallery">
              <div className="symbol-list" aria-label={t("图元类型")}>
                {payload.document.nodes.map((item) => (
                  <button
                    key={item.id}
                    className={item.id === selected ? "selected" : ""}
                    onClick={() => setSelected(item.id)}
                  >
                    {t(names[item.component]) ?? item.component}
                    <small>{item.label}</small>
                  </button>
                ))}
              </div>
              <div className="symbol-stage">
                {node && geometry && (
                  <svg
                    viewBox={`-20 -15 ${geometry.width + 40} ${geometry.height + 40}`}
                    role="img"
                    aria-label={t("参数化图元预览")}
                  >
                    <EngineeringSymbol node={{ ...node, graphic: geometry }} />
                    {geometry.ports.map((port) => (
                      <g key={port.id}>
                        <circle cx={port.x} cy={port.y} r="3.5" fill="#1769aa" />
                        <text
                          x={port.x}
                          y={port.y - 9}
                          textAnchor={port.side === "WEST" ? "start" : "end"}
                          fontSize="8"
                          fill="#1769aa"
                        >
                          {port.name}
                        </text>
                      </g>
                    ))}
                    <text
                      x={geometry.label_anchor.x}
                      y={geometry.label_anchor.y}
                      textAnchor="middle"
                      fontSize="12"
                      fill="#173247"
                    >
                      {node.label}
                    </text>
                    {detail === "schematic" && (
                      <text
                        x={geometry.annotation_anchor.x}
                        y={geometry.annotation_anchor.y}
                        textAnchor="middle"
                        fontSize="8"
                        fill="#617887"
                      >
                        {engineeringLabels(node, t).join("　")}
                      </text>
                    )}
                  </svg>
                )}
              </div>
            </div>
          )}
          <div className="drawing-footer">
            <span>
              {payload.document.spatial_state === "connectivity-only"
                ? t("连接视图 · 无物理比例")
                : t("工程视图")}
            </span>
            <span>{t("模型与图纸身份关联")}</span>
          </div>
        </section>
        <aside className="inspector" aria-label={t("设备详情")}>
          {node ? (
            <>
              <div className="inspector-title">
                <span>{t(names[node.component]) ?? node.component}</span>
                <h2>{node.label}</h2>
              </div>
              {tab === "symbols" && (
                <section>
                  <h3>{t("图形参数")}</h3>
                  <label className="field">
                    {t("图元横向比例")}
                    <output>{stretch.toFixed(2)}</output>
                    <input
                      aria-label={t("图元横向比例")}
                      type="range"
                      min="0.75"
                      max="2"
                      step="0.05"
                      value={stretch}
                      onChange={(event) => setStretch(Number(event.target.value))}
                    />
                  </label>
                  <label className="field">
                    {t("标注层次")}
                    <select value={detail} onChange={(event) => setDetail(event.target.value)}>
                      <option value="system">{t("系统图")}</option>
                      <option value="schematic">{t("原理图")}</option>
                    </select>
                  </label>
                  <p className="hint">{t("图形比例只改变呈现，不改变设备性能与物理尺寸。")}</p>
                </section>
              )}
              <section>
                <h3>{t("工程参数")}</h3>
                {Object.entries(node.parameters).length ? (
                  <dl>
                    {Object.entries(node.parameters).map(([key, binding]) => (
                      <div key={key}>
                        <dt>{t(parameterTitles[key]) ?? key}</dt>
                        <dd>
                          {binding.value === undefined
                            ? binding.state
                            : `${binding.value} ${binding.unit}`}
                          <small>{binding.state === "assumed" ? t("假设") : binding.state}</small>
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="hint">{t("此对象未绑定行为参数。")}</p>
                )}
              </section>
              <details>
                <summary>{t("来源与模型引用")}</summary>
                <p className="semantic-ref">{node.semantic_ref}</p>
                {Object.entries(node.parameters).map(([key, binding]) => (
                  <p key={key}>
                    <strong>{key}</strong>
                    <br />
                    {binding.source}
                  </p>
                ))}
                <p>
                  {t("图元解析：")}
                  {node.symbol.level}，
                  {node.symbol.presentation_exact ? t("精确图元") : t("概念族图元")}
                </p>
              </details>
              <section>
                <h3>{t("接管")}</h3>
                <ul className="port-list">
                  {(tab === "symbols" ? geometry?.ports : node.ports)?.map((port) => (
                    <li key={port.id}>
                      <span>{port.name}</span>
                      <small>{port.kind}</small>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          ) : (
            <div className="empty-selection">
              <h2>{t("选择设备")}</h2>
              <p>{t("在图上选择冷机、泵或其他设备，查看参数与依据。")}</p>
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
