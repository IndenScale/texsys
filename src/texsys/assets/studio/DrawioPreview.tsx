import React, { useEffect, useMemo, useState } from "react";
import { DrawingPreview } from "./DrawingPreview";
import {
  decompressDrawioPages,
  drawioColor,
  drawioPage,
  drawioPageSources,
  type DrawioEdge,
  type DrawioPage,
  type DrawioVertex,
} from "./drawio-preview-model";
import { t } from "./i18n";
import type { DrawingBounds, DrawingViewport } from "./preview-viewport";
import { Empty } from "./ui";

type PreviewResult = { pages: DrawioPage[] } | { error: true };

export function DrawioPreview({
  content,
  label,
  viewport,
  onViewportChange,
}: {
  content: string;
  label: string;
  viewport?: DrawingViewport;
  onViewportChange: (viewport: DrawingViewport) => void;
}) {
  const preview = useMemo(() => {
    try {
      const sources = drawioPageSources(content, label);
      if (sources.some(({ graph }) => typeof graph === "string")) return { sources };
      return { pages: sources.map((source) => drawioPage(source, source.graph as Element)) };
    } catch {
      return { error: true } as const;
    }
  }, [content, label]);
  const [compressed, setCompressed] = useState<{
    preview: typeof preview;
    result: PreviewResult;
  }>();
  useEffect(() => {
    if (!preview.sources) return;
    const request = new AbortController();
    void decompressDrawioPages(preview.sources, request.signal).then(
      (pages) => {
        if (!request.signal.aborted) setCompressed({ preview, result: { pages } });
      },
      () => {
        if (!request.signal.aborted) setCompressed({ preview, result: { error: true } });
      },
    );
    return () => request.abort();
  }, [preview]);
  const result = preview.sources
    ? compressed?.preview === preview
      ? compressed.result
      : undefined
    : preview;
  if (!result) return <Empty title={t("正在读取 Draw.io 图纸…")} />;
  if ("error" in result || !result.pages)
    return <Empty title={t("无法预览此文件")}>{t("Draw.io 文档无效或无法读取")}</Empty>;
  const page = result.pages.find(({ id }) => id === viewport?.pageId) ?? result.pages[0];
  return (
    <DrawingPreview
      bounds={page.bounds}
      label={page.label}
      pageId={page.id}
      pages={result.pages}
      viewport={viewport}
      onViewportChange={onViewportChange}
      tools={
        page.unsupported > 0 && (
          <span className="project-preview-diagram-hint">
            {t("{0} 个 Draw.io 图元无法完整显示", { 0: page.unsupported })}
          </span>
        )
      }
    >
      <rect {...page.bounds} fill={page.background} />
      {page.cells.map((cell) => cell.kind === "edge" && <Edge key={cell.id} cell={cell} />)}
      {page.cells.map((cell) => cell.kind === "vertex" && <Vertex key={cell.id} cell={cell} />)}
    </DrawingPreview>
  );
}

function length(value: string | undefined, fallback: number) {
  const parsed = value?.trim() ? Number(value) : NaN;
  return Number.isFinite(parsed) ? Math.min(1000, Math.max(0, parsed)) : fallback;
}

function DrawioLabel({ bounds, label, style }: Pick<DrawioVertex, "bounds" | "label" | "style">) {
  if (!label) return null;
  const fontSize = length(style.get("fontSize"), 12);
  const fontStyle = length(style.get("fontStyle"), 0);
  const spacing = length(style.get("spacing"), 3);
  const lines = label.split("\n");
  const lineHeight = fontSize * 1.2;
  const alignment = style.get("align");
  const verticalAlignment = style.get("verticalAlign");
  const x =
    alignment === "left"
      ? bounds.x + spacing
      : alignment === "right"
        ? bounds.x + bounds.width - spacing
        : bounds.x + bounds.width / 2;
  const y =
    verticalAlignment === "top"
      ? bounds.y + spacing + fontSize
      : verticalAlignment === "bottom"
        ? bounds.y + bounds.height - spacing - (lines.length - 1) * lineHeight
        : bounds.y + (bounds.height - lines.length * lineHeight) / 2 + fontSize;
  return (
    <text
      x={x}
      y={y}
      fill={drawioColor(style.get("fontColor"), "#172b3a")}
      fontFamily={style.get("fontFamily") ?? "Arial, sans-serif"}
      fontSize={fontSize}
      fontStyle={fontStyle & 2 ? "italic" : undefined}
      textDecoration={fontStyle & 4 ? "underline" : undefined}
      textAnchor={alignment === "left" ? "start" : alignment === "right" ? "end" : "middle"}
      xmlSpace="preserve"
    >
      {lines.map((line, index) => (
        <tspan key={index} x={x} y={y + index * lineHeight}>
          {line}
        </tspan>
      ))}
    </text>
  );
}

