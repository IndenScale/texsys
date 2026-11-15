import React, { useEffect, useRef, useState, type ReactNode } from "react";
import { OutlineView } from "./OutlineView";
import type { OutlineRange } from "./outline-model";
import { t } from "./i18n";
import { Icon } from "./ui";
import "./side-sections.css";

export const MIN_SECTION_BODY = 44;
export const MAX_SECTION_BODY = 4096;
const MIN_TREE_SPACE = 60;
const SUMMARY_HEIGHT = 28;

type Heights = { outline: number; timeline: number };
type Mode = "first-outline" | "first-timeline" | "transfer";
export type SectionControls = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bodyHeight: number;
};

export function defaultSectionHeights(): Heights {
  const vh = typeof window === "undefined" ? 720 : window.innerHeight;
  return {
    outline: Math.min(320, Math.round(vh * 0.4)),
    timeline: Math.min(160, Math.round(vh * 0.2)),
  };
}

function storedHeight(value: number | undefined, fallback: number) {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= MIN_SECTION_BODY &&
    value <= MAX_SECTION_BODY
    ? Math.round(value)
    : fallback;
}

export function ExplorerSideSections({
  showOutline,
  showTimeline,
  foldersVisible,
  activeDocument,
  cursor,
  onNavigate,
  initialHeights,
  onCommit,
  renderTimeline,
}: {
  showOutline: boolean;
  showTimeline: boolean;
  foldersVisible: boolean;
  activeDocument?: { name: string; value: string };
  cursor?: { line: number; column: number };
  onNavigate: (range: OutlineRange) => void;
  initialHeights?: Partial<Heights>;
  onCommit?: (heights: Heights) => void;
  renderTimeline?: (controls: SectionControls) => ReactNode;
}) {
  const defaults = useRef(defaultSectionHeights());
  const [heights, setHeights] = useState<Heights>(() => ({
    outline: storedHeight(initialHeights?.outline, defaults.current.outline),
    timeline: storedHeight(initialHeights?.timeline, defaults.current.timeline),
  }));
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [timelineOpen, setTimelineOpen] = useState(false);
  const [dragMode, setDragMode] = useState<Mode>();
  const rootRef = useRef<HTMLDivElement>(null);
  const heightsRef = useRef(heights);
  heightsRef.current = heights;
  const dragRef = useRef<
    { y: number; base: Heights; min: number; max: number; mode: Mode; dy: number } | undefined
  >(undefined);

  const finishRef = useRef<() => void>(() => {});
  useEffect(() => {
    const winEnd = () => finishRef.current();
    window.addEventListener("pointerup", winEnd);
    window.addEventListener("pointercancel", winEnd);
    // lostpointercapture 不冒泡，React 根监听收不到，用捕获阶段兜底。
    window.addEventListener("lostpointercapture", winEnd, true);
    return () => {
      window.removeEventListener("pointerup", winEnd);
      window.removeEventListener("pointercancel", winEnd);
      window.removeEventListener("lostpointercapture", winEnd, true);
      document.body.classList.remove("side-sash-dragging");
    };
  }, []);

  const boundsFor = (mode: Mode, base: Heights) => {
    if (mode === "transfer")
      return {
        min: MIN_SECTION_BODY - base.outline,
        max: base.timeline - MIN_SECTION_BODY,
      };
    const primary = mode === "first-outline" ? base.outline : base.timeline;
    const container = rootRef.current?.closest(".welcome-explorer, .core-explorer");
    const containerH = container?.getBoundingClientRect().height ?? 0;
    const headingH =
      container?.querySelector(".core-workspace-heading")?.getBoundingClientRect().height ?? 0;
    const sectionsH =
      (showOutline ? SUMMARY_HEIGHT + base.outline : 0) +
      (showTimeline ? SUMMARY_HEIGHT + 1 + base.timeline : 0);
    const treeEl = container?.querySelector(".core-explorer-tree, .core-file-tree");
    const treeSpace = treeEl
      ? treeEl.getBoundingClientRect().height
      : containerH > 0
        ? Math.max(0, containerH - headingH - sectionsH)
        : MAX_SECTION_BODY;
    // 边界随指针移动：向下拖扩大边界上方空间，下方节收缩。
    const maxPrimary = Math.min(
      MAX_SECTION_BODY,
      primary + Math.max(0, treeSpace - (foldersVisible ? MIN_TREE_SPACE : 0)),
    );
    return { min: primary - maxPrimary, max: primary - MIN_SECTION_BODY };
  };

  const deltaHeights = (mode: Mode, base: Heights, dy: number): Heights => {
    const delta = Math.round(dy);
    if (mode === "transfer")
      return { outline: base.outline + delta, timeline: base.timeline - delta };
    if (mode === "first-outline") return { ...base, outline: base.outline - delta };
    return { ...base, timeline: base.timeline - delta };
  };

  const expandFor = (mode: Mode) => {
    if (mode !== "first-timeline" && showOutline) setOutlineOpen(true);
    if (mode !== "first-outline" && showTimeline) setTimelineOpen(true);
  };

  const beginDrag = (event: React.PointerEvent<HTMLDivElement>, mode: Mode) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.focus({ preventScroll: true });
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Synthetic pointers (tests, automation) have no active pointer to capture.
    }
    expandFor(mode);
    const base = heightsRef.current;
    const { min, max } = boundsFor(mode, base);
    dragRef.current = { y: event.clientY, base, min, max, mode, dy: 0 };
    setDragMode(mode);
    document.body.classList.add("side-sash-dragging");
  };

  const moveDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    drag.dy = Math.min(drag.max, Math.max(drag.min, event.clientY - drag.y));
    setHeights(deltaHeights(drag.mode, drag.base, drag.dy));
  };

  const finishDrag = () => {
    const drag = dragRef.current;
    if (!drag) return;
    dragRef.current = undefined;
    setDragMode(undefined);
    document.body.classList.remove("side-sash-dragging");
    const next = deltaHeights(drag.mode, drag.base, drag.dy);
    setHeights(next);
    onCommit?.(next);
  };
  finishRef.current = finishDrag;

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // Capture may already be lost when the pointer leaves the window.
    }
    // 松开即失活：指针拖拽不留键盘焦点，避免焦点条残留。
    event.currentTarget.blur();
    finishDrag();
  };

  const keyAdjust = (event: React.KeyboardEvent<HTMLDivElement>, mode: Mode) => {
    const step = event.shiftKey ? 32 : 8;
    const dy = event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0;
    if (!dy) return;
    event.preventDefault();
    expandFor(mode);
    const base = heightsRef.current;
    const { min, max } = boundsFor(mode, base);
    const next = deltaHeights(mode, base, Math.min(max, Math.max(min, dy)));
    setHeights(next);
    onCommit?.(next);
  };

  const reset = (mode: Mode) => {
    const base = heightsRef.current;
    const next =
      mode === "transfer"
        ? { ...defaults.current }
        : mode === "first-outline"
          ? { ...base, outline: defaults.current.outline }
          : { ...base, timeline: defaults.current.timeline };
    setHeights(next);
    onCommit?.(next);
  };

  if (!showOutline && !showTimeline) return null;
  const firstMode: Mode = showOutline ? "first-outline" : "first-timeline";
  const sash = (mode: Mode, label: string, value: number) => (
    <div
      className={`side-sash${dragMode === mode ? " dragging" : ""}`}
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      aria-valuemin={MIN_SECTION_BODY}
      aria-valuemax={Math.round(value + boundsFor(mode, heights).max)}
      aria-valuenow={Math.round(value)}
      tabIndex={0}
      title={t("拖拽调整高度，双击恢复默认")}
      onPointerDown={(event) => beginDrag(event, mode)}
      onPointerMove={moveDrag}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onLostPointerCapture={finishDrag}
      onKeyDown={(event) => keyAdjust(event, mode)}
      onDoubleClick={() => reset(mode)}
    />
  );

  const defaultTimeline = (controls: SectionControls) => (
    <details
      className="welcome-side-section welcome-timeline-section"
      open={controls.open}
      onToggle={(event) => controls.onOpenChange(event.currentTarget.open)}
    >
      <summary>
        <Icon name="chevron" size={14} />
        <span>{t("时间线")}</span>
      </summary>
      <div className="timeline-body" style={{ height: controls.bodyHeight }}>
        <p className="core-hint">{t("打开文件后可查看时间线。")}</p>
      </div>
    </details>
  );

  return (
    <div className="welcome-side-sections" ref={rootRef}>
      {sash(
        firstMode,
        showOutline ? t("调整大纲高度") : t("调整时间线高度"),
        showOutline ? heights.outline : heights.timeline,
      )}
      {showOutline && (
        <OutlineView
          document={activeDocument}
          cursor={cursor}
          onNavigate={onNavigate}
          open={outlineOpen}
          onOpenChange={setOutlineOpen}
          bodyHeight={heights.outline}
        />
      )}
      {showOutline && showTimeline && sash("transfer", t("调整大纲与时间线高度"), heights.outline)}
      {showTimeline &&
        (renderTimeline ?? defaultTimeline)({
          open: timelineOpen,
          onOpenChange: setTimelineOpen,
          bodyHeight: heights.timeline,
        })}
    </div>
  );
}
