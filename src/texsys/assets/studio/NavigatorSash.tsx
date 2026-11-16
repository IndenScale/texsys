import React, { useCallback, useEffect, useRef, useState } from "react";
import { t } from "./i18n";
import "./side-sections.css";

type ResizeSession = {
  minDelta: number;
  maxDelta: number;
  apply: (delta: number, commit: boolean) => void;
};

const clampDelta = ({ minDelta, maxDelta }: ResizeSession, delta: number) =>
  Math.min(maxDelta, Math.max(minDelta, Math.round(delta)));

export function NavigatorSash({
  label,
  min,
  max,
  value,
  startResize,
  reset,
}: {
  label: string;
  min: number;
  max?: number;
  value: number;
  startResize: () => ResizeSession;
  reset: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const drag = useRef<
    (ResizeSession & { pointerId: number; y: number; delta: number; moved: boolean }) | null
  >(null);
  const finish = useCallback((event: PointerEvent) => {
    const session = drag.current;
    if (!session || session.pointerId !== event.pointerId) return;
    drag.current = null;
    document.body.classList.remove("side-sash-dragging");
    setDragging(false);
    const delta =
      event.type === "pointerup" ? clampDelta(session, event.clientY - session.y) : session.delta;
    if (session.moved || delta) session.apply(delta, true);
  }, []);

  useEffect(() => {
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    window.addEventListener("lostpointercapture", finish, true);
    return () => {
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      window.removeEventListener("lostpointercapture", finish, true);
      if (drag.current) document.body.classList.remove("side-sash-dragging");
    };
  }, [finish]);

  const end = (event: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId || !drag.current) return;
    const handle = event.currentTarget;
    handle.blur();
    finish(event.nativeEvent);
    if (handle.hasPointerCapture?.(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };

  return (
    <div
      className={`side-sash${dragging ? " dragging" : ""}`}
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      tabIndex={0}
      title={t("拖拽调整高度，双击恢复默认")}
      onPointerDown={(event) => {
        if (event.button !== 0 || drag.current) return;
        event.preventDefault();
        event.currentTarget.focus({ preventScroll: true });
        try {
          event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
          // Synthetic pointers have no active pointer to capture.
        }
        drag.current = {
          ...startResize(),
          pointerId: event.pointerId,
          y: event.clientY,
          delta: 0,
          moved: false,
        };
        setDragging(true);
        document.body.classList.add("side-sash-dragging");
      }}
      onPointerMove={(event) => {
        const session = drag.current;
        if (!session || session.pointerId !== event.pointerId) return;
        const delta = clampDelta(session, event.clientY - session.y);
        if (delta === session.delta) return;
        session.delta = delta;
        session.moved = true;
        session.apply(delta, false);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 32 : 8;
        const delta = event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0;
        if (!delta || drag.current) return;
        event.preventDefault();
        const session = startResize();
        const clamped = clampDelta(session, delta);
        if (clamped) session.apply(clamped, true);
      }}
      onDoubleClick={reset}
    />
  );
}
