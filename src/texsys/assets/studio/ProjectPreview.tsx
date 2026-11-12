import React, { useState } from "react";
import { Button, Select } from "./atomstudio/primitives";
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
import "./project-preview.css";

type Selection = { semanticRef: string; id?: string };

/** Render only the preview body. Every file keeps the same document shell and source editor. */
export function FilePreviewContent({
  api,
  path,
  kind,
  content,
  version,
  preview,
  dirty,
  openSource,
  openFile,
}: {
  api: WorkbenchProps["api"];
  path: string;
  kind: "markdown" | "diagram" | "scene";
  content: string;
  version: string;
  preview?: FilePreviewFormat;
  dirty: boolean;
  openSource: (source: PreviewSource) => void;
  openFile: (path: string) => void;
}) {
  if (kind === "markdown")
    return <MarkdownPreview content={content} path={path} onOpenFile={openFile} />;
  if (kind === "scene")
    return (
      <ScenePreview
        api={api}
        active
        path={path}
        version={version}
        content={content}
        dirty={dirty}
        openSource={openSource}
      />
    );
  let data: unknown;
  try {
    data = JSON.parse(content);
  } catch (error) {
    return <Empty title={t("无法预览此文件")}>{String(error)}</Empty>;
  }
  return <DiagramFilePreview data={data} preview={preview} openSource={openSource} />;
}

function DiagramFilePreview({
  data,
  preview,
  openSource,
}: {
  data: unknown;
  preview?: FilePreviewFormat;
  openSource: (source: PreviewSource) => void;
}) {
  const [selection, setSelection] = useState<Selection>({ semanticRef: "" });
  const source =
    sourceReference(data, selection.semanticRef, selection.id) ??
    sourceReference(preview, selection.semanticRef, selection.id);
  return (
    <div className="project-preview">
      <DiagramPreview data={data} selection={selection} select={setSelection} />
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
          <Select
            aria-label={t("图纸")}
            value={String(page.id)}
            onChange={(event) => setPageId(event.target.value)}
          >
            {pages.map((item) => (
              <option key={String(item.id)} value={String(item.id)}>
                {displayValue(item.title ?? item.id)}
              </option>
            ))}
          </Select>
        )}
        <span className="project-preview-diagram-hint">{t("选择图元可定位源码")}</span>
        <Button
          appearance="secondary"
          type="button"
          aria-label={t("缩小")}
          onClick={() => setZoom((value) => Math.max(0.5, value / 1.25))}
        >
          −
        </Button>
        <Button
          appearance="secondary"
          type="button"
          aria-label={t("放大")}
          onClick={() => setZoom((value) => Math.min(4, value * 1.25))}
        >
          +
        </Button>
        <Button appearance="secondary" type="button" onClick={() => setZoom(1)}>
          {t("适合窗口")}
        </Button>
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
        </svg>
      </div>
    </div>
  );
}
