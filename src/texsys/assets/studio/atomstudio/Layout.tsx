import { t } from "../i18n";
/* SPDX-License-Identifier: MIT
 * Slot composition adapted from AtomStudio App/Workspace layout.
 * Product data and panel contents are supplied by Tex Studio disciplines.
 */
import React, { type ReactNode } from "react";
import { HeightResizeHandle, WidthResizeHandle } from "./ResizeHandle";
import { NavigatorMenu, type NavigatorMenuItem } from "../NavigatorMenu";
import {
  DEFAULT_SIDEBAR_WIDTH,
  MIN_BOTTOM_HEIGHT,
  MIN_INSPECTOR_WIDTH,
  MIN_SIDEBAR_WIDTH,
} from "./layoutSizing";
import type { StudioLayout } from "../sdk";
import "./editor-shell.css";
import "./layout.css";

type ChangeLayout = (patch: Partial<StudioLayout>) => void;

export function sidebarLayoutMenuItems(
  layout: Pick<StudioLayout, "sidebarOpen" | "sidebarPosition">,
  change: ChangeLayout,
): NavigatorMenuItem[] {
  const onRight = layout.sidebarPosition === "right";
  const macKeys = /Mac|iPhone|iPad/.test(navigator.platform);
  return [
    {
      label: t(onRight ? "向左移动主侧栏" : "向右移动主侧栏"),
      run: () => change({ sidebarPosition: onRight ? "left" : "right" }),
    },
    {
      label: t(layout.sidebarOpen ? "隐藏主侧栏" : "显示主侧栏"),
      shortcut: [macKeys ? "⌘" : "Ctrl", "\\"],
      run: () => change({ sidebarOpen: !layout.sidebarOpen }),
    },
  ];
}

const activityBarPositions = [
  { id: "side", label: "侧边" },
  { id: "top", label: "顶部" },
  { id: "bottom", label: "底部" },
] as const;

export function activityBarPositionMenuItem(
  layout: Pick<StudioLayout, "activityBarPosition">,
  change: ChangeLayout,
): NavigatorMenuItem {
  return {
    label: t("活动栏位置"),
    submenu: activityBarPositions.map(({ id, label }) => ({
      label: t(label),
      checked: id === (layout.activityBarPosition ?? "side"),
      run: () =>
        change(
          id === "side"
            ? { activityBarPosition: id }
            : { activityBarPosition: id, sidebarOpen: true },
        ),
    })),
  };
}

