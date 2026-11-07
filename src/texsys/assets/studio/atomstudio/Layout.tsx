/* SPDX-License-Identifier: MIT
 * Slot composition adapted from AtomStudio App/Workspace layout.
 * Product data and panel contents are supplied by Tex Studio disciplines.
 */
import React, { type ReactNode } from "react";
import { ResizeHandle } from "./ResizeHandle";
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
  const sidebarMax = Math.max(208, Math.floor(window.innerWidth * 0.4));
  const inspectorMax = Math.max(280, Math.floor(window.innerWidth * 0.45));
  return (
    <div
      className={`workbench-layout ${layout.sidebarOpen ? "" : "is-sidebar-collapsed"} ${layout.inspectorOpen ? "" : "is-inspector-collapsed"} ${layout.bottomOpen ? "is-bottom-open" : ""}`}
      style={
        {
          "--sidebar-width": `${layout.sidebarWidth}px`,
          "--right-panel-width": `${layout.inspectorWidth}px`,
          "--bottom-panel-height": `${layout.bottomHeight}px`,
        } as React.CSSProperties
      }
    >
      <nav className="workbench-activity" aria-label="工作区入口">
        {activity}
      </nav>
      <aside
        id="studio-resource-panel"
        className="workbench-navigator"
        aria-label="资源浏览器"
        inert={!layout.sidebarOpen}
        aria-hidden={!layout.sidebarOpen}
      >
        <div className="workbench-navigator-content">{sidebar}</div>
      </aside>
      <ResizeHandle
        class="sidebar-layout-resizer"
        label="调整资源面板宽度"
        orientation="vertical"
        styleProperty="--sidebar-width"
        styleTarget="parent"
        value={layout.sidebarWidth}
        min={208}
        max={sidebarMax}
        defaultValue={248}
        growthDirection="forward"
        panelOpen={layout.sidebarOpen}
        onOpen={() => change({ sidebarOpen: true })}
        onDismiss={() => change({ sidebarOpen: false })}
        onChange={(sidebarWidth) => change({ sidebarWidth })}
        onCommit={(sidebarWidth, sidebarOpen) => change({ sidebarWidth, sidebarOpen })}
      />
      <div className="workbench-stage">
        <main className="workbench-editor">{children}</main>
        <ResizeHandle
          class="bottom-panel-layout-resizer"
          label="调整底部面板高度"
          orientation="horizontal"
          styleProperty="--bottom-panel-height"
          styleTarget="parent"
          value={layout.bottomHeight}
          min={144}
          max={Math.max(144, Math.floor(window.innerHeight * 0.6))}
          defaultValue={260}
          panelOpen={layout.bottomOpen}
          onOpen={() => change({ bottomOpen: true })}
          onDismiss={() => change({ bottomOpen: false })}
          onChange={(bottomHeight) => change({ bottomHeight })}
          onCommit={(bottomHeight, bottomOpen) => change({ bottomHeight, bottomOpen })}
        />
        <section
          className="workbench-bottom"
          aria-label="底部面板"
          inert={!layout.bottomOpen}
          aria-hidden={!layout.bottomOpen}
        >
          {bottom}
        </section>
      </div>
      <ResizeHandle
        class="right-panel-layout-resizer"
        label="调整属性面板宽度"
        orientation="vertical"
        styleProperty="--right-panel-width"
        styleTarget="parent"
        value={layout.inspectorWidth}
        min={280}
        max={inspectorMax}
        defaultValue={320}
        panelOpen={layout.inspectorOpen}
        onOpen={() => change({ inspectorOpen: true })}
        onDismiss={() => change({ inspectorOpen: false })}
        onChange={(inspectorWidth) => change({ inspectorWidth })}
        onCommit={(inspectorWidth, inspectorOpen) => change({ inspectorWidth, inspectorOpen })}
      />
      <aside
        className="workbench-inspector"
        inert={!layout.inspectorOpen}
        aria-hidden={!layout.inspectorOpen}
      >
        {inspector}
      </aside>
    </div>
  );
}

export function ActivityBar({
  active,
  items,
  select,
}: {
  active: string;
  items: { id: string; label: string; icon: ReactNode; count?: number; extensionStart?: boolean }[];
  select: (id: string) => void;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  return (
    <div
      ref={ref}
      className="activity-bar"
      role="tablist"
      aria-orientation="vertical"
      aria-label="资源 Activity"
      onKeyDown={(event) => {
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
      {items.map((item) => (
        <button
          key={item.id}
          role="tab"
          className={item.extensionStart ? "extension-start" : undefined}
          aria-selected={item.id === active}
          aria-controls="studio-resource-panel"
          aria-label={item.label}
          title={item.label}
          tabIndex={item.id === active ? 0 : -1}
          onClick={() => select(item.id)}
        >
          {item.icon}
          {item.count ? <span className="activity-badge">{item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}
