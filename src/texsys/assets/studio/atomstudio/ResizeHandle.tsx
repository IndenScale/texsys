import { t } from "../i18n";
/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
import React, { type JSX } from "react";
import { flushSync } from "react-dom";
import { clampPanelSize, panelSizeFromPointer } from "./layoutSizing";
import { Separator } from "./primitives";

export const RESIZE_INTENT_THRESHOLD = 3;
const SNAP_DAMPING_MS = 48;

export function hasResizeIntent(start: number, current: number): boolean {
  return Math.abs(current - start) >= RESIZE_INTENT_THRESHOLD;
}

export function snapPanelDrag(value: number, min: number) {
  const collapsed = value < min;
  return { collapsed, value: collapsed ? 0 : value };
}

type ResizeHandleProps = {
  className: string;
  label: string;
  panelRef: React.RefObject<HTMLElement | null>;
  previewProperty: `--${string}`;
  value: number;
  min: number;
  max: number;
  defaultValue: number;
  mode: "collapse" | "hide";
  open: boolean;
  onClose: () => void;
  onChange: (value: number) => void;
};

export function WidthResizeHandle({
  grows,
  ...props
}: ResizeHandleProps & { grows: "left" | "right" }) {
  return <ResizeHandle {...props} axis="x" direction={grows === "right" ? 1 : -1} />;
}

export function HeightResizeHandle({
  grows,
  ...props
}: ResizeHandleProps & { grows: "up" | "down" }) {
  return <ResizeHandle {...props} axis="y" direction={grows === "down" ? 1 : -1} />;
}

function ResizeHandle(
  props: ResizeHandleProps & { axis: "x" | "y"; direction: 1 | -1 },
): JSX.Element | null {
  const horizontal = props.axis === "x";
  const orientation = horizontal ? "vertical" : "horizontal";
  const coordinate = (event: PointerEvent | React.PointerEvent<HTMLDivElement>) =>
    horizontal ? event.clientX : event.clientY;
  const size = clampPanelSize(props.value, props.min, props.max);
  const restingValue = props.open ? size : 0;
  const restingLabel = props.open ? undefined : t("资源面板已收起");
  const closeLabel = t(props.mode === "collapse" ? "松开以收起" : "松开以隐藏");

  const cleanup = React.useRef<(() => void) | null>(null);
  React.useLayoutEffect(() => () => cleanup.current?.(), [props.open, props.previewProperty]);
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    cleanup.current?.();
    const handle = event.currentTarget;
    const target = handle.parentElement;
    const panel = props.panelRef.current;
    if (!target || !panel) return;
    const rect = panel.getBoundingClientRect();
    const startValue = horizontal ? rect.width : rect.height;
    const startPointer = coordinate(event);
    let draftValue = startValue;
    let displayedValue = startValue;
    let snappedClosed = !props.open;
    let settling = false;
    let lastFrameTime = 0;
    let resizing = false;
    let finished = false;
    let frame: number | null = null;
    handle.setPointerCapture(event.pointerId);
    const animateSnap = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const applyDraft = (now: number) => {
      frame = null;
      if (settling) {
        // Exponential damping slows down near the target and can reverse mid-flight.
        const elapsed = Math.max(0, now - lastFrameTime);
        displayedValue +=
          (draftValue - displayedValue) * (1 - Math.exp(-elapsed / SNAP_DAMPING_MS));
        settling = Math.abs(draftValue - displayedValue) > 0.5;
      }
      if (!settling) displayedValue = draftValue;
      target.style.setProperty(props.previewProperty, `${displayedValue}px`);
      if (settling) {
        lastFrameTime = now;
        frame = requestAnimationFrame(applyDraft);
      }
    };
    const update = (pointer: number) => {
      const proposed = panelSizeFromPointer(
        startValue,
        startPointer,
        pointer,
        0,
        props.max,
        props.direction,
      );
      const snapped = snapPanelDrag(proposed, props.min);
      if (
        animateSnap &&
        !settling &&
        (snapped.collapsed !== snappedClosed || (snapped.collapsed && displayedValue > 0))
      ) {
        settling = true;
        lastFrameTime = performance.now();
      }
      snappedClosed = snapped.collapsed;
      draftValue = snapped.value;
      handle.dataset.snap = snappedClosed ? "closed" : "none";
      handle.setAttribute(
        "aria-valuetext",
        snappedClosed ? (restingLabel ?? closeLabel) : t("面板尺寸 {0} 像素", { "0": draftValue }),
      );
      handle.setAttribute("aria-valuenow", String(draftValue));
      if (frame === null) frame = requestAnimationFrame(applyDraft);
    };
    const move = (next: PointerEvent) => {
      if (next.pointerId !== event.pointerId) return;
      if (!resizing) {
        if (!hasResizeIntent(startPointer, coordinate(next))) return;
        resizing = true;
        document.documentElement.classList.add(
          "is-layout-resizing",
          `is-layout-resizing--${orientation}`,
        );
        target.style.setProperty(props.previewProperty, `${startValue}px`);
        handle.dataset.dragging = "true";
      }
      update(coordinate(next));
    };
    const release = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", finish, true);
      window.removeEventListener("pointercancel", finish, true);
      window.removeEventListener("blur", cancel);
      window.removeEventListener("keydown", escape, true);
      target.style.removeProperty(props.previewProperty);
      delete handle.dataset.dragging;
      delete handle.dataset.snap;
      handle.setAttribute("aria-valuenow", String(restingValue));
      if (restingLabel) handle.setAttribute("aria-valuetext", restingLabel);
      else handle.removeAttribute("aria-valuetext");
      document.documentElement.classList.remove(
        "is-layout-resizing",
        `is-layout-resizing--${orientation}`,
      );
      cleanup.current = null;
    };
    const finish = (next: PointerEvent) => {
      if (finished || next.pointerId !== event.pointerId) return;
      finished = true;
      const cancelled = next.type !== "pointerup";
      if (!cancelled && resizing) update(coordinate(next));
      release();
      if (cancelled || !resizing) return;
      // Publish the final layout before the browser can paint after removing the preview.
      flushSync(() => {
        if (!snappedClosed) props.onChange(draftValue);
        else if (props.open) props.onClose();
      });
    };
    const cancel = () => {
      if (finished) return;
      finished = true;
      release();
    };
    const escape = (key: KeyboardEvent) => {
      if (key.key === "Escape") {
        key.preventDefault();
        cancel();
      }
    };
    cleanup.current = cancel;
    // Observe the full gesture even if the moving separator loses pointer capture.
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", finish, true);
    window.addEventListener("pointercancel", finish, true);
    window.addEventListener("blur", cancel);
    window.addEventListener("keydown", escape, true);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const backward = horizontal ? "ArrowLeft" : "ArrowUp";
    const forward = horizontal ? "ArrowRight" : "ArrowDown";
    if (event.key !== backward && event.key !== forward) return;
    event.preventDefault();
    const delta = (event.key === forward ? 16 : -16) * props.direction;
    if (!props.open) {
      if (delta > 0) props.onChange(size);
    } else if (delta < 0 && size <= props.min) props.onClose();
    else props.onChange(clampPanelSize(size + delta, props.min, props.max));
  };

  if (!props.open && props.mode === "hide") return null;
  return (
    <Separator
      className={`layout-resizer layout-resizer--${orientation} ${props.className}`}
      aria-label={props.label}
      aria-orientation={orientation}
      aria-valuemin={0}
      aria-valuemax={props.max}
      aria-valuenow={restingValue}
      aria-valuetext={restingLabel}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => props.onChange(clampPanelSize(props.defaultValue, props.min, props.max))}
    />
  );
}
