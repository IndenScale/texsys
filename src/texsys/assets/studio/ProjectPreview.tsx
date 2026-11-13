import React, { useMemo, useState } from "react";
import { DrawingPreview, svgDrawingBounds } from "./DrawingPreview";
import { DrawioPreview } from "./DrawioPreview";
import type { FilePreviewKind } from "./workspace-model";
import { ScenePreview } from "./ScenePreview";
import { MarkdownPreview } from "./MarkdownPreview";
import type { FilePreviewFormat, WorkbenchProps } from "./sdk";
import { Empty, Icon } from "./ui";
import { t } from "./i18n";
import {
  displayValue,
  record,
  records,
  safePrimitive,
  sourceReference,
  type PreviewSource,
} from "./project-preview-model";
import type { DrawingViewport, PreviewViewport } from "./preview-viewport";
import "./project-preview.css";

type Selection = { semanticRef: string; id?: string };

/** Render only the preview body. Every file keeps the same document shell and source editor. */
export function FilePreviewContent({
  api,
  active = true,
  path,
  kind,
  content,
  version,
  preview,
  dirty,
  openSource,
  openFile,
  viewport,
  onViewportChange,
}: {
  api: WorkbenchProps["api"];
  active?: boolean;
  path: string;
  kind: FilePreviewKind;
  content: string;
  version: string;
  preview?: FilePreviewFormat;
  dirty: boolean;
  openSource: (source: PreviewSource) => void;
  openFile: (path: string) => void;
  viewport?: PreviewViewport;
  onViewportChange: (viewport: PreviewViewport) => void;
}) {
  if (kind === "markdown")
    return <MarkdownPreview content={content} path={path} onOpenFile={openFile} />;
  if (kind === "scene")
    return (
      <ScenePreview
        api={api}
        active={active}
        path={path}
        version={version}
        content={content}
        dirty={dirty}
        openSource={openSource}
        viewport={viewport?.kind === "scene" ? viewport : undefined}
        onViewportChange={onViewportChange}
      />
    );
  const drawingViewport = viewport?.kind === "drawing" ? viewport : undefined;
  if (kind === "drawio")
    return (
      <DrawioPreview
        content={content}
        label={path}
        viewport={drawingViewport}
        onViewportChange={onViewportChange}
      />
    );
  if (kind === "svg")
    return (
      <SvgFilePreview
        content={content}
        label={path}
        viewport={drawingViewport}
        onViewportChange={onViewportChange}
      />
    );
  let data: unknown;
  try {
    data = JSON.parse(content);
  } catch (error) {
    return <Empty title={t("无法预览此文件")}>{String(error)}</Empty>;
  }
  return (
    <DiagramFilePreview
      data={data}
      preview={preview}
      openSource={openSource}
      viewport={drawingViewport}
      onViewportChange={onViewportChange}
    />
  );
}

function SvgFilePreview({
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
  const drawing = useMemo(() => {
    const bounds = svgDrawingBounds(content);
    if (!bounds) return;
    return { bounds, href: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(content)}` };
  }, [content]);
  if (!drawing) return <Empty title={t("无法预览此文件")}>{t("SVG 文档无效")}</Empty>;
  return (
    <DrawingPreview
      bounds={drawing.bounds}
      label={label}
      viewport={viewport}
      onViewportChange={onViewportChange}
    >
      {/* Loading as an SVG image keeps the file's markup outside the Studio document. */}
      <image href={drawing.href} {...drawing.bounds} pointerEvents="none" />
    </DrawingPreview>
  );
}

function DiagramFilePreview({
  data,
  preview,
  openSource,
  viewport,
  onViewportChange,
}: {
  data: unknown;
  preview?: FilePreviewFormat;
  openSource: (source: PreviewSource) => void;
  viewport?: DrawingViewport;
  onViewportChange: (viewport: DrawingViewport) => void;
}) {
  const [selection, setSelection] = useState<Selection>({ semanticRef: "" });
  const source =
    sourceReference(data, selection.semanticRef, selection.id) ??
    sourceReference(preview, selection.semanticRef, selection.id);
  return (
    <div className="project-preview">
      <DiagramPreview
        data={data}
        selection={selection}
        select={setSelection}
        viewport={viewport}
        onViewportChange={onViewportChange}
      />
      {selection.semanticRef && (
        <footer className="project-preview-selection">
          <Icon name="cube" size={15} />
          <span title={source?.path}>{selection.semanticRef}</span>
          {source ? (
            <button onClick={() => openSource(source)} title={source.file}>
              <Icon name="files" size={14} />
              {t("定位源码")}
            </button>
          ) : (
            <span>{t("此对象没有源码映射")}</span>
          )}
        </footer>
      )}
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
  viewport,
  onViewportChange,
}: {
  data: unknown;
  selection: Selection;
  select: (value: Selection) => void;
  viewport?: DrawingViewport;
  onViewportChange: (viewport: DrawingViewport) => void;
}) {
  const diagram = record(data);
  const pages = records(diagram.pages).map((page) => ({
    id: String(page.id),
    label: displayValue(page.title ?? page.id),
    bounds: { x: 0, y: 0, width: Number(page.width) || 1000, height: Number(page.height) || 600 },
  }));
  const page = pages.find((item) => item.id === viewport?.pageId) ?? pages[0];
  const nodes = records(diagram.nodes).filter(
    (item) => !page || !item.page || item.page === page.id,
  );
  const edges = records(diagram.edges).filter(
    (item) => !page || !item.page || item.page === page.id,
  );
  return (
    <DrawingPreview
      bounds={page?.bounds ?? { x: 0, y: 0, width: 1000, height: 600 }}
      label={displayValue(diagram.title)}
      pageId={page?.id}
      pages={pages}
      viewport={viewport}
      onViewportChange={onViewportChange}
      tools={<span className="project-preview-diagram-hint">{t("选择图元可定位源码")}</span>}
    >
      {!nodes.length && !edges.length && (
        <text x={20} y={30} fill="currentColor">
          {t("此产物没有可预览的图元")}
        </text>
      )}
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
            <title>{`${semanticRef} · ${displayValue(edge.medium)}`}</title>
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
            <title>{`${semanticRef} · ${displayValue(node.component)}`}</title>
          </g>
        );
      })}
    </DrawingPreview>
  );
}
