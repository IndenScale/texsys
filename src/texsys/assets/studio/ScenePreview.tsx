import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { t } from "./i18n";
import type { WorkbenchProps } from "./sdk";
import { Empty, Icon } from "./ui";
import {
  pickedFace,
  sceneFileRequest,
  sourceReference,
  type PreviewSource,
} from "./project-preview-model";
import {
  fitSceneViewport,
  panSceneViewport,
  sceneBounds,
  sceneProjection,
  type Point3,
  type SceneViewport,
} from "./preview-viewport";
import { usePreviewSize } from "./use-preview-size";
import "./scene-preview.css";

type SceneObject = {
  id: string;
  prim_path: string;
  semantic_ref: string;
  name: string;
  points: Point3[];
  faces: number[][];
  envelope: boolean;
};
type Scene = {
  status: "ready";
  path: string;
  version: string;
  revision: string;
  objects: SceneObject[];
  source_map?: Record<string, PreviewSource>;
};
type Response = Scene | { status: "empty"; directory: string } | { status: "unchanged" };
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
const palette = [
  [82, 124, 146],
  [66, 142, 132],
  [108, 129, 161],
  [151, 125, 94],
];

function drawScene(
  canvas: HTMLCanvasElement,
  scene: Scene,
  view: SceneViewport,
  bounds: NonNullable<ReturnType<typeof sceneBounds>>,
  selected: string,
) {
  const width = canvas.clientWidth,
    height = canvas.clientHeight;
  if (!width || !height) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.scale(ratio, ratio);
  const project = sceneProjection(view, { width, height });
  const extent = bounds.radius * 1.14;
  const line = (a: Point3, b: Point3) => {
    const [ax, ay] = project(a),
      [bx, by] = project(b);
    context.moveTo(ax, ay);
    context.lineTo(bx, by);
  };
  context.strokeStyle = "#cbd8e0";
  context.beginPath();
  for (let i = -5; i <= 5; i++) {
    const [x, y] = bounds.center,
      step = (extent * i) / 5,
      z = bounds.floor;
    line([x + step, y - extent, z], [x + step, y + extent, z]);
    line([x - extent, y + step, z], [x + extent, y + step, z]);
  }
  context.stroke();

  const faces = scene.objects.flatMap((object, index) => {
    const points = object.points.map(project);
    const color = palette[index % palette.length];
    return object.faces
      .filter((face) => face.length >= 3 && face.every((vertex) => points[vertex]))
      .map((face) => {
        const vertices = face.map((vertex) => points[vertex]);
        const depth = vertices.reduce((sum, vertex) => sum + vertex[2], 0) / vertices.length;
        const [a, b, c] = face.map((vertex) => object.points[vertex]);
        const normalZ = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
        const height = Math.abs(c[2] - a[2]) + Math.abs(b[2] - a[2]);
        const tone = normalZ > 0 && height === 0 ? 1.26 : height === 0 ? 0.86 : 1;
        return {
          vertices,
          object,
          depth,
          fill: `rgb(${color.map((channel) => Math.round(channel * tone)).join(",")})`,
        };
      });
  });
  context.strokeStyle = "#344f5f";
  context.lineWidth = 0.85;
  faces.sort((a, b) => a.depth - b.depth);
  for (const { vertices, fill, object } of faces) {
    context.beginPath();
    for (const [index, [x, y]] of vertices.entries()) {
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    }
    context.closePath();
    context.fillStyle = object.semantic_ref === selected ? "#2d9d8f" : fill;
    context.strokeStyle = object.semantic_ref === selected ? "#005e55" : "#344f5f";
    context.lineWidth = object.semantic_ref === selected ? 2 : 0.85;
    context.fill();
    context.stroke();
  }
  return faces;
}

