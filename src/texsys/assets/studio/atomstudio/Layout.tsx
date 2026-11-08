import { t } from "../i18n";
/* SPDX-License-Identifier: MIT
 * Slot composition adapted from AtomStudio App/Workspace layout.
 * Product data and panel contents are supplied by Tex Studio disciplines.
 */
import React, { type ReactNode } from "react";
import { HeightResizeHandle, WidthResizeHandle } from "./ResizeHandle";
import { NavigatorMenu } from "../NavigatorMenu";
import {
  DEFAULT_SIDEBAR_WIDTH,
  MIN_BOTTOM_HEIGHT,
  MIN_INSPECTOR_WIDTH,
  MIN_SIDEBAR_WIDTH,
} from "./layoutSizing";
import type { StudioLayout } from "../sdk";
import "./editor-shell.css";
import "./layout.css";

export function WorkbenchLayout({
  layout,
  change,
  activity,
  sidebar,
  inspector,
  bottom,
  children,
}: {
  layout: StudioLayout;
  change: (patch: Partial<StudioLayout>) => void;
  activity: ReactNode;
  sidebar: ReactNode;
  inspector: ReactNode;
  bottom: ReactNode;
  children: ReactNode;
}) {
  const navigator = React.useRef<HTMLElement>(null);
  const bottomPanel = React.useRef<HTMLElement>(null);
  const inspectorPanel = React.useRef<HTMLElement>(null);
  const [{ width, height }, setViewport] = React.useState(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
  }));
  React.useEffect(() => {
    const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const sidebarMax = Math.max(MIN_SIDEBAR_WIDTH, width <= 760 ? width : Math.floor(width * 0.4));
  const inspectorMax = Math.max(MIN_INSPECTOR_WIDTH, Math.floor(width * 0.45));
  const bottomMax = Math.max(MIN_BOTTOM_HEIGHT, Math.floor(height * 0.6));
  return (
    <div
      className={`workbench-layout ${layout.sidebarOpen ? "" : "is-sidebar-collapsed"} ${layout.inspectorOpen ? "" : "is-inspector-hidden"} ${layout.bottomOpen ? "is-bottom-open" : ""} ${layout.bottomOpen && layout.bottomMaximized ? "is-bottom-maximized" : ""}`}
      style={
        {
          "--sidebar-width": `${layout.sidebarWidth}px`,
          "--sidebar-min-width": `${MIN_SIDEBAR_WIDTH}px`,
          "--right-panel-width": `${layout.inspectorWidth}px`,
          "--inspector-min-width": `${MIN_INSPECTOR_WIDTH}px`,
          "--bottom-panel-height": `${layout.bottomHeight}px`,
          "--bottom-min-height": `${MIN_BOTTOM_HEIGHT}px`,
        } as React.CSSProperties
      }
    >
      <div className="workbench-side workbench-card">
        <nav className="workbench-activity" aria-label={t("工作区入口")}>
          {activity}
        </nav>
        <aside
          id="studio-resource-panel"
          ref={navigator}
          className="workbench-navigator"
          aria-label={t("资源浏览器")}
          inert={!layout.sidebarOpen}
          aria-hidden={!layout.sidebarOpen}
        >
          <div className="workbench-navigator-content">{sidebar}</div>
        </aside>
      </div>
      <WidthResizeHandle
        className="sidebar-layout-resizer"
        label={t("调整资源面板宽度")}
        panelRef={navigator}
        previewProperty="--sidebar-preview-width"
        value={layout.sidebarWidth}
        min={MIN_SIDEBAR_WIDTH}
        max={sidebarMax}
        defaultValue={DEFAULT_SIDEBAR_WIDTH}
        grows="right"
        mode="collapse"
        open={layout.sidebarOpen}
        onClose={() => change({ sidebarOpen: false })}
        onChange={(sidebarWidth) => change({ sidebarOpen: true, sidebarWidth })}
      />
      <div className="workbench-stage">
        <main className="workbench-editor workbench-card">{children}</main>
        <HeightResizeHandle
          className="bottom-panel-layout-resizer"
          label={t("调整底部面板高度")}
          panelRef={bottomPanel}
          previewProperty="--bottom-preview-height"
          value={layout.bottomHeight}
          min={MIN_BOTTOM_HEIGHT}
          max={bottomMax}
          defaultValue={260}
          grows="up"
          mode="hide"
          open={layout.bottomOpen}
          onClose={() => change({ bottomOpen: false })}
          onChange={(bottomHeight) => change({ bottomHeight })}
        />
        <section
          ref={bottomPanel}
          className="workbench-bottom workbench-card"
          aria-label={t("底部面板")}
          inert={!layout.bottomOpen}
          aria-hidden={!layout.bottomOpen}
        >
          {bottom}
        </section>
      </div>
      <WidthResizeHandle
        className="right-panel-layout-resizer"
        label={t("调整属性面板宽度")}
        panelRef={inspectorPanel}
        previewProperty="--inspector-preview-width"
        value={layout.inspectorWidth}
        min={MIN_INSPECTOR_WIDTH}
        max={inspectorMax}
        defaultValue={320}
        grows="left"
        mode="hide"
        open={layout.inspectorOpen}
        onClose={() => change({ inspectorOpen: false })}
        onChange={(inspectorWidth) => change({ inspectorWidth })}
      />
      <aside
        ref={inspectorPanel}
        className="workbench-inspector workbench-card"
        inert={!layout.inspectorOpen}
        aria-hidden={!layout.inspectorOpen}
      >
        {inspector}
      </aside>
    </div>
  );
}

export function ActivityBar<Id extends string>({
  active,
  items,
  select,
  hidden,
  setHidden,
}: {
  active: Id;
  items: { id: Id; label: string; icon: ReactNode; count?: number; shortcut?: string[] }[];
  select: (id: Id) => void;
  hidden: string[];
  setHidden: (ids: string[]) => void;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [point, setPoint] = React.useState<{ x: number; y: number }>();
  const visible = items.filter(({ id }) => !hidden.includes(id));
  const focusable = visible.find(({ id }) => id === active)?.id ?? visible[0]?.id;
  const label = t("配置活动栏");
  return (
    <>
      <div
        ref={ref}
        className="activity-bar"
        role={visible.length ? "tablist" : "group"}
        aria-orientation={visible.length ? "vertical" : undefined}
        aria-label={visible.length ? t("资源入口") : label}
        tabIndex={visible.length ? undefined : 0}
        title={label}
        onContextMenu={(event) => {
          event.preventDefault();
          setPoint({ x: event.clientX, y: event.clientY });
        }}
        onKeyDown={(event) => {
          if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
            event.preventDefault();
            const { right, top } = (event.target as HTMLElement).getBoundingClientRect();
            setPoint({ x: right, y: top });
            return;
          }
          if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
          const tabs = Array.from(
            ref.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? [],
          );
          const index = tabs.indexOf(event.target as HTMLButtonElement);
          if (index < 0) return;
          const next =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? tabs.length - 1
                : (index + (event.key === "ArrowDown" ? 1 : tabs.length - 1)) % tabs.length;
          event.preventDefault();
          tabs[next]?.focus();
          tabs[next]?.click();
        }}
      >
        {visible.map((item) => (
          <button
            key={item.id}
            role="tab"
            data-activity-id={item.id}
            aria-selected={item.id === active}
            aria-controls="studio-resource-panel"
            aria-label={t(item.label)}
            title={t(item.label)}
            tabIndex={item.id === focusable ? 0 : -1}
            onClick={() => select(item.id)}
          >
            {item.icon}
            {item.count ? <span className="activity-badge">{item.count}</span> : null}
          </button>
        ))}
      </div>
      {point && (
        <NavigatorMenu
          label={label}
          point={point}
          onDismiss={() => {
            setPoint(undefined);
            if (document.activeElement !== document.body) return;
            (
              ref.current?.querySelector<HTMLElement>('[role="tab"][tabindex="0"]') ?? ref.current
            )?.focus();
          }}
          items={[
            ...items.map(({ id, label, shortcut }) => {
              const checked = !hidden.includes(id);
              return {
                label: t(label),
                checked,
                checkbox: true,
                shortcut,
                run: () =>
                  setHidden(checked ? [...hidden, id] : hidden.filter((item) => item !== id)),
              };
            }),
            {
              label: t("恢复默认入口"),
              separator: true,
              run: visible.length < items.length ? () => setHidden([]) : undefined,
            },
          ]}
        />
      )}
    </>
  );
}
