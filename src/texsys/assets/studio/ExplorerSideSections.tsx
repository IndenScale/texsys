import React, { useRef, useState, type ReactNode } from "react";
import { OutlineView } from "./OutlineView";
import type { OutlineRange } from "./outline-model";
import { t } from "./i18n";
import { Icon } from "./ui";
import { NavigatorSash } from "./NavigatorSash";

const MAX_SECTION_BODY = 4096;
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
    value > 0 &&
    value <= MAX_SECTION_BODY
    ? Math.round(value)
    : fallback;
}

export function ExplorerSideSections({
  showOutline,
  showTimeline,
  activeDocument,
  cursor,
  onNavigate,
  initialHeights,
  onCommit,
  renderTimeline,
}: {
  showOutline: boolean;
  showTimeline: boolean;
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
  const rootRef = useRef<HTMLDivElement>(null);

  const renderedHeights = {
    outline: outlineOpen && showOutline ? heights.outline : 0,
    timeline: timelineOpen && showTimeline ? heights.timeline : 0,
  };
  const availableTreeSpace = (base: Heights) => {
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
    return Math.max(0, treeSpace);
  };

  const boundsFor = (mode: Mode, base: Heights) => {
    if (mode === "transfer") {
      const above = base.outline || availableTreeSpace(base);
      return {
        min: -Math.min(above, MAX_SECTION_BODY - base.timeline),
        max: Math.min(base.timeline, MAX_SECTION_BODY - base.outline),
      };
    }
    const primary = mode === "first-outline" ? base.outline : base.timeline;
    return {
      min: -Math.min(availableTreeSpace(base), MAX_SECTION_BODY - primary),
      max: primary,
    };
  };

  const deltaHeights = (mode: Mode, base: Heights, dy: number): Heights => {
    const delta = Math.round(dy);
    if (mode === "transfer")
      return {
        outline: Math.max(0, base.outline + delta),
        timeline: base.timeline - delta,
      };
    if (mode === "first-outline") return { ...base, outline: base.outline - delta };
    return { ...base, timeline: base.timeline - delta };
  };

  const startResize = (mode: Mode) => {
    const { min, max } = boundsFor(mode, renderedHeights);
    return {
      minDelta: min,
      maxDelta: max,
      apply: (delta: number, commit: boolean) => {
        const next = deltaHeights(mode, renderedHeights, delta);
        if (mode !== "first-timeline") setOutlineOpen(next.outline > 0);
        if (mode !== "first-outline") setTimelineOpen(next.timeline > 0);
        const remembered = {
          outline: next.outline || heights.outline,
          timeline: next.timeline || heights.timeline,
        };
        setHeights(remembered);
        if (commit && delta) onCommit?.(remembered);
      },
    };
  };

  const reset = (mode: Mode) => {
    const base = heights;
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
  const sash = (mode: Mode, label: string) => {
    const primary = mode === "first-outline" ? renderedHeights.outline : renderedHeights.timeline;
    const { min, max } = boundsFor(mode, renderedHeights);
    return (
      <NavigatorSash
        label={label}
        min={primary - max}
        max={primary - min}
        value={primary}
        startResize={() => startResize(mode)}
        reset={() => reset(mode)}
      />
    );
  };

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
      {sash(firstMode, showOutline ? t("调整大纲高度") : t("调整时间线高度"))}
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
      {showOutline && showTimeline && sash("transfer", t("调整大纲与时间线高度"))}
      {showTimeline &&
        (renderTimeline ?? defaultTimeline)({
          open: timelineOpen,
          onOpenChange: setTimelineOpen,
          bodyHeight: heights.timeline,
        })}
    </div>
  );
}