export function ScenePreview({
  api,
  active,
  path,
  version,
  content,
  dirty,
  openSource,
  viewport,
  onViewportChange,
}: {
  api: WorkbenchProps["api"];
  active: boolean;
  path: string;
  version: string;
  content: string;
  dirty: boolean;
  openSource?: (source: PreviewSource) => void;
  viewport?: SceneViewport;
  onViewportChange: (viewport: SceneViewport) => void;
}) {
  const [selected, select] = useState("");
  const [result, setResult] = useState<{
    key: string;
    value: Exclude<Response, { status: "unchanged" }>;
  }>();
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const { element: canvas, setElement: setCanvas, size } = usePreviewSize<HTMLCanvasElement>();
  const hits = useRef<NonNullable<ReturnType<typeof drawScene>>>([]);
  const drag = useRef<{ id: number; x: number; y: number; distance: number; pan: boolean } | null>(
    null,
  );
  const request = useMemo(
    () => sceneFileRequest(path, version, content, dirty),
    [path, version, content, dirty],
  );
  const current = result?.key === request.key ? result.value : undefined;
  const scene =
    current?.status === "ready" && current.path === path && current.version === version
      ? current
      : undefined;
  const error = failure?.key === request.key ? failure.message : "";
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let revision = "";
    const read = async () => {
      try {
        const url =
          revision && !dirty
            ? `${request.url}&revision=${encodeURIComponent(revision)}`
            : request.url;
        const response = await api<Response>(url, request.body, controller.signal);
        if (controller.signal.aborted) return;
        if (response.status === "ready" && (response.path !== path || response.version !== version))
          throw new Error(t("场景响应与当前文件不匹配"));
        setFailure(undefined);
        if (response.status !== "unchanged") {
          revision = response.status === "ready" ? response.revision : "";
          setResult({ key: request.key, value: response });
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        revision = "";
        setResult(undefined);
        setFailure({ key: request.key, message: String(error) });
      } finally {
        // A clean USD root can keep its bytes while its composition layers change.
        if (!controller.signal.aborted && !dirty) timer = setTimeout(() => void read(), 2000);
      }
    };
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, api, request, path, version, dirty]);
  const bounds = useMemo(() => scene && sceneBounds(scene.objects), [scene]);
  const fitted = useMemo(
    () => (bounds && size.width && size.height ? fitSceneViewport(bounds, size) : undefined),
    [bounds, size],
  );
  const view = viewport ?? fitted;
  useLayoutEffect(() => {
    if (!viewport && fitted) onViewportChange(fitted);
  }, [viewport, fitted, onViewportChange]);
  useLayoutEffect(() => {
    if (!active || !canvas || !scene || !view || !bounds) return;
    hits.current = drawScene(canvas, scene, view, bounds, selected) ?? [];
  }, [active, canvas, scene, view, bounds, selected, size]);
  const orbit = (x: number, y: number) => {
    if (view)
      onViewportChange({
        ...view,
        yaw: view.yaw + x * 0.012,
        pitch: clamp(view.pitch + y * 0.01, -1.45, 1.45),
      });
  };
  const zoom = (factor: number) => {
    if (view) onViewportChange({ ...view, scale: clamp(view.scale * factor, 1e-9, 1e9) });
  };
  const fit = () => {
    if (fitted) onViewportChange(fitted);
  };
  const visible = !!scene?.objects.length;
  const selectedObject = scene?.objects.find(
    (object) => object.semantic_ref === selected || object.id === selected,
  );
  const source = selectedObject
    ? sourceReference(scene, selectedObject.semantic_ref, selectedObject.id)
    : undefined;
  return (
    <section className="scene-preview">
      <div className="scene-preview-controls" role="toolbar" aria-label={t("场景视角")}>
        <button disabled={!visible} onClick={() => zoom(1 / 1.25)} aria-label={t("缩小")}>
          −
        </button>
        <button disabled={!visible} onClick={() => zoom(1.25)} aria-label={t("放大")}>
          +
        </button>
        <button disabled={!visible} onClick={fit}>
          {t("重置视角")}
        </button>
      </div>
      {error && (
        <div role="alert" className="scene-preview-error">
          {error}
        </div>
      )}
      {visible ? (
        <div className="scene-preview-body">
          <canvas
            ref={setCanvas}
            className="scene-preview-canvas"
            aria-label={t("三维场景；点击对象选择，拖动旋转")}
            tabIndex={0}
            onPointerDown={(event) => {
              if (event.button !== 0) return;
              event.currentTarget.setPointerCapture(event.pointerId);
              drag.current = {
                id: event.pointerId,
                x: event.clientX,
                y: event.clientY,
                distance: 0,
                pan: event.shiftKey,
              };
            }}
            onPointerMove={(event) => {
              const previous = drag.current;
              if (previous?.id !== event.pointerId) return;
              const x = event.clientX - previous.x,
                y = event.clientY - previous.y;
              const distance = previous.distance + Math.hypot(x, y);
              if (distance > 3 && view) {
                if (previous.pan) onViewportChange(panSceneViewport(view, x, y));
                else orbit(x, y);
              }
              drag.current = { ...previous, x: event.clientX, y: event.clientY, distance };
            }}
            onPointerUp={(event) => {
              if (drag.current?.id !== event.pointerId) return;
              if (drag.current.distance <= 3) {
                const bounds = event.currentTarget.getBoundingClientRect();
                const face = pickedFace(
                  hits.current,
                  event.clientX - bounds.left,
                  event.clientY - bounds.top,
                );
                select(face?.object.semantic_ref ?? "");
              }
              drag.current = null;
              event.currentTarget.releasePointerCapture(event.pointerId);
            }}
            onLostPointerCapture={() => {
              drag.current = null;
            }}
            onKeyDown={(event) => {
              switch (event.key) {
                case "ArrowLeft":
                  orbit(-8, 0);
                  break;
                case "ArrowRight":
                  orbit(8, 0);
                  break;
                case "ArrowUp":
                  orbit(0, -8);
                  break;
                case "ArrowDown":
                  orbit(0, 8);
                  break;
                case "+":
                case "=":
                  zoom(1.25);
                  break;
                case "-":
                  zoom(1 / 1.25);
                  break;
                case "Home":
                  fit();
                  break;
                case "Escape":
                  select("");
                  break;
                default:
                  return;
              }
              event.preventDefault();
            }}
          />
          <div className="scene-preview-objects" aria-label={t("场景对象")}>
            {scene?.objects.map((object) => (
              <button
                key={object.id}
                aria-pressed={selectedObject?.id === object.id}
                title={object.semantic_ref}
                onClick={() => select(object.semantic_ref)}
              >
                <Icon name="cube" size={14} />
                <span>{object.name}</span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <Empty
          title={t(error ? "无法预览此文件" : current ? "此场景没有可预览的几何" : "正在读取场景")}
        >
          {current && t("预览支持 USD 网格、立方体和 BRep 包络。")}
        </Empty>
      )}
      {selectedObject && (
        <div className="scene-preview-inspector">
          <span title={selectedObject.prim_path}>
            {selectedObject.semantic_ref || selectedObject.name}
          </span>
          <span>{t(selectedObject.envelope ? "包络几何" : "网格几何")}</span>
          {source && openSource && (
            <button onClick={() => openSource(source)} title={source.file}>
              <Icon name="files" size={14} />
              {t("定位源码")}
            </button>
          )}
        </div>
      )}
      {visible && scene && (
        <footer className="scene-preview-footer">
          <span>{t("{0} 个对象", { "0": scene.objects.length })}</span>
          {scene.objects.some((object) => object.envelope) && <span>{t("含包络几何")}</span>}
          <span>{t("拖动旋转，按住 Shift 拖动平移")}</span>
        </footer>
      )}
    </section>
  );
}