function horizontalActivityBar(layout: StudioLayout) {
  return (
    layout.sidebarOpen &&
    (layout.activityBarPosition === "top" || layout.activityBarPosition === "bottom")
  );
}

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
  change: ChangeLayout;
  activity: ReactNode;
  sidebar: ReactNode;
  inspector: ReactNode;
  bottom: ReactNode;
  children: ReactNode;
}) {
  const navigator = React.useRef<HTMLElement>(null);
  const bottomPanel = React.useRef<HTMLElement>(null);
  const inspectorPanel = React.useRef<HTMLElement>(null);
  const stage = React.useRef<HTMLDivElement>(null);
  const [stageWidth, setStageWidth] = React.useState(window.innerWidth);
  React.useEffect(() => {
    if (!stage.current) return;
    const observer = new ResizeObserver(([entry]) => setStageWidth(entry.contentRect.width));
    observer.observe(stage.current);
    return () => observer.disconnect();
  }, []);
  const panelPosition = layout.panelPosition ?? "bottom";
  const sideDock = panelPosition !== "bottom";
  const panelWidth = layout.panelWidth ?? 320;
  const maxDockWidth = Math.max(200, Math.floor(stageWidth * 0.55));
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
  const sidebarOnRight = layout.sidebarPosition === "right";
  const classes = [
    "workbench-layout",
    `dock-panel-${panelPosition}`,
    !layout.sidebarOpen && "is-sidebar-collapsed",
    !layout.inspectorOpen && "is-inspector-hidden",
    layout.bottomOpen && "is-bottom-open",
    layout.bottomOpen && layout.bottomMaximized && "is-bottom-maximized",
    sidebarOnRight && "sidebar-on-right",
    horizontalActivityBar(layout) &&
      `has-horizontal-activity activity-bar-${layout.activityBarPosition}`,
    layout.activityBarSize === "compact" && "activity-bar-compact",
  ];
  return (
    <div
      className={classes.filter(Boolean).join(" ")}
      style={
        {
          "--sidebar-width": `${layout.sidebarWidth}px`,
          "--sidebar-min-width": `${MIN_SIDEBAR_WIDTH}px`,
          "--right-panel-width": `${layout.inspectorWidth}px`,
          "--inspector-min-width": `${MIN_INSPECTOR_WIDTH}px`,
          "--bottom-panel-height": `${layout.bottomHeight}px`,
          "--dock-panel-width": `${panelWidth}px`,
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
        grows={sidebarOnRight ? "left" : "right"}
        mode="collapse"
        open={layout.sidebarOpen}
        onClose={() => change({ sidebarOpen: false })}
        onChange={(sidebarWidth) => change({ sidebarOpen: true, sidebarWidth })}
      />
      <div className="workbench-stage" ref={stage}>
        <main className="workbench-editor workbench-card">{children}</main>
        {sideDock ? (
          <WidthResizeHandle
            className="bottom-panel-layout-resizer"
            label={t("调整工具面板宽度")}
            panelRef={bottomPanel}
            previewProperty="--dock-panel-preview-width"
            value={panelWidth}
            min={200}
            max={maxDockWidth}
            defaultValue={320}
            grows={panelPosition === "left" ? "right" : "left"}
            mode="hide"
            open={layout.bottomOpen}
            onClose={() => change({ bottomOpen: false })}
            onChange={(panelWidth) => change({ bottomOpen: true, panelWidth })}
          />
        ) : (
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
        )}
        <section
          ref={bottomPanel}
          className="workbench-bottom workbench-card"
          aria-label={t(sideDock ? "工具面板" : "底部面板")}
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
        grows={sidebarOnRight ? "right" : "left"}
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
  layout,
  change,
}: {
  active: Id;
  items: { id: Id; label: string; icon: ReactNode; count?: number; shortcut?: string[] }[];
  select: (id: Id) => void;
  layout: StudioLayout;
  change: ChangeLayout;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [context, setContext] = React.useState<{
    point: { x: number; y: number };
    trigger?: HTMLButtonElement;
  }>();
  const hidden = layout.hiddenActivities ?? [];
  const hiddenBadges = layout.hiddenActivityBadges ?? [];
  const visible = items.filter(({ id }) => !hidden.includes(id));
  const focusable = visible.find(({ id }) => id === active)?.id ?? visible[0]?.id;
  const horizontal = horizontalActivityBar(layout);
  const label = t("配置活动栏");
  const target = items.find(({ id }) => id === context?.trigger?.dataset.activityId);
  const setHidden = (hiddenActivities: string[]) => {
    if (hiddenActivities.includes(active) && !hidden.includes(active)) {
      const fallback = items.find(({ id }) => !hiddenActivities.includes(id));
      change({
        hiddenActivities,
        sidebarActivity: fallback?.id ?? active,
        sidebarOpen: Boolean(fallback && layout.sidebarOpen),
      });
    } else {
      change({ hiddenActivities });
    }
  };
  const toggleActivity = (id: Id) =>
    setHidden(hidden.includes(id) ? hidden.filter((item) => item !== id) : [...hidden, id]);
  const menuItems: NavigatorMenuItem[] = [];
  if (target) {
    menuItems.push({
      label: t("隐藏“{name}”", { name: t(target.label) }),
      run: hidden.includes(target.id) ? undefined : () => toggleActivity(target.id),
    });
    if (target.count !== undefined) {
      const badgeHidden = hiddenBadges.includes(target.id);
      menuItems.push({
        label: t(badgeHidden ? "显示徽标" : "隐藏徽标"),
        run: () =>
          change({
            hiddenActivityBadges: badgeHidden
              ? hiddenBadges.filter((id) => id !== target.id)
              : [...hiddenBadges, target.id],
          }),
      });
    }
  }
  menuItems.push(
    ...items.map(({ id, label, shortcut }, index) => ({
      label: t(label),
      checked: !hidden.includes(id),
      checkbox: true,
      separator: index === 0 && Boolean(target),
      shortcut,
      run: () => toggleActivity(id),
    })),
  );
  if (visible.length < items.length) {
    menuItems.push({
      label: t("恢复默认入口"),
      run: () => setHidden([]),
    });
  }
  menuItems.push(
    { ...activityBarPositionMenuItem(layout, change), separator: true },
    {
      label: t("活动栏大小"),
      submenu: [
        {
          label: t("默认"),
          checked: (layout.activityBarSize ?? "default") === "default",
          run: () => change({ activityBarSize: "default" }),
        },
        {
          label: t("紧凑"),
          checked: layout.activityBarSize === "compact",
          run: () => change({ activityBarSize: "compact" }),
        },
      ],
    },
    ...sidebarLayoutMenuItems(layout, change),
  );
  return (
    <>
      <div
        ref={ref}
        className="activity-bar"
        role={visible.length ? "tablist" : "group"}
        aria-orientation={visible.length ? (horizontal ? "horizontal" : "vertical") : undefined}
        aria-label={visible.length ? t("资源入口") : label}
        tabIndex={visible.length ? undefined : 0}
        title={label}
        onContextMenu={(event) => {
          event.preventDefault();
          event.stopPropagation();
          const trigger =
            event.target instanceof Element
              ? (event.target.closest<HTMLButtonElement>("[data-activity-id]") ?? undefined)
              : undefined;
          trigger?.focus({ preventScroll: true });
          setContext({ point: { x: event.clientX, y: event.clientY }, trigger });
        }}
        onKeyDown={(event) => {
          if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
            event.preventDefault();
            event.stopPropagation();
            const invoker = event.target as HTMLElement;
            const { left, right, top, bottom } = invoker.getBoundingClientRect();
            setContext({
              point: horizontal ? { x: left, y: bottom } : { x: right, y: top },
              trigger: invoker instanceof HTMLButtonElement ? invoker : undefined,
            });
            return;
          }
          const previousKey = horizontal ? "ArrowLeft" : "ArrowUp";
          const nextKey = horizontal ? "ArrowRight" : "ArrowDown";
          if (![previousKey, nextKey, "Home", "End"].includes(event.key)) return;
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
                : (index + (event.key === nextKey ? 1 : tabs.length - 1)) % tabs.length;
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
            {item.count && !hiddenBadges.includes(item.id) ? (
              <span className="activity-badge">{item.count}</span>
            ) : null}
          </button>
        ))}
      </div>
      {context && (
        <NavigatorMenu
          label={label}
          point={context.point}
          onDismiss={() => {
            setContext(undefined);
            if (document.activeElement !== document.body) return;
            (
              (context.trigger?.isConnected ? context.trigger : undefined) ??
              ref.current?.querySelector<HTMLElement>('[role="tab"][tabindex="0"]') ??
              ref.current
            )?.focus();
          }}
          items={menuItems}
        />
      )}
    </>
  );
}
