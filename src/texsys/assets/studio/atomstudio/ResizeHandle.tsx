/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
import React, { type JSX } from "react";
import { clampPanelSize, panelSizeFromPointer } from "./layoutSizing";
import { Separator } from "./primitives";

type ResizeOrientation = "vertical" | "horizontal";
type PanelStyleProperty =
  "--sidebar-width" | "--right-panel-width" | "--bottom-panel-height" | "--problems-list-width";

export const RESIZE_INTENT_THRESHOLD = 3;

export function hasResizeIntent(start: number, current: number): boolean {
  return Math.abs(current - start) >= RESIZE_INTENT_THRESHOLD;
}

/** A magnetic edge with hysteresis: reopening requires a deliberate pull past the close band. */
export function snapPanelDrag(value: number, collapsed: boolean, min: number) {
  const closeAt = Math.min(48, Math.round(min / 4));
  const openAt = closeAt + 24;
  const nextCollapsed = collapsed ? value < openAt : value <= closeAt;
  return { collapsed: nextCollapsed, value: nextCollapsed ? 0 : value };
}

function panelPreviewName(styleProperty: PanelStyleProperty): string {
  return styleProperty.slice(2).replace(/-(width|height)$/, "");
}

export function ResizeHandle(props: {
  class?: string;
  orientation: ResizeOrientation;
  label: string;
  styleProperty: PanelStyleProperty;
  value: number;
  min: number;
  max: number;
  defaultValue: number;
  growthDirection?: "backward" | "forward";
  styleTarget?: "app-shell" | "parent";
  panelOpen?: boolean;
  onOpen?: () => void;
  onDismiss?: () => void;
  onChange: (value: number) => void;
  onCommit?: (value: number, open: boolean) => void;
}): JSX.Element {
  const coordinate = (event: PointerEvent | React.PointerEvent<HTMLDivElement>) =>
    props.orientation === "vertical" ? event.clientX : event.clientY;

  const cleanup = React.useRef<(() => void) | null>(null);
  React.useEffect(() => () => cleanup.current?.(), []);
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    cleanup.current?.();
    const handle = event.currentTarget;
    const target =
      props.styleTarget === "parent"
        ? handle.parentElement
        : handle.closest<HTMLElement>(".app-shell");
    if (!target) return;
    const panel =
      props.growthDirection === "forward"
        ? handle.previousElementSibling
        : handle.nextElementSibling;
    const rect = panel?.getBoundingClientRect();
    const startValue = rect
      ? props.orientation === "vertical"
        ? rect.width
        : rect.height
      : props.panelOpen === false
        ? 0
        : props.value;
    const remembered = props.value;
    const startPointer = coordinate(event);
    const previewName = panelPreviewName(props.styleProperty);
    let draftValue = startValue;
    let snappedClosed = props.panelOpen === false;
    let resizing = false;
    let finished = false;
    let frame: number | null = null;
    handle.setPointerCapture(event.pointerId);
    const applyDraft = () => {
      frame = null;
      target.style.setProperty(props.styleProperty, `${draftValue}px`);
      handle.setAttribute("aria-valuenow", String(Math.round(draftValue)));
    };
    const update = (pointer: number) => {
      draftValue =
        props.growthDirection === "forward"
          ? clampPanelSize(
              startValue + pointer - startPointer,
              props.onDismiss ? 0 : props.min,
              props.max,
            )
          : panelSizeFromPointer(
              startValue,
              startPointer,
              pointer,
              props.onDismiss ? 0 : props.min,
              props.max,
            );
      if (props.onDismiss) {
        const snapped = snapPanelDrag(draftValue, snappedClosed, props.min);
        snappedClosed = snapped.collapsed;
        draftValue = snapped.value;
        handle.dataset.snap = snappedClosed
          ? "closed"
          : draftValue < props.min
            ? "minimum"
            : "none";
        handle.setAttribute(
          "aria-valuetext",
          snappedClosed ? "松开以收起" : `面板尺寸 ${Math.max(props.min, draftValue)} 像素`,
        );
      }
      if (frame === null) frame = requestAnimationFrame(applyDraft);
    };
    const move = (next: PointerEvent) => {
      if (next.pointerId !== event.pointerId) return;
      if (!resizing) {
        if (!hasResizeIntent(startPointer, coordinate(next))) return;
        resizing = true;
        document.documentElement.classList.add(
          "is-layout-resizing",
          `is-layout-resizing--${props.orientation}`,
        );
        target.dataset.resizingPanel = previewName;
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
      delete target.dataset.resizingPanel;
      delete handle.dataset.dragging;
      delete handle.dataset.snap;
      handle.removeAttribute("aria-valuetext");
      document.documentElement.classList.remove(
        "is-layout-resizing",
        `is-layout-resizing--${props.orientation}`,
      );
      cleanup.current = null;
    };
    const finish = (next: PointerEvent) => {
      if (finished || next.pointerId !== event.pointerId) return;
      finished = true;
      const cancelled = next.type !== "pointerup";
      if (!cancelled && resizing) {
        update(coordinate(next));
        if (frame !== null) cancelAnimationFrame(frame);
        applyDraft();
      }
      const dismiss = resizing && !cancelled && !!props.onDismiss && snappedClosed;
      const committed = clampPanelSize(draftValue, props.min, props.max);
      release();
      // React owns the remembered size; a collapsed drag only previews the temporary live size.
      target.style.setProperty(
        props.styleProperty,
        `${cancelled || dismiss ? remembered : committed}px`,
      );
      if (cancelled || !resizing) return;
      if (props.onCommit) props.onCommit(dismiss ? remembered : committed, !dismiss);
      else if (dismiss) props.onDismiss?.();
      else {
        props.onChange(committed);
        props.onOpen?.();
      }
    };
    const cancel = () => {
      if (finished) return;
      finished = true;
      release();
      target.style.setProperty(props.styleProperty, `${remembered}px`);
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
    const growsForward = props.growthDirection === "forward";
    const increaseKey =
      props.orientation === "vertical"
        ? growsForward
          ? "ArrowRight"
          : "ArrowLeft"
        : growsForward
          ? "ArrowDown"
          : "ArrowUp";
    const decreaseKey =
      props.orientation === "vertical"
        ? growsForward
          ? "ArrowLeft"
          : "ArrowRight"
        : growsForward
          ? "ArrowUp"
          : "ArrowDown";
    if (event.key !== increaseKey && event.key !== decreaseKey) return;
    event.preventDefault();
    const delta = event.key === increaseKey ? 16 : -16;
    if (props.panelOpen === false) {
      if (delta > 0) props.onOpen?.();
    } else if (delta < 0 && props.value <= props.min && props.onDismiss) props.onDismiss();
    else props.onChange(clampPanelSize(props.value + delta, props.min, props.max));
  };

  const reset = () => {
    if (props.panelOpen === false) props.onOpen?.();
    props.onChange(props.defaultValue);
  };

  return (
    <Separator
      className={`layout-resizer layout-resizer--${props.orientation}${props.class ? ` ${props.class}` : ""}`}
      aria-label={props.label}
      aria-orientation={props.orientation}
      aria-valuemin={props.onDismiss ? 0 : props.min}
      aria-valuemax={props.max}
      aria-valuenow={props.panelOpen === false ? 0 : props.value}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={reset}
    />
  );
}
