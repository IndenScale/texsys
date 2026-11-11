import React, { useEffect, useRef, useState } from "react";
import { t } from "./i18n";
import type { WorkbenchProps } from "./sdk";
import { Empty, Icon } from "./ui";
import "./scene-preview.css";

type Point = [number, number, number];
type SceneObject = { name: string; points: Point[]; faces: number[][]; envelope: boolean };
type Scene = { status: "ready"; path: string; revision: string; objects: SceneObject[] };
type Response = Scene | { status: "empty"; directory: string } | { status: "unchanged" };
const initialView = { yaw: -0.78, pitch: 0.62, zoom: 1 };
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
const palette = [
  [82, 124, 146],
  [66, 142, 132],
  [108, 129, 161],
  [151, 125, 94],
];

function drawScene(canvas: HTMLCanvasElement, scene: Scene, view: typeof initialView) {
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
    return object.faces.map((face) => {
      const vertices = face.map((vertex) => points[vertex]);
      const depth = vertices.reduce((sum, vertex) => sum + vertex[2], 0) / vertices.length;
      const [a, b, c] = face.map((vertex) => object.points[vertex]);
      const normalZ = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
      const height = Math.abs(c[2] - a[2]) + Math.abs(b[2] - a[2]);
      const tone = normalZ > 0 && height === 0 ? 1.26 : height === 0 ? 0.86 : 1;
      return {
        vertices,
        depth,
        fill: `rgb(${color.map((channel) => Math.round(channel * tone)).join(",")})`,
      };
    });
  });
  context.strokeStyle = "#344f5f";
  context.lineWidth = 0.85;
  for (const { vertices, fill } of faces.sort((a, b) => a.depth - b.depth)) {
    context.beginPath();
    for (const [index, [x, y]] of vertices.entries()) {
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    }
    context.closePath();
    context.fillStyle = fill;
    context.fill();
    context.stroke();
  }
}

export function ScenePreview({ api, active }: { api: WorkbenchProps["api"]; active: boolean }) {
  const [result, setResult] = useState<Exclude<Response, { status: "unchanged" }>>();
  const [error, setError] = useState("");
  const [view, setView] = useState(initialView);
  const canvas = useRef<HTMLCanvasElement>(null);
  const revision = useRef("");
  const drag = useRef<{ id: number; x: number; y: number } | null>(null);
  const scene = result?.status === "ready" ? result : undefined;

  useEffect(() => {
    if (!active) return;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const response = await api<Response>(
          `/api/scene?revision=${encodeURIComponent(revision.current)}`,
        );
        if (closed) return;
        if (response.status !== "unchanged") {
          revision.current = response.status === "ready" ? response.revision : "";
          setResult(response);
        }
        setError("");
      } catch (failure) {
        if (!closed) setError(failure instanceof Error ? failure.message : String(failure));
      } finally {
        if (!closed) timer = setTimeout(() => void poll(), 2000);
      }
    };
    void poll();
    return () => {
      closed = true;
      clearTimeout(timer);
    };
  }, [api, active]);

  useEffect(() => {
    const surface = canvas.current;
    if (!surface || !scene || !active) return;
    const paint = () => drawScene(surface, scene, view);
    const resize = new ResizeObserver(paint);
    resize.observe(surface);
    paint();
    return () => resize.disconnect();
  }, [active, scene, view]);

  const orbit = (x: number, y: number) =>
    setView((previous) => ({
      ...previous,
      yaw: previous.yaw + x * 0.012,
      pitch: clamp(previous.pitch + y * 0.01, -1.45, 1.45),
    }));
  const zoom = (factor: number) =>
    setView((previous) => ({ ...previous, zoom: clamp(previous.zoom * factor, 0.2, 12) }));
  const visible = !!scene?.objects.length;

  return (
    <section className="scene-preview">
      <header className="scene-preview-header">
        <Icon name="cube" size={19} />
        <div className="scene-preview-source">
          <strong>{t("场景预览")}</strong>
          <span title={scene?.path}>{scene?.path ?? t("自动跟踪最新 USD 场景")}</span>
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
      {error && (
        <div role="alert" className="scene-preview-error">
          {t("无法读取最新 USD 场景")} · {error}
        </div>
      )}
      {visible ? (
        <canvas
          ref={canvas}
          className="scene-preview-canvas"
          aria-label={t("三维场景")}
          tabIndex={0}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
          }}
          onPointerMove={(event) => {
            const previous = drag.current;
            if (previous?.id !== event.pointerId) return;
            orbit(event.clientX - previous.x, event.clientY - previous.y);
            drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
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
              default:
                return;
            }
            event.preventDefault();
          }}
        />
      ) : (
        <Empty
          title={t(scene ? "此场景没有可预览的几何" : result ? "尚无 USD 场景" : "正在读取场景")}
        >
          {scene
            ? t("预览支持 USD 网格、立方体和 BRep 包络。")
            : result?.status === "empty"
              ? t("在 {0} 生成 USD 场景后，这里会自动加载。", { "0": result.directory })
              : undefined}
        </Empty>
      )}
      {visible && scene && (
        <footer className="scene-preview-footer">
          <span>{t("{0} 个对象", { "0": scene.objects.length })}</span>
          {scene.objects.some((object) => object.envelope) && <span>{t("含 BRep 包络")}</span>}
          <span>{t("拖动或使用方向键旋转")}</span>
        </footer>
      )}
    </section>
  );
}
