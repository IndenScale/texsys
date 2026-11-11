import React, { useEffect, useRef, useState } from "react";
import { t } from "./i18n";
import type { WorkbenchProps } from "./sdk";
import { Empty, Icon } from "./ui";
import {
  artifactQuery,
  artifactKey,
  pickedFace,
  previewArtifacts,
  sourceReference,
  sameArtifact,
  type PreviewArtifact,
  type PreviewIndex,
  type PreviewSource,
} from "./project-preview-model";
import "./scene-preview.css";

type Point = [number, number, number];
type SceneObject = {
  id: string;
  prim_path: string;
  semantic_ref: string;
  name: string;
  points: Point[];
  faces: number[][];
  envelope: boolean;
};
type Scene = {
  status: "ready";
  artifact: PreviewArtifact;
  path: string;
  revision: string;
  objects: SceneObject[];
  source_map?: Record<string, PreviewSource>;
};
type Response = Scene | { status: "empty"; directory: string } | { status: "unchanged" };
const initialView = { yaw: -0.78, pitch: 0.62, zoom: 1 };
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
  view: typeof initialView,
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
  const low: Point = [Infinity, Infinity, Infinity],
    high: Point = [-Infinity, -Infinity, -Infinity];
  for (const { points } of scene.objects) {
    for (const point of points) {
      for (let axis = 0; axis < 3; axis++) {
        low[axis] = Math.min(low[axis], point[axis]);
        high[axis] = Math.max(high[axis], point[axis]);
      }
    }
  }
  const center = low.map((value, axis) => (value + high[axis]) / 2);
  const radius = Math.max(
    0.000001,
    Math.hypot(...high.map((value, axis) => value - low[axis])) / 2,
  );
  const scale = (Math.min(width, height) * 0.4 * view.zoom) / radius;
  const cos = Math.cos(view.yaw),
    sin = Math.sin(view.yaw);
  const vertical = Math.cos(view.pitch),
    tilt = Math.sin(view.pitch);
  const project = ([x, y, z]: Point): Point => {
    const dx = x - center[0],
      dy = y - center[1],
      dz = z - center[2];
    const depth = sin * dx + cos * dy;
    return [
      width / 2 + scale * (cos * dx - sin * dy),
      height / 2 - scale * (vertical * dz - tilt * depth),
      vertical * depth + tilt * dz,
    ];
  };
  const extent = radius * 1.14;
  const line = (a: Point, b: Point) => {
    const [ax, ay] = project(a),
      [bx, by] = project(b);
    context.moveTo(ax, ay);
    context.lineTo(bx, by);
  };
  context.strokeStyle = "#cbd8e0";
  context.beginPath();
  for (let i = -5; i <= 5; i++) {
    const [x, y] = center,
      step = (extent * i) / 5,
      z = low[2];
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
  artifact: suppliedArtifact,
  selected: suppliedSelected,
  onSelect,
  openSource,
}: {
  api: WorkbenchProps["api"];
  active: boolean;
  artifact?: PreviewArtifact;
  selected?: string;
  onSelect?: (semanticRef: string) => void;
  openSource?: (source: PreviewSource) => void;
}) {
  const [index, setIndex] = useState<PreviewIndex>();
  const [chosen, setChosen] = useState("");
  const [localSelected, setLocalSelected] = useState("");
  const selected = suppliedSelected ?? localSelected;
  const select = (semanticRef: string) => {
    setLocalSelected(semanticRef);
    onSelect?.(semanticRef);
  };
  const candidates = previewArtifacts(index, "layout");
  const artifact =
    suppliedArtifact ?? candidates.find((item) => artifactKey(item) === chosen) ?? candidates[0];
  const [result, setResult] = useState<Exclude<Response, { status: "unchanged" }>>();
  const [error, setError] = useState("");
  const [indexError, setIndexError] = useState("");
  const [view, setView] = useState(initialView);
  const canvas = useRef<HTMLCanvasElement>(null);
  const hits = useRef<NonNullable<ReturnType<typeof drawScene>>>([]);
  const drag = useRef<{ id: number; x: number; y: number; distance: number } | null>(null);
  const scene =
    result?.status === "ready" && sameArtifact(result.artifact, artifact) ? result : undefined;
  const query = artifact ? artifactQuery(artifact, true) : "";
  const digest = artifact?.digest;
  const available = artifact?.available;
  useEffect(() => {
    if (!active || suppliedArtifact) return;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api<PreviewIndex>("/api/preview");
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
  }, [api, active, suppliedArtifact]);
  useEffect(() => {
    if (!active) return;
    let closed = false;
    setResult(undefined);
    setError("");
    if (query && available)
      void api<Response>(`/api/scene?${query}`).then(
        (response) => {
          if (!closed && response.status !== "unchanged") setResult(response);
        },
        (failure) => {
          if (!closed) setError(String(failure));
        },
      );
    return () => {
      closed = true;
    };
  }, [api, active, query, digest, available]);
  useEffect(() => {
    const surface = canvas.current;
    if (!surface || !scene || !active) return;
    const paint = () => {
      hits.current = drawScene(surface, scene, view, selected) ?? [];
    };
    const resize = new ResizeObserver(paint);
    resize.observe(surface);
    paint();
    return () => resize.disconnect();
  }, [active, scene, view, selected]);
  const orbit = (x: number, y: number) =>
    setView((previous) => ({
      ...previous,
      yaw: previous.yaw + x * 0.012,
      pitch: clamp(previous.pitch + y * 0.01, -1.45, 1.45),
    }));
  const zoom = (factor: number) =>
    setView((previous) => ({ ...previous, zoom: clamp(previous.zoom * factor, 0.2, 12) }));
  const visible = !!scene?.objects.length;
  const selectedObject = scene?.objects.find(
    (object) => object.semantic_ref === selected || object.id === selected,
  );
  const source = selectedObject
    ? sourceReference(scene, selectedObject.semantic_ref, selectedObject.id)
    : undefined;
  return (
    <section className="scene-preview">
      <header className="scene-preview-header">
        <Icon name="cube" size={19} />
        <div className="scene-preview-source">
          <strong>{t("场景预览")}</strong>
          {!suppliedArtifact && candidates.length > 1 ? (
            <select
              aria-label={t("场景产物")}
              value={artifact ? artifactKey(artifact) : ""}
              onChange={(event) => setChosen(event.target.value)}
            >
              {candidates.map((item) => (
                <option key={artifactKey(item)} value={artifactKey(item)}>
                  {item.title ?? item.target}
                </option>
              ))}
            </select>
          ) : (
            <span title={scene?.path}>{scene?.path ?? t("读取构建清单中的场景")}</span>
          )}
        </div>
        <div className="scene-preview-controls">
          <button disabled={!visible} onClick={() => zoom(1 / 1.25)} aria-label={t("缩小")}>
            −
          </button>
          <button disabled={!visible} onClick={() => zoom(1.25)} aria-label={t("放大")}>
            +
          </button>
          <button disabled={!visible} onClick={() => setView(initialView)}>
            {t("重置视角")}
          </button>
        </div>
      </header>
      {!suppliedArtifact && artifact && (
        <div className="scene-preview-provenance">
          {t(artifact.current && !artifact.fallback ? "当前" : "已过期")} · {t("来源运行")}{" "}
          {artifact.run_id} · {t("目标判定")}{" "}
          {t(
            (
              {
                pass: "通过",
                fail: "未通过",
                error: "执行失败",
                indeterminate: "无法判定",
              } as Record<string, string>
            )[artifact.status ?? ""] ??
              artifact.status ??
              "未判定",
          )}
          {artifact.partial && ` · ${t("部分产物")}`}
        </div>
      )}
      {(error || indexError) && (
        <div role="alert" className="scene-preview-error">
          {t("无法读取构建场景")} · {error || indexError}
        </div>
      )}
      {visible ? (
        <div className="scene-preview-body">
          <canvas
            ref={canvas}
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
              };
            }}
            onPointerMove={(event) => {
              const previous = drag.current;
              if (previous?.id !== event.pointerId) return;
              const x = event.clientX - previous.x,
                y = event.clientY - previous.y;
              const distance = previous.distance + Math.hypot(x, y);
              if (distance > 3) orbit(x, y);
              drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, distance };
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
                  setView(initialView);
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
          title={t(
            scene
              ? "此场景没有可预览的几何"
              : result?.status === "empty"
                ? "此场景没有可预览的几何"
                : artifact && !error && artifact.available
                  ? "正在读取场景"
                  : "尚无可读场景产物",
          )}
        >
          {scene ? (
            t("预览支持 USD 网格、立方体和 BRep 包络。")
          ) : (
            <>
              <p>{t("在项目终端运行构建，完成后这里会自动刷新。")}</p>
              <code>texsys build</code>
            </>
          )}
        </Empty>
      )}
      {selectedObject && (
        <div className="scene-preview-inspector">
          <span title={selectedObject.prim_path}>
            {selectedObject.semantic_ref || selectedObject.name}
          </span>
          <span>{t(selectedObject.envelope ? "包络几何 · 只读" : "网格几何 · 只读")}</span>
          {source && openSource && (
            <button
              onClick={() =>
                openSource(
                  artifact?.current ? source : { ...source, line: undefined, column: undefined },
                )
              }
              title={source.file}
            >
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
          <span>{t("点击选择，拖动或方向键旋转")}</span>
        </footer>
      )}
    </section>
  );
}