function Vertex({ cell }: { cell: DrawioVertex }) {
  const { bounds, image, shape, style, unsupported } = cell;
  const { x, y, width, height } = bounds;
  const color = {
    fill: drawioColor(style.get("fillColor"), unsupported ? "#fff6d9" : "#fff"),
    stroke: drawioColor(style.get("strokeColor"), "#172b3a"),
    strokeWidth: length(style.get("strokeWidth"), 1),
    strokeDasharray: unsupported || style.get("dashed") === "1" ? "4 4" : undefined,
  };
  const radius = style.get("rounded") === "1" ? Math.min(width, height) * 0.15 : 0;
  let graphic;
  if (image)
    // An SVG loaded as an image cannot run scripts or load external resources.
    graphic = <image {...bounds} href={image} />;
  else if (shape === "ellipse")
    graphic = (
      <ellipse cx={x + width / 2} cy={y + height / 2} rx={width / 2} ry={height / 2} {...color} />
    );
  else if (shape === "rhombus")
    graphic = (
      <polygon
        points={`${x + width / 2},${y} ${x + width},${y + height / 2} ${x + width / 2},${y + height} ${x},${y + height / 2}`}
        {...color}
      />
    );
  else if (shape !== "text" && shape !== "group")
    graphic = <rect {...bounds} rx={radius} {...color} />;
  const label = cell.label || (unsupported ? shape : "");
  const rotation = Number(style.get("rotation")) || 0;
  return (
    <g
      data-drawio-cell={cell.id}
      transform={
        Number.isFinite(rotation) && rotation
          ? `rotate(${rotation} ${x + width / 2} ${y + height / 2})`
          : undefined
      }
      opacity={Math.min(1, length(style.get("opacity"), 100) / 100)}
    >
      {graphic}
      <DrawioLabel bounds={bounds} style={style} label={label} />
    </g>
  );
}

function arrowPoints(points: DrawioEdge["points"], size: number) {
  const tip = points.at(-1)!;
  let index = points.length - 2;
  while (index >= 0 && points[index].x === tip.x && points[index].y === tip.y) index--;
  if (index < 0) return;
  const prior = points[index];
  const distance = Math.hypot(tip.x - prior.x, tip.y - prior.y);
  const dx = (size * (tip.x - prior.x)) / distance;
  const dy = (size * (tip.y - prior.y)) / distance;
  const back = { x: tip.x - dx, y: tip.y - dy };
  return `${back.x + dy / 2},${back.y - dx / 2} ${tip.x},${tip.y} ${back.x - dy / 2},${back.y + dx / 2}`;
}

function Edge({ cell }: { cell: DrawioEdge }) {
  const { points, style } = cell;
  const stroke = drawioColor(style.get("strokeColor"), "#172b3a");
  const strokeWidth = length(style.get("strokeWidth"), 1);
  const drawArrow = (direction: "start" | "end") => {
    const kind = style.get(`${direction}Arrow`) ?? (direction === "start" ? "none" : "classic");
    if (kind === "none") return null;
    const arrow = arrowPoints(
      direction === "start" ? [...points].reverse() : points,
      length(style.get(`${direction}Size`), 6) + strokeWidth,
    );
    if (!arrow) return null;
    return kind.startsWith("open") ? (
      <polyline points={arrow} fill="none" stroke={stroke} strokeWidth={strokeWidth} />
    ) : (
      <polygon
        points={arrow}
        fill={style.get(`${direction}Fill`) === "0" ? "#fff" : stroke}
        stroke={stroke}
        strokeWidth={strokeWidth}
      />
    );
  };
  const index = Math.floor(points.length / 2);
  const first = points[index - 1];
  const next = points[index];
  const bounds: DrawingBounds = {
    x: (first.x + next.x) / 2,
    y: (first.y + next.y) / 2,
    width: 0,
    height: 0,
  };
  return (
    <g data-drawio-cell={cell.id}>
      <polyline
        points={points.map(({ x, y }) => `${x},${y}`).join(" ")}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeDasharray={style.get("dashed") === "1" ? "4 4" : undefined}
        strokeLinejoin="round"
      />
      {drawArrow("start")}
      {drawArrow("end")}
      <DrawioLabel bounds={bounds} style={style} label={cell.label} />
    </g>
  );
}
