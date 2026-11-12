import React, { useEffect, useEffectEvent, useLayoutEffect, useRef, type ReactNode } from "react";
import { Button, Select } from "./atomstudio/primitives";
import { t } from "./i18n";
import {
  drawingViewBox,
  fitDrawingViewport,
  type DrawingBounds,
  type DrawingViewport,
} from "./preview-viewport";
import { usePreviewSize } from "./use-preview-size";

export type DrawingPage = { id: string; label: string; bounds: DrawingBounds };

export function DrawingPreview({
  bounds,
  label,
  pageId,
  pages,
  viewport,
  onViewportChange,
  tools,
  children,
}: {
  bounds: DrawingBounds;
  label: string;
  pageId?: string;
  pages?: DrawingPage[];
  viewport?: DrawingViewport;
  onViewportChange: (viewport: DrawingViewport) => void;
  tools?: ReactNode;
  children: ReactNode;
}) {
  const { element, setElement, size } = usePreviewSize<SVGSVGElement>();
  const drag = useRef<{ id: number; x: number; y: number } | null>(null);
  const fitted = size.width && size.height ? fitDrawingViewport(bounds, size, pageId) : undefined;
  const matchesPage = viewport && viewport.pageId === pageId;
  const view = matchesPage ? viewport : fitted;
  useLayoutEffect(() => {
    if (!matchesPage && fitted) onViewportChange(fitted);
  }, [matchesPage, fitted, onViewportChange]);
  const box = view && size.width && size.height ? drawingViewBox(view, size) : bounds;
  const pan = (dx: number, dy: number) => {
    if (view)
      onViewportChange({ ...view, x: view.x - dx / view.scale, y: view.y - dy / view.scale });
  };
  const zoom = (factor: number) => {
    if (view)
      onViewportChange({ ...view, scale: Math.min(1e9, Math.max(1e-9, view.scale * factor)) });
  };
  const fit = () => {
    if (fitted) onViewportChange(fitted);
  };
  const wheel = useEffectEvent((event: WheelEvent) => {
    const units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? size.height : 1;
    if (event.ctrlKey || event.metaKey) zoom(Math.exp((-event.deltaY * units) / 300));
    else if (event.shiftKey && !event.deltaX) pan(-event.deltaY * units, 0);
    else pan(-event.deltaX * units, -event.deltaY * units);
    event.preventDefault();
  });
  useEffect(() => {
    if (!element) return;
    const scroll = (event: WheelEvent) => wheel(event);
    element.addEventListener("wheel", scroll, { passive: false });
    return () => element.removeEventListener("wheel", scroll);
  }, [element]);
  return (
    <div className="project-preview-diagram">
      <div className="project-preview-diagram-tools">
        {!!pages && pages.length > 1 && (
          <Select
            aria-label={t("图纸")}
            value={pageId}
            onChange={(event) => {
              const next = pages.find((page) => page.id === event.target.value);
              if (next && size.width && size.height)
                onViewportChange(fitDrawingViewport(next.bounds, size, next.id));
            }}
          >
            {pages.map((page) => (
              <option key={page.id} value={page.id}>
                {page.label}
              </option>
            ))}
          </Select>
        )}
        {tools}
        <Button
          appearance="secondary"
          type="button"
          aria-label={t("缩小")}
          onClick={() => zoom(0.8)}
        >
          −
        </Button>
        <Button
          appearance="secondary"
          type="button"
          aria-label={t("放大")}
          onClick={() => zoom(1.25)}
        >
          +
        </Button>
        <Button appearance="secondary" type="button" onClick={fit}>
          {t("适合窗口")}
        </Button>
      </div>
      <div className="project-preview-drawing">
        <svg
          ref={setElement}
          viewBox={`${box.x} ${box.y} ${box.width} ${box.height}`}
          aria-label={label}
          role="group"
          tabIndex={0}
          onPointerDown={(event) => {
            if (event.button !== 0 && event.button !== 1) return;
            if (
              event.button === 0 &&
              !event.shiftKey &&
              (event.target as Element).closest('[role="button"]')
            )
              return;
            event.preventDefault();
            event.currentTarget.focus();
            event.currentTarget.setPointerCapture(event.pointerId);
            drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
          }}
          onPointerMove={(event) => {
            const previous = drag.current;
            if (previous?.id !== event.pointerId) return;
            pan(event.clientX - previous.x, event.clientY - previous.y);
            drag.current = { ...previous, x: event.clientX, y: event.clientY };
          }}
          onLostPointerCapture={() => {
            drag.current = null;
          }}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget) return;
            switch (event.key) {
              case "ArrowLeft":
                pan(-32, 0);
                break;
              case "ArrowRight":
                pan(32, 0);
                break;
              case "ArrowUp":
                pan(0, -32);
                break;
              case "ArrowDown":
                pan(0, 32);
                break;
              case "+":
              case "=":
                zoom(1.25);
                break;
              case "-":
                zoom(0.8);
                break;
              case "Home":
                fit();
                break;
              default:
                return;
            }
            event.preventDefault();
          }}
        >
          {children}
        </svg>
      </div>
    </div>
  );
}

export function svgDrawingBounds(content: string): DrawingBounds | undefined {
  const root = new DOMParser().parseFromString(content, "image/svg+xml").documentElement;
  if (root.localName !== "svg" || root.namespaceURI !== "http://www.w3.org/2000/svg") return;
  const viewBox = root
    .getAttribute("viewBox")
    ?.trim()
    .split(/[\s,]+/)
    .map(Number);
  if (viewBox?.length === 4 && viewBox.every(Number.isFinite) && viewBox[2] > 0 && viewBox[3] > 0) {
    const [x, y, width, height] = viewBox;
    return { x, y, width, height };
  }
  const dimension = (key: string, fallback: number) => {
    const raw = root.getAttribute(key)?.trim().replace(/px$/, "");
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : fallback;
  };
  return { x: 0, y: 0, width: dimension("width", 300), height: dimension("height", 150) };
}
